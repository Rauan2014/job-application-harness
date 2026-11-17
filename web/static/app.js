/**
 * Candidata IA — Frontend Telemetry & Control Application
 * Atualizações reativas em tempo real via Server-Sent Events (SSE).
 */

const state = {
  activeTab: 'dashboard',
  runner: {
    status: 'idle',
    is_running: false,
    total_jobs: 0,
    completed_jobs: 0,
    remaining_jobs: 0,
    progress_percent: 0,
    elapsed_seconds: 0,
    active_jobs: [],
    run_tokens: { total: 0, prompt: 0, completion: 0 },
    logs: [],
  },
  metrics: {
    total_processed: 0,
    completed: 0,
    submitted_live: 0,
    dry_run: 0,
    needs_review: 0,
    failed: 0,
    skipped: 0,
    tokens: { total: 0, prompt: 0, completion: 0 },
    time: { total_duration_s: 0, avg_duration_s: 0, formatted_total: '0s' },
    top_resumes: [],
  },
  results: [],
  jobs: [],
  jobsSupabaseSynced: false,
  config: null,
  cookies: null,
  cloud: {
    mode: 'byok',
    authenticated: false,
    email: '',
    quota: null,
    error: null,
  },
  filterStatus: 'all',
  searchQuery: '',
  radarSearch: '',
  radarFilter: 'all',
  resultsViewMode: 'table',
  sortColumn: 'started_at',
  sortDirection: 'desc',
  autoScroll: true,
  logFilter: 'all',
  soundAlert: true,
  timerInterval: null,
  isConfigDirty: false,
};

// ==============================================================================
// 1. Inicialização e Ciclo de Vida
// ==============================================================================

document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initGuideBanner();
  initKeyboardShortcuts();
  initSSE();
  fetchAllData();

  // Polling de segurança a cada 8 segundos caso SSE oscile
  setInterval(() => {
    if (!state.runner.is_running) {
      fetchMetrics();
    }
  }, 8000);
});

function initNavigation() {
  document.querySelectorAll('.nav-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.target;
      switchTab(target);
    });
  });

  // Alternador de tema escuro / claro
  const themeBtn = document.getElementById('btn-toggle-theme');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme');
      const next = current === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    });
    const saved = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
  }
}

function initGuideBanner() {
  const hide = localStorage.getItem('hideGuide') === '1';
  const el = document.getElementById('guide-banner');
  if (el && hide) el.style.display = 'none';

  const savedSound = localStorage.getItem('soundAlert');
  if (savedSound !== null) {
    state.soundAlert = savedSound === '1';
    const btn = document.getElementById('btn-toggle-sound');
    if (btn) btn.classList.toggle('active', state.soundAlert);
  }
}

function initKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);

    // Esc fecha qualquer modal aberto
    if (e.key === 'Escape') {
      closeRunModal();
      closePasteCookiesModal();
      closeScreenshotModal();
      closeLoginChoiceModal();
      closeJobDetailsModal();
      return;
    }

    if (isInput) return;

    // Tecla '/' foca na busca de candidaturas
    if (e.key === '/') {
      e.preventDefault();
      switchTab('jobs');
      const searchInput = document.getElementById('input-search-jobs');
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
      }
      return;
    }

    // Teclas 1 a 5 alternam abas
    if (['1', '2', '3', '4', '5'].includes(e.key)) {
      const tabs = ['dashboard', 'jobs', 'config', 'screenshots', 'design-system'];
      const idx = parseInt(e.key, 10) - 1;
      if (tabs[idx]) switchTab(tabs[idx]);
    }
  });
}

function toggleGuideBanner() {
  const el = document.getElementById('guide-banner');
  if (!el) return;
  const isHidden = el.style.display === 'none';
  el.style.display = isHidden ? 'block' : 'none';
  localStorage.setItem('hideGuide', isHidden ? '0' : '1');
}

function dismissGuideBanner() {
  const el = document.getElementById('guide-banner');
  if (el) el.style.display = 'none';
  localStorage.setItem('hideGuide', '1');
}

function toggleSoundAlert() {
  state.soundAlert = !state.soundAlert;
  localStorage.setItem('soundAlert', state.soundAlert ? '1' : '0');
  const btn = document.getElementById('btn-toggle-sound');
  if (btn) {
    btn.classList.toggle('active', state.soundAlert);
    btn.title = state.soundAlert ? 'Alerta sonoro ativado' : 'Alerta sonoro silenciado';
  }
  if (state.soundAlert) playChime('success');
}

function playChime(type = 'success') {
  if (!state.soundAlert) return;
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);

    if (type === 'success') {
      // Tom duplo ascendente suave (C5 para G5)
      osc.frequency.setValueAtTime(523.25, now);
      osc.frequency.setValueAtTime(783.99, now + 0.12);
      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.45);
      osc.start(now);
      osc.stop(now + 0.45);
    } else if (type === 'attention') {
      // Tom de atenção âmbar (A4 para E5)
      osc.frequency.setValueAtTime(440, now);
      osc.frequency.setValueAtTime(659.25, now + 0.15);
      gain.gain.setValueAtTime(0.09, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
      osc.start(now);
      osc.stop(now + 0.5);
    }
  } catch (err) {
    // Ignora restrições do navegador
  }
}

function switchTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll('.nav-tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.target === tabId);
  });
  document.querySelectorAll('.tab-pane').forEach((p) => {
    p.classList.toggle('active', p.id === `tab-${tabId}`);
  });

  if (tabId === 'jobs') {
    if (state.isConfigDirty) {
      saveSettings();
    } else {
      fetchResults();
    }
  } else if (tabId === 'config') {
    fetchConfig();
    fetchCookies();
  } else if (tabId === 'screenshots') {
    renderScreenshotsGallery();
  }
}

// ==============================================================================
// 2. Conexão em Tempo Real (SSE)
// ==============================================================================

function initSSE() {
  const evtSource = new EventSource('/api/events');

  evtSource.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      const { event, data } = payload;
      const wasRunning = state.runner.is_running;

      if (event === 'init' || event === 'status_change') {
        state.runner = { ...state.runner, ...data };
        updateRunnerUI();

        // Alerta sonoro e toast fiel ao status final
        if (wasRunning && !state.runner.is_running) {
          stopLiveTimer();
          if (state.runner.status === 'error') {
            showToast('Execução encerrada com erro. Verifique os logs.', 'danger');
          } else if (state.runner.status === 'cancelled') {
            showToast('Execução interrompida pelo usuário.', 'warning');
          } else {
            playChime('success');
            showToast('Execução finalizada! Confira o status no painel.', 'success');
          }
          fetchMetrics();
        } else if (!wasRunning && state.runner.is_running) {
          startLiveTimer();
        }
      } else if (event === 'step_update') {
        state.runner = { ...state.runner, ...data };
        updateRunnerUI();
      } else if (event === 'progress_update') {
        state.runner = { ...state.runner, ...data };
        updateRunnerUI();
        fetchMetrics();
        if (state.activeTab === 'jobs') fetchResults();
      } else if (event === 'log') {
        appendLog(data);
      } else if (event === 'laya_event') {
        handleLayaLiveEvent(data);
      }
    } catch (err) {
      console.debug('Erro ao processar evento SSE:', err);
    }
  };

  evtSource.onerror = () => {
    console.debug('Reconectando SSE...');
  };
}

// ==============================================================================
// 3. Requisições à API
// ==============================================================================

async function fetchAllData() {
  await Promise.all([
    fetchStatus(),
    fetchMetrics(),
    fetchResults(),
    fetchJobs(),
    fetchConfig(),
    fetchCookies(),
    fetchCloudStatus(),
  ]);
}

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    if (res.ok) {
      const wasRunning = state.runner.is_running;
      state.runner = await res.json();
      updateRunnerUI();
      if (state.runner.logs) renderAllLogs(state.runner.logs);
      if (state.runner.is_running && !wasRunning) startLiveTimer();
    }
  } catch (err) {
    console.error('Erro ao buscar status:', err);
  }
}

async function fetchMetrics() {
  try {
    const res = await fetch('/api/metrics');
    if (res.ok) {
      state.metrics = await res.json();
      renderKPIs();
      updateLiveTimerUI();
    }
  } catch (err) {
    console.error('Erro ao buscar métricas:', err);
  }
}

async function fetchResults() {
  try {
    const res = await fetch('/api/results?limit=500');
    if (res.ok) {
      const data = await res.json();
      state.results = data.results || [];
      renderResultsTable();
      updateScreenshotBadge();
    }
  } catch (err) {
    console.error('Erro ao buscar resultados:', err);
  }
}

async function fetchJobs() {
  try {
    const res = await fetch('/api/jobs');
    if (res.ok) {
      const data = await res.json();
      state.jobs = data.jobs || [];
      state.jobsSupabaseSynced = Boolean(data.supabase_synced);
      renderRadarJobsGrid();
      populateLayaRealJobSelect();
    }
  } catch (err) {
    console.error('Erro ao buscar vagas:', err);
  }
}

async function fetchConfig() {
  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      state.config = await res.json();
      populateConfigForm();
    }
  } catch (err) {
    console.error('Erro ao buscar configurações:', err);
  }
}

async function fetchCookies() {
  try {
    const res = await fetch('/api/cookies');
    if (res.ok) {
      state.cookies = await res.json();
      renderCookiesUI();
    }
  } catch (err) {
    console.error('Erro ao buscar cookies:', err);
  }
}

// ==============================================================================
// 4. Live HUD & Cronômetro ao Vivo
// ==============================================================================

function startLiveTimer() {
  if (state.timerInterval) clearInterval(state.timerInterval);
  state.timerInterval = setInterval(() => {
    if (state.runner.is_running) {
      state.runner.elapsed_seconds = (state.runner.elapsed_seconds || 0) + 1;
      updateLiveTimerUI();
    }
  }, 1000);
}

function stopLiveTimer() {
  if (state.timerInterval) {
    clearInterval(state.timerInterval);
    state.timerInterval = null;
  }
}

function updateLiveTimerUI() {
  const elTimer = document.getElementById('hud-timer-val');
  const elEta = document.getElementById('hud-eta-val');
  const s = state.runner.elapsed_seconds || 0;

  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  const formatted = `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  if (elTimer) elTimer.textContent = formatted;

  if (elEta) {
    const rem = state.runner.remaining_jobs || 0;
    const avg = state.metrics?.time?.avg_duration_s || 0;
    if (!state.runner.is_running) {
      elEta.textContent = '• ETA: Parado';
    } else if (rem === 0) {
      elEta.textContent = '• ETA: Concluindo...';
    } else if (avg > 0) {
      const estSecs = Math.round(rem * avg);
      const estMins = Math.ceil(estSecs / 60);
      elEta.textContent = `• ETA: ~${estMins} min restantes`;
    } else {
      elEta.textContent = '• ETA: Calculando...';
    }
  }
}

function updateRunnerUI() {
  const r = state.runner;

  // Header status pill
  const statusPill = document.getElementById('header-status-pill');
  if (statusPill) {
    if (r.is_running) {
      statusPill.className = 'status-pill status-running';
      statusPill.innerHTML = '<span class="sonar-dot"></span> EM EXECUÇÃO';
    } else {
      statusPill.className = 'status-pill status-idle';
      statusPill.innerHTML = '<span class="badge-dot"></span> PRONTO';
    }
  }

  // Botões Iniciar / Parar
  const btnStart = document.getElementById('btn-start-automation');
  const btnStop = document.getElementById('btn-stop-automation');
  if (btnStart) btnStart.style.display = r.is_running ? 'none' : 'inline-flex';
  if (btnStop) btnStop.style.display = r.is_running ? 'inline-flex' : 'none';

  // Barra de progresso
  const barFill = document.getElementById('hud-progress-fill');
  const pctText = document.getElementById('hud-progress-pct');
  const statsText = document.getElementById('hud-progress-stats');

  if (barFill) barFill.style.width = `${r.progress_percent || 0}%`;
  if (pctText) pctText.textContent = `${r.progress_percent || 0}%`;
  if (statsText) {
    statsText.textContent = `${r.completed_jobs} concluídas de ${r.total_jobs} vagas (${r.remaining_jobs} restantes)`;
  }

  // Lista de navegadores em atividade
  const activeContainer = document.getElementById('hud-active-jobs');
  if (activeContainer) {
    if (r.active_jobs && r.active_jobs.length > 0) {
      activeContainer.innerHTML = r.active_jobs
        .map(
          (j) => `
        <div class="active-job-item">
          <div class="active-job-header">
            <span class="active-company">${escapeHtml(j.company)}</span>
            <span class="badge badge-info">${escapeHtml(j.ats || 'Web')}</span>
          </div>
          <div style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(j.title)}</div>
          <div class="active-step">
            <div class="spinner"></div>
            <span>${escapeHtml(j.current_step || 'Processando formulário...')}</span>
          </div>
        </div>
      `
        )
        .join('');
    } else {
      activeContainer.innerHTML = `
        <div style="font-size:0.85rem; color:var(--text-muted); padding:0.5rem 0;">
          ${r.is_running ? 'Iniciando instâncias de navegação e preparando fila...' : 'Nenhum motor de navegação ativo. Clique em "Iniciar Protocolo" para carregar a fila.'}
        </div>
      `;
    }
  }

  updateLiveTimerUI();
  syncLayaLiveUI(r.laya_live, r.is_running);
}

let _lastLayaFrameSeq = 0;

function _appendLayaFeedCard(feedEl, ev, clearDefault = false) {
  if (!feedEl) return;
  if (clearDefault || feedEl.dataset.empty === '1') {
    feedEl.innerHTML = '';
    delete feedEl.dataset.empty;
  }
  const kind = ['laya', 'llm', 'eeo', 'action'].includes(ev.kind) ? ev.kind : 'laya';
  const card = document.createElement('div');
  card.className = `laya-feed-item ${kind}`;
  card.innerHTML = `
    <div class="laya-feed-meta">
      <span>${escapeHtml(ev.title || 'LAYA')}</span>
      <span>${escapeHtml(ev.timestamp || '')}</span>
    </div>
    <div>${escapeHtml(ev.detail || '')}</div>
  `;
  feedEl.appendChild(card);
  while (feedEl.children.length > 50) {
    feedEl.removeChild(feedEl.firstChild);
  }
  feedEl.scrollTop = feedEl.scrollHeight;
}

function _applyLayaLiveFrame(frameSeq, hasFrame) {
  const img = document.getElementById('laya-live-img');
  const placeholder = document.getElementById('laya-live-placeholder');
  if (!img || !placeholder) return;
  if (frameSeq > 0 && hasFrame) {
    if (frameSeq !== _lastLayaFrameSeq) {
      _lastLayaFrameSeq = frameSeq;
      img.src = `/api/laya/live-frame?seq=${encodeURIComponent(frameSeq)}`;
    }
    img.style.display = 'block';
    placeholder.style.display = 'none';
  }
}

function syncLayaLiveUI(layaLive, isRunning) {
  const demoBtn = document.getElementById('btn-laya-demo');
  const realBtn = document.getElementById('btn-laya-real-run');
  if (demoBtn) demoBtn.disabled = Boolean(isRunning);
  if (realBtn) realBtn.disabled = Boolean(isRunning);
  if (!layaLive) return;

  const dot = document.getElementById('laya-live-dot');
  if (dot) dot.classList.toggle('active', Boolean(layaLive.active));

  const engineEl = document.getElementById('laya-live-engine');
  if (engineEl && layaLive.engine) engineEl.textContent = layaLive.engine;

  const urlEl = document.getElementById('laya-live-url');
  if (urlEl && layaLive.url) urlEl.textContent = layaLive.url;

  const jobEl = document.getElementById('laya-live-job');
  if (jobEl && (layaLive.company || layaLive.job_title)) {
    jobEl.textContent = [layaLive.company, layaLive.job_title].filter(Boolean).join(' · ');
  }

  const stats = layaLive.stats || {};
  const s1El = document.getElementById('laya-live-s1');
  const s2El = document.getElementById('laya-live-s2');
  const eeoEl = document.getElementById('laya-live-eeo');
  if (s1El && stats.s1 !== undefined) s1El.textContent = String(stats.s1);
  if (s2El && stats.s2 !== undefined) s2El.textContent = String(stats.s2);
  if (eeoEl && stats.eeo !== undefined) eeoEl.textContent = String(stats.eeo);

  _applyLayaLiveFrame(Number(layaLive.frame_seq || 0), Boolean(layaLive.has_frame));

  const feedEl = document.getElementById('laya-live-feed');
  if (feedEl && Array.isArray(layaLive.events) && layaLive.events.length > 0) {
    feedEl.innerHTML = '';
    delete feedEl.dataset.empty;
    layaLive.events.slice(-40).forEach((ev) => _appendLayaFeedCard(feedEl, ev, false));
  }
}

function handleLayaLiveEvent(ev) {
  if (!ev) return;
  const dot = document.getElementById('laya-live-dot');
  if (dot) dot.classList.add('active');

  const engineEl = document.getElementById('laya-live-engine');
  if (engineEl && ev.engine) engineEl.textContent = ev.engine;

  const urlEl = document.getElementById('laya-live-url');
  if (urlEl && ev.url) urlEl.textContent = ev.url;

  const jobEl = document.getElementById('laya-live-job');
  if (jobEl && (ev.company || ev.job_title)) {
    jobEl.textContent = [ev.company, ev.job_title].filter(Boolean).join(' · ');
  }

  const s1El = document.getElementById('laya-live-s1');
  const s2El = document.getElementById('laya-live-s2');
  const eeoEl = document.getElementById('laya-live-eeo');
  if (s1El) s1El.textContent = String(ev.s1 ?? 0);
  if (s2El) s2El.textContent = String(ev.s2 ?? 0);
  if (eeoEl) eeoEl.textContent = String(ev.eeo ?? 0);

  const seq = Number(ev.frame_seq || 0);
  _applyLayaLiveFrame(seq, seq > 0);

  const feedEl = document.getElementById('laya-live-feed');
  _appendLayaFeedCard(feedEl, ev, false);
}

function _resetLayaLiveViewer(title, detail) {
  const feedEl = document.getElementById('laya-live-feed');
  const img = document.getElementById('laya-live-img');
  const placeholder = document.getElementById('laya-live-placeholder');
  _lastLayaFrameSeq = 0;
  if (img) {
    img.removeAttribute('src');
    img.style.display = 'none';
  }
  if (placeholder) placeholder.style.display = '';
  const s1El = document.getElementById('laya-live-s1');
  const s2El = document.getElementById('laya-live-s2');
  const eeoEl = document.getElementById('laya-live-eeo');
  if (s1El) s1El.textContent = '0';
  if (s2El) s2El.textContent = '0';
  if (eeoEl) eeoEl.textContent = '0';
  if (feedEl) {
    feedEl.innerHTML = '';
    feedEl.dataset.empty = '1';
    _appendLayaFeedCard(
      feedEl,
      {
        title,
        detail,
        kind: 'laya',
        timestamp: '--:--:--',
      },
      true
    );
  }
}

function populateLayaRealJobSelect() {
  const selectEl = document.getElementById('laya-real-job-select');
  if (!selectEl) return;
  const previous = selectEl.value;
  const validJobs = (state.jobs || []).filter((j) => /^https?:\/\//i.test(String(j.url || '').trim()));

  const statusRank = (st) => {
    const norm = String(st || 'A Fazer').trim().toLowerCase();
    if (!norm || norm === 'a fazer') return 0;
    if (norm.includes('pronta') || norm.includes('revis') || norm.includes('falh')) return 1;
    return 2;
  };
  validJobs.sort((a, b) => statusRank(a.sheet_status) - statusRank(b.sheet_status));

  selectEl.innerHTML = '<option value="">-- Selecionar vaga real da fila ou colar URL --</option>';
  validJobs.slice(0, 120).forEach((j) => {
    const opt = document.createElement('option');
    opt.value = String(j.id);
    const atsLabel = j.ats || 'Web';
    const fitLabel = j.fit_score != null ? ` (${j.fit_score}%)` : '';
    const stLabel = j.sheet_status || 'A Fazer';
    opt.textContent = `[${stLabel}] ${j.company || 'Empresa'} — ${j.title || 'Vaga'} (${atsLabel})${fitLabel}`;
    selectEl.appendChild(opt);
  });
  if (previous && validJobs.some((j) => String(j.id) === previous)) {
    selectEl.value = previous;
  }
}

function onLayaRealSelectChange() {
  const selectEl = document.getElementById('laya-real-job-select');
  const urlEl = document.getElementById('laya-real-url-input');
  if (selectEl && urlEl && selectEl.value) {
    urlEl.value = '';
  }
}

async function startLayaRealJob() {
  const jobId = document.getElementById('laya-real-job-select')?.value?.trim() || '';
  const customUrl = document.getElementById('laya-real-url-input')?.value?.trim() || '';
  const dryRun = Boolean(document.getElementById('laya-real-dryrun')?.checked);
  const forceGeneric = Boolean(document.getElementById('laya-real-force-generic')?.checked);
  const headed = Boolean(document.getElementById('laya-demo-headed')?.checked);

  if (!jobId && !customUrl) {
    showToast('Selecione uma vaga da lista ou cole uma URL válida (https://...).', 'warning');
    return;
  }

  _resetLayaLiveViewer(
    dryRun ? 'PREPARANDO DRY-RUN REAL' : 'PREPARANDO CANDIDATURA REAL',
    customUrl ? `Abrindo URL: ${customUrl}` : `Carregando vaga #${jobId} da fila real...`
  );

  try {
    const res = await fetch('/api/laya/run-real', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: customUrl ? '' : jobId,
        custom_url: customUrl || '',
        dry_run: dryRun,
        headless: !headed,
        laya_navigator: true,
        force_laya_generic: forceGeneric,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(
        dryRun
          ? 'Dry-Run real iniciado ao vivo! Acompanhe abaixo.'
          : 'Candidatura real iniciada ao vivo! Acompanhe abaixo.',
        'success'
      );
      startLiveTimer();
    } else {
      showToast(`Erro ao iniciar vaga real: ${data.detail || data.error || 'Ocupado'}`, 'danger');
    }
  } catch (err) {
    showToast(`Erro de comunicação: ${err.message}`, 'danger');
  }
}

async function startLayaVisualDemo() {
  const headed = Boolean(document.getElementById('laya-demo-headed')?.checked);
  _resetLayaLiveViewer(
    'INICIALIZANDO SANDBOX',
    'Abrindo portal de demonstração sem seletores de ATS...'
  );

  try {
    const res = await fetch('/api/laya/demo-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        headless: !headed,
        mode: 'auto',
        delay_ms: 850,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast('Sandbox visual da Laya em execução!', 'success');
      startLiveTimer();
    } else {
      showToast(`Não foi possível iniciar o sandbox: ${data.detail || data.error || 'Ocupado'}`, 'danger');
    }
  } catch (err) {
    showToast(`Erro de comunicação ao iniciar sandbox: ${err.message}`, 'danger');
  }
}

function renderKPIs() {
  const m = state.metrics;

  const elResumes = document.getElementById('kpi-resumes-sent');
  if (elResumes) {
    elResumes.textContent = `${m.submitted_live} reais`;
    const sub = document.getElementById('kpi-resumes-sub');
    if (sub) sub.textContent = `+ ${m.dry_run} em simulação protegida (Total: ${m.completed})`;
  }

  const elTime = document.getElementById('kpi-time-total');
  if (elTime) {
    elTime.textContent = m.time.formatted_total;
    const sub = document.getElementById('kpi-time-sub');
    if (sub) sub.textContent = `Média: ${m.time.avg_duration_s}s por formulário`;
  }

  const elTokens = document.getElementById('kpi-tokens-total');
  if (elTokens) {
    elTokens.textContent = Number(m.tokens.total).toLocaleString('pt-BR');
    const sub = document.getElementById('kpi-tokens-sub');
    if (sub) {
      sub.textContent = `${Number(m.tokens.prompt).toLocaleString('pt-BR')} entrada / ${Number(m.tokens.completion).toLocaleString('pt-BR')} saída`;
    }
  }

  const elReview = document.getElementById('kpi-review-total');
  if (elReview) {
    elReview.textContent = `${m.needs_review} vagas`;
    const sub = document.getElementById('kpi-review-sub');
    if (sub) sub.textContent = 'Pausadas para verificação ética';
  }

  const elFailed = document.getElementById('kpi-failed-total');
  if (elFailed) {
    elFailed.textContent = `${m.failed} erros`;
    const sub = document.getElementById('kpi-failed-sub');
    if (sub) sub.textContent = `${m.skipped || 0} vagas descartadas no filtro`;
  }

  updateScreenshotBadge();
}

function updateScreenshotBadge() {
  const badge = document.getElementById('tab-badge-screenshots');
  if (!badge) return;
  const count = state.results.filter((r) => r.screenshot_url).length;
  if (count > 0) {
    badge.textContent = count;
    badge.style.display = 'inline-block';
  } else {
    badge.style.display = 'none';
  }
}

// ==============================================================================
// 5. Console de Logs & Ferramentas
// ==============================================================================

function appendLog(entry) {
  const terminal = document.getElementById('terminal-logs');
  if (!terminal) return;

  if (state.logFilter === 'errors' && !['WARNING', 'ERROR'].includes(entry.level)) {
    return;
  }

  const line = document.createElement('div');
  line.className = 'log-line';

  let colorClass = 'log-info';
  if (entry.level === 'WARNING') colorClass = 'log-warn';
  if (entry.level === 'ERROR') colorClass = 'log-error';

  line.innerHTML = `
    <span class="log-time">[${entry.timestamp}]</span>
    <span class="${colorClass}">[${entry.level}]</span>
    <span>${escapeHtml(entry.message)}</span>
  `;
  terminal.appendChild(line);

  if (state.autoScroll) {
    terminal.scrollTop = terminal.scrollHeight;
  }
}

function renderAllLogs(logs) {
  const terminal = document.getElementById('terminal-logs');
  if (!terminal) return;
  terminal.innerHTML = '';
  logs.forEach(appendLog);
}

function copyConsoleLogs() {
  const terminal = document.getElementById('terminal-logs');
  if (!terminal || !terminal.innerText.trim()) {
    showToast('Nenhum log para copiar.', 'info');
    return;
  }
  navigator.clipboard.writeText(terminal.innerText).then(() => {
    showToast('Logs copiados para a área de transferência!', 'success');
  }).catch(() => {
    showToast('Falha ao copiar logs.', 'danger');
  });
}

function clearConsoleLogs() {
  const terminal = document.getElementById('terminal-logs');
  if (terminal) terminal.innerHTML = '';
  state.runner.logs = [];
  showToast('Console de operações limpo.', 'info');
}

function toggleAutoScroll() {
  state.autoScroll = !state.autoScroll;
  const btn = document.getElementById('btn-toggle-autoscroll');
  if (btn) {
    btn.classList.toggle('active', state.autoScroll);
    btn.textContent = state.autoScroll ? 'Auto-Scroll' : 'Scroll Pausado';
  }
}

function toggleLogSeverityFilter() {
  state.logFilter = state.logFilter === 'all' ? 'errors' : 'all';
  const btn = document.getElementById('btn-toggle-log-filter');
  if (btn) {
    btn.classList.toggle('active', state.logFilter === 'errors');
    btn.textContent = state.logFilter === 'errors' ? 'Apenas Avisos & Erros' : 'Todos os Logs';
  }
  renderAllLogs(state.runner.logs || []);
}

// ==============================================================================
// 6. Candidaturas & Grid de Vagas (Radar, Tabela, Busca, Filtros & Exportação)
// ==============================================================================

function _normalizeJobBadgeMeta(item) {
  const rawStatus = (item.status || '').toUpperCase();
  const sheetSt = (item.sheet_status || '').toLowerCase();
  if (rawStatus === 'COMPLETED' || sheetSt.includes('candidat') || sheetSt.includes('pronta')) {
    return { badgeClass: 'badge-success', label: item.display_status || item.sheet_status || 'Concluída' };
  }
  if (rawStatus === 'NEEDS_REVIEW' || sheetSt.includes('revis')) {
    return { badgeClass: 'badge-warning', label: item.display_status || item.sheet_status || 'Revisar' };
  }
  if (rawStatus === 'FAILED' || sheetSt.includes('falh')) {
    return { badgeClass: 'badge-danger', label: item.display_status || item.sheet_status || 'Falhou' };
  }
  if (rawStatus === 'SKIPPED' || sheetSt.includes('ignor')) {
    return { badgeClass: 'badge-neutral', label: item.display_status || item.sheet_status || 'Ignorada' };
  }
  return { badgeClass: 'badge-info', label: item.display_status || item.sheet_status || 'A Fazer' };
}

function buildJobGridCardHtml(item) {
  const jobId = item.job_id || item.id || '';
  const safeId = escapeHtml(jobId);
  const { badgeClass, label } = _normalizeJobBadgeMeta(item);
  const atsLabel = item.ats || item.source_platform || 'Portal';
  const isSupabase = Boolean(item.supabase_id || item.source_platform === 'supabase');
  const fitScore = item.fit_score != null && item.fit_score !== '' ? Number(item.fit_score) : null;
  const fitBadgeClass =
    fitScore == null ? '' : fitScore >= 80 ? 'badge-success' : fitScore >= 60 ? 'badge-warning' : 'badge-neutral';
  const skills = Array.isArray(item.matched_skills) ? item.matched_skills.slice(0, 4) : [];
  const metaParts = [item.modality, item.location, item.date].filter((v) => v && String(v).trim());

  return `
    <div class="job-grid-card" data-job-id="${safeId}" onclick="openJobDetailsModal('${safeId}')">
      <div>
        <div class="job-grid-top">
          <div>
            <div class="job-grid-company">${escapeHtml(item.company || 'Empresa')}</div>
            <div class="job-grid-title">${escapeHtml(item.title || 'Cargo')}</div>
          </div>
          ${
            fitScore != null
              ? `<span class="badge ${fitBadgeClass}" title="Score de compatibilidade">${fitScore}% Fit</span>`
              : `<span class="badge badge-neutral">${escapeHtml(atsLabel)}</span>`
          }
        </div>

        <div class="job-grid-meta" style="margin-top:0.55rem;">
          <span class="badge ${badgeClass}" style="font-size:0.68rem;">
            <span class="badge-dot"></span> ${escapeHtml(label)}
          </span>
          <span class="badge badge-neutral" style="font-size:0.68rem;">${escapeHtml(atsLabel)}</span>
          ${
            isSupabase
              ? `<span class="badge badge-info" style="font-size:0.68rem;" title="Sincronizada com Supabase">Supabase</span>`
              : ''
          }
        </div>

        ${
          metaParts.length > 0
            ? `<div class="job-grid-meta" style="margin-top:0.45rem;">${metaParts.map((m) => `<span>${escapeHtml(String(m))}</span>`).join('<span>&bull;</span>')}</div>`
            : ''
        }

        ${
          skills.length > 0
            ? `<div class="job-grid-skills" style="margin-top:0.55rem;">${skills.map((s) => `<span class="job-skill-chip">${escapeHtml(s)}</span>`).join('')}</div>`
            : ''
        }

        ${
          item.reason
            ? `<div style="margin-top:0.5rem; font-size:0.74rem; color:var(--text-secondary); line-height:1.4; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;" title="${escapeHtml(item.reason)}">${escapeHtml(item.reason)}</div>`
            : ''
        }
      </div>

      <div class="job-grid-actions" onclick="event.stopPropagation()">
        <button type="button" class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.74rem;" onclick="openJobDetailsModal('${safeId}')">
          Detalhes
        </button>
        ${
          item.tailored_resume_url
            ? `<a href="${sanitizeUrl(item.tailored_resume_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.74rem; text-decoration:none; border-color:rgba(16,185,129,0.45); color:var(--accent-success);">CV PDF</a>`
            : `<button type="button" class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.74rem;" onclick="tailorResumeForJob('${safeId}')">Gerar CV</button>`
        }
        <button type="button" class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.74rem;" onclick="runSingleJob('${safeId}', true)">
          Simular
        </button>
        ${
          item.url
            ? `<a href="${sanitizeUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.74rem; text-decoration:none;" title="Abrir link da vaga">Abrir</a>`
            : ''
        }
      </div>
    </div>
  `;
}

function handleRadarJobsFilter() {
  state.radarSearch = (document.getElementById('radar-jobs-search')?.value || '').trim().toLowerCase();
  state.radarFilter = document.getElementById('radar-jobs-filter')?.value || 'all';
  renderRadarJobsGrid();
}

function renderRadarJobsGrid() {
  const gridEl = document.getElementById('radar-jobs-grid');
  const countEl = document.getElementById('radar-jobs-count');
  const sbBadge = document.getElementById('radar-supabase-badge');
  if (!gridEl) return;

  const allJobs = Array.isArray(state.jobs) ? state.jobs : [];
  if (sbBadge) {
    sbBadge.style.display = state.jobsSupabaseSynced ? 'inline-flex' : 'none';
    sbBadge.className = state.jobsSupabaseSynced ? 'badge badge-success' : 'badge badge-neutral';
    sbBadge.textContent = state.jobsSupabaseSynced ? 'Supabase Sync Ativo' : 'Somente Local';
  }

  const query = state.radarSearch || '';
  const filterMode = state.radarFilter || 'all';

  const filtered = allJobs.filter((j) => {
    const st = (j.sheet_status || 'A Fazer').toLowerCase();
    if (filterMode === 'todo' && !st.includes('fazer') && st !== '') return false;
    if (filterMode === 'supabase' && !j.supabase_id && j.source_platform !== 'supabase') return false;
    if (filterMode === 'done' && (st.includes('fazer') || st === '')) return false;

    if (query) {
      const hay = [
        j.company,
        j.title,
        j.ats,
        j.source_platform,
        j.modality,
        j.location,
        ...(Array.isArray(j.matched_skills) ? j.matched_skills : []),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });

  if (countEl) {
    countEl.textContent = `${filtered.length} ${filtered.length === 1 ? 'vaga' : 'vagas'}`;
  }

  if (filtered.length === 0) {
    gridEl.innerHTML = `
      <div style="grid-column:1/-1; text-align:center; padding:1.75rem; color:var(--text-muted); font-size:0.84rem;">
        ${
          allJobs.length === 0
            ? 'Nenhuma vaga na fila ainda. Digite o cargo acima e clique em <strong>Buscar &amp; Enfileirar Vagas</strong> ou em <strong>Sincronizar Supabase</strong>.'
            : 'Nenhuma vaga corresponde ao filtro digitado.'
        }
      </div>
    `;
    return;
  }

  gridEl.innerHTML = filtered.slice(0, 60).map(buildJobGridCardHtml).join('');
}

function setResultsViewMode(mode) {
  state.resultsViewMode = mode === 'grid' ? 'grid' : 'table';
  const btnTable = document.getElementById('btn-results-view-table');
  const btnGrid = document.getElementById('btn-results-view-grid');
  const tableWrap = document.getElementById('results-table-wrap');
  const gridWrap = document.getElementById('results-grid-view');
  if (btnTable) btnTable.classList.toggle('active', state.resultsViewMode === 'table');
  if (btnGrid) btnGrid.classList.toggle('active', state.resultsViewMode === 'grid');
  if (tableWrap) tableWrap.style.display = state.resultsViewMode === 'table' ? 'block' : 'none';
  if (gridWrap) gridWrap.style.display = state.resultsViewMode === 'grid' ? 'grid' : 'none';
  renderResultsTable();
}

function renderResultsTable() {
  const container = document.getElementById('results-table-body');
  const gridContainer = document.getElementById('results-grid-view');
  if (!container) return;

  const query = state.searchQuery.toLowerCase();
  const filter = state.filterStatus;

  // Atualiza contadores dos botões de filtro
  updateFilterCounters();

  let filtered = state.results.filter((item) => {
    if (filter !== 'all' && item.status !== filter) return false;
    if (query) {
      const match =
        (item.company || '').toLowerCase().includes(query) ||
        (item.title || '').toLowerCase().includes(query) ||
        (item.resume_used || '').toLowerCase().includes(query) ||
        (item.reason || '').toLowerCase().includes(query);
      if (!match) return false;
    }
    return true;
  });

  // Ordenação por coluna
  if (state.sortColumn) {
    filtered.sort((a, b) => {
      let valA = a[state.sortColumn];
      let valB = b[state.sortColumn];
      if (valA == null) valA = '';
      if (valB == null) valB = '';

      if (typeof valA === 'number' && typeof valB === 'number') {
        return state.sortDirection === 'asc' ? valA - valB : valB - valA;
      }
      const cmp = String(valA).localeCompare(String(valB), 'pt-BR', { numeric: true });
      return state.sortDirection === 'asc' ? cmp : -cmp;
    });
  }

  // Atualiza ícones nos cabeçalhos ordenáveis
  updateSortIcons();

  if (gridContainer) {
    gridContainer.innerHTML =
      filtered.length === 0
        ? '<div style="grid-column:1/-1; text-align:center; padding:2.5rem; color:var(--text-muted);">Nenhuma candidatura encontrada com os filtros selecionados.</div>'
        : filtered.map(buildJobGridCardHtml).join('');
  }

  if (filtered.length === 0) {
    container.innerHTML = `
      <tr>
        <td colspan="7" style="text-align:center; padding:2.5rem; color:var(--text-muted);">
          Nenhuma candidatura encontrada com os filtros selecionados.
        </td>
      </tr>
    `;
    return;
  }

  container.innerHTML = filtered
    .map((item) => {
      let badgeClass = 'badge-neutral';
      if (item.status === 'PENDING') badgeClass = 'badge-info';
      if (item.status === 'COMPLETED') badgeClass = 'badge-success';
      if (item.status === 'NEEDS_REVIEW') badgeClass = 'badge-warning';
      if (item.status === 'FAILED') badgeClass = 'badge-danger';
      if (item.status === 'SKIPPED') badgeClass = 'badge-neutral';

      const durationStr = item.duration_s != null ? `${item.duration_s}s` : '-';
      const tokensStr = item.tokens_used ? Number(item.tokens_used).toLocaleString('pt-BR') : '0';

      return `
      <tr onclick="openJobDetailsModal('${escapeHtml(item.job_id)}')" title="Clique para ver auditoria completa">
        <td>
          <div style="font-weight:600;">${escapeHtml(item.company)}</div>
          <div style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(item.title)}</div>
        </td>
        <td>
          <span class="badge ${badgeClass}"><span class="badge-dot"></span> ${escapeHtml(item.display_status || item.status)}</span>
        </td>
        <td>
          <div style="display:flex; align-items:center; gap:0.4rem; font-size:0.825rem;">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--mint); flex-shrink:0;"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>
            <span style="max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:var(--font-mono); font-size:0.8rem;" title="${escapeHtml(item.resume_used || '')}">
              ${escapeHtml(item.resume_used || 'Padrão')}
            </span>
          </div>
        </td>
        <td>
          <span style="font-family:var(--font-mono); font-size:0.825rem;">${durationStr}</span>
        </td>
        <td>
          <span style="font-family:var(--font-mono); font-size:0.825rem; color:var(--cyan);">${tokensStr}</span>
        </td>
        <td>
          <span style="font-size:0.8rem; color:var(--text-secondary); max-width:280px; display:inline-block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(item.reason || '')}">
            ${escapeHtml(item.reason || 'Processamento concluído')}
          </span>
        </td>
        <td onclick="event.stopPropagation()">
          <div style="display:flex; gap:0.35rem; flex-wrap:wrap;">
            <button class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.75rem;" onclick="openJobDetailsModal('${escapeHtml(item.job_id)}')">
              Detalhes
            </button>
            ${
              item.tailored_resume_url
                ? `<a href="${sanitizeUrl(item.tailored_resume_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.75rem; text-decoration:none; border-color:rgba(16,185,129,0.45); color:var(--accent-success);" title="Abrir currículo PDF gerado para esta vaga">CV PDF</a>`
                : `<button class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.75rem;" onclick="tailorResumeForJob('${escapeHtml(item.job_id)}')" title="Gerar currículo ATS em PDF adaptado para esta vaga">Gerar CV</button>`
            }
            <button class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.75rem;" onclick="runSingleJob('${escapeHtml(item.job_id)}', true)" title="Executar simulação (dry-run) desta vaga">
              Simular
            </button>
            ${
              item.screenshot_url
                ? `<button class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.75rem;" onclick="viewScreenshot('${escapeHtml(item.screenshot_url).replace(/'/g, "\\'")}', '${escapeHtml(item.company).replace(/'/g, "\\'")}')"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:2px;"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/></svg> Tela</button>`
                : ''
            }
            ${
              item.url
                ? `<a href="${sanitizeUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="padding:0.25rem 0.55rem; font-size:0.75rem; text-decoration:none;" title="Abrir link da vaga"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" x2="21" y1="14" y2="3"/></svg></a>`
                : ''
            }
          </div>
        </td>
      </tr>
    `;
    })
    .join('');
}

function updateFilterCounters() {
  const counts = {
    all: state.results.length,
    PENDING: 0,
    COMPLETED: 0,
    NEEDS_REVIEW: 0,
    FAILED: 0,
    SKIPPED: 0,
  };

  state.results.forEach((r) => {
    if (counts[r.status] !== undefined) counts[r.status]++;
  });

  setElText('count-filter-all', counts.all);
  setElText('count-filter-pending', counts.PENDING);
  setElText('count-filter-completed', counts.COMPLETED);
  setElText('count-filter-review', counts.NEEDS_REVIEW);
  setElText('count-filter-failed', counts.FAILED);
  setElText('count-filter-skipped', counts.SKIPPED);
}

function setElText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function sortTable(column) {
  if (state.sortColumn === column) {
    state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    state.sortColumn = column;
    state.sortDirection = ['duration_s', 'tokens_used'].includes(column) ? 'desc' : 'asc';
  }
  renderResultsTable();
}

function updateSortIcons() {
  const cols = ['company', 'status', 'resume_used', 'duration_s', 'tokens_used'];
  cols.forEach((c) => {
    const icon = document.getElementById(`sort-${c}`);
    if (icon) {
      if (state.sortColumn === c) {
        icon.textContent = state.sortDirection === 'asc' ? '↑' : '↓';
      } else {
        icon.textContent = '↕';
      }
    }
  });
}

function setFilterStatus(status) {
  state.filterStatus = status;
  document.querySelectorAll('.filter-pill').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.status === status);
  });
  renderResultsTable();
}

function handleSearch(val) {
  state.searchQuery = val;
  const clearBtn = document.getElementById('btn-clear-search');
  if (clearBtn) clearBtn.style.display = val ? 'flex' : 'none';
  renderResultsTable();
}

function clearSearch() {
  const input = document.getElementById('input-search-jobs');
  if (input) input.value = '';
  state.searchQuery = '';
  const clearBtn = document.getElementById('btn-clear-search');
  if (clearBtn) clearBtn.style.display = 'none';
  renderResultsTable();
}

function exportResultsCSV() {
  if (!state.results || state.results.length === 0) {
    showToast('Nenhuma candidatura para exportar.', 'info');
    return;
  }

  const headers = [
    'Empresa',
    'Cargo',
    'ATS',
    'Status',
    'Currículo Enviado',
    'Duração (s)',
    'Tokens Total',
    'Tokens Prompt',
    'Tokens Completion',
    'Motivo / Diagnóstico',
    'Link da Vaga',
    'Iniciado Em',
    'Concluído Em',
  ];

  const rows = state.results.map((r) => [
    `"${(r.company || '').replace(/"/g, '""')}"`,
    `"${(r.title || '').replace(/"/g, '""')}"`,
    `"${(r.ats || '').replace(/"/g, '""')}"`,
    `"${(r.display_status || r.status || '').replace(/"/g, '""')}"`,
    `"${(r.resume_used || '').replace(/"/g, '""')}"`,
    r.duration_s != null ? r.duration_s : '',
    r.tokens_used || 0,
    r.prompt_tokens || 0,
    r.completion_tokens || 0,
    `"${(r.reason || '').replace(/"/g, '""')}"`,
    `"${(r.url || '').replace(/"/g, '""')}"`,
    `"${(r.started_at || '').replace(/"/g, '""')}"`,
    `"${(r.completed_at || '').replace(/"/g, '""')}"`,
  ]);

  const csvContent = '\uFEFF' + [headers.join(';'), ...rows.map((row) => row.join(';'))].join('\r\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', `candidatas_protocol_${new Date().toISOString().slice(0, 10)}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  showToast('Planilha CSV exportada com sucesso!', 'success');
}

// ==============================================================================
// 7. Modal de Auditoria Detalhada da Vaga
// ==============================================================================

function openJobDetailsModal(jobId) {
  let item = state.results.find((r) => r.job_id === jobId);
  if (!item) {
    const jobFallback = state.jobs.find((j) => j.id === jobId);
    if (!jobFallback) return;
    item = {
      job_id: jobFallback.id,
      company: jobFallback.company,
      title: jobFallback.title,
      ats: jobFallback.ats,
      url: jobFallback.url,
      status: 'PENDING',
      display_status: jobFallback.sheet_status || 'A Fazer',
      resume_used: jobFallback.resume_label || 'Padrão',
      tailored_resume_url: jobFallback.tailored_resume_url,
      matched_skills: jobFallback.matched_skills || [],
      reason: jobFallback.fit_score != null ? `Compatibilidade estimada: ${jobFallback.fit_score}%` : 'Aguardando execução na fila.',
      duration_s: null,
      tokens_used: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
    };
  }

  const modal = document.getElementById('modal-job-details');
  const titleEl = document.getElementById('job-detail-title');
  const contentEl = document.getElementById('job-detail-content');

  if (titleEl) {
    titleEl.textContent = `${item.company} — ${item.title}`;
  }

  if (contentEl) {
    contentEl.innerHTML = `
      <div class="detail-grid">
        <div class="detail-card">
          <div class="detail-label">Status da Candidatura</div>
          <div class="detail-val">
            <span class="badge ${item.status === 'COMPLETED' ? 'badge-success' : item.status === 'FAILED' ? 'badge-danger' : 'badge-warning'}">
              ${escapeHtml(item.display_status || item.status)}
            </span>
          </div>
        </div>

        <div class="detail-card">
          <div class="detail-label">Currículo Utilizado</div>
          <div class="detail-val-mono">${escapeHtml(item.resume_used || 'Padrão')}</div>
          ${
            item.tailored_resume_url
              ? `<div style="margin-top:0.45rem;"><a href="${sanitizeUrl(item.tailored_resume_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="padding:0.25rem 0.6rem; font-size:0.75rem; text-decoration:none; color:var(--accent-success);">Visualizar PDF Gerado</a></div>`
              : `<div style="margin-top:0.45rem;"><button class="btn btn-secondary" style="padding:0.25rem 0.6rem; font-size:0.75rem;" onclick="tailorResumeForJob('${escapeHtml(item.job_id)}', true)">Gerar PDF Sob Medida</button></div>`
          }
        </div>

        <div class="detail-card">
          <div class="detail-label">Tempo de Execução</div>
          <div class="detail-val-mono">${item.duration_s != null ? item.duration_s + 's' : 'N/A'}</div>
        </div>

        <div class="detail-card">
          <div class="detail-label">Consumo de Tokens</div>
          <div class="detail-val-mono">${item.tokens_used ? Number(item.tokens_used).toLocaleString('pt-BR') : '0'} tokens</div>
          <div style="font-size:0.7rem; color:var(--text-muted); margin-top:2px;">
            ${item.prompt_tokens || 0} prompt / ${item.completion_tokens || 0} resposta
          </div>
        </div>
      </div>

      <div class="card" style="margin-bottom:1rem; padding:1rem;">
        <div class="detail-label">Diagnóstico do Pipeline & Raciocínio Ético</div>
        <p style="font-size:0.85rem; color:var(--text-pure); margin-top:0.3rem; line-height:1.5;">
          ${escapeHtml(item.reason || 'Processamento sem ocorrências registradas.')}
        </p>
        ${
          Array.isArray(item.matched_skills) && item.matched_skills.length > 0
            ? `<div style="margin-top:0.75rem;"><div class="detail-label" style="margin-bottom:0.35rem;">Skills Verificadas em Destaque</div><div style="display:flex; flex-wrap:wrap; gap:0.35rem;">${item.matched_skills.map((s) => `<span class="badge badge-info">${escapeHtml(s)}</span>`).join('')}</div></div>`
            : ''
        }
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:0.85rem;">
          <button class="btn btn-secondary" style="font-size:0.78rem;" onclick="tailorResumeForJob('${escapeHtml(item.job_id)}', true)">Regerar CV em PDF</button>
          <button class="btn btn-primary" style="font-size:0.78rem;" onclick="closeJobDetailsModal(); runSingleJob('${escapeHtml(item.job_id)}', true);">Simular Candidatura (Dry Run)</button>
        </div>
      </div>

      <div class="detail-grid">
        <div class="detail-card">
          <div class="detail-label">Plataforma ATS</div>
          <div class="detail-val">${escapeHtml(item.ats || 'Desconhecido')}</div>
        </div>

        <div class="detail-card">
          <div class="detail-label">Iniciado Em</div>
          <div class="detail-val-mono" style="font-size:0.75rem;">${escapeHtml(item.started_at || '-')}</div>
        </div>

        <div class="detail-card">
          <div class="detail-label">Concluído Em</div>
          <div class="detail-val-mono" style="font-size:0.75rem;">${escapeHtml(item.completed_at || '-')}</div>
        </div>

        <div class="detail-card">
          <div class="detail-label">Link Original</div>
          <div class="detail-val">
            ${
              item.url
                ? `<a href="${sanitizeUrl(item.url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="padding:0.2rem 0.5rem; font-size:0.75rem; text-decoration:none;">Abrir Vaga ↗</a>`
                : 'Não disponível'
            }
          </div>
        </div>
      </div>

      ${
        item.screenshot_url
          ? `
        <div style="margin-top:1rem; text-align:center;">
          <div class="detail-label" style="text-align:left; margin-bottom:0.5rem;">Captura da Tela pelo Robô</div>
          <img src="${sanitizeUrl(item.screenshot_url)}" alt="Screenshot" style="max-width:100%; max-height:300px; border-radius:var(--radius-sm); border:1px solid var(--border-subtle); cursor:pointer;" onclick="viewScreenshot('${escapeHtml(item.screenshot_url).replace(/'/g, "\\'")}', '${escapeHtml(item.company).replace(/'/g, "\\'")}')" title="Clique para ampliar em tela cheia" />
        </div>
      `
          : ''
      }
    `;
  }

  if (modal) modal.classList.add('active');
}

function closeJobDetailsModal() {
  const modal = document.getElementById('modal-job-details');
  if (modal) modal.classList.remove('active');
}

// ==============================================================================
// 8. Evidências & Lightbox de Capturas de Tela
// ==============================================================================

function viewScreenshot(url, company) {
  const modal = document.getElementById('screenshot-modal');
  const img = document.getElementById('screenshot-img');
  const title = document.getElementById('screenshot-title');
  const openBtn = document.getElementById('btn-open-screenshot-tab');
  const downloadBtn = document.getElementById('btn-download-screenshot');

  if (modal && img) {
    img.src = url;
    if (title) title.textContent = `Evidência capturada pela IA — ${company}`;
    if (openBtn) openBtn.href = url;
    if (downloadBtn) {
      downloadBtn.href = url;
      downloadBtn.download = `evidencia_${company.toLowerCase().replace(/[^a-z0-9]/g, '_')}.png`;
    }
    modal.classList.add('active');
  }
}

function closeScreenshotModal() {
  const modal = document.getElementById('screenshot-modal');
  if (modal) modal.classList.remove('active');
}

function renderScreenshotsGallery() {
  const container = document.getElementById('gallery-container');
  if (!container) return;

  const items = state.results.filter((r) => r.screenshot_url);
  if (items.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; text-align:center; padding:3rem; color:var(--text-muted);">
        Nenhuma captura de tela registrada ainda. Capturas são salvas automaticamente quando a IA detecta CAPTCHA, formulário ou dúvida.
      </div>
    `;
    return;
  }

  container.innerHTML = items
    .map(
      (item) => `
    <div class="card" style="padding:1rem; cursor:pointer;" onclick="viewScreenshot('${escapeHtml(item.screenshot_url).replace(/'/g, "\\'")}', '${escapeHtml(item.company).replace(/'/g, "\\'")}')">
      <div style="font-weight:600; margin-bottom:0.2rem;">${escapeHtml(item.company)}</div>
      <div style="font-size:0.8rem; color:var(--text-muted); margin-bottom:0.75rem;">${escapeHtml(item.title)}</div>
      <img src="${sanitizeUrl(item.screenshot_url)}" alt="Screenshot" style="width:100%; height:180px; object-fit:cover; border-radius:var(--radius-sm); border:1px solid var(--border-subtle);" />
      <div style="margin-top:0.75rem; font-size:0.75rem; color:var(--danger);">${escapeHtml(item.reason || 'Revisão Necessária')}</div>
    </div>
  `
    )
    .join('');
}

// ==============================================================================
// 9. Configurações & Presets de Modelos
// ==============================================================================

function populateConfigForm() {
  const c = state.config;
  if (!c) return;

  // 1. LLM
  setValue('cfg-api-key', c.llm?.api_key || '');
  setValue('cfg-base-url', c.llm?.base_url || '');
  setValue('cfg-model', c.llm?.model || 'gemini-2.5-flash');
  setValue('cfg-llm-timeout', c.llm?.timeout_s ?? 60);
  setValue('cfg-llm-retries', c.llm?.max_retries ?? 3);
  setValue('cfg-llm-max-input', c.llm?.max_input_chars ?? 12000);
  setCheck('cfg-no-llm', c.llm?.no_llm);

  // 2. Planilha, Drive, Supabase Sync & Fila Local
  setCheck('cfg-supabase-sync', c.cloud?.supabase_sync_enabled !== false);
  setValue('cfg-discovered-jobs-file', c.discovery?.discovered_jobs_file || './cache/discovered_jobs.json');
  setValue('cfg-sheet-url', c.sheet?.google_sheet_url || '');
  setValue('cfg-drive-folder', c.sheet?.drive_folder_id || '');
  setValue('cfg-cache-dir', c.sheet?.cache_dir || './cache/resumes');
  setValue('cfg-google-oauth-file', c.sheet?.google_oauth_client_file || './client_secret.json');
  setValue('cfg-google-sa-file', c.sheet?.google_service_account_file || './service_account.json');
  setValue('cfg-google-token-file', c.sheet?.google_token_file || './token.json');
  setCheck('cfg-blank-status', c.sheet?.include_blank_status);

  // 3. Execução, Concorrência & Navegador
  setValue('cfg-concurrency', c.execution?.max_concurrency ?? 3);
  setCheck('cfg-dry-run', c.execution?.dry_run);
  setCheck('cfg-autonomous', c.execution?.autonomous_mode);
  setCheck('cfg-camoufox', c.execution?.use_camoufox);
  setCheck('cfg-headless', c.execution?.headless);
  setValue('cfg-storage-state-path', c.execution?.storage_state_path || './cache/storage_state.json');
  setValue('cfg-screenshot-dir', c.execution?.screenshot_dir || './artifacts/screenshots');
  setValue('cfg-results-file', c.execution?.results_file || './artifacts/results.jsonl');
  setValue('cfg-sheet-result-file', c.execution?.sheet_result_file || './artifacts/planilha_atualizada.xlsx');

  // 4. Timeouts & Rede
  setValue('cfg-task-timeout', c.execution?.task_timeout_s ?? 120);
  setValue('cfg-nav-timeout', c.execution?.nav_timeout_ms ?? 30000);
  setValue('cfg-action-timeout', c.execution?.action_timeout_ms ?? 10000);
  setValue('cfg-max-retries', c.execution?.max_retries ?? 2);
  setValue('cfg-backoff-base', c.execution?.backoff_base_s ?? 1.0);
  setValue('cfg-blocked-patterns', typeof c.execution?.blocked_url_patterns === 'string' ? c.execution.blocked_url_patterns : JSON.stringify(c.execution?.blocked_url_patterns || []));

  // 5. Fit Gate, Currículo Sob Medida & Radar de Portais
  setValue('cfg-min-fit-score', c.execution?.min_fit_score ?? 70);
  setCheck('cfg-fit-gate-enabled', c.execution?.fit_gate_enabled);
  setValue('cfg-resume-match-threshold', c.execution?.resume_match_threshold ?? 0.9);
  setCheck('cfg-tailor-resume-per-job', c.execution?.tailor_resume_per_job !== false);
  setValue('cfg-generated-resumes-dir', c.execution?.generated_resumes_dir || './cache/resumes/generated');
  setValue('cfg-linkedin-location', c.discovery?.linkedin_search_location || 'Brasil');
  setValue('cfg-linkedin-daily-limit', c.discovery?.linkedin_daily_limit ?? 15);
  setValue('cfg-gh-boards', Array.isArray(c.discovery?.target_greenhouse_boards) ? c.discovery.target_greenhouse_boards.join(', ') : (c.discovery?.target_greenhouse_boards || ''));
  setValue('cfg-lever-boards', Array.isArray(c.discovery?.target_lever_boards) ? c.discovery.target_lever_boards.join(', ') : (c.discovery?.target_lever_boards || ''));
  setValue('cfg-ashby-boards', Array.isArray(c.discovery?.target_ashby_boards) ? c.discovery.target_ashby_boards.join(', ') : (c.discovery?.target_ashby_boards || ''));

  const discoverLocEl = document.getElementById('discover-location');
  if (discoverLocEl && c.discovery?.linkedin_search_location) {
    discoverLocEl.value = c.discovery.linkedin_search_location;
  }

  // 6. Decisão Calibrada (Laya & Regras)
  setValue('cfg-decision-backend', c.decision?.decision_backend || 'laya');
  setValue('cfg-laya-url', c.decision?.laya_url || 'http://127.0.0.1:8787');
  setValue('cfg-laya-timeout', c.decision?.laya_timeout_s ?? 5.0);
  setValue('cfg-laya-cpu-factor', c.decision?.laya_cpu_timeout_factor ?? 5.0);
  setValue('cfg-laya-max-failures', c.decision?.laya_max_failures ?? 3);
  setValue('cfg-laya-cooldown', c.decision?.laya_breaker_cooldown_s ?? 60.0);
  setValue('cfg-laya-yes-th', c.decision?.laya_yes_threshold ?? 0.8);
  setValue('cfg-laya-no-th', c.decision?.laya_no_threshold ?? 0.2);
  setValue('cfg-laya-min-margin', c.decision?.laya_min_margin ?? 0.15);
  setCheck('cfg-laya-order-swap', c.decision?.laya_order_swap);
  setValue('cfg-laya-max-chars', c.decision?.laya_state_max_chars ?? 1500);
  setValue('cfg-laya-plan-above', c.decision?.laya_plan_above_chars ?? 600);
  setValue('cfg-laya-dp-modes', typeof c.decision?.laya_dp_modes === 'string' ? c.decision.laya_dp_modes : JSON.stringify(c.decision?.laya_dp_modes || {}));
  setCheck('cfg-laya-req-eval', c.decision?.laya_require_eval_for_active);
  setValue('cfg-laya-eval-file', c.decision?.laya_eval_file || './artifacts/laya_eval.json');
  setValue('cfg-laya-eval-max-days', c.decision?.laya_eval_max_age_days ?? 14);
  setCheck('cfg-laya-navigator', c.decision?.laya_web_navigator_enabled);
  setCheck('cfg-laya-visual', c.decision?.laya_visual_watch);
  setValue('cfg-laya-visual-delay', c.decision?.laya_visual_delay_ms ?? 750);
  setValue('cfg-eval-min-examples', c.decision?.eval_min_examples ?? 30);
  setValue('cfg-eval-margin', c.decision?.eval_margin ?? 0.10);
  setValue('cfg-eval-min-coverage', c.decision?.eval_min_coverage ?? 0.5);
  setValue('cfg-decision-audit-file', c.decision?.decision_audit_file || './artifacts/decisions.jsonl');

  // 7. Editor Direto .env
  setValue('cfg-raw-env', c.raw_env || '');

  // Sincroniza preset do modelo e modo BYOK / Managed
  state.cloud.mode = c.llm?.mode === 'managed' ? 'managed' : 'byok';
  const managedSel = document.getElementById('cfg-managed-model');
  if (managedSel && c.llm?.model) {
    const hasOpt = Array.from(managedSel.options).some((o) => o.value === c.llm.model);
    if (hasOpt) managedSel.value = c.llm.model;
  }
  renderCloudPanel();

  const presetSel = document.getElementById('cfg-model-preset');
  if (presetSel && c.llm?.model) {
    if (
      [
        'gemini-2.5-flash',
        'deepseek/deepseek-v4.1-flash',
        'minimax/minimax-m3.1-flash',
        'openai/gpt-6-luna',
        'gpt-4o-mini',
      ].includes(c.llm.model)
    ) {
      presetSel.value = c.llm.model;
    } else {
      presetSel.value = 'custom';
    }
  }

  // Remove aviso de alterações não salvas
  const unsavedBadge = document.getElementById('unsaved-badge');
  if (unsavedBadge) unsavedBadge.style.display = 'none';
  state.isConfigDirty = false;
}

function applyModelPreset(preset) {
  const modelInput = document.getElementById('cfg-model');
  const baseUrlInput = document.getElementById('cfg-base-url');

  if (preset === 'gemini-2.5-flash') {
    if (modelInput) modelInput.value = 'gemini-2.5-flash';
    if (baseUrlInput) baseUrlInput.value = 'https://generativelanguage.googleapis.com/v1beta/openai/';
  } else if (
    preset === 'deepseek/deepseek-v4.1-flash' ||
    preset === 'minimax/minimax-m3.1-flash' ||
    preset === 'openai/gpt-6-luna'
  ) {
    if (modelInput) modelInput.value = preset;
    if (baseUrlInput) baseUrlInput.value = 'https://openrouter.ai/api/v1';
  } else if (preset === 'gpt-4o-mini') {
    if (modelInput) modelInput.value = 'gpt-4o-mini';
    if (baseUrlInput) baseUrlInput.value = 'https://api.openai.com/v1';
  }

  markConfigDirty();
}

async function fetchCloudStatus(forceRefresh = false) {
  try {
    const url = forceRefresh ? '/api/cloud/status?refresh=true' : '/api/cloud/status';
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    state.cloud = {
      mode: data.mode === 'managed' ? 'managed' : state.cloud.mode || 'byok',
      authenticated: Boolean(data.authenticated),
      email: data.email || '',
      quota: data.quota || null,
      error: data.error || null,
    };
    renderCloudPanel();
  } catch (err) {
    // Mantém UI funcional em modo offline
  }
}

async function switchLlmMode(mode) {
  const normalized = mode === 'managed' ? 'managed' : 'byok';
  state.cloud.mode = normalized;
  if (normalized === 'managed') {
    const managedSel = document.getElementById('cfg-managed-model');
    if (managedSel) setValue('cfg-model', managedSel.value);
  }
  renderCloudPanel();
  try {
    const res = await fetch('/api/cloud/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: normalized }),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.config) state.config = data.config;
      showToast(
        normalized === 'managed'
          ? 'Modo Conta Gerenciada ativado.'
          : 'Modo gratuito (BYOK) ativado.',
        'info'
      );
      if (normalized === 'managed') await fetchCloudStatus(true);
    }
  } catch (err) {
    markConfigDirty();
  }
}

function selectManagedModel(val) {
  setValue('cfg-model', val);
  markConfigDirty();
}

function formatResetDate(isoStr) {
  if (!isoStr) return '—';
  const dt = new Date(isoStr);
  if (Number.isNaN(dt.getTime())) return '—';
  return dt.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function renderCloudPanel() {
  const isManaged = state.cloud.mode === 'managed';
  const byokBox = document.getElementById('byok-fields');
  const managedBox = document.getElementById('managed-cloud-panel');
  const btnByok = document.getElementById('btn-mode-byok');
  const btnManaged = document.getElementById('btn-mode-managed');
  const modeBadge = document.getElementById('llm-mode-badge');

  if (byokBox) byokBox.style.display = isManaged ? 'none' : 'block';
  if (managedBox) managedBox.style.display = isManaged ? 'block' : 'none';
  if (btnByok) btnByok.className = isManaged ? 'btn btn-secondary' : 'btn btn-primary';
  if (btnManaged) btnManaged.className = isManaged ? 'btn btn-primary' : 'btn btn-secondary';
  if (modeBadge) {
    modeBadge.textContent = isManaged ? 'Conta Gerenciada' : 'BYOK (Gratuito)';
    modeBadge.className = isManaged ? 'badge badge-success' : 'badge badge-neutral';
  }

  const loggedOutEl = document.getElementById('cloud-auth-logged-out');
  const loggedInEl = document.getElementById('cloud-auth-logged-in');
  const emailEl = document.getElementById('cloud-user-email');
  if (loggedOutEl) loggedOutEl.style.display = state.cloud.authenticated ? 'none' : 'block';
  if (loggedInEl) loggedInEl.style.display = state.cloud.authenticated ? 'flex' : 'none';
  if (emailEl) emailEl.textContent = state.cloud.email || '—';

  const sbSyncBadge = document.getElementById('cfg-supabase-sync-badge');
  if (sbSyncBadge) {
    if (state.cloud.authenticated) {
      sbSyncBadge.textContent = `Conectado (${state.cloud.email || 'Supabase'})`;
      sbSyncBadge.className = 'badge badge-success';
    } else {
      sbSyncBadge.textContent = 'Supabase Desconectado';
      sbSyncBadge.className = 'badge badge-neutral';
    }
  }

  const planBadge = document.getElementById('cloud-plan-badge');
  const statusBadge = document.getElementById('cloud-status-badge');
  const quotaBar = document.getElementById('cloud-quota-bar');
  const quotaPctEl = document.getElementById('cloud-quota-pct');
  const quotaResetEl = document.getElementById('cloud-quota-reset');
  const quotaAlertEl = document.getElementById('cloud-quota-alert');

  const q = state.cloud.quota;
  const usedPct = q && typeof q.used_pct === 'number' ? Math.max(0, Math.min(100, q.used_pct)) : 0;

  if (quotaBar) {
    quotaBar.style.width = `${usedPct}%`;
    if (usedPct >= 95) {
      quotaBar.style.background = 'var(--crimson, #ef4444)';
    } else if (usedPct >= 75) {
      quotaBar.style.background = 'var(--amber, #f59e0b)';
    } else {
      quotaBar.style.background = 'var(--mint, #10b981)';
    }
  }
  if (quotaPctEl) quotaPctEl.textContent = `${usedPct.toFixed(0)}%`;
  if (quotaResetEl) {
    quotaResetEl.textContent = q?.cycle_reset_at ? `Renova em ${formatResetDate(q.cycle_reset_at)}` : 'Renova em —';
  }

  if (planBadge) {
    const planMap = {
      pro_cloud: 'Plano Pro',
      agency_b2b: 'Plano Agência',
      free_trial: 'Trial Gratuito',
    };
    planBadge.textContent = q?.plan ? (planMap[q.plan] || q.plan) : 'Sem Plano';
  }
  if (statusBadge) {
    if (!state.cloud.authenticated) {
      statusBadge.textContent = 'Desconectado';
      statusBadge.className = 'badge badge-neutral';
    } else if (q?.is_blocked) {
      statusBadge.textContent = q.reason === 'quota_exceeded' ? 'Cota Esgotada' : 'Inativo';
      statusBadge.className = 'badge badge-danger';
    } else {
      statusBadge.textContent = 'Ativo';
      statusBadge.className = 'badge badge-success';
    }
  }

  if (quotaAlertEl) {
    if (q?.is_blocked) {
      quotaAlertEl.style.display = 'block';
      quotaAlertEl.textContent =
        q.reason === 'quota_exceeded'
          ? 'Você atingiu 100% da sua cota deste ciclo. Aguarde a renovação ou use sua chave gratuita (BYOK).'
          : 'Sua assinatura gerenciada está inativa. Ative seu plano ou alterne para BYOK gratuito.';
    } else if (state.cloud.error) {
      quotaAlertEl.style.display = 'block';
      quotaAlertEl.textContent = state.cloud.error;
    } else {
      quotaAlertEl.style.display = 'none';
    }
  }
}

async function _submitCloudAuth(endpoint) {
  const email = document.getElementById('cloud-email')?.value?.trim();
  const password = document.getElementById('cloud-password')?.value || '';
  const fb = document.getElementById('cloud-auth-feedback');
  if (!email || !password) {
    if (fb) {
      fb.style.display = 'block';
      fb.style.color = 'var(--amber)';
      fb.textContent = 'Informe e-mail e senha.';
    }
    return;
  }
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (fb) {
        fb.style.display = 'block';
        fb.style.color = 'var(--crimson)';
        fb.textContent = data.detail || 'Não foi possível autenticar.';
      }
      return;
    }
    if (data.confirmation_required) {
      if (fb) {
        fb.style.display = 'block';
        fb.style.color = 'var(--mint)';
        fb.textContent = 'Conta criada! Confirme o link enviado ao seu e-mail para entrar.';
      }
      return;
    }
    state.cloud.authenticated = Boolean(data.authenticated);
    state.cloud.email = data.email || email;
    state.cloud.quota = data.quota || null;
    state.cloud.error = null;
    if (fb) fb.style.display = 'none';
    renderCloudPanel();
    showToast('Conta gerenciada conectada com sucesso!', 'success');
  } catch (err) {
    if (fb) {
      fb.style.display = 'block';
      fb.style.color = 'var(--crimson)';
      fb.textContent = `Erro de rede: ${err.message}`;
    }
  }
}

function cloudLogin() {
  return _submitCloudAuth('/api/cloud/login');
}

function cloudSignup() {
  return _submitCloudAuth('/api/cloud/signup');
}

async function cloudLogout() {
  try {
    await fetch('/api/cloud/logout', { method: 'POST' });
    state.cloud.authenticated = false;
    state.cloud.email = '';
    state.cloud.quota = null;
    state.cloud.error = null;
    renderCloudPanel();
    showToast('Sessão encerrada.', 'info');
  } catch (err) {
    showToast('Erro ao sair da conta.', 'danger');
  }
}

async function refreshCloudQuota() {
  await fetchCloudStatus(true);
  showToast('Consumo da conta atualizado.', 'info');
}

function markConfigDirty() {
  state.isConfigDirty = true;
  const unsavedBadge = document.getElementById('unsaved-badge');
  if (unsavedBadge) unsavedBadge.style.display = 'inline-flex';
}

function openCurrentSheet() {
  const url = document.getElementById('cfg-sheet-url')?.value;
  if (!url) {
    showToast('Informe o link do Google Sheets primeiro.', 'info');
    return;
  }
  window.open(url, '_blank');
}

function setValue(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

function setCheck(id, val) {
  const el = document.getElementById(id);
  if (el) el.checked = Boolean(val);
}

async function saveSettings(e) {
  if (e) e.preventDefault();
  const btn = document.getElementById('btn-save-settings');
  if (btn) btn.disabled = true;

  const payload = {
    llm: {
      mode: state.cloud.mode || 'byok',
      api_key: document.getElementById('cfg-api-key')?.value,
      base_url: document.getElementById('cfg-base-url')?.value,
      model: document.getElementById('cfg-model')?.value,
      timeout_s: parseFloat(document.getElementById('cfg-llm-timeout')?.value || '60'),
      max_retries: parseInt(document.getElementById('cfg-llm-retries')?.value || '3', 10),
      max_input_chars: parseInt(document.getElementById('cfg-llm-max-input')?.value || '12000', 10),
      no_llm: document.getElementById('cfg-no-llm')?.checked,
    },
    sheet: {
      google_sheet_url: document.getElementById('cfg-sheet-url')?.value,
      drive_folder_id: document.getElementById('cfg-drive-folder')?.value,
      cache_dir: document.getElementById('cfg-cache-dir')?.value,
      google_oauth_client_file: document.getElementById('cfg-google-oauth-file')?.value,
      google_service_account_file: document.getElementById('cfg-google-sa-file')?.value,
      google_token_file: document.getElementById('cfg-google-token-file')?.value,
      include_blank_status: document.getElementById('cfg-blank-status')?.checked,
    },
    execution: {
      dry_run: document.getElementById('cfg-dry-run')?.checked,
      autonomous_mode: document.getElementById('cfg-autonomous')?.checked,
      max_concurrency: parseInt(document.getElementById('cfg-concurrency')?.value || '3', 10),
      use_camoufox: document.getElementById('cfg-camoufox')?.checked,
      headless: document.getElementById('cfg-headless')?.checked,
      min_fit_score: parseInt(document.getElementById('cfg-min-fit-score')?.value || '70', 10),
      fit_gate_enabled: document.getElementById('cfg-fit-gate-enabled')?.checked,
      resume_match_threshold: parseFloat(document.getElementById('cfg-resume-match-threshold')?.value || '0.9'),
      tailor_resume_per_job: document.getElementById('cfg-tailor-resume-per-job')?.checked,
      generated_resumes_dir: document.getElementById('cfg-generated-resumes-dir')?.value,
      task_timeout_s: parseFloat(document.getElementById('cfg-task-timeout')?.value || '120'),
      nav_timeout_ms: parseInt(document.getElementById('cfg-nav-timeout')?.value || '30000', 10),
      action_timeout_ms: parseInt(document.getElementById('cfg-action-timeout')?.value || '10000', 10),
      max_retries: parseInt(document.getElementById('cfg-max-retries')?.value || '2', 10),
      backoff_base_s: parseFloat(document.getElementById('cfg-backoff-base')?.value || '1.0'),
      blocked_url_patterns: document.getElementById('cfg-blocked-patterns')?.value,
      storage_state_path: document.getElementById('cfg-storage-state-path')?.value,
      screenshot_dir: document.getElementById('cfg-screenshot-dir')?.value,
      results_file: document.getElementById('cfg-results-file')?.value,
      sheet_result_file: document.getElementById('cfg-sheet-result-file')?.value,
    },
    cloud: {
      supabase_sync_enabled: document.getElementById('cfg-supabase-sync')?.checked,
    },
    discovery: {
      discovered_jobs_file: document.getElementById('cfg-discovered-jobs-file')?.value,
      linkedin_search_location: document.getElementById('cfg-linkedin-location')?.value,
      linkedin_daily_limit: parseInt(document.getElementById('cfg-linkedin-daily-limit')?.value || '15', 10),
      target_greenhouse_boards: document.getElementById('cfg-gh-boards')?.value,
      target_lever_boards: document.getElementById('cfg-lever-boards')?.value,
      target_ashby_boards: document.getElementById('cfg-ashby-boards')?.value,
    },
    decision: {
      decision_backend: document.getElementById('cfg-decision-backend')?.value,
      laya_url: document.getElementById('cfg-laya-url')?.value,
      laya_timeout_s: parseFloat(document.getElementById('cfg-laya-timeout')?.value || '5.0'),
      laya_cpu_timeout_factor: parseFloat(document.getElementById('cfg-laya-cpu-factor')?.value || '5.0'),
      laya_max_failures: parseInt(document.getElementById('cfg-laya-max-failures')?.value || '3', 10),
      laya_breaker_cooldown_s: parseFloat(document.getElementById('cfg-laya-cooldown')?.value || '60.0'),
      laya_yes_threshold: parseFloat(document.getElementById('cfg-laya-yes-th')?.value || '0.8'),
      laya_no_threshold: parseFloat(document.getElementById('cfg-laya-no-th')?.value || '0.2'),
      laya_min_margin: parseFloat(document.getElementById('cfg-laya-min-margin')?.value || '0.15'),
      laya_order_swap: document.getElementById('cfg-laya-order-swap')?.checked,
      laya_state_max_chars: parseInt(document.getElementById('cfg-laya-max-chars')?.value || '1500', 10),
      laya_plan_above_chars: parseInt(document.getElementById('cfg-laya-plan-above')?.value || '600', 10),
      laya_dp_modes: document.getElementById('cfg-laya-dp-modes')?.value,
      laya_require_eval_for_active: document.getElementById('cfg-laya-req-eval')?.checked,
      laya_eval_file: document.getElementById('cfg-laya-eval-file')?.value,
      laya_eval_max_age_days: parseInt(document.getElementById('cfg-laya-eval-max-days')?.value || '14', 10),
      laya_web_navigator_enabled: document.getElementById('cfg-laya-navigator')?.checked,
      laya_visual_watch: document.getElementById('cfg-laya-visual')?.checked,
      laya_visual_delay_ms: parseInt(document.getElementById('cfg-laya-visual-delay')?.value || '750', 10),
      eval_min_examples: parseInt(document.getElementById('cfg-eval-min-examples')?.value || '30', 10),
      eval_margin: parseFloat(document.getElementById('cfg-eval-margin')?.value || '0.10'),
      eval_min_coverage: parseFloat(document.getElementById('cfg-eval-min-coverage')?.value || '0.5'),
      decision_audit_file: document.getElementById('cfg-decision-audit-file')?.value,
    },
  };

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      state.config = await res.json();
      populateConfigForm();
      await Promise.all([fetchJobs(), fetchResults(), fetchMetrics()]);
      showToast('Configurações e planilha sincronizadas com sucesso!', 'success');
    } else {
      showToast('Erro ao salvar configurações.', 'danger');
    }
  } catch (err) {
    showToast(`Erro na requisição: ${err.message}`, 'danger');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function saveRawEnvDirect() {
  const textarea = document.getElementById('cfg-raw-env');
  if (!textarea) return;

  const raw = textarea.value;
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_env: raw }),
    });
    if (res.ok) {
      state.config = await res.json();
      populateConfigForm();
      await Promise.all([fetchJobs(), fetchResults(), fetchMetrics()]);
      showToast('Arquivo .env salvo com sucesso diretamente!', 'success');
    } else {
      showToast('Erro ao salvar arquivo .env.', 'danger');
    }
  } catch (err) {
    showToast(`Erro na requisição: ${err.message}`, 'danger');
  }
}

async function testApiKey() {
  const btn = document.getElementById('btn-test-key');
  const resultBox = document.getElementById('test-key-result');
  if (btn) btn.disabled = true;
  if (resultBox) {
    resultBox.style.display = 'block';
    resultBox.innerHTML = '<span class="spinner" style="display:inline-block; vertical-align:middle; margin-right:0.4rem;"></span> Testando chave com o provedor de IA...';
  }

  const payload = {
    api_key: document.getElementById('cfg-api-key')?.value,
    base_url: document.getElementById('cfg-base-url')?.value,
    model: document.getElementById('cfg-model')?.value,
  };

  try {
    const res = await fetch('/api/config/test-llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.success) {
      if (resultBox) {
        resultBox.style.color = 'var(--mint)';
        resultBox.textContent = `✓ ${data.message} (${data.latency_ms}ms)`;
      }
      showToast('Chave de API validada com sucesso!', 'success');
      playChime('success');
    } else {
      if (resultBox) {
        resultBox.style.color = 'var(--crimson)';
        resultBox.textContent = `✕ ${data.error || 'Falha ao testar chave'}`;
      }
      showToast('Falha na validação da chave.', 'danger');
    }
  } catch (err) {
    if (resultBox) {
      resultBox.style.color = 'var(--crimson)';
      resultBox.textContent = `✕ Erro: ${err.message}`;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function togglePasswordVisibility(inputId) {
  const el = document.getElementById(inputId);
  if (el) el.type = el.type === 'password' ? 'text' : 'password';
}

// ==============================================================================
// 10. Sessão de Contas & Cookies
// ==============================================================================

function renderCookiesUI() {
  const c = state.cookies;
  const statusEl = document.getElementById('cookies-status-badge');
  const detailsEl = document.getElementById('cookies-details-text');
  const portalsEl = document.getElementById('cookies-portals-list');

  if (!c) return;

  if (statusEl) {
    if (c.has_session) {
      statusEl.className = 'badge badge-success';
      statusEl.textContent = `Sessão Salva (${c.cookie_count} cookies)`;
    } else {
      statusEl.className = 'badge badge-neutral';
      statusEl.textContent = 'Nenhuma sessão ativa salva';
    }
  }

  if (detailsEl) {
    detailsEl.textContent = c.has_session
      ? `Última sincronização: ${c.last_modified} em cache/storage_state.json`
      : 'Cookies mantêm suas credenciais do Indeed, Gupy e LinkedIn salvas para evitar desafios de login.';
  }

  if (portalsEl) {
    if (c.known_portals && c.known_portals.length > 0) {
      portalsEl.innerHTML = c.known_portals
        .map((p) => `<span class="badge badge-info"><span class="badge-dot"></span> ${escapeHtml(p)} Conectado</span>`)
        .join(' ');
    } else if (c.domains && c.domains.length > 0) {
      portalsEl.innerHTML = `<span style="font-size:0.8rem; color:var(--text-muted);">${c.domains.length} domínios salvos</span>`;
    } else {
      portalsEl.innerHTML = '';
    }
  }

  // Sincronizar badges da aba Radar (LinkedIn / Gupy)
  const portals = c.portals || {};
  _syncRadarPortalBadge('linkedin', portals.linkedin, 'src-linkedin');
  _syncRadarPortalBadge('gupy', portals.gupy, 'src-gupy');
}

function _syncRadarPortalBadge(portal, info, checkboxId) {
  const dot = document.getElementById(`radar-dot-${portal}`);
  const text = document.getElementById(`radar-status-${portal}`);
  const checkbox = document.getElementById(checkboxId);
  const connected = Boolean(info && info.connected);

  if (dot) {
    dot.style.background = connected ? 'var(--accent-success)' : 'var(--accent-warning)';
  }
  if (text) {
    text.textContent = connected
      ? `Conectado (${info.cookie_count || 0} cookies)`
      : 'Desconectado';
    text.style.color = connected ? 'var(--accent-success)' : 'var(--text-muted)';
  }
  if (checkbox && connected && !checkbox.dataset.userToggled) {
    checkbox.checked = true;
  }
}

function openPasteCookiesModal() {
  const modal = document.getElementById('modal-paste-cookies');
  if (modal) modal.classList.add('active');
}

function closePasteCookiesModal() {
  const modal = document.getElementById('modal-paste-cookies');
  if (modal) modal.classList.remove('active');
}

async function submitPastedCookies() {
  const text = document.getElementById('txt-cookies-json')?.value;
  if (!text) return;

  try {
    const res = await fetch('/api/cookies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: text }),
    });
    if (res.ok) {
      state.cookies = await res.json();
      renderCookiesUI();
      closePasteCookiesModal();
      showToast('Cookies importados e gravados com sucesso!', 'success');
      playChime('success');
    } else {
      const err = await res.json();
      showToast(`Erro ao importar cookies: ${err.detail || 'Formato inválido'}`, 'danger');
    }
  } catch (err) {
    showToast(`Erro na requisição: ${err.message}`, 'danger');
  }
}

async function clearSavedCookies() {
  if (!confirm('Deseja realmente apagar todos os cookies de sessão salvos? Você terá que se logar novamente nos portais.')) {
    return;
  }
  try {
    const res = await fetch('/api/cookies', { method: 'DELETE' });
    if (res.ok) {
      state.cookies = await res.json();
      renderCookiesUI();
      showToast('Sessão e cookies apagados com sucesso.', 'info');
    }
  } catch (err) {
    showToast('Erro ao apagar cookies.', 'danger');
  }
}

function triggerVisualLogin() {
  openLoginChoiceModal();
}

function openLoginChoiceModal() {
  const modal = document.getElementById('modal-login-choice');
  if (modal) modal.classList.add('active');
}

function closeLoginChoiceModal() {
  const modal = document.getElementById('modal-login-choice');
  if (modal) modal.classList.remove('active');
}

let _portalPollTimer = null;

async function connectPortal(portal) {
  const label = portal === 'linkedin' ? 'LinkedIn' : portal === 'gupy' ? 'Gupy' : portal;
  showToast(`Abrindo ${label} no Camoufox (auto-detecção de login ativada)...`, 'info');

  try {
    const res = await fetch(`/api/cookies/connect/${encodeURIComponent(portal)}`, {
      method: 'POST',
    });
    const data = await res.json();
    if (!data.success) {
      showToast(`Falha ao abrir ${label}: ${data.error || 'erro desconhecido'}`, 'danger');
      return;
    }

    showToast(
      `Faça login no ${label} na janela aberta. A sessão será salva automaticamente.`,
      'success'
    );

    if (_portalPollTimer) clearInterval(_portalPollTimer);
    let attempts = 0;
    _portalPollTimer = setInterval(async () => {
      attempts += 1;
      await fetchCookies();
      const connected = state.cookies?.portals?.[portal]?.connected;
      if (connected || attempts >= 40) {
        clearInterval(_portalPollTimer);
        _portalPollTimer = null;
        if (connected) {
          showToast(`${label} conectado e cookies salvos!`, 'success');
        }
      }
    }, 3000);
  } catch (err) {
    showToast(`Erro ao conectar ${label}: ${err.message}`, 'danger');
  }
}

async function syncSupabaseJobsNow() {
  showToast('Sincronizando vagas com o Supabase...', 'info');
  try {
    const res = await fetch('/api/cloud/sync-jobs', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) {
      showToast(data.detail || 'Conecte sua conta Supabase em Configurações > Modo de IA primeiro.', 'warning');
      return;
    }
    showToast(
      `Supabase sincronizado: +${data.pushed || 0} enviadas, +${data.pulled || 0} importadas (${data.remote_total || 0} no banco).`,
      'success'
    );
    await Promise.all([fetchJobs(), fetchResults(), fetchMetrics()]);
  } catch (err) {
    showToast(`Falha na sincronização com Supabase: ${err.message}`, 'danger');
  }
}

async function discoverJobsNow() {
  const btn = document.getElementById('btn-discover-jobs');
  const feedbackEl = document.getElementById('discover-feedback');
  const query = (document.getElementById('discover-query')?.value || 'desenvolvedor').trim();
  const location = (document.getElementById('discover-location')?.value || 'Brasil').trim();

  const sources = [];
  if (document.getElementById('src-linkedin')?.checked) sources.push('linkedin');
  if (document.getElementById('src-gupy')?.checked) sources.push('gupy');
  if (document.getElementById('src-ats-boards')?.checked) sources.push('ats_boards');
  if (document.getElementById('src-open-feeds')?.checked) sources.push('open_feeds');

  if (sources.length === 0) {
    showToast('Selecione ao menos uma fonte de busca.', 'warning');
    return;
  }

  const originalBtnHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    const spanEl = btn.querySelector('span');
    if (spanEl) spanEl.textContent = 'Buscando vagas...';
    else btn.textContent = 'Buscando vagas...';
  }
  if (feedbackEl) {
    feedbackEl.style.display = 'block';
    feedbackEl.textContent = `Consultando ${sources.join(', ')}...`;
  }

  try {
    const res = await fetch('/api/jobs/discover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, location, limit: 25, sources }),
    });
    const data = await res.json();
    if (!data.success) {
      showToast(`Erro na busca: ${data.error || 'falha desconhecida'}`, 'danger');
      if (feedbackEl) {
        feedbackEl.style.display = 'block';
        feedbackEl.textContent = data.error || 'Erro ao buscar vagas.';
      }
      return;
    }

    const sbInfo =
      data.supabase_sync && typeof data.supabase_sync.inserted === 'number'
        ? ` | Supabase: +${data.supabase_sync.inserted} sincronizadas`
        : '';
    const warnText =
      Array.isArray(data.warnings) && data.warnings.length > 0
        ? ` | Avisos: ${data.warnings.join(' ; ')}`
        : '';
    const msg = `${data.total_found || 0} vagas encontradas (${data.added || 0} novas na fila, total ${data.queue_total || 0})${sbInfo}.${warnText}`;
    if (feedbackEl) {
      feedbackEl.style.display = 'block';
      feedbackEl.textContent = msg;
    }
    showToast(msg, 'success');

    await Promise.all([fetchJobs(), fetchResults()]);
  } catch (err) {
    showToast(`Erro ao buscar vagas: ${err.message}`, 'danger');
    if (feedbackEl) {
      feedbackEl.style.display = 'block';
      feedbackEl.textContent = err.message;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalBtnHtml;
    }
  }
}

async function tailorResumeForJob(jobId, reopenModal = false) {
  showToast('Gerando currículo ATS em PDF adaptado para esta vaga...', 'info');
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/tailor-resume`, {
      method: 'POST',
    });
    const data = await res.json();
    if (!data.success) {
      showToast(`Falha ao gerar PDF: ${data.error || 'erro'}`, 'danger');
      return;
    }
    showToast(`Currículo gerado: ${data.filename}`, 'success');
    await Promise.all([fetchJobs(), fetchResults()]);
    if (reopenModal) {
      openJobDetailsModal(jobId);
    }
  } catch (err) {
    showToast(`Erro ao gerar currículo: ${err.message}`, 'danger');
  }
}

async function runSingleJob(jobId, dryRun = true) {
  const headed = Boolean(document.getElementById('laya-demo-headed')?.checked);
  _resetLayaLiveViewer(
    dryRun ? 'PREPARANDO DRY-RUN REAL' : 'PREPARANDO CANDIDATURA REAL',
    `Carregando vaga #${jobId}...`
  );
  showToast(
    dryRun ? 'Iniciando simulação ao vivo para esta vaga...' : 'Iniciando candidatura ao vivo...',
    'info'
  );
  try {
    const res = await fetch('/api/laya/run-real', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: String(jobId),
        dry_run: Boolean(dryRun),
        headless: !headed,
        laya_navigator: true,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(`Não foi possível iniciar: ${data.detail || data.error || 'erro'}`, 'danger');
      return;
    }
    showToast('Execução ao vivo iniciada! Acompanhe no Visor Laya.', 'success');
    startLiveTimer();
    switchTab('dashboard');
    document.getElementById('laya-live-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    showToast(`Erro ao iniciar vaga: ${err.message}`, 'danger');
  }
}

async function startPortalLogin(url) {
  if (!url) return;
  closeLoginChoiceModal();

  showToast('Iniciando navegador Camoufox para login...', 'info');

  try {
    const res = await fetch('/api/cookies/launch-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, use_camoufox: true }),
    });
    const data = await res.json();
    if (data.success) {
      showToast('Navegador aberto! Faça login e feche a janela ao terminar.', 'success');
      setTimeout(fetchCookies, 5000);
    } else {
      showToast(`Falha ao abrir navegador: ${data.error}`, 'danger');
    }
  } catch (err) {
    showToast(`Erro: ${err.message}`, 'danger');
  }
}

// ==============================================================================
// 11. Modal de Disparo de Automação
// ==============================================================================

function openRunModal() {
  const modal = document.getElementById('modal-run');
  const dec = state.config?.decision || {};
  const exe = state.config?.execution || {};
  const navChk = document.getElementById('run-opt-laya-navigator');
  const visChk = document.getElementById('run-opt-laya-visual');
  const headChk = document.getElementById('run-opt-headed');
  if (navChk) {
    const cfgNav = document.getElementById('cfg-laya-navigator');
    navChk.checked = cfgNav ? cfgNav.checked : Boolean(dec.laya_web_navigator_enabled);
  }
  if (visChk) {
    const cfgVis = document.getElementById('cfg-laya-visual');
    visChk.checked = cfgVis ? cfgVis.checked : Boolean(dec.laya_visual_watch);
  }
  if (headChk) {
    const cfgHeadless = document.getElementById('cfg-headless');
    headChk.checked = cfgHeadless ? !cfgHeadless.checked : !Boolean(exe.headless ?? true);
  }
  if (modal) modal.classList.add('active');
}

function closeRunModal() {
  const modal = document.getElementById('modal-run');
  if (modal) modal.classList.remove('active');
}

async function executeRun() {
  closeRunModal();
  if (state.isConfigDirty) {
    await saveSettings();
  }
  const limitVal = document.getElementById('run-opt-limit')?.value;
  const isDryRun = document.getElementById('run-opt-dryrun')?.checked;
  const retryFailed = document.getElementById('run-opt-retry-failed')?.checked;
  const retryReview = document.getElementById('run-opt-retry-review')?.checked;
  const layaNav = document.getElementById('run-opt-laya-navigator')?.checked;
  const layaVisual = document.getElementById('run-opt-laya-visual')?.checked;
  const headedRun = document.getElementById('run-opt-headed')?.checked;
  const sheetUrl = document.getElementById('cfg-sheet-url')?.value?.trim();

  const payload = {
    limit: limitVal ? parseInt(limitVal, 10) : null,
    dry_run: Boolean(isDryRun),
    include_failed: Boolean(retryFailed),
    include_review: Boolean(retryReview),
    laya_navigator: Boolean(layaNav),
    laya_visual: Boolean(layaVisual),
    headless: !Boolean(headedRun),
    autonomous: true,
    sheet: sheetUrl || undefined,
  };

  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      showToast('Protocolo iniciado! Acompanhe a telemetria ao vivo.', 'success');
      startLiveTimer();
      switchTab('dashboard');
    } else {
      showToast(`Erro ao iniciar: ${data.detail || 'Falha'}`, 'danger');
    }
  } catch (err) {
    showToast(`Erro: ${err.message}`, 'danger');
  }
}

async function stopAutomation() {
  if (!confirm('Deseja realmente interromper o lote ativo de candidaturas agora?')) return;
  try {
    const res = await fetch('/api/stop', { method: 'POST' });
    if (res.ok) {
      showToast('Comando de parada enviado. Encerrando processos...', 'warning');
      stopLiveTimer();
    }
  } catch (err) {
    showToast('Erro ao parar automação.', 'danger');
  }
}

// ==============================================================================
// 12. Sistema de Notificações Toast
// ==============================================================================

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  let iconSvg = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--cyan);"><circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="16" y2="12"/><line x1="12" x2="12.01" y1="8" y2="8"/></svg>';
  if (type === 'success') {
    iconSvg = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--mint);"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
  } else if (type === 'danger') {
    iconSvg = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--crimson);"><circle cx="12" cy="12" r="10"/><line x1="15" x2="9" y1="9" y2="15"/><line x1="9" x2="15" y1="9" y2="15"/></svg>';
  } else if (type === 'warning') {
    iconSvg = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--amber);"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" x2="12" y1="9" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/></svg>';
  }

  toast.innerHTML = `
    <span style="display:inline-flex; align-items:center;">${iconSvg}</span>
    <span style="flex-grow:1;">${escapeHtml(message)}</span>
    <button style="background:transparent; border:none; color:var(--text-muted); cursor:pointer; font-size:1rem; padding:0 0.2rem; margin-left:0.5rem;" onclick="this.parentElement.remove()">&times;</button>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.25s ease';
    setTimeout(() => toast.remove(), 250);
  }, 4000);
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function sanitizeUrl(url) {
  if (!url) return '';
  const clean = String(url).trim();
  if (/^https?:\/\//i.test(clean) || clean.startsWith('/') || clean.startsWith('./')) {
    return escapeHtml(clean);
  }
  return '#';
}

