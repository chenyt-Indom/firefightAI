import urllib.request, os, time

os.chdir(r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI')
url = 'https://ghproxy.net/https://github.com/frida/frida/releases/download/17.16.0/frida-server-17.16.0-android-x86_64.xz'

for attempt in range(3):
    try:
        print(f'Attempt {attempt+1}/3...')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        # Resume if partial
        existing = 0
        if os.path.exists('frida-server.xz'):
            existing = os.path.getsize('frida-server.xz')
            if existing > 1000000:
                req.add_header('Range', f'bytes={existing}-')
                print(f'  Resuming from {existing/1024:.0f}KB')
        
        resp = urllib.request.urlopen(req, timeout=120)
        mode = 'ab' if existing > 0 else 'wb'
        with open('frida-server.xz', mode) as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        resp.close()
        
        sz = os.path.getsize('frida-server.xz')
        print(f'Done! {sz/1024/1024:.1f}MB')
        if sz > 30000000:  # >30MB
            import sys; sys.exit(0)
    except Exception as e:
        print(f'  {e}')
        time.sleep(2)

print('Failed after 3 attempts')
