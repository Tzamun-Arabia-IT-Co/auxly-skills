(() => {
  const byId = (id) => document.getElementById(id);
  const token = new URLSearchParams(location.search).get('token') || '';
  const logo = byId('brandLogo');
  if (logo && window.AUXLY_LOGO) logo.src = window.AUXLY_LOGO;

  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const KIND_ICON = { codex: '◆', claude: '✦', gemini: '✧', agy: '▲', opencode: '❮❯', custom: '⚙' };
  const SICON = { pending: '', running: '●', done: '✓', failed: '✕', blocked: '!' };
  const CICON = { pending: '', running: '●', pass: '✓', fail: '✕', skip: '–' };

  // ---- tiny markdown renderer (escaped first) ----
  const inline = (t) => esc(t).replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const md = (src) => {
    if (!src || !src.trim()) return '<span class="placeholder">—</span>';
    const out = []; let list = null, fence = false, buf = [];
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    src.replace(/\r\n/g, '\n').split('\n').forEach((line) => {
      if (/^\s*```/.test(line)) { if (fence) { out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`); buf = []; fence = false; } else { close(); fence = true; } return; }
      if (fence) { buf.push(line); return; }
      if (!line.trim()) { close(); return; }
      let m = line.match(/^(#{1,6})\s+(.*)$/); if (m) { close(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); return; }
      if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { close(); out.push('<hr>'); return; }
      let ol = line.match(/^\s*\d+[.)]\s+(.*)$/), ul = line.match(/^\s*[-*+]\s+(.*)$/);
      if (ol) { if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(ol[1])}</li>`); return; }
      if (ul) { if (list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inline(ul[1])}</li>`); return; }
      close(); out.push(`<p>${inline(line)}</p>`);
    });
    if (fence && buf.length) out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
    close(); return out.join('\n');
  };

  const post = (route, payload) => fetch(route, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Auxly-Token': token }, body: JSON.stringify(payload || {}) }).catch(() => {});

  let state = null, selected = null, userPicked = false;

  const setConn = (ok, label) => { const c = byId('conn'); c.dataset.ok = ok ? '1' : '0'; byId('connText').textContent = label; };

  const renderMeter = (s) => {
    const m = s.meter || {}; const host = byId('meter');
    if (!m.tokens_in && !m.tokens_out && !(m.agents && Object.keys(m.agents).length)) { host.innerHTML = ''; return; }
    const k = (n) => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
    let h = `<span class="chip">⛽ tokens in <b>${k(m.tokens_in || 0)}</b> · out <b>${k(m.tokens_out || 0)}</b></span>`;
    if (m.priced && m.cost) h += `<span class="chip cost">≈ $${(m.cost).toFixed(3)}</span>`;
    const n = m.agents ? Object.keys(m.agents).length : 0;
    if (n) h += `<span class="chip">${n} model${n === 1 ? '' : 's'}</span>`;
    host.innerHTML = h;
  };

  const stageList = (s) => (s.stage_order || []).map((n) => s.stages[n]).filter(Boolean);

  const renderTabs = (s) => {
    const host = byId('tabs'); host.innerHTML = '';
    stageList(s).forEach((st) => {
      const el = document.createElement('div');
      el.className = 'tab' + (st.name === selected ? ' sel' : '');
      el.dataset.status = st.status || 'pending';
      el.innerHTML = `<span class="tdot"></span>${esc(st.title || st.name)}`;
      el.addEventListener('click', () => { selected = st.name; userPicked = true; render(state); });
      host.appendChild(el);
    });
  };

  const INTENTS = {
    plan: [['Execute ▶', 'start_execute']],
    execute: [['Run checks ▶', 'start_verify'], ['Review ▶', 'start_review']],
    verify: [['Review ▶', 'start_review']],
    review: [['Re-run review ▶', 'start_review']],
  };
  const renderActions = (s) => {
    const host = byId('actions'); host.innerHTML = '';
    const st = s.stages[selected]; if (!st) return;
    (INTENTS[st.kind] || []).forEach(([label, name]) => {
      const b = document.createElement('button'); b.className = 'btn primary'; b.textContent = label;
      b.addEventListener('click', () => { post('/api/intent', { kind: 'intent', name }); b.textContent = 'queued ✓'; });
      host.appendChild(b);
    });
  };

  const renderNotifs = (s) => {
    const host = byId('notifs'); host.innerHTML = '';
    (s.blockers || []).forEach((b) => {
      const open = b.status !== 'resolved';
      const el = document.createElement('div'); el.className = 'notif blocker' + (open ? '' : ' resolved');
      let h = `<div class="nh"><span class="ntag">🔴 Blocker · action needed</span>${b.slice ? `<span class="nslice">slice ${esc(b.slice)}</span>` : ''}</div><div class="nsubj">${esc(b.subject)}</div>`;
      if (b.detail) h += `<div class="ndet">${esc(b.detail)}</div>`;
      if (open) h += `<div class="rrow"><textarea data-b="${esc(b.id)}" placeholder="Your answer / resolution so work can resume…"></textarea><button class="btn resolve" data-b="${esc(b.id)}">Resolve &amp; resume</button></div>`;
      else h += `<div class="rnote">✓ Resolved${b.resolution ? ': ' + esc(b.resolution) : ''}</div>`;
      el.innerHTML = h; host.appendChild(el);
    });
    (s.warnings || []).filter((w) => w.status !== 'dismissed').forEach((w) => {
      const el = document.createElement('div'); el.className = 'notif warning';
      let h = `<div class="nh"><span class="ntag">🟡 Warning · awareness</span>${w.slice ? `<span class="nslice">slice ${esc(w.slice)}</span>` : ''}<button class="btn dismiss" data-w="${esc(w.id)}" style="margin-left:auto">Dismiss</button></div><div class="nsubj">${esc(w.subject)}</div>`;
      if (w.detail) h += `<div class="ndet">${esc(w.detail)}</div>`;
      el.innerHTML = h; host.appendChild(el);
    });
    host.querySelectorAll('button.resolve').forEach((btn) => btn.addEventListener('click', () => {
      const ta = host.querySelector(`textarea[data-b="${CSS.escape(btn.dataset.b)}"]`);
      post('/api/intent', { kind: 'blocker', id: btn.dataset.b, resolution: ta ? ta.value : '' });
    }));
    host.querySelectorAll('button.dismiss').forEach((btn) => btn.addEventListener('click', () =>
      post('/api/intent', { kind: 'warning', id: btn.dataset.w })));
  };

  const renderAgents = (s) => {
    const panel = byId('agentsPanel'), host = byId('agents'); const list = s.agents || [];
    if (!list.length) { panel.classList.add('hidden'); host.innerHTML = ''; return; }
    panel.classList.remove('hidden');
    byId('agentsSub').textContent = `${list.length} agent${list.length === 1 ? '' : 's'} · ${list.filter((a) => a.status === 'active').length} active`;
    host.innerHTML = list.map((a) => `<div class="agent" data-status="${esc(a.status || 'idle')}">
      <span class="adot"></span>
      <div class="amain"><span class="aname">${esc(KIND_ICON[a.kind] || '•')} ${esc(a.name)}</span>
        ${a.model ? `<span class="amodel">${esc(a.model)}</span>` : ''}${a.current ? `<span class="acur">▸ ${esc(a.current)}</span>` : ''}</div>
      <div class="ameta"><span class="arole">${esc(a.role || 'executor')}</span><span class="atag ${esc(a.status || 'idle')}">${esc(a.status || 'idle')}</span></div></div>`).join('');
  };

  const checksHtml = (checks) => `<div class="checks">${checks.map((c) => `<div class="check" data-s="${esc(c.status)}"><span class="cic">${CICON[c.status] || ''}</span><span class="cname">${esc(c.name)}</span>${c.output ? `<span class="cout">${esc(c.output)}</span>` : ''}</div>`).join('')}</div>`;
  const execBody = (st) => {
    const d = st.data || {}; const phases = d.phases || []; const pr = d.progress || { done: 0, total: 0, pct: 0 };
    let h = `<div class="prow"><span>Progress</span><span><b>${pr.pct}%</b> · ${pr.done}/${pr.total} slices</span></div><div class="bar"><span style="width:${pr.pct}%"></span></div>`;
    h += phases.map((p) => `<div class="phase"><div class="phead"><span class="pid">${esc(p.id)}</span><span class="pname">${esc(p.name)}</span><span class="pstat" data-s="${esc(p.status)}">${esc(p.status)}</span></div>
      <div class="slices">${(p.slices || []).map((sl) => `<div class="slice" data-s="${esc(sl.status)}"><span class="sic">${SICON[sl.status] || ''}</span><span class="sid">${esc(sl.id)}</span><span class="sname">${esc(sl.name)}</span>${sl.note ? `<span class="snote">${esc(sl.note)}</span>` : ''}</div>`).join('')}</div></div>`).join('');
    if ((d.checks || []).length) h += `<div style="margin-top:.6rem" class="panel-sub">Checks</div>` + checksHtml(d.checks);
    return h || '<span class="placeholder">No phases yet.</span>';
  };
  const findingsBody = (st) => {
    const f = (st.data || {}).findings || [];
    if (!f.length) return (st.data || {}).markdown ? md(st.data.markdown) : '<span class="placeholder">No findings.</span>';
    return f.map((x) => `<div class="finding"><div class="fhead"><span class="sev ${esc(x.severity)}">${esc(x.severity)}</span><span class="ftitle">${esc(x.title)}</span>${x.verdict ? `<span class="verdict ${esc(x.verdict)}">${esc(x.verdict)}</span>` : ''}</div>${x.file ? `<div class="floc">${esc(x.file)}${x.line ? ':' + esc(x.line) : ''}</div>` : ''}${x.detail ? `<div class="fdet">${esc(x.detail)}</div>` : ''}</div>`).join('');
  };
  const boardBody = (st) => {
    const runs = (st.data || {}).runs || [];
    if (!runs.length) return '<span class="placeholder">No runs found.</span>';
    return `<div class="board">${runs.map((r) => `<div class="run-card"><span class="rk">${esc(r.kind)} · ${esc(r.status || '')}</span><span class="rt">${esc(r.title || r.id)}</span><span class="rm">${esc(r.id)}</span><span class="rm">${esc((r.stages || []).join(' · '))}</span></div>`).join('')}</div>`;
  };

  const renderStage = (s) => {
    const st = s.stages[selected];
    if (!st) { byId('stageTitle').textContent = 'Stage'; byId('stageBody').innerHTML = '<span class="placeholder">No stage yet.</span>'; byId('stageSub').textContent = ''; return; }
    byId('stageTitle').textContent = st.title || st.name;
    byId('stageSub').textContent = st.status || '';
    const body = byId('stageBody');
    if (st.kind === 'execute') body.innerHTML = execBody(st);
    else if (st.kind === 'verify') body.innerHTML = checksHtml((st.data || {}).checks || []);
    else if (st.kind === 'review') body.innerHTML = findingsBody(st);
    else if (st.kind === 'board') body.innerHTML = boardBody(st);
    else body.innerHTML = md((st.data || {}).markdown || '');
  };

  const renderLog = (s) => {
    const host = byId('log'); host.innerHTML = (s.log || []).slice(-80).map((e) =>
      `<div><span class="lt">${esc(e.ts ? new Date(e.ts).toLocaleTimeString() : '')}</span> ${esc(e.msg)}</div>`).join('');
    host.scrollTop = host.scrollHeight;
  };

  const render = (s) => {
    state = s;
    byId('runTitle').textContent = s.title || 'Auxly';
    byId('runId').textContent = s.run_id || '—';
    byId('runBadge').dataset.status = s.run_status || 'running';
    byId('runStatusText').textContent = s.run_status || 'running';
    if (!userPicked || !s.stages[selected]) selected = s.active_stage || (s.stage_order || [])[0] || null;
    renderMeter(s); renderTabs(s); renderActions(s); renderNotifs(s); renderAgents(s); renderStage(s); renderLog(s);
  };

  const pollState = () => fetch('/api/state', { cache: 'no-store' })
    .then((r) => r.json()).then(render).catch(() => {});

  let src = null, pollTimer = null, reconnectTimer = null;
  const startPolling = () => { if (!pollTimer) pollTimer = setInterval(pollState, 3000); };
  const stopPolling = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };

  const connect = () => {
    if (!window.EventSource) { setConn(false, 'polling'); startPolling(); pollState(); return; }
    try { if (src) src.close(); } catch (x) {}
    src = new EventSource(`/events?token=${encodeURIComponent(token)}`);
    src.onopen = () => {
      setConn(true, 'live');
      stopPolling();
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };
    src.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (x) {} };
    src.onerror = () => {
      setConn(false, 'reconnecting…');
      startPolling();                 // keep the panel live while the stream is down
      try { src.close(); } catch (x) {}
      if (!reconnectTimer) reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, 2500);
    };
  };
  pollState();
  connect();
})();
