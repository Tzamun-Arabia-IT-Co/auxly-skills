(() => {
  const byId = (id) => document.getElementById(id);
  const token = new URLSearchParams(window.location.search).get('token') || '';

  const logo = byId('brandLogo');
  if (logo && window.AUXLY_LOGO) logo.src = window.AUXLY_LOGO;

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const SICON = { pending: '', running: '●', done: '✓', failed: '✕', blocked: '!' };
  const KIND_ICON = { codex: '◆', claude: '✦', gemini: '✧', agy: '▲', opencode: '❮❯', custom: '⚙' };

  let state = null;

  const post = async (route, payload) => {
    try {
      await fetch(route, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Dash-Token': token },
        body: JSON.stringify(payload || {}),
      });
    } catch (e) { /* ignore */ }
  };

  const setConn = (ok, label) => {
    const c = byId('conn');
    c.dataset.ok = ok ? '1' : '0';
    byId('connText').textContent = label;
  };

  const renderHeader = (s) => {
    byId('runTitle').textContent = s.title || 'Execution';
    byId('runId').textContent = s.run_id || '—';
    const badge = byId('runBadge');
    badge.dataset.status = s.status || 'running';
    byId('runStatusText').textContent = s.status || 'running';
    const pct = (s.progress && s.progress.pct) || 0;
    byId('progPct').textContent = pct + '%';
    byId('progCount').textContent = `${(s.progress && s.progress.done) || 0}/${(s.progress && s.progress.total) || 0} slices`;
    byId('progBar').style.width = pct + '%';
  };

  const renderBlockers = (s) => {
    const host = byId('blockers');
    host.textContent = '';
    const list = (s.blockers || []);
    list.forEach((b) => {
      const open = b.status !== 'resolved';
      const el = document.createElement('div');
      el.className = 'notif blocker' + (open ? '' : ' resolved');
      let html = `<div class="n-head"><span class="n-tag">🔴 Blocker · action needed</span>`;
      if (b.slice) html += `<span class="n-slice">slice ${esc(b.slice)}</span>`;
      html += `</div><div class="n-subject">${esc(b.subject)}</div>`;
      if (b.detail) html += `<div class="n-detail">${esc(b.detail)}</div>`;
      if (open) {
        html += `<div class="resolve-row">
          <textarea data-bid="${esc(b.id)}" placeholder="Your answer / resolution so execution can resume…"></textarea>
          <button class="btn resolve" data-bid="${esc(b.id)}">Resolve &amp; resume</button></div>`;
      } else if (b.resolution) {
        html += `<div class="resolution-note">✓ Resolved: ${esc(b.resolution)}</div>`;
      } else {
        html += `<div class="resolution-note">✓ Resolved</div>`;
      }
      el.innerHTML = html;
      host.appendChild(el);
    });
    host.querySelectorAll('button.resolve').forEach((btn) => {
      btn.addEventListener('click', () => {
        const bid = btn.dataset.bid;
        const ta = host.querySelector(`textarea[data-bid="${CSS.escape(bid)}"]`);
        post('/api/resolve', { kind: 'blocker', id: bid, resolution: ta ? ta.value : '' });
      });
    });
  };

  const renderWarnings = (s) => {
    const host = byId('warnings');
    host.textContent = '';
    (s.warnings || []).filter((w) => w.status !== 'dismissed').forEach((w) => {
      const el = document.createElement('div');
      el.className = 'notif warning';
      let html = `<div class="n-head"><span class="n-tag">🟡 Warning · awareness</span>`;
      if (w.slice) html += `<span class="n-slice">slice ${esc(w.slice)}</span>`;
      html += `<button class="btn dismiss" data-wid="${esc(w.id)}" style="margin-left:auto">Dismiss</button></div>`;
      html += `<div class="n-subject">${esc(w.subject)}</div>`;
      if (w.detail) html += `<div class="n-detail">${esc(w.detail)}</div>`;
      el.innerHTML = html;
      host.appendChild(el);
    });
    host.querySelectorAll('button.dismiss').forEach((btn) => {
      btn.addEventListener('click', () => post('/api/resolve', { kind: 'warning', id: btn.dataset.wid }));
    });
  };

  const renderAgents = (s) => {
    const panel = byId('agentsPanel');
    const host = byId('agents');
    const list = s.agents || [];
    if (!list.length) { panel.style.display = 'none'; host.textContent = ''; return; }
    panel.style.display = '';
    const active = list.filter((a) => a.status === 'active').length;
    byId('agentsSub').textContent = `${list.length} agent${list.length === 1 ? '' : 's'} · ${active} active`;
    host.textContent = '';
    list.forEach((a) => {
      const icon = KIND_ICON[a.kind] || '•';
      const el = document.createElement('div');
      el.className = 'agent';
      el.dataset.status = a.status || 'idle';
      const tag = (a.status || 'idle');
      el.innerHTML = `
        <span class="adot"></span>
        <div class="agent-main">
          <span class="agent-name">${esc(icon)} ${esc(a.name)}</span>
          ${a.model ? `<span class="agent-model">${esc(a.model)}</span>` : ''}
          ${a.current ? `<span class="agent-current">▸ ${esc(a.current)}</span>` : ''}
        </div>
        <div class="agent-meta">
          <span class="arole">${esc(a.role || 'executor')}</span>
          <span class="atag ${esc(tag)}">${esc(tag)}</span>
        </div>`;
      host.appendChild(el);
    });
  };

  const renderPhases = (s) => {
    const host = byId('phases');
    host.textContent = '';
    const phases = s.phases || [];
    byId('phasesSub').textContent = `${phases.length} phase${phases.length === 1 ? '' : 's'}`;
    phases.forEach((p) => {
      const wrap = document.createElement('div');
      wrap.className = 'phase';
      const slices = (p.slices || []).map((sl) => `
        <div class="slice" data-s="${esc(sl.status)}">
          <span class="sicon">${SICON[sl.status] || ''}</span>
          <span class="sid">${esc(sl.id)}</span>
          <span class="sname">${esc(sl.name)}</span>
          ${sl.note ? `<span class="snote">${esc(sl.note)}</span>` : ''}
        </div>`).join('');
      wrap.innerHTML = `
        <div class="phase-head">
          <span class="phase-id">${esc(p.id)}</span>
          <span class="phase-name">${esc(p.name)}</span>
          <span class="phase-status" data-s="${esc(p.status)}">${esc(p.status)}</span>
        </div>
        <div class="slices">${slices}</div>`;
      host.appendChild(wrap);
    });
  };

  const renderLog = (s) => {
    const host = byId('log');
    host.textContent = '';
    (s.log || []).slice(-80).forEach((e) => {
      const line = document.createElement('div');
      const t = e.ts ? new Date(e.ts).toLocaleTimeString() : '';
      line.innerHTML = `<span class="lt">${esc(t)}</span> ${esc(e.msg)}`;
      host.appendChild(line);
    });
    host.scrollTop = host.scrollHeight;
  };

  const render = (s) => {
    state = s;
    renderHeader(s);
    renderAgents(s);
    renderBlockers(s);
    renderWarnings(s);
    renderPhases(s);
    renderLog(s);
  };

  const connect = () => {
    if (!window.EventSource) { setConn(false, 'SSE unsupported'); return; }
    const src = new EventSource(`/events?token=${encodeURIComponent(token)}`);
    src.onopen = () => setConn(true, 'live');
    src.onmessage = (ev) => {
      try { render(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
    };
    src.onerror = () => setConn(false, 'reconnecting…');
  };

  // initial fetch then live stream
  fetch('/api/state', { cache: 'no-store' })
    .then((r) => r.json()).then(render).catch(() => {});
  connect();
})();
