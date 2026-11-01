// Tool catalogue + the user's preferences (which groups are on, which tools are muted, when to ask first).
// The backend advertises everything that exists; what a chat actually sends to the model is decided here.

const KEY = 'lfm3.tools.prefs';
const listeners = new Set();

const load = () => {
  try { return { enabledGroups: {}, disabledTools: [], approval: {}, pinnedSkills: [], ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { enabledGroups: {}, disabledTools: [], approval: {}, pinnedSkills: [] }; }
};

export const toolstore = {
  groups: [],
  filesRoot: null,
  loaded: false,
  prefs: load(),
  forced: new Set(), // groups switched on for the turn in progress (e.g. document tools while a document is being worked on)
  hidden: new Set(), // tools withheld for the turn in progress (creating a new file while the user is changing one)
  activeDoc: null,   // the document the turn works on: sent with tool calls so "the document" means that one
  scope: [],         // sections the request named ("the picture in Place 3"): picture tools stay inside them
  turn: null,        // id of the turn in progress: edits made during it can be undone together

  subscribe(fn) { listeners.add(fn); if (this.loaded) fn(this); return () => listeners.delete(fn); },
  _emit() { listeners.forEach((fn) => fn(this)); },
  _save() { try { localStorage.setItem(KEY, JSON.stringify(this.prefs)); } catch { /* private mode */ } this._emit(); },

  _apply(data) { this.groups = data.groups; this.filesRoot = data.files_root; this.loaded = true; this._emit(); return data; },

  async refresh() { return this._apply(await (await fetch('/api/tools')).json()); },

  async _post(url, body, method = 'POST') {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
    return this._apply(data);
  },
  setFilesRoot(path) { return this._post('/api/tools/files-root', { path }); },
  reloadMcp() { return this._post('/api/mcp/reload'); },
  addServer(name, url) { return this._post('/api/mcp/servers', { name, url }); },
  addLocalServer(name, command, args) { return this._post('/api/mcp/servers', { name, command, args }); },
  removeServer(name) { return this._post(`/api/mcp/servers/${encodeURIComponent(name)}`, null, 'DELETE'); },
  setServerEnabled(name, enabled) { return this._post(`/api/mcp/servers/${encodeURIComponent(name)}/enabled`, { enabled }); },

  group(id) { return this.groups.find((g) => g.id === id); },
  groupOf(toolName) { return this.groups.find((g) => g.tools.some((t) => t.name === toolName)); },
  usable(g) { return g && (g.status === 'ready' || g.status === 'connected'); },
  isGroupOn(id) { return ((id === 'skills' || (this.prefs.enabledGroups[id] ?? (id === 'memory' || id === 'utility'))) || this.forced.has(id)) && this.usable(this.group(id)); }, // skills are always on: they only apply when a request matches // memory, and the offline calculator and clock, are on unless switched off
  force(ids) { this.forced = new Set(ids); },
  hide(names) { this.hidden = new Set(names); },
  setGroupOn(id, on) { this.prefs.enabledGroups[id] = on; this._save(); },
  isToolOn(name) { return !this.prefs.disabledTools.includes(name); },
  setToolOn(name, on) {
    const set = new Set(this.prefs.disabledTools);
    on ? set.delete(name) : set.add(name);
    this.prefs.disabledTools = [...set];
    this._save();
  },
  isPinned(name) { return this.prefs.pinnedSkills.includes(name); },
  setPinned(name, on) {
    const set = new Set(this.prefs.pinnedSkills);
    on ? set.add(name) : set.delete(name);
    this.prefs.pinnedSkills = [...set];
    this._save();
  },
  approval(groupId) { return this.prefs.approval[groupId] || this.group(groupId)?.default_approval || 'ask'; },
  setApproval(groupId, v) { this.prefs.approval[groupId] = v; this._save(); },

  // OpenAI-format tool list for the groups currently switched on.
  enabledTools() {
    return this.groups.filter((g) => this.isGroupOn(g.id))
      .flatMap((g) => g.tools.filter((t) => this.isToolOn(t.name) && !this.hidden.has(t.name)).map((t) => ({ ...t, group: g.id })));
  },
  schemas() {
    return this.enabledTools().map((t) => ({ type: 'function', function: { name: t.name, description: t.description, parameters: t.schema } }));
  },
  activeCount() { return this.enabledTools().length; },

  async call(name, args) {
    const r = await fetch('/api/tools/call', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, arguments: args, context: { active_document: this.activeDoc, only_sections: this.scope, turn: this.turn } }) });
    if (!r.ok) throw new Error(`Tool request failed (${r.status})`);
    return r.json();
  },
};

toolstore.refresh().catch(() => {});
