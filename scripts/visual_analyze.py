"""
视觉分析模块 - 无触控数据时, 通过帧间差异分析玩家操作
对比相邻帧的像素变化, 推断玩家的屏幕操作区域和动作模式
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loguru import logger


class VisualActionAnalyzer:
    """通过帧间差异分析玩家操作"""

    def __init__(self, session_dir: str, skip_frames: int = 50):
        self.session_dir = Path(session_dir)
        self.frames_dir = self.session_dir / "frames"
        self.frame_index_file = self.session_dir / "frame_index.jsonl"
        self.skip_frames = skip_frames

        self.frame_index = []
        self.actions = []  # 推断的动作
        self.hotspots = Counter()  # 热点区域 (网格化)
        self.motion_heatmap = np.zeros((8, 12))  # 8行x12列的运动热图

    def load(self) -> bool:
        if not self.frame_index_file.exists():
            logger.error("帧索引文件不存在")
            return False
        for line in self.frame_index_file.read_text().strip().split("\n"):
            if line.strip():
                self.frame_index.append(json.loads(line))
        logger.info(f"加载 {len(self.frame_index)} 帧索引")
        return True

    def analyze(self) -> list[dict]:
        """通过帧间差异分析玩家操作"""
        logger.info(f"开始视觉分析 (skip={self.skip_frames})...")

        prev_frame = None
        prev_idx = -1
        analyzed = 0

        for i in range(0, len(self.frame_index), self.skip_frames):
            entry = self.frame_index[i]
            frame_path = self.frames_dir / entry["filename"]

            if not frame_path.exists():
                continue

            try:
                current = cv2.imread(str(frame_path))
                if current is None:
                    continue

                if prev_frame is not None:
                    # 计算帧间差异
                    diff = cv2.absdiff(prev_frame, current)
                    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
                    _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)

                    # 找到变化区域
                    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    changes = []
                    for cnt in contours:
                        area = cv2.contourArea(cnt)
                        if area > 500:  # 忽略微小变化
                            x, y, w, h = cv2.boundingRect(cnt)
                            cx = (x + w // 2) / current.shape[1]
                            cy = (y + h // 2) / current.shape[0]
                            changes.append({
                                "x": round(cx, 4),
                                "y": round(cy, 4),
                                "area": int(area),
                                "width": int(w),
                                "height": int(h),
                            })

                            # 更新热图
                            gx = min(int(cx * 12), 11)
                            gy = min(int(cy * 8), 7)
                            self.motion_heatmap[gy][gx] += 1
                            self.hotspots[f"{gx},{gy}"] += 1

                    if changes:
                        self.actions.append({
                            "frame_from": prev_idx,
                            "frame_to": entry["frame"],
                            "change_count": len(changes),
                            "changes": changes[:5],  # 最多保存5个变化区域
                            "total_change_area": sum(c["area"] for c in changes),
                        })

                prev_frame = current
                prev_idx = entry["frame"]
                analyzed += 1

            except Exception as e:
                logger.warning(f"帧分析失败 frame_{entry['frame']}: {e}")

        logger.info(f"视觉分析完成: {analyzed} 帧分析, {len(self.actions)} 个动作推断")
        return self.actions

    def save_results(self) -> dict:
        """保存分析结果"""
        # 热图热点
        hotspot_list = sorted(self.hotspots.items(), key=lambda x: -x[1])[:15]

        result = {
            "session_name": self.session_dir.name,
            "analyzed_at": datetime.now().isoformat(),
            "total_frames": len(self.frame_index),
            "analyzed_frames": len(self.actions),
            "method": "frame_differencing",
            "hotspots": [{"grid": k, "intensity": v} for k, v in hotspot_list],
            "motion_heatmap": self.motion_heatmap.tolist(),
            "actions": self.actions[:200],
            "summary": self._summarize(),
        }

        out = self.session_dir / "visual_analysis.json"
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"视觉分析保存: {out}")
        return result

    def _summarize(self) -> dict:
        """总结分析结果"""
        if not self.actions:
            return {}

        change_counts = [a["change_count"] for a in self.actions]
        total_areas = [a["total_change_area"] for a in self.actions]

        # 动作频率
        high_action = sum(1 for c in change_counts if c > 5)
        medium_action = sum(1 for c in change_counts if 2 <= c <= 5)
        low_action = sum(1 for c in change_counts if c < 2)

        # 找出变化最大的帧 (可能是关键操作时刻)
        key_moments = sorted(
            self.actions, key=lambda x: x["total_change_area"], reverse=True
        )[:10]

        return {
            "total_actions": len(self.actions),
            "avg_changes_per_frame": round(sum(change_counts) / len(change_counts), 1),
            "avg_change_area": round(sum(total_areas) / len(total_areas), 0),
            "action_intensity": {
                "high": high_action,
                "medium": medium_action,
                "low": low_action,
            },
            "key_moments": [
                {
                    "frame": km["frame_to"],
                    "changes": km["change_count"],
                    "area": km["total_change_area"],
                }
                for km in key_moments[:5]
            ],
        }

    def print_report(self) -> None:
        summary = self._summarize()
        if not summary:
            print("无分析结果")
            return

        print(f"\n{'='*60}")
        print(f"  视觉分析报告: {self.session_dir.name}")
        print(f"{'='*60}")
        print(f"  分析帧数: {len(self.actions)}")
        print(f"  平均每帧变化区域: {summary['avg_changes_per_frame']}")
        print(f"  平均变化面积: {summary['avg_change_area']:.0f} px")
        print(f"  高活动帧(>5变化): {summary['action_intensity']['high']}")
        print(f"  中活动帧(2-5变化): {summary['action_intensity']['medium']}")
        print(f"  低活动帧(<2变化): {summary['action_intensity']['low']}")

        print(f"\n  [关键操作时刻]")
        for km in summary.get("key_moments", [])[:5]:
            print(f"  帧#{km['frame']}: {km['changes']}个变化区域, 面积={km['area']}")

        if self.hotspots:
            print(f"\n  [操作热点区域]")
            for grid, count in sorted(self.hotspots.items(), key=lambda x: -x[1])[:5]:
                print(f"  网格({grid}): {int(count)} 次")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="视觉分析 - 帧间差异推断玩家操作")
    parser.add_argument("--session", "-s", required=True, help="会话名")
    parser.add_argument("--sessions_dir", default="sessions", help="会话根目录")
    parser.add_argument("--skip", type=int, default=50, help="每隔N帧分析")
    args = parser.parse_args()

    session_dir = Path(args.sessions_dir) / args.session
    analyzer = VisualActionAnalyzer(session_dir, skip_frames=args.skip)

    if not analyzer.load():
        sys.exit(1)

    analyzer.analyze()
    analyzer.save_results()
    analyzer.print_report()


if __name__ == "__main__":
    main()