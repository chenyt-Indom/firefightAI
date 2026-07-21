"""
学习模块 - 从录制的游戏会话中提取战术模式, 生成few-shot示例

流程:
  1. 读取 analysis.json (状态-动作对)
  2. 聚类分析: 识别常见战术模式
  3. 生成新的few-shot示例
  4. 更新 prompts/few_shot_learned.txt (AI参考学习到的模式)

使用方法:
  python scripts/learn_from_session.py --session my_game_1
  python scripts/learn_from_session.py --session my_game_1 --merge  # 合并到主few_shot
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger


class TacticalPatternLearner:
    """战术模式学习器 - 从玩家操作中提取可复用的战术模式"""

    def __init__(self, sessions_dir: str = "sessions"):
        self.sessions_dir = Path(sessions_dir)
        self.prompts_dir = Path("src/decision/prompts")

    def learn_from_session(self, session_name: str) -> dict:
        """从单个会话中学习 (支持触控分析和视觉分析)"""
        session_dir = self.sessions_dir / session_name
        analysis_file = session_dir / "analysis.json"
        visual_file = session_dir / "visual_analysis.json"

        # 优先使用触控分析, 回退到视觉分析
        if analysis_file.exists():
            analysis = json.loads(analysis_file.read_text(encoding="utf-8"))
            actions = analysis.get("actions", [])
            pairs = analysis.get("state_action_pairs", [])
            method = "touch"
        elif visual_file.exists():
            logger.info("使用视觉分析数据 (无触控数据)")
            analysis = json.loads(visual_file.read_text(encoding="utf-8"))
            raw_actions = analysis.get("actions", [])
            actions = self._convert_visual_actions(raw_actions)
            pairs = []
            method = "visual"
        else:
            logger.error(f"分析文件不存在: {analysis_file} / {visual_file}")
            logger.error("请先运行: python scripts/analyze_session.py 或 python scripts/visual_analyze.py")
            return {}

        if not actions:
            logger.warning("没有动作数据, 无法学习")
            return {}

        logger.info(f"开始学习: {session_name} ({len(actions)} 动作, {len(pairs)} 状态-动作对)")

        # 1. 动作类型统计
        action_stats = self._analyze_action_types(actions)

        # 2. 空间模式分析 (玩家偏好哪些区域)
        spatial_patterns = self._analyze_spatial_patterns(actions)

        # 3. 动作序列分析 (发现常见战术连招)
        sequences = self._analyze_action_sequences(actions)

        # 4. 时序模式分析
        temporal_patterns = self._analyze_temporal_patterns(actions)

        result = {
            "session_name": session_name,
            "learned_at": datetime.now().isoformat(),
            "analysis_method": method,
            "total_actions": len(actions),
            "action_distribution": action_stats,
            "spatial_hotspots": spatial_patterns,
            "tactical_sequences": sequences,
            "temporal_patterns": temporal_patterns,
            "playstyle": self._infer_playstyle(action_stats, spatial_patterns, sequences),
        }

        # 保存学习结果
        output_file = session_dir / "learned_patterns.json"
        output_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"学习结果已保存: {output_file}")

        return result

    def _convert_visual_actions(self, raw_actions: list[dict]) -> list[dict]:
        """将视觉分析的动作格式转换为学习器格式"""
        converted = []
        for action in raw_actions:
            changes = action.get("changes", [])
            if changes:
                # 用变化区域中心作为目标位置
                for c in changes[:3]:
                    converted.append({
                        "frame_idx": action.get("frame_to", 0),
                        "action_type": "visual_change",
                        "target_x": c.get("x", 0),
                        "target_y": c.get("y", 0),
                        "gesture": "visual",
                    })
        return converted

    def _analyze_action_types(self, actions: list[dict]) -> dict:
        """分析动作类型分布"""
        stats = Counter()
        for a in actions:
            stats[a.get("action_type", "unknown")] += 1
        return dict(stats)

    def _analyze_spatial_patterns(self, actions: list[dict]) -> dict:
        """分析空间分布模式 (玩家偏好区域)"""
        # 将屏幕分成6x4网格
        grid_w, grid_h = 6, 4
        grid = defaultdict(int)

        for a in actions:
            tx = a.get("target_x", 0)
            ty = a.get("target_y", 0)
            gx = min(int(tx * grid_w), grid_w - 1)
            gy = min(int(ty * grid_h), grid_h - 1)
            grid[f"{gx},{gy}"] += 1

        # 找到热点区域
        hotspots = sorted(grid.items(), key=lambda x: -x[1])[:10]
        return [{"grid": k, "count": v} for k, v in hotspots]

    def _analyze_action_sequences(self, actions: list[dict]) -> list[dict]:
        """分析常见动作序列 (2-3步战术连招)"""
        # 提取2-grams
        bigrams = Counter()
        for i in range(len(actions) - 1):
            seq = f"{actions[i]['action_type']} -> {actions[i+1]['action_type']}"
            bigrams[seq] += 1

        # 提取3-grams
        trigrams = Counter()
        for i in range(len(actions) - 2):
            seq = f"{actions[i]['action_type']} -> {actions[i+1]['action_type']} -> {actions[i+2]['action_type']}"
            trigrams[seq] += 1

        sequences = []
        for seq, count in bigrams.most_common(10):
            sequences.append({"pattern": seq, "type": "bigram", "count": count})
        for seq, count in trigrams.most_common(5):
            sequences.append({"pattern": seq, "type": "trigram", "count": count})

        return sequences

    def _analyze_temporal_patterns(self, actions: list[dict]) -> dict:
        """分析时序模式"""
        if len(actions) < 2:
            return {}

        # 动作间隔分布
        intervals = []
        for i in range(1, len(actions)):
            if actions[i].get("frame_idx") and actions[i-1].get("frame_idx"):
                gap = actions[i]["frame_idx"] - actions[i-1]["frame_idx"]
                intervals.append(gap)

        if intervals:
            avg_interval = sum(intervals) / len(intervals)
            intervals.sort()
            return {
                "avg_frame_gap": round(avg_interval, 1),
                "median_frame_gap": intervals[len(intervals) // 2],
                "min_frame_gap": intervals[0],
                "max_frame_gap": intervals[-1],
            }
        return {}

    def _infer_playstyle(self, action_stats: dict, spatial: list, sequences: list) -> dict:
        """推断玩家风格"""
        total = sum(action_stats.values()) or 1
        select_pct = action_stats.get("select", 0) / total
        move_pct = action_stats.get("move", 0) / total
        swipe_pct = action_stats.get("swipe_map", 0) / total

        style = []
        if move_pct > 0.4:
            style.append("进攻型")
        elif move_pct > 0.2:
            style.append("平衡型")
        else:
            style.append("防守型")

        if select_pct > 0.3:
            style.append("微操型")
        if swipe_pct > 0.1:
            style.append("全局视野型")

        if move_pct > 0.3 and select_pct > 0.2:
            style.append("多线操作型")

        return {
            "labels": style,
            "aggressiveness": round(move_pct, 2),
            "micro_intensity": round(select_pct, 2),
            "map_awareness": round(swipe_pct, 2),
        }

    def generate_few_shot_examples(self, session_name: str) -> list[dict]:
        """从学习结果生成few-shot示例"""
        session_dir = self.sessions_dir / session_name
        pattern_file = session_dir / "learned_patterns.json"

        if not pattern_file.exists():
            # 先学习
            self.learn_from_session(session_name)

        if not pattern_file.exists():
            return []

        patterns = json.loads(pattern_file.read_text(encoding="utf-8"))
        playstyle = patterns.get("playstyle", {})
        spatial = patterns.get("spatial_hotspots", [])
        sequences = patterns.get("tactical_sequences", [])

        examples = []

        # 生成示例模板
        if sequences:
            top_sequence = sequences[0]
            examples.append({
                "source": f"learned_from_{session_name}",
                "pattern": top_sequence["pattern"],
                "frequency": top_sequence["count"],
                "playstyle": playstyle.get("labels", []),
                "description": (
                    f"玩家在{session_name}会话中频繁使用此战术模式: "
                    f"{top_sequence['pattern']} (出现{top_sequence['count']}次)"
                ),
            })

        if spatial:
            top_hotspot = spatial[0]
            examples.append({
                "source": f"learned_from_{session_name}",
                "pattern": "spatial_preference",
                "hotspot": top_hotspot["grid"],
                "frequency": top_hotspot["count"],
                "description": (
                    f"玩家偏好操作区域: 网格{top_hotspot['grid']} "
                    f"(出现{top_hotspot['count']}次)"
                ),
            })

        return examples

    def save_learned_prompts(self, session_name: str) -> Path:
        """将学习到的模式保存为few-shot提示文件"""
        patterns = self.learn_from_session(session_name)
        if not patterns:
            return None

        playstyle = patterns.get("playstyle", {})
        sequences = patterns.get("tactical_sequences", [])
        spatial = patterns.get("spatial_hotspots", [])
        action_stats = patterns.get("action_distribution", {})

        # 生成学习提示
        lines = [
            f"## AI学习自玩家会话: {session_name}",
            f"## 学习时间: {patterns.get('learned_at', '')}",
            f"## 玩家风格: {', '.join(playstyle.get('labels', []))}",
            f"## 进攻性: {playstyle.get('aggressiveness', 0):.0%}",
            f"## 微操强度: {playstyle.get('micro_intensity', 0):.0%}",
            "",
            "### 玩家操作习惯",
        ]

        for action_type, count in sorted(action_stats.items(), key=lambda x: -x[1]):
            lines.append(f"- {action_type}: {count}次")

        if sequences:
            lines.append("")
            lines.append("### 常见战术模式")
            for seq in sequences[:5]:
                lines.append(f"- {seq['pattern']} (出现{seq['count']}次)")

        if spatial:
            lines.append("")
            lines.append("### 偏好操作区域")
            for spot in spatial[:3]:
                lines.append(f"- 网格{spot['grid']}: {spot['count']}次")

        lines.append("")
        lines.append("### 学习建议")
        lines.append("AI应参考以上玩家的操作习惯和战术偏好，")
        lines.append("在决策时模仿玩家的风格和战术模式。")

        output_file = self.prompts_dir / "few_shot_learned.txt"
        output_file.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"学习提示已保存: {output_file}")

        return output_file

    def merge_to_main_prompts(self, session_name: str) -> bool:
        """将学习到的模式合并到主few_shot.txt"""
        learned_file = self.prompts_dir / "few_shot_learned.txt"
        if not learned_file.exists():
            self.save_learned_prompts(session_name)
        if not learned_file.exists():
            return False

        main_file = self.prompts_dir / "few_shot.txt"
        main_content = main_file.read_text(encoding="utf-8")
        learned_content = learned_file.read_text(encoding="utf-8")

        # 追加到few_shot.txt末尾
        new_content = main_content.rstrip() + "\n\n---\n\n" + learned_content

        # 备份
        backup_file = self.prompts_dir / f"few_shot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        main_file.rename(backup_file)

        main_file.write_text(new_content, encoding="utf-8")
        logger.info(f"已合并学习模式到 few_shot.txt (备份: {backup_file.name})")
        return True

    def print_learning_report(self, session_name: str) -> None:
        """打印学习报告"""
        patterns = self.learn_from_session(session_name)
        if not patterns:
            return

        playstyle = patterns.get("playstyle", {})
        action_stats = patterns.get("action_distribution", {})
        sequences = patterns.get("tactical_sequences", [])
        spatial = patterns.get("spatial_hotspots", [])

        print(f"\n{'='*60}")
        print(f"  学习报告: {session_name}")
        print(f"{'='*60}")

        print(f"\n  [玩家风格]")
        print(f"  类型: {', '.join(playstyle.get('labels', []))}")
        print(f"  进攻性: {playstyle.get('aggressiveness', 0):.0%}")
        print(f"  微操强度: {playstyle.get('micro_intensity', 0):.0%}")
        print(f"  地图意识: {playstyle.get('map_awareness', 0):.0%}")

        print(f"\n  [操作分布]")
        total = sum(action_stats.values()) or 1
        for action_type, count in sorted(action_stats.items(), key=lambda x: -x[1]):
            bar = "█" * int(count / total * 30)
            print(f"  {action_type:12s}: {bar} {count} ({count/total:.0%})")

        if sequences:
            print(f"\n  [常见战术模式]")
            for seq in sequences[:5]:
                print(f"  {seq['pattern']} (x{seq['count']})")

        if spatial:
            print(f"\n  [偏好操作区域]")
            for spot in spatial[:3]:
                print(f"  网格{spot['grid']}: {spot['count']}次")

        print(f"\n{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Firefight AI - 战术学习模块 (从玩家操作中学习)",
        epilog="""
示例:
  python scripts/learn_from_session.py --session my_game_1              # 学习并生成报告
  python scripts/learn_from_session.py --session my_game_1 --save        # 学习并保存prompt
  python scripts/learn_from_session.py --session my_game_1 --merge       # 学习并合并到few_shot
        """,
    )
    parser.add_argument("--session", "-s", required=True, help="会话名称")
    parser.add_argument("--sessions_dir", default="sessions", help="会话根目录")
    parser.add_argument("--save", action="store_true", help="保存学习到的prompt")
    parser.add_argument("--merge", action="store_true", help="合并到主few_shot.txt")

    args = parser.parse_args()

    learner = TacticalPatternLearner(sessions_dir=args.sessions_dir)

    # 打印学习报告
    learner.print_learning_report(args.session)

    if args.save:
        learner.save_learned_prompts(args.session)

    if args.merge:
        learner.merge_to_main_prompts(args.session)
        print("\n  [已合并] 学习到的战术模式已合并到 few_shot.txt")


if __name__ == "__main__":
    main()