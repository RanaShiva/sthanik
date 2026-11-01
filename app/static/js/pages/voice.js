import { h, icon } from '../ui.js';
import { modelGate } from '../gate.js';
import { createVoice } from '../voice.js';
import { transcribeTab, speakTab } from '../audiotools.js';

// One row of modes, no nested tabs: a live conversation, transcribe a recording, or read text aloud.
const MODES = [
  ['talk', 'Conversation', 'mic2'],
  ['transcribe', 'Transcribe', 'audio'],
  ['speak', 'Read aloud', 'volume'],
];
const VOICES = [['audio', 'Audio-2B'], ['omni', 'Omni-2B']];
const KEY = 'lfm3.voice';

export default {
  title: 'Voice',
  subtitle: 'Talk to a model, transcribe recordings, read text aloud',
  mount(view) {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch { /* ignore */ }
    let mode = MODES.some((m) => m[0] === saved.mode) ? saved.mode : 'talk';
    let voice = VOICES.some((v) => v[0] === saved.voice) ? saved.voice : 'audio';
    const remember = () => { try { localStorage.setItem(KEY, JSON.stringify({ mode, voice })); } catch { /* private mode */ } };

    const gateSlot = h('div');
    const body = h('div', { style: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' } });
    const modeBar = h('div', { class: 'tabs' }, MODES.map(([id, label, ic]) => h('button', { class: 'tab', 'data-id': id, onclick: () => open(id) }, icon(ic), label)));
    const who = h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'Voice model', title: 'Which model you talk to' }, VOICES.map(([v, l]) => h('option', { value: v }, l)));
    who.value = voice;
    who.addEventListener('change', () => { voice = who.value; remember(); open('talk', true); });
    const row = h('div', { class: 'row wrap', style: { marginBottom: '14px', gap: '12px' } }, modeBar, h('div', { class: 'grow' }), who);

    let current = null, gate = null;
    function open(next, force = false) {
      if (!force && next === mode && current) return;
      current?.destroy?.();
      gate?.destroy();
      mode = next;
      remember();
      modeBar.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.id === mode));
      const talking = mode === 'talk';
      who.style.display = talking ? '' : 'none';
      view.classList.toggle('fill', talking);
      if (talking) {
        gate = modelGate(voice);
        current = createVoice({
          model: voice, title: `Talk with ${voice === 'omni' ? 'LFM3-Omni-2B' : 'LFM3-Audio-2B'}`,
          blurb: voice === 'omni' ? 'A live spoken conversation: the model hears you directly and answers in speech and text.' : 'Speak naturally and the model answers in speech and text. Audio streams to the model while you talk.',
        });
      } else {
        gate = modelGate('audio');
        current = mode === 'transcribe' ? transcribeTab() : speakTab();
      }
      gateSlot.replaceChildren(gate);
      body.replaceChildren(current);
    }

    view.append(row, gateSlot, body);
    open(mode, true);
    return () => { gate?.destroy(); current?.destroy?.(); };
  },
};
