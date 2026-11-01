// Spoken conversation over llama-server's /v1/realtime WebSocket (bridged by the backend at /ws/realtime).
import { h, icon, toast, markdown } from './ui.js';
import { ensureModel, synthesize } from './api.js';
import { toolstore } from './toolstore.js';
import { Mic, PcmPlayer, resample, floatToPcm16, pcm16ToB64, b64ToPcm16, encodeWav, drawLevels } from './audio.js';

const MODES = [['text,audio', 'Speech + text'], ['audio', 'Speech only'], ['text', 'Text only']];

// System prompts from the LFM3 model cards for a forced output mode.
const INSTRUCTIONS = {
  audio: { 'text,audio': 'Respond with interleaved text and audio.', audio: 'Respond with audio.', text: 'Respond with text.' },
  omni: { 'text,audio': 'Respond in interleaved text and audio.', audio: 'Respond with audio.', text: 'Respond in text.' },
};

// What a voice conversation may look up on its own: read-only tools that need no click to approve.
const VOICE_TOOLS = ['web_search', 'news_search', 'fetch_url', 'current_time', 'calculator'];
const MODE_WORDS = { 'text,audio': 'interleaved text and audio', audio: 'audio', text: 'text' };
const MAX_CALL_ROUNDS = 4;
// A small model sometimes answers "I can't look things up" although it has the tool; the page then asks it, once, to search.
const CANT_LOOK_UP = /\b(?:(?:do not|don't|cannot|can't|can not|unable to|no way to|not able to)\b[^.!?]{0,60}\b(?:internet|browse|browsing|online|real[- ]time|live (?:data|info|price|access)|look (?:it |that |this )?up|check (?:the )?(?:current|latest|live))|no (?:live |real[- ]time )?(?:internet|web) access)/i;

// While a lookup runs the model has nothing to say, so the assistant says a short line in its own voice (made once per page
// visit by the loaded speech model, before the conversation starts; the browser's built-in speech voices can be network-based).
const FILLERS = ['Give me a moment, I’m searching for that.', 'Let me look that up for you.'];
const fillerClips = new Map(); // model key -> Promise<string[]> of object URLs
function prepareFillers(model) {
  if (!fillerClips.has(model)) {
    const p = (async () => {
      const urls = [];
      for (const text of FILLERS) {
        try { urls.push(URL.createObjectURL(await synthesize({ text }))); } catch { break; } // no spoken line is better than a failed start
      }
      return urls;
    })();
    fillerClips.set(model, p);
    p.then((u) => { if (!u.length) fillerClips.delete(model); }); // try again next time
  }
  return fillerClips.get(model);
}

function voiceTools() {
  return toolstore.enabledTools().filter((t) => VOICE_TOOLS.includes(t.name) && toolstore.approval(t.group) === 'auto')
    .map((t) => ({ type: 'function', name: t.name, description: t.description, parameters: t.schema }));
}

export function createVoice({ model, title, blurb }) {
  // Looking things up is what makes a voice assistant useful when it does not know the answer: on unless you turned web search off.
  if (toolstore.prefs.enabledGroups.web === undefined) toolstore.setGroupOn('web', true);
  let ws = null, session = null, mic = null, stopLevels = null, connecting = null;
  let mode = 'text,audio';
  let instructionsEdited = false;
  let dirty = false;
  let turn = null; // current assistant turn
  let sentAt = 0;
  const player = new PcmPlayer();
  let filler = null, fillerN = 0;
  const stopAudio = () => { player.stop(); if (filler) { filler.pause(); filler = null; } };
  const sayFiller = async () => {
    if (mode === 'text') return;
    const clips = (await fillerClips.get(model)) || [];
    if (!clips.length || !waiting) return;
    filler = new Audio(clips[fillerN++ % clips.length]);
    filler.addEventListener('ended', () => { filler = null; });
    filler.play().catch(() => { filler = null; }); // blocked autoplay: the status line still says it is looking
  };
  let waiting = false; // a lookup is running and nothing has been spoken since

  const log = h('div', { class: 'voice-log' });
  const viz = h('canvas', { class: 'viz', style: { display: 'none' } }); // shown only while recording
  const status = h('span', { class: 'chip' }, h('span', { class: 'dot' }), 'Not connected');
  const micBtn = h('button', { class: 'mic-big', title: 'Start talking' }, icon('mic'));
  const caption = h('div', { class: 'mic-caption' }, 'Tap to talk');
  const textIn = h('input', { class: 'input', placeholder: '…or type a message and press Enter' });
  const instr = h('textarea', { class: 'input', rows: 3 });
  const defaultInstr = () => (voiceTools().length
    ? `Respond with function calls, and ${MODE_WORDS[mode]}. Use web_search for any real-world fact, person, event, price, weather or news that you are not certain of; then answer briefly.`
    : INSTRUCTIONS[model][mode]);
  instr.value = defaultInstr();
  const seg = h('div', { class: 'seg' }, MODES.map(([v, l]) => h('button', {
    class: v === mode ? 'active' : '', 'data-v': v,
    onclick: () => {
      mode = v; dirty = true;
      seg.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === v));
      if (!instructionsEdited) instr.value = defaultInstr();
      toast('Applies to the next conversation (starts a fresh session).');
    },
  }, l)));
  instr.addEventListener('input', () => { instructionsEdited = true; dirty = true; });

  const CAPTIONS = { 'Listening…': 'Listening: tap to send', 'Thinking…': 'Thinking…', 'Speaking…': 'Speaking: tap to cut in', 'Loading model…': 'Loading the model…', 'Preparing voice…': 'Preparing the voice…', 'Looking it up…': 'Looking it up…', 'Connecting…': 'Connecting…' };
  const setStatus = (text, kind = '', pulse = false) => {
    status.className = `chip ${kind}`;
    status.replaceChildren(h('span', { class: `dot${pulse ? ' pulse' : ''}` }), text);
    caption.textContent = CAPTIONS[text] || 'Tap to talk';
    micBtn.classList.toggle('busy', text === 'Thinking…' || text === 'Speaking…');
  };

  function emptyLog() {
    log.replaceChildren(h('div', { class: 'empty' },
      h('h2', {}, title), h('p', {}, blurb)));
  }

  function addBubble(role, content) {
    if (log.querySelector('.empty')) log.replaceChildren();
    const bubble = h('div', { class: 'bubble' }, content);
    const body = h('div', { class: 'msg-body' }, h('div', { class: 'msg-role' }, role === 'user' ? 'You' : 'LFM3'), bubble);
    const el = h('div', { class: `msg ${role}` }, h('div', { class: 'avatar' }, role === 'user' ? 'Y' : 'L'), body);
    log.append(el);
    log.scrollTop = log.scrollHeight;
    return { el, bubble, body };
  }

  // Tool calls: the client runs them, sends the results as function_call_output items, then asks for the next response.
  let callRounds = 0, callEpoch = 0, nudged = false;
  const toolRow = (text) => { if (log.querySelector('.empty')) log.replaceChildren(); const el = h('div', { class: 'voice-tool' }, icon('globe', 14), text); log.append(el); log.scrollTop = log.scrollHeight; return el; };
  async function runCalls(calls) {
    const epoch = ++callEpoch;
    callRounds++;
    setStatus('Looking it up…', 'accent', true);
    const outputs = [];
    for (const c of calls) {
      let args = {};
      try { args = JSON.parse(c.arguments || '{}'); } catch { /* the tool will say what is wrong */ }
      const row = toolRow(c.name === 'web_search' || c.name === 'news_search' ? `Searching the web: ${String(args.query || '').slice(0, 80)}` : c.name === 'fetch_url' ? `Reading ${String(args.url || '').slice(0, 70)}` : `Using ${c.name}`);
      let out;
      try {
        const r = await toolstore.call(c.name, args);
        out = r.ok ? String(r.text).slice(0, 6000) : `Error: ${r.error}`;
        row.classList.add(r.ok ? 'ok' : 'bad');
      } catch (e) { out = `Error: ${e.message}`; row.classList.add('bad'); }
      outputs.push({ call_id: c.call_id, output: out });
    }
    if (epoch !== callEpoch || !ws || ws.readyState !== WebSocket.OPEN) return; // you spoke over it, or the session ended
    for (const o of outputs) ws.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'function_call_output', call_id: o.call_id, output: o.output } }));
    sentAt = performance.now();
    ws.send(JSON.stringify({ type: 'response.create' }));
    setStatus('Thinking…', 'accent', true);
  }

  async function connect() {
    if (ws && ws.readyState === WebSocket.OPEN && !dirty) return;
    if (connecting) return connecting;
    connecting = (async () => {
      disconnect();
      setStatus('Loading model…', 'warn', true);
      await ensureModel(model);
      if (voiceTools().length && mode !== 'text' && !fillerClips.has(model)) { setStatus('Preparing voice…', 'warn', true); await prepareFillers(model); }
      setStatus('Connecting…', 'warn', true);
      await new Promise((resolve, reject) => {
        ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/realtime`);
        ws.onerror = () => reject(new Error('Could not open the realtime socket'));
        ws.onclose = () => { setStatus('Disconnected'); ws = null; };
        let first = true;
        ws.onmessage = (e) => {
          const ev = JSON.parse(e.data);
          if (first) {
            first = false;
            if (ev.type === 'error') return reject(new Error(ev.error?.message || 'Realtime error'));
            const a = ev.session?.audio || {};
            session = { inRate: a.input?.format?.rate || 16000, outRate: a.output?.format?.rate || 24000 };
            const cfg = { type: 'realtime', output_modalities: mode.split(',') };
            const tools = voiceTools();
            if (!instructionsEdited) instr.value = defaultInstr(); // the tool list may only have loaded after this panel was built
            if (tools.length) cfg.tools = tools; // fixed for the whole session: the protocol does not allow changing them later
            if (instr.value.trim()) cfg.instructions = instr.value.trim() + (tools.length && !instructionsEdited ? '\n\nList of tools:' : '');
            ws.send(JSON.stringify({ type: 'session.update', session: cfg }));
            ws.onmessage = handle;
            dirty = false;
            resolve();
          }
        };
      });
      setStatus('Ready', 'good');
    })();
    try { await connecting; } finally { connecting = null; }
  }

  function disconnect() {
    stopAudio();
    if (ws) { ws.onclose = null; try { ws.close(); } catch { /* closed */ } ws = null; }
  }

  function handle(e) {
    const ev = JSON.parse(e.data);
    switch (ev.type) {
      case 'response.created':
        turn = { ...addBubble('assistant', ''), text: '', pcm: [], first: 0, n: 0 };
        turn.bubble.innerHTML = '<span class="spinner"></span>';
        setStatus('Thinking…', 'accent', true);
        break;
      case 'response.output_text.delta':
        if (!turn) break;
        if (!turn.first) turn.first = performance.now();
        turn.text += ev.delta;
        if (!nudged && !turn.refused && voiceTools().length && CANT_LOOK_UP.test(turn.text)) { // stop it mid-sentence, before the refusal is spoken
          turn.refused = true;
          ws?.send(JSON.stringify({ type: 'response.cancel' }));
          stopAudio();
        }
        turn.bubble.innerHTML = markdown(turn.text) + '<span class="cursor"></span>';
        log.scrollTop = log.scrollHeight;
        break;
      case 'response.output_audio.delta': {
        if (!turn || turn.refused) break;
        waiting = false;
        if (filler) { filler.pause(); filler = null; } // the real answer starts: the waiting line gives way
        if (!turn.first) turn.first = performance.now();
        const pcm = b64ToPcm16(ev.delta);
        turn.pcm.push(pcm);
        player.push(pcm, session.outRate);
        setStatus('Speaking…', 'accent', true);
        break;
      }
      case 'response.done': {
        const calls = (ev.response?.output || []).filter((o) => o.type === 'function_call');
        if (turn) {
          turn.bubble.innerHTML = turn.text ? markdown(turn.text) : (calls.length ? '' : '<span class="muted">(speech only)</span>');
          if (turn.pcm.length) {
            const total = turn.pcm.reduce((n, c) => n + c.length, 0);
            const all = new Float32Array(total);
            let o = 0;
            for (const c of turn.pcm) { for (let i = 0; i < c.length; i++) all[o + i] = c[i] / 32768; o += c.length; }
            turn.body.append(h('audio', { controls: true, src: URL.createObjectURL(encodeWav(all, session.outRate)), style: { marginTop: '8px', maxWidth: '320px' } }));
            turn.body.append(h('div', { class: 'msg-meta' },
              h('span', {}, `${(total / session.outRate).toFixed(1)} s audio`),
              turn.first && sentAt && h('span', {}, `${Math.round(turn.first - sentAt)} ms to first output`)));
          } else if (turn.first && sentAt && !calls.length) {
            turn.body.append(h('div', { class: 'msg-meta' }, h('span', {}, `${Math.round(turn.first - sentAt)} ms to first output`)));
          }
          if (!turn.text && !turn.pcm.length && calls.length) turn.el.remove(); // a response that was only a tool call leaves no empty bubble
        }
        const spoke = !!turn?.pcm.length;
        if (turn && !calls.length && !nudged && voiceTools().length && ws?.readyState === WebSocket.OPEN && CANT_LOOK_UP.test(turn.text)) {
          nudged = true;
          turn.el.remove(); // the refusal is replaced by a searched answer
          turn = null;
          setStatus('Looking it up…', 'accent', true);
          ws.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'You can search the internet. Call web_search for this question now, then answer from the results.' }] } }));
          sentAt = performance.now();
          ws.send(JSON.stringify({ type: 'response.create' }));
          break;
        }
        turn = null;
        if (calls.length && callRounds < MAX_CALL_ROUNDS) {
          waiting = true;
          if (!spoke) sayFiller();
          runCalls(calls);
        }
        else { callRounds = 0; setStatus('Ready', 'good'); }
        break;
      }
      case 'error':
        toast(ev.error?.message || 'Realtime error', 'error');
        setStatus('Error', 'bad');
        break;
      default: break;
    }
  }

  async function toggleMic() {
    if (mic) return stopTalking();
    try {
      await connect();
      callEpoch++; callRounds = 0; nudged = false; waiting = false; // starting to speak abandons a lookup still in progress
      stopAudio();
      let buf = [];
      const step = Math.round(session.inRate * 0.1);
      const flush = (force) => {
        const n = buf.reduce((a, c) => a + c.length, 0);
        if (!n || (!force && n < step)) return;
        const all = new Float32Array(n);
        let o = 0;
        for (const c of buf) { all.set(c, o); o += c.length; }
        buf = [];
        ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify({
          type: 'input_audio_buffer.append', audio: pcm16ToB64(floatToPcm16(all)),
        }));
      };
      mic = new Mic({ onChunk: (c, rate) => { buf.push(resample(c, rate, session.inRate)); flush(false); } });
      mic.flushRest = () => flush(true);
      await mic.start();
      micBtn.classList.add('live');
      micBtn.title = 'Stop and send';
      viz.style.display = '';
      stopLevels = drawLevels(viz, mic.analyser);
      setStatus('Listening…', 'bad', true);
    } catch (e) {
      mic = null;
      toast(e.message, 'error');
      setStatus('Ready', 'good');
    }
  }

  async function stopTalking() {
    const m = mic; mic = null;
    m.flushRest();
    stopLevels?.(); viz.style.display = 'none';
    micBtn.classList.remove('live'); micBtn.title = 'Start talking';
    const { samples, rate, seconds } = await m.stop();
    if (seconds < 0.3) { setStatus('Ready', 'good'); toast('That was too short.'); return; }
    const wav = encodeWav(resample(samples, rate, 16000), 16000);
    addBubble('user', h('div', {}, h('div', { class: 'muted', style: { marginBottom: '4px' } }, `🎤 Voice message · ${seconds.toFixed(1)} s`),
      h('audio', { controls: true, src: URL.createObjectURL(wav), style: { maxWidth: '300px' } })));
    sentAt = performance.now();
    ws.send(JSON.stringify({ type: 'response.create' }));
    setStatus('Thinking…', 'accent', true);
  }

  async function sendText() {
    const text = textIn.value.trim();
    if (!text) return;
    textIn.value = '';
    try {
      await connect();
      stopAudio();
      addBubble('user', text);
      callEpoch++; callRounds = 0; nudged = false; waiting = false;
      sentAt = performance.now();
      ws.send(JSON.stringify({
        type: 'conversation.item.create',
        item: { type: 'message', role: 'user', content: [{ type: 'input_text', text }] },
      }));
      ws.send(JSON.stringify({ type: 'response.create' }));
    } catch (e) { toast(e.message, 'error'); setStatus('Error', 'bad'); }
  }

  textIn.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) sendText(); });
  micBtn.addEventListener('click', toggleMic);

  const newSession = () => { disconnect(); emptyLog(); setStatus('Not connected'); dirty = false; };

  const interrupt = () => { turn && ws?.send(JSON.stringify({ type: 'response.cancel' })); waiting = false; callEpoch++; stopAudio(); };
  const webBox = h('input', { type: 'checkbox', checked: toolstore.isGroupOn('web') || toolstore.prefs.enabledGroups.web === true, onchange: (e) => { toolstore.setGroupOn('web', e.target.checked); dirty = true; toast('Applies to the next conversation (starts a fresh session).'); } });
  const settings = h('div', { class: 'card voice-settings', style: { display: 'none' } },
    h('div', { class: 'field' }, h('label', {}, 'Reply format'), seg),
    h('label', { class: 'switch-row', title: 'Let it look things up on the web when it is not sure' }, h('span', {}, h('b', {}, 'Search the web when it does not know'), h('small', {}, 'Your question words are sent to Bing')), h('span', { class: 'switch' }, webBox, h('i'))),
    h('div', { class: 'field' }, h('label', {}, 'System prompt'), instr),
    h('p', { class: 'muted', style: { fontSize: '12px', margin: 0 } },
      'Audio streams to the model as you speak, so the reply starts right after you stop. Use headphones so the microphone does not pick up the speakers. Changes apply to the next session.'));
  const gear = h('button', { class: 'btn sm', title: 'Reply format and system prompt', onclick: () => { settings.style.display = settings.style.display === 'none' ? '' : 'none'; } }, icon('sliders', 14), 'Settings');

  const root = h('div', { class: 'voice' },
    h('div', { class: 'voice-top' }, status, h('div', { class: 'grow' }),
      h('button', { class: 'btn sm', onclick: interrupt }, icon('stop', 14), 'Interrupt'),
      h('button', { class: 'btn sm', onclick: newSession }, icon('refresh', 14), 'New session'), gear),
    settings, log,
    h('div', { class: 'voice-stage' }, micBtn, caption, viz,
      h('div', { class: 'voice-type' }, textIn)));
  emptyLog();
  root.destroy = () => { disconnect(); mic?.stop(); stopLevels?.(); };
  return root;
}
