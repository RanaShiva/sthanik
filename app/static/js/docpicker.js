// A picker for documents that stays usable with hundreds of them: search, type filter, recent files grouped by day, the
// documents of this conversation first, paging, and full keyboard control (type to search, ↑ ↓ to move, Enter to choose, Esc).
import { h, icon, timeAgo, fmtBytes } from './ui.js';

const KIND_ICON = { pdf: 'filetext', docx: 'file', pptx: 'presentation', xlsx: 'table', html: 'globe', md: 'file', txt: 'file' };
const TYPES = [['all', 'All'], ['document', 'Documents'], ['slides', 'Slides'], ['sheet', 'Sheets']];
const PAGE = 40;
let cache = { at: 0, docs: [] };

export async function loadDocuments(force = false) {
  if (!force && Date.now() - cache.at < 4000) return cache.docs;
  try {
    const d = await (await fetch('/api/documents')).json();
    cache = { at: Date.now(), docs: d.documents || [] };
  } catch { /* backend restarting: keep what we had */ }
  return cache.docs;
}

export const dayLabel = (ts) => {
  const d = new Date(ts * 1000), n = new Date();
  const days = Math.round((new Date(n.getFullYear(), n.getMonth(), n.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return 'This week';
  if (days < 31) return 'This month';
  return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
};

export function groupByDay(items, key = 'created') {
  const out = [];
  for (const it of items) {
    const l = dayLabel(it[key]);
    if (!out.length || out.at(-1).label !== l) out.push({ label: l, items: [] });
    out.at(-1).items.push(it);
  }
  return out;
}

// Rank for a search: title starts with the words > title contains them > only the file name does.
export function matchScore(doc, q) {
  if (!q) return 1;
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const title = (doc.title || '').toLowerCase(), name = doc.name.toLowerCase();
  if (!words.every((w) => title.includes(w) || name.includes(w))) return 0;
  return title.startsWith(words[0]) ? 3 : words.every((w) => title.includes(w)) ? 2 : 1;
}

export const kindOf = (d) => d.kind || (d.format === 'pptx' ? 'slides' : d.format === 'xlsx' ? 'sheet' : 'document');

export function docRow(d, { active = false, onclick } = {}) {
  return h('button', { type: 'button', class: `dp-row${active ? ' on' : ''}`, onclick, 'data-name': d.name },
    h('span', { class: 'dp-ic' }, icon(KIND_ICON[d.format] || 'file', 18)),
    h('span', { class: 'dp-text' }, h('b', {}, d.title || d.name), h('small', {}, `${d.format.toUpperCase()} · ${fmtBytes(d.size)} · ${timeAgo(d.created)}`)),
    active && h('span', { class: 'dp-check' }, icon('check', 16)));
}

export async function openDocPicker({ anchor, activeName = null, mine = [], onPick, onClear, onClose, editableOnly = true } = {}) {
  document.querySelectorAll('.dp').forEach((e) => e.remove());
  let docs = (await loadDocuments(true)).filter((d) => !editableOnly || d.editable);
  let type = 'all', q = '', shown = PAGE, sel = 0;
  const mineSet = new Set(mine);

  const input = h('input', { class: 'input dp-search', placeholder: 'Search your documents…', 'aria-label': 'Search documents', autocomplete: 'off' });
  const chips = h('div', { class: 'dp-chips' }, TYPES.map(([k, l]) => h('button', { type: 'button', class: k === type ? 'on' : '', 'data-k': k, onclick: () => { type = k; shown = PAGE; sel = 0; draw(); } }, l)));
  const list = h('div', { class: 'dp-list', role: 'listbox' });
  const foot = h('div', { class: 'dp-foot' });
  const box = h('div', { class: 'dp', role: 'dialog', 'aria-label': 'Choose a document' },
    h('div', { class: 'dp-head' }, icon('search', 16), input), chips, list, foot);
  document.body.append(box);

  const close = () => { box.remove(); document.removeEventListener('mousedown', outside, true); document.removeEventListener('keydown', onKey, true); onClose?.(); };
  const outside = (e) => { if (!box.contains(e.target) && !anchor?.contains?.(e.target)) close(); };
  const pick = (d) => { close(); onPick?.(d); };

  let rows = []; // the visible documents in order, for keyboard selection
  function draw() {
    chips.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.k === type));
    const pool = docs.filter((d) => (type === 'all' || kindOf(d) === type) && matchScore(d, q) > 0);
    const ranked = q ? [...pool].sort((a, b) => matchScore(b, q) - matchScore(a, q) || b.created - a.created) : pool; // list_documents is already newest first
    const slice = ranked.slice(0, shown);
    rows = slice;
    const kids = [];
    const row = (d) => docRow(d, { active: d.name === activeName, onclick: () => pick(d) });
    if (!q) {
      const here = slice.filter((d) => mineSet.has(d.name));
      if (here.length) kids.push(h('div', { class: 'dp-group' }, 'In this conversation'), ...here.map(row));
      for (const g of groupByDay(slice.filter((d) => !mineSet.has(d.name)))) kids.push(h('div', { class: 'dp-group' }, g.label), ...g.items.map(row));
    } else {
      kids.push(h('div', { class: 'dp-group' }, `${ranked.length} match${ranked.length === 1 ? '' : 'es'}`), ...slice.map(row));
    }
    if (!ranked.length) kids.push(h('div', { class: 'dp-empty' }, docs.length ? `Nothing matches “${q}”.` : 'No editable documents yet. Ask a model to write one, or use Plan.'));
    if (ranked.length > shown) kids.push(h('button', { type: 'button', class: 'dp-more', onclick: () => { shown += PAGE; draw(); } }, `Show ${Math.min(PAGE, ranked.length - shown)} more (${ranked.length - shown} left)`));
    list.replaceChildren(...kids);
    sel = Math.min(sel, Math.max(0, slice.length - 1));
    mark();
    foot.replaceChildren(...[h('span', { class: 'muted' }, `${docs.length} document${docs.length === 1 ? '' : 's'}`), h('div', { class: 'grow' }),
      activeName && onClear && h('button', { type: 'button', class: 'btn sm ghost', onclick: () => { close(); onClear(); } }, 'Stop working on this'),
      h('a', { class: 'btn sm ghost', href: '#/library', onclick: close }, 'Open Library')].filter(Boolean));
  }
  function mark() {
    const els = [...list.querySelectorAll('.dp-row')];
    els.forEach((e, i) => e.classList.toggle('sel', i === sel));
    els[sel]?.scrollIntoView({ block: 'nearest' });
  }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, rows.length - 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); mark(); }
    else if (e.key === 'Enter' && rows[sel]) { e.preventDefault(); pick(rows[sel]); }
  }
  let t = 0;
  input.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { q = input.value.trim(); shown = PAGE; sel = 0; draw(); }, 80); });

  // Place it next to its anchor: above when there is room (the chat composer sits at the bottom), else below.
  const r = anchor?.getBoundingClientRect?.();
  const w = Math.min(460, innerWidth - 24);
  box.style.width = `${w}px`;
  if (r) {
    box.style.left = `${Math.max(12, Math.min(r.left, innerWidth - w - 12))}px`;
    const room = innerHeight - r.bottom;
    if (room < 380 && r.top > room) { box.style.bottom = `${innerHeight - r.top + 8}px`; box.style.maxHeight = `${Math.max(260, r.top - 20)}px`; }
    else { box.style.top = `${r.bottom + 8}px`; box.style.maxHeight = `${Math.max(260, room - 20)}px`; }
  } else { box.style.left = '50%'; box.style.top = '15%'; box.style.transform = 'translateX(-50%)'; }
  draw();
  document.addEventListener('mousedown', outside, true);
  document.addEventListener('keydown', onKey, true);
  input.focus();
  return { close };
}
