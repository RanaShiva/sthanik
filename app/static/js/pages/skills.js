import { h, icon, toast, modal, markdown } from '../ui.js';
import { enableCodeCopy } from '../markdown.js';
import { toolstore } from '../toolstore.js';

const FILTERS = [['all', 'All'], ['builtin', 'Built-in'], ['custom', 'Yours'], ['github', 'Installed']];

async function api(url, method = 'GET', body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

const kindOf = (sk) => (sk.builtin ? 'builtin' : sk.custom ? 'custom' : 'github');
const kindLabel = { builtin: 'Built-in', custom: 'Yours', github: 'GitHub' };

export default {
  title: 'Skills',
  subtitle: 'Playbooks the model follows to do a job well',
  mount(view) {
    let skills = [], removed = [], filter = 'all', query = '', busy = false;
    const grid = h('div', { class: 'skill-grid' });
    const actions = h('div', { class: 'row wrap' });

    const guard = (fn) => async (...a) => {
      if (busy) return;
      busy = true;
      try { await fn(...a); } catch (e) { toast(e.message, 'error', 7000); }
      busy = false;
    };
    const load = (d) => { skills = d.skills; removed = d.removed_builtin || []; };
    const refresh = async () => {
      load(await api('/api/skills'));
      for (const n of [...toolstore.prefs.pinnedSkills]) if (!skills.some((s) => s.name === n)) toolstore.setPinned(n, false); // forget pins of removed skills
      await toolstore.refresh().catch(() => {});
      render();
    };

    // ---- actions
    const install = guard(async (repo, path = '', overwrite = false) => {
      toast(`Fetching ${repo}…`);
      try {
        const res = await api('/api/skills/install', 'POST', { repo, path, overwrite });
        load(res);
        toast(`Installed ${res.installed.length} skill${res.installed.length === 1 ? '' : 's'}: ${res.installed.join(', ')}`, 'info', 7000);
      } catch (e) {
        if (/Already installed/.test(e.message) && confirm(`${e.message}\n\nReplace the installed copies with the latest version?`)) {
          const res = await api('/api/skills/install', 'POST', { repo, path, overwrite: true });
          load(res);
          toast(`Updated ${res.installed.join(', ')}`);
        } else throw e;
      }
      await refresh();
    });

    const tryIt = guard(async (sk) => {
      if (!sk.enabled) await api(`/api/skills/${encodeURIComponent(sk.name)}/enabled`, 'POST', { enabled: true });
      try { sessionStorage.setItem('lfm3.prefill', JSON.stringify({ model: 'text', text: sk.example, groups: sk.tools })); } catch { /* private mode */ }
      location.hash = '#/chat';
    });

    function installDialog() {
      const repo = h('input', { class: 'input', placeholder: 'owner/repository' });
      const path = h('input', { class: 'input', placeholder: 'Sub-folder (optional)' });
      const go = () => { if (repo.value.trim()) { m.close(); install(repo.value, path.value); } };
      const m = modal('Install skills from GitHub', h('div', { class: 'stack' },
        h('p', { class: 'muted', style: { margin: 0 } }, 'Downloads every SKILL.md folder in a public repository. Only text files are fetched, and nothing in a skill is ever executed.'),
        h('div', { class: 'field' }, h('label', {}, 'Repository'), repo),
        h('div', { class: 'field' }, h('label', {}, 'Folder'), path),
        h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
          h('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'),
          h('button', { class: 'btn primary', onclick: go }, icon('download'), 'Install'))));
      repo.focus();
      repo.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    }

    function createDialog() {
      const name = h('input', { class: 'input', placeholder: 'my-skill-name' });
      const desc = h('textarea', { class: 'input', rows: 3, placeholder: 'What it does and WHEN the model should use it. The model reads only this line to decide.' });
      const text = h('textarea', { class: 'input', rows: 10, placeholder: '# Instructions\n\nStep-by-step guidance for the model…' });
      const words = h('input', { class: 'input', placeholder: 'invoice, receipt, billing' });
      const m = modal('Create a skill', h('div', { class: 'stack' },
        h('div', { class: 'field' }, h('label', {}, 'Name'), name, h('small', { class: 'muted' }, 'Lowercase letters, digits and hyphens.')),
        h('div', { class: 'field' }, h('label', {}, 'Description'), desc),
        h('div', { class: 'field' }, h('label', {}, 'Instructions (Markdown)'), text),
        h('div', { class: 'field' }, h('label', {}, 'Trigger words (optional)'), words,
          h('small', { class: 'muted' }, 'Comma-separated. When one appears in your message, this skill is loaded for the model automatically.')),
        h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
          h('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'),
          h('button', { class: 'btn primary', onclick: guard(async () => {
            load(await api('/api/skills', 'POST', { name: name.value, description: desc.value, body: text.value, triggers: words.value.split(',').map((w) => w.trim()).filter(Boolean) }));
            m.close(); toast('Skill created'); await refresh();
          }) }, icon('check'), 'Save skill'))), { wide: true });
      name.focus();
    }

    function viewDialog(name) {
      const body = h('div', { class: 'skill-view' }, h('span', { class: 'spinner' }));
      modal(name, body, { wide: true });
      api(`/api/skills/${encodeURIComponent(name)}`).then((sk) => {
        const fileView = h('pre', { class: 'codeinline', style: { display: 'none', maxHeight: '300px', overflow: 'auto', whiteSpace: 'pre-wrap' } });
        const md = h('div', { class: 'bubble skill-md', html: markdown(sk.body) });
        enableCodeCopy(md);
        body.replaceChildren(...[
          h('p', { class: 'muted' }, sk.description),
          h('div', { class: 'row wrap', style: { margin: '10px 0 14px' } },
            h('span', { class: 'chip' }, kindLabel[kindOf(sk)]), sk.license && h('span', { class: 'chip' }, sk.license), h('span', { class: 'chip' }, `~${sk.tokens.toLocaleString()} tokens`)),
          sk.triggers.length > 0 && h('div', { class: 'row wrap', style: { marginBottom: '12px' } }, h('span', { class: 'label' }, 'Loads automatically when you mention'),
            sk.triggers.map((t) => h('span', { class: 'chip accent' }, t))),
          sk.files.length > 0 && h('div', { class: 'row wrap', style: { marginBottom: '12px' } }, h('span', { class: 'label' }, 'Reference files'),
            sk.files.map((f) => h('button', { class: 'btn sm', onclick: async () => {
              try { const r = await api(`/api/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(f)}`); fileView.textContent = r.text; fileView.style.display = ''; fileView.scrollIntoView({ block: 'nearest' }); }
              catch (e) { toast(e.message, 'error'); }
            } }, f))),
          fileView, md].filter(Boolean));
      }).catch((e) => { body.textContent = e.message; });
    }

    // ---- rendering
    function card(sk) {
      const kind = kindOf(sk);
      return h('div', { class: `card skill-card${sk.enabled ? '' : ' off'}` },
        h('div', { class: 'skill-top' },
          h('div', { class: 'skill-ic' }, icon(sk.icon || 'book', 22)),
          h('div', { class: 'grow' }, h('div', { class: 'skill-title' }, sk.title), h('div', { class: 'skill-id' }, sk.name)),
          h('label', { class: 'sw-wrap', title: sk.enabled ? 'On: the model can load this skill' : 'Off: hidden from the model' },
            h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: sk.enabled, onchange: guard(async (e) => {
              load(await api(`/api/skills/${encodeURIComponent(sk.name)}/enabled`, 'POST', { enabled: e.target.checked }));
              render(); toolstore.refresh().catch(() => {});
            }) }), h('i')))),
        h('p', { class: 'skill-desc' }, sk.description),
        h('div', { class: 'row wrap' },
          h('span', { class: `chip${kind === 'builtin' ? ' accent' : ''}` }, icon(kind === 'github' ? 'github' : kind === 'custom' ? 'pencil' : 'sparkles', 12), kindLabel[kind]),
          h('span', { class: 'chip' }, `~${sk.tokens.toLocaleString()} tokens`),
          sk.files.length > 0 && h('span', { class: 'chip' }, `${sk.files.length} file${sk.files.length === 1 ? '' : 's'}`)),
        h('div', { class: 'skill-foot' },
          sk.example && h('button', { class: 'btn sm primary', title: sk.example, onclick: () => tryIt(sk) }, icon('play', 13), 'Try it'),
          h('button', { class: 'btn sm', onclick: () => viewDialog(sk.name) }, 'View'),
          h('div', { class: 'grow' }),
          h('label', { class: 'pill-toggle', title: `Put this skill's full text into every chat (uses ~${sk.tokens.toLocaleString()} tokens per message)` },
            h('input', { type: 'checkbox', checked: toolstore.isPinned(sk.name), disabled: !sk.enabled, onchange: (e) => { toolstore.setPinned(sk.name, e.target.checked); render(); } }), icon('zap', 14), 'Always on'),
          h('button', { class: 'icon-btn', title: 'Remove this skill', onclick: guard(async () => {
            const extra = sk.builtin ? ' You can bring it back with “Restore built-ins”.' : ' Its files are deleted from the skills folder.';
            if (!confirm(`Remove “${sk.title}”?${extra}`)) return;
            load(await api(`/api/skills/${encodeURIComponent(sk.name)}`, 'DELETE'));
            toolstore.setPinned(sk.name, false); toast('Skill removed'); render(); toolstore.refresh().catch(() => {});
          }) }, icon('trash', 16))));
    }

    function render() {
      const q = query.trim().toLowerCase();
      const shown = skills.filter((s) => (filter === 'all' || kindOf(s) === filter)
        && (!q || `${s.title} ${s.name} ${s.description}`.toLowerCase().includes(q)));
      const on = skills.filter((s) => s.enabled).length;
      const pinned = skills.filter((s) => toolstore.isPinned(s.name) && s.enabled).reduce((n, s) => n + s.tokens, 0);
      actions.replaceChildren(...[
        removed.length > 0 && h('button', { class: 'btn sm ghost', onclick: guard(async () => { load(await api('/api/skills/restore-defaults', 'POST')); toast('Built-in skills restored'); await refresh(); }) }, icon('refresh', 14), `Restore built-ins (${removed.length})`),
        h('button', { class: 'btn', onclick: installDialog }, icon('github'), 'Install from GitHub'),
        h('button', { class: 'btn primary', onclick: createDialog }, icon('plus'), 'Create skill')].filter(Boolean));
      grid.replaceChildren(...(shown.length ? shown.map(card) : [h('div', { class: 'card card-pad muted skill-empty' },
        skills.length ? 'No skills match your search.' : 'No skills yet. Restore the built-ins, install a repository, or create your own.')]));
      status.replaceChildren(...[h('span', { class: 'chip good' }, `${on} of ${skills.length} on`),
        pinned > 0 && h('span', { class: 'chip warn' }, `Always-on skills add ~${pinned.toLocaleString()} tokens to every message`)].filter(Boolean));
    }

    const status = h('div', { class: 'row wrap' });
    const seg = h('div', { class: 'seg' }, FILTERS.map(([id, label]) => h('button', { class: id === filter ? 'active' : '', 'data-id': id, onclick: () => {
      filter = id; seg.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.id === id)); render();
    } }, label)));
    const search = h('input', { class: 'input', placeholder: 'Search skills', 'aria-label': 'Search skills', style: { maxWidth: '260px' } });
    search.addEventListener('input', () => { query = search.value; render(); });

    view.append(
      h('div', { class: 'skills-intro' },
        h('p', { class: 'muted grow' }, 'A skill is a short playbook the model reads before a job, so PDFs, decks and spreadsheets come out structured and consistent. Turn on ',
          h('b', {}, 'Skills'), ' under Tools in Chat and the model picks the right one itself, or press ', h('b', {}, 'Try it'), ' to see one work.'),
        actions),
      h('div', { class: 'skills-bar' }, seg, search, h('div', { class: 'grow' }), status),
      grid);
    refresh().catch((e) => toast(e.message, 'error'));
    return toolstore.subscribe(() => { if (skills.length) render(); });
  },
};
