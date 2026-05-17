import os
import base64
import shutil
import uuid
import glob
import asyncio
import time
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
if os.name == 'nt':  # Windows
    FFMPEG_PATH = os.path.join(BASE_DIR, "ffmpeg.exe")
    FFMPEG_AVAILABLE = os.path.exists(FFMPEG_PATH)
else:
    FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
    FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
TEMP_DIR    = os.path.join(BASE_DIR, "downloads")
os.makedirs(TEMP_DIR, exist_ok=True)

# ── Cookie file resolution ────────────────────────────────────────────────────
# Priority order:
#   1. COOKIES_CONTENT env var  → written to a temp file (for Render / cloud)
#   2. Local cookies.txt file   → used as-is (for local dev)
#   3. Browser cookies          → fallback (local dev only)

_ENV_COOKIE_PATH = os.path.join(TEMP_DIR, "_env_cookies.txt")

def _write_env_cookies() -> str | None:
    """If COOKIES_CONTENT env var is set, write it to a temp file and return the path.
    Supports both raw Netscape cookie text and base64-encoded content.
    Base64 is preferred because Render's env var UI can corrupt tab characters.
    Always writes with Unix LF line endings — yt-dlp on Linux rejects CRLF files.
    """
    raw = os.environ.get("COOKIES_CONTENT", "").strip()
    if not raw:
        return None
    # Try base64 decode first; fall back to raw text
    try:
        content = base64.b64decode(raw).decode("utf-8")
        logging.getLogger(__name__).info("Loaded cookies from COOKIES_CONTENT env var (base64).")
    except Exception:
        content = raw
        logging.getLogger(__name__).info("Loaded cookies from COOKIES_CONTENT env var (raw text).")
    # Normalize to Unix LF — yt-dlp on Linux fails to parse CRLF cookie files
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    # Write in binary mode to prevent Python adding OS-specific newlines on Windows
    with open(_ENV_COOKIE_PATH, "wb") as f:
        f.write(content.encode("utf-8"))
    logging.getLogger(__name__).info("Cookie file written: %s (%d bytes)", _ENV_COOKIE_PATH, len(content))
    return _ENV_COOKIE_PATH

def _find_cookie_file() -> str | None:
    """Find a cookies.txt file — env var takes priority over local files."""
    env_path = _write_env_cookies()
    if env_path:
        return env_path
    for name in ["www.instagram.com_cookies.txt", "instagram.com_cookies.txt", "cookies.txt"]:
        p = os.path.join(BASE_DIR, name)
        if os.path.exists(p): return p
    matches = glob.glob(os.path.join(BASE_DIR, "*cookies.txt"))
    return matches[0] if matches else None

COOKIE_FILE: str | None = _find_cookie_file()

# Fallback browser for cookies if no cookie file is found.
# Only useful locally — disabled on Render (no browser installed).
COOKIE_BROWSER: str | None = None if os.environ.get("RENDER") else "firefox"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Concurrency controls ──────────────────────────────────────────────────────
# Max simultaneous yt-dlp processes (each downloads + merges a file).
# Raise this if your machine has more bandwidth/cores; lower it to be conservative.
MAX_CONCURRENT_DOWNLOADS = 5

# Semaphore is created in lifespan so it belongs to the correct event loop.
download_semaphore: asyncio.Semaphore

# ── Rate limiting (in-memory, per IP) ────────────────────────────────────────
# Each IP is allowed RATE_LIMIT_REQUESTS calls per RATE_LIMIT_WINDOW seconds.
# Note: this is per-process; add Redis if running multiple workers.
RATE_LIMIT_REQUESTS = 10   # max requests per window
RATE_LIMIT_WINDOW   = 60   # window size in seconds

_rate_store: dict[str, list[float]] = defaultdict(list)
_rate_lock  = asyncio.Lock()

async def _check_rate_limit(ip: str) -> None:
    async with _rate_lock:
        now    = time.monotonic()
        cutoff = now - RATE_LIMIT_WINDOW
        # Drop timestamps outside the current window
        _rate_store[ip] = [t for t in _rate_store[ip] if t > cutoff]
        if len(_rate_store[ip]) >= RATE_LIMIT_REQUESTS:
            raise HTTPException(
                status_code=429,
                detail="Too many requests — please wait a moment before trying again.",
            )
        _rate_store[ip].append(now)

# ── Stale-file cleanup ────────────────────────────────────────────────────────
STALE_AGE_SECS      = 600   # remove temp files older than 10 minutes
CLEANUP_INTERVAL    = 300   # run cleanup every 5 minutes

def _delete_stale_files() -> None:
    """Synchronous: scan TEMP_DIR and remove files older than STALE_AGE_SECS."""
    now = time.time()
    for path in glob.glob(os.path.join(TEMP_DIR, "*")):
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > STALE_AGE_SECS:
                os.remove(path)
                logger.info("Removed stale file: %s", path)
        except Exception as exc:
            logger.warning("Could not remove stale file %s: %s", path, exc)

async def _periodic_cleanup() -> None:
    """Background task: run stale-file cleanup on a fixed interval."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        await asyncio.to_thread(_delete_stale_files)

# ── App lifespan ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global download_semaphore
    # Create the semaphore inside the event loop that will use it
    download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    # Clean up any leftovers from a previous crashed run
    await asyncio.to_thread(_delete_stale_files)
    # Start the periodic cleanup background task
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    logger.info(
        "Media Downloader ready — max %d concurrent downloads, "
        "rate limit %d req/%ds per IP",
        MAX_CONCURRENT_DOWNLOADS, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW,
    )
    yield
    # Shutdown: cancel the cleanup task gracefully
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Media Downloader API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ───────────────────────────────────────────────────────────
class InfoRequest(BaseModel):
    url: str

# ── yt-dlp sync helpers (run in thread pool, never in the event loop) ─────────

def _build_ydl_opts(url: str = "") -> dict:
    """Base yt-dlp options shared by all calls."""
    opts: dict = {
        "quiet":             True,
        "no_warnings":       True,
        "nocheckcertificate": True,
        "restrictfilenames": True,
        "windowsfilenames":  True,
        "ffmpeg_location":   FFMPEG_PATH,
        # Abort stalled/slow connections after 30 s so threads don't leak
        "socket_timeout":    30,
        "noplaylist": True,
        # Use a real Chrome User-Agent; some platforms (Instagram) reject
        # requests that look like bots or use the default yt-dlp UA.
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    }

    is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    
    # Always apply cookies if they exist
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    elif COOKIE_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIE_BROWSER,)
        
    # We skip the Webshare proxy for YouTube because their datacenter IPs are hard-banned
    proxy_url = os.environ.get("PROXY_URL", "").strip()
    if proxy_url and not is_youtube:
        opts["proxy"] = proxy_url
        
    return opts

def _build_info_opts(url: str = "") -> dict:
    """
    yt-dlp options for metadata-only fetches (no download).
    Strips format/merge keys that are only relevant during actual downloads.
    """
    opts = _build_ydl_opts(url)
    opts.pop("format", None)
    opts.pop("merge_output_format", None)
    return opts

def _friendly_error(exc: Exception) -> str:
    """
    Convert yt-dlp's internal error strings into clear, actionable messages.
    Especially important for Instagram authentication failures.
    """
    msg = str(exc)
    low = msg.lower()

    if any(k in low for k in ("login", "log in", "sign in", "authentication", "not logged")):
        if "youtube" in low or "bot" in low or "confirm you're not a bot" in low:
            return (
                "YouTube requires you to sign in to confirm you're not a bot. "
                "Update your cookies.txt with a fresh YouTube session, or ensure "
                "your proxy is working correctly."
            )
        return (
            "Login required. Export your cookies using the 'Get cookies.txt LOCALLY' "
            "Chrome extension while logged into the platform, then update the app."
        )
    if "empty media response" in low:
        return (
            "Instagram returned an empty response — your cookies.txt is "
            "missing, expired, or not linked to an Instagram account. "
            "Re-export cookies.txt while logged into Instagram and replace "
            "the existing file."
        )
    if "private" in low:
        return "This post is private. You must be following this account and have valid cookies."
    if "age" in low and "restrict" in low:
        return "This content is age-restricted. Log in via cookies.txt to access it."
    if any(k in low for k in ("unavailable", "not available", "been removed")):
        return "This content is unavailable or has been removed."
    if "unsupported url" in low:
        return "This URL is not supported. Please paste a valid Instagram or YouTube link."
    # Return the original yt-dlp message as a fallback
    return msg

def _unwrap_info(info: dict) -> dict:
    """Unwrap the playlist wrapper some extractors return for single videos."""
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        if not entries:
            raise ValueError("No media found at this URL")
        return entries[0]
    return info

def _fmt_size(b: float | None) -> str | None:
    """Convert bytes to a human-readable string."""
    if b is None:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def _sync_fetch_info(url: str) -> dict:
    """
    Blocking: fetch video metadata without downloading.
    Always call via asyncio.to_thread() — never directly from async code.
    """
    # Info-only fetch — use lightweight opts (no format/merge keys)
    with yt_dlp.YoutubeDL(_build_info_opts(url)) as ydl:
        info = ydl.extract_info(url, download=False)

    info = _unwrap_info(info)

    title     = info.get("title", "Unknown Title")
    thumbnail = info.get("thumbnail", "")
    duration  = info.get("duration_string", "")
    if not duration and info.get("duration"):
        m, s = divmod(info["duration"], 60)
        h, m = divmod(m, 60)
        duration = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    extractor = info.get("extractor", "unknown").lower()
    platform  = (
        "YouTube"   if "youtube"   in extractor else
        "Instagram" if "instagram" in extractor else
        extractor.capitalize()
    )

    # Best available filesize estimate from the formats list
    filesize: float | None = None
    for fmt in reversed(info.get("formats", [])):
        fs = fmt.get("filesize") or fmt.get("filesize_approx")
        if fs:
            filesize = fs
            break

    return {
        "title":     title,
        "thumbnail": thumbnail,
        "duration":  duration or "N/A",
        "platform":  platform,
        "extractor": extractor,
        "filesize":  _fmt_size(filesize),
        "ext":       info.get("ext", "unknown").upper(),
    }

# ── Global Progress Store ─────────────────────────────────────────────────────
# Maps task_id to { "status": str, "percent": float, "download_name": str, "actual_path": str, "error": str }
_progress_store: dict[str, dict] = {}

def _progress_hook(d: dict, task_id: str) -> None:
    """Updates the progress store based on yt-dlp's status."""
    if task_id not in _progress_store:
        return

    status = d.get('status')
    if status == 'downloading':
        _progress_store[task_id]['status'] = 'downloading'
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        downloaded = d.get('downloaded_bytes', 0)
        if total > 0:
            percent = round((downloaded / total) * 100, 1)
            # yt-dlp might download video and audio separately (resetting to 0%).
            # We only update if the new percent is higher to avoid a jumpy UI, 
            # though this is an approximation for multi-part downloads.
            current_pct = _progress_store[task_id].get('percent', 0)
            if percent > current_pct or percent < 5: 
                _progress_store[task_id]['percent'] = percent
    elif status == 'finished':
        # Finished downloading one of the streams, but still merging
        _progress_store[task_id]['percent'] = 100
        _progress_store[task_id]['status'] = 'processing'

def _sync_download(url: str, task_id: str) -> None:
    """
    Blocking: download media to TEMP_DIR using the given task_id as prefix.
    Always call via asyncio.to_thread() — never directly from async code.
    """
    try:
        opts = _build_ydl_opts(url)

        if FFMPEG_AVAILABLE:
            # ffmpeg found — download best video+audio separately and merge to MP4
            opts["format"]              = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]"
            opts["merge_output_format"] = "mp4"
            logger.info("ffmpeg available — using merge format")
        else:
            # ffmpeg NOT found — download a pre-merged stream only (no merging needed)
            opts["format"] = "best[height<=1080][ext=mp4]/best[height<=1080]/best"
            logger.warning("ffmpeg NOT found — using pre-merged format (lower quality possible)")

        opts["outtmpl"] = os.path.join(TEMP_DIR, f"{task_id}.%(ext)s")
        
        # Attach the progress hook
        opts["progress_hooks"] = [lambda d: _progress_hook(d, task_id)]

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        info = _unwrap_info(info)

        # Locate the merged output file by UUID prefix.
        matches = glob.glob(os.path.join(TEMP_DIR, f"{task_id}*"))
        if not matches:
            raise FileNotFoundError("Downloaded file not found on disk")
        # Prefer files with an extension over the raw task_id (if both exist)
        matches.sort(key=lambda x: len(x), reverse=True)
        actual_path = matches[0]

        # Build a safe filename for the Content-Disposition header
        _, ext = os.path.splitext(actual_path)
        if not ext or ext == ".":
            ext = ".mp4"  # Default to mp4 if no extension found
            
        raw_title  = info.get("title", "download")
        safe_title = "".join(
            c for c in raw_title if c.isalnum() or c in (" ", ".", "-", "_")
        ).strip()
        
        # yt-dlp sometimes falls back to the filename (task_id) if it can't find a title
        if not safe_title or task_id in safe_title:
            safe_title = "media_download"
            
        download_name = f"{safe_title}{ext}"

        if task_id in _progress_store:
            _progress_store[task_id]['actual_path'] = actual_path
            _progress_store[task_id]['download_name'] = download_name
            _progress_store[task_id]['status'] = 'completed'
            
    except Exception as exc:
        logger.error("Download error for task %s [%s]: %s", task_id, url, exc)
        if task_id in _progress_store:
            _progress_store[task_id]['status'] = 'error'
            _progress_store[task_id]['error'] = _friendly_error(exc)

def _cleanup_file(filepath: str) -> None:
    """Synchronous: delete a single temp file (used as a BackgroundTask)."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info("Cleaned up: %s", filepath)
    except Exception as exc:
        logger.error("Error cleaning up %s: %s", filepath, exc)

# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/cookie-status")
async def cookie_status():
    """Debug endpoint: shows exactly what cookie source the server is using."""
    env_raw = os.environ.get("COOKIES_CONTENT", "")
    env_set = bool(env_raw.strip())
    env_len = len(env_raw.strip())

    # Try base64 decode
    b64_ok = False
    if env_set:
        try:
            base64.b64decode(env_raw.strip()).decode("utf-8")
            b64_ok = True
        except Exception:
            pass

    cookie_file_exists = COOKIE_FILE is not None and os.path.exists(COOKIE_FILE)
    cookie_file_lines = 0
    has_sessionid = False
    if cookie_file_exists:
        try:
            lines = open(COOKIE_FILE, encoding="utf-8").readlines()
            cookie_file_lines = len([l for l in lines if l.strip() and not l.startswith("#")])
            has_sessionid = any("sessionid" in l for l in lines)
        except Exception:
            pass

    return JSONResponse({
        "COOKIES_CONTENT_env_set": env_set,
        "COOKIES_CONTENT_length": env_len,
        "COOKIES_CONTENT_is_base64": b64_ok,
        "COOKIE_FILE_path": COOKIE_FILE,
        "COOKIE_FILE_exists": cookie_file_exists,
        "COOKIE_FILE_data_lines": cookie_file_lines,
        "has_instagram_sessionid": has_sessionid,
        "COOKIE_BROWSER_fallback": COOKIE_BROWSER,
    })


@app.post("/api/info")
async def get_info(req: InfoRequest, request: Request):
    """
    Fetch video metadata (title, thumbnail, duration, filesize) without downloading.
    Rate-limited per IP. Non-blocking — yt-dlp runs in a thread pool.
    """
    await _check_rate_limit(request.client.host)
    try:
        # Run the blocking yt-dlp call off the event loop
        data = await asyncio.to_thread(_sync_fetch_info, req.url)
        return JSONResponse(data)
    except Exception as exc:
        logger.error("Info error [%s]: %s", req.url, exc)
        raise HTTPException(status_code=400, detail=_friendly_error(exc))

@app.post("/api/prepare")
async def prepare_download(req: InfoRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Starts an asynchronous download task and returns a task_id for progress polling.
    """
    await _check_rate_limit(request.client.host)
    if not req.url:
        raise HTTPException(status_code=400, detail="URL is required")

    task_id = str(uuid.uuid4())
    _progress_store[task_id] = {
        "status": "starting",
        "percent": 0.0,
        "download_name": None,
        "actual_path": None,
        "error": None
    }
    
    # Run the download in a background task
    async def _run_download_task():
        async with download_semaphore:
            await asyncio.to_thread(_sync_download, req.url, task_id)

    background_tasks.add_task(_run_download_task)
    return JSONResponse({"task_id": task_id})

@app.get("/api/progress")
async def get_progress(task_id: str):
    """
    Returns the current progress of a download task.
    """
    if task_id not in _progress_store:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    
    task_info = _progress_store[task_id]
    if task_info["status"] == "error":
        raise HTTPException(status_code=400, detail=task_info["error"])
        
    return JSONResponse({
        "status": task_info["status"],
        "percent": task_info["percent"]
    })

@app.get("/api/download")
async def download_media(task_id: str):
    """
    Serves the downloaded media to the client after it is completed.
    - Temp file is deleted automatically by the periodic cleanup task.
    """
    import urllib.parse
    
    if task_id not in _progress_store:
        raise HTTPException(status_code=404, detail="Task not found or expired")
        
    task_info = _progress_store[task_id]
    if task_info["status"] != "completed":
        raise HTTPException(status_code=400, detail="Download is not completed yet")
        
    actual_path = task_info["actual_path"]
    download_name = task_info["download_name"]

    # Create a universally safe ASCII fallback name
    ascii_name = "".join(c for c in download_name if ord(c) < 128)
    if not ascii_name or ascii_name.startswith('.'):
        ascii_name = "download" + (os.path.splitext(download_name)[1] or ".mp4")
        
    quoted_name = urllib.parse.quote(download_name)
    
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=utf-8''{quoted_name}"
    }
    
    # Note: We rely on the periodic cleanup task to delete the file
    # instead of a background_task, to avoid PermissionError on Windows.
    return FileResponse(path=actual_path, headers=headers)

# ── Static files ──────────────────────────────────────────────────────────────
# Use BASE_DIR so the app works regardless of the working directory it's launched from
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # Single-process dev server:
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
    #
    # For production with multiple CPU cores, use Gunicorn + Uvicorn workers:
    #   pip install gunicorn
    #   gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:8000
    #
    # Note: the in-memory semaphore and rate limiter are per-process.
    # For multi-worker rate limiting, replace _rate_store with a Redis backend.
