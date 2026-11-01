// Audio utilities on LFM3-Audio: transcribe a recording, read text aloud. (The spoken conversation is in voice.js.)
import { h, icon, toast, downloadBlob, fmtBytes } from './ui.js';
import { ensureModel, streamChat, synthesize, LANGS } from './api.js';
import { Mic, fileToWav, encodeWav, resample, blobToB64, drawLevels } from './audio.js';

const langSelect = (value = 'en') => {
  const s = h('select', { class: 'input' }, Object.entries(LANGS).map(([k, v]) => h('option', { value: k }, v)));
  s.value = value;
  return s;
};

export function transcribeTab() {
  let clip = null; // {blob, b64, seconds, name}
  let mic = null, stopLevels = null, abort = null;
  const lang = langSelect();
  const player = h('audio', { controls: true, style: { display: 'none' } });
  const viz = h('canvas', { class: 'viz', style: { display: 'none' } });
  const fileInput = h('input', { type: 'file', accept: 'audio/*,video/*', hidden: true });
  const info = h('div', { class: 'muted' });
  const drop = h('div', { class: 'drop' }, icon('upload', 26), h('div', {}, h('b', {}, 'Drop an audio file'), ' or click to browse'),
    h('div', { style: { fontSize: '12px', marginTop: '4px' } }, 'WAV, MP3, FLAC, OGG, M4A…'));
  const micBtn = h('button', { class: 'btn' }, icon('mic'), 'Record');
  const runBtn = h('button', { class: 'btn primary', disabled: true }, icon('text'), 'Transcribe');
  const result = h('div', { class: 'result placeholder' }, 'The transcript will appear here.');
  const stats = h('div', { class: 'stat-row' });
  const copyBtn = h('button', { class: 'btn sm', disabled: true }, icon('copy', 14), 'Copy');

  async function setClip(blob, seconds, name) {
    clip = { blob, seconds, name, b64: await blobToB64(blob) };
    player.src = URL.createObjectURL(blob);
    player.style.display = '';
    info.textContent = `${name} · ${seconds.toFixed(1)} s`;
    runBtn.disabled = false;
  }

  async function takeFile(f) {
    if (!f) return;
    try {
      info.textContent = 'Decoding…';
      const { blob, seconds } = await fileToWav(f);
      await setClip(blob, seconds, f.name);
    } catch (e) { toast(`Couldn't decode ${f.name}: ${e.message}`, 'error'); info.textContent = ''; }
  }

  drop.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => takeFile(fileInput.files[0]));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); takeFile(e.dataTransfer.files[0]); });

  micBtn.addEventListener('click', async () => {
    if (mic) {
      const m = mic; mic = null;
      stopLevels?.(); viz.style.display = 'none';
      micBtn.replaceChildren(icon('mic'), 'Record'); micBtn.classList.remove('danger');
      const { samples, rate, seconds } = await m.stop();
      if (seconds > 0.3) await setClip(encodeWav(resample(samples, rate, 16000), 16000), seconds, 'Recording');
      return;
    }
    try {
      mic = new Mic(); await mic.start();
      viz.style.display = '';
      stopLevels = drawLevels(viz, mic.analyser);
      micBtn.replaceChildren(icon('stop', 14), 'Stop'); micBtn.classList.add('danger');
    } catch (e) { mic = null; toast(`Microphone unavailable: ${e.message}`, 'error'); }
  });

  runBtn.addEventListener('click', async () => {
    if (abort) return abort.abort();
    abort = new AbortController();
    runBtn.replaceChildren(icon('stop', 14), 'Stop');
    result.className = 'result'; result.textContent = '';
    stats.replaceChildren(); copyBtn.disabled = true;
    let text = '';
    try {
      await ensureModel('audio');
      const t0 = performance.now();
      const res = await streamChat({
        signal: abort.signal,
        messages: [
          { role: 'system', content: `Perform ASR in ${LANGS[lang.value]}.` },
          { role: 'user', content: [{ type: 'input_audio', input_audio: { data: clip.b64, format: 'wav' } }] },
        ],
        params: { temperature: 0, max_tokens: 2048 },
        onDelta: ({ content }) => { text += content; result.textContent = text; },
      });
      const secs = (performance.now() - t0) / 1000;
      stats.replaceChildren(
        h('div', { class: 'stat' }, h('b', {}, clip.seconds.toFixed(1) + ' s'), h('span', {}, 'audio')),
        h('div', { class: 'stat' }, h('b', {}, secs.toFixed(1) + ' s'), h('span', {}, 'processing')),
        h('div', { class: 'stat' }, h('b', {}, (clip.seconds / secs).toFixed(1) + '×'), h('span', {}, 'real-time factor')),
        ...(res.usage ? [h('div', { class: 'stat' }, h('b', {}, String(res.usage.completion_tokens)), h('span', {}, 'tokens'))] : []));
      copyBtn.disabled = !text;
      if (!text.trim()) { result.className = 'result placeholder'; result.textContent = 'No speech detected.'; }
    } catch (e) {
      if (e.name !== 'AbortError') { result.className = 'result'; result.replaceChildren(h('span', { class: 'error-text' }, e.message)); }
    } finally {
      abort = null;
      runBtn.replaceChildren(icon('text'), 'Transcribe');
    }
  });
  copyBtn.addEventListener('click', () => { navigator.clipboard.writeText(result.textContent); toast('Copied'); });

  const el = h('div', { class: 'grid-2' },
    h('div', { class: 'card card-pad stack' },
      h('div', { class: 'panel-title' }, icon('audio'), 'Audio input'),
      drop, fileInput, h('div', { class: 'row' }, micBtn, h('div', { class: 'grow' }, info)), viz, player,
      h('div', { class: 'field' }, h('label', {}, 'Language'), lang), h('div', {}, runBtn)),
    h('div', { class: 'card card-pad stack' },
      h('div', { class: 'row' }, h('div', { class: 'panel-title grow' }, icon('text'), 'Transcript'), copyBtn),
      result, stats));
  el.destroy = () => { abort?.abort(); mic?.stop(); stopLevels?.(); };
  return el;
}

export function speakTab() {
  let voice = 'female', abort = null;
  const text = h('textarea', { class: 'input', rows: 6, placeholder: 'Type something for the model to say…' });
  text.value = 'Hello! I am a Liquid foundation model running entirely on your own GPU. It is nice to meet you.';
  const lang = langSelect();
  const seed = h('input', { class: 'input', type: 'number', placeholder: 'random', min: -1 });
  const seg = h('div', { class: 'seg' }, ['female', 'male'].map((v) => h('button', {
    class: v === voice ? 'active' : '', 'data-v': v,
    onclick: () => { voice = v; seg.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === v)); },
  }, v[0].toUpperCase() + v.slice(1))));
  const runBtn = h('button', { class: 'btn primary' }, icon('volume'), 'Generate speech');
  const out = h('div', { class: 'result placeholder' }, 'Generated audio will appear here.');
  const stats = h('div', { class: 'stat-row' });

  runBtn.addEventListener('click', async () => {
    if (abort) return abort.abort();
    if (!text.value.trim()) return toast('Enter some text first.');
    abort = new AbortController();
    runBtn.replaceChildren(icon('stop', 14), 'Cancel');
    out.className = 'result placeholder'; out.replaceChildren(h('span', { class: 'spinner' })); stats.replaceChildren();
    try {
      await ensureModel('audio');
      const t0 = performance.now();
      const blob = await synthesize({ text: text.value.trim(), voice, lang: lang.value, seed: seed.value === '' ? -1 : +seed.value, signal: abort.signal });
      const secs = (performance.now() - t0) / 1000;
      const url = URL.createObjectURL(blob);
      const probe = new Audio(url);
      await new Promise((r) => { probe.onloadedmetadata = r; probe.onerror = r; });
      const dur = probe.duration || 0;
      const audio = h('audio', { controls: true, src: url, autoplay: true });
      out.className = 'result'; out.replaceChildren(audio, h('div', { style: { marginTop: '12px' } },
        h('button', { class: 'btn sm', onclick: () => downloadBlob(blob, 'lfm3-speech.wav') }, icon('download', 14), 'Download WAV')));
      stats.replaceChildren(
        h('div', { class: 'stat' }, h('b', {}, dur.toFixed(1) + ' s'), h('span', {}, 'audio')),
        h('div', { class: 'stat' }, h('b', {}, secs.toFixed(1) + ' s'), h('span', {}, 'generation')),
        h('div', { class: 'stat' }, h('b', {}, dur ? (dur / secs).toFixed(1) + '×' : '–'), h('span', {}, 'real-time factor')),
        h('div', { class: 'stat' }, h('b', {}, fmtBytes(blob.size)), h('span', {}, 'file')));
    } catch (e) {
      if (e.name !== 'AbortError') { out.className = 'result'; out.replaceChildren(h('span', { class: 'error-text' }, e.message)); }
      else { out.className = 'result placeholder'; out.textContent = 'Cancelled.'; }
    } finally {
      abort = null;
      runBtn.replaceChildren(icon('volume'), 'Generate speech');
    }
  });

  const el = h('div', { class: 'grid-2' },
    h('div', { class: 'card card-pad stack' },
      h('div', { class: 'panel-title' }, icon('text'), 'Text to speak'),
      text,
      h('div', { class: 'row wrap' },
        h('div', { class: 'field' }, h('label', {}, 'Voice'), seg),
        h('div', { class: 'field grow' }, h('label', {}, 'Language'), lang),
        h('div', { class: 'field', style: { width: '110px' } }, h('label', {}, 'Seed'), seed)),
      h('div', {}, runBtn)),
    h('div', { class: 'card card-pad stack' }, h('div', { class: 'panel-title' }, icon('volume'), 'Result'), out, stats));
  el.destroy = () => abort?.abort();
  return el;
}
