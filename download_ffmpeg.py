import urllib.request
import zipfile
import os

url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
zip_path = "ffmpeg.zip"
print("Downloading ffmpeg...")
urllib.request.urlretrieve(url, zip_path)
print("Extracting...")
with zipfile.ZipFile(zip_path, 'r') as zip_ref:
    for file in zip_ref.namelist():
        if file.endswith('ffmpeg.exe'):
            with zip_ref.open(file) as source, open('ffmpeg.exe', 'wb') as target:
                target.write(source.read())
            break
os.remove(zip_path)
print("Done!")
