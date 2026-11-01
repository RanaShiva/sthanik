// Backend client: model lifecycle, streaming chat completions, TTS.
import { toast } from './ui.js';

export const state = { status: null, listeners: new Set() };

export function onStatus(fn) {
  state.listeners.add(fn);
  if (state.status) fn(state.status);
  return () => state.listeners.delete(fn);
}

export async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    state.status = await r.json();
    state.listeners.forEach((fn) => fn(state.status));
  } catch { /* backend restarting */ }
  return state.status;
}

// Replies being generated right now (token -> model key). A reply keeps streaming while you browse other pages, so
// nothing may swap the resident model out from under it.
const running = new Map();
const announce = () => window.dispatchEvent(new Event('lfm3:generation'));
export function beginGeneration(key) {
  const token = Symbol('generation');
  running.set(token, key);
  announce();
  return () => { running.delete(token); announce(); };
}
export const isGenerating = (key) => [...running.values()].includes(key);

// The slot whose files actually back `key` (the status says so; normally the slot itself).
export const weightsFor = (key) => state.status?.slots?.[key] || key;

export const loaded = () => (state.status?.llama.state === 'ready' ? state.status.llama.key : null);

export async function loadModel(key) {
  const r = await fetch(`/api/models/${key}/load`, { method: 'POST' });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `Could not load model (${r.status})`);
  }
  await refreshStatus();
}

export async function unloadModel() {
  await fetch('/api/models/unload', { method: 'POST' });
  await refreshStatus();
}

// Make sure `key` is the resident model, loading (and swapping out another) if necessary.
export async function ensureModel(key) {
  const label = state.status?.models[weightsFor(key)]?.label || key;
  if (state.status?.llama.key === key && state.status.llama.state === 'ready') return;
  const resident = state.status?.llama.key;
  if (resident && resident !== key && isGenerating(resident)) {
    toast(`${state.status.models[state.status.llama.weights || resident]?.label || resident} is still replying. ${label} will load as soon as it finishes.`, 'info', 6000);
    while (isGenerating(resident)) await new Promise((r) => setTimeout(r, 300));
  }
  if (!(state.status?.llama.key === key && state.status.llama.state === 'loading')) {
    toast(`Loading ${label}…`);
    await loadModel(key);
  }
  for (let i = 0; i < 600; i++) {
    await refreshStatus();
    const l = state.status.llama;
    if (l.key === key && l.state === 'ready') return;
    if (l.state === 'error') throw new Error(l.error || 'Model failed to start');
    if (l.state === 'idle') throw new Error('Model load was cancelled');
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error('Timed out waiting for the model');
}

// Like ensureModel, but trusts nothing cached: a tool (image generation) can free the GPU between two model calls.
export async function ensureLoaded(key) {
  await refreshStatus();
  return ensureModel(key);
}

// POST a chat completion and stream deltas. Calls onDelta({content, reasoning}) per chunk.
export async function streamChat({ messages, params = {}, signal, onDelta }) {
  const body = {
    messages,
    stream: true,
    stream_options: { include_usage: true },
    timings_per_token: false,
    ...params,
  };
  const r = await fetch('/llama/v1/chat/completions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const e = await r.json(); msg = e.error?.message || e.detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  let timings = null, usage = null, finish = null;
  const calls = []; // tool calls arrive as fragments keyed by index
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line.startsWith('data:')) continue;
      const data = line.slice(5).trim();
      if (data === '[DONE]') continue;
      let j;
      try { j = JSON.parse(data); } catch { continue; }
      if (j.error) throw new Error(j.error.message || 'Generation error');
      const ch = j.choices?.[0];
      if (ch?.delta) {
        for (const tc of ch.delta.tool_calls || []) {
          const c = (calls[tc.index ?? 0] ||= { id: '', name: '', arguments: '' });
          if (tc.id) c.id = tc.id;
          if (tc.function?.name) c.name += tc.function.name;
          if (tc.function?.arguments) c.arguments += tc.function.arguments;
        }
        onDelta({ content: ch.delta.content || '', reasoning: ch.delta.reasoning_content || '', toolCalls: calls });
      }
      if (ch?.finish_reason) finish = ch.finish_reason;
      if (j.timings) timings = j.timings;
      if (j.usage) usage = j.usage;
    }
  }
  return { timings, usage, finish, toolCalls: calls.filter(Boolean) };
}

// Speech -> text with LFM3-Audio (loading it first if another model is resident). `b64` is a 16 kHz mono WAV.
export async function transcribeAudio(b64, signal, via = 'audio') {
  const end = beginGeneration(via);
  try {
    await ensureLoaded(via); // Omni listens with itself (it is already loaded); the others borrow the audio model
    let text = '';
    await streamChat({
      signal,
      messages: [{ role: 'system', content: via === 'omni' ? 'Perform ASR in English.' : 'Perform ASR.' }, { role: 'user', content: [{ type: 'input_audio', input_audio: { data: b64, format: 'wav' } }] }],
      params: { temperature: 0, max_tokens: 1024 },
      onDelta: ({ content }) => { text += content; },
    });
    return text.trim();
  } finally { end(); }
}

// Text → speech (LFM3-Audio / LFM3-Omni projector). Returns a WAV Blob.
export async function synthesize({ text, voice = 'female', lang = 'en', seed = -1, signal }) {
  const r = await fetch('/llama/tts', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ input: text, voice, lang, seed, response_format: 'wav' }),
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const e = await r.json(); msg = e.error?.message || msg; } catch { /* ignore */ }
    throw new Error(`Speech synthesis failed: ${msg}`);
  }
  return r.blob();
}

export const LANGS = {
  en: 'English', 'en-gb': 'English (UK)', zh: 'Chinese', ja: 'Japanese', ko: 'Korean', es: 'Spanish', fr: 'French', de: 'German',
  it: 'Italian', pt: 'Portuguese', nl: 'Dutch', pl: 'Polish', sv: 'Swedish', da: 'Danish', fi: 'Finnish', el: 'Greek',
  tr: 'Turkish', ar: 'Arabic', he: 'Hebrew',
};
