const TABS = {
  'ig-post':  { ph: 'https://www.instagram.com/p/…' },
  'ig-story': { ph: 'https://www.instagram.com/stories/…' },
};

let active = 'ig-post';
let currentUrlToDownload = '';

const $ = id => document.getElementById(id);
const urlInput     = $('urlInput');
const pasteBtn     = $('pasteBtn');
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
  if (url.includes('instagram.com/stories')) {
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
  let url = urlInput.value.trim();
  if (!url) { showErr('Please enter a URL.'); return; }
  
  // Auto-prepend https:// if missing
  if (!/^https?:\/\//i.test(url)) {
    url = 'https://' + url;
    urlInput.value = url; // Update input field to show the full URL
  }

  // Validate structure first
  let parsed;
  try { 
    parsed = new URL(url); 
  } catch { 
    showErr('That doesn\'t look like a valid URL.'); 
    return; 
  }
  
  // Only allow http/https
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    showErr('Only http:// and https:// links are supported.');
    return;
  }
  
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
      let detail = 'Failed to fetch video info.';
      try {
        const errorData = await res.json();
        detail = errorData.detail || detail;
      } catch { /* response wasn't JSON (e.g. 500 HTML page) */ }
      throw new Error(detail);
    }
    
    const data = await res.json();
    showResult(data, url);
  } catch (err) {
    showErr(err.message);
  } finally {
    dlBtn.classList.remove('loading');
  }
});

let pollInterval = null;

saveBtn.addEventListener('click', async () => {
  if (!currentUrlToDownload) return;

  const url = currentUrlToDownload;
  
  saveBtn.style.display = 'none';
  $('progressContainer').classList.add('show');
  $('progressFill').style.width = '0%';
  $('progressText').textContent = 'Preparing...';

  try {
    const res = await fetch('/api/prepare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });

    if (!res.ok) {
      let detail = 'Failed to prepare download.';
      try {
        const errorData = await res.json();
        detail = errorData.detail || detail;
      } catch { }
      throw new Error(detail);
    }

    const { task_id } = await res.json();
    
    pollInterval = setInterval(async () => {
      try {
        const progRes = await fetch(`/api/progress?task_id=${task_id}`);
        if (!progRes.ok) {
           const errData = await progRes.json();
           throw new Error(errData.detail || 'Failed to get progress');
        }
        const progData = await progRes.json();
        
        if (progData.status === 'error') {
           throw new Error('Download failed on server.');
        }

        if (progData.status === 'starting') {
           $('progressText').textContent = 'Starting download...';
        } else if (progData.status === 'downloading') {
           const p = progData.percent || 0;
           $('progressFill').style.width = `${p}%`;
           $('progressText').textContent = `Downloading... ${p}%`;
        } else if (progData.status === 'processing') {
           $('progressFill').style.width = `100%`;
           $('progressText').textContent = 'Processing (merging audio/video)...';
        } else if (progData.status === 'completed') {
           clearInterval(pollInterval);
           $('progressFill').style.width = `100%`;
           $('progressText').textContent = 'Completed! Saving to device...';
           
           window.location.assign(`/api/download?task_id=${task_id}`);
           
           setTimeout(() => {
             $('progressContainer').classList.remove('show');
             saveBtn.style.display = 'inline-flex';
           }, 3000);
        }
        
      } catch (e) {
        clearInterval(pollInterval);
        showErr(e.message);
        $('progressContainer').classList.remove('show');
        saveBtn.style.display = 'inline-flex';
      }
    }, 1000);

  } catch (err) {
    showErr(err.message);
    $('progressContainer').classList.remove('show');
    saveBtn.style.display = 'inline-flex';
  }
});

function showResult(data, url) {
  currentUrlToDownload = url;

  rThumb.src = data.thumbnail || 'https://via.placeholder.com/800x450?text=No+Thumbnail';
  rTitle.textContent = data.title;
  
  // Use the actual extension (e.g. JPG for images, MP4 for video)
  let ext = data.ext || 'MP4';
  if (ext === 'UNKNOWN') ext = 'MP4';
  rChip.textContent = ext;
  
  rPlatform.textContent = data.platform;
  rSize.textContent = data.filesize || '—';
  rDur.textContent = data.duration;
  
  saveBtn.style.display = 'inline-flex';
  $('progressContainer').classList.remove('show');
  
  resultCard.classList.add('show');
  resultCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

const COOKIE_HINT =
  'Tip: Export your Instagram cookies using the ' +
  '<a href="https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenk" ' +
  'target="_blank" style="color:inherit;text-decoration:underline">Get cookies.txt LOCALLY</a> ' +
  'Chrome extension while logged in, then save the file as <b>cookies.txt</b> in the app folder.';

function showErr(msg) {
  errBox.innerHTML = msg;
  // Append cookie hint for Instagram auth failures
  const low = msg.toLowerCase();
  if (low.includes('cookie') || low.includes('login') || low.includes('empty response') || low.includes('log in')) {
    errBox.innerHTML += '<br><small style="opacity:.8">' + COOKIE_HINT + '</small>';
  }
  errBox.classList.add('show');
}
function clearErr() { errBox.classList.remove('show'); errBox.innerHTML = ''; }
