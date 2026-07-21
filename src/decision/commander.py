"""战术指挥官 - 调用DeepSeek LLM进行战术决策"""
from __future__ import annotations

import json, logging
_log = logging.getLogger("commander")
_log.warning("===== COMMANDER v4 REQUESTS直连模式已加载 =====")
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
        """主决策入口"""

        # 尝试主模型
        result = self._call_llm(game_state, use_fallback=False)
        if result is not None:
            return result

        # 降级
        result = self._call_llm(game_state, use_fallback=True)
        return result

    def _call_llm(self, game_state: GameState, use_fallback: bool = False) -> Optional[LLMResponse]:
        """LLM调用 - 双路冗余: DeepSeek主+Zhipu备"""
        import requests as _req
        
        tag = "FALLBACK" if use_fallback else "PRIMARY"
        api_key = self.fallback_api_key if use_fallback else self.api_key
        api_base = self.fallback_api_base if use_fallback else self.api_base
        model = self.fallback_model if use_fallback else self.model
        
        _log.warning(f"[{tag}] START: provider={self.fallback_provider if use_fallback else self.provider} model={model} key={'YES' if api_key and 'YOUR_' not in api_key else 'NO'} base={api_base[:40]}")
        
        if not api_key or "YOUR_" in api_key:
            _log.error(f"[{tag}] API Key未配置")
            return None
        
        try:
            state_text = game_state.to_llm_text()
            user_message = self._build_user_message(state_text)
            sp = (self._system_prompt or "")[:2000]
            if self._tactics_rules:
                sp += "\n" + self._tactics_rules[:300]
            
            body = {"model": model, "messages": [
                {"role": "system", "content": sp},
                {"role": "user", "content": user_message[:4000]}
            ], "max_tokens": self.max_tokens or 384, "temperature": self.temperature or 0.3}
            
            for at in range(1):  # 主备各1次,不重试
                try:
                    t0 = time.time()
                    resp = _req.post(f"{api_base.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=body, timeout=(10, self.timeout + 15))
                    dt = (time.time() - t0) * 1000
                    
                    if resp.status_code == 200:
                        raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                        if not raw:
                            _log.warning(f"[{tag}] attempt{at+1}: 空响应")
                            continue
                        parsed = self._parse_response(raw)
                        if parsed:
                            _log.warning(f"[{tag}] OK attempt{at+1} {dt:.0f}ms: {parsed.analysis[:60]}")
                            return parsed
                        _log.warning(f"[{tag}] attempt{at+1}: 解析失败 raw={raw[:100]}")
                        body["messages"].append({"role": "user", "content": "输出JSON格式"})
                    else:
                        _log.warning(f"[{tag}] attempt{at+1}: HTTP{resp.status_code} {resp.text[:80]}")
                except Exception as e:
                    _log.error(f"[{tag}] attempt{at+1}: {e}")
        
        except Exception as e:
            _log.error(f"[{tag}] 构建消息失败: {e}")
        
        _log.error(f"[{tag}] 3次尝试后失败")
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
        """解析LLM响应 - 自动修复截断JSON"""
        try:
            text = raw_text.strip()
            # 去掉markdown代码块
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[0].startswith("```"): lines = lines[1:]
                if lines and lines[-1].strip() == "```": lines = lines[:-1]
                text = "\n".join(lines)
            
            # 🔥 尝试修复截断的JSON (常见于LLM输出被截断)
            data = None
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # 修复1: 补全缺失的引号
                fixed = text
                if fixed.count('"') % 2 != 0:
                    fixed = fixed.rstrip() + '"'
                    # 补全可能缺失的括号
                    open_braces = fixed.count('{') - fixed.count('}')
                    open_brackets = fixed.count('[') - fixed.count(']')
                    fixed += ']' * open_brackets + '}' * open_braces
                try:
                    data = json.loads(fixed)
                except:
                    # 修复2: 找最后一个完整的{...}
                    last_brace = text.rfind('}')
                    if last_brace > 0:
                        text = text[:last_brace+1]
                        if text.count('{') > text.count('}'): text += '}'
                        if text.count('[') > text.count(']'): text += ']'
                        try: data = json.loads(text)
                        except: pass
            
            if data is None:
                logger.warning(f"JSON无法修复, 原始: {text[:100]}")
                return None
            
            # 🔥 兼容简化格式: 没有analysis/commands字段时自动映射
            if "analysis" not in data and "commands" not in data:
                tactic = data.get("tactic", data.get("decision", data.get("action", "hold")))
                reason = data.get("reason", data.get("分析", ""))
                
                from src.state.models import Command
                cmds = []
                tl = str(tactic).lower()
                # 全选所有友军
                all_ids = [1,2,3,4,5,6,7,8]  # 最多8个
                if "advance" in tl or "前进" in tl or "进攻" in tl or "向前" in tl:
                    cmds.append(Command(action="move", unit_ids=all_ids, target=(0.55, 0.35), reason=f"推进: {reason[:50]}"))
                elif "retreat" in tl or "后退" in tl or "撤退" in tl or "后撤" in tl:
                    cmds.append(Command(action="move", unit_ids=all_ids, target=(0.5, 0.75), reason=f"撤退: {reason[:50]}"))
                elif "defend" in tl or "防守" in tl or "固守" in tl or "hold" in tl:
                    cmds.append(Command(action="move", unit_ids=all_ids, target=(0.5, 0.55), reason=f"防守: {reason[:50]}"))
                elif "attack" in tl or "攻击" in tl or "开火" in tl:
                    cmds.append(Command(action="attack", unit_ids=all_ids, target_enemy_id=101, reason=f"攻击: {reason[:50]}"))
                else:
                    cmds.append(Command(action="move", unit_ids=all_ids, target=(0.5, 0.45), reason=f"移动: {reason[:50]}"))
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