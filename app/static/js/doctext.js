// Small text rules for plans and documents. Pure functions (no page needed), so they are tested with node.

// A title that names the job instead of the subject. Only these shapes: "Create a PDF about X", "Document Outline for PDF on X",
// "Outline of a document on X". "Report on Q3 Sales" and "Plan for a Weekend in Lisbon" are real titles and stay as they are.
// Mirrors doclint.clean_title (the server applies the same rule to titles that do not come from a plan); tests/test_doctext.py checks both.
const ASK = /^\s*(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:create|make|write|generate|prepare|build|produce)\s+(?:me\s+)?(?:(?:an?|the)\s+)?(?:(?:pdf|document|doc|report|file|word file)\s+)?(?:(?:about|on|of|for|covering)\s+)?/i;
const META = /^\s*(?:(?:document|doc|pdf|word|file)\s+(?:outline|plan|draft|structure|template|skeleton)|(?:outline|plan|draft|structure|template|skeleton)\s+(?:for|of)\s+(?:an?\s+|the\s+)?(?:pdf|document|doc|file|word file))\s*(?:for|of|on|about)?\s*/i;
const FORMAT = /^\s*(?:(?:an?|the)\s+)?(?:pdf|document|doc|word file|file)\s+(?:on|about|of|for|covering)\s+/i;

export function cleanTitle(title) {
  const t = String(title || '').split(/\s+/).filter(Boolean).join(' ');
  if (t.endsWith('?')) return t; // a question is not a title that names its job
  let cut = t;
  if (ASK.test(t)) cut = t.replace(ASK, '');
  else if (META.test(t)) cut = t.replace(META, '').replace(FORMAT, '');
  cut = cut.replace(/^[\s:\-–—]+|[\s:\-–—]+$/g, '');
  if (cut.length < 5 || cut.split(' ').length < 2) return t;
  return cut[0].toUpperCase() + cut.slice(1);
}

// Every web address in a piece of text, whole: one that has a bracket in it (…/Neural_network_(machine_learning)) keeps its closing bracket,
// and a bracket or full stop that merely ends the sentence around it is left out.
export function urlsIn(text) {
  const out = [];
  for (const m of String(text || '').matchAll(/https?:\/\/[^\s<>"'\]]+/g)) {
    let u = m[0];
    for (;;) {
      const last = u.slice(-1);
      const unbalanced = last === ')' && (u.match(/\(/g) || []).length < (u.match(/\)/g) || []).length;
      if (/[.,;:!?]/.test(last) || unbalanced) u = u.slice(0, -1); else break;
    }
    if (u.length > 8) out.push(u);
  }
  return out;
}
