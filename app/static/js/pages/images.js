import { h, icon, toast, modal, timeAgo, fmtBytes } from '../ui.js';
import { onStatus } from '../api.js';
import { openDocPicker, groupByDay } from '../docpicker.js';
import { bulkSelect } from '../bulk.js';

const SIZE_LABEL = { square: 'Square', landscape: 'Landscape', portrait: 'Portrait', wide: 'Wide' };

export default {
  title: 'Images',
  subtitle: 'Make pictures on your own GPU with Z-Image-Turbo',
  mount(view) {
    let size = 'square';
    let sizes = { square: [768, 768] };
    let busy = false;
    let showDiagrams = false;
    let all = [], q = '', shownN = 48, shownList = [];
    const sel = bulkSelect({
      noun: 'picture', visible: () => shownList.map((i) => i.name), redraw: () => drawGallery(), done: () => load(),
      warn: 'Documents that use them will lose them the next time they are rebuilt.',
      remove: async (name) => (await fetch(`/api/images/${encodeURIComponent(name)}`, { method: 'DELETE' })).ok,
    });
    const find = h('input', { class: 'input', placeholder: 'Search pictures by what they show…', 'aria-label': 'Search pictures', style: { maxWidth: '340px' } });
    const countEl = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    let st = 0;
    find.addEventListener('input', () => { clearTimeout(st); st = setTimeout(() => { q = find.value.trim().toLowerCase(); shownN = 48; drawGallery(); }, 100); });
    let timer = 0;

    const banner = h('div');
    const prompt = h('textarea', { class: 'input', rows: 4, placeholder: 'Describe the picture: subject, setting, style, light. For example: “a watercolour of a lighthouse at dawn, soft pink clouds, calm sea”', 'aria-label': 'Prompt' });
    const seg = h('div', { class: 'seg' });
    const steps = h('input', { class: 'input', type: 'number', min: 1, max: 20, value: 8, 'aria-label': 'Steps' });
    const seed = h('input', { class: 'input', type: 'number', placeholder: 'random', 'aria-label': 'Seed' });
    const go = h('button', { class: 'btn primary' }, icon('wand', 16), 'Generate');
    const stop = h('button', { class: 'btn', style: { display: 'none' } }, 'Cancel');
    const progress = h('div', { class: 'plan-prog', style: { display: 'none' } }, h('span', { class: 'muted' }), h('div', { class: 'plan-bar' }, h('i')));
    const gallery = h('div', { class: 'img-grid' });
    const toggle = h('label', { class: 'plan-opt' }, h('input', { type: 'checkbox', onchange: (e) => { showDiagrams = e.target.checked; load(); } }), 'Show diagrams too');

    const paintSizes = () => {
      seg.replaceChildren(...Object.entries(sizes).map(([k, [w, hh]]) => h('button', { class: k === size ? 'active' : '', title: `${w} × ${hh}`, onclick: () => { size = k; paintSizes(); } }, SIZE_LABEL[k] || k)));
    };

    function paintStatus(s) {
      const im = s?.image;
      if (!im) return;
      if (im.sizes) { sizes = im.sizes; if (!sizes[size]) size = Object.keys(sizes)[0]; if (!seg.childNodes.length) paintSizes(); }
      banner.replaceChildren(...(im.available ? [] : [h('div', { class: 'banner error' }, icon('info'), h('div', { class: 'grow' },
        h('b', {}, 'The image model is not installed. '), `Missing: ${im.missing.join(', ')}. See the README (“Image generation”) for the download steps.`))]));
      busy = !!im.busy;
      go.disabled = !im.available || busy;
      stop.style.display = busy ? '' : 'none';
      progress.style.display = busy ? '' : 'none';
      if (busy) {
        const pct = im.steps ? Math.min(100, (im.step / im.steps) * 100) : 0;
        progress.firstChild.textContent = `${im.stage || 'Working'}${im.step ? ` · step ${im.step} of ${im.steps}` : ''} · ${Math.round(im.elapsed || 0)} s`;
        progress.lastChild.firstChild.style.width = `${pct}%`;
      }
    }

    async function pollStatus() {
      clearTimeout(timer);
      try { paintStatus(await (await fetch('/api/images/status')).json()); } catch { /* backend restarting */ }
      timer = setTimeout(pollStatus, busy ? 700 : 3000);
    }

    go.addEventListener('click', async () => {
      if (!prompt.value.trim()) { prompt.focus(); return toast('Describe the picture first.'); }
      go.disabled = true; busy = true; pollStatus();
      try {
        const r = await fetch('/api/images/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: prompt.value, size, steps: +steps.value || 8, seed: seed.value === '' ? null : +seed.value }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || 'The picture could not be made');
        toast('Picture ready');
        open(d);
      } catch (e) { if (e.message !== 'Cancelled.') toast(e.message, 'error', 9000); }
      busy = false; pollStatus(); load();
    });
    stop.addEventListener('click', () => fetch('/api/images/cancel', { method: 'POST' }));

    // Put this picture into an existing document: choose it with the document picker, then (optionally) the section.
    function addToDoc(img, close) {
      let chosen = null;
      const pickBtn = h('button', { type: 'button', class: 'btn', style: { flex: 1, minWidth: 0, justifyContent: 'space-between' } }, h('span', { class: 'ws-btn-text' }, 'Choose a document…'), icon('chevronDown', 14));
      const sec = h('select', { class: 'input', 'aria-label': 'Section', style: { flex: 1, minWidth: 0 }, disabled: true }, h('option', { value: '' }, 'A section without a picture'));
      const btn = h('button', { class: 'btn primary', disabled: true }, icon('plus', 15), 'Add');
      pickBtn.addEventListener('click', () => openDocPicker({ anchor: pickBtn, activeName: chosen?.name, onPick: async (d) => {
        chosen = d;
        pickBtn.querySelector('.ws-btn-text').textContent = d.title || d.name;
        btn.disabled = false; sec.disabled = false;
        try {
          const src = await (await fetch(`/api/documents/${encodeURIComponent(d.name)}/source`)).json();
          sec.replaceChildren(h('option', { value: '' }, 'A section without a picture'), ...(src.outline || []).filter((o) => o.level >= 2).map((o) => h('option', { value: o.title }, o.title)));
        } catch { /* keep the default */ }
      } }));
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const r = await fetch(`/api/documents/${encodeURIComponent(chosen.name)}/pictures`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ images: [img.name], section: sec.value || null }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || 'Could not add it');
          toast(`Added to “${chosen.title || chosen.name}”${d.added?.[0]?.section ? `, in “${d.added[0].section}”` : ''}`);
          close();
        } catch (e) { toast(e.message, 'error', 8000); btn.disabled = false; }
      });
      return h('div', { class: 'stack', style: { gap: '6px' } }, h('div', { class: 'muted', style: { fontSize: '12.5px' } }, 'Use this picture in a document'), h('div', { class: 'row wrap' }, pickBtn, sec, btn));
    }

    function open(img) {
      const md = `![${(img.prompt || 'picture').slice(0, 60)}](img:${img.name})`;
      const m = modal(img.kind === 'diagram' ? 'Diagram' : 'Picture', h('div', { class: 'stack' },
        h('img', { src: img.url, alt: img.prompt || '', style: { maxWidth: '100%', borderRadius: '12px', border: '1px solid var(--border)' } }),
        img.prompt && h('p', { style: { margin: 0 } }, img.prompt),
        img.origin === 'web' && h('div', { class: 'muted', style: { fontSize: '12.5px' } }, icon('globe', 13), ` From ${img.credit || 'the web'}`, /^https?:\/\//.test(img.source_url || '') && [' · ', h('a', { href: img.source_url, target: '_blank', rel: 'noopener noreferrer', style: { color: 'var(--accent)' } }, 'open the source page')]),
        h('small', { class: 'muted' }, [img.size_px && `${img.size_px[0]} × ${img.size_px[1]}`, img.seed != null && `seed ${img.seed}`, fmtBytes(img.size), timeAgo(img.created)].filter(Boolean).join(' · ')),
        img.kind !== 'diagram' && addToDoc(img, () => m.close()),
        h('div', { class: 'row wrap' },
          h('a', { class: 'btn', href: img.url, download: img.name }, icon('download', 15), 'Download'),
          h('button', { class: 'btn', title: 'Paste this line into a document, slide or chat to show the picture', onclick: async () => { await navigator.clipboard.writeText(md); toast('Copied the document reference'); } }, icon('copy', 15), 'Copy reference'),
          img.kind !== 'diagram' && img.origin !== 'web' && img.prompt && h('button', { class: 'btn', onclick: () => { prompt.value = img.prompt; if (img.seed != null) seed.value = img.seed; m.close(); window.scrollTo(0, 0); } }, 'Reuse prompt'),
          h('div', { class: 'grow' }),
          h('button', { class: 'btn danger', onclick: async () => {
            if (!confirm('Delete this picture? Documents that use it will lose it the next time they are rebuilt.')) return;
            const r = await fetch(`/api/images/${encodeURIComponent(img.name)}`, { method: 'DELETE' });
            if (!r.ok) return toast('Could not delete it', 'error');
            m.close(); load();
          } }, icon('trash', 15), 'Delete'))), { wide: true });
    }

    // ---- find a real picture on the web
    const webQ = h('input', { class: 'input grow', placeholder: 'Find a real photo: e.g. Konark Sun Temple, red panda, map of the Mauryan empire…', 'aria-label': 'Search the web for pictures' });
    const webGo = h('button', { class: 'btn' }, icon('search', 15), 'Search the web');
    const webOut = h('div', { class: 'web-grid' });
    const webNote = h('div', { class: 'muted', style: { fontSize: '12.5px' } });
    const searchWeb = async () => {
      if (!webQ.value.trim()) return webQ.focus();
      webGo.disabled = true; webNote.textContent = 'Searching…'; webOut.replaceChildren();
      try {
        const r = await fetch('/api/webimages/search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: webQ.value }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || 'The search failed');
        webNote.textContent = d.results.length ? `${d.results.length} results. Save the ones you want; they go into your library with their source recorded.` : 'No pictures found. Try other words.';
        webOut.replaceChildren(...d.results.map((c) => {
          const save = h('button', { class: 'btn sm primary' }, icon('download', 14), 'Save');
          save.addEventListener('click', async () => {
            save.disabled = true;
            try {
              const sr = await fetch('/api/webimages/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: c.id }) });
              const sd = await sr.json().catch(() => ({}));
              if (!sr.ok) throw new Error(sd.detail || 'Could not save it');
              save.replaceChildren(icon('check', 14), 'Saved'); toast('Saved to your pictures'); load();
            } catch (e) { toast(e.message, 'error', 7000); save.disabled = false; }
          });
          return h('div', { class: 'web-card' },
            h('img', { src: `/api/webimages/thumb/${c.id}`, alt: c.title, loading: 'lazy', onerror: (e) => { e.target.style.visibility = 'hidden'; } }),
            h('div', { class: 'web-meta' }, h('b', { title: c.title }, c.title), h('small', {}, `${c.source || 'web'}${c.width ? ` · ${c.width} × ${c.height}` : ''}`)), save);
        }));
      } catch (e) { webNote.textContent = ''; toast(e.message, 'error', 8000); }
      webGo.disabled = false;
    };
    webGo.addEventListener('click', searchWeb);
    webQ.addEventListener('keydown', (e) => { if (e.key === 'Enter') searchWeb(); });

    function drawGallery() {
      const words = q.split(/\s+/).filter(Boolean);
      const list = shownList = all.filter((i) => (showDiagrams || i.kind !== 'diagram') && words.every((w) => `${i.prompt || ''} ${i.name}`.toLowerCase().includes(w)));
      sel.refresh();
      countEl.textContent = list.length === all.length ? `${all.length} item${all.length === 1 ? '' : 's'}` : `${list.length} of ${all.length}`;
      const tile = (img) => h('button', { class: `img-tile${sel.active && sel.has(img.name) ? ' picked' : ''}`, title: img.prompt || img.name, onclick: () => { if (sel.active) { sel.toggle(img.name); drawGallery(); } else open(img); } },
        sel.active && h('span', { class: 'img-check' }, sel.has(img.name) ? icon('check', 14) : null),
        h('img', { src: img.url, alt: img.prompt || '', loading: 'lazy' }), img.origin === 'web' && h('span', { class: 'img-badge', title: `From ${img.credit || 'the web'}` }, 'Web'), h('span', { class: 'img-cap' }, img.prompt || img.name));
      const page = list.slice(0, shownN);
      const kids = [];
      for (const g of groupByDay(page)) kids.push(h('div', { class: 'img-day' }, g.label), ...g.items.map(tile));
      if (!list.length) kids.push(h('div', { class: 'card card-pad muted', style: { gridColumn: '1 / -1' } }, all.length ? 'No pictures match that search.' : 'No pictures yet. Describe one above, or ask the model in Chat to illustrate a document.'));
      if (list.length > shownN) kids.push(h('button', { type: 'button', class: 'dp-more', style: { gridColumn: '1 / -1' }, onclick: () => { shownN += 48; drawGallery(); } }, `Show ${Math.min(48, list.length - shownN)} more (${list.length - shownN} left)`));
      gallery.replaceChildren(...kids);
    }

    async function load() {
      try { all = (await (await fetch('/api/images')).json()).images; drawGallery(); } catch { /* backend restarting */ }
    }

    view.append(banner,
      h('div', { class: 'card card-pad stack' },
        prompt,
        h('div', { class: 'row wrap' },
          h('div', { class: 'field' }, h('label', {}, 'Shape'), seg),
          h('div', { class: 'field', style: { width: '90px' } }, h('label', {}, 'Steps'), steps),
          h('div', { class: 'field', style: { width: '130px' } }, h('label', {}, 'Seed'), seed),
          h('div', { class: 'grow' }), stop, go),
        progress,
        h('p', { class: 'muted', style: { margin: 0, fontSize: '12.5px' } }, 'The image model needs the whole GPU, so the chat model is unloaded while a picture is made and reloads on your next message. Expect a minute or two per picture on this laptop.')),
      h('div', { class: 'card card-pad stack' },
        h('div', { class: 'panel-title' }, icon('globe'), 'Find a picture on the web'),
        h('div', { class: 'row wrap' }, webQ, webGo), webNote, webOut,
        h('p', { class: 'muted', style: { margin: 0, fontSize: '12px' } }, 'Pictures from the web may be copyrighted. Sthanik records where each one came from and credits it in documents; check the licence before you publish anything.')),
      h('div', { class: 'section-title' }, 'Gallery', toggle),
      h('div', { class: 'row wrap', style: { gap: '10px', marginBottom: '12px' } }, find, h('div', { class: 'grow' }), countEl),
      sel.bar,
      gallery);
    const off = onStatus(paintStatus);
    paintSizes(); pollStatus(); load();
    return () => { off(); clearTimeout(timer); };
  },
};
