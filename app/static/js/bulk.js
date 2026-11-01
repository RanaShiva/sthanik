// Select several items in a Library list and delete them in one go.
//   const sel = bulkSelect({ noun: 'document', visible: () => [ids of everything the filters match], remove: async (id) => true|false, redraw, done, warn })
//   sel.bar goes above the list; while sel.active each row shows a checkbox and a click toggles it (sel.has(id), sel.toggle(id)).
import { h, icon, toast } from './ui.js';

export function bulkSelect({ noun, visible, remove, redraw, done, warn = '' }) {
  const picked = new Set();
  let active = false;

  const selectBtn = h('button', { type: 'button', class: 'btn sm', title: `Choose several ${noun}s to delete at once`, onclick: () => setActive(true) }, icon('check', 14), 'Select');
  const allBtn = h('button', { type: 'button', class: 'btn sm', onclick: () => { const ids = visible(); if (ids.length && ids.every((i) => picked.has(i))) picked.clear(); else ids.forEach((i) => picked.add(i)); paint(); redraw(); } });
  const count = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
  const delBtn = h('button', { type: 'button', class: 'btn sm danger', onclick: () => run() }, icon('trash', 14), 'Delete');
  const cancelBtn = h('button', { type: 'button', class: 'btn sm ghost', onclick: () => setActive(false) }, 'Cancel');
  const bar = h('div', { class: 'row wrap bulk-bar', style: { gap: '8px' } }, selectBtn, allBtn, count, h('div', { class: 'grow' }), delBtn, cancelBtn);

  function paint() {
    const ids = visible();
    for (const i of [...picked]) if (!ids.includes(i)) picked.delete(i); // a filter hid it: do not delete what you cannot see
    selectBtn.style.display = active ? 'none' : '';
    for (const el of [allBtn, count, delBtn, cancelBtn]) el.style.display = active ? '' : 'none';
    const all = ids.length > 0 && ids.every((i) => picked.has(i));
    allBtn.replaceChildren(all ? 'Clear selection' : `Select all ${ids.length}`);
    allBtn.disabled = !ids.length;
    count.textContent = picked.size ? `${picked.size} selected` : 'Nothing selected';
    delBtn.disabled = !picked.size;
    delBtn.replaceChildren(icon('trash', 14), picked.size ? `Delete ${picked.size}` : 'Delete');
  }

  function setActive(on) {
    active = on;
    if (!on) picked.clear();
    paint();
    redraw();
  }

  async function run() {
    const ids = [...picked];
    if (!ids.length || !confirm(`Delete ${ids.length} ${noun}${ids.length === 1 ? '' : 's'}? This cannot be undone.${warn ? `\n\n${warn}` : ''}`)) return;
    delBtn.disabled = true;
    let ok = 0, failed = 0;
    for (const id of ids) {
      count.textContent = `Deleting ${ok + failed + 1} of ${ids.length}…`;
      try { (await remove(id)) ? ok++ : failed++; } catch { failed++; }
    }
    toast(failed ? `Deleted ${ok}; ${failed} could not be deleted.` : `Deleted ${ok} ${noun}${ok === 1 ? '' : 's'}.`, failed ? 'error' : 'info');
    active = false;
    picked.clear();
    paint();
    await done();
  }

  paint();
  return {
    bar,
    get active() { return active; },
    has: (id) => picked.has(id),
    toggle(id) { if (picked.has(id)) picked.delete(id); else picked.add(id); paint(); },
    refresh: paint,
  };
}
