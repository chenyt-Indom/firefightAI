"""不猜偏移：先选中→截图→检测圆圈精确位置→从圆圈拖拽"""
import cv2, numpy as np, subprocess, time, os

ADB = r'D:\MuMuPlayer\nx_main\adb.exe'
MUMU = r'D:\MuMuPlayer\nx_main\MuMuManager.exe'
OUT = r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots'

def capture():
    r = subprocess.run([ADB, '-s', '127.0.0.1:7555', 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def mumu(cmd):
    subprocess.run([MUMU, 'control', '-v', '0', 'tool', 'cmd', '-c', cmd],
                   capture_output=True, text=True, timeout=10)

def find_circle_after_tap(before, after, ux, uy, search_radius=80):
    """选中后，在单位周围搜索新增的蓝色圆圈"""
    h1, w1 = before.shape[:2]
    x1 = max(0, ux - search_radius)
    y1 = max(0, uy - 20)
    x2 = min(w1, ux + search_radius)
    y2 = min(h1, uy + search_radius + 40)
    
    # BGR蓝色范围检测
    def blue_mask(img):
        roi = img[y1:y2, x1:x2]
        return cv2.inRange(roi, np.array([80,40,0]), np.array([255,255,80]))
    
    before_blue = blue_mask(before)
    after_blue = blue_mask(after)
    
    # 新增的蓝色像素
    new_blue = cv2.subtract(after_blue, before_blue)
    ys, xs = np.where(new_blue > 0)
    
    if len(xs) < 3:
        return None
    
    # 找新增蓝色区域的重心
    cx_new = int(np.mean(xs)) + x1
    cy_new = int(np.mean(ys)) + y1
    
    # 也找最大联通区域中心
    num_labels, _, stats, centroids = cv2.connectedComponentsWithStats(new_blue, 8)
    if num_labels > 1:
        # 找最大区域
        largest_label = 1
        for i in range(2, num_labels):
            if stats[i, 4] > stats[largest_label, 4]:
                largest_label = i
        ccx, ccy = int(centroids[largest_label][0]) + x1, int(centroids[largest_label][1]) + y1
        return (cx_new, cy_new), (ccx, ccy), len(xs)
    
    return None

# 找第一个合适的单位
before = capture()
hsv = cv2.cvtColor(before, cv2.COLOR_BGR2HSV)
ally_mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
_, _, stats, centroids = cv2.connectedComponentsWithStats(ally_mask, 8)

# 找中间区域的大单位 (放宽筛选)
best = None
all_candidates = []
for i in range(1, min(200, len(stats))):
    area = stats[i, 4]
    cx, cy = int(centroids[i][0]), int(centroids[i][1])
    if 50 < area < 3000 and 100 < cy < 950 and 100 < cx < 1800:
        all_candidates.append((cx, cy, area))

if all_candidates:
    all_candidates.sort(key=lambda x: -x[2])  # 最大的优先
    best = all_candidates[0]

if not best:
    print("无合适单位!"); exit()

ux, uy, area = best
print(f"选中单位: ({ux}, {uy}), 面积={area}")

# 步骤1: 取消所有选中
mumu("input tap 10 10")
time.sleep(0.3)

# 步骤2: 重新截图（确保无选中状态）
before = capture()

# 步骤3: 点击单位选中
mumu(f"input tap {ux} {uy}")
time.sleep(0.8)  # 等蓝线出现

# 步骤4: 截图检测圆圈位置
after_tap = capture()
cv2.imwrite(os.path.join(OUT, 'after_tap.png'), after_tap)

result = find_circle_after_tap(before, after_tap, ux, uy)
if not result:
    print("未检测到新增蓝色像素! 可能偏移不在搜索范围内")
    # 扩大搜索
    before2 = capture()
    after_tap2 = capture()
    result2 = find_circle_after_tap(before2, after_tap2, ux, uy, search_radius=120)
    if result2:
        result = result2
    else:
        print("扩大搜索也未找到")
        exit()

(avg_x, avg_y), (ccx, ccy), count = result
print(f"检测到圆圈: 重心({avg_x},{avg_y}), 最大区域中心({ccx},{ccy}), 新增{count}蓝像素")
print(f"相对单位偏移: 重心(dx={avg_x-ux}, dy={avg_y-uy}), 区域中心(dx={ccx-ux}, dy={ccy-uy})")

# 步骤5: 从精确圆圈位置拖拽
target_y = max(10, ccy - 250)
print(f"拖拽: ({ccx},{ccy}) → ({ccx},{target_y})")
mumu(f"input swipe {ccx} {ccy} {ccx} {target_y} 1000")
time.sleep(2.0)

# 步骤6: 最终截图
after_move = capture()

# 对比
diff = cv2.absdiff(before, after_move)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
change_pct = np.count_nonzero(thresh) / (before.shape[0]*before.shape[1]) * 100

# 标记截图
mb = before.copy()
ma = after_move.copy()
cv2.circle(mb, (ux,uy), 10, (0,255,255), 3)
cv2.circle(mb, (ccx,ccy), 8, (255,0,0), 3)
cv2.circle(mb, (ccx,target_y), 6, (0,0,255), 2)
cv2.circle(ma, (ux,uy), 10, (0,255,255), 3)
cv2.circle(ma, (ccx,ccy), 8, (255,0,0), 3)
cv2.circle(ma, (ccx,target_y), 6, (0,0,255), 2)
cv2.imwrite(os.path.join(OUT, 'precise_before.png'), mb)
cv2.imwrite(os.path.join(OUT, 'precise_after.png'), ma)

print(f"\n画面变化: {change_pct:.2f}%")
print(f"截图: after_tap.png(选中状态), precise_before.png, precise_after.png")
print("看 after_tap.png 确认蓝线/圆圈是否出现, 再看单位有没有移动!")
