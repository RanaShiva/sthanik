import { h, icon, toast, modal, fmtBytes } from '../ui.js';
import { onStatus, loadModel, unloadModel, ensureModel } from '../api.js';
import { safeBrowsingSwitch } from '../safebrowsing.js';

const ICONS = { text: 'chat', vl: 'eye', audio: 'mic', omni: 'sparkles' };
const MOD_ICON = { text: 'text', image: 'image', video: 'video', audio: 'audio' };

export default {
  title: 'Models & System',
  subtitle: 'Weights on disk, what is resident on the GPU, and server logs',
  mount(view) {
    const grid = h('div', { class: 'model-grid' });
    const gpuBox = h('div', { class: 'card card-pad' });
    const logBox = h('div', { class: 'logbox' }, 'No server output yet.');
    let busy = null;

    // GPU readout: nodes built once and updated in place (util/temperature change every poll).
    const gv = {};
    const gpuRow = (label, key) => [h('dt', {}, label), (gv[key] = h('dd'))];
    const gpuList = h('dl', { class: 'kv' }, gpuRow('Device', 'name'), gpuRow('VRAM', 'vram'), gpuRow('Utilisation', 'util'), gpuRow('Temperature', 'temp'), gpuRow('Power', 'power'));
    const noGpu = h('p', { class: 'muted' }, 'No NVIDIA GPU readout. Whether the engine uses a graphics card, and which, is shown under Setup.');
    gpuBox.append(h('div', { class: 'panel-title' }, icon('cpu'), 'GPU'), gpuList, noGpu, h('a', { class: 'btn sm', href: '#/setup', style: { marginTop: '10px' } }, 'Hardware & setup…'));
    const put = (el, text) => {
  if (el.firstChild?.nodeType === 3) { if (el.firstChild.data !== text) el.firstChild.data = text; } else if (el.textContent !== text) el.textContent = text;
};

    let gridSig = '';
    const render = (s) => {
      if (!s) return;
      const g = s.gpu;
      gpuList.style.display = g ? '' : 'none';
      noGpu.style.display = g ? 'none' : '';
      if (g) {
        put(gv.name, g.name);
        put(gv.vram, `${(g.mem_used_mb / 1024).toFixed(2)} / ${(g.mem_total_mb / 1024).toFixed(1)} GB`);
        put(gv.util, `${g.util_pct}%`);
        put(gv.temp, `${g.temp_c} °C`);
        put(gv.power, g.power_w != null ? `${g.power_w.toFixed(0)} W` : '—');
      }
      const sig = JSON.stringify([s.safe_browsing, s.llama, Object.entries(s.models).map(([k, m]) => [k, m.downloaded, m.size_gb, m.ctx, m.label, m.repo])]);
      if (sig === gridSig) return;
      gridSig = sig;
      paintFiles();
      grid.replaceChildren(...Object.entries(s.models).map(([key, m]) => {
        const here = (s.llama.weights || s.llama.key) === key;
        const st = here ? s.llama.state : 'idle';
        const chip = !m.downloaded ? h('span', { class: 'chip warn' }, h('span', { class: 'dot pulse' }), 'Downloading / missing')
          : st === 'ready' ? h('span', { class: 'chip good' }, h('span', { class: 'dot' }), 'Loaded on GPU')
            : st === 'loading' ? h('span', { class: 'chip accent' }, h('span', { class: 'dot pulse' }), 'Loading…')
              : st === 'error' ? h('span', { class: 'chip bad' }, 'Failed')
                : h('span', { class: 'chip' }, 'On disk');
        return h('div', { class: `card model-card${here && st !== 'idle' ? ' active' : ''}` },
          h('div', { class: 'model-head' },
            h('div', { class: 'model-icon' }, icon(ICONS[key])),
            h('div', { class: 'grow' }, h('div', { class: 'model-name' }, m.label), h('div', { class: 'model-role' }, m.role)), chip),
          h('p', { class: 'muted' }, m.description),
          h('div', { class: 'row wrap' },
            m.inputs.map((i) => h('span', { class: 'chip' }, icon(MOD_ICON[i], 12), `${i} in`)),
            m.outputs.map((i) => h('span', { class: 'chip accent' }, icon(MOD_ICON[i], 12), `${i} out`))),
          h('dl', { class: 'kv' },
            h('dt', {}, 'Precision'), h('dd', {}, m.precision),
            h('dt', {}, 'Size on disk'), h('dd', {}, m.downloaded ? `${m.size_gb} GB` : '—'),
            h('dt', {}, 'Context'), h('dd', {}, `${m.ctx.toLocaleString()} tokens`),
            h('dt', {}, 'Source'), h('dd', {}, m.repo ? h('a', { href: `https://huggingface.co/${m.repo}`, target: '_blank', rel: 'noopener', style: { color: 'var(--accent)' } }, m.repo.replace('LiquidAI/', '')) : 'Local file')),
          here && st === 'ready' && s.llama.load_seconds != null && h('div', { class: 'muted', style: { fontSize: '12px' } }, `Loaded in ${s.llama.load_seconds} s`),
          st === 'error' && h('div', { class: 'error-text', style: { fontSize: '12px' } }, s.llama.error),
          h('div', { class: 'row', style: { marginTop: 'auto' } },
            h('button', { class: 'btn', title: 'Use different weights for this slot', onclick: () => openChange(key) }, icon('sliders', 15), 'Change…'),
            st === 'ready' || st === 'loading'
              ? h('button', { class: 'btn danger', onclick: async () => { await unloadModel(); toast(`${m.label} unloaded`); } }, icon('power'), 'Unload')
              : m.backs
                ? h('span', { class: 'muted', style: { fontSize: '12px' } }, `Replaces ${s.models[m.backs].label} while safe browsing is off.`)
                : h('button', { class: 'btn primary', disabled: !m.downloaded || busy, onclick: async () => {
                busy = key;
                try { await ensureModel(key); } catch (e) { toast(e.message, 'error'); }
                busy = null;
              } }, icon('power'), here && st === 'error' ? 'Retry' : 'Load')));
      }));

    };

    // ---- change which weights back a slot
    async function openChange(key) {
      let reg;
      try { reg = await (await fetch('/api/models/registry')).json(); } catch { return toast('Could not read the model list.', 'error'); }
      const slot = reg.models[key];
      const cur = slot.current;
      const pickFrom = (list, value, allowNone) => {
        const sel = h('select', { class: 'input' }, allowNone ? h('option', { value: '' }, '(none)') : null,
          list.map((f) => h('option', { value: f.path }, `${f.path.replace(/^models\//, '')} · ${fmtBytes(f.size)}`)));
        sel.value = list.some((f) => f.path === value) || allowNone ? (value || '') : list[0]?.path || '';
        return sel;
      };
      const name = h('input', { class: 'input', value: cur.label, maxlength: 60 });
      const model = pickFrom(reg.files.filter((f) => !f.projector), cur.model, false);
      const proj = slot.needs_projector ? pickFrom(reg.files.filter((f) => f.projector), cur.mmproj, false) : null;
      // A projector sitting next to the chosen weights is almost always the one that belongs to them.
      const dirOf = (p) => p.slice(0, p.lastIndexOf('/'));
      const matchProj = () => { const mate = reg.files.find((f) => f.projector && dirOf(f.path) === dirOf(model.value)); if (proj && mate) proj.value = mate.path; };
      model.addEventListener('change', matchProj);
      const ctx = h('input', { class: 'input', type: 'number', min: 2048, step: 1024, value: cur.ctx });
      const repo = h('input', { class: 'input', value: cur.repo || '', placeholder: 'owner/name on Hugging Face (optional)' });
      const field = (label, el, hint) => h('div', { class: 'field' }, h('label', {}, label), el, hint && h('small', { class: 'muted' }, hint));
      const save = h('button', { class: 'btn primary' }, icon('check', 15), 'Save');
      const reset = h('button', { class: 'btn', disabled: !slot.overridden }, 'Reset to default');
      const m = modal(`Change ${reg.models[key].default.label}`, h('div', { class: 'stack' },
        h('p', { class: 'muted', style: { margin: 0 } }, 'Point this slot at other weights from the models folder. Pick the same kind of model it is for (for example a vision model for Vision). The new weights load the next time this model is used.'),
        field('Name', name), field('Model file', model, 'Models you download below show up here too. You can also copy any .gguf into the models folder.'),
        proj && field('Projector file (mmproj)', proj, 'The vision / audio encoder that belongs to the model above.'),
        field('Context size (tokens)', ctx, 'Larger windows need more GPU memory. On this 6 GB card the defaults were measured to fit.'),
        field('Source', repo),
        h('div', { class: 'row' }, save, reset, h('div', { class: 'grow' }, h('small', { class: 'muted' }, slot.overridden ? 'Customised' : 'Using the built-in weights')))));
      const send = async (url, init, done) => {
        save.disabled = reset.disabled = true;
        try {
          const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...init });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || 'Failed');
          toast(done); m.close();
          gridSig = ''; // repaint the cards with the new names
          setTimeout(paintFiles, 300);
        } catch (e) { toast(e.message, 'error', 8000); save.disabled = false; reset.disabled = !slot.overridden; }
      };
      save.addEventListener('click', () => send(`/api/models/${key}/registry`, { method: 'PUT', body: JSON.stringify({
        label: name.value, model: model.value, ...(proj ? { mmproj: proj.value } : {}), ctx: ctx.value, repo: repo.value }) }, 'Saved. It will be used the next time it loads.'));
      reset.addEventListener('click', () => send(`/api/models/${key}/registry`, { method: 'DELETE' }, 'Back to the built-in weights.'));
    }

    // ---- download more weights (Hugging Face)
    const dlList = h('div', { class: 'stack', style: { gap: '8px' } });
    const dlRepo = h('input', { class: 'input grow', placeholder: 'Repository, e.g. LiquidAI/LFM3-1.7B-GGUF' });
    const dlFile = h('input', { class: 'input grow', placeholder: 'File, e.g. LFM3-1.7B-Q4_K_M.gguf' });
    const dlBtn = h('button', { class: 'btn primary' }, icon('download', 15), 'Download');
    let dlTimer = 0, wasRunning = false;
    const paintDownloads = (items) => {
      dlList.replaceChildren(...items.filter((d) => d.state !== 'cancelled').map((d) => {
        const pct = d.total ? Math.min(100, (d.done / d.total) * 100) : 0;
        return h('div', { class: 'plan-step ' + (d.state === 'done' ? 'done' : d.state === 'running' ? 'running' : '') },
          h('div', { class: 'grow' },
            h('div', { class: 'plan-name' }, `${d.repo} · ${d.file.split('/').pop()}`),
            d.state === 'running' && h('div', { class: 'plan-prog' }, h('span', { class: 'muted' }, `${fmtBytes(d.done)}${d.total ? ` of ${fmtBytes(d.total)}` : ''}`), h('div', { class: 'plan-bar' }, h('i', { style: { width: `${pct}%` } }))),
            d.state === 'done' && h('div', { class: 'muted plan-sum' }, `Saved to ${d.path}. Choose what to use it as under Model files.`),
            d.state === 'failed' && h('div', { class: 'error-text plan-sum' }, d.error)),
          d.state === 'running' && h('button', { class: 'btn sm', onclick: async () => { await fetch(`/api/models/downloads/${d.id}`, { method: 'DELETE' }); pollDownloads(); } }, 'Cancel'));
      }));
    };
    const pollDownloads = async () => {
      clearTimeout(dlTimer);
      try {
        const { downloads } = await (await fetch('/api/models/downloads')).json();
        paintDownloads(downloads);
        const running = downloads.some((d) => d.state === 'running');
        if (running) dlTimer = setTimeout(pollDownloads, 1000);
        if (wasRunning && !running) paintFiles();
        wasRunning = running;
      } catch { /* backend restarting */ }
    };
    dlBtn.addEventListener('click', async () => {
      dlBtn.disabled = true;
      try {
        const r = await fetch('/api/models/downloads', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo: dlRepo.value.trim(), file: dlFile.value.trim() }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || 'Could not start the download');
        pollDownloads();
      } catch (e) { toast(e.message, 'error', 8000); }
      dlBtn.disabled = false;
    });
    pollDownloads();

    // ---- every model file on disk, named like the cards above: what it is used as, and (for downloads) use it as something else or delete it
    const ROLES = { text: 'Chat', vl: 'Vision', audio: 'Voice', omni: 'Omni' };
    const registryPrecision = (p) => (p.match(/(?<![A-Za-z0-9])(BF16|F16|F32|I?Q\d(?:_[A-Z0-9]+)*)(?![A-Za-z0-9])/i)?.[1] || 'GGUF').toUpperCase();
    const niceName = (path) => {
      const stem = path.split('/').pop().replace(/\.(gguf|safetensors)$/i, '');
      const p = registryPrecision(path);
      return p === 'GGUF' ? stem : `${stem.replace(new RegExp(`[-_.]?${p}$`, 'i'), '')} (${p})`;
    };
    const dirOf = (p) => p.slice(0, p.lastIndexOf('/'));
    const fileBox = h('div', { class: 'ftable' });
    async function useAs(reg, f, slot, mate) {
      const cur = reg.models[slot];
      if (!confirm(`Use ${niceName(f.path)} as ${ROLES[slot]}?\n\nIt replaces ${cur.current.label}. The new weights load the next time that model is used. “Reset to default” on its card undoes this.`)) return;
      const body = { label: niceName(f.path), model: f.path, ...(cur.needs_projector ? { mmproj: mate.path } : {}) };
      try {
        const r = await fetch(`/api/models/${slot}/registry`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || 'Failed');
        toast(`${ROLES[slot]} now uses ${niceName(f.path)}.`);
      } catch (e) { toast(e.message, 'error', 8000); }
      gridSig = '';
      paintFiles();
    }
    async function removeFile(f) {
      if (!confirm(`Delete ${f.path.split('/').pop()}?\n\nThis frees ${fmtBytes(f.size)}. You can download it again later.`)) return;
      const r = await fetch('/api/models/files', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: f.path }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) return toast(d.detail || 'Could not delete it.', 'error', 8000);
      toast(`Deleted, ${fmtBytes(d.freed)} freed`); gridSig = ''; paintFiles();
    }
    async function paintFiles() {
      let reg;
      try { reg = await (await fetch('/api/models/registry')).json(); } catch { return; }
      const order = Object.keys(reg.models);
      const usedBy = {};
      for (const k of order) for (const f of [reg.models[k].current.model, reg.models[k].current.mmproj]) if (f) (usedBy[f] ||= []).push(k);
      const cell = (cls, ...kids) => h('div', { class: `fcell ${cls}` }, ...kids);
      const row = (name, sub, role, size, action, del) => h('div', { class: 'frow' },
        cell('fname', h('div', { class: 'plan-name' }, name), h('div', { class: 'muted plan-sum' }, sub)),
        cell('frole', role), cell('fsize', size), cell('fact', action), cell('fdel', del));
      const head = h('div', { class: 'frow fhead' }, cell('fname', 'Model'), cell('frole', 'Used as'), cell('fsize', 'Size'), cell('fact', ''), cell('fdel', ''));

      const rows = reg.files.slice().sort((a, b) => {
        const rank = (f) => (usedBy[f.path] ? Math.min(...usedBy[f.path].map((k) => order.indexOf(k))) : 99);
        const ra = rank(a), rb = rank(b);
        return ra - rb || a.path.localeCompare(b.path);
      }).map((f) => {
        const by = usedBy[f.path] || [];
        const custom = f.path.startsWith('models/custom/');
        const label = by.length ? reg.models[by[0]].current.label : niceName(f.path);
        const roleChip = by.length ? h('span', { class: 'chip good' }, ROLES[by[0]]) : h('span', { class: 'chip' }, 'Not in use');
        // A file can back a slot only if it can do that slot's job: text-only weights fit the two chat slots; the vision, voice and
        // Omni slots also need the projector (the vision / audio encoder) that sits in the same folder.
        const mate = f.projector ? null : reg.files.find((x) => x.projector && dirOf(x.path) === dirOf(f.path));
        const roles = !custom || f.projector ? [] : order.filter((k) => reg.models[k].current.model !== f.path && (!reg.models[k].needs_projector || mate));
        const pick = roles.length ? h('select', { class: 'input', 'aria-label': `Use ${label} as` },
          h('option', { value: '' }, 'Use as…'), roles.map((k) => h('option', { value: k }, ROLES[k]))) : null;
        if (pick) pick.addEventListener('change', () => { const k = pick.value; pick.value = ''; if (k) useAs(reg, f, k, mate); });
        const del = custom ? h('button', { class: 'btn sm danger', disabled: by.length > 0, title: by.length ? `In use as ${by.map((k) => ROLES[k]).join(', ')}` : 'Delete this file from the disk', onclick: () => removeFile(f) }, icon('trash', 14), 'Delete') : null;
        return row(f.projector ? `${label} · projector` : label, `${f.path.split('/').pop()}${f.projector ? '' : ` · ${registryPrecision(f.path)}`}`, roleChip, fmtBytes(f.size), pick, del);
      });
      // Picture drawing uses its own fixed set of files (not a language model), listed so the disk usage adds up.
      const pictures = (reg.image_files || []).map((f) => row(f.label, f.path.split('/').pop(), h('span', { class: 'chip accent' }, 'Picture drawing'), fmtBytes(f.size), null, null));
      fileBox.replaceChildren(head, ...rows, ...(pictures.length ? [h('div', { class: 'fgroup' }, 'Picture drawing (Library → Pictures)'), ...pictures] : []));
    }
    paintFiles();

    let lastLog = '';
    const pollLogs = async () => {
      try {
        const { lines } = await (await fetch('/api/logs')).json();
        if (lines.length) {
          const text = lines.join('\n');
          if (text !== lastLog) {
            lastLog = text;
            const stick = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 40;
            logBox.textContent = text;
            if (stick) logBox.scrollTop = logBox.scrollHeight;
          }
        }
      } catch { /* ignore */ }
    };
    pollLogs();
    const t = setInterval(pollLogs, 2000);
    const off = onStatus(render);

    view.append(
      h('div', { class: 'banner' }, icon('info'), h('div', { class: 'grow' },
        'The BF16 weights of each model are 3.5 GB, so your 6 GB GPU holds ',
        h('b', {}, 'one model at a time'), '. Opening a tool loads its model automatically and swaps out the previous one.')),
      h('div', { class: 'card card-pad' }, safeBrowsingSwitch()),
      grid,
      h('div', { class: 'section-title' }, 'Get more models'),
      h('div', { class: 'card card-pad stack' },
        h('p', { class: 'muted', style: { margin: 0 } }, 'Download a single GGUF file from Hugging Face into the models folder (resumable). Check the model’s licence and size first: this machine holds one model of about 4 GB on the GPU at a time.'),
        h('div', { class: 'row wrap' }, dlRepo, dlFile, dlBtn), dlList),
      h('div', { class: 'section-title' }, 'Model files'),
      h('div', { class: 'card card-pad stack' },
        h('p', { class: 'muted', style: { margin: 0 } }, 'Every model file on this computer, named as on the cards above. For models you downloaded, Use as… offers only the roles a file can fill (text-only models can be the chat model; Vision, Voice and Omni need a model that comes with its projector file). The new weights load the next time that model is used, and “Reset to default” on its card undoes the change.'),
        fileBox),
      h('div', { class: 'section-title' }, 'System'),
      gpuBox,
      h('div', { class: 'section-title' }, 'llama.cpp server log'),
      logBox);
    return () => { off(); clearInterval(t); clearTimeout(dlTimer); };
  },
};
