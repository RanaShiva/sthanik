import { h, icon, toast } from '../ui.js';
import { CHAT_MODELS } from '../chatconfigs.js';
import { modelGate } from '../gate.js';
import { onStatus } from '../api.js';

const KEY = 'lfm3.chat.model';

export default {
  title: 'Chat',
  subtitle: 'Text or Omni: pick the model, everything else follows it',
  fill: true,
  keep: true, // conversations (and any reply in progress) survive navigating to another page or switching model
  mount(view) {
    const pre = (() => { try { const v = sessionStorage.getItem('lfm3.chat.preselect'); sessionStorage.removeItem('lfm3.chat.preselect'); return v; } catch { return null; } })();
    const prefilled = (() => { try { return JSON.parse(sessionStorage.getItem('lfm3.prefill') || 'null')?.model; } catch { return null; } })();
    let key = pre || (['text', 'omni'].includes(prefilled) ? prefilled : null) || (() => { try { return localStorage.getItem(KEY); } catch { return null; } })() || 'text';
    if (!CHAT_MODELS.some((m) => m.key === key)) key = 'text';

    const made = {}; // model key -> { chat, gate }, created the first time it is shown
    const slot = h('div', { style: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' } });
    const info = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    const bar = h('div', { class: 'tabs', style: { alignSelf: 'flex-start' } });
    const row = h('div', { class: 'row wrap', style: { marginBottom: '12px', gap: '12px' } }, bar, info);
    let shown = null;

    const tabs = CHAT_MODELS.map((m) => h('button', { class: 'tab', 'data-id': m.key, title: `${m.model} · ${m.sub}`, onclick: () => show(m.key) }, icon(m.icon), m.label));
    bar.append(...tabs);

    // Text and Omni run different weights while safe browsing is off, so their labels follow the status.
    let labels = '';
    const names = {};
    const showInfo = () => {
      const def = CHAT_MODELS.find((m) => m.key === shown);
      info.textContent = `${names[shown] || def.model} · ${def.sub}`;
    };
    const offStatus = onStatus((s) => {
      for (const m of CHAT_MODELS) names[m.key] = s.models[s.slots?.[m.key] || m.key]?.label || null;
      const sig = JSON.stringify(names);
      if (sig !== labels) { labels = sig; if (shown) showInfo(); }
    });

    function show(k, remember = true) {
      if (shown === k) return;
      if (shown) made[shown].chat.suspend?.(); // detached, not destroyed: a reply still streaming keeps going
      shown = k;
      const def = CHAT_MODELS.find((m) => m.key === k);
      if (!made[k]) made[k] = { gate: modelGate(k), chat: def.make({ onNeed: (to) => { show(to); toast(`Switched to ${CHAT_MODELS.find((m) => m.key === to)?.label || to}`, 'info', 2500); } }) };
      else made[k].chat.resume?.();
      slot.replaceChildren(made[k].gate, made[k].chat);
      tabs.forEach((t) => t.classList.toggle('active', t.dataset.id === k));
      showInfo();
      if (remember) { try { localStorage.setItem(KEY, k); } catch { /* private mode */ } }
    }

    view.append(row, slot);
    show(key, false);
    return {
      destroy: () => { offStatus(); Object.values(made).forEach((m) => { m.gate.destroy(); m.chat.destroy(); }); },
      suspend: () => made[shown]?.chat.suspend?.(),
      resume: () => {
        let want = null;
        try { want = sessionStorage.getItem('lfm3.chat.preselect'); sessionStorage.removeItem('lfm3.chat.preselect'); } catch { /* ignore */ }
        if (want && want !== shown && CHAT_MODELS.some((m) => m.key === want)) show(want);
        else made[shown]?.chat.resume?.();
      },
    };
  },
};
