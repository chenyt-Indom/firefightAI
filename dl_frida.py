import urllib.request, sys, os
os.chdir(r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI')
urls = [
    'https://ghproxy.net/https://github.com/frida/frida/releases/download/17.16.0/frida-server-17.16.0-android-x86_64.xz',
    'https://gh.llkk.cc/https://github.com/frida/frida/releases/download/17.16.0/frida-server-17.16.0-android-x86_64.xz',
    'https://github.com/frida/frida/releases/download/17.16.0/frida-server-17.16.0-android-x86_64.xz',
]
for url in urls:
    try:
        print(f'Try: {url[:60]}...')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=60)
        data = resp.read()
        resp.close()
        sz = len(data)
        if sz > 1000000:
            with open('frida-server.xz', 'wb') as f:
                f.write(data)
            print(f'OK! {sz/1024/1024:.1f}MB')
            sys.exit(0)
        print(f'  Too small: {sz} bytes')
    except Exception as e:
        print(f'  Fail: {e}')
print('All failed')
