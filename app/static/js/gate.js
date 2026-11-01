// Banner shown at the top of a tool page when its model isn't the one resident on the GPU.
import { h, icon, toast } from './ui.js';
import { onStatus, ensureModel } from './api.js';

export function modelGate(key) {
  const el = h('div');
  let busy = false;
  let sig = '';
  const render = (s) => {
    if (!s) return;
    const m = s.models[s.slots?.[key] || key];
    const l = s.llama;
    // Status is polled every 1.5 s; rebuilding an unchanged banner made the page flicker.
    const next = JSON.stringify([m.downloaded, m.label, l.key, l.state, l.error, busy]);
    if (next === sig) return;
    sig = next;
    el.replaceChildren();
    const ready = l.key === key && l.state === 'ready';
    if (ready) return;
    const loading = l.key === key && l.state === 'loading';
    const failed = l.key === key && l.state === 'error';
    if (!m.downloaded) {
      el.append(h('div', { class: 'banner error' }, icon('info'),
        h('div', { class: 'grow' }, h('b', {}, `${m.label} isn't downloaded yet. `), 'Its files are still being fetched — check back shortly.')));
      return;
    }
    const other = l.key && l.state !== 'idle' && l.key !== key ? s.models[l.weights || l.key]?.label : null;
    el.append(h('div', { class: failed ? 'banner error' : 'banner' },
      loading ? h('span', { class: 'spinner' }) : icon(failed ? 'info' : 'cpu'),
      h('div', { class: 'grow' },
        h('b', {}, loading ? `Loading ${m.label}…` : failed ? `${m.label} failed to start. ` : `${m.label} powers this tool. `),
        loading ? 'Weights are being moved onto the GPU.' : failed ? (l.error || '') :
          other ? `${other} is loaded; loading this will swap it out (your GPU holds one model at a time).` : 'No model is loaded yet — it loads automatically on first use, or load it now.'),
      !loading && h('button', {
        class: 'btn primary sm', disabled: busy,
        onclick: async () => {
          busy = true; render(s);
          try { await ensureModel(key); } catch (e) { toast(e.message, 'error'); }
          busy = false;
        },
      }, icon('power'), failed ? 'Retry' : 'Load model')));
  };
  const off = onStatus(render);
  el.destroy = off;
  return el;
}
