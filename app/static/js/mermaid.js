// Draws ```mermaid blocks in rendered Markdown. Blocks start out as plain source (see markdown.js) and are swapped for the
// diagram once Mermaid accepts the text, so half-written diagrams while a reply streams simply keep showing their source.
const SRC = '/vendor/mermaid/mermaid.min.js';
let loader = null;
let counter = 0;

// Flowchart labels with punctuation (A[Step (draft)]) are a syntax error unless quoted, and small models forget. Mirrors app/diagrams.py.
const LABEL = /(?<![\w"])([A-Za-z_][\w-]*)\[([^[\]"\n]*)\]/g;
const DECISION = /(?<![\w"])([A-Za-z_][\w-]*)\{([^{}"\n]*)\}/g;
const RISKY = /[()<>&:;,#%]/;
export function autoquote(code) {
  const first = (code.split('\n').find((l) => l.trim()) || '').trim().toLowerCase();
  if (!/^(flowchart|graph)/.test(first)) return code;
  const fix = (open, close) => (m, id, label) => (!label.trim() || '(/\\'.includes(label[0]) || !RISKY.test(label) ? m : `${id}${open}"${label.trim()}"${close}`);
  return code.split('\n').map((line) => (/^\s*(%%|click |style |classDef |linkStyle )/.test(line) ? line
    : line.replace(LABEL, fix('[', ']')).replace(DECISION, fix('{', '}')))).join('\n');
}

const decode = (el) => { try { return decodeURIComponent(el.dataset.code || ''); } catch { return ''; } };
const dark = () => document.documentElement.dataset.theme !== 'light';

function load() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  return (loader ||= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = SRC;
    s.onload = () => (window.mermaid ? resolve(window.mermaid) : reject(new Error('Mermaid failed to load')));
    s.onerror = () => { loader = null; reject(new Error('Mermaid failed to load')); };
    document.head.append(s);
  }));
}

function configure(mermaid) {
  const d = dark();
  mermaid.initialize({
    startOnLoad: false, securityLevel: 'strict', theme: 'base', suppressErrorRendering: true,
    fontFamily: getComputedStyle(document.body).fontFamily,
    themeVariables: d
      ? { darkMode: true, background: '#0c1410', primaryColor: '#12301f', primaryBorderColor: '#3ddc84', primaryTextColor: '#e6f2ea', lineColor: '#5fd49a',
          secondaryColor: '#16231b', tertiaryColor: '#0c1410', noteBkgColor: '#1d2a22', noteTextColor: '#e6f2ea', clusterBkg: '#101b15', textColor: '#e6f2ea', edgeLabelBackground: '#0c1410' }
      : { primaryColor: '#e6f0ea', primaryBorderColor: '#14633a', primaryTextColor: '#12201a', lineColor: '#14633a', secondaryColor: '#f3f8f5',
          tertiaryColor: '#ffffff', noteBkgColor: '#fff8dc', noteTextColor: '#12201a', clusterBkg: '#f3f8f5', edgeLabelBackground: '#ffffff' },
  });
  return d;
}

function toolbar(code, svgText) {
  const bar = document.createElement('div');
  bar.className = 'mmd-bar';
  const btn = (label, fn) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'copy-code'; b.textContent = label;
    b.addEventListener('click', () => fn(b));
    bar.append(b);
  };
  btn('Copy source', async (b) => {
    try { await navigator.clipboard.writeText(code); b.textContent = 'Copied'; } catch { b.textContent = 'Failed'; }
    setTimeout(() => { b.textContent = 'Copy source'; }, 1400);
  });
  btn('Download SVG', () => {
    const url = URL.createObjectURL(new Blob([svgText], { type: 'image/svg+xml' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'diagram.svg'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  });
  return bar;
}

async function draw(el, code, final) {
  const st = el._st;
  try {
    const mermaid = await load();
    const isDark = configure(mermaid);
    const { svg } = await mermaid.render(`mmd${++counter}`, autoquote(code));
    if (decode(el) !== code) return; // the text moved on while we were drawing
    const box = document.createElement('div');
    box.className = 'mmd-svg';
    box.innerHTML = svg; // Mermaid runs in strict mode and sanitises its own output
    el.replaceChildren(box, toolbar(code, svg));
    el.classList.add('drawn');
    Object.assign(st, { ok: true, dark: isDark });
  } catch (e) {
    document.getElementById(`dmmd${counter}`)?.remove();
    st.ok = false;
    if (final && decode(el) === code && !el.querySelector('.mmd-err')) {
      const note = document.createElement('div');
      note.className = 'mmd-err';
      note.textContent = `Diagram couldn’t be drawn: ${String(e.message || e).split('\n')[0].slice(0, 160)}`;
      el.append(note);
    }
  }
}

// Call after the Markdown HTML has been placed in `root`. `final` is false while text is still streaming in.
export function hydrateMermaid(root, final = true) {
  for (const el of root.querySelectorAll('.mmd')) {
    const code = decode(el);
    const st = (el._st ||= { code: null, timer: 0, ok: false, final: false, dark: null });
    const stale = st.ok && st.dark !== dark(); // theme changed since it was drawn
    if (st.code === code && !stale && (st.ok || !final || st.final)) continue;
    const changed = st.code !== code;
    st.code = code;
    if (final) st.final = true; else if (changed) st.final = false;
    clearTimeout(st.timer);
    if (stale) el.classList.remove('drawn');
    st.timer = setTimeout(() => draw(el, code, final), final ? 0 : 450);
  }
}
