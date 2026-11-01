import { h, icon } from './ui.js';
import { onStatus, refreshStatus, isGenerating, state } from './api.js';
import { toolstore } from './toolstore.js';
import { safeTopControl } from './safebrowsing.js';

const ROUTES = {
  chat: () => import('./pages/chat.js'),
  vision: () => import('./pages/vision.js'),
  voice: () => import('./pages/voice.js'),
  library: () => import('./pages/library.js'),
  tools: () => import('./pages/toolsandskills.js'),
  schedule: () => import('./pages/schedule.js'),
  models: () => import('./pages/models.js'),
  setup: () => import('./pages/setup.js'),  // not in the navigation: opened on first run and from Models & System
};
// [route, label, icon, model key shown with a live dot while it is resident]
const NAV = [
  'Playgrounds',
  ['chat', 'Chat', 'chat', ['text', 'omni']],
  ['vision', 'Vision', 'eye', ['vl']],
  ['voice', 'Voice', 'mic', ['audio']],
  'Library',
  ['library', 'Library', 'filetext', '@image'],
  'Configure',
  ['tools', 'Tools & Skills', 'plug'],
  ['schedule', 'Schedules', 'calendar', '@schedule'],
  ['models', 'Models & System', 'cpu'],
];

const sidebar = document.getElementById('sidebar');
const nav = document.getElementById('nav');
const tags = {};
NAV.forEach((n) => {
  if (typeof n === 'string') return nav.append(h('div', { class: 'nav-label' }, n));
  const [id, label, ic, key] = n;
  const tag = h('span', { class: 'nav-tag' });
  if (key) tags[Array.isArray(key) ? key.join('+') : key] = tag;  // model keys get a "loaded" dot; '@schedule' shows scheduler activity
  nav.append(h('a', { href: `#/${id}`, 'data-id': id, onclick: () => closeNav() }, icon(ic), label, tag));
});

const menuBtn = document.getElementById('menu-btn');
menuBtn.append(icon('menu'));
// Desktop: collapse/expand the navigation rail (remembered). Phone width: slide it over the page.
const appEl = document.querySelector('.app');
const narrow = matchMedia('(max-width: 820px)');
let navCollapsed = false;
try { navCollapsed = localStorage.getItem('lfm3.nav.collapsed') === '1'; } catch { /* private mode */ }
const applyNav = () => {
  appEl.classList.toggle('nav-collapsed', !narrow.matches && navCollapsed);
  appEl.classList.toggle('nav-open', narrow.matches && sidebar.classList.contains('open')); // shows the backdrop
  const open = narrow.matches ? sidebar.classList.contains('open') : !navCollapsed;
  menuBtn.setAttribute('aria-expanded', String(open));
  menuBtn.title = open ? 'Hide navigation' : 'Show navigation';
};
menuBtn.addEventListener('click', () => {
  if (narrow.matches) sidebar.classList.toggle('open');
  else {
    navCollapsed = !navCollapsed;
    try { localStorage.setItem('lfm3.nav.collapsed', navCollapsed ? '1' : '0'); } catch { /* private mode */ }
  }
  applyNav();
});
function closeNav() {
  if (sidebar.classList.contains('open')) { sidebar.classList.remove('open'); applyNav(); }
}
document.getElementById('scrim').addEventListener('click', closeNav);
addEventListener('keydown', (e) => { if (e.key === 'Escape') closeNav(); });
narrow.addEventListener('change', () => { sidebar.classList.remove('open'); applyNav(); });
appEl.style.transition = 'none'; // first paint: no slide animation
applyNav();
requestAnimationFrame(() => requestAnimationFrame(() => { appEl.style.transition = ''; }));

// Quit: shown only when the program that started the server can stop it (the launcher: packaged builds and `./run.sh`).
const quitBtn = document.getElementById('quit-btn');
quitBtn.append(icon('power'), 'Quit Sthanik');
quitBtn.addEventListener('click', async () => {
  if (!confirm(`Quit Sthanik?${isGenerating() ? ' A reply is still being written and will be lost.' : ''} The model is unloaded; your chats stay saved.`)) return;
  await fetch('/api/quit', { method: 'POST' }).catch(() => {});
  document.body.replaceChildren(h('p', { style: { padding: '40px', font: '16px system-ui', color: '#555' } }, 'Sthanik has stopped. You can close this tab.'));
});
onStatus((s) => { quitBtn.hidden = !s.can_quit; });

// theme
const root = document.documentElement;
const themeBtn = document.getElementById('theme-toggle');       // sidebar
const themeBtnTop = document.getElementById('theme-toggle-top'); // top bar: reachable even when the sidebar is hidden
const currentTheme = () => (root.dataset.theme === 'light' ? 'light' : 'dark');
const paintTheme = () => {
  const dark = currentTheme() === 'dark';
  themeBtn.replaceChildren(icon(dark ? 'sun' : 'moon'), dark ? 'Light theme' : 'Dark theme');
  themeBtnTop.replaceChildren(icon(dark ? 'sun' : 'moon'));
  themeBtnTop.title = dark ? 'Switch to light theme' : 'Switch to dark theme';
  themeBtnTop.setAttribute('aria-label', themeBtnTop.title);
};
// Applied atomically: transitions are suppressed for two frames so nothing is ever caught half-way between palettes.
function applyTheme(next, persist) {
  root.classList.add('theme-switching');
  root.dataset.theme = next;
  if (persist) { try { localStorage.setItem('lfm3.theme', next); } catch { /* private mode */ } }
  paintTheme();
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('theme-switching')));
}
for (const b of [themeBtn, themeBtnTop]) b.addEventListener('click', () => applyTheme(currentTheme() === 'light' ? 'dark' : 'light', true));
// Another tab changed the theme: follow it instead of drifting out of sync until the next refresh.
addEventListener('storage', (e) => { if (e.key === 'lfm3.theme' && (e.newValue === 'light' || e.newValue === 'dark')) applyTheme(e.newValue, false); });
// Until you pick a theme yourself, follow the operating system (live).
const osLight = matchMedia('(prefers-color-scheme: light)');
osLight.addEventListener('change', () => {
  let saved = null;
  try { saved = localStorage.getItem('lfm3.theme'); } catch { /* private mode */ }
  if (!saved) applyTheme(osLight.matches ? 'light' : 'dark', false);
});
paintTheme();

// live status: GPU card, resident-model pill, sidebar dots
const gpuCard = document.getElementById('gpu-card');
const pill = document.getElementById('model-pill');
pill.parentElement.insertBefore(safeTopControl(), pill); // top right, left of the model chip
// Built once and updated in place: replacing these nodes on every poll made the sidebar flicker.
const gpu = { name: h('span'), mem: h('b'), fill: h('i'), meter: null, util: h('span'), temp: h('span'), head: null };
gpu.meter = h('div', { class: 'meter' }, gpu.fill);
gpu.head = h('div', { class: 'gpu-head' }, gpu.name, gpu.mem);
gpuCard.append(gpu.head, gpu.meter, h('div', { class: 'gpu-meta' }, gpu.util, gpu.temp));
// Context window of the resident model, and the CPU: same card, built once and patched in place.
const meterOf = () => { const fill = h('i'); return { fill, el: h('div', { class: 'meter' }, fill) }; };
const ctx = { label: h('span'), val: h('b'), bar: meterOf(), note: h('span'), box: null };
ctx.box = h('div', { class: 'gpu-sect', title: 'How much of the model’s context window the current conversation uses (prompt + reply so far), and the reply length limit' },
  h('div', { class: 'gpu-head' }, ctx.label, ctx.val), ctx.bar.el, h('div', { class: 'gpu-meta' }, ctx.note));
const cpu = { name: h('span'), util: h('b'), bar: meterOf(), load: h('span'), temp: h('span'), box: null };
cpu.box = h('div', { class: 'gpu-sect' }, h('div', { class: 'gpu-head' }, cpu.name, cpu.util), cpu.bar.el, h('div', { class: 'gpu-meta' }, cpu.load, cpu.temp));
const ram = { val: h('b'), bar: meterOf(), box: null };
ram.box = h('div', { class: 'gpu-sect' }, h('div', { class: 'gpu-head' }, h('span', {}, 'Memory'), ram.val), ram.bar.el);
gpuCard.append(ctx.box, cpu.box, ram.box);
const fmtTok = (n) => (n >= 10240 ? `${Math.round(n / 1024)}k` : n >= 1024 ? `${(n / 1024).toFixed(1).replace(/\.0$/, '')}k` : String(n));
const setText = (el, text) => {
  if (el.firstChild?.nodeType === 3) { if (el.firstChild.data !== text) el.firstChild.data = text; } else if (el.textContent !== text) el.textContent = text;
};
let pillKey = '';

onStatus((s) => {
  const g = s.gpu;
  gpu.meter.style.display = g ? '' : 'none';
  gpu.util.parentElement.style.display = g ? '' : 'none';
  if (g) {
    const pct = (g.mem_used_mb / g.mem_total_mb) * 100;
    setText(gpu.name, g.name.replace('NVIDIA GeForce ', '').replace(' Laptop GPU', ' Laptop'));
    setText(gpu.mem, `${(g.mem_used_mb / 1024).toFixed(1)} / ${(g.mem_total_mb / 1024).toFixed(0)} GB`);
    gpu.fill.style.width = `${pct.toFixed(1)}%`;
    gpu.meter.classList.toggle('hot', pct > 90);
    setText(gpu.util, `util ${g.util_pct}%`);
    setText(gpu.temp, `${g.temp_c} °C`);
  } else {
    setText(gpu.name, 'CPU only');
    setText(gpu.mem, '');
  }
  const c = s.ctx;
  ctx.box.style.display = c ? '' : 'none';
  if (c) {
    const pct = (c.used / c.n_ctx) * 100;
    setText(ctx.label, c.busy ? 'Context · in use' : 'Context · last request');
    setText(ctx.val, `${fmtTok(c.used)} / ${fmtTok(c.n_ctx)}`);
    ctx.bar.fill.style.width = `${pct.toFixed(1)}%`;
    ctx.bar.el.classList.toggle('hot', pct > 85);
    setText(ctx.note, c.busy
      ? (c.reading ? `reading ${fmtTok(c.prompt)}-token prompt…` : `writing ${c.decoded}${c.max_new ? ` / ${fmtTok(c.max_new)} max` : ''} tokens`)
      : `${pct.toFixed(0)}% of window${c.max_new ? ` · reply limit ${fmtTok(c.max_new)}` : ''}`);
  }
  const u = s.cpu;
  cpu.box.style.display = ram.box.style.display = u ? '' : 'none';
  if (u) {
    setText(cpu.name, `CPU · ${u.name}`);
    setText(cpu.util, u.util_pct == null ? '…' : `${Math.round(u.util_pct)}%`);
    cpu.bar.fill.style.width = `${u.util_pct || 0}%`;
    cpu.bar.el.classList.toggle('hot', (u.util_pct || 0) > 90);
    setText(cpu.load, `${u.cores} threads · load ${u.load1}`);
    setText(cpu.temp, u.temp_c == null ? '' : `${Math.round(u.temp_c)} °C`);
    if (u.ram_total_mb) {
      const rp = (u.ram_used_mb / u.ram_total_mb) * 100;
      setText(ram.val, `${(u.ram_used_mb / 1024).toFixed(1)} / ${(u.ram_total_mb / 1024).toFixed(1)} GB`);
      ram.bar.fill.style.width = `${rp.toFixed(1)}%`;
      ram.bar.el.classList.toggle('hot', rp > 90);
    }
  }
  const l = s.llama;
  const label = l.key ? s.models[l.weights || l.key].label : null;
  const im = s.image?.busy ? s.image : null;
  const priv = s.safe_browsing === false;
  document.documentElement.toggleAttribute('data-private', priv); // the purple private-session look
  const key = `${l.state}|${label}|${im ? `img${im.step || 0}` : ''}|${priv}`;
  if (key !== pillKey) {
    pillKey = key;
    pill.replaceChildren(
      im ? h('span', { class: 'chip accent' }, h('span', { class: 'dot pulse' }), `Drawing${im.step ? ` ${im.step}/${im.steps}` : '…'}`) :
      l.state === 'ready' ? h('span', { class: 'chip good' }, h('span', { class: 'dot' }), label)
        : l.state === 'loading' ? h('span', { class: 'chip accent' }, h('span', { class: 'dot pulse' }), `Loading ${label}…`)
          : l.state === 'error' ? h('span', { class: 'chip bad' }, `${label} failed`)
            : h('span', { class: 'chip' }, 'No model loaded'));
  }
  // scheduler: pulsing dot while a task runs, solid dot for results you haven't looked at yet
  const sc = s.scheduler || { running: 0, last_finished: 0 };
  let seen = 0;
  try { seen = +localStorage.getItem('lfm3.runs.seen') || 0; } catch { /* private mode */ }
  const unseen = location.hash.startsWith('#/schedule') ? false : sc.last_finished > seen;
  const sTag = tags['@schedule'];
  if (sTag) {
    const state = sc.running ? 'run' : unseen ? 'new' : '';
    if (sTag.dataset.state !== state) {
      sTag.dataset.state = state;
      sTag.replaceChildren(...(state ? [h('i', { class: `live-dot${state === 'run' ? ' pulse' : ''}`, title: state === 'run' ? 'A task is running' : 'New results' })] : []));
    }
  }
  const iTag = tags['@image'];
  if (iTag) {
    const st = s.image?.busy ? 'run' : '';
    if (iTag.dataset.state !== st) {
      iTag.dataset.state = st;
      iTag.replaceChildren(...(st ? [h('i', { class: 'live-dot pulse', title: 'Drawing a picture' })] : []));
    }
  }
  lastLlama = l;
  paintModelDots();
});

// Dot beside a playground: pulsing while a reply streams (even from another page), solid while its model is loaded.
let lastLlama = null;
function paintModelDots() {
  for (const [ids, tag] of Object.entries(tags)) {
    if (ids.startsWith('@')) continue;
    const keys = ids.split('+');
    const state = keys.some((k) => isGenerating(k)) ? 'gen' : lastLlama && keys.includes(lastLlama.key) && lastLlama.state === 'ready' ? 'live' : '';
    if (tag.dataset.state === state) continue;
    tag.dataset.state = state;
    tag.replaceChildren(...(state ? [h('i', { class: `live-dot${state === 'gen' ? ' pulse' : ''}`, title: state === 'gen' ? 'Replying…' : 'Loaded on the GPU' })] : []));
  }
}
addEventListener('lfm3:generation', paintModelDots);

// router
// Pages flagged `keep` (Chat, Vision, Omni) are mounted once and only detached while you are elsewhere, so the conversation,
// scroll position and any reply still streaming survive navigation. Everything else is rebuilt on each visit.
const kept = new Map(); // route id -> { host, handle }
let current = null;     // { id, keep, handle }
let navToken = 0;
const asHandle = (r) => (typeof r === 'function' ? { destroy: r } : r || {});

// Old links and bookmarks: Vision and Omni became models inside Chat, Speech became Voice.
const ALIASES = {
  omni: ['chat', 'lfm3.chat.preselect', 'omni'], speech: ['voice'], home: ['chat'],
  documents: ['library', 'lfm3.tab.library', 'documents'], images: ['library', 'lfm3.tab.library', 'images'], memory: ['library', 'lfm3.tab.library', 'memory'],
  skills: ['tools', 'lfm3.tab.toolsandskills', 'skills'],
};

async function route() {
  let id = (location.hash.replace(/^#\/?/, '') || 'chat').split('/')[0];
  if (ALIASES[id]) {
    const [to, k, v] = ALIASES[id];
    if (k) { try { sessionStorage.setItem(k, v); } catch { /* ignore */ } }
    history.replaceState(null, '', `#/${to}`);
    id = to;
  }
  // Until the engine and a chat model are installed nothing else works: go to Setup.
  if (state.status?.setup?.needed && id !== 'setup') { history.replaceState(null, '', '#/setup'); id = 'setup'; }
  const known = id in ROUTES ? id : 'chat';
  const token = ++navToken;
  const page = (await ROUTES[known]()).default;
  if (token !== navToken) return;
  if (current) {
    if (current.keep) current.handle.suspend?.();
    else current.handle.destroy?.();
  }
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.id === known));
  document.getElementById('page-title').textContent = page.title;
  document.getElementById('page-sub').textContent = page.subtitle;
  document.title = `${page.title} · Sthanik`;
  const view = document.getElementById('view');
  view.replaceChildren();
  view.className = `view${page.fill ? ' fill' : ''}`;
  if (page.keep) {
    let k = kept.get(known);
    if (!k) {
      const host = h('div', { class: 'page-host' });
      k = { host, handle: asHandle(page.mount(host)) };
      kept.set(known, k);
      view.append(host);
    } else {
      view.append(k.host);
      k.handle.resume?.();
    }
    current = { id: known, keep: true, handle: k.handle };
  } else {
    current = { id: known, keep: false, handle: asHandle(page.mount(view)) };
  }
}
addEventListener('hashchange', route);

await refreshStatus();
toolstore.refresh().catch(() => {});
// Poll faster while the model is writing so the context meter moves with the reply.
(function pollStatus() {
  refreshStatus().finally(() => setTimeout(pollStatus, state.status?.ctx?.busy ? 600 : 1500));
}());
route();
