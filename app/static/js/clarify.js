// Before a big request starts (a report, slides, a plan), check that it says what it is about, and ask for what is missing:
// a few questions with answer choices and a free-text field for "none of these". The answers are added to the request.
import { h, icon } from './ui.js';

const BIG = /\b(report|presentation|slides?|deck|spreadsheet|proposal|essay|article|white ?paper|brief|research|analy[sz]e|analysis|compare|comparison|study)\b/i;
const MAKE_VERB = /^\s*(?:please\s+|can you\s+|could you\s+)?(?:create|write|make|generate|prepare|draft|build|produce|compile|design|put together)\b/i;

// A request that makes something, as opposed to a remark or a quick question. Plan mode is for these.
export const looksLikeTask = (text) => { const n = text.trim().length; return (n >= 8 && MAKE_VERB.test(text) && (BIG.test(text) || n >= 25)) || (n >= 25 && BIG.test(text)) || n >= 160; };
// Worth a check for missing details before work starts (a short "make a presentation" is exactly what needs one).
export const needsCheck = (text) => { const n = text.trim().length; return n >= 8 && ((MAKE_VERB.test(text) && (BIG.test(text) || n >= 25)) || (BIG.test(text) && n >= 60)); };

const GENERIC = /\b(?:the|this|that|my|our|a)\s+(movie|film|show|series|documentary|book|novel|game|album|song|company|startup|product|app|brand|candidate|politician|player|team|match|event|paper|city|country|university|school|restaurant|hotel|trip|project)\b/i;
const KNOWN_CAPS = new Set(['West', 'Western', 'East', 'Eastern', 'North', 'South', 'American', 'European', 'Indian', 'British', 'Asian', 'African', 'English', 'Chinese', 'Japanese', 'Korean',
  'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
const FILLER = new Set(('a an the of for on about to me please and or with in by as is are be it its this that my our your new short long quick detailed deep deeply full complete some few '
  + 'create write make generate prepare draft build produce compile design put together can could you report presentation slides slide deck spreadsheet document proposal essay article brief research analysis analyse analyze summary').split(' '));

// Things a person means without saying: "the movie" with no title, or "make a report" with no subject.
function obviousGaps(text) {
  const out = [];
  const toks = text.split(/\s+/);
  // A capital letter inside a sentence marks a name; the first word of each sentence does not.
  const named = /["“][^"”]{2,}["”]/.test(text) || toks.some((w, i) => {
    if (i === 0 || /[.!?:]$/.test(toks[i - 1])) return false;
    const c = w.replace(/^[("'“]+|[.,!?)"'”]+$/g, '');
    return /^[A-Z][A-Za-z]{2,}/.test(c) && !KNOWN_CAPS.has(c);
  });
  const g = GENERIC.exec(text);
  if (g && !named) out.push({ question: `Which ${g[1]} do you mean?`, options: [], key: g[1] });
  const words = text.toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/).filter((w) => w && !FILLER.has(w));
  if (!g && words.length < 2) out.push({ question: 'What should it be about?', options: [], key: 'topic' });
  return out;
}

const SYSTEM = 'You check a request before any work starts. Decide what the person still has to tell you to get a good result. '
  + 'Ask ONLY about things that are missing and that would change the result, such as the exact subject, who it is for, the time period, or the kind of sources to use. '
  + 'Never ask about anything the request already says. Prefer asking nothing over asking something unneeded. '
  + 'Reply with JSON only: {"questions":[{"question":"...","options":["...","..."]}]}. At most 3 questions and at most 4 short options each; options must be real possible answers (never "Other" or "None"). '
  + 'If nothing essential is missing reply {"questions":[]}.';

async function modelQuestions(text, signal) {
  const body = {
    messages: [{ role: 'system', content: SYSTEM }, { role: 'user', content: `Request: ${text}` }],
    temperature: 0.2, max_tokens: 380, reasoning_effort: 'none', stream: false, response_format: { type: 'json_object' },
  };
  const r = await fetch('/llama/v1/chat/completions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!r.ok) return [];
  const raw = (await r.json()).choices?.[0]?.message?.content || '';
  let data = null;
  try { data = JSON.parse(raw); } catch { const m = /\{[\s\S]*\}/.exec(raw); try { data = m && JSON.parse(m[0]); } catch { /* not JSON: ask nothing */ } }
  const qs = Array.isArray(data?.questions) ? data.questions : [];
  return qs.map((q) => ({
    question: String(q?.question || '').trim(),
    options: (Array.isArray(q?.options) ? q.options : []).map((o) => String(o).trim()).filter((o) => o && o.length <= 70 && !/^(other|none|n\/a|something else)\b/i.test(o)).slice(0, 4),
  })).filter((q) => q.question.length >= 8 && q.question.length <= 160 && q.question.endsWith('?'));
}

const sameTopic = (a, b) => { const w = (s) => new Set(s.toLowerCase().match(/[a-z]{4,}/g) || []); const x = w(a), y = w(b); return [...x].filter((t) => y.has(t)).length >= 2; };

// Resolves to the text to append to the request ('' to carry on as it was written), or null if the person cancelled.
export async function clarify({ text, signal, ensure, container, scrollDown }) {
  const spin = h('div', { class: 'clarify-wait' }, h('span', { class: 'spinner' }), 'Checking whether I need to ask anything first…');
  container.append(spin); scrollDown?.();
  let qs = obviousGaps(text);
  try {
    await ensure();
    const asked = await modelQuestions(text, signal);
    for (const q of asked) if (qs.length < 3 && !qs.some((o) => sameTopic(o.question, q.question))) qs.push(q);
  } catch (e) {
    if (e.name === 'AbortError') { spin.remove(); return null; }
  }
  spin.remove();
  qs = qs.slice(0, 3);
  if (!qs.length) return '';

  return new Promise((resolve) => {
    const picks = qs.map(() => ({ choice: '', other: '' }));
    const card = h('div', { class: 'clarify' });
    const done = (v) => { card.remove(); resolve(v); };
    const compose = () => {
      const lines = qs.map((q, i) => ({ q: q.question, a: (picks[i].other.trim() || picks[i].choice).trim() })).filter((x) => x.a).map((x) => `- ${x.q} ${x.a}`);
      return lines.length ? `\n\nDetails:\n${lines.join('\n')}` : '';
    };
    const goBtn = h('button', { class: 'btn primary', onclick: () => done(compose()) }, icon('check', 15), 'Continue');
    const refresh = () => { goBtn.disabled = !picks.some((p) => p.choice || p.other.trim()); };
    const blocks = qs.map((q, i) => {
      const chips = q.options.map((o) => h('button', { type: 'button', class: 'clarify-opt', onclick: () => {
        picks[i].choice = picks[i].choice === o ? '' : o; picks[i].other = ''; other.value = '';
        chips.forEach((c) => c.classList.toggle('on', c.dataset.v === picks[i].choice)); refresh();
      }, 'data-v': o }, o));
      const other = h('input', { class: 'input clarify-other', placeholder: q.options.length ? 'None of these: type your own…' : 'Type your answer…', 'aria-label': q.question });
      other.addEventListener('input', () => { picks[i].other = other.value; if (other.value) { picks[i].choice = ''; chips.forEach((c) => c.classList.remove('on')); } refresh(); });
      other.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !goBtn.disabled) done(compose()); });
      return h('div', { class: 'clarify-q' }, h('div', { class: 'clarify-qt' }, `${i + 1}. ${q.question}`), chips.length ? h('div', { class: 'clarify-opts' }, ...chips) : null, other);
    });
    card.append(
      h('div', { class: 'clarify-head' }, h('span', { class: 'clarify-ic' }, icon('wand', 16)),
        h('div', { class: 'grow' }, h('b', {}, 'A quick question before I start'), h('small', {}, 'Pick an answer or type your own. Skip whatever does not matter.')),
        h('button', { class: 'icon-btn', title: 'Cancel: keep my message in the box', onclick: () => done(null) }, icon('x'))),
      ...blocks,
      h('div', { class: 'row', style: { justifyContent: 'flex-end', gap: '8px' } },
        h('button', { class: 'btn', onclick: () => done('') }, 'Skip questions'), goBtn));
    refresh();
    container.append(card); scrollDown?.();
    (blocks[0].querySelector('input'))?.focus();
  });
}
