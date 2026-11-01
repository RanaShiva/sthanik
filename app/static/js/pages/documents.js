import { h, icon, toast } from '../ui.js';
import { fileCard } from '../chatview.js';
import { openDocEditor } from '../docedit.js';
import { docRow, groupByDay, kindOf, matchScore } from '../docpicker.js';
import { bulkSelect } from '../bulk.js';

const FORMATS = [['pdf', 'PDF document'], ['docx', 'Word (.docx)'], ['pptx', 'Slides (.pptx)'], ['slides-pdf', 'Slides (PDF)'], ['html', 'HTML page'], ['md', 'Markdown']];
const TYPES = [['all', 'All'], ['document', 'Documents'], ['slides', 'Slides'], ['sheet', 'Sheets']];
const SORTS = [['new', 'Newest first'], ['old', 'Oldest first'], ['name', 'Name A–Z'], ['size', 'Largest first']];
const PAGE = 30;

export default {
  title: 'Documents',
  subtitle: 'Everything the models made: search, filter, open, edit',
  mount(view) {
    let docs = [], q = '', type = 'all', sort = 'new', shown = PAGE, pdfOk = true, pool = [];
    const sel = bulkSelect({
      noun: 'document', visible: () => pool.map((d) => d.name), redraw: () => draw(), done: () => load(),
      remove: async (name) => (await fetch(`/api/documents/${encodeURIComponent(name)}`, { method: 'DELETE' })).ok,
    });
    const list = h('div', { class: 'stack', style: { gap: '4px' } });
    const count = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    const created = h('div');

    const search = h('input', { class: 'input grow', placeholder: 'Search by title or file name…', 'aria-label': 'Search documents' });
    const chips = h('div', { class: 'dp-chips', style: { padding: 0 } }, TYPES.map(([k, l]) => h('button', { type: 'button', 'data-k': k, onclick: () => { type = k; shown = PAGE; draw(); } }, l)));
    const sorter = h('select', { class: 'input', style: { width: 'auto' }, 'aria-label': 'Sort' }, SORTS.map(([v, l]) => h('option', { value: v }, l)));
    let t = 0;
    search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { q = search.value.trim(); shown = PAGE; draw(); }, 100); });
    sorter.addEventListener('change', () => { sort = sorter.value; shown = PAGE; draw(); });

    const load = async () => {
      const r = await (await fetch('/api/documents')).json();
      pdfOk = r.pdf;
      docs = r.documents || [];
      draw();
    };

    function actions(f) {
      return h('div', { class: 'row', style: { gap: '6px', flex: 'none' } },
        f.format === 'pdf' && h('a', { class: 'btn sm ghost', href: `${f.url}?inline=1`, target: '_blank', rel: 'noopener' }, 'Open'),
        f.editable && h('button', { class: 'btn sm', title: 'Edit this file, or ask the model to change it', onclick: () => openDocEditor(f, { onSaved: load }) }, icon('pencil', 14), 'Edit'),
        f.editable && h('button', { class: 'btn sm', title: 'Open a chat that works on this document', onclick: () => {
          try { sessionStorage.setItem('lfm3.chat.activedoc', JSON.stringify(f)); } catch { /* private mode */ }
          location.hash = '#/chat';
        } }, icon('chat', 14), 'Chat'),
        h('a', { class: 'btn sm', href: f.url, download: f.name, title: 'Download' }, icon('download', 14)),
        h('button', { class: 'btn sm danger', title: 'Delete this file', onclick: async () => {
          if (!confirm(`Delete “${f.title || f.name}”?`)) return;
          const r = await fetch(`/api/documents/${encodeURIComponent(f.name)}`, { method: 'DELETE' });
          if (!r.ok) return toast('Could not delete the file', 'error');
          load();
        } }, icon('trash', 14)));
    }

    function draw() {
      chips.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.k === type));
      pool = docs.filter((d) => (type === 'all' || kindOf(d) === type) && matchScore(d, q) > 0);
      if (q) pool.sort((a, b) => matchScore(b, q) - matchScore(a, q) || b.created - a.created);
      else if (sort === 'old') pool.sort((a, b) => a.created - b.created);
      else if (sort === 'name') pool.sort((a, b) => (a.title || a.name).localeCompare(b.title || b.name));
      else if (sort === 'size') pool.sort((a, b) => b.size - a.size);
      else pool.sort((a, b) => b.created - a.created);
      sel.refresh();
      count.textContent = pool.length === docs.length ? `${docs.length} file${docs.length === 1 ? '' : 's'}` : `${pool.length} of ${docs.length}`;
      const page = pool.slice(0, shown);
      const row = (f) => sel.active
        ? h('div', { class: `doc-row2 sel-row${sel.has(f.name) ? ' picked' : ''}` },
          h('input', { type: 'checkbox', class: 'sel-box', checked: sel.has(f.name), 'aria-label': `Select ${f.title || f.name}`, onchange: () => { sel.toggle(f.name); draw(); } }), docRow(f, { onclick: () => { sel.toggle(f.name); draw(); } }))
        : h('div', { class: 'doc-row2' }, docRow(f, { onclick: () => f.format === 'pdf' ? window.open(`${f.url}?inline=1`, '_blank', 'noopener') : f.editable ? openDocEditor(f, { onSaved: load }) : (location.href = f.url) }), actions(f));
      const kids = [];
      if (sort === 'new' && !q) for (const g of groupByDay(page)) kids.push(h('div', { class: 'dp-group', style: { position: 'static' } }, g.label), ...g.items.map(row));
      else kids.push(...page.map(row));
      if (!pool.length) kids.push(h('div', { class: 'card card-pad muted' }, docs.length ? 'No documents match. Clear the search or change the filter.' :
        'Nothing here yet. Ask a model in Chat to “make a PDF report about…”, press the download icon under any reply, or use “New document” above.'));
      if (pool.length > shown) kids.push(h('button', { type: 'button', class: 'dp-more', onclick: () => { shown += PAGE; draw(); } }, `Show ${Math.min(PAGE, pool.length - shown)} more (${pool.length - shown} left)`));
      list.replaceChildren(...kids);
    }

    // Making a file by hand: tucked away, since most documents come from the models.
    const title = h('input', { class: 'input', placeholder: 'Title' });
    const fmt = h('select', { class: 'input', style: { maxWidth: '200px' } }, FORMATS.map(([v, l]) => h('option', { value: v }, l)));
    const text = h('textarea', { class: 'input mono', rows: 9, spellcheck: false,
      placeholder: '# Project update\n\nWrite Markdown here: headings, lists, tables and code all work.\n\nFor slides, start each slide with "## Slide title" followed by bullets.' });
    const make = h('button', { class: 'btn primary' }, icon('zap'), 'Create file');
    make.addEventListener('click', async () => {
      if (!text.value.trim()) return toast('Write something first.');
      make.disabled = true;
      try {
        const r = await fetch('/api/documents/export', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: title.value || 'Document', markdown: text.value, format: fmt.value }) });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Failed');
        created.replaceChildren(fileCard(d));
        load();
      } catch (e) { toast(e.message, 'error', 8000); }
      make.disabled = false;
    });
    const newDoc = h('details', { class: 'card card-pad' },
      h('summary', { class: 'panel-title', style: { cursor: 'pointer', margin: 0 } }, icon('pencil'), 'New document from Markdown'),
      h('div', { class: 'stack', style: { marginTop: '12px' } },
        h('div', { class: 'row wrap' }, h('div', { class: 'grow' }, title), fmt), text,
        h('div', { class: 'row wrap' }, make, h('div', { class: 'grow' }, created)),
        h('p', { class: 'muted', style: { fontSize: '12.5px', margin: 0 } }, 'Models can also create these files themselves: ask in Chat for a PDF, a Word file, a slide deck or a spreadsheet.',
          !pdfOk && ' PDF export needs Chrome, Chromium or Brave installed.')));

    view.append(
      h('div', { class: 'stack', style: { gap: '12px' } },
        h('div', { class: 'row wrap', style: { gap: '10px' } }, search, sorter),
        h('div', { class: 'row wrap', style: { gap: '10px' } }, chips, h('div', { class: 'grow' }), count),
        sel.bar, list, newDoc));
    load().catch((e) => toast(e.message, 'error'));
  },
};
