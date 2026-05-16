import os
import uuid
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import logging

# Resolve paths relative to this script file (not CWD)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_PATH = os.path.join(BASE_DIR, "ffmpeg.exe")

app = FastAPI(title="Media Downloader API")

# Allow all origins so browser requests to /api/* work without CORS errors
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Temporary directory for downloads
TEMP_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(TEMP_DIR, exist_ok=True)

# Path to a cookies.txt file exported from your browser (recommended).
# Export using the 'Get cookies.txt LOCALLY' browser extension.
# Set to None to fall back to browser-based cookie extraction.
COOKIE_FILE: str | None = os.path.join(BASE_DIR, "cookies.txt")

# Fallback browser for cookies if cookies.txt doesn't exist.
# Use 'firefox' (recommended) — Firefox doesn't lock its DB while running.
# Chrome will fail if Chrome is open. Set to None to disable.
COOKIE_BROWSER: str | None = "firefox"

class InfoRequest(BaseModel):
    url: str

class BrowserRequest(BaseModel):
    browser: str  # e.g. 'chrome', 'firefox', 'edge', 'none'

def get_yt_dlp_options(quality: str = 'best'):
    options = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'restrictfilenames': True,
        'windowsfilenames': True,
        'ffmpeg_location': FFMPEG_PATH,
    }
    # Prefer cookies.txt file; fall back to browser extraction
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        options['cookiefile'] = COOKIE_FILE
    elif COOKIE_BROWSER:
        options['cookiesfrombrowser'] = (COOKIE_BROWSER,)
    if quality == 'mp3':
        options['format'] = 'bestaudio/best'
        options['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif quality in ['1080p', '720p', '480p']:
        height = quality.replace('p', '')
        options['format'] = f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}][ext=mp4]/best'
    else:
        # Default video format
        options['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    return options

def cleanup_file(filepath: str):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Cleaned up {filepath}")
    except Exception as e:
        logger.error(f"Error cleaning up {filepath}: {e}")

@app.post("/api/info")
async def get_info(req: InfoRequest):
    try:
        ydl_opts = {'quiet': True, 'nocheckcertificate': True, 'restrictfilenames': True, 'windowsfilenames': True, 'ffmpeg_location': FFMPEG_PATH}
        if COOKIE_FILE and os.path.exists(COOKIE_FILE):
            ydl_opts['cookiefile'] = COOKIE_FILE
        elif COOKIE_BROWSER:
            ydl_opts['cookiesfrombrowser'] = (COOKIE_BROWSER,)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            
            # Extract basic info
            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail', '')
            duration = info.get('duration_string', '')
            if not duration and info.get('duration'):
                m, s = divmod(info.get('duration'), 60)
                h, m = divmod(m, 60)
                duration = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                
            extractor = info.get('extractor', 'unknown').lower()
            
            platform = "YouTube" if 'youtube' in extractor else "Instagram" if 'instagram' in extractor else extractor.capitalize()
            
            return JSONResponse({
                "title": title,
                "thumbnail": thumbnail,
                "duration": duration or "N/A",
                "platform": platform,
                "extractor": extractor
            })
    except Exception as e:
        logger.error(f"Error fetching info: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/download")
async def download_media(url: str, format: str = 'best', background_tasks: BackgroundTasks = None):
    if background_tasks is None:
        background_tasks = BackgroundTasks()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
        
    try:
        extract_audio = format == 'mp3'
        file_id = str(uuid.uuid4())
        
        ydl_opts = get_yt_dlp_options(format)
        outtmpl = os.path.join(TEMP_DIR, f"{file_id}.%(ext)s")
        ydl_opts['outtmpl'] = outtmpl

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # ytdlp might change the extension during postprocessing (e.g. to mp3)
            # Find the actual downloaded file
            actual_filename = ydl.prepare_filename(info)
            if extract_audio:
                # If postprocessed to mp3, the ext might be different from prepare_filename
                base, _ = os.path.splitext(actual_filename)
                actual_filename = f"{base}.mp3"
                
            if not os.path.exists(actual_filename):
                raise Exception("Downloaded file not found")
                
            # Set up cleanup after response
            background_tasks.add_task(cleanup_file, actual_filename)
            
            # Clean up the filename for the user
            _, actual_ext = os.path.splitext(actual_filename)
            download_name = f"{info.get('title', 'download')}{actual_ext}"
            # Remove characters that might cause issues in headers
            download_name = "".join(c for c in download_name if c.isalnum() or c in (' ', '.', '-', '_')).strip()
            
            return FileResponse(
                path=actual_filename,
                filename=download_name
            )
            
    except Exception as e:
        logger.error(f"Error downloading: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Mount static files (this serves index.html, style.css, script.js)
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
