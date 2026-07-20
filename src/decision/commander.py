"""战术指挥官 - 调用DeepSeek LLM进行战术决策"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from openai import OpenAI

from src.state.models import GameState, LLMResponse, Command as CmdModel
from src.utils.logger import log_decision


class TacticalCommander:
    """LLM战术决策引擎,支持DeepSeek主模型和GLM-4备用"""

    def __init__(
        self,
        provider: str = "deepseek",
        model: str = "deepseek-chat",
        api_key: str = "",
        api_base: str = "https://api.deepseek.com/v1",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        timeout: int = 15,
        retry_count: int = 3,
        # 备用模型配置
        fallback_provider: str = "zhipu",
        fallback_model: str = "glm-4-flash",
        fallback_api_key: str = "",
        fallback_api_base: str = "https://open.bigmodel.cn/api/paas/v4",
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retry_count = retry_count

        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model
        self.fallback_api_key = fallback_api_key
        self.fallback_api_base = fallback_api_base

        self._client: Optional[OpenAI] = None
        self._fallback_client: Optional[OpenAI] = None
        self._system_prompt: str = ""
        self._few_shot_examples: str = ""
        self._tactics_rules: str = ""       # L2 提炼的持久化规则
        self._learned_examples: str = ""    # L1 当前轮注入的动态示例
        self._decision_count = 0
        self._total_decision_time = 0.0

    def load_prompts(self) -> None:
        """加载prompt模板"""
        prompts_dir = Path(__file__).parent / "prompts"

        system_path = prompts_dir / "system.txt"
        if system_path.exists():
            self._system_prompt = system_path.read_text(encoding="utf-8")
            logger.info(f"系统prompt加载成功: {len(self._system_prompt)}字符")
        else:
            logger.warning("系统prompt文件不存在,使用默认prompt")
            self._system_prompt = "你是一名经验丰富的现代战术指挥官。"

        few_shot_path = prompts_dir / "few_shot.txt"
        if few_shot_path.exists():
            self._few_shot_examples = few_shot_path.read_text(encoding="utf-8")
            logger.info(f"Few-shot示例加载成功: {len(self._few_shot_examples)}字符")
        else:
            self._few_shot_examples = ""

        # 加载学习到的模式 (从玩家操作中学到的战术)
        learned_path = prompts_dir / "few_shot_learned.txt"
        if learned_path.exists():
            learned_content = learned_path.read_text(encoding="utf-8")
            self._few_shot_examples += "\n\n---\n\n" + learned_content
            logger.info(f"学习到的模式加载成功: {len(learned_content)}字符")

        # 加载 L2 提炼的战术规则
        self._tactics_rules = ""
        self.reload_tactics_rules()

    def reload_tactics_rules(self) -> None:
        """重新加载 L2 提炼的战术规则 (策略提炼后调用)"""
        from src.learning.strategy_compressor import StrategyCompressor
        self._tactics_rules = StrategyCompressor.load_rules()
        if self._tactics_rules:
            logger.info(f"战术规则加载成功: {len(self._tactics_rules)}字符")

    def set_learned_examples(
        self,
        memory_retriever,
        state_hash: str,
        ally_count: int,
        enemy_count: int,
    ) -> None:
        """设置动态学习的 few-shot 示例 (每轮 LLM 调用前由 controller 调用)

        从经验库检索相似案例, 格式化为 few-shot, 注入到 prompt 中。
        """
        self._learned_examples = memory_retriever.format_as_few_shot(
            memory_retriever.retrieve(state_hash, ally_count, enemy_count)
        )

    def decide(self, game_state: GameState) -> Optional[LLMResponse]:
        """主决策入口: 调用LLM进行战术决策

        Args:
            game_state: 当前游戏状态

        Returns:
            LLMResponse或None(失败时)
        """
        # 尝试主模型
        result = self._call_llm(game_state, use_fallback=False)
        if result is not None:
            return result

        # 降级到备用模型
        logger.warning("主模型调用失败,降级到备用模型")
        result = self._call_llm(game_state, use_fallback=True)
        return result

    def _call_llm(self, game_state: GameState, use_fallback: bool = False) -> Optional[LLMResponse]:
        """调用LLM API - 使用requests直连(绕过openai库DLL问题)"""
        import requests as _req
        
        api_key = self.fallback_api_key if use_fallback else self.api_key
        # 🔥 如果主备provider相同，用主api_base
        if use_fallback and self.fallback_provider == self.provider:
            api_base = self.api_base
        else:
            api_base = self.fallback_api_base if use_fallback else self.api_base
        model = self.fallback_model if use_fallback else self.model
        provider_name = self.fallback_provider if use_fallback else self.provider
        
        if not api_key or "YOUR_" in api_key:
            logger.error(f"API Key未配置 ({provider_name})")
            return None
        
        state_text = game_state.to_llm_text()
        user_message = self._build_user_message(state_text)
        system_content = (self._system_prompt or "")[:3000]
        if self._tactics_rules:
            system_content += "\n" + self._tactics_rules[:500]
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message[:5000]},
            ],
            "max_tokens": self.max_tokens or 384,
            "temperature": self.temperature or 0.3,
        }
        
        for attempt in range(1, self.retry_count + 2):
            try:
                start_time = time.time()
                log_decision(f"[{provider_name}/{model}] 第{attempt}次请求, 友={game_state.ally_count} 敌={game_state.enemy_count}")
                
                resp = _req.post(
                    f"{api_base.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload, timeout=(8, self.timeout + 10)
                )
                
                if resp.status_code != 200:
                    logger.warning(f"LLM返回{resp.status_code}: {resp.text[:150]}")
                    continue
                
                elapsed = (time.time() - start_time) * 1000
                self._decision_count += 1
                self._total_decision_time += elapsed
                
                raw_text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if not raw_text:
                    logger.warning(f"LLM返回空内容 (第{attempt}次)")
                    continue
                
                log_decision(f"LLM响应 ({elapsed:.0f}ms): {raw_text[:200]}...")
                llm_response = self._parse_response(raw_text)
                if llm_response is not None:
                    log_decision(f"决策完成: {llm_response.analysis[:100]}... ({len(llm_response.commands)}条指令)")
                    return llm_response
                
                logger.warning(f"LLM输出格式错误 (第{attempt}次)")
                messages = payload["messages"]
                messages.append({"role": "user", "content": "请严格按照JSON schema格式输出"})
            except Exception as e:
                logger.error(f"LLM调用异常 (第{attempt}次): {e}")
        
        logger.error(f"LLM重试{self.retry_count}次后仍失败")
        return None

    def _build_user_message(self, state_text: str) -> str:
        """构建用户消息: 状态 + 静态few-shot + 动态学习示例"""
        parts = [state_text]

        if self._few_shot_examples:
            parts.append("\n---\n")
            parts.append("## 参考示例(Few-shot)")
            parts.append(self._few_shot_examples)

        # L1 动态注入: 本轮检索到的相似成功案例
        if self._learned_examples:
            parts.append("\n---\n")
            parts.append(self._learned_examples)

        parts.append("\n---\n")
        parts.append("请根据以上战场状态,输出你的战术决策(JSON格式)。")

        # 每轮调用后清空动态示例 (避免累积)
        self._learned_examples = ""

        return "\n".join(parts)

    def _parse_response(self, raw_text: str) -> Optional[LLMResponse]:
        """解析LLM响应为LLMResponse - 兼容多种输出格式"""
        try:
            text = raw_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[0].startswith("```"): lines = lines[1:]
                if lines and lines[-1].strip() == "```": lines = lines[:-1]
                text = "\n".join(lines)
            data = json.loads(text)
            
            # 🔥 兼容简化格式: 没有analysis/commands字段时自动映射
            if "analysis" not in data and "commands" not in data:
                tactic = data.get("tactic", data.get("decision", data.get("action", "hold")))
                reason = data.get("reason", data.get("分析", ""))
                
                from src.state.models import Command
                cmds = []
                tl = str(tactic).lower()
                if "advance" in tl or "前进" in tl or "进攻" in tl:
                    cmds.append(Command(action="move", unit_ids=[1], target=(0.55, 0.4), reason=f"{reason[:50]}"))
                elif "retreat" in tl or "后退" in tl or "撤退" in tl:
                    cmds.append(Command(action="move", unit_ids=[1], target=(0.5, 0.7), reason=f"{reason[:50]}"))
                elif "defend" in tl or "防守" in tl:
                    cmds.append(Command(action="move", unit_ids=[1], target=(0.5, 0.6), reason=f"{reason[:50]}"))
                else:
                    cmds.append(Command(action="select", unit_ids=[1], reason=f"{reason[:50]}"))
                data = {"analysis": reason[:200] or str(tactic), "commands": [c.model_dump() for c in cmds]}
            
            if "commands" not in data: data["commands"] = []
            if "analysis" not in data: data["analysis"] = "战术决策"
            return LLMResponse.model_validate(data)
        except Exception as e:
            logger.warning(f"解析失败: {e}")
            return None

    def _get_client(self, use_fallback: bool = False) -> Optional[OpenAI]:
        """获取OpenAI客户端"""
        if use_fallback:
            if self._fallback_client is None:
                if not self.fallback_api_key or self.fallback_api_key == "YOUR_GLM_API_KEY":
                    logger.error("备用模型API Key未配置")
                    return None
                self._fallback_client = OpenAI(
                    api_key=self.fallback_api_key,
                    base_url=self.fallback_api_base,
                    timeout=self.timeout,
                )
            return self._fallback_client
        else:
            if self._client is None:
                if not self.api_key or self.api_key == "YOUR_DEEPSEEK_API_KEY":
                    logger.error("主模型API Key未配置")
                    return None
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.api_base,
                    timeout=self.timeout,
                )
            return self._client

    @property
    def avg_decision_time(self) -> float:
        if self._decision_count == 0:
            return 0.0
        return self._total_decision_time / self._decision_count

    @property
    def decision_count(self) -> int:
        return self._decision_count