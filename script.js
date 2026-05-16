const TABS = {
  'ig-post':  { ph: 'https://www.instagram.com/p/…', quality: false },
  'ig-story': { ph: 'https://www.instagram.com/stories/…', quality: false },
  'youtube':  { ph: 'https://www.youtube.com/watch?v=…', quality: true },
};

let active = 'ig-post';
let currentUrlToDownload = '';

const $ = id => document.getElementById(id);
const urlInput     = $('urlInput');
const pasteBtn     = $('pasteBtn');
const qualityWrap  = $('qualityWrap');
const qualitySelect = $('qualitySelect');
const dlBtn        = $('dlBtn');
const errBox       = $('errBox');
const resultCard   = $('resultCard');
const rThumb       = $('rThumb');
const rTitle       = $('rTitle');
const rChip        = $('rChip');
const rPlatform    = $('rPlatform');
const rSize        = $('rSize');
const rDur         = $('rDur');
const saveBtn      = $('saveBtn');

function setTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  if (btn) btn.classList.add('active');
  active = tabId;
  urlInput.placeholder = TABS[active].ph;
  qualityWrap.classList.toggle('open', TABS[active].quality);
  clearErr();
  resultCard.classList.remove('show');
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    urlInput.value = '';
    setTab(btn.dataset.tab);
  });
});

pasteBtn.addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    urlInput.value = text;
    autoDetectPlatform(text);
    clearErr();
  } catch { showErr('Clipboard access denied — please paste manually.'); }
});

function autoDetectPlatform(url) {
  if (!url) return;
  url = url.toLowerCase();
  if (url.includes('youtube.com') || url.includes('youtu.be')) {
    setTab('youtube');
  } else if (url.includes('instagram.com/stories')) {
    setTab('ig-story');
  } else if (url.includes('instagram.com/p') || url.includes('instagram.com/reel')) {
    setTab('ig-post');
  }
}

urlInput.addEventListener('input', (e) => {
  clearErr();
  autoDetectPlatform(e.target.value);
});

dlBtn.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  if (!url) { showErr('Please enter a URL.'); return; }
  try { new URL(url); } catch { showErr('That doesn\'t look like a valid URL.'); return; }
  
  clearErr();
  dlBtn.classList.add('loading');
  resultCard.classList.remove('show');

  try {
    const res = await fetch('/api/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    
    if (!res.ok) {
      const errorData = await res.json();
      throw new Error(errorData.detail || 'Failed to fetch video info.');
    }
    
    const data = await res.json();
    showResult(data, url);
  } catch (err) {
    showErr(err.message);
  } finally {
    dlBtn.classList.remove('loading');
  }
});

saveBtn.addEventListener('click', () => {
  if (!currentUrlToDownload) return;
  
  const q = qualitySelect.value;
  const downloadUrl = `/api/download?url=${encodeURIComponent(currentUrlToDownload)}&format=${q}`;
  
  const orig = saveBtn.textContent;
  saveBtn.textContent = 'Starting Download...';
  
  // Trigger download
  const a = document.createElement('a');
  a.href = downloadUrl;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  setTimeout(() => { saveBtn.textContent = orig; }, 3000);
});

function showResult(data, url) {
  currentUrlToDownload = url;
  const q = qualitySelect.value;
  const isAudio = active === 'youtube' && q === 'mp3';
  
  rThumb.src = data.thumbnail || 'https://via.placeholder.com/800x450?text=No+Thumbnail';
  rTitle.textContent = data.title;
  rChip.textContent = active === 'youtube' ? (isAudio ? 'MP3' : q.toUpperCase()) : 'MP4';
  rPlatform.textContent = data.platform;
  rSize.textContent = 'Calculated on dl'; // Backend streams, size isn't known until dl
  rDur.textContent = data.duration;
  
  resultCard.classList.add('show');
  resultCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function showErr(msg) { errBox.textContent = msg; errBox.classList.add('show'); }
function clearErr()   { errBox.classList.remove('show'); errBox.textContent = ''; }
