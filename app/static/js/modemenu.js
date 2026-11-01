// The Simple / Complex switch beside Search. Simple answers quickly. Complex makes its own to-do list for the request, researches each item
// in several rounds, and checks and fixes its own work before it stops.
import { h, icon } from './ui.js';

export const MODES = [
  { id: 'simple', label: 'Simple', icon: 'zap', blurb: 'Quick answers. Searches the web when it needs to.' },
  { id: 'complex', label: 'Complex', icon: 'workflow', blurb: 'Makes its own to-do list, researches each item in rounds, then reviews and fixes its work. Slower; best for reports, slides and research.' },
];

export function createModeMenu({ get, set }) {
  const lead = h('span', { class: 'cm-lead' });
  const label = h('span');
  const button = h('button', { type: 'button', class: 'chip-btn cm-btn', 'aria-haspopup': 'true', 'aria-expanded': 'false' }, lead, label, icon('chevronDown', 13));
  const panel = h('div', { class: 'popover mode-panel', style: { display: 'none' } });
  const paint = () => {
    const m = MODES.find((x) => x.id === get()) || MODES[0];
    lead.replaceChildren(icon(m.icon, 15));
    label.textContent = m.label;
    button.classList.toggle('on', m.id === 'complex');
    button.title = m.blurb;
    panel.replaceChildren(...MODES.map((o) => h('button', { type: 'button', class: `mode-opt${o.id === m.id ? ' on' : ''}`, onclick: () => { set(o.id); close(); paint(); } },
      h('span', { class: 'cm-tile-ic' }, icon(o.icon, 18)),
      h('span', { class: 'cm-tile-text' }, h('b', {}, o.label), h('small', {}, o.blurb)),
      o.id === m.id ? icon('check', 16) : null)));
  };
  const close = () => { panel.style.display = 'none'; button.setAttribute('aria-expanded', 'false'); };
  button.addEventListener('click', (e) => { e.stopPropagation(); const open = panel.style.display === 'none'; panel.style.display = open ? '' : 'none'; button.setAttribute('aria-expanded', String(open)); });
  const outside = (e) => { if (!panel.contains(e.target) && !button.contains(e.target)) close(); };
  document.addEventListener('click', outside);
  paint();
  return { button, panel, destroy: () => document.removeEventListener('click', outside), paint };
}
