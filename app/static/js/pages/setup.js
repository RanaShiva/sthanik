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
    let plan = null, quant = '', picked = new Set(), image = false, timer = 0, dead = false;
    const intro = h('div', { class: 'banner' }, icon('info', 18), h('div', { class: 'grow' }, h('b', {}, 'Looking at this computer…'), h('div', { class: 'muted' }, 'Checking the processor, memory and graphics card.')));
    const hwBox = h('div', { class: 'card card-pad' });
    const engineBox = h('div', { class: 'card card-pad' });
    const modelBox = h('div', { class: 'card card-pad' });
    const actionBox = h('div', { class: 'row wrap', style: { gap: '10px', alignItems: 'center' } });
    const progressBox = h('div', { class: 'card card-pad', style: { display: 'none' } });
    view.append(h('div', { class: 'stack', style: { gap: '14px', maxWidth: '860px' } }, intro, hwBox, engineBox, modelBox, actionBox, progressBox));

    const need = () => {
      if (!plan) return 0;
      const parts = plan.components.filter((c) => picked.has(c.id)).reduce((n, c) => n + c.size, 0);
      return parts + (image && plan.image ? plan.image.size : 0) + (plan.runtime.installed ? 0 : (plan.runtime.candidates[0]?.size || 0));
    };

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
          ? h('p', {}, h('span', { class: 'chip good' }, h('span', { class: 'dot' }), 'Installed'), ` llama.cpp ${rt.current?.tag || ''}${rt.current?.backend && rt.current.backend !== 'unknown' ? ` · ${BACKEND[rt.current.backend] || rt.current.backend}` : ''}`)
          : [h('p', {}, `llama.cpp ${rt.tag} will run models on ${BACKEND[first?.backend] || 'the CPU'} (${first?.why || 'CPU only'}), ${fmtBytes(first?.size || 0)} to download.`),
            rt.candidates.length > 1 && h('p', { class: 'muted' }, `It checks that the build really finds your hardware. If not, it tries: ${rt.candidates.slice(1).map((c) => BACKEND[c.backend]).join(', then ')}.`)]);

      const quants = h('select', { class: 'input', style: { maxWidth: '520px' }, onchange: async (e) => { quant = e.target.value; await load(); } },
        plan.quants.map((q) => h('option', { value: q.id, selected: q.id === plan.quant }, `${q.id}${q.id === plan.recommended ? ' (recommended)' : ''}: ${q.note}`)));
      fill(modelBox, h('div', { class: 'panel-title' }, icon('download'), 'Models'),
        h('p', { class: 'muted' }, `Recommended for ${gb(plan.budget_mb)} of ${plan.budget_where}. A smaller precision is a smaller download and runs where memory is short.`),
        h('div', { class: 'field' }, h('label', {}, 'Precision'), quants),
        plan.components.map((c) => h('label', { class: 'switch-row' },
          h('span', {}, h('b', {}, c.label, c.installed ? h('span', { class: 'chip good', style: { marginLeft: '8px' } }, 'installed') : null), h('small', {}, `${c.detail}${c.size ? ` · ${fmtBytes(c.size)}` : ''}`)),
          h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: picked.has(c.id), disabled: c.required, onchange: (e) => { e.target.checked ? picked.add(c.id) : picked.delete(c.id); paintTotal(); } }), h('i')))),
        plan.image && h('label', { class: 'switch-row' },
          h('span', {}, h('b', {}, plan.image.label, plan.image.installed ? h('span', { class: 'chip good', style: { marginLeft: '8px' } }, 'installed') : null), h('small', {}, `${plan.image.detail} · ${fmtBytes(plan.image.size)}`)),
          h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: image, onchange: (e) => { image = e.target.checked; paintTotal(); } }), h('i'))));
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
      startBtn.lastChild.textContent = n ? 'Install' : 'Check the installation';
    }

    async function load() {
      try {
        plan = await getJSON(`/api/setup/plan${quant ? `?quant=${encodeURIComponent(quant)}` : ''}`);
        quant = plan.quant;
        picked = new Set(plan.components.filter((c) => c.required || picked.has(c.id)).map((c) => c.id));
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
        else if (job.state === 'done') { await refreshStatus(); toast('Setup finished', 'info', 3000); await load(); }
      } catch { timer = setTimeout(poll, 2000); }
    }

    async function start() {
      try {
        await getJSON('/api/setup/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ components: [...picked], quant, image }) });
        poll();
      } catch (e) { toast(e.message, 'error', 7000); }
    }

    load().then(poll);
    return () => { dead = true; clearTimeout(timer); };
  },
};
