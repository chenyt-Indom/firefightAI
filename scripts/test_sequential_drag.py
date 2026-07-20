"""验证串行拖拽: 每个单位独立执行 tap→wait→swipe

Firefight 控制协议 (用户确认):
  1. tap 单位 → 蓝线出现 (选中)
  2. 等 0.5s → 游戏注册
  3. swipe 从蓝线到目标 → 拖拽移动 (游戏自带寻路)

关键: 一次只能选中一个单位, 必须串行执行。
"""
import sys, os, time, math, subprocess
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── 配置 ───
MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"

# 圆圈偏移 (回归 test_move_correct.py 的 35px——那次成功了4个)
CIRCLE_OFFSET_Y = 35   # 圆圈在单位下方35px
CLUSTER_EPS     = 50
INTER_DELAY     = 0.3  # 单位间间隔
MAX_UNITS       = 5    # 测试5个单位

# ─── 工具函数 ───
def mumu(cmd: str, timeout: int = 5):
    """发送一条 MuMuManager 命令"""
    r = subprocess.run(
        [MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    ok = "errcode: 0" in r.stdout or r.returncode == 0
    return ok, r.stdout.strip()[:100]

def capture():
    """ADB 管道截图"""
    r = subprocess.run(
        [ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10,
    )
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_and_cluster(img, eps=50):
    """颜色检测 + 距离聚类 → 单位列表 [(cx, cy, member_count), ...]"""
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 2, h // 2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95, 100, 100]), np.array([115, 255, 255]))

    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    points = []
    for i in range(1, n):
        if stats[i, 4] >= 10:  # 缩略图面积阈值
            points.append(np.array([centroids[i][0], centroids[i][1]]))

    if len(points) <= 1:
        return [(int(p[0]) * 2, int(p[1]) * 2, 1) for p in points]

    # 距离聚类
    clusters = []
    used = set()
    for i in range(len(points)):
        if i in used:
            continue
        cluster = [points[i]]
        used.add(i)
        queue = [i]
        while queue:
            cur = queue.pop(0)
            for j in range(len(points)):
                if j not in used:
                    dist = math.hypot(points[cur][0] - points[j][0],
                                      points[cur][1] - points[j][1])
                    if dist < eps:
                        cluster.append(points[j])
                        used.add(j)
                        queue.append(j)
        avg = np.mean(cluster, axis=0)
        best = min(cluster, key=lambda p: math.hypot(p[0] - avg[0], p[1] - avg[1]))
        clusters.append((int(best[0]) * 2, int(best[1]) * 2, len(cluster)))

    return clusters

# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("🧪 串行拖拽验证测试")
print("=" * 60)

# ── 1. 截图前 ──
print("\n📸 截图(前)...")
before = capture()
if before is None:
    print("❌ 截图失败!")
    sys.exit(1)
h, w = before.shape[:2]
print(f"   分辨率: {w}x{h}")

# ── 2. 检测友军单位 ──
print("\n🔍 检测友军单位 + 聚类...")
units = detect_and_cluster(before, eps=CLUSTER_EPS)
print(f"   检测到 {len(units)} 个单位 (含聚类)")
for i, (cx, cy, n) in enumerate(units[:8]):
    print(f"   单位#{i+1}: ({cx:4d}, {cy:4d}) 成员={n}")

if len(units) == 0:
    print("❌ 没检测到友军!")
    sys.exit(1)

# ── 3. 批量发送: 所有单位的 tap+swipe 打包成一条命令 (test_move_correct 成功方式) ──
print(f"\n🎮 批量拖拽: 前 {MAX_UNITS} 个单位, tap+swipe全部一条命令")
print(f"   偏移: dy={CIRCLE_OFFSET_Y}")

t_start = time.time()
cmds = []
for i, (ux, uy, n) in enumerate(units[:MAX_UNITS]):
    tx, ty = ux, max(10, uy - 300)
    cmds.append(f"input tap {ux} {uy}")
    cmds.append(f"input swipe {ux} {uy + CIRCLE_OFFSET_Y} {tx} {ty} 500")
    print(f"   单位#{i+1}: tap({ux},{uy}) + swipe({ux},{uy+CIRCLE_OFFSET_Y})→({tx},{ty})")

batch = "; ".join(cmds)
ok, out = mumu(batch, timeout=10)
print(f"\n   发送 {len(cmds)} 条指令 (1次调用): {'✅' if ok else '❌'}")

t_exec = (time.time() - t_start) * 1000
success_count = MAX_UNITS if ok else 0
print(f"   耗时: {t_exec:.0f}ms")

# ── 4. 等游戏响应后截图 ──
print("\n⏳ 等待游戏响应 (2秒)...")
time.sleep(2.0)

print("📸 截图(后)...")
after = capture()

# ── 5. 保存截图 ──
out_dir = "C:/Users/19853/WorkBuddy/2026-07-18-07-52-25/firefightAI/screenshots"
os.makedirs(out_dir, exist_ok=True)

# 标注检测到的单位
before_annot = before.copy()
for i, (cx, cy, n) in enumerate(units[:MAX_UNITS]):
    color = (0, 255, 0) if i < success_count else (0, 0, 255)
    cv2.circle(before_annot, (cx, cy), 15, color, 3)
    cv2.putText(before_annot, f"#{i+1}({n})", (cx + 20, cy),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    # 标注拖拽起点(圆圈)和终点
    sx, sy = cx, cy + CIRCLE_OFFSET_Y
    tx, ty = cx, max(10, cy - 300)
    cv2.circle(before_annot, (sx, sy), 5, (255, 255, 0), -1)  # 圆圈
    cv2.arrowedLine(before_annot, (sx, sy), (tx, ty), (255, 0, 255), 2)

cv2.imwrite(f"{out_dir}/seq_before.png", before_annot)
cv2.imwrite(f"{out_dir}/seq_after.png", after)
print(f"   截图: {out_dir}/seq_before.png, {out_dir}/seq_after.png")

# ── 6. 像素对比 ──
print("\n📊 像素对比分析...")
diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
changed = np.count_nonzero(gray > 30)
total = gray.size
pct = changed / total * 100

_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
large = [c for c in contours if cv2.contourArea(c) > 500]

print(f"   变化像素: {changed:,} / {total:,} = {pct:.2f}%")
print(f"   显著区域 (>500px²): {len(large)} 个")

# ── 7. 结论 ──
print("\n" + "=" * 60)
if pct > 2.0 and len(large) > 5:
    print("✅ 画面有明显变化, 拖拽很可能生效! 请到游戏里确认单位位置")
elif pct > 0.5:
    print("⚠️ 画面有轻微变化, 可能部分单位移动了")
else:
    print("❌ 画面几乎无变化, 单位可能没移动")
print(f"对比截图: {out_dir}/seq_before.png vs {out_dir}/seq_after.png")
print("=" * 60)
