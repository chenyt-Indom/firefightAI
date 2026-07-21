"""Firefight AI 控制面板服务端 — Flask + SocketIO 实时数据推送

启动方式:
    python dashboard_server.py [--port 5000]
    
打开浏览器 http://localhost:5000 即可查看控制面板。
AI 系统自动上线，实时显示战场数据和自主学习过程。
"""

from __future__ import annotations

import os
import sys
import time
import json
import threading
import argparse
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from flask import Flask, render_template_string, request, send_from_directory
from flask_socketio import SocketIO, emit
from loguru import logger

# ── 初始化 Flask + SocketIO ──
app = Flask(__name__)
app.config["SECRET_KEY"] = "firefight_dashboard"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── 全局状态 ──
_dashboard_state: dict = {
    "running": False,
    "cycle": 0,
    "allies": 0,
    "enemies": 0,
    "score": 0,
    "total_score": 0,
    "last_decision": "",
    "last_action": "",
    "last_reason": "",
    "cycle_time_ms": 0,
    "avg_cycle_time_ms": 0,
    "decisions": [],
    "experience_count": 0,
    "rules_count": 0,
    "status": "就绪",
    "game_session": "",
    "scores_history": [],
    "user_commands": [],  # 用户下达的指令历史
}
_lock = threading.Lock()
_controller: object = None
_user_instruction: str = ""  # 当前用户指令, 会被注入到下一轮LLM


def update_state(**kwargs):
    """线程安全更新面板状态"""
    with _lock:
        _dashboard_state.update(kwargs)


def get_state() -> dict:
    with _lock:
        return dict(_dashboard_state)


# ── 加载配置 ──
def load_config() -> dict:
    path = Path(__file__).parent / "config" / "settings.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── AI 线程 ──
def _run_ai_loop():
    """在独立线程中运行 AI 控制器"""
    global _controller

    update_state(status="初始化组件...")

    # 导入并构建组件
    from src.execution.adb_utils import ADBUtils
    from src.execution.mumu_manager import MuMuManagerTouch
    from src.screen.capture import ScreenCapture
    from src.vision.detector import UnitDetector
    from src.vision.ocr_reader import UIReader
    from src.state.manager import StateManager
    from src.decision.commander import TacticalCommander
    from src.decision.parser import CommandParser
    from src.execution.executor import CommandExecutor
    from src.controller.game_controller import GameController
    from src.learning.battle_memory import BattleMemory
    from src.learning.outcome_eval import OutcomeEvaluator
    from src.learning.memory_retriever import MemoryRetriever
    from src.learning.strategy_compressor import StrategyCompressor
    from src.utils.logger import setup_logger

    cfg = load_config()
    game_cfg = cfg["game"]
    device_cfg = cfg["device"]
    llm_cfg = cfg["llm"]
    loop_cfg = cfg["game_loop"]
    yolo_cfg = cfg["yolo"]
    ocr_cfg = cfg["ocr"]
    team_cfg = cfg["team_detection"]
    scrcpy_cfg = cfg["scrcpy"]
    learn_cfg = cfg.get("learning", {})
    screen_size = (game_cfg["screen_width"], game_cfg["screen_height"])

    # ADB
    active_device = device_cfg.get("active", "mumu")
    device_info = device_cfg.get(active_device, {})
    adb = ADBUtils(
        host=device_info.get("adb_host", "127.0.0.1"),
        port=device_info.get("adb_port", 7555),
        connect_timeout=device_cfg["adb_connect_timeout"],
        command_timeout=device_cfg["adb_command_timeout"],
        retry_count=device_cfg["adb_retry_count"],
    )

    if not adb.ensure_connected():
        update_state(status="❌ ADB连接失败")
        return

    update_state(status="ADB已连接, 加载模型...")

    # 屏幕捕获
    capture = ScreenCapture(adb=adb, max_fps=scrcpy_cfg["max_fps"],
                            bitrate=scrcpy_cfg["bitrate"],
                            max_width=scrcpy_cfg["max_width"],
                            max_height=scrcpy_cfg["max_height"],
                            timeout=scrcpy_cfg["timeout"])

    # YOLO
    detector = UnitDetector(
        model_path=yolo_cfg["model_path"],
        fallback_model_path=yolo_cfg["fallback_model_path"],
        confidence_threshold=yolo_cfg["confidence_threshold"],
        iou_threshold=yolo_cfg["iou_threshold"],
        image_size=yolo_cfg["image_size"],
        device=yolo_cfg["device"],
    )
    detector.load_model()

    ocr = UIReader()
    ocr.load_model()

    state_manager = StateManager(screen_size=screen_size)

    commander = TacticalCommander(
        provider=llm_cfg["provider"], model=llm_cfg["model"],
        api_key=llm_cfg["api_key"], api_base=llm_cfg["api_base"],
        temperature=llm_cfg["temperature"], max_tokens=llm_cfg["max_tokens"],
        timeout=llm_cfg["timeout"], retry_count=llm_cfg["retry_count"],
        fallback_provider=llm_cfg["fallback_provider"],
        fallback_model=llm_cfg["fallback_model"],
        fallback_api_key=llm_cfg["fallback_api_key"],
        fallback_api_base=llm_cfg["fallback_api_base"],
    )
    commander.load_prompts()

    parser = CommandParser(screen_size=screen_size)
    game_session = str(int(time.time()))

    # 学习系统
    battle_memory = BattleMemory() if learn_cfg.get("enabled", True) else None
    outcome_eval = OutcomeEvaluator() if learn_cfg.get("enabled", True) else None
    memory_retriever = MemoryRetriever(battle_memory) if battle_memory else None
    strategy_compressor = (
        StrategyCompressor(battle_memory=battle_memory, api_key=llm_cfg["api_key"],
                           api_base=llm_cfg["api_base"], model=llm_cfg["model"])
        if battle_memory else None
    )

    # MuMuManager
    mumu_cfg = cfg.get("mumu_manager", {})
    touch = MuMuManagerTouch(
        exe_path=mumu_cfg.get("exe_path", r"D:\MuMuPlayer\nx_main\MuMuManager.exe"),
        verbosity=mumu_cfg.get("verbosity", 0),
        timeout=mumu_cfg.get("timeout", 5.0),
    )

    pause_x = int(loop_cfg["pause_button_x"] * screen_size[0])
    pause_y = int(loop_cfg["pause_button_y"] * screen_size[1])
    executor = CommandExecutor(adb=adb, screen_size=screen_size, touch=touch if touch.is_connected else None,
                               pause_button=(pause_x, pause_y))

    capture.start()

    # ── 创建控制器，注入事件回调 ──
    controller = DashboardGameController(
        adb=adb, capture=capture, detector=detector,
        state_manager=state_manager, commander=commander,
        parser=parser, executor=executor,
        max_cycles=loop_cfg["max_cycles"],
        game_over_timeout=loop_cfg["game_over_timeout"],
        battle_memory=battle_memory, outcome_eval=outcome_eval,
        memory_retriever=memory_retriever,
        strategy_compressor=strategy_compressor,
        game_session=game_session,
        event_callback=_on_cycle_event,
    )
    _controller = controller

    update_state(game_session=game_session, status="⚔️ 战斗中...")
    time.sleep(1)

    try:
        result = controller.run()
        if result:
            update_state(status="🎉 胜利!")
        else:
            update_state(status="游戏结束")
    except Exception as e:
        logger.exception(f"AI异常: {e}")
        update_state(status=f"❌ {str(e)[:60]}")
    finally:
        update_state(running=False)
        capture.stop()


def _on_cycle_event(event: dict):
    """每轮 AI 决策后回调"""
    cycle = event.get("cycle", 0)
    allies = event.get("allies", 0)
    enemies = event.get("enemies", 0)
    score = event.get("score", 0)
    decision = event.get("decision", "")
    action = event.get("action", "")
    cycle_time = event.get("cycle_time", 0)

    # 从 LLM 响应中提取完整的决策和理由
    full = _last_full_decision
    analysis = full.get("analysis", decision)
    prediction = full.get("next_prediction", "")
    commands_detail = full.get("commands", [])
    reason_text = ""
    actions_text = []

    for c in commands_detail:
        a = c.get("action", "?")
        ids = c.get("unit_ids", [])
        tgt = c.get("target", None)
        r = c.get("reason", "")

        if a in ("select",) and ids:
            actions_text.append(f"select({','.join(str(i) for i in ids[:5])})")
        elif a in ("move", "attack") and ids and tgt:
            actions_text.append(f"{a}({ids[0]}→{tgt[0]:.2f},{tgt[1]:.2f})")
        elif a == "zoom_in":
            actions_text.append("🔍放大")
        elif a == "zoom_out":
            actions_text.append("🔎缩小")
        if r:
            reason_text += f"[{a}] {r}; "

    if not reason_text:
        reason_text = decision
    if not actions_text:
        actions_text = [action] if action else ["无行动"]

    action_display = " + ".join(actions_text)
    reason_display = reason_text.rstrip("; ")

    # 计算分数变化
    old_score = get_state().get("total_score", 0)
    new_total = old_score + score

    # 读取学习数据
    exp_count = 0
    rules_count = 0
    try:
        from src.learning.strategy_compressor import StrategyCompressor
        rules = StrategyCompressor.load_rules()
        rules_count = len(rules.get("rules", [])) if rules else 0
    except Exception:
        pass
    try:
        from src.learning.battle_memory import BattleMemory
        bm = BattleMemory()
        exp_count = bm.count()
    except Exception:
        pass

    # 累计平均时间
    state = get_state()
    old_avg = state.get("avg_cycle_time_ms", 0)
    new_avg = old_avg + (cycle_time - old_avg) / max(cycle, 1)

    # 分数历史
    scores_history = state.get("scores_history", [])[-49:]
    scores_history.append({"cycle": cycle, "score": score, "total": new_total})

    # 决策历史
    decisions = state.get("decisions", [])[-19:]
    decisions.append({
        "cycle": cycle, "action": action_display,
        "decision": analysis, "reason": reason_display,
        "prediction": prediction,
        "allies": allies, "enemies": enemies, "score": score,
    })

    update_state(
        cycle=cycle, allies=allies, enemies=enemies,
        score=score, total_score=new_total,
        last_decision=analysis, last_action=action_display,
        last_reason=reason_display,
        cycle_time_ms=cycle_time, avg_cycle_time_ms=round(new_avg),
        decisions=decisions, scores_history=scores_history,
        experience_count=exp_count, rules_count=rules_count,
        status=f"⚔️ 第{cycle}轮 ({allies}vs{enemies})",
    )

    # WebSocket 推送
    socketio.emit("cycle_update", get_state())


# ── GameController 子类 (添加事件回调) ──
class DashboardGameController:
    """包装原有的 GameController，注入回调"""

    def __new__(cls, event_callback=None, **kwargs):
        from src.controller.game_controller import GameController
        instance = GameController.__new__(GameController)
        GameController.__init__(instance, **kwargs)
        instance._dashboard_callback = event_callback
        return instance


# Patch GameController._record_cycle to emit events
import src.controller.game_controller as gc_mod

_original_record = gc_mod.GameController._record_cycle


def _patched_record(self, state, outcome, commands):
    _original_record(self, state, outcome, commands)

    cb = getattr(self, "_dashboard_callback", None)
    if cb is None:
        return

    decision = "无决策"
    action = "无行动"
    if commands:
        for c in commands:
            if c.action:
                decision = c.reason or "无理由"
                action = f"{c.action.value}({','.join(str(u) for u in (c.unit_ids or []))})"
                break

    cb({
        "cycle": self._cycle_count,
        "allies": state.ally_count,
        "enemies": state.enemy_count,
        "score": outcome.get("score", 0) if outcome else 0,
        "decision": decision,
        "action": action,
        "cycle_time": int((time.time() - getattr(self, '_cycle_start', time.time())) * 1000),
    })


gc_mod.GameController._record_cycle = _patched_record


# 再 patch run 方法记录每轮开始时间
_original_run = gc_mod.GameController.run


def _patched_run(self):
    self._cycle_start = 0
    return _original_run(self)


gc_mod.GameController.run = _patched_run


# 在每轮开始时更新时间戳
_original_fast_execute = gc_mod.GameController._fast_execute


def _patched_fast_execute(self, commands, state):
    self._cycle_start = time.time()
    return _original_fast_execute(self, commands, state)


gc_mod.GameController._fast_execute = _patched_fast_execute


# ── Patch Commander to inject user instruction ──
import src.decision.commander as cmd_mod

_original_build = cmd_mod.TacticalCommander._build_user_message


def _patched_build_message(self, state_text):
    """注入用户指令到 LLM 上下文"""
    global _user_instruction
    message = _original_build(self, state_text)

    if _user_instruction:
        # 找到 "请根据以上战场状态" 之前插入
        marker = "请根据以上战场状态"
        if marker in message:
            parts = message.split(marker, 1)
            message = (
                f"{parts[0]}"
                f"\n---\n"
                f"## ⚡ 指挥官指令 (你必须执行!)\n"
                f"{_user_instruction}\n"
                f"\n---\n"
                f"{marker}{parts[1]}"
            )
        else:
            message += f"\n\n⚠️ 指挥官最新指令: {_user_instruction}"
        _user_instruction = ""  # 只用一次
    return message


cmd_mod.TacticalCommander._build_user_message = _patched_build_message


# ── Patch Commander to capture full decision details ──
_original_decide = cmd_mod.TacticalCommander.decide


def _patched_decide(self, state):
    result = _original_decide(self, state)
    # 记录完整决策内容供面板使用
    if result:
        try:
            data = json.loads(result)
            global _last_full_decision
            _last_full_decision = {
                "analysis": data.get("analysis", ""),
                "next_prediction": data.get("next_prediction", ""),
                "commands": [
                    {
                        "action": c.get("action", "?"),
                        "unit_ids": c.get("unit_ids", []),
                        "target": c.get("target", None),
                        "reason": c.get("reason", ""),
                    }
                    for c in data.get("commands", [])
                ],
            }
        except Exception:
            pass
    return result


cmd_mod.TacticalCommander.decide = _patched_decide
_last_full_decision: dict = {}


# ── Flask 路由 ──
@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/stats")
def api_stats():
    return get_state()


@socketio.on("connect")
def on_connect():
    emit("cycle_update", get_state())


@socketio.on("start")
def on_start():
    if not get_state().get("running"):
        update_state(running=True, status="⚔️ 战斗中...")
        t = threading.Thread(target=_run_ai_loop, daemon=True)
        t.start()
        emit("started", {"status": "ok"})


@socketio.on("stop")
def on_stop():
    ctrl = _controller
    if ctrl:
        ctrl.stop()
    update_state(running=False, status="已停止")
    emit("stopped", {"status": "ok"})


@socketio.on("get_state")
def on_get_state():
    emit("cycle_update", get_state())


@socketio.on("send_command")
def on_send_command(data: dict):
    """接收用户指令 → AI分析反馈 → 注入决策 + 存储学习"""
    global _user_instruction
    cmd = data.get("command", "").strip()
    if not cmd:
        return
    _user_instruction = cmd

    # 获取当前战场状态
    state = get_state()
    cycle = state.get("cycle", 0)
    allies = state.get("allies", 0)
    enemies = state.get("enemies", 0)

    # 记录到历史
    commands = state.get("user_commands", [])[-19:]
    commands.append({
        "cycle": cycle, "command": cmd,
        "allies": allies, "enemies": enemies,
    })
    update_state(user_commands=commands)

    # ── AI 分析反馈 (后台线程, 不阻塞) ──
    def analyze_command():
        try:
            from openai import OpenAI
            import yaml
            from pathlib import Path
            cfg_path = Path(__file__).parent / "config" / "settings.yaml"
            with open(cfg_path) as f:
                llm_cfg = yaml.safe_load(f)["llm"]

            client = OpenAI(
                api_key=llm_cfg["api_key"],
                base_url=llm_cfg["api_base"],
            )

            sys_prompt = (
                "你是Firefight战术AI。指挥官给你下达了一条指令。"
                "请用1-2句话分析: 1)你对这条指令的见解 2)你将在下一轮如何运用它。"
                "结合当前战场状态(友{allies}vs敌{enemies})说明你的调整方案。"
                "格式: '见解: ... | 方案: ...'"
                f"当前兵力: 友{allies}vs敌{enemies} (第{cycle}轮)"
            )

            resp = client.chat.completions.create(
                model=llm_cfg.get("model", "deepseek-chat"),
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": f"指挥官指令: {cmd}"},
                ],
                max_tokens=150,
                temperature=0.5,
                timeout=5,
            )
            analysis = resp.choices[0].message.content.strip()

            # 推送到控制面板
            socketio.emit("command_analysis", {
                "command": cmd,
                "cycle": cycle,
                "analysis": analysis,
                "allies": allies,
                "enemies": enemies,
            })

            # 存入学习系统
            try:
                from src.learning.battle_memory import BattleMemory
                bm = BattleMemory()
                bm.record(
                    state_hash=f"cmd_{int(time.time())}",
                    ally_count=allies,
                    enemy_count=enemies,
                    ally_positions=[],
                    decision={
                        "action": "user_command",
                        "reason": f"指挥官: {cmd} | 分析: {analysis}",
                        "target": [],
                    },
                    outcome_score=10,
                    cycle_num=cycle,
                    game_session=state.get("game_session", ""),
                )
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"指令分析失败: {e}")
            socketio.emit("command_analysis", {
                "command": cmd,
                "cycle": cycle,
                "analysis": f"(分析暂时不可用: {str(e)[:50]})",
                "allies": allies,
                "enemies": enemies,
            })

    t = threading.Thread(target=analyze_command, daemon=True)
    t.start()

    # 先推送确认
    emit("command_recorded", {
        "command": cmd,
        "cycle": cycle,
    })
    logger.info(f"📝 用户指令(分析中): {cmd}")


# ── Dashboard HTML ──
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Firefight AI 控制面板</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0e14; color: #d0d0d0; min-height: 100vh; }
    .header { background: #11151c; border-bottom: 1px solid #252a33; padding: 14px 24px; display: flex; justify-content: space-between; align-items: center; }
    .header h1 { font-size: 20px; font-weight: 600; color: #58a5f3; }
    .header .status { font-size: 14px; padding: 6px 14px; border-radius: 6px; background: #1a1f2b; }
    .header .status.running { color: #4caf50; }
    .header .status.stopped { color: #888; }
    .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
    .controls { display: flex; gap: 10px; margin-bottom: 20px; }
    .controls button { padding: 10px 28px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
    .btn-start { background: #4caf50; color: #000; }
    .btn-start:hover { background: #66bb6a; }
    .btn-stop { background: #e53935; color: #fff; }
    .btn-stop:hover { background: #f44336; }
    .cmd-input-wrapper { display: flex; gap: 8px; flex: 1; max-width: 500px; margin-left: auto; }
    .cmd-input-wrapper input { flex: 1; padding: 10px 14px; border: 1px solid #252a33; border-radius: 8px; background: #1a1f2b; color: #d0d0d0; font-size: 13px; outline: none; }
    .cmd-input-wrapper input:focus { border-color: #58a5f3; }
    .btn-send { background: #58a5f3; color: #000; padding: 10px 16px; border: none; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; }
    .btn-send:hover { background: #7ab8f5; }
    .btn-pause { background: #ff9800; color: #000; }
    .btn-pause:hover { background: #ffb74d; }
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 20px; }
    .stat-card { background: #11151c; border: 1px solid #252a33; border-radius: 10px; padding: 16px 18px; }
    .stat-card .label { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
    .stat-card .value { font-size: 28px; font-weight: 700; }
    .stat-card .value.blue { color: #58a5f3; }
    .stat-card .value.red { color: #e53935; }
    .stat-card .value.green { color: #4caf50; }
    .stat-card .value.yellow { color: #ff9800; }
    .main-grid { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }
    .panel { background: #11151c; border: 1px solid #252a33; border-radius: 10px; padding: 16px; }
    .panel h3 { font-size: 14px; font-weight: 600; color: #aaa; margin-bottom: 12px; border-bottom: 1px solid #252a33; padding-bottom: 8px; }
    .chart-container { height: 300px; position: relative; }
    .log-list { max-height: 300px; overflow-y: auto; font-size: 12px; }
    .log-item { padding: 8px 8px; border-bottom: 1px solid #1a1f2b; }
    .log-item .header { display: flex; gap: 6px; align-items: center; margin-bottom: 3px; background: none; border: none; padding: 0; }
    .log-item .cyc { color: #888; min-width: 30px; font-size: 11px; }
    .log-item .act { color: #58a5f3; font-weight: 600; font-size: 12px; flex: 1; }
    .log-item .sco { min-width: 45px; text-align: right; font-size: 12px; font-weight: 600; }
    .log-item .sco.pos { color: #4caf50; }
    .log-item .sco.neg { color: #e53935; }
    .log-item .reason { font-size: 11px; color: #999; padding-left: 36px; }
    .cmd-item { background: #1a2530; border-left: 2px solid #ff9800; }
    .full-width { grid-column: 1 / -1; }
    .exp-bar { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
    .exp-bar .bar-bg { flex: 1; height: 6px; background: #252a33; border-radius: 3px; overflow: hidden; }
    .exp-bar .bar-fill { height: 100%; background: #58a5f3; border-radius: 3px; transition: width 0.5s; }
    .exp-bar span { font-size: 13px; color: #aaa; }
  </style>
</head>
<body>
  <div class="header">
    <h1>⚔️ Firefight AI 控制面板</h1>
    <div class="status stopped" id="status-badge">● 已停止</div>
  </div>
  <div class="container">
    <div class="controls">
      <button class="btn-start" onclick="startAI()">▶ 上线 AI</button>
      <button class="btn-stop" onclick="stopAI()">■ 停止</button>
      <div class="cmd-input-wrapper">
        <input type="text" id="cmd-input" placeholder="💬 输入战术指令, AI将在下一轮执行…" onkeydown="if(event.key==='Enter')sendCommand()">
        <button class="btn-send" onclick="sendCommand()">发送</button>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card"><div class="label">当前轮次</div><div class="value blue" id="cycle">0</div></div>
      <div class="stat-card"><div class="label">友军</div><div class="value blue" id="allies">0</div></div>
      <div class="stat-card"><div class="label">敌军</div><div class="value red" id="enemies">0</div></div>
      <div class="stat-card"><div class="label">本轮评分</div><div class="value yellow" id="score">0</div></div>
      <div class="stat-card"><div class="label">总得分</div><div class="value green" id="total-score">0</div></div>
      <div class="stat-card"><div class="label">平均耗时</div><div class="value" id="avg-time" style="color:#aaa">0ms</div></div>
      <div class="stat-card"><div class="label">经验库</div><div class="value yellow" id="exp-count">0</div></div>
      <div class="stat-card"><div class="label">战术规则</div><div class="value blue" id="rules-count">0</div></div>
    </div>

    <div class="main-grid">
      <div class="panel full-width">
        <h3>📊 分数趋势</h3>
        <div class="chart-container"><canvas id="scoreChart"></canvas></div>
      </div>
      <div class="panel">
        <h3>🧠 决策日志</h3>
        <div class="log-list" id="decision-log"></div>
      </div>
      <div class="panel">
        <h3>📈 学习进度</h3>
        <div id="learning-info">
          <div style="margin-bottom:12px; font-size:13px;">
            <span style="color:#888">当前状态:</span>
            <span id="battle-status" style="color:#58a5f3;">待上线</span>
          </div>
          <div class="exp-bar">
            <span>经验:</span>
            <div class="bar-bg"><div class="bar-fill" id="exp-bar-fill" style="width:0%"></div></div>
            <span id="exp-label">0条</span>
          </div>
          <div style="margin-top:10px; font-size:12px; color:#888;" id="learning-detail"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const socket = io();

    let scoreChart = new Chart(document.getElementById('scoreChart'), {
      type: 'line',
      data: { labels: [], datasets: [
        { label: '本轮评分', data: [], borderColor: '#ff9800', backgroundColor: 'rgba(255,152,0,0.1)', tension: 0.3, pointRadius: 2 },
        { label: '累计得分', data: [], borderColor: '#4caf50', backgroundColor: 'rgba(76,175,80,0.1)', tension: 0.3, pointRadius: 2, yAxisID: 'y1' }
      ]},
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#888', font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#1a1f2b' } },
          y: { ticks: { color: '#555' }, grid: { color: '#1a1f2b' } },
          y1: { position: 'right', ticks: { color: '#555' }, grid: { display: false } }
        },
        interaction: { intersect: false, mode: 'index' }
      }
    });

    let scoreData = { labels: [], scores: [], totals: [] };

    function startAI() {
      socket.emit('start');
      document.getElementById('status-badge').className = 'status running';
      document.getElementById('status-badge').textContent = '● 连接中...';
    }

    function stopAI() {
      socket.emit('stop');
      document.getElementById('status-badge').className = 'status stopped';
      document.getElementById('status-badge').textContent = '● 已停止';
    }

    function escapeHtml(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function sendCommand() {
      var inp = document.getElementById('cmd-input');
      var cmd = inp.value.trim();
      if (!cmd) return;
      socket.emit('send_command', { command: cmd });
      inp.value = '';
      inp.placeholder = '指令已发送, AI将在下一轮执行...';
      setTimeout(function() { inp.placeholder = '💬 输入战术指令, AI将在下一轮执行…'; }, 2000);
    }

    socket.on('connect', function() { startAI(); });

    socket.on('command_recorded', function(data) {
      var log = document.getElementById('decision-log');
      var html = '<div class="log-item cmd-item">' +
        '<div class="header"><span class="cyc">📝</span>' +
        '<span class="act" style="color:#ff9800">指挥官: ' + escapeHtml(data.command) + '</span></div></div>' +
        log.innerHTML;
      log.innerHTML = html;
    });

    socket.on('command_analysis', function(data) {
      var log = document.getElementById('decision-log');
      var html = '<div class="log-item cmd-item">' +
        '<div class="header"><span class="cyc">🤖</span>' +
        '<span class="act" style="color:#58a5f3">AI见解</span></div>' +
        '<div class="reason">' + escapeHtml(data.analysis || '') + '</div></div>' +
        log.innerHTML;
      log.innerHTML = html;

      var detail = document.getElementById('learning-detail');
      detail.textContent = '最新指令分析: ' + (data.analysis || '').substring(0, 60) + '...';
    });

    socket.on('cycle_update', function(data) {
      // 状态卡片
      document.getElementById('cycle').textContent = data.cycle;
      document.getElementById('allies').textContent = data.allies;
      document.getElementById('enemies').textContent = data.enemies;

      var sc = data.score || 0;
      var scEl = document.getElementById('score');
      scEl.textContent = (sc >= 0 ? '+' : '') + sc;
      scEl.className = 'value ' + (sc > 0 ? 'green' : sc < 0 ? 'red' : 'yellow');

      document.getElementById('total-score').textContent = data.total_score || 0;
      document.getElementById('avg-time').textContent = (data.avg_cycle_time_ms || 0) + 'ms';
      document.getElementById('exp-count').textContent = data.experience_count || 0;
      document.getElementById('rules-count').textContent = data.rules_count || 0;
      document.getElementById('battle-status').textContent = data.status || '战斗中';

      // 状态标签
      var badge = document.getElementById('status-badge');
      if (data.running || data.cycle > 0) {
        badge.className = 'status running';
        badge.textContent = '● ' + (data.status || '战斗中');
      }

      // 经验进度条
      var exp = data.experience_count || 0;
      var expPct = Math.min(100, exp / 10);  // 1000条=100%
      document.getElementById('exp-bar-fill').style.width = expPct + '%';
      document.getElementById('exp-label').textContent = exp + '条';
      document.getElementById('learning-detail').textContent =
        '规则数: ' + (data.rules_count || 0) + ' | 场次: ' + (data.game_session || '');

      // 分数图表
      if (data.scores_history) {
        scoreData.labels = data.scores_history.map(s => '#' + s.cycle);
        scoreData.scores = data.scores_history.map(s => s.score);
        scoreData.totals = data.scores_history.map(s => s.total);
        scoreChart.data.labels = scoreData.labels;
        scoreChart.data.datasets[0].data = scoreData.scores;
        scoreChart.data.datasets[1].data = scoreData.totals;
        scoreChart.update();
      }

      // 决策日志
      if (data.decisions) {
        var log = document.getElementById('decision-log');
        var html = '';
        for (var i = data.decisions.length - 1; i >= 0; i--) {
          var d = data.decisions[i];
          var scClass = d.score > 0 ? 'pos' : d.score < 0 ? 'neg' : '';
          var scStr = d.score > 0 ? '+' + d.score : '' + d.score;
          html += '<div class="log-item">' +
            '<div class="header">' +
              '<span class="cyc">#' + d.cycle + '</span>' +
              '<span class="act">' + escapeHtml(d.action || '-') + '</span>' +
              '<span class="sco ' + scClass + '">' + scStr + '</span>' +
            '</div>' +
            '<div class="reason">💡 ' + escapeHtml(d.reason || d.decision || '') + '</div>' +
            (d.prediction ? '<div class="reason" style="color:#ff9800">🔮 ' + escapeHtml(d.prediction) + '</div>' : '') +
            '</div>';
        }
        log.innerHTML = html;
      }

      // 决策简述
      if (data.last_decision) {
        document.getElementById('battle-status').textContent =
          '#' + data.cycle + ' ' + data.last_decision;
      }
    });

    socket.on('started', function() {
      document.getElementById('status-badge').className = 'status running';
      document.getElementById('status-badge').textContent = '● ⚔️ 战斗中';
    });

    socket.on('stopped', function() {
      document.getElementById('status-badge').className = 'status stopped';
      document.getElementById('status-badge').textContent = '● 已停止';
    });
  </script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Firefight AI 控制面板")
    parser.add_argument("--port", type=int, default=5000, help="HTTP端口 (默认5000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  🔥 Firefight AI 控制面板")
    print(f"  地址: http://localhost:{args.port}")
    print(f"  打开浏览器即可查看, AI 自动上线")
    print(f"{'='*60}\n")

    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
