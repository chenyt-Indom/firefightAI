#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Firefight AI 云端控制面板
部署到腾讯云服务器，本地游戏控制器远程连接
"""

import os, sys, json, threading, time, logging
from pathlib import Path
from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit

# ── 配置 ──
PORT = int(os.environ.get("PORT", 5000))
SECRET_KEY = os.environ.get("SECRET_KEY", "firefight-ai-cloud")
PROJECT_ROOT = Path(__file__).parent.resolve()

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CloudPanel")

# ── 全局状态 ──
_lock = threading.Lock()
_state = {
    "running": False, "cycle": 0, "allies": 0, "enemies": 0,
    "score": 0, "total_score": 0, "last_decision": "", "last_action": "",
    "cycle_time_ms": 0, "avg_cycle_time_ms": 0, "decisions": [],
    "experience_count": 0, "rules_count": 0, "status": "等待本地连接...",
    "scores_history": [], "user_commands": [],
}

# ── HTTP 路由 ──
@app.route("/")
def index():
    return DASHBOARD_HTML

@app.route("/health")
def health():
    return {"status": "ok", "timestamp": time.time()}

# ── WebSocket ──
@socketio.on("connect")
def on_connect():
    logger.info(f"客户端连接: {request.sid}")
    emit("cycle_update", _get_state())

@socketio.on("disconnect")
def on_disconnect():
    logger.info(f"客户端断开: {request.sid}")

@socketio.on("start")
def on_start():
    with _lock: _state["status"] = "等待本地控制器..."
    emit("cycle_update", _get_state())
    logger.info("收到启动请求")

@socketio.on("stop")
def on_stop():
    with _lock: 
        _state["running"] = False
        _state["status"] = "已停止"
    emit("cycle_update", _get_state())

@socketio.on("get_state")
def on_get_state():
    emit("cycle_update", _get_state())

@socketio.on("cycle_report")
def on_cycle_report(data):
    """接收本地游戏控制器的周期报告"""
    with _lock:
        cycle = data.get("cycle", 0)
        _state.update({
            "running": True, "cycle": cycle,
            "allies": data.get("allies", 0), "enemies": data.get("enemies", 0),
            "score": data.get("score", 0),
            "last_decision": data.get("analysis", ""),
            "last_action": data.get("action", ""),
            "cycle_time_ms": data.get("cycle_time", 0),
            "status": f"⚔️ 第{cycle}轮 ({data.get('allies',0)}vs{data.get('enemies',0)})",
        })
        
        # 累计
        _state["total_score"] = _state.get("total_score", 0) + data.get("score", 0)
        
        # 平均时间
        old_avg = _state.get("avg_cycle_time_ms", 0)
        ct = data.get("cycle_time", 0)
        _state["avg_cycle_time_ms"] = round(old_avg + (ct - old_avg) / max(cycle, 1))
        
        # 决策历史
        decisions = _state.get("decisions", [])[-19:]
        decisions.append({
            "cycle": cycle,
            "action": data.get("action", ""),
            "decision": data.get("analysis", ""),
            "reason": data.get("reason", ""),
            "allies": data.get("allies", 0),
            "enemies": data.get("enemies", 0),
            "score": data.get("score", 0),
            "prediction": data.get("prediction", ""),
        })
        _state["decisions"] = decisions
        
        # 分数历史
        sh = _state.get("scores_history", [])[-49:]
        sh.append({"cycle": cycle, "score": data.get("score", 0), "total": _state["total_score"]})
        _state["scores_history"] = sh
    
    emit("cycle_update", _get_state(), broadcast=True)
    logger.debug(f"周期报告 #{cycle}: {data.get('allies',0)}vs{data.get('enemies',0)}")

@socketio.on("send_command")
def on_send_command(data):
    """用户指令→转发到本地控制器"""
    cmd = data.get("command", "").strip()
    if not cmd: return
    
    # 记录
    with _lock:
        cmds = _state.get("user_commands", [])[-19:]
        cmds.append({"cycle": _state.get("cycle", 0), "command": cmd})
        _state["user_commands"] = cmds
    
    # 广播给所有客户端(包括本地控制器)
    emit("user_command", {"command": cmd, "cycle": _state.get("cycle", 0)}, broadcast=True)
    logger.info(f"📝 用户指令: {cmd}")

def _get_state():
    with _lock: return dict(_state)

# ── 面板 HTML ──
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Firefight AI 云端面板</title>
<style>
*{margin:0;box-sizing:border-box}body{background:#0d1117;color:#e6edf3;font-family:Microsoft YaHei,Arial;padding:16px;max-width:900px;margin:0 auto}
h1{color:#58a5f3;font-size:20px;margin-bottom:4px}.sub{color:#666;font-size:12px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:16px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center}
.card .val{font-size:28px;font-weight:bold}.card .lbl{font-size:11px;color:#888;margin-top:4px}
.val.g{color:#3fb950}.val.r{color:#f85149}.val.b{color:#58a5f3}.val.y{color:#d29922}
.chart-container{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;margin-bottom:16px}
canvas{width:100%!important;max-height:250px}
.log-container{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;max-height:400px;overflow-y:auto}
.log-item{padding:8px 0;border-bottom:1px solid #1a1f2b;font-size:13px}
.log-item .header{display:flex;gap:8px;align-items:center;margin-bottom:4px}
.log-item .cyc{color:#666;min-width:35px}.log-item .act{color:#58a5f3;font-weight:600}
.log-item .sco{min-width:45px;text-align:right;font-weight:bold}
.log-item .sco.pos{color:#3fb950}.log-item .sco.neg{color:#f85149}
.log-item .reason{color:#aaa;font-size:12px}.log-item .pred{color:#d29922;font-size:12px;margin-top:2px}
.progress-bar{background:#30363d;border-radius:4px;height:6px;margin-top:6px;overflow:hidden}
.progress-fill{background:#58a5f3;height:100%;transition:width .3s}
.status{display:inline-block;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:bold}
.status.running{background:#3fb95022;color:#3fb950}.status.stopped{background:#f8514922;color:#f85149}
.input-area{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;margin-bottom:16px;display:flex;gap:8px}
.input-area input{flex:1;background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:8px 12px;border-radius:6px;font-size:14px;outline:none}
.input-area input:focus{border-color:#58a5f3}
.input-area button{background:#238636;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:14px;white-space:nowrap}
.input-area button:hover{background:#2ea043}
</style></head><body>
<h1>🔥 Firefight AI 云端控制面板</h1>
<div class="sub"><span class="status" id="status-badge">● 连接中...</span> <span id="status-text"></span></div>

<div class="grid" id="stats-grid">
  <div class="card"><div class="val b" id="val-cycle">0</div><div class="lbl">当前轮次</div></div>
  <div class="card"><div class="val g" id="val-allies">0</div><div class="lbl">友军</div></div>
  <div class="card"><div class="val r" id="val-enemies">0</div><div class="lbl">敌军</div></div>
  <div class="card"><div class="val y" id="val-score">0</div><div class="lbl">本轮评分</div></div>
  <div class="card"><div class="val b" id="val-total">0</div><div class="lbl">累计得分</div></div>
  <div class="card"><div class="val" id="val-time">0ms</div><div class="lbl">平均耗时</div></div>
</div>

<div class="chart-container">
  <canvas id="scoreChart"></canvas>
</div>

<div class="input-area">
  <input type="text" id="cmd-input" placeholder="输入战术指令... (如: 全部坦克向右侧包抄)" onkeydown="if(event.key==='Enter')sendCmd()">
  <button onclick="sendCmd()">📤 发送</button>
</div>

<div class="log-container" id="decision-log"><div style="color:#666;text-align:center">等待 AI 上线...</div></div>

<div id="learning-info" style="margin-top:12px;font-size:12px;color:#888;display:flex;gap:16px">
  <span>🧠 经验库: <b id="ln-exp">0</b> 条</span>
  <span>📋 战术规则: <b id="ln-rules">0</b> 条</span>
  <div class="progress-bar" style="flex:1;margin-top:6px"><div class="progress-fill" id="ln-progress" style="width:0%"></div></div>
</div>

<script src="https://cdn.socket.io/4.7.4/socket.io.min.js"></script>
<script>
const socket = io();
const STATUS = document.getElementById("status-badge");
const LOG = document.getElementById("decision-log");

socket.on("connect", () => { STATUS.className = "status running"; STATUS.textContent = "● 已连接"; });

let chart = null;
const ctx = document.getElementById("scoreChart").getContext("2d");

function initChart() {
  chart = new Chart(ctx, {
    type: "line",
    data: { labels: [], datasets: [
      { label: "本轮评分", data: [], borderColor: "#d29922", tension: 0.3, pointRadius: 0 },
      { label: "累计得分", data: [], borderColor: "#3fb950", tension: 0.3, pointRadius: 0 }
    ]},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#888", font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: "#666", maxTicksLimit: 10 } },
        y: { ticks: { color: "#666" } }
      }
    }
  });
}

function esc(s) { return (s||"").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

socket.on("cycle_update", function(d) {
  if (!chart) initChart();
  document.getElementById("val-cycle").textContent = d.cycle || 0;
  document.getElementById("val-allies").textContent = d.allies || 0;
  document.getElementById("val-enemies").textContent = d.enemies || 0;
  document.getElementById("val-score").textContent = (d.score>0?"+":"") + (d.score||0);
  document.getElementById("val-score").className = "val " + (d.score>0?"g":d.score<0?"r":"y");
  document.getElementById("val-total").textContent = d.total_score || 0;
  document.getElementById("val-time").textContent = (d.avg_cycle_time_ms||0) + "ms";
  document.getElementById("status-text").textContent = d.status || "";
  document.getElementById("ln-exp").textContent = d.experience_count || 0;
  document.getElementById("ln-rules").textContent = d.rules_count || 0;

  if (d.running) { STATUS.className = "status running"; STATUS.textContent = "● AI在线"; }
  else { STATUS.className = "status stopped"; STATUS.textContent = "● 已停止"; }

  // 图表
  var sh = d.scores_history || [];
  chart.data.labels = sh.map(function(x) { return "#" + x.cycle; });
  chart.data.datasets[0].data = sh.map(function(x) { return x.score; });
  chart.data.datasets[1].data = sh.map(function(x) { return x.total; });
  chart.update("none");

  // 日志
  if (d.decisions && d.decisions.length) {
    var h = "";
    for (var i = d.decisions.length - 1; i >= Math.max(0, d.decisions.length - 15); i--) {
      var de = d.decisions[i];
      var sc = de.score > 0 ? "pos" : de.score < 0 ? "neg" : "";
      var ss = de.score > 0 ? "+"+de.score : ""+de.score;
      h += '<div class="log-item"><div class="header">' +
        '<span class="cyc">#' + de.cycle + '</span>' +
        '<span class="act">' + esc(de.action || "-") + '</span>' +
        '<span class="sco ' + sc + '">' + ss + '</span></div>' +
        '<div class="reason">💡 ' + esc(de.reason || de.decision || "") + '</div>';
      if (de.prediction) h += '<div class="pred">🔮 ' + esc(de.prediction) + '</div>';
      h += '</div>';
    }
    LOG.innerHTML = h;
  }

  // 用户指令
  var ucmds = d.user_commands || [];
  for (var j = 0; j < ucmds.length; j++) {
    var u = ucmds[j];
    h = '<div class="log-item"><div class="header"><span class="cyc">📝</span>' +
      '<span class="act" style="color:#ff9800">指挥官: ' + esc(u.command) + '</span></div></div>' + h;
  }
});

function sendCmd() {
  var inp = document.getElementById("cmd-input");
  var cmd = inp.value.trim();
  if (!cmd) return;
  socket.emit("send_command", { command: cmd });
  inp.value = "";
}
</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</body></html>"""

if __name__ == "__main__":
    print(f"""
============================================================
  🔥 Firefight AI 云端控制面板
  地址: http://0.0.0.0:{PORT}
  等待本地游戏控制器连接...
============================================================
""")
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)
