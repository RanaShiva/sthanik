// Markdown → sanitized HTML with syntax highlighting and LaTeX. Model and web output are untrusted, so
// everything passes through DOMPurify; KaTeX output is trusted (generated here, not by the model) and is
// substituted back in after sanitising.
import { marked } from '../vendor/marked.js';
import DOMPurify from '../vendor/purify.js';
import hljs from '../vendor/hljs/highlight.js';
import katex from '../vendor/katex/katex.js';
import { hydrateMermaid } from './mermaid.js';

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const escAttr = (s) => esc(s).replace(/"/g, '&quot;');
const DIAGRAM_START = /^\s*(?:(?:flowchart|graph)\s+(?:TD|TB|BT|RL|LR)\b|sequenceDiagram\b|gantt\b|classDiagram\b|stateDiagram(?:-v2)?\b|erDiagram\b|journey\b|pie\b|mindmap\b|timeline\b|gitGraph\b)/i;
const IMG_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{2,90}\.(png|jpe?g|webp)$/;

marked.use({
  gfm: true,
  breaks: false,
  renderer: {
    // Only our own stored pictures (`img:NAME`) are shown; any other image reference is reduced to its alt text so
    // nothing in a reply (or a web page the model read) can make the browser fetch a remote address.
    image({ href, text }) {
      const m = /^img:(.+)$/.exec(href || '');
      if (!m || !IMG_NAME.test(m[1])) return esc(text || '');
      return `<img class="chat-img" src="/api/images/${m[1]}" alt="${escAttr(text || '')}" loading="lazy">`;
    },
    code({ text, lang }) {
      const name = (lang || '').trim().split(/\s+/)[0].toLowerCase();
      // Labelled mermaid, or an unlabelled block that starts like a diagram (models often forget the label).
      if (name === 'mermaid' || (['', 'text', 'plaintext', 'flowchart', 'graph', 'diagram'].includes(name) && DIAGRAM_START.test(text))) {
        return `<div class="mmd" data-code="${encodeURIComponent(text)}"><div class="codeblock"><div class="codehead"><span>mermaid</span></div><pre><code class="hljs">${esc(text)}</code></pre></div></div>`;
      }
      let html;
      if (name && hljs.getLanguage(name)) html = hljs.highlight(text, { language: name, ignoreIllegals: true }).value;
      else html = esc(text);
      return `<div class="codeblock"><div class="codehead"><span>${esc(name || 'text')}</span>` +
        `<button type="button" class="copy-code">Copy</button></div><pre><code class="hljs">${html}</code></pre></div>`;
    },
  },
});

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

// Pull math out before markdown sees it (so `_` and `*` inside formulas survive), restore it afterwards.
function extractMath(src) {
  const store = [];
  const keep = (tex, display) => {
    store.push({ tex, display });
    return `@@M${store.length - 1}@@`;
  };
  const parts = src.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g); // odd indexes are code: leave untouched
  const out = parts.map((seg, i) => {
    if (i % 2) return seg;
    return seg
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => keep(t, true))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => keep(t, true))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => keep(t, false))
      .replace(/(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])(?!\d)/g, (_, t) => keep(t, false));
  });
  return { text: out.join(''), store };
}

function restoreMath(html, store) {
  return html.replace(/@@M(\d+)@@/g, (m, n) => {
    const item = store[+n];
    if (!item) return m;
    try {
      return katex.renderToString(item.tex.trim(), { displayMode: item.display, throwOnError: false, output: 'html', trust: false });
    } catch {
      return esc(item.tex);
    }
  });
}

export function renderMarkdown(src) {
  const { text, store } = extractMath(src || '');
  const raw = marked.parse(text);
  const clean = DOMPurify.sanitize(raw, { ADD_ATTR: ['target'] });
  return restoreMath(clean, store);
}

// One delegated listener makes every "Copy" button in `container` work, including ones rendered later.
export function enableCodeCopy(container, onCopied) {
  container.addEventListener('click', async (e) => {
    const btn = e.target.closest?.('.copy-code');
    if (!btn) return;
    const code = btn.closest('.codeblock')?.querySelector('code')?.innerText || '';
    try {
      await navigator.clipboard.writeText(code);
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1400);
      onCopied?.();
    } catch { btn.textContent = 'Failed'; }
  });
}

// Update `el` so it matches `html`, touching only what differs. Streaming used to replace the whole message on every
// frame, which re-created code blocks and formulas each time (flicker, lost text selection, scroll jumps inside blocks).
export function patchInto(el, html, final = true) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  syncChildren(el, tpl.content);
  hydrateMermaid(el, final);
}

function syncChildren(dst, src) {
  const next = [...src.childNodes];
  for (let i = 0; i < next.length; i++) {
    const a = dst.childNodes[i];
    const b = next[i];
    if (!a) dst.appendChild(b);
    else if (a.isEqualNode(b)) continue;
    else if (a.nodeType === 3 && b.nodeType === 3) a.data = b.data;
    else if (a.nodeType === 1 && b.nodeType === 1 && a.tagName === b.tagName) {
      if (a.classList.contains('mmd') && b.classList.contains('mmd')) a.setAttribute('data-code', b.getAttribute('data-code')); // a drawn diagram owns its class and children
      else { syncAttrs(a, b); syncChildren(a, b); }
    }
    else dst.replaceChild(b, a);
  }
  while (dst.childNodes.length > next.length) dst.removeChild(dst.lastChild);
}

function syncAttrs(a, b) {
  for (const { name, value } of [...b.attributes]) if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  for (const { name } of [...a.attributes]) if (!b.hasAttribute(name)) a.removeAttribute(name);
}
