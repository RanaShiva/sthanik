import { h, icon, toast, modal, markdown, timeAgo } from '../ui.js';
import { enableCodeCopy } from '../markdown.js';
import { toolstore } from '../toolstore.js';
import { fileCard } from '../chatview.js';

const MODELS = [['text', 'Chat · LFM3-1.7B'], ['vl', 'Vision · LFM3-VL-2B'], ['omni', 'Omni · LFM3-Omni-2B']];
const OUTPUTS = [['none', 'Just keep the answer'], ['pdf', 'PDF document'], ['docx', 'Word (.docx)'], ['pptx', 'Slides (.pptx)'], ['html', 'HTML page'], ['md', 'Markdown file']];
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const STATUS = { ok: ['good', 'Done'], error: ['bad', 'Failed'], running: ['accent', 'Running'], queued: ['warn', 'Waiting for a quiet moment'], cancelled: ['', 'Cancelled'] };

async function api(url, method = 'GET', body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

const fmtWhen = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  const mins = Math.round((d - Date.now()) / 60000);
  const rel = mins < 1 ? 'any moment' : mins < 60 ? `in ${mins} min` : mins < 1440 ? `in ${Math.round(mins / 60)} h` : `in ${Math.round(mins / 1440)} d`;
  return `${d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })} (${rel})`;
};

export default {
  title: 'Schedules',
  subtitle: 'Run prompts on a timer, with tools and skills, and keep the results',
  mount(view) {
    let snap = { tasks: [], running: [], last_finished: 0 };
    let lastJson = '';
    const open = new Set(); // task ids whose history is expanded
    const histories = {};
    const list = h('div', { class: 'stack' });
    let alive = true;

    const markSeen = () => { try { localStorage.setItem('lfm3.runs.seen', String(snap.last_finished || 0)); } catch { /* private mode */ } };

    async function refresh(force = false) {
      try {
        const d = await api('/api/schedules');
        const hs = {};
        for (const id of open) hs[id] = (await api(`/api/runs?task=${id}`)).runs;
        const j = JSON.stringify([d, hs]);
        if (!force && j === lastJson) return; // nothing changed: leave the DOM alone (rebuilding it caused flicker)
        lastJson = j; snap = d; Object.assign(histories, hs); markSeen();
        if (alive) render();
      } catch { /* backend restarting */ }
    }

    function runBlock(r) {
      const [kind, label] = STATUS[r.status] || ['', r.status];
      const out = h('div', { class: 'bubble run-out' });
      out.innerHTML = markdown(r.output || '');
      enableCodeCopy(out);
      return h('div', { class: 'run' },
        h('div', { class: 'run-head' },
          h('span', { class: `chip ${kind}` }, r.status === 'running' ? h('span', { class: 'spinner', style: { width: '10px', height: '10px' } }) : h('span', { class: 'dot' }), label),
          h('span', { class: 'muted' }, `${new Date(r.started * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })} · ${r.trigger === 'manual' ? 'run manually' : 'scheduled'}${r.duration ? ` · ${r.duration} s` : ''}`),
          h('div', { class: 'grow' }),
          (r.status === 'running' || r.status === 'queued') && h('button', { class: 'btn sm', onclick: async () => { await api(`/api/runs/${r.id}/cancel`, 'POST'); refresh(true); } }, 'Cancel'),
          !['running', 'queued'].includes(r.status) && h('button', { class: 'icon-btn', title: 'Delete this result', onclick: async () => { await api(`/api/runs/${r.id}`, 'DELETE'); refresh(true); } }, icon('trash', 15))),
        r.error && h('div', { class: 'error-text', style: { fontSize: '13px' } }, r.error),
        r.tool_calls?.length > 0 && h('div', { class: 'row wrap' }, r.tool_calls.map((c) => h('span', { class: `chip ${c.ok ? '' : 'warn'}`, title: c.summary || '' }, icon(c.ok ? 'wrench' : 'alert', 12), c.name))),
        r.files?.length > 0 && h('div', { class: 'files' }, r.files.map(fileCard)),
        r.output && h('details', { class: 'think', open: r.trigger === 'manual' && r === (histories[r.task_id] || [])[0] }, h('summary', {}, 'Result'), out));
    }

    function taskCard(t) {
      const running = snap.running.includes(t.id);
      const [lk, ll] = STATUS[t.last_status] || ['', ''];
      const hist = h('div', { class: 'runs' });
      if (open.has(t.id)) {
        const runs = histories[t.id] || [];
        hist.replaceChildren(...(runs.length ? runs.map(runBlock) : [h('div', { class: 'muted', style: { padding: '12px 18px' } }, 'No runs yet.')]));
      }
      return h('div', { class: 'card task' },
        h('div', { class: 'tg-head' },
          h('div', { class: 'tg-icon' }, icon('calendar', 20)),
          h('div', { class: 'grow' },
            h('div', { class: 'tg-title' }, t.name, h('span', { class: 'chip' }, icon('clock', 12), t.summary),
              running && h('span', { class: 'chip accent' }, h('span', { class: 'dot pulse' }), 'Running')),
            h('div', { class: 'tg-desc' }, t.enabled ? `Next: ${fmtWhen(t.next_run)}` : 'Paused',
              t.last_run ? ` · last run ${timeAgo(t.last_run)}` : '', ' ', ll && h('span', { class: `chip ${lk}` }, ll))),
          h('div', { class: 'tg-controls' },
            h('label', { class: 'sw-wrap', title: t.enabled ? 'Pause this task' : 'Resume this task' }, h('span', { class: 'sw-label' }, 'Active'),
              h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: t.enabled, onchange: async (e) => {
                try { await api(`/api/schedules/${t.id}/enabled`, 'POST', { enabled: e.target.checked }); } catch (err) { toast(err.message, 'error'); }
                refresh(true);
              } }), h('i'))))),
        h('div', { class: 'task-body' },
          h('p', { class: 'task-prompt' }, t.prompt.length > 220 ? `${t.prompt.slice(0, 220)}…` : t.prompt),
          h('div', { class: 'row wrap' },
            h('span', { class: 'chip' }, MODELS.find((m) => m[0] === t.model)?.[1] || t.model),
            t.output_format !== 'none' && h('span', { class: 'chip accent' }, icon('filetext', 12), OUTPUTS.find((o) => o[0] === t.output_format)?.[1]),
            t.tools.map((g) => h('span', { class: 'chip' }, icon('wrench', 12), toolstore.group(g)?.label || g, t.allow_unattended.includes(g) ? ' (unattended)' : '')),
            t.skills && h('span', { class: 'chip' }, icon('book', 12), 'skills'))),
        h('div', { class: 'task-foot' },
          h('button', { class: 'btn sm primary', disabled: running, onclick: async () => {
            try { await api(`/api/schedules/${t.id}/run`, 'POST'); open.add(t.id); toast('Started. The result appears below.'); } catch (e) { toast(e.message, 'error'); }
            refresh(true);
          } }, icon('play', 14), 'Run now'),
          h('button', { class: 'btn sm', onclick: () => { if (open.has(t.id)) open.delete(t.id); else open.add(t.id); refresh(true); } },
            icon(open.has(t.id) ? 'chevronDown' : 'chevronRight', 14), open.has(t.id) ? 'Hide results' : 'Results'),
          h('div', { class: 'grow' }),
          h('button', { class: 'btn sm', onclick: () => editor(t) }, icon('pencil', 14), 'Edit'),
          h('button', { class: 'btn sm danger', onclick: async () => {
            if (!confirm(`Delete “${t.name}” and its results?`)) return;
            try { await api(`/api/schedules/${t.id}`, 'DELETE'); } catch (e) { toast(e.message, 'error'); }
            open.delete(t.id); refresh(true);
          } }, icon('trash', 14))),
        hist);
    }

    function render() {
      list.replaceChildren(...(snap.tasks.length ? snap.tasks.map(taskCard) : [h('div', { class: 'card card-pad empty-card' },
        h('div', { class: 'empty-orb', style: { width: '56px', height: '56px', borderRadius: '18px' } }, icon('calendar', 24)),
        h('h3', {}, 'Nothing scheduled yet'),
        h('p', { class: 'muted' }, 'Create a task like “every weekday at 8:00, search the news about open-source AI and make me a PDF briefing”.'),
        h('button', { class: 'btn primary', onclick: () => editor() }, icon('plus'), 'Create your first task'))]));
    }

    // ------------------------------------------------------------ editor
    function editor(existing) {
      const d = existing ? JSON.parse(JSON.stringify(existing)) : {
        name: '', prompt: '', model: 'text', schedule: { type: 'daily', time: '08:00' }, tools: [], allow_unattended: [],
        skills: false, output_format: 'none', thinking: false, enabled: true, max_tokens: 2048,
      };
      const sch = d.schedule;
      const name = h('input', { class: 'input', value: d.name, placeholder: 'Morning briefing' });
      const prompt = h('textarea', { class: 'input', rows: 5, placeholder: 'What should the model do each time? Be specific; nobody can answer questions while it runs.' });
      prompt.value = d.prompt;
      const model = h('select', { class: 'input' }, MODELS.map(([v, l]) => h('option', { value: v }, l)));
      model.value = d.model;
      const output = h('select', { class: 'input' }, OUTPUTS.map(([v, l]) => h('option', { value: v }, l)));
      output.value = d.output_format;
      const length = h('select', { class: 'input' }, [[1024, 'Short (1K tokens)'], [2048, 'Normal (2K)'], [4096, 'Long (4K)'], [8192, 'Very long (8K)']].map(([v, l]) => h('option', { value: v }, l)));
      length.value = String(d.max_tokens);

      // schedule builder
      const kind = h('select', { class: 'input' }, [['interval', 'Every N minutes / hours'], ['daily', 'Every day'], ['weekly', 'Certain weekdays'], ['cron', 'Cron expression'], ['once', 'Once']].map(([v, l]) => h('option', { value: v }, l)));
      kind.value = sch.type;
      const spec = h('div', { class: 'row wrap' });
      const previewBox = h('div', { class: 'preview' });
      let timer;
      const refreshPreview = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          try {
            const r = await api('/api/schedules/preview', 'POST', { schedule: d.schedule });
            previewBox.replaceChildren(r.error ? h('span', { class: 'error-text' }, r.error)
              : h('div', {}, h('span', { class: 'label' }, 'Next runs'), h('div', { class: 'row wrap', style: { marginTop: '6px' } },
                r.next.map((iso) => h('span', { class: 'chip' }, new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }))))));
          } catch (e) { previewBox.textContent = e.message; }
        }, 250);
      };
      const buildSpec = () => {
        const s = d.schedule;
        const set = (k, v) => { s[k] = v; refreshPreview(); };
        spec.replaceChildren();
        if (s.type === 'interval') {
          const n = h('input', { class: 'input', type: 'number', min: 1, value: s.every ?? 60, style: { width: '100px' }, oninput: (e) => set('every', +e.target.value) });
          const u = h('select', { class: 'input', style: { width: '130px' }, onchange: (e) => set('unit', e.target.value) }, ['minutes', 'hours'].map((x) => h('option', { value: x }, x)));
          u.value = s.unit || 'minutes';
          s.every ??= 60; s.unit ??= 'minutes';
          spec.append(h('span', {}, 'Every'), n, u, h('span', { class: 'muted', style: { fontSize: '12px' } }, 'minimum 5 minutes'));
        } else if (s.type === 'daily' || s.type === 'weekly') {
          s.time ??= '08:00';
          if (s.type === 'weekly') {
            s.days ??= [0, 1, 2, 3, 4];
            spec.append(h('div', { class: 'seg' }, DAYS.map((dn, i) => h('button', { type: 'button', class: s.days.includes(i) ? 'active' : '', onclick: (e) => {
              s.days = s.days.includes(i) ? s.days.filter((x) => x !== i) : [...s.days, i];
              e.currentTarget.classList.toggle('active', s.days.includes(i)); refreshPreview();
            } }, dn))));
          }
          spec.append(h('span', {}, 'at'), h('input', { class: 'input', type: 'time', value: s.time, style: { width: '130px' }, oninput: (e) => set('time', e.target.value) }));
        } else if (s.type === 'cron') {
          s.expr ??= '0 8 * * 1-5';
          spec.append(h('input', { class: 'input mono', value: s.expr, style: { maxWidth: '260px' }, oninput: (e) => set('expr', e.target.value) }),
            h('span', { class: 'muted', style: { fontSize: '12px' } }, 'minute hour day-of-month month day-of-week'));
        } else {
          const soon = new Date(Date.now() + 3600e3);
          soon.setMinutes(0, 0, 0);
          s.at ??= new Date(soon - soon.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
          spec.append(h('input', { class: 'input', type: 'datetime-local', value: s.at, oninput: (e) => set('at', e.target.value) }));
        }
        refreshPreview();
      };
      kind.addEventListener('change', () => { d.schedule = { type: kind.value }; buildSpec(); });
      buildSpec();

      // tools
      const toolBox = h('div', { class: 'stack', style: { gap: '8px' } });
      const renderTools = () => {
        toolBox.replaceChildren(...toolstore.groups.filter((g) => g.id !== 'skills' && toolstore.usable(g)).map((g) => {
          const on = d.tools.includes(g.id);
          const ask = g.default_approval === 'ask';
          return h('div', { class: 'tool-pick' },
            h('label', { class: 'row' }, h('input', { type: 'checkbox', checked: on, onchange: (e) => {
              d.tools = e.target.checked ? [...d.tools, g.id] : d.tools.filter((x) => x !== g.id);
              if (!e.target.checked) d.allow_unattended = d.allow_unattended.filter((x) => x !== g.id);
              renderTools();
            } }), h('span', { class: 'tg-icon', style: { width: '28px', height: '28px', borderRadius: '9px' } }, icon(g.icon || 'wrench', 14)),
            h('span', { class: 'grow' }, h('b', {}, g.label), h('small', { class: 'muted', style: { display: 'block' } }, g.description))),
            on && ask && h('label', { class: 'row warn-line' }, h('input', { type: 'checkbox', checked: d.allow_unattended.includes(g.id), onchange: (e) => {
              d.allow_unattended = e.target.checked ? [...d.allow_unattended, g.id] : d.allow_unattended.filter((x) => x !== g.id);
            } }), h('span', {}, `Run without asking. Nobody is watching, so ${g.label} tools would otherwise be skipped. Only allow this for sources you trust.`)));
        }));
      };
      renderTools();

      const skillsCb = h('input', { type: 'checkbox', checked: d.skills, onchange: (e) => { d.skills = e.target.checked; } });
      const thinkCb = h('input', { type: 'checkbox', checked: d.thinking, onchange: (e) => { d.thinking = e.target.checked; } });
      const save = h('button', { class: 'btn primary' }, icon('check'), existing ? 'Save changes' : 'Create task');
      const m = modal(existing ? 'Edit task' : 'New scheduled task', h('div', { class: 'stack' },
        h('div', { class: 'field' }, h('label', {}, 'Name'), name),
        h('div', { class: 'field' }, h('label', {}, 'Prompt'), prompt),
        h('div', { class: 'grid-2' }, h('div', { class: 'field' }, h('label', {}, 'Model'), model), h('div', { class: 'field' }, h('label', {}, 'Save the result as'), output)),
        h('div', { class: 'field' }, h('label', {}, 'When'), kind, spec, previewBox),
        h('div', { class: 'field' }, h('label', {}, 'Tools it may use'), toolBox),
        h('div', { class: 'row wrap' },
          h('label', { class: 'row' }, skillsCb, h('span', {}, 'Let it use skills')),
          h('label', { class: 'row' }, thinkCb, h('span', {}, 'Think before answering (slower)')),
          h('div', { class: 'grow' }), h('div', { class: 'field', style: { width: '190px' } }, length)),
        h('p', { class: 'muted', style: { fontSize: '12.5px' } },
          'Runs only while this app is open, one at a time, and waits until the model has been idle for about a minute so it won’t interrupt your chat. Missed runs are skipped.'),
        h('div', { class: 'row', style: { justifyContent: 'flex-end' } }, h('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'), save)), { wide: true });
      name.focus();
      save.addEventListener('click', async () => {
        save.disabled = true;
        const body = { ...d, name: name.value, prompt: prompt.value, model: model.value, output_format: output.value, max_tokens: +length.value };
        try {
          await api(existing ? `/api/schedules/${existing.id}` : '/api/schedules', existing ? 'PUT' : 'POST', body);
          m.close(); toast(existing ? 'Task updated' : 'Task created'); refresh(true);
        } catch (e) { toast(e.message, 'error', 7000); save.disabled = false; }
      });
    }

    view.append(
      h('div', { class: 'row', style: { marginBottom: '14px' } },
        h('div', { class: 'grow muted' }, 'Tasks run locally on your GPU, in the background, and show up here when they finish.'),
        h('button', { class: 'btn primary', onclick: () => editor() }, icon('plus'), 'New task')),
      list);
    refresh(true);
    const poll = setInterval(refresh, 3000);
    return () => { alive = false; clearInterval(poll); };
  },
};
