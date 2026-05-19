/* ── YTSave · script.js ──────────────────────────────────────────────────── */

const TABS = {
  'yt-video': { ph: 'https://www.youtube.com/watch?v=…' },
  'yt-audio': { ph: 'https://www.youtube.com/watch?v=…' },
};

let active = 'yt-video';
let currentUrl = '';
let selectedFormatId = '';
let pollInterval = null;
const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8000' : '';


/* DOM refs */
const $ = id => document.getElementById(id);
const urlInput     = $('urlInput');
const pasteBtn     = $('pasteBtn');
const dlBtn        = $('dlBtn');
const btnLabel     = $('btnLabel');
const errBox       = $('errBox');
const resultCard   = $('resultCard');
const rThumb       = $('rThumb');
const rTitle       = $('rTitle');
const rPlatform    = $('rPlatform');
const rSize        = $('rSize');
const rDur         = $('rDur');
const qualityWrap  = $('qualityWrap');
const qualityGrid  = $('qualityGrid');
const progressWrap = $('progressWrap');
const progressFill = $('progressFill');
const progressText = $('progressText');
const saveBtn      = $('saveBtn');

/* ── Tab switching ─────────────────────────────────────────────────────────── */
function setTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  if (btn) btn.classList.add('active');
  active = tabId;
  urlInput.placeholder = TABS[active]?.ph || '';
  clearErr();
  resultCard.classList.remove('show');
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    urlInput.value = '';
    setTab(btn.dataset.tab);
  });
});

/* ── Paste ─────────────────────────────────────────────────────────────────── */
pasteBtn.addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    urlInput.value = text;
    autoDetect(text);
    clearErr();
  } catch {
    showErr('Clipboard access denied — please paste manually.');
  }
});

function autoDetect(url) {
  if (!url) return;
  url = url.toLowerCase();
  if (url.includes('youtube.com') || url.includes('youtu.be')) {
    setTab('yt-video');
  }
}

urlInput.addEventListener('input', e => {
  clearErr();
  autoDetect(e.target.value);
});

/* ── Fetch info ────────────────────────────────────────────────────────────── */
dlBtn.addEventListener('click', async () => {
  let url = urlInput.value.trim();
  if (!url) { showErr('Please enter a YouTube URL.'); return; }

  if (!/^https?:\/\//i.test(url)) {
    url = 'https://' + url;
    urlInput.value = url;
  }

  let parsed;
  try { parsed = new URL(url); }
  catch { showErr("That doesn't look like a valid URL."); return; }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    showErr('Only http:// and https:// links are supported.');
    return;
  }

  clearErr();
  dlBtn.classList.add('loading');
  resultCard.classList.remove('show');

  try {
    const res = await fetch(`${API_BASE}/api/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    if (!res.ok) {
      let detail = 'Failed to fetch video info.';
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(detail);
    }

    const data = await res.json();
    currentUrl = url;
    showResult(data);
  } catch (err) {
    showErr(err.message);
  } finally {
    dlBtn.classList.remove('loading');
  }
});

/* ── Show result ───────────────────────────────────────────────────────────── */
function showResult(data) {
  rThumb.src = data.thumbnail || '';
  rTitle.textContent = data.title || '—';
  rPlatform.textContent = data.platform || 'YouTube';
  rDur.textContent = data.duration || '—';
  rSize.textContent = data.filesize || '—';

  /* Build quality buttons */
  qualityGrid.innerHTML = '';
  selectedFormatId = '';

  const formats = active === 'yt-audio'
    ? (data.audio_formats || [])
    : (data.video_formats || []);

  if (formats.length > 0) {
    formats.forEach((f, i) => {
      const btn = document.createElement('button');
      btn.className = 'quality-btn' + (i === 0 ? ' selected' : '');
      btn.textContent = f.label;
      btn.dataset.id = f.id;
      if (i === 0) selectedFormatId = f.id;
      btn.addEventListener('click', () => {
        document.querySelectorAll('.quality-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');
        selectedFormatId = f.id;
      });
      qualityGrid.appendChild(btn);
    });
    qualityWrap.style.display = 'flex';
  } else {
    selectedFormatId = active === 'yt-audio' ? 'audio-mp3-320' : 'video-best';
    qualityWrap.style.display = 'none';
  }

  progressWrap.classList.remove('show');
  saveBtn.classList.remove('hidden');
  resultCard.classList.add('show');
  resultCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/* ── Save / download ───────────────────────────────────────────────────────── */
saveBtn.addEventListener('click', async () => {
  if (!currentUrl) return;

  const format_id = selectedFormatId || (active === 'yt-audio' ? 'audio-mp3-320' : 'video-best');

  saveBtn.classList.add('hidden');
  progressWrap.classList.add('show');
  progressFill.style.width = '0%';
  progressText.textContent = 'Preparing…';

  try {
    const res = await fetch(`${API_BASE}/api/prepare`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: currentUrl, format_id }),
    });

    if (!res.ok) {
      let detail = 'Failed to start download.';
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(detail);
    }

    const { task_id } = await res.json();

    if (pollInterval) clearInterval(pollInterval);

    pollInterval = setInterval(async () => {
      try {
        const progRes = await fetch(`${API_BASE}/api/progress?task_id=${task_id}`);
        if (!progRes.ok) {
          const errData = await progRes.json();
          throw new Error(errData.detail || 'Progress check failed');
        }
        const prog = await progRes.json();

        if (prog.status === 'starting') {
          progressText.textContent = 'Starting download…';
          progressFill.style.width = '5%';
        } else if (prog.status === 'downloading') {
          const p = prog.percent || 0;
          progressFill.style.width = `${p}%`;
          progressText.textContent = `Downloading… ${p}%`;
        } else if (prog.status === 'processing') {
          progressFill.style.width = '100%';
          progressText.textContent = 'Merging audio & video…';
        } else if (prog.status === 'completed') {
          clearInterval(pollInterval);
          progressFill.style.width = '100%';
          progressText.textContent = 'Done! Saving to device…';
          
          // Programmatic a-download trigger for perfect filename handling
          const a = document.createElement('a');
          a.href = `${API_BASE}/api/download?task_id=${task_id}`;
          a.download = rTitle.textContent ? rTitle.textContent.trim() : 'download.mp4';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);

          setTimeout(() => {
            progressWrap.classList.remove('show');
            saveBtn.classList.remove('hidden');
          }, 3500);
        }
      } catch (e) {
        clearInterval(pollInterval);
        progressWrap.classList.remove('show');
        saveBtn.classList.remove('hidden');
        showErr(e.message);
      }
    }, 1000);

  } catch (err) {
    progressWrap.classList.remove('show');
    saveBtn.classList.remove('hidden');
    showErr(err.message);
  }
});

/* ── Helpers ───────────────────────────────────────────────────────────────── */
function showErr(msg) {
  errBox.innerHTML = msg;
  errBox.classList.add('show');
}
function clearErr() {
  errBox.classList.remove('show');
  errBox.innerHTML = '';
}
