"""策略提炼器 (L2) — 从高分经验中总结可复用战术规则

触发时机:
  - 每局结束后 (game_over)
  - 或每 15 轮自动触发一次

工作流:
  1. 从 BattleMemory 取 Top-20 高分经验
  2. 格式化为表格发送给 LLM
  3. LLM 总结 2-3 条战术规则
  4. 追加写入 data/tactics_rules.yaml
  5. 规则自动注入到下一轮的 System Prompt 中
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from .battle_memory import BattleMemory


RULES_PATH = Path(__file__).parent.parent.parent / "data" / "tactics_rules.yaml"


class StrategyCompressor:
    """从经验中提炼战术规则"""

    def __init__(
        self,
        battle_memory: BattleMemory,
        api_key: str = "",
        api_base: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
    ):
        self.memory = battle_memory
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self._last_compress_cycle = 0  # 上次提炼的轮次
        self._compress_interval = 15    # 每15轮提炼一次

    def should_compress(self, cycle_num: int) -> bool:
        """判断是否应该触发热提炼"""
        return (cycle_num - self._last_compress_cycle) >= self._compress_interval

    def compress(self, cycle_num: int, game_session: str = "", force: bool = False) -> list[str]:
        """执行策略提炼

        Returns:
            新提炼的规则列表
        """
        if not force and not self.should_compress(cycle_num):
            return []

        logger.info(f"🧠 策略提炼开始 (第{cycle_num}轮)...")

        # 获取高分经验
        top_exps = self.memory.get_top_experiences(top_k=20, game_session=game_session)
        if len(top_exps) < 5:
            logger.info("经验不足 (需要≥5条), 跳过提炼")
            return []

        # 构建提炼 prompt
        exp_text = self._format_experiences(top_exps)
        prompt = self._build_compress_prompt(exp_text)

        # 调用 LLM 总结
        rules = self._call_llm_for_rules(prompt)
        if not rules:
            return []

        # 保存到 YAML
        self._save_rules(rules)

        self._last_compress_cycle = cycle_num
        logger.info(f"策略提炼完成: {len(rules)}条新规则")
        for r in rules:
            logger.info(f"  → {r}")

        return rules

    def compress_on_game_over(self, cycle_num: int, game_session: str = "") -> list[str]:
        """游戏结束时强制提炼"""
        return self.compress(cycle_num, game_session, force=True)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _format_experiences(self, exps: list[dict]) -> str:
        """格式化经验列表为文本"""
        lines = ["## 高分战术经验 (供总结规律)", ""]
        lines.append("| 编号 | 友军数 | 敌军数 | 决策 | 目标坐标 | 得分 |")
        lines.append("|------|--------|--------|------|----------|------|")

        for i, exp in enumerate(exps, 1):
            dec = exp["decision"]
            action = dec.get("action", "?")
            target = dec.get("target", [])
            target_str = f"({target[0]:.2f},{target[1]:.2f})" if len(target) == 2 else "-"
            reason = dec.get("reason", "")[:20]

            lines.append(
                f"| {i} | {exp['ally_count']} | {exp['enemy_count']} | "
                f"{action}({reason}) | {target_str} | +{exp['outcome_score']:.0f} |"
            )

        return "\n".join(lines)

    def _build_compress_prompt(self, exp_text: str) -> str:
        """构建规则提炼 prompt"""
        return f"""{exp_text}

---
请从以上高分战术经验中, 总结 2-3 条可复用的战术规则。

要求:
1. 每条规则格式: "当 [条件] 时, 应该 [行动], 因为 [理由]"
2. 规则必须具体可执行, 不能太笼统
3. 条件基于战场数量对比 (如"己方数量≥敌方2倍")
4. 输出为YAML格式:

```yaml
rules:
  - rule: "当己方数量≥敌方2倍时, 应派2-3个单位前出侦察+主力跟进，侦察组和主力保持0.15以上距离，利用优势兵力分割包围"
  - rule: "当己方数量≤敌方一半时, 应该全部撤退至后方0.50,0.65，因为劣势兵力正面交战必败"
```"""

    def _call_llm_for_rules(self, prompt: str) -> list[str]:
        """调用 LLM 提炼规则"""
        from openai import OpenAI

        if not self.api_key:
            logger.warning("API Key 未配置, 跳过 LLM 规则提炼")
            return []

        try:
            client = OpenAI(api_key=self.api_key, base_url=self.api_base, timeout=15)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一名军事战术分析专家。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=512,
            )

            raw = response.choices[0].message.content or ""
            logger.debug(f"规则提炼 LLM 响应: {raw[:200]}")

            # 解析 YAML
            return self._parse_rules(raw)

        except Exception as e:
            logger.error(f"规则提炼 LLM 调用失败: {e}")
            return []

    def _parse_rules(self, raw: str) -> list[str]:
        """从 LLM 响应中解析规则列表"""
        try:
            # 清理 markdown
            text = raw.strip()
            if "```" in text:
                # 提取代码块内容
                lines = text.split("\n")
                content_lines = []
                in_block = False
                for line in lines:
                    if line.startswith("```"):
                        if in_block:
                            break
                        in_block = True
                        continue
                    if in_block:
                        content_lines.append(line)
                text = "\n".join(content_lines)

            data = yaml.safe_load(text)
            if isinstance(data, dict) and "rules" in data:
                rules = data["rules"]
                if isinstance(rules, list):
                    return [r["rule"] if isinstance(r, dict) else str(r) for r in rules]
        except Exception as e:
            logger.warning(f"规则解析失败: {e}")

        return []

    def _save_rules(self, rules: list[str]) -> None:
        """保存规则到 yaml 文件"""
        RULES_PATH.parent.mkdir(parents=True, exist_ok=True)

        # 读取已有规则
        existing: list[str] = []
        if RULES_PATH.exists():
            try:
                data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "rules" in data:
                    existing = data["rules"]
            except Exception:
                pass

        # 去重合并 (简单去重: 比较前20字符)
        seen = {r[:20] for r in existing}
        new_rules = []
        for r in rules:
            if r[:20] not in seen:
                new_rules.append(r)
                seen.add(r[:20])

        all_rules = existing + new_rules

        # 最多保留 10 条
        if len(all_rules) > 10:
            all_rules = all_rules[-10:]

        RULES_PATH.write_text(
            yaml.dump({"rules": all_rules}, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        logger.info(f"规则已保存: {RULES_PATH} (共{len(all_rules)}条, 新增{len(new_rules)}条)")

    @staticmethod
    def load_rules() -> str:
        """加载持久化规则, 返回可注入 system prompt 的文本"""
        if not RULES_PATH.exists():
            return ""

        try:
            data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "rules" not in data:
                return ""

            rules = data["rules"]
            if not rules:
                return ""

            lines = ["\n## 已学习的战术规则"]
            for i, r in enumerate(rules, 1):
                lines.append(f"{i}. {r}")
            return "\n".join(lines)

        except Exception:
            return ""
