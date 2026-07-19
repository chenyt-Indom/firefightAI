"""旗帜 YOLO 训练数据生成

读取阵营选择/单位选择截图, 手动标注常见旗帜位置, 生成YOLO格式数据集
支持快速标注: 屏幕坐标 → 自动生成YOLO txt

用法: python scripts/train_flags.py
"""

import subprocess, time, cv2, numpy as np
from pathlib import Path
import yaml

PROJECT = Path(__file__).parent.parent.resolve()
if not (PROJECT / "screenshots").exists():
    PROJECT = Path(r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI")
DATASET = PROJECT / "data" / "flag_yolo"
DATASET.mkdir(parents=True, exist_ok=True)
(DATASET / "images").mkdir(exist_ok=True)
(DATASET / "labels").mkdir(exist_ok=True)

# 已知阵营 → class_id 映射
# 按界面顺序 (China=0=红色, USA=1=蓝白红, ...)
FACTION_CLASSES = {
    "UN": 0, "Poland": 1, "UK": 2, "France": 3, "ODKB": 4, "USA": 5,
    "Japan": 6, "China": 7, "Korea": 8, "Germany": 9, "Italy": 10,
    "Ukraine": 11, "Scandinavia": 12, "MidEast": 13, "Balkans": 14,
    "CentralEU": 15, "Baltic": 16,
}

# 已知截图中的旗帜位置 (按视觉检查)
# 格式: (image_filename, faction_name, x1, y1, x2, y2)
KNOWN_FLAGS = [
    # 阵营选择界面 (4张)
    ("screenshots/flag_screen_0.png", "UN", 940, 198, 985, 245),
    ("screenshots/flag_screen_0.png", "Poland", 940, 246, 985, 280),
    ("screenshots/flag_screen_0.png", "UK", 940, 290, 985, 320),
    ("screenshots/flag_screen_0.png", "France", 940, 330, 985, 365),
    ("screenshots/flag_screen_0.png", "USA", 940, 370, 985, 405),
    ("screenshots/flag_screen_0.png", "Japan", 940, 410, 985, 445),
    ("screenshots/flag_screen_0.png", "China", 940, 450, 985, 480),
    ("screenshots/flag_screen_0.png", "Korea", 940, 490, 985, 520),
    ("screenshots/flag_screen_0.png", "Germany", 940, 530, 985, 565),
    ("screenshots/flag_screen_0.png", "Italy", 940, 570, 985, 605),
    ("screenshots/flag_screen_0.png", "Ukraine", 940, 610, 985, 645),
    ("screenshots/flag_screen_0.png", "Scandinavia", 940, 650, 985, 685),
    ("screenshots/flag_screen_0.png", "MidEast", 940, 690, 985, 720),
    ("screenshots/flag_screen_0.png", "Balkans", 940, 730, 985, 760),
    ("screenshots/flag_screen_0.png", "CentralEU", 940, 770, 985, 800),
    # You/Enemy 选兵界面 (side_X)
    ("screenshots/side_0.png", "China", 0, 280, 100, 350),  # You: China
    ("screenshots/side_0.png", "USA", 0, 460, 100, 540),  # Enemy: USA
]


def xywh2yolo(x1, y1, x2, y2, W, H):
    cx = ((x1 + x2) / 2) / W
    cy = ((y1 + y2) / 2) / H
    w = (x2 - x1) / W
    h = (y2 - y1) / H
    return cx, cy, w, h


def auto_detect_flags(img_path: str) -> list:
    """自动检测旗帜位置 (简化版)"""
    img = cv2.imread(img_path)
    if img is None:
        return []
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = hsv[:,:,1]
    # 找x=920-1000范围, 25-50px高的旗状区域
    mask = (sat > 80).astype(np.uint8) * 255
    roi = mask[100:1050, 920:1000]
    n, labels, stats, _ = cv2.connectedComponentsWithStats(roi, 8)
    boxes = []
    for i in range(1, n):
        x1, y1, w, h = stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3]
        if 25 <= w <= 60 and 25 <= h <= 60:
            boxes.append((x1 + 920, y1 + 100, x1 + w + 920, y1 + h + 100))
    return boxes


def main():
    print("=" * 50)
    print("  🚩 Firefight 旗帜 YOLO 训练数据生成")
    print("=" * 50)

    # 收集训练数据 - 用完整路径作为key
    labeled = []  # (img_path, faction, x1, y1, x2, y2)
    img_count = {}  # key: 完整路径

    for img_path, faction, x1, y1, x2, y2 in KNOWN_FLAGS:
        if img_path not in img_count:
            img_count[img_path] = 0
        img_count[img_path] += 1
        labeled.append((img_path, faction, x1, y1, x2, y2))

    print(f"手动标注: {len(labeled)} 个旗帜, {len(img_count)} 张图")

    # 为每张图创建YOLO文件
    for img_name, count in img_count.items():
        # img_name 是完整路径, 取目录前缀
        src = PROJECT / img_name
        if not src.exists():
            print(f"  ⚠️ 跳过缺失: {img_name}")
            continue
        # 复制图片
        dst_img = DATASET / "images" / Path(img_name).name
        cv2.imwrite(str(dst_img), cv2.imread(str(src)))

        # 生成YOLO标注
        H, W = 1080, 1920
        labels = []
        for ipath, faction, x1, y1, x2, y2 in labeled:
            if ipath != img_name:
                continue
            cls_id = FACTION_CLASSES.get(faction, 0)
            cx, cy, w, h = xywh2yolo(x1, y1, x2, y2, W, H)
            labels.append(f"{cls_id} {cx:.4f} {cy:.4f} {w:.4f} {h:.4f}")
        # 写入标签
        dst_lbl = DATASET / "labels" / (Path(img_name).stem + ".txt")
        dst_lbl.write_text("\n".join(labels))
        print(f"  ✅ {img_name}: {count} 个旗标")

    # 生成 data.yaml
    yaml_content = {
        "path": str(DATASET),
        "train": "images",
        "val": "images",
        "names": FACTION_CLASSES,
        "nc": len(FACTION_CLASSES),
    }
    yaml_path = DATASET / "data.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(yaml_content, f, default_flow_style=False)
    print(f"\n✅ data.yaml: {yaml_path}")

    print(f"\n📁 数据集: {DATASET}")
    print(f"   {len(list((DATASET / 'images').iterdir()))} 张图")
    print(f"   {len(labeled)} 个标注")


if __name__ == "__main__":
    main()
