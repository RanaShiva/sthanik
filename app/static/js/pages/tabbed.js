// A page made of a few existing pages behind one row of tabs. Each tab mounts its page into its own container (rebuilt on
// every visit to the tab); the last tab is remembered, and a link can pre-select one with sessionStorage 'lfm3.tab.<id>'.
import { h, icon } from '../ui.js';

export function tabbedPage({ id, title, subtitle, tabs }) {
  // tabs: [[key, label, icon, () => Promise<{ default: page } | { mount }>]]
  return {
    title, subtitle,
    mount(view) {
      const storeKey = `lfm3.tab.${id}`;
      let pick = null;
      try { pick = sessionStorage.getItem(storeKey); sessionStorage.removeItem(storeKey); } catch { /* ignore */ }
      if (!pick) { try { pick = localStorage.getItem(storeKey); } catch { /* ignore */ } }
      let key = tabs.some((t) => t[0] === pick) ? pick : tabs[0][0];
      const bar = h('div', { class: 'tabs', style: { marginBottom: '16px', alignSelf: 'flex-start' } },
        tabs.map(([k, label, ic]) => h('button', { class: 'tab', 'data-id': k, onclick: () => open(k) }, icon(ic), label)));
      const body = h('div');
      let handle = null, token = 0;
      const stop = () => { const d = typeof handle === 'function' ? handle : handle?.destroy; try { d?.(); } catch { /* ignore */ } handle = null; };
      async function open(k) {
        const my = ++token;
        stop();
        key = k;
        try { localStorage.setItem(storeKey, k); } catch { /* private mode */ }
        bar.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.id === k));
        const mod = await tabs.find((t) => t[0] === k)[3]();
        if (my !== token) return;
        const page = mod.default || mod;
        const holder = h('div');
        body.replaceChildren(holder);
        handle = page.mount(holder);
      }
      view.append(bar, body);
      open(key);
      return () => { token++; stop(); };
    },
  };
}
