import base64, subprocess

# Read Chrome cookies file - extract only instagram.com lines (deduplicated)
lines = open('cookies-3 chrome.txt', encoding='utf-8', errors='ignore').readlines()
seen = set()
ig_lines = []
for l in lines:
    if '.instagram.com' in l and l.strip() and not l.startswith('#'):
        parts = l.strip().split('\t')
        if len(parts) >= 6:
            name = parts[5]
            if name not in seen:
                seen.add(name)
                ig_lines.append(l.rstrip() + '\n')

# Build merged cookies.txt
header = '# Netscape HTTP Cookie File\n# This file is generated for yt-dlp.  Do not edit.\n\n'
combined = header + ''.join(ig_lines)

with open('cookies.txt', 'w', encoding='utf-8') as f:
    f.write(combined)

print(f'Instagram cookies: {len(ig_lines)}')
print('Instagram cookie names:', [l.strip().split('\t')[5] for l in ig_lines])

b64 = base64.b64encode(combined.encode('utf-8')).decode()
print(f'\nBase64 length: {len(b64)} chars')
print('\nBase64 value (copy this into Render COOKIES_CONTENT):')
print(b64)

# Copy to clipboard
proc = subprocess.run(['powershell', '-command', f'Set-Clipboard -Value "{b64}"'], capture_output=True)
if proc.returncode == 0:
    print('\nCopied to clipboard!')
