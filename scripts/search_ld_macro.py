import os, glob, subprocess
# 方法1: 直接搜 LDPlayer 安装路径
candidates = []
for drive in ["C:", "D:", "E:"]:
    candidates += glob.glob(f"{drive}/**/*ldplayer*", recursive=True) or []
    candidates += glob.glob(f"{drive}/**/*ldrecord*", recursive=True) or []
    candidates += glob.glob(f"{drive}/**/*macro*", recursive=True) or []
    candidates += glob.glob(f"{drive}/**/*operat*", recursive=True) or []

candidates = [c for c in candidates if os.path.exists(c)]
for c in candidates[:50]:
    if os.path.isdir(c):
        print(f"[DIR] {c}")
        try:
            for f in os.listdir(c)[:10]:
                fp = os.path.join(c, f)
                sz = os.path.getsize(fp) if os.path.isfile(fp) else "DIR"
                print(f"  {f} ({sz})")
                if f.endswith(('.json', '.ld', '.script', '.macro', '.record', '.txt', '.xml', '.dat')):
                    try:
                        with open(fp, 'r', errors='ignore') as ff:
                            content = ff.read()[:500]
                        print(f"    CONTENT: {content[:200]}")
                    except: pass
        except: pass
    elif os.path.isfile(c):
        sz = os.path.getsize(c)
        print(f"[FILE] {c} ({sz}B)")
        if sz < 100000:
            try:
                with open(c, 'r', errors='ignore') as ff:
                    print(f"  {ff.read()[:300]}")
            except: pass

# 方法2: 用 WMI 查进程
print("\n=== LDPlayer进程 ===")
r = subprocess.run(["wmic", "process", "where", "name like '%ld%'", "get", "ProcessId,ExecutablePath", "/format:csv"], capture_output=True, text=True, shell=True)
for line in r.stdout.split("\n"):
    if "ld" in line.lower(): print(line.strip())

# 方法3: 搜录制码
print("\n=== 搜录制码 ===")
for root, dirs, files in os.walk("C:"):
    for f in files:
        if f.endswith(('.json', '.ld', '.script', '.macro', '.record', '.dat')):
            fp = os.path.join(root, f)
            if os.path.getsize(fp) < 100000:
                try:
                    with open(fp, 'r', errors='ignore') as ff:
                        content = ff.read()
                    if "leidian" in content.lower() or "33ddad150773" in content:
                        print(f"FOUND: {fp}")
                        print(content[:500])
                except: pass
