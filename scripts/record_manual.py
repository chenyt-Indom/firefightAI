"""截图法录制: 前→用户拖拽→后 → 分析单位位移"""
import time, subprocess, cv2, numpy as np, sys

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"

def cap():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

print("📸 截图(前)...")
before = cap()
cv2.imwrite("screenshots/manual_before.png", before)

print()
print("┌─────────────────────────────────────┐")
print("│  现在请用你的手指拖拽一个单位移动！   │")
print("│  拖完后按 Enter 继续                 │")
print("└─────────────────────────────────────┘")
print()

input("按 Enter 截图(后)...")

print("📸 截图(后)...")
after = cap()
cv2.imwrite("screenshots/manual_after.png", after)

# 检测前后帧中的所有单位
def detect_allies(img):
    h, w = img.shape[:2]
    small = cv2.resize(img, (w//2, h//2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    return [(int(centroids[i][0]*2), int(centroids[i][1]*2), stats[i][4])
            for i in range(1, len(stats)) if stats[i][4] >= 10]

before_allies = detect_allies(before)
after_allies = detect_allies(after)

# 找移动最大的单位 (位移 > 50px)
print(f"\n📊 分析单位移动:")
moved = []
for i, (bx, by, ba) in enumerate(before_allies[:20]):
    best_match = min(after_allies, key=lambda a: (a[0]-bx)**2 + (a[1]-by)**2,
                     default=(0,0,0))
    dist = ((best_match[0]-bx)**2 + (best_match[1]-by)**2)**0.5
    if dist > 30:
        moved.append((i, bx, by, best_match[0], best_match[1], dist))

moved.sort(key=lambda m: -m[5])
print(f"  检测到 {len(moved)} 个单位明显移动 (>30px)")
for _, bx, by, ax, ay, dist in moved[:5]:
    print(f"  从({bx},{by}) → ({ax},{ay}) 位移={dist:.0f}px")

print()
print("现在请描述你刚才的拖拽动作:")
print("  1. 手指点在哪里? (大致坐标)")
print("  2. 按了多久才开始拖?")
print("  3. 拖到哪里?")
print("  4. 是慢慢拖还是快速滑动?")

desc = input("> ")

# 解析用户输入
print(f"\n📝 记录: {desc}")
print("截图: screenshots/manual_before.png, manual_after.png")
