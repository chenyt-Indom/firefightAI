"""交互式标注工具 - 用鼠标框选单位并分配类别

使用方法:
  python scripts/annotate.py --input datasets/firefight_mod/

操作说明:
  鼠标拖拽  - 框选单位
  数字键0-5 - 分配类别: 0=坦克 1=IFV 2=步兵 3=狙击手 4=直升机 5=建筑
  D         - 删除最后选中的框
  Backspace - 删除所有框
  N/→       - 下一张图
  P/←       - 上一张图
  S         - 保存当前标注
  Q/Esc     - 退出并保存
  +/-       - 缩放图像
  R         - 重置缩放
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# 现代MOD类别映射
CLASS_NAMES = {
    0: "tank",
    1: "ifv",
    2: "infantry",
    3: "sniper",
    4: "helicopter",
    5: "building",
}

CLASS_COLORS = {
    0: (0, 0, 255),      # 坦克 - 红色
    1: (0, 165, 255),    # IFV - 橙色
    2: (0, 255, 0),      # 步兵 - 绿色
    3: (255, 0, 255),    # 狙击手 - 紫色
    4: (255, 255, 0),    # 直升机 - 青色
    5: (128, 128, 128),  # 建筑 - 灰色
}


class Annotator:
    def __init__(self, input_dir: str):
        self.input_dir = Path(input_dir)
        self.labels_dir = self.input_dir / "labels"
        self.labels_dir.mkdir(exist_ok=True)
        self.progress_file = self.input_dir / "annotate_progress.json"

        # 加载图片列表
        self.images = sorted(self.input_dir.glob("frame_*.jpg"))
        if not self.images:
            print(f"错误: {input_dir} 中没有找到 frame_*.jpg 图片")
            sys.exit(1)

        # 加载进度
        self.current_idx = self._load_progress()
        self.current_idx = min(self.current_idx, len(self.images) - 1)

        # 当前图片状态
        self.img: np.ndarray | None = None
        self.display_img: np.ndarray | None = None
        self.boxes: list[dict] = []  # [{x1,y1,x2,y2,cls_id}]
        self.drawing = False
        self.start_pt = (0, 0)
        self.current_pt = (0, 0)
        self.selected_class = 0

        # 缩放
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.panning = False
        self.pan_start = (0, 0)

        print(f"标注工具启动: {len(self.images)} 张图片")
        print(f"从第 {self.current_idx + 1} 张开始")

    def run(self):
        self._load_image()
        cv2.namedWindow("Annotator", cv2.WINDOW_NORMAL)
        cv2.setMouseCallback("Annotator", self._mouse_callback)

        while True:
            self._render()
            key = cv2.waitKey(20) & 0xFF

            if key == ord("q") or key == 27:  # Q / Esc
                self._save()
                break
            elif key == ord("n") or key == 83:  # N / →
                self._save()
                self.current_idx = min(self.current_idx + 1, len(self.images) - 1)
                self._load_image()
            elif key == ord("p") or key == 81:  # P / ←
                self._save()
                self.current_idx = max(self.current_idx - 1, 0)
                self._load_image()
            elif key == ord("s"):  # S
                self._save()
                print(f"已保存: {self.images[self.current_idx].name}")
            elif key == ord("d"):  # D
                if self.boxes:
                    removed = self.boxes.pop()
                    print(f"删除框: cls={CLASS_NAMES.get(removed['cls_id'], '?')}")
            elif key == 8:  # Backspace
                self.boxes.clear()
                print("清除所有框")
            elif key in (ord("0"), ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
                self.selected_class = key - ord("0")
                print(f"当前类别: [{self.selected_class}] {CLASS_NAMES[self.selected_class]}")
            elif key == ord("=") or key == ord("+"):  # 放大
                self.scale = min(self.scale * 1.2, 5.0)
                self._update_display()
            elif key == ord("-"):  # 缩小
                self.scale = max(self.scale / 1.2, 0.2)
                self._update_display()
            elif key == ord("r"):  # 重置缩放
                self.scale = 1.0
                self.offset_x = 0
                self.offset_y = 0
                self._update_display()

        cv2.destroyAllWindows()
        self._save_progress()
        print(f"标注完成, 进度已保存到第 {self.current_idx + 1} 张")

    def _load_image(self):
        path = self.images[self.current_idx]
        self.img = cv2.imread(str(path))
        if self.img is None:
            print(f"无法加载图片: {path}")
            return
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.boxes = []
        self._load_labels()
        self._update_display()
        print(f"[{self.current_idx + 1}/{len(self.images)}] {path.name}")

    def _load_labels(self):
        label_path = self.labels_dir / f"{self.images[self.current_idx].stem}.txt"
        if not label_path.exists():
            return
        h, w = self.img.shape[:2]
        with open(label_path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 5:
                    cls_id = int(parts[0])
                    cx, cy, bw, bh = map(float, parts[1:])
                    # 从YOLO格式转回像素坐标
                    x1 = int((cx - bw / 2) * w)
                    y1 = int((cy - bh / 2) * h)
                    x2 = int((cx + bw / 2) * w)
                    y2 = int((cy + bh / 2) * h)
                    self.boxes.append({
                        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                        "cls_id": cls_id,
                    })

    def _save(self):
        if not self.boxes:
            # 删除空标注文件
            label_path = self.labels_dir / f"{self.images[self.current_idx].stem}.txt"
            if label_path.exists():
                label_path.unlink()
            return
        h, w = self.img.shape[:2]
        label_path = self.labels_dir / f"{self.images[self.current_idx].stem}.txt"
        with open(label_path, "w") as f:
            for b in self.boxes:
                x1, y1, x2, y2 = b["x1"], b["y1"], b["x2"], b["y2"]
                cx = ((x1 + x2) / 2) / w
                cy = ((y1 + y2) / 2) / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                f.write(f"{b['cls_id']} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")

    def _update_display(self):
        if self.img is None:
            return
        h, w = self.img.shape[:2]
        new_w = int(w * self.scale)
        new_h = int(h * self.scale)
        self.display_img = cv2.resize(self.img, (new_w, new_h))

    def _render(self):
        if self.display_img is None:
            return
        canvas = self.display_img.copy()

        # 绘制已有标注框
        for b in self.boxes:
            x1 = int(b["x1"] * self.scale)
            y1 = int(b["y1"] * self.scale)
            x2 = int(b["x2"] * self.scale)
            y2 = int(b["y2"] * self.scale)
            cls_id = b["cls_id"]
            color = CLASS_COLORS.get(cls_id, (255, 255, 255))
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
            label = f"{cls_id}:{CLASS_NAMES.get(cls_id, '?')}"
            cv2.putText(canvas, label, (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # 绘制正在绘制的框
        if self.drawing:
            cv2.rectangle(canvas, self.start_pt, self.current_pt,
                          (0, 255, 255), 1)

        # 状态栏
        h, w = canvas.shape[:2]
        info = [
            f"Image: {self.current_idx + 1}/{len(self.images)} - {self.images[self.current_idx].name}",
            f"Class: [{self.selected_class}] {CLASS_NAMES[self.selected_class]}",
            f"Boxes: {len(self.boxes)}",
            f"Scale: {self.scale:.1f}x",
            "Keys: 0-5=class D=del N=next P=prev S=save Q=quit",
        ]
        y_offset = h - 20
        for i, text in enumerate(reversed(info)):
            cv2.putText(canvas, text, (5, y_offset),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
            y_offset -= 16

        cv2.imshow("Annotator", canvas)

    def _mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start_pt = (x, y)
            self.current_pt = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE:
            if self.drawing:
                self.current_pt = (x, y)
        elif event == cv2.EVENT_LBUTTONUP:
            if self.drawing:
                self.drawing = False
                x1 = min(self.start_pt[0], x)
                y1 = min(self.start_pt[1], y)
                x2 = max(self.start_pt[0], x)
                y2 = max(self.start_pt[1], y)
                # 转换回原始像素坐标
                ox1 = int(x1 / self.scale)
                oy1 = int(y1 / self.scale)
                ox2 = int(x2 / self.scale)
                oy2 = int(y2 / self.scale)
                if ox2 - ox1 > 3 and oy2 - oy1 > 3:  # 过滤太小的框
                    self.boxes.append({
                        "x1": ox1, "y1": oy1,
                        "x2": ox2, "y2": oy2,
                        "cls_id": self.selected_class,
                    })
                    print(f"添加框: [{self.selected_class}] {CLASS_NAMES[self.selected_class]} "
                          f"({ox1},{oy1})-({ox2},{oy2})")

    def _save_progress(self):
        self.progress_file.write_text(json.dumps({"last_idx": self.current_idx}))

    def _load_progress(self) -> int:
        if self.progress_file.exists():
            try:
                data = json.loads(self.progress_file.read_text())
                return data.get("last_idx", 0)
            except Exception:
                pass
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Firefight MOD 单位标注工具")
    parser.add_argument("--input", "-i", default="datasets/firefight_mod",
                        help="图片目录")
    args = parser.parse_args()

    annotator = Annotator(args.input)
    annotator.run()