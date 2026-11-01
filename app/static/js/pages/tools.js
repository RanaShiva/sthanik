import { h, icon, toast } from '../ui.js';
import { toolstore } from '../toolstore.js';

const OPEN_KEY = 'lfm3.tools.open';

export default {
  title: 'Tools & Connectors',
  subtitle: 'Give models the web, your files, images and any MCP server',
  mount(view) {
    const body = h('div');
    let busy = false;
    // Every card starts collapsed; expanding is remembered for the session.
    let open = new Set();
    try { open = new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY) || '[]')); } catch { /* ignore */ }
    const remember = () => { try { sessionStorage.setItem(OPEN_KEY, JSON.stringify([...open])); } catch { /* private mode */ } };

    const wrap = (fn) => async (...a) => {
      if (busy) return;
      busy = true;
      try { await fn(...a); } catch (e) { toast(e.message, 'error'); }
      busy = false;
      render();
    };

    const statusChip = (g) => {
      if (g.status === 'ready' || g.status === 'connected') return h('span', { class: 'chip good' }, h('span', { class: 'dot' }), g.kind === 'mcp' ? 'Connected' : 'Ready');
      if (g.status === 'starting') return h('span', { class: 'chip accent' }, h('span', { class: 'dot pulse' }), 'Starting');
      if (g.status === 'needs_setup') return h('span', { class: 'chip warn' }, 'Needs setup');
      if (g.status === 'disabled') return h('span', { class: 'chip' }, 'Disabled');
      return h('span', { class: 'chip bad' }, 'Error');
    };

    const approvalSelect = (g) => {
      const sel = h('select', { class: 'input', title: 'When the model calls one of these tools', style: { width: 'auto' } },
        h('option', { value: 'auto' }, 'Run automatically'), h('option', { value: 'ask' }, 'Ask me first'));
      sel.value = toolstore.approval(g.id);
      sel.addEventListener('change', () => { toolstore.setApproval(g.id, sel.value); });
      return sel;
    };

    const sw = (label, title, checked, onchange, disabled = false) => h('label', { class: 'sw-wrap sw-inline', title, onclick: (e) => e.stopPropagation() },
      h('span', { class: 'sw-label' }, label),
      h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked, disabled, onchange }), h('i')));

    // A collapsed-by-default card. `bodyEls` only shows when the card is expanded.
    function card(id, { iconName, title, chips = [], desc, foot, bodyEls }) {
      const el = h('div', { class: `card tcard${open.has(id) ? ' open' : ''}` });
      const head = h('button', { class: 'tcard-head', type: 'button', 'aria-expanded': String(open.has(id)) },
        h('span', { class: 'tg-icon' }, icon(iconName, 20)),
        h('span', { class: 'grow' }, h('span', { class: 'tcard-title' }, title), h('span', { class: 'row wrap', style: { gap: '6px', marginTop: '4px' } }, chips)),
        h('span', { class: 'tcard-chev' }, icon('chevronDown', 16)));
      head.addEventListener('click', () => {
        const now = !el.classList.contains('open');
        el.classList.toggle('open', now);
        head.setAttribute('aria-expanded', String(now));
        now ? open.add(id) : open.delete(id);
        remember();
      });
      el.append(...[head, h('p', { class: 'tcard-desc' }, desc), foot ? h('div', { class: 'tcard-foot' }, foot) : null, h('div', { class: 'tcard-body' }, bodyEls)].filter(Boolean));
      return el;
    }

    function groupCard(g) {
      const usable = toolstore.usable(g);
      const isMcp = g.kind === 'mcp';
      const bodyEls = [];
      bodyEls.push(h('div', { class: 'tg-setup' },
        usable && h('label', { class: 'row' }, h('span', { class: 'muted' }, 'When the model calls these tools:'), approvalSelect(g)),
        isMcp && sw('Server', 'Start or stop this MCP server', g.enabled, wrap(async (e) => {
          if (e.target.checked && g.transport !== 'http' && !confirm(`Start ${g.id.slice(4)}?\n\nThis runs a program on your computer as you, and it can read or change your files. Only continue if you trust it.`)) return;
          await toolstore.setServerEnabled(g.id.slice(4), e.target.checked);
        })),
        isMcp && h('button', { class: 'btn sm danger', onclick: wrap(async () => { if (confirm(`Remove the server ${g.id.slice(4)}?`)) await toolstore.removeServer(g.id.slice(4)); }) }, icon('trash', 14), 'Remove server')));
      if (g.id === 'files') {
        const path = h('input', { class: 'input grow', placeholder: '/home/you/Documents/notes', value: toolstore.filesRoot || '' });
        bodyEls.push(h('div', { class: 'tg-setup' }, icon('folder', 18), path,
          h('button', { class: 'btn sm primary', onclick: wrap(() => toolstore.setFilesRoot(path.value)) }, 'Connect folder'),
          toolstore.filesRoot && h('button', { class: 'btn sm', onclick: wrap(() => toolstore.setFilesRoot('')) }, 'Disconnect'),
          h('span', { class: 'muted', style: { fontSize: '12px', flexBasis: '100%' } },
            'Read-only. The model can list and read text files inside this folder only; hidden files and anything outside it are refused.')));
      }
      if (g.tools.length) {
        bodyEls.push(h('div', { class: 'tg-tools' }, g.tools.map((t) => h('div', { class: 'tg-tool' },
          h('input', { type: 'checkbox', checked: toolstore.isToolOn(t.name), title: 'Offer this tool', onchange: (e) => toolstore.setToolOn(t.name, e.target.checked) }),
          h('div', { class: 'grow' }, h('b', {}, t.name), h('p', {}, t.description))))));
      }
      const approval = toolstore.approval(g.id) === 'ask' ? 'asks first' : 'runs automatically';
      return card(g.id, {
        iconName: g.icon || 'wrench', title: g.label,
        chips: [statusChip(g), isMcp && h('span', { class: 'chip' }, g.transport === 'http' ? 'remote' : 'local')].filter(Boolean),
        desc: g.error && !usable ? g.error : g.description,
        foot: [h('span', { class: 'muted tcard-meta' }, `${g.tools.length} tool${g.tools.length === 1 ? '' : 's'}${usable ? ` · ${approval}` : ''}`), h('div', { class: 'grow' }),
          g.id === 'skills' ? h('span', { class: 'chip good', title: 'Skills apply by themselves when a request matches one' }, 'Always on') : sw('In chats', usable ? 'Offer these tools in chats' : 'Not available yet', toolstore.isGroupOn(g.id), (e) => toolstore.setGroupOn(g.id, e.target.checked), !usable)],
        bodyEls,
      });
    }

    function render() {
      if (!toolstore.loaded) { body.replaceChildren(h('div', { class: 'muted' }, 'Loading…')); return; }
      const builtin = toolstore.groups.filter((g) => g.kind === 'builtin');
      const mcp = toolstore.groups.filter((g) => g.kind === 'mcp');
      let kind = 'remote';
      const name = h('input', { class: 'input', placeholder: 'Name, e.g. github', 'aria-label': 'Server name' });
      const url = h('input', { class: 'input', placeholder: 'https://example.com/mcp', 'aria-label': 'Server URL' });
      const cmd = h('input', { class: 'input', placeholder: 'Command, e.g. npx', 'aria-label': 'Command' });
      const args = h('input', { class: 'input', placeholder: 'Arguments, e.g. -y @modelcontextprotocol/server-filesystem /home/me/notes', 'aria-label': 'Arguments' });
      const remoteRow = h('div', { class: 'stack', style: { gap: '8px' } }, url, h('small', { class: 'muted' }, 'A streamable-HTTP MCP server, no sign-in. It receives whatever the model sends it.'));
      const localRow = h('div', { class: 'stack', style: { gap: '8px', display: 'none' } }, cmd, args,
        h('small', { class: 'muted' }, 'Runs a program on this computer as you. It is added switched off and starts only after you turn it on and confirm.'));
      const tabs = ['remote', 'local'].map((k) => h('button', { type: 'button', class: `tab${k === kind ? ' active' : ''}`, onclick: () => {
        kind = k; tabs.forEach((t, n) => t.classList.toggle('active', ['remote', 'local'][n] === k));
        remoteRow.style.display = k === 'remote' ? '' : 'none'; localRow.style.display = k === 'local' ? '' : 'none';
      } }, k === 'remote' ? 'Remote (URL)' : 'Local (command)'));
      const addCard = card('add-server', {
        iconName: 'plus', title: 'Add an MCP server', chips: [h('span', { class: 'chip' }, 'Remote or local')],
        desc: 'Connect more tools: a server on the web, or a program that runs on this computer.',
        bodyEls: [h('div', { class: 'tg-setup', style: { alignItems: 'stretch', flexDirection: 'column', gap: '10px' } },
          h('div', { class: 'tabs', style: { alignSelf: 'flex-start' } }, ...tabs), name, remoteRow, localRow,
          h('div', {}, h('button', { class: 'btn primary', onclick: wrap(async () => {
            if (kind === 'remote') await toolstore.addServer(name.value, url.value); else await toolstore.addLocalServer(name.value, cmd.value, args.value);
            toast(kind === 'remote' ? 'Server added' : 'Server added, switched off. Turn it on when you are ready.');
            name.value = url.value = cmd.value = args.value = '';
          }) }, icon('plus'), 'Add server')))],
      });
      body.replaceChildren(
        h('div', { class: 'banner' }, icon('shield'), h('div', { class: 'grow' },
          h('b', {}, 'You stay in control. '),
          'Web tools send the model’s search queries to the internet (Bing) and fetch pages you or the model name. Tool results are treated as untrusted text, and tools that read your files or run third-party code ask before running by default.')),
        h('div', { class: 'section-title', style: { marginTop: '8px' } }, 'Built-in'),
        h('div', { class: 'tcards' }, builtin.map(groupCard)),
        h('div', { class: 'section-title' }, 'MCP servers',
          h('button', { class: 'btn sm', onclick: wrap(() => toolstore.reloadMcp()) }, icon('refresh', 14), 'Reload')),
        h('div', { class: 'tcards' }, [...mcp.map(groupCard), addCard]),
      );
    }

    const off = toolstore.subscribe(() => { if (!busy) render(); });
    toolstore.refresh().catch(() => {});
    view.append(body);
    render();
    return off;
  },
};
