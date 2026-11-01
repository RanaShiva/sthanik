// What a conversation is working on, and checking that the work was actually done.
//
// 1. The "working on" document: created or opened in this chat, it is pinned above the message box and described to the model
//    (with its text and the editing tools switched on), so "make the intro shorter" can only mean that document.
// 2. Earlier documents and pictures are listed for the model, so it can refer back to them.
// 3. After every turn on a document the result is checked against the request. If the model said it changed something but the
//    document is unchanged, Sthanik applies the request itself; if you asked for "10 pictures" and fewer exist, the rest are made.
import { h, icon, toast } from './ui.js';
import { onStatus } from './api.js';
import { openDocEditor } from './docedit.js';
import { openDocPicker } from './docpicker.js';
import { toolstore } from './toolstore.js';
import { timeAgo } from './ui.js';

const api = (name, tail = '') => `/api/documents/${encodeURIComponent(name)}${tail}`;
const json = (r) => r.json().catch(() => ({}));
const post = (url, body, signal) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Sthanik-Turn': toolstore.turn || '' }, body: JSON.stringify(body), signal });
const hash = (s) => { let x = 5381; for (let i = 0; i < s.length; i++) x = ((x << 5) + x + s.charCodeAt(i)) | 0; return x; };
const ZERO = { sections: 0, images: 0, diagrams: 0, slides: 0, tables: 0, words: 0 };

// ---- reading the request
const QUESTION = /^\s*(what|why|how|who|when|where|which|is|are|was|were|does|do|did|can you explain|could you explain|tell me|explain)\b/i;
const EDIT_VERB = /\b(make|change|add|remove|delete|shorten|expand|rewrite|translate|fix|rename|update|replace|insert|edit|improve|summari[sz]e|simplify|reword|rephrase|reformat|trim|cut|extend|correct|move|swap|polish|include|append|proofread|reduce|increase|lengthen|capitali[sz]e|turn)\b/i;
const PIC_NOUN = /\b(images?|pictures?|photos?|photographs?|illustrations?|graphics?)\b/i;
const ADD_PIC = /\b(generate|add|insert|include|create|make|put|draw|illustrate|give|attach|find|get|fetch|download|search|show|use|need|want|place)\b/i;
const REMOVE_PIC = /\b(remove|delete|get rid of|take out|clear|drop|erase)\b/i;
const REPLACE_PIC = /\b(replace|swap|substitute|instead of|change)\b/i;
// Asking to look at / judge the pictures that are there (the vision model does that), not to add or remove any.
const REVIEW_STRONG = /\b(analy[sz]e|analysis|review|inspect|evaluate|assess|vision model|look(?:s|ing)? at|quality|relevan\w+|irrelevant|not (?:so )?good|bad|poor)\b/i;
const REVIEW_WEAK = /\b(ugly|wrong|unrelated|better|good)\b/i;
const CLEANUP = /\b(clean ?up|tidy|uniform|consistent|consistency|weird|stray|leftover|junk|placeholders?|messy|clutter|unwanted|extra (stuff|text))\b/i;

// What a request asks about pictures and tidiness. "remove these images and put actual images from online" is a REPLACE with source web.
export function readIntent(text) {
  const t = text || '';
  const noun = PIC_NOUN.test(t);
  const removes = noun && REMOVE_PIC.test(t);
  const replaces = noun && (REPLACE_PIC.test(t) || (removes && ADD_PIC.test(t.replace(REMOVE_PIC, ' '))));
  const adds = noun && (replaces || ADD_PIC.test(t.replace(REMOVE_PIC, ' ')));
  const web = /\b(online|from the (web|internet)|real|actual|authentic|photographs?|photos?|downloaded?|find|search)\b/i.test(t);
  const drawn = /\b(generate[d]?|draw|drawn|ai|illustrat\w*|create[d]?)\b/i.test(t);
  const asksDrawn = /\b(drawn|draw|generate[d]?|create[d]?|illustrations?|ai)\b/i.test(t);
  const review = noun && !removes && /\b(document|report|file|deck|slides?|pdf|here|there|already|these|those|the (?:images?|pictures?|photos?))\b/i.test(t)
    && (REVIEW_STRONG.test(t) || (REVIEW_WEAK.test(t) && !asksDrawn && !REPLACE_PIC.test(t)));
  if (review) return { adds: false, replaces: false, removes: false, review: true, cleanup: CLEANUP.test(t), source: 'web' };
  return { adds, replaces, removes: removes && !adds, cleanup: CLEANUP.test(t), source: web ? 'web' : drawn ? 'generate' : 'auto' };
}
const isPictureClause = (c) => PIC_NOUN.test(c) && (ADD_PIC.test(c) || REMOVE_PIC.test(c) || REPLACE_PIC.test(c)) && !/\b(section|slide|chapter|heading|caption)\b/i.test(c);
export const wantsEdit = (t) => !QUESTION.test(t || '') && EDIT_VERB.test(t || '');
export const wantsPictures = (t) => readIntent(t).adds;
// Pictures made this turn belong in the document when the request says so ("…to add into the document"), not for "draw me a lion".
export const wantsAttach = (t) => readIntent(t).adds && /\b(add|insert|put|include|attach|place|into|replace)\b|\b(document|report|file|deck|slides?|presentation)\b/i.test(t || '');
// The part of a request that is about the text (the picture clauses are handled by the picture tools).
export const textClauses = (t) => (t || '').split(/\s*(?:[.;]|,|\band then\b|\bthen\b|\band\b)\s*/i).filter((c) => c.trim() && !isPictureClause(c) && !CLEANUP.test(c)).join(', ');

const NUMBER = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, fifteen: 15, twenty: 20 };
const UNIT = { image: 'images', picture: 'images', photo: 'images', illustration: 'images', graphic: 'images', section: 'sections', slide: 'slides', diagram: 'diagrams', flowchart: 'diagrams', table: 'tables' };
const COUNT = /\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)[\s-]+((?:(?:more|new|extra|additional|different|separate|ai|ai-generated|relevant|good|nice)\s+)*)(image|picture|photo|illustration|graphic|section|slide|diagram|flowchart|table)s?\b/gi;

const RATE = /^\s+(per|each|for each|for every|in each|on each|every)\b/i;

// "10 images", "add 3 more sections", "a 12 slide deck" -> [{unit, n, delta}]. delta: add this many on top of what exists.
export function countRequirements(text) {
  const out = [];
  for (const m of (text || '').matchAll(COUNT)) {
    const n = /^\d/.test(m[1]) ? +m[1] : NUMBER[m[1].toLowerCase()];
    const lead = text.slice(Math.max(0, m.index - 45), m.index);
    if (RATE.test(text.slice(m.index + m[0].length, m.index + m[0].length + 14))) continue; // "2 slides per topic" is a rate, not the total
    out.push({ unit: UNIT[m[3].toLowerCase()], n, delta: /\b(add|insert|generate|more|extra|additional|another|attach)\b/i.test(`${lead} ${m[2]}`) });
  }
  return out;
}

// "an image for the intro" is one picture; "images", "each place", "every slide", "add images to the document" mean all of it.
export const pictureScope = (t) => (/\b(each|every|all|per|everywhere|throughout)\b/i.test(t || '') ? 'coverage'
  : /\b(an?|one|the)\s+(ai[- ]generated\s+|new\s+|nice\s+|good\s+)*(image|picture|photo|illustration|graphic)\b/i.test(t || '') ? 'single' : 'coverage');

// Content sections (level-2 headings, i.e. slides in a deck) that hold no picture. Introduction/summary style sections count only when nothing else is left.
const BOILER = /^\s*(introduction|overview|conclusion|summary|abstract|references?|sources?|bibliography|appendix|next steps|table of contents|contents|acknowledg\w*)\b/i;
export function uncoveredSections(source, only = null) {
  const lines = (source || '').split('\n');
  const heads = [];
  let fenced = false;
  lines.forEach((l, i) => { if (/^\s*```/.test(l)) fenced = !fenced; else if (!fenced && /^##\s+/.test(l)) heads.push([i, l.replace(/^##\s+/, '').trim()]); });
  const want = only && only.length ? new Set(only.map((t) => t.toLowerCase())) : null;
  const open = [], boiler = [];
  heads.forEach(([i, t], k) => {
    const end = k + 1 < heads.length ? heads[k + 1][0] : lines.length;
    if (want && !want.has(t.toLowerCase())) return;
    if (/!\[[^\]]*\]\(img:/.test(lines.slice(i + 1, end).join('\n'))) return;
    (BOILER.test(t) ? boiler : open).push(t);
  });
  return open.length ? open : boiler;
}

// Which sections (level-2 headings) a request names: by number ("Place 4"), by full title, or by the subject after the colon ("Taxila").
export function mentionedSections(text, source) {
  const low = (text || '').toLowerCase();
  const out = [];
  let fenced = false;
  for (const l of (source || '').split('\n')) {
    if (/^\s*```/.test(l)) fenced = !fenced;
    else if (!fenced && /^##\s+/.test(l)) {
      const title = l.replace(/^##\s+/, '').trim();
      const t = title.toLowerCase();
      const num = /^([a-z][\w ]{1,20}?)\s+(\d{1,3})\s*[:.)\-–—]/.exec(t);
      const subject = t.split(/[:\-–—]/).slice(1).join(' ').trim();
      const hit = low.includes(t)
        || (num && new RegExp(`\\b${num[1].trim()}\\s*${num[2]}\\b`).test(low))
        || (subject.length >= 4 && new RegExp(`\\b${subject.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(low));
      if (hit && !BOILER.test(title)) out.push(title);
    }
  }
  return out;
}

// Names of the pictures in the given sections (all sections when `only` is empty).
export function sectionPictures(source, only = []) {
  const lines = (source || '').split('\n');
  const want = only.length ? new Set(only.map((t) => t.toLowerCase())) : null;
  const names = [];
  let inSec = !want, fenced = false;
  for (const l of lines) {
    if (/^\s*```/.test(l)) fenced = !fenced;
    if (!fenced && /^#{1,6}\s+/.test(l)) { const lv = l.match(/^#+/)[0].length; if (lv <= 2) inSec = !want || (lv === 2 && want.has(l.replace(/^#+\s+/, '').trim().toLowerCase())); }
    const m = !fenced && inSec && /^\s*!\[[^\]]*\]\(img:([^)\s]+)\)\s*$/.exec(l);
    if (m) names.push(m[1]);
  }
  return names;
}

const factOf = (facts, unit) => (unit === 'slides' ? facts.slides ?? facts.sections : facts[unit]) ?? 0;

// ---- text for the model
function activeBlock(src, file) {
  const long = src.source.length > 6000;
  const outline = src.outline.map((o) => `${'  '.repeat(o.level - 1)}- ${o.title}`).join('\n');
  const body = long ? `(The document is long, so here is only its outline: use read_document to read it.)\n${outline}` : src.source;
  return `The user is working on this ${src.kind === 'slides' ? 'slide deck' : src.kind === 'sheet' ? 'spreadsheet' : 'document'} in this conversation: "${src.title}" (file ${file.name}). It has ${src.facts.sections} sections and ${src.facts.images} pictures. Current text:\n<document>\n${body}\n</document>\n` +
    'Requests to change, add, remove, shorten, expand, rewrite, translate or fix something, or to add pictures or diagrams, refer to THIS document unless the user clearly asks for a new one. ' +
    'To change it, call edit_document ONCE with ALL the requested changes in one instruction (do not rewrite the text in your reply). To add NEW pictures call illustrate_document with the number wanted (leave the count out to give EVERY section a picture: do that for "add images" or "one for each place"; never stop part-way); to put pictures that already exist (for example ones you just made with generate_image) into it, call add_picture_to_document. ' +
    'Never claim a change was made unless the tool reported it; if the tool says something was NOT done, tell the user plainly.';
}

function libraryBlock(docs, imgs) {
  const parts = [];
  if (docs.length) parts.push('Documents made earlier (the user may refer to them; list_documents, read_document and edit_document work on them):\n' + docs.slice(0, 6).map((d) => `- ${d.name}: ${d.title}`).join('\n'));
  if (imgs.length) parts.push('Pictures made earlier (show one with ![caption](img:NAME)):\n' + imgs.slice(0, 6).map((i) => `- ${i.name}: ${(i.prompt || '').slice(0, 70)}`).join('\n'));
  return parts.join('\n\n');
}

// A saved set of check rows, shown again when a conversation is reopened.
export function checksCard(rows) {
  return h('div', { class: 'checks' }, h('div', { class: 'checks-head' }, icon('check', 15), 'Checked'),
    ...rows.map((r) => h('div', { class: `check-row ${r.s === 'run' ? 'warn' : r.s}` }, icon(r.s === 'ok' ? 'check' : r.s === 'bad' ? 'x' : 'alert', 14), h('span', {}, r.t))));
}

export function createWorkspace({ onChange, getSignal, mine }) {
  let active = null; // {name, title, format, created}
  // Always present above the message box: a quiet "Work on a document…" link when there is none, a card when there is.
  const chip = h('div', { class: 'ws-wrap' });
  const open = (anchor) => openDocPicker({ anchor, activeName: active?.name, mine: mine?.() || [], onPick: (d) => ws.set(d), onClear: () => ws.clear() });

  const paint = () => {
    if (!active) {
      const b = h('button', { type: 'button', class: 'ws-idle', title: 'Pick the document this conversation works on: requests like “shorten the intro” then apply to it' },
        icon('filetext', 15), 'Work on a document…', icon('chevronDown', 13));
      b.addEventListener('click', () => open(b));
      chip.replaceChildren(b);
      return;
    }
    const change = h('button', { type: 'button', class: 'btn sm', title: 'Switch to another document' }, 'Change', icon('chevronDown', 12));
    change.addEventListener('click', () => open(change));
    chip.replaceChildren(h('div', { class: 'ws-chip' },
      icon('filetext', 17),
      h('span', { class: 'ws-title' }, h('b', {}, active.title || active.name), h('small', {}, `Working on this · ${active.format.toUpperCase()}${active.created ? ` · ${timeAgo(active.created)}` : ''}`)),
      h('button', { type: 'button', class: 'btn sm', title: 'Open the editor', onclick: () => openDocEditor(active, { onSaved: () => {} }) }, icon('pencil', 13), 'Edit'),
      change,
      h('button', { type: 'button', class: 'icon-btn', title: 'Stop working on this document', onclick: () => ws.clear() }, icon('x', 14))));
  };

  const sourceOf = async (name) => {
    try {
      const r = await fetch(api(name, '/source'));
      return r.ok ? await r.json() : null;
    } catch { return null; }
  };

  const ws = {
    chip,
    state: () => (active ? { name: active.name, title: active.title, format: active.format } : null),
    get active() { return active; },
    set(file) { if (!file?.name || file.editable === false) return; active = { name: file.name, title: file.title || file.name.replace(/-\d{8}-\d{6}.*$/, '').replace(/-/g, ' '), format: file.format, created: file.created }; paint(); onChange?.(); },
    clear() { active = null; paint(); onChange?.(); },
    restore(s) { active = s && s.name ? s : null; paint(); },
    // Files a turn created or changed: the latest editable one becomes the document being worked on.
    noteFiles(files, { keep = false } = {}) { if (keep && active) return; const f = [...files].reverse().find((x) => x.editable !== false && x.format !== 'xlsx') || [...files].reverse().find((x) => x.editable); if (f) ws.set(f); },

    // Pictures made during a turn on the document being worked on (see wantsAttach).
    adoptFrom: async (items) => {
      if (active) return;
      const names = [];
      for (const it of items || []) for (const f of it.files || []) names.push(f.name);
      for (const it of items || []) for (const p of it.parts || []) for (const f of p.files || []) names.push(f.name);
      if (!names.length) return;
      try {
        const docs = ((await (await fetch('/api/documents')).json()).documents || []).filter((d) => d.editable);
        const hit = [...names].reverse().map((n) => docs.find((d) => d.name === n)).find(Boolean);
        if (hit) { active = { name: hit.name, title: hit.title || hit.name, format: hit.format }; paint(); }
      } catch { /* backend restarting */ }
    },

    // What to tell the model for the message about to be sent.
    async context(userText) {
      const ctx = { system: '', force: [], before: null, excludeSkills: [] };
      if (active) {
        const src = await sourceOf(active.name);
        if (!src) { ws.clear(); } else {
          active.title = src.title;
          ctx.before = { hash: hash(src.source), facts: src.facts, source: src.source };
          ctx.beforeName = active.name;
          ctx.system = activeBlock(src, active);
          ctx.force.push('documents');
          const it = readIntent(userText);
          if (it.adds || it.replaces) ctx.force.push('images');
          ctx.activeName = active.name;
          ctx.only = (it.adds || it.replaces || it.removes || it.review) ? mentionedSections(userText, src.source) : [];
          // Changing the open document: the model must not start a new file instead (it once wrote a "plan" document about the job).
          ctx.changing = wantsEdit(textClauses(userText)) || it.adds || it.removes || it.cleanup;
          if (ctx.changing) { ctx.hide = ['create_document', 'create_slides', 'create_spreadsheet']; ctx.excludeSkills = ['pdf', 'docx', 'pptx', 'xlsx', 'research-brief']; }
          // Only removing pictures (or only tidying): nothing that adds pictures may be offered, or the model puts them back.
          if ((it.removes && !it.adds) || (it.cleanup && !it.adds)) ctx.hide.push('illustrate_document', 'generate_image', 'find_image', 'add_picture_to_document');
          return ctx;
        }
      }
      try {
        const [d, i] = await Promise.all([fetch('/api/documents').then(json), fetch('/api/images').then(json)]);
        ctx.system = libraryBlock((d.documents || []).filter((x) => x.editable), (i.images || []).filter((x) => x.kind === 'image'));
        if (/\b(document|report|file|pdf|slides?|deck|presentation|section|intro|the text)\b/i.test(userText) && (d.documents || []).length) ctx.force.push('documents');
      } catch { /* the library is a convenience */ }
      return ctx;
    },

    // After the turn: make sure what was asked for exists. Returns {el, rows} (or null when there is nothing to report).
    async verify({ userText, ctx }) {
      const failures = ctx.failures || [];
      const it = readIntent(userText);
      const textPart = textClauses(userText);
      const wantE = wantsEdit(textPart);
      const reqs = countRequirements(userText);
      const relevant = !!active && (wantE || it.adds || it.removes || it.review || it.cleanup || reqs.length || (ctx.newImages || []).length);
      if (!relevant && !failures.length) return null;
      const signal = getSignal?.();
      const rows = [];
      const el = h('div', { class: 'checks' });
      let live = '';
      const draw = () => el.replaceChildren(h('div', { class: 'checks-head' }, icon('check', 15), 'Checked'),
        ...rows.map((r) => h('div', { class: `check-row ${r.s}` }, r.s === 'run' ? h('span', { class: 'spinner' }) : icon(r.s === 'ok' ? 'check' : r.s === 'bad' ? 'x' : 'alert', 14), h('span', {}, r.t === live.k ? live.v : r.t))));
      const off = onStatus((s) => {
        const b = s.image?.batch;
        if (b?.busy) { live = { k: rows.find((r) => r.s === 'run')?.t, v: `${b.stage || 'Working'} (${b.done}/${b.total} done)` }; draw(); }
      });
      const add = (s, t) => { rows.push({ s, t }); draw(); return rows[rows.length - 1]; };
      const finish = (row, s, t) => { row.s = s; row.t = t; live = ''; draw(); };
      const finishRows = () => ({ el, rows: rows.map((r) => ({ s: r.s === 'run' ? 'warn' : r.s, t: r.t })) });
      let touched = false;
      const cancel = () => fetch('/api/images/cancel', { method: 'POST' }).catch(() => {});
      signal?.addEventListener('abort', cancel, { once: true });
      try {
        let cur = active ? await sourceOf(active.name) : null;
        if (relevant && cur) {
          if (ctx.beforeName && ctx.beforeName !== active.name) ctx.before = null; // a different document was made this turn
          const before = ctx.before || { hash: null, facts: ZERO, source: '' };
          const refresh = async () => { cur = (await sourceOf(active.name)) || cur; };
          const run = async (label, url, body) => {
            const row = add('run', label);
            const r = await post(url, body, signal);
            const d = await json(r);
            return { row, r, d };
          };

          // 1. Tidiness: "make it uniform", "remove the weird prompts at the end". Done first, so pictures land in a clean document.
          if (it.cleanup) {
            const { row, r, d } = await run('Cleaning the document…', api(active.name, '/tidy'), {});
            if (r.ok) {
              touched = touched || (d.fixed || []).length > 0;
              finish(row, (d.fixed || []).length ? 'ok' : 'ok', (d.fixed || []).length ? `Cleaned: ${d.fixed.join('; ')}.` : 'Nothing needed cleaning.');
              for (const i of (d.remaining || [])) add('warn', `${i.detail}${i.fixable ? '' : '. Ask me to add them.'}`);
            } else finish(row, 'bad', `Could not clean it: ${d.detail || r.status}`);
            await refresh();
          }

          // 2. Each part of the text request: was it really done? Whatever was not, Sthanik does now.
          if (wantE && ctx.before) {
            const row = add('run', 'Checking that every change was made…');
            const r = await post(api(active.name, '/verify-edit'), { instruction: textPart, before: ctx.before.source }, signal);
            const d = await json(r);
            rows.splice(rows.indexOf(row), 1);
            if (r.ok) {
              for (const c of d.checked) {
                if (c.done) add('ok', `${c.clause}`);
                else if (c.fixed) { touched = true; add('ok', `${c.clause}: the model had not done this, so Sthanik did it.`); }
                else add('warn', `${c.clause}: could not be done${d.failed?.length ? ` (${d.failed[0].slice(0, 120)})` : ''}.`);
              }
              if (d.checked.some((c) => c.fixed)) toast('Missing changes were applied', 'info', 3000);
            } else add('bad', `Could not check the edit: ${d.detail || r.status}`);
            draw();
            await refresh();
          }

          // 3a. New pictures made this turn were meant for the document: put them in.
          const fresh = (ctx.newImages || []).filter((n) => !cur.source.includes(n));
          if (wantsAttach(userText) && fresh.length) {
            const { row, r, d } = await run(`Placing ${fresh.length} new picture${fresh.length === 1 ? '' : 's'} in the document…`, api(active.name, '/pictures'), { images: fresh });
            await refresh();
            if (r.ok) { touched = true; finish(row, d.skipped ? 'warn' : 'ok', `Placed ${d.added.length} picture${d.added.length === 1 ? '' : 's'} in: ${d.added.map((a) => a.section || 'the top').join(', ')}.`); }
            else finish(row, 'bad', `Could not place the pictures: ${d.detail || r.status}`);
          }

          const only = ctx.only || [];
          const where = only.length ? ` in ${only.join(', ')}` : '';
          // 3b0. A pure removal: whatever pictures remain (in the named sections, or everywhere) are unwanted, whoever put them there.
          if (it.removes && !it.adds) {
            const left = sectionPictures(cur.source, only);
            if (left.length) {
              const { row, r, d } = await run(`${left.length} picture${left.length === 1 ? ' is' : 's are'} still there${where}. Removing ${left.length === 1 ? 'it' : 'them'}…`, api(active.name, '/remove-pictures'), { only });
              await refresh(); touched = true;
              const now = sectionPictures(cur.source, only).length;
              finish(row, r.ok && !now ? 'ok' : 'warn', r.ok ? `Removed ${d.removed?.length ?? 0}.${now ? ` ${now} remain.` : ' None remain.'}` : `Could not remove them: ${d.detail || r.status}`);
            } else add('ok', `No pictures remain${where}.`);
          }
          // 3b. Old pictures the request wanted gone (replace / remove): the new ones from this turn stay.
          if (it.replaces || it.removes) {
            const names = sectionPictures(before.source || '', only);
            const keep = new Set((ctx.newImages || []));
            const stillThere = names.filter((n) => !keep.has(n) && sectionPictures(cur.source, only).includes(n));
            if (!names.length) add('ok', 'There were no pictures to remove.');
            else if (!stillThere.length) add('ok', `The ${names.length} old picture${names.length === 1 ? ' is' : 's are'} gone.`);
            else {
              const row = add('run', `${stillThere.length} old picture${stillThere.length === 1 ? ' is' : 's are'} still in the document. Removing ${stillThere.length === 1 ? 'it' : 'them'}…`);
              let gone = 0;
              for (const n of stillThere) { const r = await post(api(active.name, '/remove-pictures'), { image: n, only }, signal); if (r.ok) gone++; }
              touched = true;
              await refresh();
              finish(row, gone === stillThere.length ? 'ok' : 'warn', `Removed ${gone} of ${stillThere.length} old picture${stillThere.length === 1 ? '' : 's'}.`);
            }
          }

          // 3c. Counts ("10 images", "5 sections").
          let askedPictures = false;
          for (const q of reqs) {
            const have = factOf(cur.facts, q.unit);
            const want = q.delta ? factOf(before.facts, q.unit) + q.n : q.n;
            const label = `${q.unit === 'images' ? 'pictures' : q.unit}`;
            if (q.unit === 'images') askedPictures = true;
            if (have >= want) { add('ok', `${have} ${label} in the document (you asked for ${q.delta ? `${q.n} more` : q.n}).`); continue; }
            if (q.unit === 'images') {
              const { row, r, d } = await run(`Only ${have} of ${want} pictures exist. Making the other ${want - have}…`, api(active.name, '/illustrate'), { count: want - have, source: it.source });
              await refresh();
              touched = true;
              const now = factOf(cur.facts, 'images');
              if (r.ok) finish(row, now >= want ? 'ok' : 'warn', `${now} of ${want} pictures now.${d.failed?.length ? ` Problems: ${d.failed.join('; ')}` : ''}`);
              else finish(row, 'bad', `Could not make the pictures: ${d.detail || r.status}`);
            } else add('warn', `Only ${have} ${label}, but you asked for ${want}. Tell me to add the rest.`);
          }

          // 3d. Pictures asked for without a number: "images for each place", "find images online". The job is the whole document, not
          //     however many the model happened to do before it stopped.
          if (it.adds && !askedPictures) {
            if (pictureScope(userText) === 'single') {
              if (factOf(cur.facts, 'images') <= factOf(before.facts, 'images') && !(ctx.newImages || []).length) {
                const { row, r, d } = await run('No picture was added, so Sthanik is getting one…', api(active.name, '/illustrate'), { count: 1, source: it.source });
                await refresh();
                touched = true;
                if (r.ok) finish(row, d.added.length ? 'ok' : 'warn', `Added ${d.added.length} picture.${d.failed?.length ? ` Problems: ${d.failed.join('; ')}` : ''}`);
                else finish(row, 'bad', `Could not get the picture: ${d.detail || r.status}`);
              }
            } else {
              const left = uncoveredSections(cur.source, only);
              const total = only.length || (cur.facts.slides ?? cur.facts.sections ?? 0);
              if (!left.length) add('ok', only.length ? `${only.join(', ')} ${only.length === 1 ? 'has' : 'have'} a picture.` : `Every section has a picture (${factOf(cur.facts, 'images')} in the document).`);
              else {
                const { row, r, d } = await run(`${left.length} of ${total} sections still have no picture. Getting them…`, api(active.name, '/illustrate'), { count: left.length, source: it.source, only });
                await refresh();
                touched = true;
                const still = uncoveredSections(cur.source, only).length;
                if (r.ok) finish(row, still ? 'warn' : 'ok', still ? `${left.length - still} added; ${still} section${still === 1 ? '' : 's'} still without a picture.${d.failed?.length ? ` ${d.failed[0]}` : ''}` : `Every section now has a picture (${factOf(cur.facts, 'images')} in the document${it.source === 'web' ? ', from the web' : ''}).`);
                else finish(row, 'bad', `Could not get the pictures: ${d.detail || r.status}`);
              }
            }
          }
        }

        // 3c. "Check the pictures / the images are not good": a vision-model review, done here if the model did not call the tool.
        if (it.review && !ctx.reviewed) {
          const { row, r, d } = await run('Looking at every picture with the vision model (loading it takes a moment)…', api(active.name, '/review-pictures'), { only: ctx.only || [] });
          await refresh(); touched = true;
          finish(row, r.ok ? (d.unchanged ? 'warn' : 'ok') : 'bad', r.ok ? d.summary : `Could not review the pictures: ${d.detail || r.status}`);
        } else if (it.review) add('ok', 'The pictures were reviewed with the vision model.');

        // 4. Tools that failed and were never made to work: say so, instead of letting a cheerful reply hide it.
        for (const f of failures) add('warn', `${f.name} did not work: ${String(f.text).slice(0, 150)}`);
      } catch (e) {
        if (e.name !== 'AbortError') add('bad', `The check stopped: ${e.message}`);
      } finally {
        off();
        signal?.removeEventListener?.('abort', cancel);
        draw();
      }
      if (touched) toast('Document updated', 'info', 2500);
      return rows.length ? finishRows() : null;
    },
  };
  paint();
  return ws;
}
