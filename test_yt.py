import yt_dlp
import traceback

ydl_opts = {'quiet': True, 'nocheckcertificate': True}
try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info('https://www.youtube.com/watch?v=BaW_jenozKc', download=False)
        print("Success!")
except Exception as e:
    traceback.print_exc()
