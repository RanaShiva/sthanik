// The private-session switch. Off (default): everything is saved as usual and search uses Bing's strict adult filter. On: nothing is saved to disk
// and that filter is off; the server wipes what the session made when the switch goes back off. (In the code "safe" means "not private".)
import { h, icon, toast } from './ui.js';
import { onStatus, state } from './api.js';

export const isPrivate = () => state.status?.safe_browsing === false;
// Linux keeps the private session on a RAM disk; Windows and macOS have none, so it is a temporary folder that is zero-filled and removed at the end.
export const privateWhere = () => (state.status?.private_ram === false ? 'in a temporary folder that is erased when the session ends' : 'kept in RAM only and never saved to disk');

// Browser-side leftovers of a session: which conversation was open last.
function forgetSession() {
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith('lfm3.lastchat.')) localStorage.removeItem(k);
    for (const k of ['lfm3.chat.activedoc', 'lfm3.prefill', 'lfm3.chat.preselect']) sessionStorage.removeItem(k);
  } catch { /* private window */ }
}

let switching = false;

export async function setSafe(want) {
  if (switching || want === !isPrivate()) return false;
  switching = true;
  try {
    const r = await fetch('/api/safe-browsing', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ safe: want }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Could not change it (${r.status})`);
    forgetSession();
    location.reload(); // drops every conversation, picture and search result held by the page
    return true;
  } catch (e) {
    toast(e.message, 'error', 7000);
    switching = false;
    return false;
  }
}

const TITLE_OFF = 'Start a private session: nothing is saved, and the web-search adult filter is switched off.';

// The row on the Models & System page.
export function safeBrowsingSwitch() {
  const box = h('input', { type: 'checkbox', 'aria-label': 'Private session' });
  box.addEventListener('change', async () => { if (!(await setSafe(!box.checked))) box.checked = isPrivate(); });
  onStatus((s) => { if (!switching && s.safe_browsing !== undefined) box.checked = !s.safe_browsing; });
  return h('label', { class: 'switch-row', title: TITLE_OFF },
    h('span', {}, h('b', {}, 'Private session'),
      h('small', {}, 'Off: chats, documents and pictures are saved and web search uses the strict adult-content filter. On: nothing is saved to disk (' + privateWhere() + '), schedules and memory are paused, and that search filter is off.')),
    h('span', { class: 'switch' }, box, h('i')));
}

// The control in the top-right corner of every page.
export function safeTopControl() {
  const lead = h('span', { class: 'st-ic' });
  const label = h('span', { class: 'st-label' });
  const btn = h('button', { type: 'button', class: 'safe-top', onclick: () => setSafe(isPrivate()) }, lead, label);
  onStatus((s) => {
    const priv = s.safe_browsing === false;
    btn.classList.toggle('private', priv);
    lead.replaceChildren(icon(priv ? 'lock' : 'shield', 15));
    label.textContent = priv ? 'Private' : 'Private session';
    btn.title = priv ? 'Private session is on: nothing is saved, search filter off. Click to end it.' : TITLE_OFF;
    btn.setAttribute('aria-pressed', String(priv));
  });
  return btn;
}
