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
    executeBtn: byId('executeBtn'),
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
    keepOpenStatus: byId('keepOpenStatus'),
    stepper: byId('stepper'),
    plannerTicker: byId('plannerTicker'),
    plannerTickerText: byId('plannerTickerText'),
    judgeTicker: byId('judgeTicker'),
    judgeTickerText: byId('judgeTickerText'),
    workloadChips: byId('workloadChips'),
    crewTable: byId('crewTable'),
    addCrewBtn: byId('addCrewBtn'),
    crewStatus: byId('crewStatus'),
    planSearch: byId('planSearch'),
    collapseAllBtn: byId('collapseAllBtn'),
    copyPlanBtn: byId('copyPlanBtn'),
    editorGrid: byId('editorGrid'),
    splitHandle: byId('splitHandle'),
    toastWrap: byId('toastWrap')
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

  // ---- Toasts --------------------------------------------------------------
  const toast = (title, detail, tone) => {
    if (!elements.toastWrap) return;
    const el = document.createElement('div');
    el.className = 'toast' + (tone ? ' ' + tone : '');
    const body = document.createElement('div');
    body.className = 't-body';
    const b = document.createElement('b'); b.textContent = title; body.appendChild(b);
    if (detail) { const s = document.createElement('span'); s.textContent = detail; body.appendChild(s); }
    el.appendChild(body);
    elements.toastWrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 250);
    }, 4200);
  };

  // ---- Phase stepper -------------------------------------------------------
  const STEPS = [
    { key: 'planning', label: 'Planning', match: ['starting', 'planning'] },
    { key: 'judging', label: 'Judging', match: ['judging'] },
    { key: 'complete', label: 'Ready', match: ['complete', 'done', 'accepted'] }
  ];
  const renderStepper = (phase) => {
    if (!elements.stepper) return;
    const p = (phase || '').toLowerCase();
    let activeIdx = STEPS.findIndex((s) => s.match.includes(p));
    if (activeIdx < 0) activeIdx = p ? 0 : -1;
    elements.stepper.textContent = '';
    STEPS.forEach((step, i) => {
      const wrap = document.createElement('span');
      wrap.className = 'step' + (i < activeIdx ? ' done' : i === activeIdx ? ' active' : '');
      const bead = document.createElement('span');
      bead.className = 'bead';
      bead.textContent = i < activeIdx ? '✓' : String(i + 1);
      const lbl = document.createElement('span');
      lbl.className = 'slabel'; lbl.textContent = step.label;
      wrap.append(bead, lbl);
      elements.stepper.appendChild(wrap);
      if (i < STEPS.length - 1) {
        const bar = document.createElement('span');
        bar.className = 'bar' + (i < activeIdx ? ' filled' : '');
        elements.stepper.appendChild(bar);
      }
    });
  };

  // ---- Working ticker (per-member "alive" feel) ----------------------------
  const tickerStart = {}; // id -> epoch ms when it started running
  let tickerTimer = null;
  const RUNNING = (s) => s === 'running' || s === 'retrying' || s === 'refining';
  const updateTickers = () => {
    const planners = Array.isArray(currentState.planners) ? currentState.planners : [];
    const runningP = planners.find((p) => RUNNING(p.status));
    const setTick = (tickerEl, textEl, on, id, verb) => {
      if (!tickerEl) return;
      tickerEl.classList.toggle('on', !!on);
      if (on) {
        if (!tickerStart[id]) tickerStart[id] = Date.now();
        const secs = Math.floor((Date.now() - tickerStart[id]) / 1000);
        if (textEl) textEl.textContent = `${verb} ${id} · ${secs}s`;
      } else if (id) {
        delete tickerStart[id];
      }
    };
    setTick(elements.plannerTicker, elements.plannerTickerText, !!runningP, runningP ? runningP.id : '', 'working');
    const jStatus = currentState.judge && currentState.judge.status;
    setTick(elements.judgeTicker, elements.judgeTickerText, RUNNING(jStatus), 'judge', 'judging');
    if (!tickerTimer) tickerTimer = window.setInterval(updateTickers, 1000);
  };

  // ---- Workload + crew -----------------------------------------------------
  const computeWorkload = (planText) => {
    const md = planText || '';
    const phases = (md.match(/^###\s+Phase\b/gim) || []).length
      || (md.match(/^##\s+Phases\b/im) ? 1 : 0);
    const tasks = (md.match(/^####\s+Task\b/gim) || []).length;
    const risks = (extractSections(md, RISK_NAMES).match(/^\s*[-*]\s+/gm) || []).length;
    const words = md ? md.trim().split(/\s+/).length : 0;
    return { phases, tasks, risks, words };
  };
  const renderWorkload = (wl) => {
    if (!elements.workloadChips) return;
    const chips = [
      { n: wl.phases, l: 'phases' },
      { n: wl.tasks, l: 'tasks' },
      { n: wl.risks, l: 'risks flagged' },
      { n: wl.words, l: 'plan words' }
    ];
    elements.workloadChips.textContent = '';
    chips.forEach((c) => {
      const chip = document.createElement('span'); chip.className = 'wchip';
      const num = document.createElement('span'); num.className = 'wnum'; num.textContent = c.n;
      const lab = document.createElement('span'); lab.className = 'wlabel'; lab.textContent = c.l;
      chip.append(num, lab); elements.workloadChips.appendChild(chip);
    });
  };

  // Agents available to hire = the council roster (kind+model), de-duplicated.
  let agentOptions = []; // [{id, kind, model}]
  let crewState = null;  // [{role, agent, model}] — null until first built
  const DEFAULT_ROLES = [
    { role: 'Engineer', prefer: ['codex', 'claude'] },
    { role: 'Reviewer', prefer: ['claude', 'gemini', 'codex'] },
    { role: 'Tester', prefer: ['gemini', 'claude'] }
  ];
  const buildAgentOptions = (state) => {
    const roster = Array.isArray(state.council) ? state.council : [];
    const seen = new Set();
    const opts = [];
    roster.forEach((m) => {
      const key = m.id;
      if (seen.has(key)) return; seen.add(key);
      opts.push({ id: m.id, kind: m.kind, model: m.model || '' });
    });
    if (!opts.length) opts.push({ id: 'claude', kind: 'claude', model: 'opus' });
    return opts;
  };
  const pickAgentFor = (prefer) => {
    for (const kind of prefer) {
      const hit = agentOptions.find((a) => a.kind === kind);
      if (hit) return hit;
    }
    return agentOptions[0];
  };
  const defaultCrew = () =>
    DEFAULT_ROLES.map(({ role, prefer }) => {
      const a = pickAgentFor(prefer);
      return { role, agent: a ? a.id : '', model: a ? a.model : '' };
    });
  const renderCrew = () => {
    if (!elements.crewTable) return;
    elements.crewTable.textContent = '';
    (crewState || []).forEach((member, idx) => {
      const row = document.createElement('div'); row.className = 'crew-row';
      // role
      const roleWrap = document.createElement('div'); roleWrap.className = 'crew-role';
      const role = document.createElement('input'); role.type = 'text'; role.value = member.role;
      role.addEventListener('input', () => { crewState[idx].role = role.value; });
      roleWrap.appendChild(role);
      // agent select
      const sel = document.createElement('select');
      agentOptions.forEach((a) => {
        const o = document.createElement('option'); o.value = a.id;
        o.textContent = `${a.id} (${a.kind})`; sel.appendChild(o);
      });
      sel.value = member.agent || (agentOptions[0] && agentOptions[0].id) || '';
      sel.addEventListener('change', () => {
        crewState[idx].agent = sel.value;
        const a = agentOptions.find((x) => x.id === sel.value);
        if (a && a.model) { crewState[idx].model = a.model; modelInput.value = a.model; }
        markCrewEdited();
      });
      // model input
      const modelInput = document.createElement('input'); modelInput.type = 'text';
      modelInput.placeholder = 'default'; modelInput.value = member.model || '';
      modelInput.addEventListener('input', () => { crewState[idx].model = modelInput.value; markCrewEdited(); });
      // remove
      const rm = document.createElement('button'); rm.className = 'rm'; rm.type = 'button'; rm.textContent = '✕';
      rm.title = 'Remove role';
      rm.addEventListener('click', () => { crewState.splice(idx, 1); renderCrew(); markCrewEdited(); });
      row.append(roleWrap, sel, modelInput, rm);
      elements.crewTable.appendChild(row);
    });
  };
  let crewEdited = false;
  const markCrewEdited = () => { crewEdited = true; setText(elements.crewStatus, 'crew edited — will apply on Execute'); };
  const syncCrew = (state) => {
    agentOptions = buildAgentOptions(state);
    if (!crewState) crewState = defaultCrew();
    else {
      // keep user edits, but repair agents that no longer exist
      crewState.forEach((m) => {
        if (!agentOptions.some((a) => a.id === m.agent)) {
          const a = agentOptions[0]; m.agent = a ? a.id : ''; if (a && !m.model) m.model = a.model;
        }
      });
    }
    renderCrew();
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

  // Wrap each <h2>…(until next h2) in a collapsible .md-section and add copy
  // buttons to code blocks, then re-apply any active search highlight.
  const decoratePreview = (el) => {
    if (!el) return;
    const nodes = Array.from(el.childNodes);
    const sections = [];
    let current = null;
    nodes.forEach((node) => {
      if (node.nodeType === 1 && node.tagName === 'H2') {
        current = document.createElement('div');
        current.className = 'md-section';
        sections.push(current);
        current.appendChild(node);
      } else if (current) {
        current.appendChild(node);
      } else {
        sections.push(node); // preamble before first h2
      }
    });
    if (sections.some((s) => s.classList && s.classList.contains('md-section'))) {
      el.textContent = '';
      sections.forEach((s) => el.appendChild(s));
      el.querySelectorAll('.md-section > h2').forEach((h) => {
        h.addEventListener('click', () => h.parentElement.classList.toggle('collapsed'));
      });
    }
    el.querySelectorAll('pre').forEach((pre) => {
      const btn = document.createElement('button');
      btn.className = 'copy-btn'; btn.type = 'button'; btn.textContent = 'copy';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const code = pre.querySelector('code');
        copyText(code ? code.textContent : pre.textContent);
        btn.textContent = 'copied'; btn.classList.add('ok');
        setTimeout(() => { btn.textContent = 'copy'; btn.classList.remove('ok'); }, 1200);
      });
      pre.appendChild(btn);
    });
    applySearchHighlight();
  };

  const renderPreview = (md) => {
    setMarkdown(elements.finalPlanPreview, md);
    decoratePreview(elements.finalPlanPreview);
  };

  const copyText = (text) => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
  };
  const fallbackCopy = (text) => {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    ta.remove();
  };

  // In-plan search: highlight matches in the preview text nodes.
  const applySearchHighlight = () => {
    const el = elements.finalPlanPreview;
    const q = (elements.planSearch && elements.planSearch.value || '').trim();
    if (!el) return;
    el.querySelectorAll('mark').forEach((m) => {
      const parent = m.parentNode; parent.replaceChild(document.createTextNode(m.textContent), m); parent.normalize();
    });
    if (!q || q.length < 2) return;
    const rx = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    const targets = [];
    let n;
    while ((n = walker.nextNode())) {
      if (n.parentNode && n.parentNode.closest && n.parentNode.closest('.copy-btn')) continue;
      if (rx.test(n.nodeValue)) targets.push(n);
    }
    let first = null;
    targets.forEach((node) => {
      const frag = document.createDocumentFragment();
      let last = 0; const s = node.nodeValue; rx.lastIndex = 0; let m;
      while ((m = rx.exec(s))) {
        if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
        const mark = document.createElement('mark'); mark.textContent = m[0]; frag.appendChild(mark);
        if (!first) first = mark;
        last = m.index + m[0].length;
        if (m.index === rx.lastIndex) rx.lastIndex++;
      }
      if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
    if (first && first.scrollIntoView) first.scrollIntoView({ block: 'center' });
  };

  const updateFinalPlan = (planText) => {
    latestFinalPlan = planText || '';
    if (!editorDirty) {
      elements.finalPlanEditor.value = latestFinalPlan;
    }
    renderPreview(latestFinalPlan);
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
    renderStepper(state.phase || '');
    updateTickers();
    renderWorkload(computeWorkload(state.final_plan || ''));
    syncCrew(state);

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
      const tone = message.payload?.status === 'failed' ? 'error' : 'success';
      if (action === 'save') {
        setStatus(elements.saveStatus, status);
      } else if (action === 'accept' || action === 'execute') {
        setStatus(elements.saveStatus, status);
        toast(action === 'execute' ? 'Executing' : 'Accepted', status, 'success');
        attemptCloseUi();
      } else if (action === 'refine') {
        setStatus(elements.refineStatus, status);
        toast(message.payload?.status === 'failed' ? 'Refine failed' : 'Refined', status, tone);
      } else if (action === 'keepalive') {
        setStatus(elements.keepOpenStatus, status);
      } else if (action === 'error') {
        toast('Error', status, 'error');
      }
      updateLastUpdated(message.payload?.timestamp);
      return;
    }
  };

  // SSE is the primary live channel, but if it ever drops we must not freeze
  // on the last frame: fall back to polling /api/state, and keep trying to
  // re-establish the stream. The poll is cleared the moment SSE reconnects.
  let evtSource = null;
  let pollTimer = null;
  let reconnectTimer = null;

  const startPolling = () => {
    if (pollTimer) return;
    pollTimer = window.setInterval(fetchState, 3000);
  };
  const stopPolling = () => {
    if (pollTimer) { window.clearInterval(pollTimer); pollTimer = null; }
  };

  const connectEvents = () => {
    if (!window.EventSource) {
      // No SSE at all — poll only.
      setConnection('polling');
      startPolling();
      fetchState();
      return;
    }
    try {
      if (evtSource) { evtSource.close(); }
    } catch (e) { /* ignore */ }

    const source = new EventSource(eventsEndpoint);
    evtSource = source;
    setConnection('connecting…');

    source.onopen = () => {
      setConnection('connected');
      stopPolling(); // live stream is back — no need to poll
      if (reconnectTimer) { window.clearTimeout(reconnectTimer); reconnectTimer = null; }
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
      // Keep the panel live via polling while the stream is down…
      startPolling();
      // …and rebuild the EventSource (the browser's auto-retry can wedge).
      try { source.close(); } catch (e) { /* ignore */ }
      if (!reconnectTimer) {
        reconnectTimer = window.setTimeout(() => { reconnectTimer = null; connectEvents(); }, 2500);
      }
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
      renderPreview(elements.finalPlanEditor.value);
      const status = editorDirty ? 'edited locally' : 'synced';
      setText(elements.finalPlanStatus, status);
    });
  }

  const doExecute = () => {
    const workload = computeWorkload(elements.finalPlanEditor.value);
    postAction(
      '/api/execute',
      { final_plan: elements.finalPlanEditor.value, crew: crewState || [], workload },
      elements.saveStatus
    );
    toast('Executing', 'Plan + crew handed off to /auxly-execute', 'success');
  };
  const doSave = () => {
    postAction('/api/save', { final_plan: elements.finalPlanEditor.value }, elements.saveStatus);
    toast('Saved', 'A numbered copy was written to the run folder');
  };
  const doRefine = () => {
    postAction(
      '/api/refine',
      { context: elements.refineContext.value, final_plan: elements.finalPlanEditor.value },
      elements.refineStatus
    );
    toast('Refining', 'The judge is revising the plan…');
  };

  if (elements.executeBtn) elements.executeBtn.addEventListener('click', doExecute);
  if (elements.saveBtn) elements.saveBtn.addEventListener('click', doSave);
  if (elements.refineBtn) elements.refineBtn.addEventListener('click', doRefine);

  if (elements.addCrewBtn) {
    elements.addCrewBtn.addEventListener('click', () => {
      if (!crewState) crewState = [];
      const a = agentOptions[0] || { id: '', model: '' };
      crewState.push({ role: 'New role', agent: a.id, model: a.model || '' });
      renderCrew(); markCrewEdited();
    });
  }

  // Copy whole plan
  if (elements.copyPlanBtn) {
    elements.copyPlanBtn.addEventListener('click', () => {
      copyText(elements.finalPlanEditor.value);
      toast('Copied', 'Full plan copied to clipboard', 'success');
    });
  }
  // Collapse / expand all sections
  if (elements.collapseAllBtn) {
    elements.collapseAllBtn.addEventListener('click', () => {
      const secs = elements.finalPlanPreview.querySelectorAll('.md-section');
      const anyOpen = Array.from(secs).some((s) => !s.classList.contains('collapsed'));
      secs.forEach((s) => s.classList.toggle('collapsed', anyOpen));
      elements.collapseAllBtn.textContent = anyOpen ? 'Expand all' : 'Collapse all';
    });
  }
  // In-plan search
  if (elements.planSearch) {
    elements.planSearch.addEventListener('input', () => applySearchHighlight());
  }

  // Drag-resize the editor / preview split
  if (elements.splitHandle && elements.editorGrid) {
    let dragging = false;
    const onMove = (e) => {
      if (!dragging) return;
      const rect = elements.editorGrid.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
      const ratio = Math.min(0.8, Math.max(0.2, x / rect.width));
      elements.editorGrid.style.setProperty('--split', `${ratio}fr`);
      elements.editorGrid.style.gridTemplateColumns = `${ratio}fr 6px ${1 - ratio}fr`;
    };
    const stop = () => { dragging = false; elements.splitHandle.classList.remove('drag'); document.body.style.userSelect = ''; };
    elements.splitHandle.addEventListener('mousedown', () => { dragging = true; elements.splitHandle.classList.add('drag'); document.body.style.userSelect = 'none'; });
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', stop);
  }

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    const inField = ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target.tagName || '').toUpperCase());
    const mod = e.metaKey || e.ctrlKey;
    // "/" focuses search when not typing in a field
    if (e.key === '/' && !inField) { e.preventDefault(); elements.planSearch && elements.planSearch.focus(); return; }
    // ⌘/Ctrl+Enter inside refine box → refine; elsewhere → execute
    if (mod && e.key === 'Enter') {
      e.preventDefault();
      if (e.target === elements.refineContext) doRefine();
      else doExecute();
      return;
    }
    if (mod && (e.key === 's' || e.key === 'S')) { e.preventDefault(); doSave(); return; }
    // ← / → switch council member when not typing
    if (!inField && (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && elements.plannerSelect) {
      const opts = Array.from(elements.plannerSelect.options).map((o) => o.value).filter(Boolean);
      if (opts.length > 1) {
        const i = opts.indexOf(selectedPlannerId);
        const next = e.key === 'ArrowRight' ? (i + 1) % opts.length : (i - 1 + opts.length) % opts.length;
        selectedPlannerId = opts[next];
        elements.plannerSelect.value = selectedPlannerId;
        elements.plannerSelect.dispatchEvent(new Event('change'));
      }
    }
  });

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
