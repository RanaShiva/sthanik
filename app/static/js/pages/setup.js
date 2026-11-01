import { h, icon, toast, fmtBytes } from '../ui.js';
import { refreshStatus } from '../api.js';

const MB = 1024 * 1024;
const gb = (mb) => `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB`;
const OS = { linux: 'Linux', windows: 'Windows', macos: 'macOS' };
const BACKEND = { cuda: 'NVIDIA CUDA', vulkan: 'Vulkan graphics', metal: 'Apple Metal', cpu: 'the CPU' };

// replaceChildren does not flatten arrays the way h() does
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));

async function getJSON(url, init) {
  const r = await fetch(url, init);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
  return d;
}

export default {
  title: 'Setup',
  subtitle: 'Detect this computer, then install the engine and the models it can run',
  mount(view) {
    let plan = null, picked = new Set(), image = false, timer = 0, dead = false;
    const quants = {};                       // part -> precision chosen for it ('text', 'vl', ..., 'diffusion', 'llm')
    const own = {};                          // part -> { on, model, mmproj }: files of the user's own
    const engine = { on: false, path: '' };  // a llama-server the user already has
    let found = { engines: [], files: [] };
    const intro = h('div', { class: 'banner' }, icon('info', 18), h('div', { class: 'grow' }, h('b', {}, 'Looking at this computer…'), h('div', { class: 'muted' }, 'Checking the processor, memory and graphics card.')));
    const hwBox = h('div', { class: 'card card-pad' });
    const engineBox = h('div', { class: 'card card-pad' });
    const modelBox = h('div', { class: 'card card-pad' });
    const actionBox = h('div', { class: 'row wrap', style: { gap: '10px', alignItems: 'center' } });
    const progressBox = h('div', { class: 'card card-pad', style: { display: 'none' } });
    view.append(h('div', { class: 'stack', style: { gap: '14px', maxWidth: '860px' } }, intro, hwBox, engineBox, modelBox, actionBox, progressBox));

    const choice = (c) => c.quants.find((q) => q.id === (quants[c.id] || c.quant)) || c.quants[0];
    const imageChoice = (k) => plan.image.parts[k].quants.find((q) => q.id === (quants[k] || plan.image.parts[k].quant)) || plan.image.parts[k].quants[0];
    const need = () => {
      if (!plan) return 0;
      const parts = plan.components.filter((c) => picked.has(c.id) && !(own[c.id]?.on)).reduce((n, c) => n + choice(c).size, 0);
      const pics = image && plan.image ? plan.image.engine_size + plan.image.vae_size + imageChoice('diffusion').size + imageChoice('llm').size : 0;
      return parts + pics + (plan.runtime.installed || engine.on ? 0 : (plan.runtime.candidates[0]?.size || 0));
    };

    const sel = (id, label, options, value, onchange, aria = label) => h('label', { class: 'prec' }, h('span', {}, label),
      h('select', { class: 'input', 'aria-label': `${aria} precision`, 'data-part': id, onchange: (e) => { onchange(e.target.value); paintTotal(); } },
        options.map((q) => h('option', { value: q.id, selected: q.id === value }, `${q.id}${q.id === plan.recommended ? ' (recommended)' : ''} · ${q.installed ? 'installed' : q.size ? fmtBytes(q.size) : 'size unknown'} · ${q.note}`))));

    function ownEngineBox() {
      const rt = plan.runtime;
      const box = h('div', { class: 'own' });
      const draw = () => {
        for (const el of engineBox.querySelectorAll('.dl-engine')) el.style.display = engine.on ? 'none' : '';
        const input = h('input', { class: 'input', type: 'text', placeholder: '/path/to/llama-server (or the folder it is in)', value: engine.path, 'aria-label': 'Path of your llama-server',
          oninput: (e) => { engine.path = e.target.value; } });
        fill(box, h('label', { class: 'switch-row' },
          h('span', {}, h('b', {}, 'I already have llama.cpp'), h('small', {}, rt.current?.external ? 'Choose another build, or keep the one above.' : 'Use my own llama-server instead of downloading one. It is started and checked before it is kept.')),
          h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: engine.on, onchange: (e) => { engine.on = e.target.checked; draw(); paintTotal(); } }), h('i'))),
          engine.on && h('div', { class: 'stack', style: { gap: '8px', marginTop: '6px' } }, input,
            found.engines.map((x) => h('button', { class: 'btn sm', type: 'button', title: x.path, onclick: () => { engine.path = x.path; draw(); } }, icon('check', 13), `${x.path} · ${x.source}`)),
            h('small', { class: 'muted' }, 'Use a build from llama.cpp itself (b-numbered releases work best; this app was tested with ' + rt.tag + '). Keep its libraries next to the program.')));
      };
      draw();
      return box;
    }

    function partRow(c) {
      const o = (own[c.id] ||= { on: false, model: '', mmproj: '' });
      const box = h('div', { class: 'part' });
      const draw = () => {
        fill(box,
          h('label', { class: 'switch-row' },
            h('span', {}, h('b', {}, c.label, c.installed ? h('span', { class: 'chip good', style: { marginLeft: '8px' } }, c.own ? `installed · ${c.own}` : 'installed') : null), h('small', {}, c.detail)),
            h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: picked.has(c.id) || o.on, disabled: c.required && !c.installed, onchange: (e) => { e.target.checked ? picked.add(c.id) : picked.delete(c.id); if (!e.target.checked) o.on = false; draw(); paintTotal(); } }), h('i'))),
          (picked.has(c.id) || o.on) && !o.on && sel(c.id, 'Precision', c.quants, quants[c.id] || c.quant, (v) => { quants[c.id] = v; }, c.label),
          (picked.has(c.id) || o.on) && h('button', { class: 'linkish', type: 'button', onclick: () => { o.on = !o.on; if (o.on) picked.add(c.id); draw(); paintTotal(); } }, o.on ? 'Download it instead' : 'I already have these files'),
          o.on && h('div', { class: 'own stack', style: { gap: '8px' } },
            h('input', { class: 'input', type: 'text', list: 'own-models', placeholder: '/path/to/model.gguf', value: o.model, 'aria-label': `${c.label} model file`, oninput: (e) => { o.model = e.target.value; } }),
            c.needs_projector && h('input', { class: 'input', type: 'text', list: 'own-projectors', placeholder: '/path/to/mmproj-….gguf (its projector)', value: o.mmproj, 'aria-label': `${c.label} projector file`, oninput: (e) => { o.mmproj = e.target.value; } }),
            h('small', { class: 'muted' }, 'The file is linked into the models folder (no extra space on the same disk; a copy on another), checked, and loaded once to see that it works. Your original is never changed.')));
      };
      draw();
      return box;
    }

    function pictureRow() {
      const im = plan.image;
      const box = h('div', { class: 'part' });
      const draw = () => {
        fill(box,
          h('label', { class: 'switch-row' },
            h('span', {}, h('b', {}, im.label, im.installed ? h('span', { class: 'chip good', style: { marginLeft: '8px' } }, 'installed') : null), h('small', {}, im.detail)),
            h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: image, onchange: (e) => { image = e.target.checked; draw(); paintTotal(); } }), h('i'))),
          image && [sel('diffusion', im.parts.diffusion.label, im.parts.diffusion.quants, quants.diffusion || im.parts.diffusion.quant, (v) => { quants.diffusion = v; }),
            sel('llm', im.parts.llm.label, im.parts.llm.quants, quants.llm || im.parts.llm.quant, (v) => { quants.llm = v; }),
            h('small', { class: 'muted' }, `Also the drawing engine and the image decoder${im.engine_size + im.vae_size ? ` (${fmtBytes(im.engine_size + im.vae_size)})` : ' (installed)'}. The drawing model matters most for detail; the text encoder mostly for how well it follows long prompts.`)]);
      };
      draw();
      return box;
    }

    function paintPlan() {
      const hw = plan.hardware;
      const gpus = hw.gpus.length ? hw.gpus.map((g) => `${g.name}${g.vram_mb && !g.unified ? ` · ${gb(g.vram_mb)}` : ''}`) : ['No graphics card found'];
      intro.replaceChildren(icon(plan.needed ? 'info' : 'check', 18), h('div', { class: 'grow' },
        h('b', {}, plan.needed ? 'Sthanik needs a one-time setup' : 'Everything needed is installed'),
        h('div', { class: 'muted' }, plan.needed ? 'Nothing leaves this computer except the downloads below (github.com for the engine, huggingface.co for the models).' : 'You can add more parts below, or check the installation again.')));

      fill(hwBox, h('div', { class: 'panel-title' }, icon('cpu'), 'This computer'),
        h('dl', { class: 'kv' },
          h('dt', {}, 'System'), h('dd', {}, `${OS[hw.os] || hw.os} · ${hw.arch}`),
          h('dt', {}, 'Processor'), h('dd', {}, `${hw.cpu} · ${hw.threads} threads`),
          h('dt', {}, 'Memory'), h('dd', {}, hw.ram_mb ? gb(hw.ram_mb) : 'unknown'),
          h('dt', {}, 'Graphics'), h('dd', {}, gpus.map((g) => h('div', {}, g))),
          h('dt', {}, 'Free disk'), h('dd', {}, gb(plan.disk_free_mb))));

      const rt = plan.runtime, first = rt.candidates[0];
      fill(engineBox, h('div', { class: 'panel-title' }, icon('sparkles'), 'Engine'),
        rt.installed
          ? h('p', {}, h('span', { class: 'chip good' }, h('span', { class: 'dot' }), 'Installed'), ` llama.cpp ${rt.current?.tag || ''}${rt.current?.backend && rt.current.backend !== 'unknown' ? ` · ${BACKEND[rt.current.backend] || rt.current.backend}` : ''}`,
            rt.current?.external ? h('span', { class: 'muted' }, ` · your own build, kept at ${rt.current.path}`) : null)
          : [h('p', { class: 'dl-engine' }, `llama.cpp ${rt.tag} will run models on ${BACKEND[first?.backend] || 'the CPU'} (${first?.why || 'CPU only'}), ${fmtBytes(first?.size || 0)} to download.`),
            rt.candidates.length > 1 && h('p', { class: 'muted dl-engine' }, `It checks that the build really finds your hardware. If not, it tries: ${rt.candidates.slice(1).map((c) => BACKEND[c.backend]).join(', then ')}.`)],
        ownEngineBox());

      fill(modelBox, h('div', { class: 'panel-title' }, icon('download'), 'Models'),
        h('p', { class: 'muted' }, `Recommended for ${gb(plan.budget_mb)} of ${plan.budget_where}: ${plan.recommended}. Each part has its own precision. A smaller one is a smaller download and runs where memory is short, with a little less exactness. Only one model sits in memory at a time.`),
        h('datalist', { id: 'own-models' }, found.files.filter((f) => !f.projector).map((f) => h('option', { value: f.path }, `${f.name} · ${fmtBytes(f.size)}`))),
        h('datalist', { id: 'own-projectors' }, found.files.filter((f) => f.projector).map((f) => h('option', { value: f.path }, `${f.name} · ${fmtBytes(f.size)}`))),
        plan.components.map(partRow),
        plan.image && pictureRow());
      paintTotal();
    }

    const startBtn = h('button', { class: 'btn primary', onclick: () => start() }, icon('download', 15), 'Install');
    const totalNote = h('span', { class: 'muted' });
    actionBox.append(startBtn, totalNote);
    function paintTotal() {
      const n = need();
      const short = plan && n + 1e9 > plan.disk_free_mb * MB;
      totalNote.textContent = n ? `${fmtBytes(n)} to download${short ? ` — not enough free disk (${gb(plan.disk_free_mb)})` : ''}` : 'Nothing to download';
      totalNote.className = short ? 'error-text' : 'muted';
      startBtn.disabled = !!short;
      const mine = engine.on || Object.values(own).some((o) => o.on);
      startBtn.lastChild.textContent = mine ? 'Check and use my files' : n ? 'Install' : 'Check the installation';
    }

    async function load() {
      try {
        if (!found.loaded) found = { ...(await getJSON('/api/setup/detect').catch(() => ({ engines: [], files: [] }))), loaded: true };
        plan = await getJSON('/api/setup/plan');
        picked = new Set(plan.components.filter((c) => (c.required && !c.installed) || picked.has(c.id)).map((c) => c.id));
        paintPlan();
      } catch (e) {
        intro.replaceChildren(icon('x', 18), h('div', { class: 'grow' }, h('b', {}, 'Could not look at this computer'), h('div', { class: 'muted' }, e.message)));
        intro.className = 'banner error';
      }
    }

    function paintJob(job) {
      progressBox.style.display = job.state === 'idle' ? 'none' : '';
      const total = job.steps.length;
      const finished = job.steps.filter((s) => ['done', 'skipped'].includes(s.state)).length;
      fill(progressBox, h('div', { class: 'panel-title' }, icon(job.state === 'done' ? 'check' : 'download'), job.state === 'running' ? `Installing (${finished} of ${total})` : job.state === 'done' ? 'Finished' : job.state === 'cancelled' ? 'Stopped' : 'Stopped with an error'),
        job.error && h('div', { class: 'banner error' }, icon('x', 18), h('div', { class: 'grow' }, job.error)),
        job.steps.map((s) => h('div', { style: { margin: '10px 0' } },
          h('div', { class: 'row', style: { justifyContent: 'space-between' } }, h('span', {}, s.label), h('span', { class: s.state === 'failed' ? 'error-text' : 'muted' },
            s.state === 'running' && s.total ? `${fmtBytes(s.done)} / ${fmtBytes(s.total)}` : s.state === 'pending' ? 'waiting' : s.state === 'skipped' ? 'already there' : s.state === 'done' ? 'done' : s.state)),
          s.state === 'running' || (s.total && s.state !== 'skipped') ? h('div', { class: 'meter' }, h('i', { style: { width: `${s.total ? Math.min(100, (100 * s.done) / s.total) : s.state === 'done' ? 100 : 4}%` } })) : null,
          s.note && s.state !== 'skipped' && h('div', { class: 'muted', style: { fontSize: '12px' } }, s.note))),
        job.state === 'running' && h('button', { class: 'btn', onclick: () => fetch('/api/setup/cancel', { method: 'POST' }) }, 'Cancel'),
        job.state === 'done' && h('div', { class: 'row', style: { gap: '10px' } }, h('a', { class: 'btn primary', href: '#/chat' }, 'Open Chat'), h('a', { class: 'btn', href: '#/models' }, 'Models & System')));
      startBtn.disabled = job.state === 'running';
    }

    async function poll() {
      if (dead) return;
      try {
        const job = await getJSON('/api/setup/status');
        paintJob(job);
        if (job.state === 'running') timer = setTimeout(poll, 700);
        else if (job.state === 'done') { engine.on = false; picked = new Set(); for (const o of Object.values(own)) o.on = false; await refreshStatus(); toast('Setup finished', 'info', 3000); await load(); }
      } catch { timer = setTimeout(poll, 2000); }
    }

    async function start() {
      try {
        const mine = {};
        for (const c of plan.components) {
          const o = own[c.id];
          if (!picked.has(c.id) || !o?.on) continue;
          if (!o.model.trim()) throw new Error(`${c.label}: give the path of your model file, or switch to downloading it.`);
          mine[c.id] = { model: o.model.trim(), mmproj: o.mmproj.trim() };
        }
        if (engine.on && !engine.path.trim()) throw new Error('Give the path of your llama-server, or switch to downloading it.');
        const chosen = {};
        for (const c of plan.components) if (picked.has(c.id) && quants[c.id]) chosen[c.id] = quants[c.id];
        if (image) for (const k of ['diffusion', 'llm']) if (quants[k]) chosen[k] = quants[k];
        await getJSON('/api/setup/start', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ components: [...picked], quants: chosen, image, own: { engine: engine.on ? engine.path.trim() : '', models: mine } }) });
        poll();
      } catch (e) { toast(e.message, 'error', 7000); }
    }

    load().then(poll);
    return () => { dead = true; clearTimeout(timer); };
  },
};
