// Long-term memory: the short facts every conversation is given. View, add, edit, pin and delete them; switch memory off.
import { h, icon, toast, timeAgo } from './ui.js';
import { bulkSelect } from './bulk.js';

const api = async (url, method = 'GET', body) => {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `Request failed (${r.status})`);
  return d;
};

export function memoryPanel() {
  const list = h('div', { class: 'stack', style: { gap: '8px' } });
  const input = h('input', { class: 'input grow', placeholder: 'e.g. “I prefer short answers.” or “My company is Acme Textiles.”', maxlength: 240, 'aria-label': 'New memory' });
  const addBtn = h('button', { class: 'btn primary' }, icon('plus', 15), 'Remember');
  const sw = h('input', { type: 'checkbox' });
  const count = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
  let data = { enabled: true, items: [] };
  const sel = bulkSelect({
    noun: 'memory', visible: () => data.items.map((i) => i.id), redraw: () => draw(), done: () => load(),
    remove: async (id) => { try { await api(`/api/memory/${id}`, 'DELETE'); return true; } catch { return false; } },
  });

  const draw = () => {
    sw.checked = data.enabled;
    sel.refresh();
    count.textContent = `${data.items.length} of 100`;
    list.replaceChildren(...(data.items.length ? data.items.map((it) => {
      const text = h('input', { class: 'input mem-text', value: it.text, maxlength: 240, 'aria-label': 'Fact' });
      text.addEventListener('change', async () => { try { await api(`/api/memory/${it.id}`, 'PUT', { text: text.value }); toast('Updated'); } catch (e) { toast(e.message, 'error'); text.value = it.text; } });
      if (sel.active) return h('label', { class: `mem-row sel-row${sel.has(it.id) ? ' picked' : ''}` },
        h('input', { type: 'checkbox', class: 'sel-box', checked: sel.has(it.id), 'aria-label': 'Select this fact', onchange: () => { sel.toggle(it.id); draw(); } }), h('span', { class: 'mem-text-ro' }, it.text));
      return h('div', { class: `mem-row${it.pinned ? ' pinned' : ''}` }, text,
        h('small', { class: 'muted mem-meta' }, `${it.source === 'model' ? 'noted by the model' : 'added by you'} · ${timeAgo(it.updated)}`),
        h('button', { class: 'icon-btn', title: it.pinned ? 'Unpin' : 'Pin to the top', onclick: async () => { await api(`/api/memory/${it.id}`, 'PUT', { pinned: !it.pinned }); load(); } }, icon('check', 15)),
        h('button', { class: 'icon-btn', title: 'Forget this', onclick: async () => { data = await api(`/api/memory/${it.id}`, 'DELETE'); draw(); } }, icon('trash', 15)));
    }) : [h('div', { class: 'card card-pad muted' }, 'Nothing remembered yet. Tell the model “remember that…”, or add a fact above. Facts are short sentences that go into every conversation.')]));
  };
  async function load() { try { data = await api('/api/memory'); draw(); } catch { /* backend restarting */ } }
  const add = async () => {
    if (!input.value.trim()) return;
    try { await api('/api/memory', 'POST', { text: input.value }); input.value = ''; load(); } catch (e) { toast(e.message, 'error'); }
  };
  addBtn.addEventListener('click', add);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
  sw.addEventListener('change', async () => { try { data = await api('/api/memory/settings', 'PUT', { enabled: sw.checked }); } catch (e) { toast(e.message, 'error'); } draw(); });

  const el = h('div', { class: 'stack' },
    h('div', { class: 'card card-pad stack' },
      h('label', { class: 'switch-row' }, h('span', {}, h('b', {}, 'Use memory in conversations'), h('small', {}, 'The facts below are shown to the model in every chat, voice and scheduled run.')),
        h('span', { class: 'switch' }, sw, h('i'))),
      h('div', { class: 'row wrap' }, input, addBtn), h('div', { class: 'row' }, count)),
    sel.bar, list);
  load();
  return el;
}
