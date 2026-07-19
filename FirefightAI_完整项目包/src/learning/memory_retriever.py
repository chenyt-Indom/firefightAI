"""记忆检索器 — 为 LLM 决策注入相似成功案例 (Few-shot Prompt)

工作流:
  1. 每轮 LLM 调用前, 用当前战场状态检索历史高分经验
  2. 将 Top-3 成功案例格式化为 few-shot 示例
  3. 注入到 LLM prompt 中 (追加在 system prompt 之后)

格式示例:
  [成功战例1] 状态: 15友vs8敌 → 决策: 移动A1到(0.5,0.6)攻击E3 → 得分: +25
  [成功战例2] 状态: 12友vs10敌 → 决策: 集火E1 → 得分: +30
"""

from __future__ import annotations

from .battle_memory import BattleMemory


class MemoryRetriever:
    """为 LLM 检索相关成功经验"""

    def __init__(self, battle_memory: BattleMemory):
        self.memory = battle_memory

    def retrieve(
        self,
        state_hash: str,
        ally_count: int,
        enemy_count: int,
        top_k: int = 3,
        min_score: float = 5.0,
    ) -> list[dict]:
        """检索相似状态下的成功经验

        Returns:
            [{"decision": dict, "outcome_score": float, "ally_count": int, "enemy_count": int}, ...]
        """
        return self.memory.retrieve_similar(
            state_hash=state_hash,
            ally_count=ally_count,
            top_k=top_k,
            min_score=min_score,
        )

    def format_as_few_shot(self, experiences: list[dict]) -> str:
        """将经验格式化为 few-shot prompt 片段

        格式紧凑, 不浪费 token:
          [成功战例] 友14vs敌10 → 移动A1到(0.62,0.68) → 得分+25
        """
        if not experiences:
            return ""

        lines = ["## 历史成功战例 (参考)"]
        for i, exp in enumerate(experiences, 1):
            decision = exp["decision"]
            action = decision.get("action", "?")
            target = decision.get("target", [])
            reason = decision.get("reason", "")
            score = exp.get("outcome_score", 0)
            ally = exp.get("ally_count", "?")
            enemy = exp.get("enemy_count", "?")

            # 构建紧凑描述
            parts = [f"{i}. 友{ally}vs敌{enemy}"]
            parts.append(f"→ {action}")

            if target and len(target) == 2:
                parts.append(f"目标({target[0]:.2f},{target[1]:.2f})")
            if reason:
                # 截断过长原因
                reason_short = reason[:30] + "..." if len(reason) > 30 else reason
                parts.append(f"({reason_short})")

            parts.append(f"得分+{score:.0f}")
            lines.append(" ".join(parts))

        return "\n".join(lines)

    def inject_into_prompt(self, prompt: str, state_hash: str, ally_count: int, enemy_count: int) -> str:
        """一站式方法: 检索经验 → 格式化 → 注入到 prompt 末尾

        Args:
            prompt: 原始 prompt (通常是用户消息)
            state_hash: 当前状态哈希
            ally_count: 当前友军数
            enemy_count: 当前敌军数

        Returns:
            注入经验后的 prompt
        """
        exps = self.retrieve(state_hash, ally_count, enemy_count)
        if not exps:
            return prompt

        few_shot = self.format_as_few_shot(exps)
        return prompt + "\n\n" + few_shot
