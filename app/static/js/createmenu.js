// The "Create" menu beside Search in the chat composer: the kinds of file the chat can make, and the tool switches.
import { h, icon } from './ui.js';
import { toolstore } from './toolstore.js';

// What each entry makes: the tool the model should call, the skill that tells it how, and a one-line description for the tile.
export const MAKE = [
  { id: 'docx', label: 'Word document', ext: '.docx', icon: 'filetext', tool: 'create_document', fmt: 'docx', exportAs: 'docx', plan: 'a Word document (.docx)', skill: 'docx', blurb: 'Report, letter or memo' },
  { id: 'pptx', label: 'PowerPoint slides', ext: '.pptx', icon: 'presentation', tool: 'create_slides', fmt: 'pptx', exportAs: 'pptx', plan: 'a PowerPoint presentation', skill: 'pptx', blurb: 'A slide deck' },
  { id: 'xlsx', label: 'Excel spreadsheet', ext: '.xlsx', icon: 'table', tool: 'create_spreadsheet', fmt: null, exportAs: null, plan: 'a spreadsheet (Excel .xlsx)', skill: 'xlsx', blurb: 'Tables and numbers' },
  { id: 'pdf', label: 'PDF document', ext: '.pdf', icon: 'file', tool: 'create_document', fmt: 'pdf', exportAs: 'pdf', plan: 'a PDF document', skill: 'pdf', blurb: 'A report to share or print' },
  { id: 'slides-pdf', label: 'Slides as PDF', ext: '.pdf', icon: 'presentation', tool: 'create_slides', fmt: 'pdf', exportAs: 'slides-pdf', plan: 'a presentation as PDF slides', skill: 'pptx', blurb: 'A deck that opens anywhere' },
  { id: 'html', label: 'Web page', ext: '.html', icon: 'globe', tool: 'create_document', fmt: 'html', exportAs: 'html', plan: 'a document, as a web page (HTML)', skill: 'html', blurb: 'A page you open in a browser' },
];
export const makeKind = (id) => MAKE.find((m) => m.id === id) || null;

const skillBodies = new Map();
// The instruction for a turn that should end in a file: which tool to call and the skill's own guidance.
export async function makeInstruction(kind) {
  let skill = '';
  try {
    if (!skillBodies.has(kind.skill)) skillBodies.set(kind.skill, await (await fetch(`/api/skills/${encodeURIComponent(kind.skill)}`)).json());
    const sk = skillBodies.get(kind.skill);
    if (sk?.body && sk.enabled !== false) skill = `\n\n# Skill: ${sk.name}\n\n${sk.body}`;
  } catch { /* the skill was removed or the backend is restarting: the instruction alone still works */ }
  return `The user wants a ${kind.label} file (${kind.ext}). Write the complete content yourself, then call the ${kind.tool} tool once${kind.fmt ? ` with format "${kind.fmt}"` : ''} to make the file. `
    + `Afterwards reply with one or two sentences about what you made. Do not paste the whole document into the chat.${skill}`;
}

export function createMenu({ onPick }) {
  let kind = null;
  const label = h('span', {}, 'Create');
  const badge = h('span', { class: 'badge', style: { display: 'none' } });
  const lead = h('span', { class: 'cm-lead' }, icon('sparkles', 15));
  const button = h('button', { type: 'button', class: 'chip-btn cm-btn', title: 'Create a file, and choose which tools the chat may use', 'aria-haspopup': 'true', 'aria-expanded': 'false' },
    lead, label, badge, icon('chevronDown', 13));
  const panel = h('div', { class: 'popover cm-panel', style: { display: 'none' } });
  const chosen = h('button', { type: 'button', class: 'chip-btn on', style: { display: 'none' }, title: 'The next message makes this file. Click to cancel.' });

  const open = () => { panel.style.display = ''; button.setAttribute('aria-expanded', 'true'); };
  const close = () => { panel.style.display = 'none'; button.setAttribute('aria-expanded', 'false'); };
  const toggle = () => (panel.style.display === 'none' ? open() : close());

  function setKind(next) {
    kind = next;
    chosen.style.display = kind ? '' : 'none';
    if (kind) chosen.replaceChildren(icon(kind.icon, 14), `${kind.label}`, icon('x', 12));
    onPick?.(kind);
  }
  chosen.addEventListener('click', () => setKind(null));

  let sig = '';
  function render() {
    // Status and tool events arrive every second or so; repainting an unchanged open menu would make it flicker.
    const next = JSON.stringify([kind?.id, toolstore.groups.map((g) => [g.id, g.status, g.tools.length, toolstore.isGroupOn(g.id), toolstore.usable(g), toolstore.approval(g.id)])]);
    if (next === sig) return;
    sig = next;
    const n = toolstore.activeCount();
    badge.style.display = n ? '' : 'none';
    badge.textContent = n;

    const tiles = MAKE.map((m) => h('button', { type: 'button', class: `cm-tile${kind?.id === m.id ? ' on' : ''}`, onclick: () => { setKind(kind?.id === m.id ? null : m); close(); } },
      h('span', { class: 'cm-tile-ic' }, icon(m.icon, 18)),
      h('span', { class: 'cm-tile-text' }, h('b', {}, m.label), h('small', {}, `${m.ext} · ${m.blurb}`))));

    panel.replaceChildren(
      h('div', { class: 'cm-label' }, 'Create a file'),
      h('div', { class: 'cm-grid' }, ...tiles),
      h('div', { class: 'cm-label' }, 'Tools & connectors'),
      ...toolstore.groups.filter((g) => g.id !== 'skills').map((g) => { // skills are always available: nothing to switch
        const usable = toolstore.usable(g);
        return h('label', { class: `pop-row${usable ? '' : ' off'}` },
          h('span', { class: 'pop-ic' }, icon(g.icon || 'wrench', 16)),
          h('span', { class: 'pop-text' }, h('b', {}, g.label),
            h('small', {}, usable ? `${g.tools.length} tool${g.tools.length === 1 ? '' : 's'} · ${toolstore.approval(g.id) === 'ask' ? 'asks first' : 'runs automatically'}` : (g.error || g.status))),
          h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: toolstore.isGroupOn(g.id), disabled: !usable, onchange: (e) => toolstore.setGroupOn(g.id, e.target.checked) }), h('i')));
      }),
      h('a', { class: 'popover-foot', href: '#/tools' }, 'Manage tools & connectors', icon('chevronRight', 14)));
  }

  button.addEventListener('click', (e) => { e.stopPropagation(); toggle(); });
  const outside = (e) => { if (!panel.contains(e.target) && !button.contains(e.target)) close(); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('click', outside);
  document.addEventListener('keydown', esc);
  const offTools = toolstore.subscribe(render);
  render();

  return {
    button, panel, chosen,
    get kind() { return kind; },
    setKind,
    destroy() { document.removeEventListener('click', outside); document.removeEventListener('keydown', esc); offTools?.(); },
  };
}
