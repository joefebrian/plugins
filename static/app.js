const API = '/api';
const THEME_KEY = 'av-theme';
let currentProfileId = null;
let pollTimer = null;
let currentUser = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function getTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem(THEME_KEY, theme);
  updateThemeLabel();
}

function updateThemeLabel() {
  const label = $('#theme-toggle-label');
  if (!label) return;
  label.textContent = getTheme() === 'dark' ? 'Dark mode' : 'Light mode';
}

function refreshIcons() {
  if (window.lucide && typeof lucide.createIcons === 'function') {
    lucide.createIcons();
  }
}

function initTheme() {
  updateThemeLabel();
  refreshIcons();
  const btn = $('#btn-theme-toggle');
  if (btn) {
    btn.onclick = () => setTheme(getTheme() === 'dark' ? 'light' : 'dark');
  }
}

initTheme();

function fmtNum(n) {
  if (n == null) return '-';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}

function fmtMoney(n) {
  if (n == null) return '-';
  return 'Rp ' + n.toLocaleString('id-ID');
}

function fmtDate(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('id-ID', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function fmtDateShort(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleDateString('id-ID', {
    day: 'numeric', month: 'short', year: 'numeric',
  });
}

function getFilterParams() {
  const params = new URLSearchParams();
  params.set('status', $('#filter-status').value);
  params.set('sort_by', $('#filter-sort').value);
  const dateFrom = $('#filter-date-from').value;
  const dateTo = $('#filter-date-to').value;
  const minViews = $('#filter-min-views').value;
  const maxViews = $('#filter-max-views').value;
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  if (minViews) params.set('min_views', minViews);
  if (maxViews) params.set('max_views', maxViews);
  return params;
}

function getFilterBody() {
  const params = getFilterParams();
  return {
    status: params.get('status'),
    sort_by: params.get('sort_by'),
    date_from: params.get('date_from') || null,
    date_to: params.get('date_to') || null,
    min_views: params.get('min_views') ? parseInt(params.get('min_views'), 10) : null,
    max_views: params.get('max_views') ? parseInt(params.get('max_views'), 10) : null,
  };
}

function showToast(msg, type = 'success') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = `toast ${type}`;
  el.classList.remove('hidden');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add('hidden'), 6000);
}

function showLoading(text = 'Processing...') {
  $('#loading-text').textContent = text;
  $('#loading').classList.remove('hidden');
}

function hideLoading() {
  $('#loading').classList.add('hidden');
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, { credentials: 'same-origin', ...opts });
  if (res.status === 401) {
    window.location.href = '/login.html';
    throw new Error('Sesi berakhir, silakan login ulang');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || 'Request failed');
  return data;
}

async function pollJob(jobId, onDone) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const job = await api(`/jobs/${jobId}`);
      $('#loading-text').textContent = job.message;
      if (job.status === 'done') {
        clearInterval(pollTimer);
        hideLoading();
        onDone(job);
      } else if (job.status === 'error') {
        clearInterval(pollTimer);
        hideLoading();
        showToast(job.message, 'error');
      }
    } catch (e) {
      clearInterval(pollTimer);
      hideLoading();
      showToast(e.message, 'error');
    }
  }, 1500);
}

async function loadProfiles() {
  const profiles = await api('/profiles');
  const list = $('#profile-list');
  list.innerHTML = '';

  profiles.forEach((p) => {
    const el = document.createElement('div');
    el.className = 'profile-item' + (p.id === currentProfileId ? ' active' : '');
    el.dataset.id = p.id;
    const icon = p.platform === 'tiktok' ? '🎵' : '📸';
    el.innerHTML = `
      <div class="pi-icon">${icon}</div>
      <div>
        <div class="pi-name">@${p.username}</div>
        <div class="pi-meta">${p.total || 0} video · ${p.pending || 0} pending</div>
      </div>`;
    el.onclick = () => selectProfile(p.id);
    list.appendChild(el);
  });

  if (profiles.length === 0) {
    $('#empty-state').classList.remove('hidden');
    $('#dashboard').classList.add('hidden');
    currentProfileId = null;
  } else {
    const exists = profiles.some((p) => p.id === currentProfileId);
    if (!currentProfileId || !exists) {
      selectProfile(profiles[0].id);
    }
  }
}

async function selectProfile(id) {
  currentProfileId = id;
  $$('.profile-item').forEach((el) => {
    el.classList.toggle('active', Number(el.dataset.id) === id);
  });

  $('#empty-state').classList.add('hidden');
  $('#dashboard').classList.remove('hidden');

  const profile = await api(`/profiles/${id}`);
  renderProfile(profile);
  await loadVideos();
  await loadHeroes();
}

function renderProfile(p) {
  $('#profile-platform').textContent = p.platform;
  $('#profile-title').textContent = '@' + p.username;
  $('#profile-link').href = p.url;
  $('#stat-total').textContent = p.total || 0;
  $('#stat-downloaded').textContent = p.downloaded || 0;
  $('#stat-pending').textContent = p.pending || 0;
  $('#stat-gmv').textContent = fmtMoney(p.total_gmv || 0);
  $('#stat-commission').textContent = fmtMoney(p.total_commission || 0);
  $('#stat-scanned').textContent = fmtDate(p.last_scanned_at);
}

async function loadVideos() {
  const params = getFilterParams();
  const videos = await api(`/profiles/${currentProfileId}/videos?${params}`);
  const tbody = $('#video-table');
  tbody.innerHTML = '';
  const countEl = $('#filter-count');
  if (countEl) countEl.textContent = `${videos.length} video`;

  videos.forEach((v) => {
    const tr = document.createElement('tr');
    const statusCls = v.is_downloaded ? 'status-done' : 'status-pending';
    const statusText = v.is_downloaded ? '✓ Downloaded' : '○ Pending';
    tr.innerHTML = `
      <td><span class="status-badge ${statusCls}">${statusText}</span></td>
      <td>
        <div class="video-title">${v.title || 'Untitled'}</div>
        <div class="video-id">${v.platform_video_id}</div>
      </td>
      <td>${fmtDateShort(v.posted_at)}</td>
      <td>${fmtNum(v.views)}</td>
      <td>${fmtNum(v.likes)}</td>
      <td class="${v.gmv ? 'money' : 'money-empty'}">${fmtMoney(v.gmv)}</td>
      <td class="${v.commission ? 'money' : 'money-empty'}">${fmtMoney(v.commission)}</td>
      <td>
        <button class="btn btn-sm btn-secondary" data-dl="${v.platform_video_id}" title="Download">↓</button>
        <button class="btn btn-sm btn-ghost" data-edit="${v.id}" data-vid="${v.platform_video_id}" data-gmv="${v.gmv || ''}" data-comm="${v.commission || ''}" title="Edit GMV">✎</button>
        ${v.is_downloaded ? `<a class="btn btn-sm btn-ghost" href="/api/videos/${v.id}/file" target="_blank">▶</a>` : ''}
        ${(v.youtube_uploads || []).map((u) => `<a class="btn btn-sm btn-ghost" href="${u.youtube_url}" target="_blank" title="${u.channel_title || 'YouTube'}">YT</a>`).join('')}
        ${!(v.youtube_uploads || []).length && v.youtube_url ? `<a class="btn btn-sm btn-ghost" href="${v.youtube_url}" target="_blank" title="YouTube">YT</a>` : ''}
        ${(v.facebook_uploads || []).map((u) => `<a class="btn btn-sm btn-ghost" href="${u.post_url}" target="_blank" title="${u.page_name || 'Facebook'}">FB</a>`).join('')}
        <a class="btn btn-sm btn-ghost" href="${v.url}" target="_blank">↗</a>
      </td>`;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll('[data-dl]').forEach((btn) => {
    btn.onclick = () => downloadSingle(btn.dataset.dl);
  });
  tbody.querySelectorAll('[data-edit]').forEach((btn) => {
    btn.onclick = () => openEditGmv(btn.dataset);
  });
}

async function loadHeroes() {
  const data = await api(`/profiles/${currentProfileId}/heroes?top=8`);
  $('#hero-rank-badge').textContent = 'by ' + (data.ranked_by === 'gmv' ? 'GMV' : 'Engagement');

  const list = $('#hero-list');
  list.innerHTML = '';

  data.videos.forEach((v, i) => {
    const el = document.createElement('div');
    el.className = 'hero-item';
    el.innerHTML = `
      <div class="hero-rank">${i + 1}</div>
      <div class="hero-info">
        <div class="hero-title">${v.title || v.platform_video_id}</div>
        <div class="hero-gmv">${fmtMoney(v.gmv)}</div>
        <div class="hero-meta">${fmtNum(v.views)} views · ${fmtNum(v.likes)} likes · ${v.is_downloaded ? '✓ DL' : 'pending'}</div>
      </div>
      <a class="btn btn-sm btn-secondary" href="${v.url}" target="_blank">↗</a>`;
    list.appendChild(el);
  });
}

function openModal(id) {
  $(`#modal-${id}`).classList.remove('hidden');
}

function closeModals() {
  $$('.modal').forEach((m) => m.classList.add('hidden'));
}

function normalizeUsernameInput(value) {
  const raw = value.trim();
  const handles = [...raw.matchAll(/@([A-Za-z0-9._]+)/g)]
    .map((m) => m[1])
    .filter((h) => !['http', 'https', 'www'].includes(h.toLowerCase()));
  if (handles.length) return handles[handles.length - 1];

  const ig = [...raw.matchAll(/instagram\.com\/([A-Za-z0-9._]+)/gi)]
    .map((m) => m[1])
    .filter((h) => !['p', 'reel', 'reels', 'tv', 'stories'].includes(h.toLowerCase()));
  if (ig.length) return ig[ig.length - 1];

  const tt = [...raw.matchAll(/tiktok\.com\/@?([A-Za-z0-9._]+)/gi)].map((m) => m[1]);
  if (tt.length) return tt[tt.length - 1];

  return raw.replace(/^@/, '').split('/')[0].split('?')[0];
}

async function startScan(platform, username) {
  const clean = normalizeUsernameInput(username);
  showLoading(`Scanning @${clean}...`);
  closeModals();
  const job = await api('/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ platform, username: clean }),
  });
  pollJob(job.id, async (done) => {
    const s = done.result.scan || {};
    let msg = 'Scan selesai';
    if (s.new > 0) msg += `: ${s.new} video baru`;
    else if (s.incremental) msg += ': tidak ada video baru';
    if (s.total) msg += ` (${s.total} total di database)`;
    showToast(msg);
    if (done.result.profile) currentProfileId = done.result.profile.id;
    await loadProfiles();
    if (currentProfileId) await selectProfile(currentProfileId);
  });
}

function getDownloadOptions(overrides = {}) {
  const batchVal = $('#download-batch').value;
  const limit = batchVal === '' ? null : parseInt(batchVal, 10);
  const quality = $('#download-quality').value || 'best';
  return { limit, quality, only_pending: true, ...overrides };
}

async function downloadFiltered() {
  if (!currentProfileId) {
    showToast('Pilih profil dulu', 'error');
    return;
  }
  const opts = {
    ...getDownloadOptions({ only_pending: $('#filter-status').value === 'pending' }),
    ...getFilterBody(),
    apply_filters: true,
  };
  const label = opts.limit ? `${opts.limit} video` : 'semua hasil filter';
  showLoading(`Downloading ${label} (HQ)...`);
  try {
    const job = await api(`/profiles/${currentProfileId}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    });
    pollJob(job.id, async (done) => {
      const r = done.result || {};
      if (!r.total_attempted) {
        showToast(r.errors?.[0] || 'Tidak ada video untuk di-download', 'error');
        return;
      }
      let msg = `Download: ${r.success} berhasil, ${r.failed} gagal, ${r.skipped} skip`;
      if (r.errors?.length) msg += ` — ${r.errors[0]}`;
      showToast(msg, r.failed > 0 && r.success === 0 ? 'error' : 'success');
      await selectProfile(currentProfileId);
    });
  } catch (e) {
    hideLoading();
    showToast(e.message, 'error');
  }
}

async function downloadPending() {
  if (!currentProfileId) {
    showToast('Pilih profil dulu', 'error');
    return;
  }
  const opts = getDownloadOptions();
  const label = opts.limit ? `${opts.limit} video` : 'semua pending';
  showLoading(`Downloading ${label} (HQ)...`);
  try {
    const job = await api(`/profiles/${currentProfileId}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    });
    pollJob(job.id, async (done) => {
      const r = done.result || {};
      if (!r.total_attempted) {
        showToast(r.errors?.[0] || 'Tidak ada video pending untuk di-download', 'error');
        return;
      }
      let msg = `Download: ${r.success} berhasil, ${r.failed} gagal, ${r.skipped} skip`;
      if (r.errors?.length) msg += ` — ${r.errors[0]}`;
      showToast(msg, r.failed > 0 && r.success === 0 ? 'error' : 'success');
      await selectProfile(currentProfileId);
    });
  } catch (e) {
    hideLoading();
    showToast(e.message, 'error');
  }
}

async function downloadSingle(videoId) {
  const { quality } = getDownloadOptions();
  showLoading('Downloading video (HQ)...');
  try {
    const job = await api(`/profiles/${currentProfileId}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_ids: [videoId], only_pending: false, limit: 1, quality }),
    });
    pollJob(job.id, async (done) => {
      const r = done.result || {};
      if (r.success > 0) {
        showToast('Video HQ berhasil di-download ✓');
      } else {
        showToast(r.errors?.[0] || 'Download gagal', 'error');
      }
      await selectProfile(currentProfileId);
    });
  } catch (e) {
    hideLoading();
    showToast(e.message, 'error');
  }
}

// Event listeners
$('#btn-scan-open').onclick = () => openModal('scan');
$('#btn-scan-empty').onclick = () => openModal('scan');
$$('[data-close]').forEach((b) => (b.onclick = closeModals));
$$('.modal-backdrop').forEach((b) => (b.onclick = closeModals));

$('#form-scan').onsubmit = (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  startScan(fd.get('platform'), fd.get('username'));
};

$('#btn-rescan').onclick = async () => {
  const p = await api(`/profiles/${currentProfileId}`);
  startScan(p.platform, p.username);
};

$('#btn-download-pending').onclick = () => downloadPending();
$('#btn-download-filtered').onclick = () => downloadFiltered();
$('#btn-export-csv').onclick = () => {
  if (!currentProfileId) {
    showToast('Pilih profil dulu', 'error');
    return;
  }
  const params = getFilterParams();
  window.location.href = `/api/profiles/${currentProfileId}/videos/export.csv?${params}`;
};
$('#btn-import-gmv').onclick = () => { openModal('gmv'); loadTikTokShopConfig(); };

let oauthMonitoringTimer = null;
let aiMonitoringTimer = null;

function switchView(view) {
  const views = {
    profiles: '#view-profiles',
    youtube: '#view-youtube',
    facebook: '#view-facebook',
    threads: '#view-threads',
    settings: '#view-settings',
    'oauth-monitoring': '#view-oauth-monitoring',
  };
  if (!views[view]) return;
  Object.entries(views).forEach(([key, sel]) => {
    const el = $(sel);
    if (el) el.classList.toggle('hidden', view !== key);
  });
  $$('.nav-item[data-view]').forEach((el) => {
    el.classList.toggle('active', el.dataset.view === view);
  });
  document.querySelector('.main')?.scrollTo(0, 0);
  if (['youtube', 'facebook', 'threads', 'oauth-monitoring'].includes(view)) {
    $('#nav-multiupload-toggle')?.closest('.nav-group')?.classList.add('open');
  }
  if (oauthMonitoringTimer) {
    clearInterval(oauthMonitoringTimer);
    oauthMonitoringTimer = null;
  }
  if (aiMonitoringTimer) {
    clearInterval(aiMonitoringTimer);
    aiMonitoringTimer = null;
  }
  if (view === 'youtube') loadYouTubePage();
  if (view === 'facebook') loadFacebookPage();
  if (view === 'threads') loadThreadsPage();
  if (view === 'settings') {
    loadSettingsPage();
    aiMonitoringTimer = setInterval(loadSettingsPage, 30000);
  }
  if (view === 'oauth-monitoring') {
    loadOAuthMonitoringPage();
    oauthMonitoringTimer = setInterval(loadOAuthMonitoringPage, 30000);
  }
}

function initSidebarNavigation() {
  const nav = document.querySelector('.sidebar-nav');
  if (!nav || nav.dataset.navBound === '1') return;
  nav.dataset.navBound = '1';
  nav.addEventListener('click', (e) => {
    const btn = e.target.closest('.nav-item[data-view]');
    if (!btn || btn.disabled || btn.classList.contains('nav-soon')) return;
    e.preventDefault();
    switchView(btn.dataset.view);
  });
}
initSidebarNavigation();
$('#btn-goto-oauth-monitoring')?.addEventListener('click', () => switchView('oauth-monitoring'));
$('#link-oauth-monitoring')?.addEventListener('click', (e) => {
  e.preventDefault();
  switchView('oauth-monitoring');
});
$('#link-settings-ai')?.addEventListener('click', (e) => {
  e.preventDefault();
  switchView('settings');
});
$('#link-settings-threads')?.addEventListener('click', (e) => {
  e.preventDefault();
  switchView('settings');
});
$('#nav-multiupload-toggle').onclick = () => {
  $('#nav-multiupload-toggle').closest('.nav-group').classList.toggle('open');
};

function quotaBarClass(pct) {
  if (pct >= 100) return 'exhausted';
  if (pct >= 80) return 'warning';
  return 'ok';
}

function statusBadge(status) {
  const labels = {
    ok: 'OK',
    warning: 'Warning',
    exhausted: 'Exhausted',
    minute_limit: 'Min Limit',
    disabled: 'Disabled',
  };
  const cls = status === 'minute_limit' ? 'exhausted' : status;
  return `<span class="status-badge ${cls}">${labels[status] || status}</span>`;
}

function renderQuotaBar(used, limit, pct) {
  const cls = quotaBarClass(pct);
  return `
    <div class="quota-bar-wrap">
      <div class="quota-bar-label">${used} / ${limit} (${pct}%)</div>
      <div class="quota-bar"><div class="quota-bar-fill ${cls}" style="width:${Math.min(pct, 100)}%"></div></div>
    </div>`;
}

function fillOAuthAppSelect(apps, selectEl, overview) {
  if (!selectEl) return;
  const recommended = overview?.recommended_app_id;
  selectEl.innerHTML = '<option value="">Auto — pilih app tersedia</option>';
  apps.forEach((app) => {
    const opt = document.createElement('option');
    opt.value = app.id;
    const avail = app.available_for_grant ? '✓' : '✗ limit';
    const minInfo = `${app.tokens_last_minute || 0}/${app.minute_grant_limit || 18}/min`;
    opt.textContent = `${app.label} (${minInfo}, ${app.grants_today}/${app.grants_limit} day) ${avail}`;
    if (!app.available_for_grant) opt.disabled = true;
    if (app.id === recommended) opt.selected = true;
    selectEl.appendChild(opt);
  });
}

function renderOAuthMiniSummary(overview) {
  const el = $('#youtube-oauth-summary');
  if (!el) return;
  if (!overview?.apps?.length) {
    el.innerHTML = '<p>Belum ada OAuth App. Setup primary app di atas.</p>';
    return;
  }
  const lines = overview.apps.map((app) => {
    const icon = app.status === 'ok' ? '✓' : app.status === 'warning' ? '⚠' : '✗';
    return `<div class="mini-row"><span>${icon} ${app.label}</span><span>${app.tokens_last_minute || 0}/${app.minute_grant_limit || 18}/min · ${app.grants_today}/${app.grants_limit} day · ${app.channels_count} ch</span></div>`;
  });
  el.innerHTML = lines.join('');
}

function renderQuotaBanner(overview) {
  const banner = $('#youtube-quota-banner');
  if (!banner) return;
  if (!overview?.apps?.length) {
    banner.classList.add('hidden');
    return;
  }
  if (!overview.any_available) {
    banner.className = 'quota-banner danger';
    banner.innerHTML = '⚠ Semua OAuth App grant/refresh limit habis. Tambah backup app di <strong>OAuth Monitoring</strong> atau tunggu reset harian.';
    return;
  }
  const exhausted = overview.apps.filter((a) => a.status === 'exhausted' || a.status === 'minute_limit').length;
  if (exhausted > 0) {
    banner.className = 'quota-banner warning';
    banner.innerHTML = `⚠ ${exhausted} OAuth App limit habis (harian atau 18 token/menit) — system auto-rotate ke backup. <a href="#" id="banner-goto-monitoring">Lihat Monitoring</a>`;
    $('#banner-goto-monitoring')?.addEventListener('click', (e) => {
      e.preventDefault();
      switchView('oauth-monitoring');
    });
    return;
  }
  banner.classList.add('hidden');
}

function renderOAuthSummaryCards(overview) {
  const el = $('#oauth-summary-cards');
  if (!el) return;
  const availCls = overview.any_available ? 'ok' : 'bad';
  const rec = overview.apps.find((a) => a.id === overview.recommended_app_id);
  el.innerHTML = `
    <div class="oauth-summary-card">
      <div class="osc-label">Total Apps</div>
      <div class="osc-value">${overview.total_apps}</div>
    </div>
    <div class="oauth-summary-card ${availCls}">
      <div class="osc-label">Available</div>
      <div class="osc-value">${overview.available_apps}</div>
    </div>
    <div class="oauth-summary-card ${rec ? 'ok' : 'warn'}">
      <div class="osc-label">Recommended</div>
      <div class="osc-value" style="font-size:14px;margin-top:8px">${rec ? rec.label : '—'}</div>
    </div>`;
}

function renderOAuthAppsTable(apps) {
  const tbody = $('#oauth-apps-table');
  const countEl = $('#oauth-apps-count');
  if (!tbody) return;
  if (countEl) countEl.textContent = `${apps.length} apps`;
  if (!apps.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="modal-desc">Belum ada OAuth App. Tambah primary di YouTube Uploader atau backup di bawah.</td></tr>';
    return;
  }
  tbody.innerHTML = apps.map((app) => {
    const minReset = app.status === 'minute_limit' && app.minute_resets_in
      ? `<div class="app-id">reset ~${app.minute_resets_in}s</div>` : '';
    return `
    <tr data-app-id="${app.id}">
      <td>
        <div class="app-label">${app.label}</div>
        <div class="app-id">#${app.id} · priority ${app.priority}${app.id === 1 || app.priority <= 100 ? ' · primary' : ''}</div>
        ${app.last_error ? `<div class="app-id" title="${app.last_error}">⚠ ${app.last_error.slice(0, 60)}</div>` : ''}
      </td>
      <td>${statusBadge(app.status)}${minReset}</td>
      <td>${renderQuotaBar(app.tokens_last_minute || 0, app.minute_grant_limit || 18, app.minute_pct || 0)}</td>
      <td>${renderQuotaBar(app.grants_today, app.grants_limit, app.grants_pct)}</td>
      <td>${renderQuotaBar(app.refreshes_today, app.refreshes_limit, app.refreshes_pct)}</td>
      <td>${app.uploads_today}</td>
      <td>${app.channels_count}</td>
      <td>
        <div class="oauth-actions">
          <button class="btn btn-sm btn-ghost" data-reset-app="${app.id}" title="Clear rate limit flag">Reset</button>
          <button class="btn btn-sm btn-ghost" data-toggle-app="${app.id}" title="Toggle aktif/nonaktif">${app.is_active ? 'Disable' : 'Enable'}</button>
          <button class="btn btn-sm btn-danger" data-del-app="${app.id}">Hapus</button>
        </div>
      </td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('[data-reset-app]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/youtube/oauth-apps/${btn.dataset.resetApp}/reset-limit`, { method: 'POST' });
        showToast('Rate limit flag di-reset');
        loadOAuthMonitoringPage();
      } catch (e) {
        showToast(e.message, 'error');
      }
    };
  });
  tbody.querySelectorAll('[data-toggle-app]').forEach((btn) => {
    btn.onclick = async () => {
      const app = apps.find((a) => String(a.id) === btn.dataset.toggleApp);
      if (!app) return;
      try {
        await api(`/youtube/oauth-apps/${app.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_active: !app.is_active }),
        });
        showToast(app.is_active ? 'App dinonaktifkan' : 'App diaktifkan');
        loadOAuthMonitoringPage();
      } catch (e) {
        showToast(e.message, 'error');
      }
    };
  });
  tbody.querySelectorAll('[data-del-app]').forEach((btn) => {
    btn.onclick = async () => {
      const app = apps.find((a) => String(a.id) === btn.dataset.delApp);
      if (!app || !confirm(`Hapus OAuth App "${app.label}"? Channel terikat tetap ada tapi tidak bisa refresh token.`)) return;
      try {
        await api(`/youtube/oauth-apps/${app.id}`, { method: 'DELETE' });
        showToast('OAuth App dihapus');
        loadOAuthMonitoringPage();
      } catch (e) {
        showToast(e.message, 'error');
      }
    };
  });
}

function providerTypeLabel(type) {
  return { openai: 'OpenAI', gemini: 'Gemini' }[type] || type;
}

function renderAiSummaryCards(overview) {
  const el = $('#ai-summary-cards');
  if (!el) return;
  const availCls = overview.any_available ? 'ok' : 'bad';
  const rec = overview.providers?.find((p) => p.id === overview.recommended_provider_id);
  el.innerHTML = `
    <div class="oauth-summary-card">
      <div class="osc-label">Total Providers</div>
      <div class="osc-value">${overview.total_providers || 0}</div>
    </div>
    <div class="oauth-summary-card ${availCls}">
      <div class="osc-label">Available</div>
      <div class="osc-value">${overview.available_providers || 0}</div>
    </div>
    <div class="oauth-summary-card ${rec ? 'ok' : 'warn'}">
      <div class="osc-label">Recommended</div>
      <div class="osc-value" style="font-size:14px;margin-top:8px">${rec ? rec.label : '—'}</div>
    </div>`;
}

function renderAiProvidersTable(providers) {
  const tbody = $('#ai-providers-table');
  const countEl = $('#ai-providers-count');
  if (!tbody) return;
  if (countEl) countEl.textContent = `${providers.length} providers`;
  if (!providers.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="modal-desc">Belum ada AI provider. Tambah OpenAI atau Gemini di bawah, atau import dari OPENAI_API_KEY di .env (auto-seed saat pertama kali).</td></tr>';
    return;
  }
  tbody.innerHTML = providers.map((p) => `
    <tr data-provider-id="${p.id}">
      <td>
        <div class="app-label">${p.label}</div>
        <div class="app-id">#${p.id} · ${providerTypeLabel(p.provider)} · priority ${p.priority} · ${p.api_key || '—'}</div>
        ${p.last_error ? `<div class="app-id" title="${p.last_error}">⚠ ${p.last_error.slice(0, 60)}</div>` : ''}
      </td>
      <td>${statusBadge(p.status)}</td>
      <td>${renderQuotaBar(p.tokens_today, p.tokens_limit, p.tokens_pct)}</td>
      <td>${renderQuotaBar(p.requests_today, p.requests_limit, p.requests_pct)}</td>
      <td><span class="chip">${p.model || '—'}</span></td>
      <td>
        <div class="oauth-actions">
          <button class="btn btn-sm btn-ghost" data-reset-ai="${p.id}" title="Reset limit &amp; error flag">Reset</button>
          <button class="btn btn-sm btn-ghost" data-toggle-ai="${p.id}" title="Toggle aktif/nonaktif">${p.is_active ? 'Disable' : 'Enable'}</button>
          <button class="btn btn-sm btn-danger" data-del-ai="${p.id}">Hapus</button>
        </div>
      </td>
    </tr>`).join('');

  tbody.querySelectorAll('[data-reset-ai]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/settings/ai/providers/${btn.dataset.resetAi}/reset-limit`, { method: 'POST' });
        showToast('AI provider limit di-reset');
        loadSettingsPage();
      } catch (e) {
        showToast(e.message, 'error');
      }
    };
  });
  tbody.querySelectorAll('[data-toggle-ai]').forEach((btn) => {
    btn.onclick = async () => {
      const p = providers.find((x) => String(x.id) === btn.dataset.toggleAi);
      if (!p) return;
      try {
        await api(`/settings/ai/providers/${p.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ is_active: !p.is_active }),
        });
        showToast(p.is_active ? 'Provider dinonaktifkan' : 'Provider diaktifkan');
        loadSettingsPage();
      } catch (e) {
        showToast(e.message, 'error');
      }
    };
  });
  tbody.querySelectorAll('[data-del-ai]').forEach((btn) => {
    btn.onclick = async () => {
      const p = providers.find((x) => String(x.id) === btn.dataset.delAi);
      if (!p || !confirm(`Hapus AI provider "${p.label}"?`)) return;
      try {
        await api(`/settings/ai/providers/${p.id}`, { method: 'DELETE' });
        showToast('AI provider dihapus');
        loadSettingsPage();
      } catch (e) {
        showToast(e.message, 'error');
      }
    };
  });
}

function fmtUserStatus(status) {
  const map = { pending: 'Menunggu', approved: 'Disetujui', rejected: 'Ditolak' };
  return map[status] || status;
}

async function loadAdminUsers() {
  const section = $('#admin-users-section');
  const tbody = $('#admin-users-table');
  if (!section || !tbody || !currentUser?.is_admin) return;

  section.classList.remove('hidden');
  try {
    const users = await api('/admin/users');
    const pending = users.filter((u) => u.status === 'pending').length;
    const badge = $('#admin-pending-count');
    if (badge) badge.textContent = `${pending} pending`;

    tbody.innerHTML = users.map((u) => {
      const created = u.created_at ? new Date(u.created_at).toLocaleDateString('id-ID') : '-';
      const statusClass = `user-status-${u.status}`;
      let actions = '<span class="text-muted">—</span>';
      if (u.status === 'pending') {
        actions = `
          <button type="button" class="btn btn-sm btn-primary" data-approve-user="${u.id}">Setujui</button>
          <button type="button" class="btn btn-sm btn-ghost" data-reject-user="${u.id}">Tolak</button>`;
      }
      return `<tr>
        <td><strong>@${u.username}</strong>${u.role === 'admin' ? ' <span class="badge">admin</span>' : ''}</td>
        <td>${u.display_name || u.username}</td>
        <td class="${statusClass}">${fmtUserStatus(u.status)}</td>
        <td>${created}</td>
        <td class="oauth-actions">${actions}</td>
      </tr>`;
    }).join('');

    tbody.querySelectorAll('[data-approve-user]').forEach((btn) => {
      btn.onclick = async () => {
        try {
          await api(`/admin/users/${btn.dataset.approveUser}/approve`, { method: 'POST' });
          showToast('User disetujui ✓');
          loadAdminUsers();
        } catch (e) {
          showToast(e.message, 'error');
        }
      };
    });
    tbody.querySelectorAll('[data-reject-user]').forEach((btn) => {
      btn.onclick = async () => {
        const reason = prompt('Alasan penolakan (opsional):') || '';
        try {
          await api(`/admin/users/${btn.dataset.rejectUser}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason }),
          });
          showToast('User ditolak');
          loadAdminUsers();
        } catch (e) {
          showToast(e.message, 'error');
        }
      };
    });
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function loadSettingsPage() {
  try {
    await loadAdminUsers();
    const overview = await api('/settings/ai/monitoring');
    renderAiSummaryCards(overview);
    renderAiProvidersTable(overview.providers || []);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function loadOAuthMonitoringPage() {
  try {
    const overview = await api('/youtube/oauth-apps/monitoring');
    renderOAuthSummaryCards(overview);
    renderOAuthAppsTable(overview.apps || []);
    const redirectDefault = `${window.location.origin}/api/youtube/oauth/callback`;
    const backupForm = $('#form-oauth-backup');
    if (backupForm && !backupForm.redirect_uri.value) {
      const primary = overview.apps?.[0];
      backupForm.redirect_uri.value = primary?.redirect_uri || redirectDefault;
    }
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function loadYouTubeAppConfig() {
  const cfg = await api('/youtube/app-config');
  const form = $('#form-youtube-app-config');
  if (!form) return;
  form.client_id.value = cfg.client_id || '';
  form.client_secret.value = cfg.client_secret || '';
  form.redirect_uri.value = cfg.redirect_uri || `${window.location.origin}/api/youtube/oauth/callback`;
}

function fillChannelSelects(channels, selects) {
  selects.forEach((sel) => {
    if (!sel) return;
    sel.innerHTML = '';
    if (!channels.length) {
      sel.innerHTML = '<option value="">— Connect channel dulu —</option>';
      return;
    }
    channels.forEach((ch) => {
      const opt = document.createElement('option');
      opt.value = ch.id;
      opt.textContent = ch.label || ch.channel_title || `Channel #${ch.id}`;
      if (!ch.connected) opt.disabled = true;
      sel.appendChild(opt);
    });
  });
}

function renderYouTubeChannels(channels) {
  const list = $('#youtube-channel-list');
  const channelSelects = [$('#yt-upload-channel'), $('#yt-manual-channel')];
  if (!list) return;

  list.innerHTML = '';
  fillChannelSelects(channels, channelSelects);

  if (!channels.length) {
    list.innerHTML = '<p class="modal-desc">Belum ada channel. Klik <strong>+ Connect Akun</strong> untuk tambah.</p>';
    return;
  }

  channels.forEach((ch) => {
    const card = document.createElement('div');
    card.className = 'channel-card';
    const thumb = ch.channel_thumbnail
      ? `<img src="${ch.channel_thumbnail}" alt="" />`
      : '<div class="brand-icon" style="width:40px;height:40px;font-size:12px">YT</div>';
    card.innerHTML = `
      ${thumb}
      <div class="ch-info">
        <div class="ch-title">${ch.label || ch.channel_title || 'Channel'}</div>
        <div class="ch-meta">${ch.connected ? '✓ Terhubung' : '○ Disconnect'} · ${ch.channel_id || '-'}${ch.oauth_app_label ? ` · <span title="OAuth App">${ch.oauth_app_label}</span>` : ''}</div>
      </div>
      <div class="ch-actions">
        <button class="btn btn-sm btn-ghost" data-test-ch="${ch.id}">Test</button>
        <button class="btn btn-sm btn-ghost" data-disc-ch="${ch.id}">Disconnect</button>
        <button class="btn btn-sm btn-danger" data-del-ch="${ch.id}">Hapus</button>
      </div>`;
    list.appendChild(card);
  });

  list.querySelectorAll('[data-test-ch]').forEach((btn) => {
    btn.onclick = async () => {
      showLoading('Test channel...');
      try {
        const res = await api(`/youtube/channels/${btn.dataset.testCh}/test`, { method: 'POST' });
        hideLoading();
        showToast(`OK: ${res.channel_title}`);
        loadYouTubePage();
      } catch (e) {
        hideLoading();
        showToast(e.message, 'error');
      }
    };
  });
  list.querySelectorAll('[data-disc-ch]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('Disconnect channel ini?')) return;
      await api(`/youtube/channels/${btn.dataset.discCh}/disconnect`, { method: 'POST' });
      showToast('Channel disconnected');
      loadYouTubePage();
    };
  });
  list.querySelectorAll('[data-del-ch]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('Hapus channel dari sistem?')) return;
      await api(`/youtube/channels/${btn.dataset.delCh}`, { method: 'DELETE' });
      showToast('Channel dihapus');
      loadYouTubePage();
    };
  });
}

async function loadYouTubeProfileSelect() {
  const select = $('#yt-upload-profile');
  if (!select) return;
  const profiles = await api('/profiles');
  select.innerHTML = '';
  profiles.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `@${p.username} (${p.platform})`;
    if (p.id === currentProfileId) opt.selected = true;
    select.appendChild(opt);
  });
  if (!profiles.length) {
    select.innerHTML = '<option value="">— Scan profil dulu —</option>';
  }
}

let ytTitleVariants = [];

async function loadYouTubeTitleProfiles() {
  const sel = $('#yt-title-profile');
  if (!sel) return;
  const profiles = await api('/profiles');
  sel.innerHTML = '';
  profiles.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `@${p.username} (${p.platform})`;
    if (p.id === currentProfileId) opt.selected = true;
    sel.appendChild(opt);
  });
  if (!profiles.length) sel.innerHTML = '<option value="">— Scan profil dulu —</option>';
  await loadYouTubeTitleVideos();
}

async function loadYouTubeTitleVideos() {
  const profileSel = $('#yt-title-profile');
  const videoSel = $('#yt-title-video');
  if (!videoSel) return;
  videoSel.innerHTML = '<option value="">— Pakai keyword manual —</option>';
  const profileId = parseInt(profileSel?.value, 10);
  if (!profileId) return;
  const videos = await api(`/profiles/${profileId}/videos?status=downloaded&sort_by=gmv`);
  videos.filter((v) => v.is_downloaded).slice(0, 50).forEach((v) => {
    const opt = document.createElement('option');
    opt.value = v.id;
    opt.textContent = `${(v.title || v.platform_video_id).slice(0, 50)} · ${v.views || 0} views`;
    videoSel.appendChild(opt);
  });
}

function renderTitleVariants(data) {
  const container = $('#yt-title-variants');
  const chips = $('#yt-search-suggestions');
  const abBox = $('#yt-ab-actions');
  if (!container) return;

  ytTitleVariants = data.variants || [];
  container.innerHTML = '';

  if (data.ai_available && !data.ai_used && data.ai_error) {
    showToast(`AI fallback: ${data.ai_error}`, 'error');
  } else if (data.ai_used) {
    const prov = data.provider_used ? ` via ${data.provider_used}` : '';
    const tok = data.tokens_used ? ` · ${data.tokens_used} tokens` : '';
    showToast(`Judul di-generate dengan AI + YouTube search ✓${prov}${tok}`);
  }

  if (chips) {
    const sugg = data.search_suggestions || [];
    if (sugg.length) {
      chips.classList.remove('hidden');
      chips.innerHTML = `<span class="modal-desc" style="width:100%;margin-bottom:4px">Trend YouTube search:</span>${sugg.map((s) => `<span class="chip">${s}</span>`).join('')}`;
    } else {
      chips.classList.add('hidden');
    }
  }

  const sourceLabel = { ai_openai: 'AI OpenAI', ai_gemini: 'AI Gemini', youtube_search: 'YT Search', original: 'Asli' };
  ytTitleVariants.forEach((v, i) => {
    const card = document.createElement('label');
    card.className = 'title-variant-card';
    card.innerHTML = `
      <input type="checkbox" name="yt-ab-pick" value="${i}" />
      <div>
        <div class="tv-title">${v.title}</div>
        <div class="tv-meta">${sourceLabel[v.source] || v.source} · ${v.reason || ''}</div>
      </div>
      <button type="button" class="btn btn-sm btn-ghost" data-use-title="${i}">Pakai</button>`;
    container.appendChild(card);
  });

  container.querySelectorAll('[data-use-title]').forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      const item = ytTitleVariants[parseInt(btn.dataset.useTitle, 10)];
      if (!item) return;
      const titleInput = document.querySelector('#form-youtube-manual [name=title]');
      if (titleInput) {
        titleInput.value = item.title;
        const useFn = document.querySelector('#form-youtube-manual [name=use_filename_as_title]');
        if (useFn) useFn.checked = false;
      }
      showToast('Judul diisi ke form upload satuan');
      document.getElementById('panel-yt-single-upload')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  });

  if (abBox) abBox.classList.toggle('hidden', ytTitleVariants.length < 2);
}

async function loadYouTubePage() {
  try {
    await loadYouTubeAppConfig();
    const [channels, overview] = await Promise.all([
      api('/youtube/channels'),
      api('/youtube/oauth-apps/monitoring'),
    ]);
    renderYouTubeChannels(channels);
    fillOAuthAppSelect(overview.apps || [], $('#youtube-connect-app'), overview);
    fillChannelSelects(channels, [$('#yt-ab-channel'), $('#yt-manual-channel')]);
    renderOAuthMiniSummary(overview);
    renderQuotaBanner(overview);
    await loadYouTubeProfileSelect();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

$('#panel-ai-title-lab')?.addEventListener('toggle', (e) => {
  if (e.target.open) loadYouTubeTitleProfiles().catch((err) => showToast(err.message, 'error'));
});
$('#yt-title-profile')?.addEventListener('change', () => loadYouTubeTitleVideos());

function syncManualScheduleUi() {
  const enabled = !!$('#yt-manual-schedule-enabled')?.checked;
  const fields = $('#yt-manual-schedule-fields');
  const privacy = $('#yt-manual-privacy');
  const files = $('#yt-manual-files');
  const fileCount = files?.files?.length || 0;
  if (fields) fields.classList.toggle('hidden', !enabled);
  if (privacy && enabled) {
    privacy.value = 'private';
    privacy.disabled = true;
  } else if (privacy) {
    privacy.disabled = false;
  }
  const block = $('#yt-manual-schedule-block');
  if (block && fileCount > 1) {
    block.classList.add('schedule-disabled');
    if ($('#yt-manual-schedule-enabled')?.checked) {
      $('#yt-manual-schedule-enabled').checked = false;
      if (fields) fields.classList.add('hidden');
      if (privacy) privacy.disabled = false;
      showToast('Jadwal tayang hanya untuk 1 file', 'error');
    }
  } else if (block) {
    block.classList.remove('schedule-disabled');
  }
}

$('#yt-manual-schedule-enabled')?.addEventListener('change', syncManualScheduleUi);
$('#yt-manual-files')?.addEventListener('change', syncManualScheduleUi);

function syncBulkScheduleUi() {
  const enabled = !!$('#yt-bulk-schedule-enabled')?.checked;
  const fields = $('#yt-bulk-schedule-fields');
  const privacy = $('#yt-bulk-privacy');
  const limit = parseInt($('#form-youtube-upload [name=limit]')?.value, 10) || 10;
  if (fields) fields.classList.toggle('hidden', !enabled);
  if (privacy && enabled) {
    privacy.value = 'private';
    privacy.disabled = true;
  } else if (privacy) {
    privacy.disabled = false;
  }
  const preview = $('#yt-bulk-schedule-preview');
  if (preview && enabled) {
    const lastHours = Math.max(0, limit - 1) * 3;
    preview.textContent = `Video #1 di jadwal pertama, lalu tiap video +3 jam (total ${limit} video → terakhir +${lastHours} jam dari video pertama). Upload sebagai Private, tayang otomatis.`;
  }
}

$('#yt-bulk-schedule-enabled')?.addEventListener('change', syncBulkScheduleUi);
$('#form-youtube-upload [name=limit]')?.addEventListener('change', syncBulkScheduleUi);

$('#btn-yt-generate-titles')?.addEventListener('click', async () => {
  const profileId = parseInt($('#yt-title-profile')?.value, 10) || null;
  const videoId = parseInt($('#yt-title-video')?.value, 10) || null;
  const keyword = ($('#yt-title-keyword')?.value || '').trim();
  const useAi = !!$('#yt-title-use-ai')?.checked;

  showLoading('Generate judul...');
  try {
    const body = { use_ai: useAi, count: 5 };
    if (videoId) body.video_id = videoId;
    else if (profileId) body.profile_id = profileId;
    if (keyword) body.keyword = keyword;
    body.base_title = keyword;

    const data = await api('/youtube/titles/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    hideLoading();
    renderTitleVariants(data);
  } catch (e) {
    hideLoading();
    showToast(e.message, 'error');
  }
});

$('#btn-yt-run-ab')?.addEventListener('click', async () => {
  const channelId = parseInt($('#yt-ab-channel')?.value, 10);
  const videoId = parseInt($('#yt-title-video')?.value, 10);
  const picks = $$('input[name=yt-ab-pick]:checked').map((el) => ytTitleVariants[parseInt(el.value, 10)]?.title).filter(Boolean);

  if (!channelId) {
    showToast('Pilih channel untuk A/B test', 'error');
    return;
  }
  if (!videoId) {
    showToast('Pilih video dari profil untuk A/B test', 'error');
    return;
  }
  if (picks.length < 2) {
    showToast('Centang minimal 2 judul untuk A/B', 'error');
    return;
  }

  showLoading(`A/B test: upload ${picks.length} varian unlisted...`);
  try {
    const job = await api('/youtube/titles/ab-test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        youtube_channel_id: channelId,
        video_id: videoId,
        title_variants: picks,
        auto_thumbnail: !!$('#form-youtube-manual [name=auto_thumbnail]')?.checked,
      }),
    });
    pollJob(job.id, (done) => {
      const r = done.result || {};
      const links = (r.uploads || []).map((u) => `${u.variant}: ${u.title.slice(0, 40)}`).join(' · ');
      showToast(r.message || `A/B selesai — ${links}`);
      if (r.uploads?.length) {
        window.open(r.uploads[0].youtube_url, '_blank');
      }
    });
  } catch (e) {
    hideLoading();
    showToast(e.message, 'error');
  }
});

$('#form-youtube-app-config').onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    client_id: fd.get('client_id'),
    client_secret: fd.get('client_secret'),
    redirect_uri: fd.get('redirect_uri') || null,
  };
  if (!body.client_secret || String(body.client_secret).startsWith('••')) delete body.client_secret;
  try {
    const res = await api('/youtube/app-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showToast(res.message || 'OAuth app tersimpan');
    loadYouTubeAppConfig();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

$('#btn-youtube-connect').onclick = async () => {
  const label = ($('#youtube-connect-label')?.value || '').trim();
  const appSel = $('#youtube-connect-app');
  const oauthAppId = appSel?.value ? parseInt(appSel.value, 10) : null;
  try {
    const body = { label };
    if (oauthAppId) body.oauth_app_id = oauthAppId;
    const res = await api('/youtube/oauth/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.message) showToast(res.message, 'success');
    else if (res.oauth_app_label) showToast(`Connect via ${res.oauth_app_label}`, 'success');
    if (res.auth_url) window.location.href = res.auth_url;
  } catch (err) {
    showToast(err.message, 'error');
  }
};

$('#btn-oauth-refresh')?.addEventListener('click', () => loadOAuthMonitoringPage());
$('#btn-ai-refresh')?.addEventListener('click', () => loadSettingsPage());

$('#form-ai-provider')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const model = (fd.get('model') || '').trim();
  const body = {
    label: fd.get('label'),
    provider: fd.get('provider'),
    api_key: fd.get('api_key'),
    priority: parseInt(fd.get('priority'), 10) || 100,
    daily_token_limit: parseInt(fd.get('daily_token_limit'), 10) || 100000,
    daily_request_limit: parseInt(fd.get('daily_request_limit'), 10) || 500,
  };
  if (model) body.model = model;
  try {
    const res = await api('/settings/ai/providers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showToast(res.message || 'AI Provider ditambahkan');
    e.target.reset();
    e.target.priority.value = '100';
    e.target.daily_token_limit.value = '100000';
    e.target.daily_request_limit.value = '500';
    loadSettingsPage();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

$('#form-oauth-backup')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    label: fd.get('label'),
    client_id: fd.get('client_id'),
    client_secret: fd.get('client_secret'),
    redirect_uri: fd.get('redirect_uri') || null,
    priority: parseInt(fd.get('priority'), 10) || 200,
    minute_grant_limit: parseInt(fd.get('minute_grant_limit'), 10) || 18,
    daily_grant_limit: parseInt(fd.get('daily_grant_limit'), 10) || 100,
    daily_refresh_limit: parseInt(fd.get('daily_refresh_limit'), 10) || 5000,
  };
  try {
    const res = await api('/youtube/oauth-apps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showToast(res.message || 'Backup OAuth App ditambahkan');
    e.target.reset();
    e.target.priority.value = '200';
    e.target.minute_grant_limit.value = '18';
    e.target.daily_grant_limit.value = '100';
    e.target.daily_refresh_limit.value = '5000';
    loadOAuthMonitoringPage();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

$('#form-youtube-upload').onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const profileId = parseInt(fd.get('profile_id') || $('#yt-upload-profile')?.value, 10);
  const channelId = parseInt(fd.get('youtube_channel_id'), 10);
  if (!profileId) {
    showToast('Pilih profil sumber video', 'error');
    return;
  }
  if (!channelId) {
    showToast('Pilih target YouTube channel', 'error');
    return;
  }

  const tagsRaw = (fd.get('tags') || '').trim();
  const scheduleOn = !!e.target.schedule_enabled?.checked;
  const scheduleStart = e.target.schedule_start?.value;
  if (scheduleOn && !scheduleStart) {
    showToast('Isi waktu tayang video pertama untuk jadwal bulk', 'error');
    return;
  }

  const body = {
    youtube_channel_id: channelId,
    limit: parseInt(fd.get('limit'), 10) || 10,
    privacy: scheduleOn ? 'private' : fd.get('privacy'),
    category_id: '22',
    title_template: fd.get('title_template') || '{title}',
    description_template: fd.get('description_template') || '',
    tags: tagsRaw ? tagsRaw.split(',').map((t) => t.trim()).filter(Boolean) : null,
    skip_uploaded: !!e.target.skip_uploaded?.checked,
    only_downloaded: true,
    apply_filters: !!e.target.apply_filters?.checked,
    auto_thumbnail: !!e.target.auto_thumbnail?.checked,
    schedule_enabled: scheduleOn,
    schedule_interval_hours: 3,
  };
  if (scheduleOn && scheduleStart) {
    body.schedule_start = new Date(scheduleStart).toISOString();
  }
  if (body.apply_filters) Object.assign(body, getFilterBody());

  const loadingMsg = scheduleOn
    ? `Upload + jadwal ${body.limit} video (interval 3 jam)...`
    : `Upload ${body.limit} video ke YouTube...`;
  showLoading(loadingMsg);
  try {
    const job = await api(`/profiles/${profileId}/youtube-upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    pollJob(job.id, async (done) => {
      const r = done.result || {};
      if (!r.total_attempted) {
        showToast(r.errors?.[0] || 'Tidak ada video untuk di-upload', 'error');
        return;
      }
      let msg = `YouTube: ${r.success} uploaded, ${r.failed} gagal, ${r.skipped} skip`;
      if (r.channel_title) msg += ` → ${r.channel_title}`;
      if (r.scheduled && r.schedule_start) {
        const start = fmtDate(r.schedule_start);
        const end = r.schedule_last ? fmtDate(r.schedule_last) : '';
        msg += ` · Jadwal ${start}${end ? ` → ${end}` : ''} (3j)`;
      }
      if (r.errors?.length) msg += ` — ${r.errors[0]}`;
      showToast(msg, r.failed > 0 && r.success === 0 ? 'error' : 'success');
      if (profileId === currentProfileId) await selectProfile(currentProfileId);
      loadYouTubePage();
    });
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
};

$('#form-youtube-manual').onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  const channelId = form.channel_id?.value;
  const fileInput = form.querySelector('[name="files"]');
  if (!channelId) {
    showToast('Pilih target YouTube channel', 'error');
    return;
  }
  if (!fileInput?.files?.length) {
    showToast('Pilih file video dulu', 'error');
    return;
  }

  const scheduleOn = !!form.schedule_enabled?.checked;
  if (scheduleOn && fileInput.files.length !== 1) {
    showToast('Jadwal tayang hanya untuk upload 1 file', 'error');
    return;
  }
  if (scheduleOn && !form.publish_at?.value) {
    showToast('Isi waktu tayang untuk jadwal', 'error');
    return;
  }

  const fd = new FormData();
  for (const file of fileInput.files) fd.append('files', file);
  fd.append('title', form.title?.value || '');
  fd.append('description', form.description?.value || '');
  fd.append('privacy', scheduleOn ? 'private' : (form.privacy?.value || 'private'));
  fd.append('tags', form.tags?.value || '');
  fd.append('use_filename_as_title', form.use_filename_as_title?.checked ? 'true' : 'false');
  fd.append('auto_thumbnail', form.auto_thumbnail?.checked ? 'true' : 'false');
  fd.append('schedule_enabled', scheduleOn ? 'true' : 'false');
  if (scheduleOn && form.publish_at?.value) {
    const local = new Date(form.publish_at.value);
    fd.append('publish_at', local.toISOString());
  }

  showLoading(`Upload ${fileInput.files.length} file ke YouTube...`);
  try {
    const res = await fetch(`/api/youtube/channels/${channelId}/upload-manual`, {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (res.status === 401) {
      window.location.href = '/login.html';
      return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Upload gagal');

    pollJob(data.id, (done) => {
      const r = done.result || {};
      if (!r.total_attempted) {
        showToast(r.errors?.[0] || 'Upload gagal', 'error');
        return;
      }
      let msg = `Manual upload: ${r.success} berhasil, ${r.failed} gagal`;
      if (r.uploads?.length) msg += ` — ${r.uploads[0].youtube_url}`;
      if (r.errors?.length) msg += ` — ${r.errors[0]}`;
      showToast(msg, r.failed > 0 && r.success === 0 ? 'error' : 'success');
      form.reset();
      if (form.use_filename_as_title) form.use_filename_as_title.checked = true;
      syncManualScheduleUi();
      loadYouTubePage();
    });
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
};

async function loadFacebookAppConfig() {
  const cfg = await api('/facebook/app-config');
  const form = $('#form-facebook-app-config');
  if (!form) return;
  form.app_id.value = cfg.app_id || '';
  form.app_secret.value = cfg.app_secret || '';
  form.redirect_uri.value = cfg.redirect_uri || `${window.location.origin}/api/facebook/oauth/callback`;
}

function fillFacebookPageSelects(pages, selects) {
  selects.forEach((sel) => {
    if (!sel) return;
    sel.innerHTML = '';
    if (!pages.length) {
      sel.innerHTML = '<option value="">— Connect Page dulu —</option>';
      return;
    }
    pages.forEach((p) => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.label || p.page_name || `Page #${p.id}`;
      if (!p.connected) opt.disabled = true;
      sel.appendChild(opt);
    });
  });
}

function renderFacebookPages(pages) {
  const list = $('#facebook-page-list');
  const selects = [$('#fb-upload-page'), $('#fb-manual-page')];
  if (!list) return;

  list.innerHTML = '';
  fillFacebookPageSelects(pages, selects);

  if (!pages.length) {
    list.innerHTML = '<p class="modal-desc">Belum ada Page. Klik <strong>+ Connect Page</strong> untuk tambah.</p>';
    return;
  }

  pages.forEach((p) => {
    const card = document.createElement('div');
    card.className = 'channel-card';
    const thumb = p.page_thumbnail
      ? `<img src="${p.page_thumbnail}" alt="" />`
      : '<div class="brand-icon" style="width:40px;height:40px;font-size:12px">f</div>';
    card.innerHTML = `
      ${thumb}
      <div class="ch-info">
        <div class="ch-title">${p.label || p.page_name || 'Page'}</div>
        <div class="ch-meta">${p.connected ? '✓ Terhubung' : '○ Disconnect'} · ${p.page_id || '-'}</div>
      </div>
      <div class="ch-actions">
        <button class="btn btn-sm btn-ghost" data-test-fb="${p.id}">Test</button>
        <button class="btn btn-sm btn-ghost" data-disc-fb="${p.id}">Disconnect</button>
        <button class="btn btn-sm btn-danger" data-del-fb="${p.id}">Hapus</button>
      </div>`;
    list.appendChild(card);
  });

  list.querySelectorAll('[data-test-fb]').forEach((btn) => {
    btn.onclick = async () => {
      showLoading('Test Page...');
      try {
        const res = await api(`/facebook/pages/${btn.dataset.testFb}/test`, { method: 'POST' });
        hideLoading();
        showToast(`OK: ${res.page_name}`);
        loadFacebookPage();
      } catch (e) {
        hideLoading();
        showToast(e.message, 'error');
      }
    };
  });
  list.querySelectorAll('[data-disc-fb]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('Disconnect Page ini?')) return;
      await api(`/facebook/pages/${btn.dataset.discFb}/disconnect`, { method: 'POST' });
      showToast('Page disconnected');
      loadFacebookPage();
    };
  });
  list.querySelectorAll('[data-del-fb]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('Hapus Page dari sistem?')) return;
      await api(`/facebook/pages/${btn.dataset.delFb}`, { method: 'DELETE' });
      showToast('Page dihapus');
      loadFacebookPage();
    };
  });
}

async function loadFacebookProfileSelect() {
  const select = $('#fb-upload-profile');
  if (!select) return;
  const profiles = await api('/profiles');
  select.innerHTML = '';
  profiles.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `@${p.username} (${p.platform})`;
    if (p.id === currentProfileId) opt.selected = true;
    select.appendChild(opt);
  });
  if (!profiles.length) {
    select.innerHTML = '<option value="">— Scan profil dulu —</option>';
  }
}

async function loadFacebookPage() {
  try {
    await loadFacebookAppConfig();
    const pages = await api('/facebook/pages');
    renderFacebookPages(pages);
    await loadFacebookProfileSelect();
  } catch (e) {
    showToast(e.message, 'error');
  }
}

$('#form-facebook-app-config')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    label: 'Facebook App',
    app_id: fd.get('app_id'),
    app_secret: fd.get('app_secret'),
    redirect_uri: fd.get('redirect_uri') || null,
  };
  if (!body.app_secret || String(body.app_secret).startsWith('••')) delete body.app_secret;
  try {
    const res = await api('/facebook/app-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showToast(res.message || 'Meta App tersimpan');
    loadFacebookAppConfig();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

$('#btn-facebook-connect')?.addEventListener('click', async () => {
  const label = ($('#facebook-connect-label')?.value || '').trim();
  try {
    const res = await api('/facebook/oauth/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    if (res.auth_url) window.location.href = res.auth_url;
  } catch (err) {
    showToast(err.message, 'error');
  }
});

$('#form-facebook-upload')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const profileId = parseInt(fd.get('profile_id') || $('#fb-upload-profile')?.value, 10);
  const pageId = parseInt(fd.get('facebook_page_id'), 10);
  if (!profileId) {
    showToast('Pilih profil sumber video', 'error');
    return;
  }
  if (!pageId) {
    showToast('Pilih target Facebook Page', 'error');
    return;
  }

  const body = {
    facebook_page_id: pageId,
    limit: parseInt(fd.get('limit'), 10) || 10,
    published: fd.get('published') === 'true',
    title_template: fd.get('title_template') || '{title}',
    description_template: fd.get('description_template') || '',
    skip_uploaded: !!e.target.skip_uploaded?.checked,
    only_downloaded: true,
    apply_filters: !!e.target.apply_filters?.checked,
  };
  if (body.apply_filters) Object.assign(body, getFilterBody());

  showLoading(`Upload ${body.limit} video ke Facebook...`);
  try {
    const job = await api(`/profiles/${profileId}/facebook-upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    pollJob(job.id, async (done) => {
      const r = done.result || {};
      if (!r.total_attempted) {
        showToast(r.errors?.[0] || 'Tidak ada video untuk di-upload', 'error');
        return;
      }
      let msg = `Facebook: ${r.success} uploaded, ${r.failed} gagal, ${r.skipped} skip`;
      if (r.page_name) msg += ` → ${r.page_name}`;
      if (r.errors?.length) msg += ` — ${r.errors[0]}`;
      showToast(msg, r.failed > 0 && r.success === 0 ? 'error' : 'success');
      if (profileId === currentProfileId) await selectProfile(currentProfileId);
      loadFacebookPage();
    });
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
});

$('#form-facebook-manual')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const pageId = form.page_id?.value;
  const fileInput = form.querySelector('[name="files"]');
  if (!pageId) {
    showToast('Pilih target Facebook Page', 'error');
    return;
  }
  if (!fileInput?.files?.length) {
    showToast('Pilih file video dulu', 'error');
    return;
  }

  const fd = new FormData();
  for (const file of fileInput.files) fd.append('files', file);
  fd.append('title', form.title?.value || '');
  fd.append('description', form.description?.value || '');
  fd.append('published', form.published?.value || 'true');
  fd.append('use_filename_as_title', form.use_filename_as_title?.checked ? 'true' : 'false');

  showLoading(`Upload ${fileInput.files.length} file ke Facebook...`);
  try {
    const res = await fetch(`/api/facebook/pages/${pageId}/upload-manual`, {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (res.status === 401) {
      window.location.href = '/login.html';
      return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Upload gagal');

    pollJob(data.id, (done) => {
      const r = done.result || {};
      if (!r.total_attempted) {
        showToast(r.errors?.[0] || 'Upload gagal', 'error');
        return;
      }
      let msg = `Manual upload: ${r.success} berhasil, ${r.failed} gagal`;
      if (r.uploads?.length) msg += ` — ${r.uploads[0].post_url}`;
      if (r.errors?.length) msg += ` — ${r.errors[0]}`;
      showToast(msg, r.failed > 0 && r.success === 0 ? 'error' : 'success');
      form.reset();
      if (form.use_filename_as_title) form.use_filename_as_title.checked = true;
      loadFacebookPage();
    });
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
});

function fillThreadsSelects(accounts, selects) {
  selects.forEach((sel) => {
    if (!sel) return;
    sel.innerHTML = '';
    if (!accounts.length) {
      sel.innerHTML = '<option value="">— Connect akun dulu —</option>';
      return;
    }
    accounts.forEach((a) => {
      const opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.label || `@${a.username}` || `Account #${a.id}`;
      if (!a.connected) opt.disabled = true;
      sel.appendChild(opt);
    });
  });
}

function renderThreadsAccounts(accounts) {
  const el = $('#threads-account-list');
  if (!el) return;
  if (!accounts.length) {
    el.innerHTML = '<p class="modal-desc">Belum ada akun. Klik + Connect Akun.</p>';
    return;
  }
  el.innerHTML = accounts.map((a) => `
    <div class="channel-card" data-threads-id="${a.id}">
      <div class="channel-info">
        <div class="channel-name">${a.label || '@' + (a.username || a.threads_user_id)}</div>
        <div class="channel-meta">${a.voice_locale?.toUpperCase() || 'ID'} · ${a.voice_style || 'genz'} · ${a.niche || 'no niche'}</div>
        <div class="channel-meta">${a.autopost?.enabled ? '🟢 Auto-post ON' : '⚪ Auto-post off'} · ${a.autopost?.posts_today || 0}/${a.autopost?.posts_per_day || 6} hari ini</div>
      </div>
      <div class="channel-actions">
        <button class="btn btn-sm btn-ghost" data-threads-test="${a.id}">Test</button>
        <button class="btn btn-sm btn-ghost" data-threads-disconnect="${a.id}">Disconnect</button>
        <button class="btn btn-sm btn-danger" data-threads-del="${a.id}">Hapus</button>
      </div>
    </div>`).join('');

  el.querySelectorAll('[data-threads-test]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const r = await api(`/threads/accounts/${btn.dataset.threadsTest}/test`, { method: 'POST' });
        showToast(`OK @${r.username || 'connected'}`);
      } catch (e) { showToast(e.message, 'error'); }
    };
  });
  el.querySelectorAll('[data-threads-disconnect]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/threads/accounts/${btn.dataset.threadsDisconnect}/disconnect`, { method: 'POST' });
        showToast('Disconnected');
        loadThreadsPage();
      } catch (e) { showToast(e.message, 'error'); }
    };
  });
  el.querySelectorAll('[data-threads-del]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('Hapus akun Threads ini?')) return;
      try {
        await api(`/threads/accounts/${btn.dataset.threadsDel}`, { method: 'DELETE' });
        showToast('Akun dihapus');
        loadThreadsPage();
      } catch (e) { showToast(e.message, 'error'); }
    };
  });
}

function renderThreadsTopics(data) {
  const list = $('#threads-topic-list');
  const hints = $('#threads-topic-hints');
  if (!list) return;
  list.innerHTML = '';
  if (hints) {
    const h = data.keyword_hints || [];
    if (h.length) {
      hints.classList.remove('hidden');
      hints.innerHTML = `<span class="modal-desc" style="width:100%">Trend hints:</span>${h.map((s) => `<span class="chip">${s}</span>`).join('')}`;
    } else hints.classList.add('hidden');
  }
  (data.topics || []).forEach((t, i) => {
    const card = document.createElement('div');
    card.className = 'title-variant-card';
    card.innerHTML = `
      <div>
        <div class="tv-title">${t.topic}</div>
        <div class="tv-meta">${t.hook || ''}</div>
        <div class="tv-meta" style="margin-top:6px">${t.caption || ''}</div>
        <div class="tv-meta">💡 ${t.engagement_tip || ''}</div>
      </div>
      <button type="button" class="btn btn-sm btn-ghost" data-use-topic="${i}">Pakai</button>`;
    list.appendChild(card);
  });
  list.querySelectorAll('[data-use-topic]').forEach((btn) => {
    btn.onclick = () => {
      const t = data.topics[parseInt(btn.dataset.useTopic, 10)];
      if (!t) return;
      const form = $('#form-threads-post');
      if (form?.caption) form.caption.value = t.caption || t.hook || '';
      if (form?.topic_tag) form.topic_tag.value = t.topic_tag || '';
      showToast('Caption diisi ke Post Manual');
    };
  });
  if (data.ai_used) showToast(`Topik generated via ${data.provider_used || 'AI'} ✓`);
}

async function loadThreadsProfileSelects() {
  const profiles = await api('/profiles');
  const selects = [$('#threads-bulk-profile'), $('#threads-autopost-profile')];
  selects.forEach((sel) => {
    if (!sel) return;
    sel.innerHTML = '<option value="">— Opsional —</option>';
    profiles.forEach((p) => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `@${p.username} (${p.platform})`;
      if (p.id === currentProfileId) opt.selected = true;
      sel.appendChild(opt);
    });
  });
}

async function loadThreadsPage() {
  try {
    const accounts = await api('/threads/accounts');
    renderThreadsAccounts(accounts);
    const sels = [
      $('#threads-voice-account'), $('#threads-post-account'), $('#threads-autopost-account'),
      $('#threads-bulk-account'), $('#threads-topic-account'),
    ];
    fillThreadsSelects(accounts, sels);
    await loadThreadsProfileSelects();

    const voiceAcc = parseInt($('#threads-voice-account')?.value, 10);
    const acc = accounts.find((a) => a.id === voiceAcc) || accounts[0];
    if (acc) {
      if ($('#threads-voice-locale')) $('#threads-voice-locale').value = acc.voice_locale || 'id';
      if ($('#threads-voice-style')) $('#threads-voice-style').value = acc.voice_style || 'genz';
      if ($('#threads-niche')) $('#threads-niche').value = acc.niche || '';
      const ap = acc.autopost;
      if (ap && $('#form-threads-autopost')) {
        const f = $('#form-threads-autopost');
        if (f.enabled) f.enabled.checked = !!ap.enabled;
        if (f.interval_hours) f.interval_hours.value = ap.interval_hours || 4;
        if (f.posts_per_day) f.posts_per_day.value = ap.posts_per_day || 6;
        if (f.post_video) f.post_video.checked = ap.post_video !== false;
        if (f.topic_seed) f.topic_seed.value = ap.topic_seed || '';
        if ($('#threads-autopost-profile') && ap.profile_id) $('#threads-autopost-profile').value = ap.profile_id;
        const st = $('#threads-autopost-status');
        if (st) st.textContent = ap.next_run_at ? `Next run: ${fmtDate(ap.next_run_at)}` : '';
      }
    }
  } catch (e) {
    showToast(e.message, 'error');
  }
}

$('#btn-threads-connect')?.addEventListener('click', async () => {
  const label = ($('#threads-connect-label')?.value || '').trim();
  try {
    const res = await api('/threads/oauth/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    if (res.auth_url) window.location.href = res.auth_url;
  } catch (err) {
    showToast(err.message, 'error');
  }
});

$('#threads-voice-locale')?.addEventListener('change', () => {
  const loc = $('#threads-voice-locale')?.value;
  const styleSel = $('#threads-voice-style');
  if (!styleSel) return;
  if (loc === 'us') {
    styleSel.innerHTML = '<option value="us_slang" selected>US Slang</option>';
  } else {
    styleSel.innerHTML = '<option value="genz" selected>Gen Z</option><option value="millennial">Milenial</option>';
  }
});

$('#btn-threads-save-voice')?.addEventListener('click', async () => {
  const id = parseInt($('#threads-voice-account')?.value, 10);
  if (!id) { showToast('Pilih akun', 'error'); return; }
  try {
    await api(`/threads/accounts/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        voice_locale: $('#threads-voice-locale')?.value,
        voice_style: $('#threads-voice-style')?.value,
        niche: ($('#threads-niche')?.value || '').trim(),
      }),
    });
    showToast('Voice & niche tersimpan');
    loadThreadsPage();
  } catch (e) { showToast(e.message, 'error'); }
});

$('#btn-threads-generate-topics')?.addEventListener('click', async () => {
  const accId = parseInt($('#threads-topic-account')?.value, 10) || null;
  const niche = ($('#threads-niche')?.value || $('#threads-voice-account option:checked')?.textContent || 'lifestyle').trim();
  const locale = $('#threads-voice-locale')?.value || 'id';
  const style = $('#threads-voice-style')?.value || 'genz';
  showLoading('Generate topik viral...');
  try {
    const data = await api('/threads/topics/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ niche, locale, style, count: 8, account_id: accId }),
    });
    hideLoading();
    renderThreadsTopics(data);
  } catch (e) {
    hideLoading();
    showToast(e.message, 'error');
  }
});

$('#form-threads-post')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const accountId = parseInt(e.target.account_id?.value, 10);
  if (!accountId) { showToast('Pilih akun', 'error'); return; }
  showLoading('Posting ke Threads...');
  try {
    const res = await api(`/threads/accounts/${accountId}/post`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        caption: e.target.caption.value,
        topic_tag: e.target.topic_tag?.value || null,
      }),
    });
    hideLoading();
    showToast(res.message || 'Posted ✓');
    if (res.post_url) window.open(res.post_url, '_blank');
    loadThreadsPage();
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
});

$('#form-threads-autopost')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const accountId = parseInt($('#threads-autopost-account')?.value, 10);
  if (!accountId) { showToast('Pilih akun', 'error'); return; }
  const fd = new FormData(e.target);
  const body = {
    enabled: !!$('#threads-autopost-enabled')?.checked,
    interval_hours: parseFloat(fd.get('interval_hours')) || 4,
    posts_per_day: parseInt(fd.get('posts_per_day'), 10) || 6,
    post_video: !!e.target.post_video?.checked,
    profile_id: parseInt($('#threads-autopost-profile')?.value, 10) || null,
    topic_seed: (fd.get('topic_seed') || '').trim() || null,
  };
  try {
    const res = await api(`/threads/accounts/${accountId}/autopost`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showToast(res.message || 'Auto-post disimpan');
    loadThreadsPage();
  } catch (err) { showToast(err.message, 'error'); }
});

$('#btn-threads-autopost-run')?.addEventListener('click', async () => {
  const accountId = parseInt($('#threads-autopost-account')?.value, 10);
  if (!accountId) { showToast('Pilih akun', 'error'); return; }
  showLoading('Auto-post sekarang...');
  try {
    const job = await api(`/threads/accounts/${accountId}/autopost/run`, { method: 'POST' });
    pollJob(job.id, (done) => {
      const r = done.result || {};
      if (r.posted) showToast(`Posted ✓ ${r.post_url || ''}`);
      else if (r.skipped) showToast(`Skip: ${r.reason}`, 'error');
      else showToast(r.error || 'Gagal', 'error');
      loadThreadsPage();
    });
  } catch (e) {
    hideLoading();
    showToast(e.message, 'error');
  }
});

$('#form-threads-bulk')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const profileId = parseInt($('#threads-bulk-profile')?.value, 10);
  const accountId = parseInt($('#threads-bulk-account')?.value, 10);
  if (!profileId || !accountId) {
    showToast('Pilih profil & akun Threads', 'error');
    return;
  }
  const fd = new FormData(e.target);
  const body = {
    threads_account_id: accountId,
    limit: parseInt(fd.get('limit'), 10) || 5,
    use_ai_caption: !!e.target.use_ai_caption?.checked,
    skip_uploaded: !!e.target.skip_uploaded?.checked,
  };
  showLoading(`Bulk post ${body.limit} ke Threads...`);
  try {
    const job = await api(`/profiles/${profileId}/threads-upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    pollJob(job.id, (done) => {
      const r = done.result || {};
      let msg = `Threads: ${r.success} ok, ${r.failed} gagal`;
      if (r.account) msg += ` → @${r.account}`;
      if (r.errors?.length) msg += ` — ${r.errors[0]}`;
      showToast(msg, r.failed > 0 && r.success === 0 ? 'error' : 'success');
      loadThreadsPage();
    });
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
});

$('#btn-delete-profile').onclick = async () => {
  if (!currentProfileId) return;
  const p = await api(`/profiles/${currentProfileId}`);
  if (!confirm(`Hapus profil @${p.username} dan semua datanya?\nVideo yang sudah di-download juga akan dihapus.`)) return;
  try {
    const res = await api(`/profiles/${currentProfileId}`, { method: 'DELETE' });
    showToast(`Profil @${res.deleted_profile} dihapus (${res.deleted_videos} video)`);
    currentProfileId = null;
    await loadProfiles();
  } catch (e) {
    showToast(e.message, 'error');
  }
};

// GMV modal tabs
$$('#modal-gmv .tab').forEach((tab) => {
  tab.onclick = () => {
    $$('#modal-gmv .tab').forEach((t) => t.classList.remove('active'));
    $$('#modal-gmv .tab-panel').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    $(`#tab-${tab.dataset.tab}`).classList.add('active');
  };
});

function openEditGmv(dataset) {
  openModal('gmv');
  $$('#modal-gmv .tab').forEach((t) => t.classList.remove('active'));
  $$('#modal-gmv .tab-panel').forEach((p) => p.classList.remove('active'));
  $('#modal-gmv .tab[data-tab="manual"]').classList.add('active');
  $('#tab-manual').classList.add('active');
  const form = $('#form-gmv-manual');
  form.video_id.value = dataset.vid;
  form.gmv.value = dataset.gmv || '';
  form.commission.value = dataset.comm || '';
  form.dataset.dbId = dataset.edit;
}

$('#form-gmv').onsubmit = async (e) => {
  e.preventDefault();
  const file = e.target.file.files[0];
  if (!file) return;
  showLoading('Importing GMV...');
  closeModals();
  const fd = new FormData();
  fd.append('file', file);
  try {
    const result = await api(`/profiles/${currentProfileId}/import-gmv`, {
      method: 'POST',
      body: fd,
    });
    hideLoading();
    showToast(`GMV updated: ${result.updated} video, ${result.unmatched} tidak cocok`);
    await selectProfile(currentProfileId);
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
};

async function loadTikTokShopConfig() {
  try {
    const cfg = await api('/tiktok-shop/config');
    const status = $('#api-status');
    const form = $('#form-tiktok-shop');
    if (!cfg.configured) {
      status.textContent = 'Belum dikonfigurasi — isi App Key & Secret';
      status.className = 'api-status warn';
      return;
    }
    form.app_key.value = cfg.app_key || '';
    form.app_secret.placeholder = cfg.has_app_secret ? '•••••••• (tersimpan)' : '';
    form.access_token.placeholder = cfg.has_access_token ? '•••••••• (tersimpan)' : '';
    form.shop_cipher.value = cfg.shop_cipher || '';
    form.region.value = cfg.region || 'ID';
    const syncInfo = cfg.last_sync_at ? ` · Terakhir sync: ${fmtDate(cfg.last_sync_at)}` : '';
    const tokenInfo = cfg.has_access_token ? 'Access Token ✓' : 'Access Token belum diisi';
    status.textContent = `API terkonfigurasi · ${tokenInfo}${syncInfo}`;
    status.className = 'api-status ' + (cfg.has_access_token ? 'ok' : 'warn');
  } catch (_) {}
}

$('#form-tiktok-shop').onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    app_key: fd.get('app_key'),
    region: fd.get('region'),
    shop_cipher: fd.get('shop_cipher') || null,
    is_active: true,
  };
  const secret = fd.get('app_secret');
  const token = fd.get('access_token');
  if (secret) body.app_secret = secret;
  if (token) body.access_token = token;

  try {
    const res = await api('/tiktok-shop/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showToast(res.message);
    loadTikTokShopConfig();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

$('#btn-test-api').onclick = async () => {
  showLoading('Testing koneksi API...');
  try {
    const res = await api('/tiktok-shop/test', { method: 'POST' });
    hideLoading();
    showToast(res.message || 'Koneksi berhasil');
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
};

$('#btn-sync-api').onclick = async () => {
  if (!currentProfileId) {
    showToast('Pilih profil dulu', 'error');
    return;
  }
  const days = parseInt($('#form-tiktok-shop').sync_days.value, 10) || 30;
  showLoading(`Sync GMV ${days} hari dari TikTok Shop API...`);
  try {
    const job = await api(`/profiles/${currentProfileId}/sync-gmv-api`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days }),
    });
    pollJob(job.id, async (done) => {
      const r = done.result || {};
      showToast(`Sync: ${r.updated} video updated, ${r.unmatched} tidak cocok (${r.api_records || 0} dari API)`);
      await selectProfile(currentProfileId);
      loadTikTokShopConfig();
    });
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
};

$('#form-gmv-paste').onsubmit = async (e) => {
  e.preventDefault();
  const text = e.target.text.value.trim();
  if (!text) return;
  showLoading('Importing data...');
  closeModals();
  try {
    const result = await api(`/profiles/${currentProfileId}/import-gmv-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    hideLoading();
    showToast(`Import: ${result.updated} updated, ${result.unmatched} tidak cocok`);
    await selectProfile(currentProfileId);
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
};

$('#form-gmv-manual').onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  const gmv = form.gmv.value ? parseFloat(form.gmv.value) : null;
  const commission = form.commission.value ? parseFloat(form.commission.value) : null;
  const dbId = form.dataset.dbId;

  showLoading('Menyimpan...');
  closeModals();
  try {
    if (dbId) {
      await api(`/videos/${dbId}/metrics`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gmv, commission }),
      });
    } else {
      const videoId = form.video_id.value.trim();
      if (!videoId) throw new Error('Video ID wajib diisi');
      const text = `${videoId}, ${gmv || ''}, ${commission || ''}`;
      await api(`/profiles/${currentProfileId}/import-gmv-text`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
    }
    hideLoading();
    showToast('Data GMV/komisi tersimpan');
    await selectProfile(currentProfileId);
  } catch (err) {
    hideLoading();
    showToast(err.message, 'error');
  }
  delete form.dataset.dbId;
};

['filter-status', 'filter-sort', 'filter-date-from', 'filter-date-to', 'filter-min-views', 'filter-max-views']
  .forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', () => loadVideos().catch((e) => showToast(e.message, 'error')));
    if (el.tagName === 'INPUT') {
      el.addEventListener('input', () => {
        clearTimeout(el._filterT);
        el._filterT = setTimeout(() => loadVideos().catch((e) => showToast(e.message, 'error')), 400);
      });
    }
  });

$('#btn-cookies').onclick = () => $('#cookies-input').click();
async function updateCookiesStatus() {
  try {
    const s = await api('/cookies/status');
    const el = $('#cookies-status');
    if (!el) return;
    el.textContent = s.message;
    el.className = 'sidebar-hint ' + (s.ok ? 'hint-ok' : 'hint-warn');
  } catch (_) {}
}

$('#cookies-input').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await api('/cookies', { method: 'POST', body: fd });
    showToast(`${res.message} (${res.tiktok_cookies} cookies TikTok)`);
    updateCookiesStatus();
  } catch (err) {
    showToast(err.message, 'error');
  }
};

$('#btn-logout').onclick = async () => {
  try {
    await api('/auth/logout', { method: 'POST' });
  } catch (_) {}
  window.location.href = '/login.html';
};

// Init
(async () => {
  try {
    const me = await api('/auth/me');
    if (!me.authenticated) {
      window.location.href = '/login.html';
      return;
    }
    currentUser = me;
    const settingsLine = $('#settings-user-line');
    if (settingsLine) {
      settingsLine.textContent = `Login sebagai @${me.username}${me.is_admin ? ' (admin)' : ''} — data profil terisolasi per akun.`;
    }
    if (me.is_admin) await loadAdminUsers();
    const ytParams = new URLSearchParams(window.location.search);
    const initView = ytParams.get('view');
    if (['youtube', 'facebook', 'threads', 'oauth-monitoring', 'settings'].includes(initView)) switchView(initView);
    if (ytParams.get('facebook') === 'connected') {
      switchView('facebook');
      const count = ytParams.get('count') || '1';
      showToast(`Facebook Page berhasil terhubung ✓ (${count} page)`);
      history.replaceState({}, '', '/index.html?view=facebook');
    } else if (ytParams.get('facebook') === 'error') {
      switchView('facebook');
      showToast(`Facebook error: ${ytParams.get('msg') || 'unknown'}`, 'error');
      history.replaceState({}, '', '/index.html?view=facebook');
    }
    if (ytParams.get('threads') === 'connected') {
      switchView('threads');
      showToast('Threads akun berhasil terhubung ✓');
      history.replaceState({}, '', '/index.html?view=threads');
    } else if (ytParams.get('threads') === 'error') {
      switchView('threads');
      showToast(`Threads error: ${ytParams.get('msg') || 'unknown'}`, 'error');
      history.replaceState({}, '', '/index.html?view=threads');
    }
    if (ytParams.get('youtube') === 'connected') {
      switchView('youtube');
      showToast('YouTube channel berhasil terhubung ✓');
      history.replaceState({}, '', '/index.html?view=youtube');
    } else if (ytParams.get('youtube') === 'error') {
      switchView('youtube');
      showToast(`YouTube error: ${ytParams.get('msg') || 'unknown'}`, 'error');
      history.replaceState({}, '', '/index.html?view=youtube');
    }

    await loadProfiles();
    updateCookiesStatus();
  } catch (e) {
    if (!String(e.message).includes('login')) showToast(e.message, 'error');
  }
})();