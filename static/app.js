const API = '/api';
let currentProfileId = null;
let pollTimer = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

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
  const status = $('#filter-status').value;
  const sort = $('#filter-sort').value;
  const videos = await api(`/profiles/${currentProfileId}/videos?status=${status}&sort_by=${sort}`);
  const tbody = $('#video-table');
  tbody.innerHTML = '';

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
      <td>${fmtNum(v.views)}</td>
      <td>${fmtNum(v.likes)}</td>
      <td class="${v.gmv ? 'money' : 'money-empty'}">${fmtMoney(v.gmv)}</td>
      <td class="${v.commission ? 'money' : 'money-empty'}">${fmtMoney(v.commission)}</td>
      <td>
        <button class="btn btn-sm btn-secondary" data-dl="${v.platform_video_id}" title="Download">↓</button>
        <button class="btn btn-sm btn-ghost" data-edit="${v.id}" data-vid="${v.platform_video_id}" data-gmv="${v.gmv || ''}" data-comm="${v.commission || ''}" title="Edit GMV">✎</button>
        ${v.is_downloaded ? `<a class="btn btn-sm btn-ghost" href="/api/videos/${v.id}/file" target="_blank">▶</a>` : ''}
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
    showToast(`Scan selesai: ${done.result.scan.total} video ditemukan`);
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
$('#btn-import-gmv').onclick = () => { openModal('gmv'); loadTikTokShopConfig(); };

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
$$('.tab').forEach((tab) => {
  tab.onclick = () => {
    $$('.tab').forEach((t) => t.classList.remove('active'));
    $$('.tab-panel').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    $(`#tab-${tab.dataset.tab}`).classList.add('active');
  };
});

function openEditGmv(dataset) {
  openModal('gmv');
  $$('.tab').forEach((t) => t.classList.remove('active'));
  $$('.tab-panel').forEach((p) => p.classList.remove('active'));
  $('.tab[data-tab="manual"]').classList.add('active');
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

$('#filter-status').onchange = loadVideos;
$('#filter-sort').onchange = loadVideos;

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
    await loadProfiles();
    updateCookiesStatus();
  } catch (e) {
    if (!String(e.message).includes('login')) showToast(e.message, 'error');
  }
})();