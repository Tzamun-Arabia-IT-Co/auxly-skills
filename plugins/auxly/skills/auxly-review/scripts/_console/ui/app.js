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

  const toast = (msg, tone) => {
    let wrap = byId('toastWrap');
    if (!wrap) { wrap = document.createElement('div'); wrap.id = 'toastWrap'; wrap.className = 'toast-wrap'; document.body.appendChild(wrap); }
    const el = document.createElement('div'); el.className = 'toast' + (tone ? ' ' + tone : ''); el.textContent = msg;
    wrap.appendChild(el); requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 250); }, 3600);
  };

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

  // Single linear flow: one "Next" button that advances to the next step.
  // intent = the signal the worker agent picks up to start that step.
  const FLOW = ['plan', 'execute', 'verify', 'review'];
  const STEP_INTENT = { execute: 'start_execute', verify: 'start_verify', review: 'start_review' };
  const STEP_VERB = { execute: 'execute', verify: 'verify', review: 'review' };
  // Is the current stage finished (ready to advance)?
  const stageDone = (st) => {
    if (!st) return false;
    if (st.status === 'done' || st.status === 'complete') return true;
    if (st.kind === 'execute') { const pr = (st.data || {}).progress || {}; return pr.total > 0 && pr.pct >= 100; }
    if (st.kind === 'verify') { const c = (st.data || {}).checks || []; return c.length > 0 && c.every((x) => ['pass', 'skip'].includes(x.status)); }
    return false;
  };
  const renderActions = (s) => {
    const host = byId('actions'); host.innerHTML = '';
    const st = s.stages[selected]; if (!st) return;
    const idx = FLOW.indexOf(st.kind);
    const nextKind = idx >= 0 ? FLOW.slice(idx + 1).find((k) => s.stages[k]) : null;

    if (!nextKind) {
      // last step — show completion, no Next
      if (stageDone(st)) { const m = document.createElement('span'); m.className = 'flow-msg ok'; m.textContent = '✓ All steps complete'; host.appendChild(m); }
      return;
    }
    const nextTitle = (s.stages[nextKind].title) || nextKind;
    const ready = stageDone(st);

    if (ready) {
      const m = document.createElement('span'); m.className = 'flow-msg ok';
      m.textContent = `✓ ${st.title || st.name} complete — click Next to ${STEP_VERB[nextKind] || nextKind}`;
      host.appendChild(m);
    }
    const b = document.createElement('button');
    b.className = 'btn primary next' + (ready ? ' ready' : '');
    b.textContent = `Next: ${nextTitle} ▶`;
    if (!ready) b.title = `${st.title || st.name} still in progress — you can jump ahead, or wait until it's done`;
    b.addEventListener('click', () => {
      const name = STEP_INTENT[nextKind];
      if (name) post('/api/intent', { kind: 'intent', name });
      selected = nextKind; userPicked = true; render(state);
      toast(`Moving to ${nextTitle} — agent starting…`);
    });
    host.appendChild(b);
  };

  // Always-visible blockers/warnings count so the user knows the state at a
  // glance — green "0 blockers" when clear, red/amber with a count when not.
  const renderHealth = (s) => {
    const host = byId('statusPills'); if (!host) return;
    const blk = (s.blockers || []).filter((b) => b.status !== 'resolved').length;
    const wrn = (s.warnings || []).filter((w) => w.status !== 'dismissed').length;
    host.innerHTML =
      `<span class="spill ${blk ? 'block' : 'ok'}"><span class="si">${blk ? '⛔' : '✓'}</span>${blk} blocker${blk === 1 ? '' : 's'}</span>` +
      `<span class="spill ${wrn ? 'warn' : 'ok'}"><span class="si">${wrn ? '⚠️' : '✓'}</span>${wrn} warning${wrn === 1 ? '' : 's'}</span>`;
  };

  const renderNotifs = (s) => {
    const host = byId('notifs'); host.innerHTML = '';
    (s.blockers || []).forEach((b) => {
      const open = b.status !== 'resolved';
      const el = document.createElement('div'); el.className = 'notif blocker' + (open ? '' : ' resolved');
      const tag = open ? '🔴 Blocker · action needed' : '✅ Blocker resolved';
      let h = `<div class="nh"><span class="ntag">${tag}</span>${b.slice ? `<span class="nslice">slice ${esc(b.slice)}</span>` : ''}</div>`;
      if (open) {
        h += `<div class="nsubj">${esc(b.subject)}</div>`;
        if (b.detail) h += `<div class="ndet">${esc(b.detail)}</div>`;
        h += `<div class="rrow"><textarea data-b="${esc(b.id)}" placeholder="Your answer / resolution so work can resume…"></textarea><button class="btn resolve" data-b="${esc(b.id)}">Resolve &amp; resume</button></div>`;
      } else {
        // compact, unmistakably-done record — no red, no "action needed"
        h += `<div class="rnote">${esc(b.subject)} — resolved${b.resolution ? ': ' + esc(b.resolution) : ''}</div>`;
      }
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
  // ---- rich PLAN renderer (matches the council's plan view) ----------------
  const RISK_NAMES = ['risks', 'risk', 'edge cases', 'gaps', 'missing steps', 'contradictions'];
  const PRO_NAMES = ['pros', 'strengths', 'advantages'];
  const CON_NAMES = ['cons', 'weaknesses', 'trade-offs', 'tradeoffs'];
  const extractSections = (src, names) => {
    if (!src) return '';
    const want = names.map((n) => n.toLowerCase());
    const chunks = []; let cap = false, buf = [];
    const flush = () => { if (buf.length) { chunks.push(buf.join('\n').trim()); buf = []; } };
    src.replace(/\r\n/g, '\n').split('\n').forEach((line) => {
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { const t = h[2].replace(/[*:#]/g, '').trim().toLowerCase(); if (cap) flush(); cap = want.some((w) => t === w || t.startsWith(w + ' ') || t.includes(w)); return; }
      if (cap) buf.push(line);
    });
    flush();
    return chunks.filter(Boolean).join('\n\n').trim();
  };
  const cardHtml = (cls, title, body) => {
    const c = (body || '').trim();
    if (!c || c === '—') return '';
    return `<div class="card ${cls}"><div class="card-title">${title}</div><div class="md">${md(c)}</div></div>`;
  };
  const planBody = (st) => {
    const d = st.data || {};
    const text = d.markdown || '';
    if (!text.trim()) return '<span class="placeholder">No plan yet.</span>';
    const roster = Array.isArray(d.council) ? d.council : [];
    let h = '';
    if (roster.length) {
      h += '<div class="roster-line">' + roster.map((m) =>
        `<span class="rchip"><span class="rk">${KIND_ICON[m.kind] || '•'}</span> ${esc(m.id || m.kind)}${m.model ? ` <span class="rmodel">${esc(m.model)}</span>` : ''}${m.role === 'judge' ? ' <span class="rrole">judge</span>' : ''}</span>`).join('') + '</div>';
    }
    const cards = cardHtml('risk', '⚠️ Risks &amp; Edge Cases', extractSections(text, RISK_NAMES))
      + cardHtml('pros', '✅ Pros', extractSections(text, PRO_NAMES))
      + cardHtml('cons', '⚖️ Cons', extractSections(text, CON_NAMES));
    if (cards) h += `<div class="insight-cards">${cards}</div>`;
    h += `<div class="plan-toolbar"><input id="planSearch" class="plan-search" type="search" placeholder="Search in plan… ( / )" /><button id="planCollapse" class="ghost-btn" type="button">Collapse all</button><button id="planCopy" class="ghost-btn" type="button">Copy plan</button></div>`;
    h += `<div id="planDoc" class="md plan-doc">${md(text)}</div>`;
    return h;
  };
  // wrap H2 sections collapsible + wire search/copy after innerHTML is set
  const decoratePlan = (text) => {
    const doc = byId('planDoc'); if (!doc) return;
    const nodes = Array.from(doc.childNodes); const secs = []; let cur = null;
    nodes.forEach((n) => {
      if (n.nodeType === 1 && n.tagName === 'H2') { cur = document.createElement('div'); cur.className = 'md-section'; secs.push(cur); cur.appendChild(n); }
      else if (cur) cur.appendChild(n); else secs.push(n);
    });
    if (secs.some((s) => s.classList && s.classList.contains('md-section'))) {
      doc.textContent = ''; secs.forEach((s) => doc.appendChild(s));
      doc.querySelectorAll('.md-section > h2').forEach((hh) => hh.addEventListener('click', () => hh.parentElement.classList.toggle('collapsed')));
    }
    const search = byId('planSearch');
    if (search) search.addEventListener('input', () => {
      const q = search.value.trim();
      doc.querySelectorAll('mark').forEach((m) => { const p = m.parentNode; p.replaceChild(document.createTextNode(m.textContent), m); p.normalize(); });
      if (q.length < 2) return;
      const rx = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      const w = document.createTreeWalker(doc, NodeFilter.SHOW_TEXT, null); const tgt = []; let nn;
      while ((nn = w.nextNode())) { if (rx.test(nn.nodeValue)) tgt.push(nn); }
      let first = null;
      tgt.forEach((node) => { const frag = document.createDocumentFragment(); let last = 0, s = node.nodeValue, m2; rx.lastIndex = 0;
        while ((m2 = rx.exec(s))) { if (m2.index > last) frag.appendChild(document.createTextNode(s.slice(last, m2.index))); const mk = document.createElement('mark'); mk.textContent = m2[0]; frag.appendChild(mk); if (!first) first = mk; last = m2.index + m2[0].length; if (m2.index === rx.lastIndex) rx.lastIndex++; }
        if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last))); node.parentNode.replaceChild(frag, node); });
      if (first) first.scrollIntoView({ block: 'center' });
    });
    const collapse = byId('planCollapse');
    if (collapse) collapse.addEventListener('click', () => { const ss = doc.querySelectorAll('.md-section'); const open = [...ss].some((s) => !s.classList.contains('collapsed')); ss.forEach((s) => s.classList.toggle('collapsed', open)); collapse.textContent = open ? 'Expand all' : 'Collapse all'; });
    const copy = byId('planCopy');
    if (copy) copy.addEventListener('click', () => { (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).catch(() => {}); copy.textContent = 'Copied'; setTimeout(() => copy.textContent = 'Copy plan', 1200); });
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
    else if (st.kind === 'plan') { body.innerHTML = planBody(st); decoratePlan((st.data || {}).markdown || ''); }
    else body.innerHTML = md((st.data || {}).markdown || '');
  };

  const renderLog = (s) => {
    const host = byId('log'); host.innerHTML = (s.log || []).slice(-80).map((e) =>
      `<div><span class="lt">${esc(e.ts ? new Date(e.ts).toLocaleTimeString() : '')}</span> ${esc(e.msg)}</div>`).join('');
    host.scrollTop = host.scrollHeight;
  };

  // Try to enable desktop notifications quietly so the completion ping can
  // reach the user even if the tab is in the background.
  if (window.Notification && Notification.permission === 'default') {
    try { Notification.requestPermission(); } catch (e) { /* ignore */ }
  }
  let lastRunStatus = null;
  const notifyComplete = (s) => {
    const blk = (s.blockers || []).filter((b) => b.status !== 'resolved').length;
    const done = (s.stages.execute && (s.stages.execute.data || {}).progress) || {};
    const title = s.title || 'Auxly run';
    const msg = `✓ ${title} complete${done.total ? ` — ${done.done}/${done.total} slices` : ''} · ${blk} open blockers`;
    toast(msg, 'success');
    if (window.Notification && Notification.permission === 'granted') {
      try { new Notification('Auxly — task complete', { body: msg }); } catch (e) { /* ignore */ }
    }
  };

  const render = (s) => {
    state = s;
    byId('runTitle').textContent = s.title || 'Auxly';
    byId('runId').textContent = s.run_id || '—';
    byId('runBadge').dataset.status = s.run_status || 'running';
    byId('runStatusText').textContent = s.run_status || 'running';
    if (!userPicked || !s.stages[selected]) selected = s.active_stage || (s.stage_order || [])[0] || null;
    renderMeter(s); renderHealth(s); renderTabs(s); renderActions(s); renderNotifs(s); renderAgents(s); renderStage(s); renderLog(s);
    // Orchestrator finished → fire a one-time completion notification.
    const rs = s.run_status || '';
    if (rs === 'complete' && lastRunStatus && lastRunStatus !== 'complete') notifyComplete(s);
    lastRunStatus = rs;
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
