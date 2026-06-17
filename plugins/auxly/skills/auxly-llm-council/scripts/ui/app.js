(() => {
  const byId = (id) => document.getElementById(id);

  // ---- Brand logo (embedded data URI from logo.js, fully self-contained) ----
  const brandLogo = byId('brandLogo');
  if (brandLogo && window.AUXLY_LOGO) {
    brandLogo.src = window.AUXLY_LOGO;
  }

  // ---- Minimal, safe Markdown renderer -------------------------------------
  // Escapes HTML first, then applies a small set of block/inline rules so the
  // council output reads as a formatted document instead of a raw JSON/markdown
  // dump. Intentionally tiny: no external deps, no innerHTML from raw input.
  const escapeHtml = (s) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const renderInline = (text) =>
    escapeHtml(text)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  const renderMarkdown = (md) => {
    if (!md || !md.trim()) {
      return '<span class="placeholder">—</span>';
    }
    const lines = md.replace(/\r\n/g, '\n').split('\n');
    const out = [];
    let listType = null; // 'ul' | 'ol'
    let inFence = false;
    let fenceBuf = [];
    const closeList = () => {
      if (listType) { out.push(`</${listType}>`); listType = null; }
    };
    for (const raw of lines) {
      const line = raw;
      const fence = line.match(/^\s*```/);
      if (fence) {
        if (inFence) { out.push(`<pre><code>${escapeHtml(fenceBuf.join('\n'))}</code></pre>`); fenceBuf = []; inFence = false; }
        else { closeList(); inFence = true; }
        continue;
      }
      if (inFence) { fenceBuf.push(line); continue; }

      if (!line.trim()) { closeList(); continue; }

      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { closeList(); const lvl = h[1].length; out.push(`<h${lvl}>${renderInline(h[2])}</h${lvl}>`); continue; }
      if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { closeList(); out.push('<hr>'); continue; }

      const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      const ul = line.match(/^\s*[-*+]\s+(.*)$/);
      if (ol) {
        if (listType !== 'ol') { closeList(); out.push('<ol>'); listType = 'ol'; }
        out.push(`<li>${renderInline(ol[1])}</li>`); continue;
      }
      if (ul) {
        if (listType !== 'ul') { closeList(); out.push('<ul>'); listType = 'ul'; }
        out.push(`<li>${renderInline(ul[1])}</li>`); continue;
      }
      closeList();
      out.push(`<p>${renderInline(line)}</p>`);
    }
    if (inFence && fenceBuf.length) { out.push(`<pre><code>${escapeHtml(fenceBuf.join('\n'))}</code></pre>`); }
    closeList();
    return out.join('\n');
  };

  // Extract the body under any markdown heading whose title matches one of the
  // given names (case-insensitive). Used to surface Risks / Pros / Cons.
  const extractSections = (md, names) => {
    if (!md) return '';
    const wanted = names.map((n) => n.toLowerCase());
    const lines = md.replace(/\r\n/g, '\n').split('\n');
    const chunks = [];
    let capture = false;
    let buf = [];
    const flush = () => { if (buf.length) { chunks.push(buf.join('\n').trim()); buf = []; } };
    for (const line of lines) {
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        const title = h[2].replace(/[*:#]/g, '').trim().toLowerCase();
        if (capture) flush();
        capture = wanted.some((w) => title === w || title.startsWith(w + ' ') || title.includes(w));
        continue;
      }
      if (capture) buf.push(line);
    }
    flush();
    return chunks.filter(Boolean).join('\n\n').trim();
  };

  const setMarkdown = (el, md) => {
    if (!el) return;
    el.innerHTML = renderMarkdown(md);
  };

  // Fill a Risk/Pros/Cons card; hide it when the section is absent/empty.
  const setCard = (cardEl, bodyEl, md) => {
    if (!cardEl || !bodyEl) return;
    const content = (md || '').trim();
    const empty = !content || /^(-?\s*<[^>]+>\s*)+$/.test(content) || content === '—';
    if (empty) {
      cardEl.classList.add('empty');
      bodyEl.innerHTML = '';
    } else {
      cardEl.classList.remove('empty');
      bodyEl.innerHTML = renderMarkdown(content);
    }
  };

  const RISK_NAMES = ['risks', 'risk', 'edge cases', 'gaps', 'missing steps', 'contradictions'];
  const PRO_NAMES = ['pros', 'strengths', 'advantages'];
  const CON_NAMES = ['cons', 'weaknesses', 'trade-offs', 'tradeoffs'];

  const KIND_ICON = { codex: '◆', claude: '✦', gemini: '✧', agy: '▲', opencode: '❮❯', custom: '⚙' };

  // Connection pill keeps its status dot; setStatus would wipe the child node.
  const setConnection = (label) => {
    const el = elements.connectionStatus;
    if (!el) return;
    el.dataset.tone = label;
    el.innerHTML = '<span class="dot"></span>' + escapeHtml(label);
  };

  // Render the live council roster in the header: every member as a chip with
  // its kind icon, name, model, role and a status dot driven by live state.
  const renderCouncil = (state) => {
    if (elements.councilMode) {
      const mode = state.council_mode || '';
      elements.councilMode.dataset.mode = mode;
      elements.councilMode.textContent = mode
        ? (mode === 'claude-only' ? 'Claude-only' : 'Multi-vendor')
        : '';
      elements.councilMode.style.display = mode ? '' : 'none';
    }
    const roster = Array.isArray(state.council) ? state.council : [];
    if (!elements.councilRoster) return;
    // Map live statuses by member id (planners + judge).
    const statusById = {};
    (Array.isArray(state.planners) ? state.planners : []).forEach((p) => { if (p.id) statusById[p.id] = p.status; });
    if (state.judge && roster.some((m) => m.role === 'judge')) {
      const j = roster.find((m) => m.role === 'judge');
      if (j) statusById[j.id] = state.judge.status;
    }
    elements.councilRoster.textContent = '';
    roster.forEach((m) => {
      const chip = document.createElement('span');
      chip.className = 'member' + (m.role === 'judge' ? ' judge' : '');
      chip.dataset.status = statusById[m.id] || 'pending';
      const icon = KIND_ICON[m.kind] || '•';
      const dot = document.createElement('span'); dot.className = 'mdot';
      const name = document.createElement('span'); name.className = 'mname'; name.textContent = `${icon} ${m.id}`;
      const model = document.createElement('span'); model.className = 'mmodel'; model.textContent = m.model || m.kind;
      const role = document.createElement('span'); role.className = 'mrole'; role.textContent = m.role === 'judge' ? 'judge' : 'member';
      chip.append(dot, name, model, role);
      chip.title = `${m.id} · ${m.kind} · ${m.model || 'default'} · ${m.role}`;
      elements.councilRoster.appendChild(chip);
    });
  };

  const elements = {
    runId: byId('runId'),
    phase: byId('phase'),
    taskBrief: byId('taskBrief'),
    plannerSummary: byId('plannerSummary'),
    plannerSelect: byId('plannerSelect'),
    plannerId: byId('plannerId'),
    plannerStatus: byId('plannerStatus'),
    plannerSummaryText: byId('plannerSummaryText'),
    plannerErrors: byId('plannerErrors'),
    judgeStatus: byId('judgeStatus'),
    judgeSummary: byId('judgeSummary'),
    judgeErrors: byId('judgeErrors'),
    finalPlanStatus: byId('finalPlanStatus'),
    finalPlanEditor: byId('finalPlanEditor'),
    finalPlanPreview: byId('finalPlanPreview'),
    previewPane: byId('previewPane'),
    editToggle: byId('editToggle'),
    previewToggle: byId('previewToggle'),
    resetLatest: byId('resetLatest'),
    acceptBtn: byId('acceptBtn'),
    saveBtn: byId('saveBtn'),
    saveStatus: byId('saveStatus'),
    refineContext: byId('refineContext'),
    refineBtn: byId('refineBtn'),
    refineStatus: byId('refineStatus'),
    connectionStatus: byId('connectionStatus'),
    councilRoster: byId('councilRoster'),
    councilMode: byId('councilMode'),
    lastUpdated: byId('lastUpdated'),
    sessionCountdown: byId('sessionCountdown'),
    keepOpenToggle: byId('keepOpenToggle'),
    keepOpenStatus: byId('keepOpenStatus')
  };

  const token = new URLSearchParams(window.location.search).get('token');
  const stateEndpoint = '/api/state';
  const eventsEndpoint = token ? `/events?token=${encodeURIComponent(token)}` : '/events';

  let currentState = {
    run_id: '',
    task_brief: '',
    phase: '',
    planners: [],
    judge: { status: '', summary: '', errors: [] },
    final_plan: '',
    errors: [],
    timestamps: {}
  };

  let latestFinalPlan = '';
  let editorDirty = false;
  let editorLocked = false;
  let previewVisible = true;
  let selectedPlannerId = '';
  let sessionDeadline = null;
  let sessionKeepOpen = false;
  let sessionTimer = null;

  const setText = (el, value, fallback = '—') => {
    if (!el) {
      return;
    }
    const text = typeof value === 'string' && value.trim() ? value : fallback;
    el.textContent = text;
  };

  const setStatus = (el, message, tone) => {
    if (!el) {
      return;
    }
    el.textContent = message;
    if (tone) {
      el.dataset.tone = tone;
    } else {
      delete el.dataset.tone;
    }
  };

  const attemptCloseUi = () => {
    try {
      window.close();
    } catch (error) {
      // ignore
    }
    setTimeout(() => {
      if (!window.closed) {
        setStatus(elements.saveStatus, 'accepted — you can close this tab');
      }
    }, 300);
  };

  const formatTimestamp = (value) => {
    if (!value) {
      return '—';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleTimeString();
  };

  const updateLastUpdated = (value) => {
    setText(elements.lastUpdated, `last update: ${formatTimestamp(value)}`);
  };

  const formatRemaining = (ms) => {
    const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const pad = (value) => String(value).padStart(2, '0');
    if (hours > 0) {
      return `${hours}:${pad(minutes)}:${pad(seconds)}`;
    }
    return `${minutes}:${pad(seconds)}`;
  };

  const updateSessionCountdown = () => {
    if (!elements.sessionCountdown) {
      return;
    }
    if (sessionKeepOpen) {
      setText(elements.sessionCountdown, 'session: keep open');
      return;
    }
    if (!sessionDeadline) {
      setText(elements.sessionCountdown, 'session: —');
      return;
    }
    const remaining = sessionDeadline - Date.now();
    if (remaining <= 0) {
      setText(elements.sessionCountdown, 'session: closing…');
      return;
    }
    setText(elements.sessionCountdown, `session: ${formatRemaining(remaining)} left`);
  };

  const setSessionState = (state) => {
    sessionKeepOpen = Boolean(state?.keep_open);
    sessionDeadline = state?.ui_deadline ? new Date(state.ui_deadline).getTime() : null;
    if (elements.keepOpenToggle) {
      elements.keepOpenToggle.checked = sessionKeepOpen;
    }
    if (!sessionTimer) {
      sessionTimer = window.setInterval(updateSessionCountdown, 1000);
    }
    updateSessionCountdown();
  };

  const setStatusLink = (el, message, url) => {
    if (!el) {
      return;
    }
    el.textContent = '';
    const text = message || '';
    if (text) {
      el.appendChild(document.createTextNode(text + ' '));
    }
    if (url) {
      const link = document.createElement('a');
      link.href = url;
      link.textContent = 'Open new run';
      link.target = '_blank';
      link.rel = 'noopener';
      el.appendChild(link);
    }
  };

  const updatePlannerList = (planners) => {
    if (!elements.plannerSelect) {
      return;
    }
    elements.plannerSelect.textContent = '';
    if (!Array.isArray(planners) || planners.length === 0) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'Waiting for planner output…';
      elements.plannerSelect.appendChild(option);
      updatePlannerDetail(null);
      return;
    }

    planners.forEach((planner) => {
      const option = document.createElement('option');
      option.value = planner.id || '';
      option.textContent = planner.id || 'planner';
      elements.plannerSelect.appendChild(option);
    });

    if (!selectedPlannerId || !planners.some((p) => p.id === selectedPlannerId)) {
      selectedPlannerId = planners[0].id || '';
    }
    elements.plannerSelect.value = selectedPlannerId;
    const active = planners.find((planner) => planner.id === selectedPlannerId) || planners[0];
    updatePlannerDetail(active);
  };

  const setChip = (el, status) => {
    if (!el) return;
    el.textContent = status || 'pending';
    el.dataset.tone = status || '';
  };

  const renderErrors = (listEl, errors) => {
    if (!listEl) return;
    listEl.textContent = '';
    if (Array.isArray(errors)) {
      errors.forEach((err) => {
        const item = document.createElement('li');
        item.textContent = err;
        listEl.appendChild(item);
      });
    }
  };

  const updatePlannerDetail = (planner) => {
    const md = planner?.summary || '';
    setText(elements.plannerId, planner?.id || '—');
    setChip(elements.plannerStatus, planner?.status);
    setMarkdown(elements.plannerSummaryText, md);
    setCard(byId('plannerRiskCard'), byId('plannerRisks'), extractSections(md, RISK_NAMES));
    setCard(byId('plannerProsCard'), byId('plannerPros'), extractSections(md, PRO_NAMES));
    setCard(byId('plannerConsCard'), byId('plannerCons'), extractSections(md, CON_NAMES));
    renderErrors(elements.plannerErrors, planner?.errors);
  };

  const updateJudge = (judge) => {
    const md = judge?.summary || '';
    setChip(elements.judgeStatus, judge?.status);
    setMarkdown(elements.judgeSummary, md);
    setCard(byId('judgeRiskCard'), byId('judgeRisks'), extractSections(md, RISK_NAMES));
    setCard(byId('judgeProsCard'), byId('judgePros'), extractSections(md, PRO_NAMES));
    setCard(byId('judgeConsCard'), byId('judgeCons'), extractSections(md, CON_NAMES));
    renderErrors(elements.judgeErrors, judge?.errors);
  };

  const updateFinalPlan = (planText) => {
    latestFinalPlan = planText || '';
    if (!editorDirty) {
      elements.finalPlanEditor.value = latestFinalPlan;
    }
    setMarkdown(elements.finalPlanPreview, latestFinalPlan);
    const status = editorDirty ? 'edited locally' : 'synced';
    setText(elements.finalPlanStatus, status);
  };

  const applyState = (state) => {
    currentState = state;
    setText(elements.runId, state.run_id || '—');
    setText(elements.phase, state.phase || '—');
    setText(elements.taskBrief, state.task_brief || '—');

    const planners = Array.isArray(state.planners) ? state.planners : [];
    elements.plannerSummary.textContent = `${planners.length} member${planners.length === 1 ? '' : 's'}`;
    updatePlannerList(planners);

    updateJudge(state.judge || { status: 'pending', summary: '', errors: [] });
    updateFinalPlan(state.final_plan || '');
    renderCouncil(state);

    updateLastUpdated(state.timestamps?.updated_at || new Date().toISOString());
    setSessionState(state);
  };

  const fetchState = async () => {
    try {
      const response = await fetch(stateEndpoint, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error('failed');
      }
      const payload = await response.json();
      applyState(payload);
    } catch (error) {
      setConnection('state fetch failed');
    }
  };

  const handleEvent = (message) => {
    if (!message || typeof message !== 'object') {
      return;
    }

    if (message.type === 'phase_change') {
      currentState.phase = message.payload?.phase || currentState.phase;
      applyState(currentState);
      updateLastUpdated(message.payload?.timestamp);
      return;
    }

    if (message.type === 'planner_update') {
      const planner = message.payload?.planner;
      if (planner && planner.id) {
        const planners = Array.isArray(currentState.planners) ? [...currentState.planners] : [];
        const index = planners.findIndex((item) => item.id === planner.id);
        if (index >= 0) {
          planners[index] = planner;
        } else {
          planners.push(planner);
        }
        currentState.planners = planners;
      }
      applyState(currentState);
      updateLastUpdated(message.payload?.timestamp);
      return;
    }

    if (message.type === 'judge_update') {
      if (message.payload?.judge) {
        currentState.judge = message.payload.judge;
      }
      applyState(currentState);
      updateLastUpdated(message.payload?.timestamp);
      return;
    }

    if (message.type === 'final_plan') {
      currentState.final_plan = message.payload?.final_plan || currentState.final_plan;
      applyState(currentState);
      updateLastUpdated(message.payload?.timestamp);
      return;
    }

    if (message.type === 'session_update') {
      currentState.keep_open = message.payload?.keep_open;
      currentState.ui_deadline = message.payload?.ui_deadline;
      setSessionState(currentState);
      updateLastUpdated(message.payload?.timestamp);
      return;
    }

    if (message.type === 'action_result') {
      const action = message.payload?.action;
      const status = message.payload?.message || message.payload?.status || 'updated';
      if (action === 'save') {
        setStatus(elements.saveStatus, status);
      } else if (action === 'accept') {
        setStatus(elements.saveStatus, status);
        attemptCloseUi();
      } else if (action === 'refine') {
        setStatus(elements.refineStatus, status);
      } else if (action === 'keepalive') {
        setStatus(elements.keepOpenStatus, status);
      }
      updateLastUpdated(message.payload?.timestamp);
      return;
    }
  };

  const connectEvents = () => {
    if (!window.EventSource) {
      setConnection('SSE not supported');
      return;
    }

    const source = new EventSource(eventsEndpoint);
    setConnection('connecting…');

    source.onopen = () => {
      setConnection('connected');
      fetchState();
    };

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleEvent(data);
      } catch (error) {
        setConnection('event parse error');
      }
    };

    source.onerror = () => {
      setConnection('reconnecting…');
    };
  };

  const postAction = async (path, payload, statusEl) => {
    if (!token) {
      setStatus(statusEl, 'missing token', 'warning');
      return;
    }
    const suppressQueued = path === '/api/save';
    setStatus(statusEl, suppressQueued ? 'saving…' : 'sending…');
    try {
      const response = await fetch(path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-UI-Token': token
        },
        body: JSON.stringify(payload || {})
      });
      if (!response.ok) {
        throw new Error('action failed');
      }
      if (!suppressQueued) {
        setStatus(statusEl, 'queued');
      }
    } catch (error) {
      setStatus(statusEl, 'failed');
    }
  };

  if (elements.finalPlanEditor) {
    elements.finalPlanEditor.addEventListener('input', () => {
      if (editorLocked) {
        return;
      }
      editorDirty = elements.finalPlanEditor.value !== latestFinalPlan;
      setMarkdown(elements.finalPlanPreview, elements.finalPlanEditor.value);
      const status = editorDirty ? 'edited locally' : 'synced';
      setText(elements.finalPlanStatus, status);
    });
  }

  elements.acceptBtn.addEventListener('click', () => {
    postAction('/api/accept', { final_plan: elements.finalPlanEditor.value }, elements.saveStatus);
  });

  elements.saveBtn.addEventListener('click', () => {
    postAction('/api/save', { final_plan: elements.finalPlanEditor.value }, elements.saveStatus);
  });

  if (elements.refineBtn) {
    elements.refineBtn.addEventListener('click', () => {
      postAction(
        '/api/refine',
        {
          context: elements.refineContext.value,
          final_plan: elements.finalPlanEditor.value
        },
        elements.refineStatus
      );
    });
  }

  if (elements.plannerSelect) {
    elements.plannerSelect.addEventListener('change', () => {
      selectedPlannerId = elements.plannerSelect.value;
      const planners = Array.isArray(currentState.planners) ? currentState.planners : [];
      const active = planners.find((planner) => planner.id === selectedPlannerId) || planners[0];
      updatePlannerDetail(active || null);
    });
  }

  if (elements.keepOpenToggle) {
    elements.keepOpenToggle.addEventListener('change', () => {
      postAction(
        '/api/keepalive',
        { keep_open: elements.keepOpenToggle.checked },
        elements.keepOpenStatus
      );
    });
  }

  fetchState();
  connectEvents();
})();
