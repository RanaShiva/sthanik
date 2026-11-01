// Does a message need a web lookup before the model answers?
//
// A 1.7B model asked "How many minutes for a jammy egg?" or "Who won the 2025 Nobel Prize in Physics?" often answers from memory (wrongly)
// or searches once, in the wrong place, and gives up. When the user has Search switched on, a question whose answer is a fact that changes or
// that a small model cannot be trusted to remember (a figure, a date, a name, "latest", "current") is looked up by the app itself before the
// model writes anything, and the results are in front of it when it answers.
//
// The rule is deliberately narrow: a question that asks for such a fact, short enough to be one question, and not about the conversation, the
// user's own material, code, a calculation or a piece of writing. Anything else is left to the model, which can still search for itself.

const ASKS = /^\s*(?:(?:hey|hi|hello|ok(?:ay)?|so|and|also)[,.]?\s+)?(?:(?:please|pls)\s+)?(?:(?:can|could|would) you (?:please )?(?:tell me|find out|check|look up|search(?: for)?)\s+)?(?:tell me\s+|find out\s+|look up\s+|search for\s+)?(?:who|what|which|when|where|how (?:many|much|long|old|far|tall|big|fast|high|heavy|often|late|early)|is|are|was|were|did|does|do|will|has|have|whose|(?:the\s+)?(?:latest|current|newest))\b/i;

// What makes the answer something to look up rather than to remember.
const FACT = new RegExp([
  '\\b(?:latest|current(?:ly)?|newest|recent(?:ly)?|today|tonight|tomorrow|yesterday|right now|nowadays|this (?:year|week|month|season)|last (?:year|week|month|night))\\b',
  '\\b20[12]\\d\\b',
  '\\bhow (?:many|much|long|old|far|tall|big|fast|high|heavy|often)\\b',
  '\\b(?:what|which) (?:year|date|time|day|version|number|percentage|percent|price|score|rank|ranking|temperature|weather)\\b',
  '\\bwhen (?:did|was|is|does|will|do|were|are)\\b',
  '\\bwho (?:won|is the|are the|was the|were the|wrote|invented|discovered|founded|created|directed|plays?|played|owns|leads|runs)\\b',
  '\\b(?:winners?|ceo|cto|cfo|president|prime minister|chancellor|governor|mayor|captain|coach|champions?)\\b',
  '\\b(?:price|cost|population|release date|released|version|score|scores|schedule|weather|forecast|stock|exchange rate|news|record|ranking|ranked|standings|salary|net worth|age of|height of|distance (?:to|between)|time in)\\b',
  '\\b(?:minutes?|hours?|degrees|temperature)\\b.*\\b(?:cook|boil|bake|roast|fry|grill|steep|brew|soak|marinate|rest|proof)\\b|\\b(?:cook|boil|bake|roast|fry|grill|steep|brew|soak|marinate|rest|proof)\\b.*\\b(?:minutes?|hours?|degrees|temperature)\\b',
].join('|'), 'i');

// What it is not: the conversation itself, the user's own things, code, sums and writing jobs.
const NOT = new RegExp([
  '\\b(?:my|mine|our|ours|myself)\\b',
  '\\b(?:this|these|above|below|previous(?:ly)?|earlier|following|attached|uploaded|the (?:document|file|image|picture|photo|video|audio|recording|text|code|function|script|error|conversation|chat))\\b',
  '```|`[^`]+`',
  '\\b(?:translate|rewrite|paraphrase|summari[sz]e|proofread|edit|write|draft|compose|generate|create|make|draw|design|code|implement|debug|refactor|solve|calculate|compute|convert|simplify|expand|derive|prove)\\b',
  '\\d\\s*[-+*/^x×÷]\\s*\\d',
].join('|'), 'i');

export function needsLookup(text) {
  const t = String(text || '').trim();
  if (t.length < 8 || t.length > 220 || /\n/.test(t)) return false;
  if ((t.match(/[?.!]/g) || []).length > 2 && !/\?\s*$/.test(t)) return false; // several sentences: a task, not a question
  return ASKS.test(t) && FACT.test(t) && !NOT.test(t);
}

// The words to search for: the question as asked, without the politeness in front of it.
export function lookupQuery(text) {
  return String(text || '').trim()
    .replace(/^\s*(?:(?:hey|hi|hello|ok(?:ay)?|so|and|also)[,.]?\s+)?(?:please\s+)?(?:(?:can|could|would) you (?:please )?(?:tell me|find out|check|look up|search(?: for)?)\s+)?(?:tell me\s+|find out\s+|look up\s+|search for\s+)?/i, '')
    .replace(/[?\s]+$/, '')
    .slice(0, 200);
}
