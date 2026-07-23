# 🔥 Firefight AI 指挥系统

> 基于视觉大模型 + 大语言模型的实时战术 AI 指挥系统。截图→看图→决策→执行，端到端自动化。

---

## 这是什么？

一个能**自己玩即时战略游戏**的 AI。给定游戏画面，它：

1. 通过 ADB 截取模拟器画面
2. GLM-4V-Plus 视觉大模型看懂战场（敌方在哪、友军在哪、地形如何）
3. DeepSeek 大语言模型做出战术决策（包抄、夺旗、集火）
4. 通过 ADB 触控执行指令（点击选中、拖拽移动、缩放地图）
5. 每轮评估效果，从经验中学习，越打越聪明

---

## 架构总览

```
┌─────────────────────────────────────────────────────┐
│                    Web 控制面板                        │
│          (Flask + SocketIO，实时战场监控)              │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                 主循环 (GameController)               │
│                                                       │
│  截图(PIL/ADB) → 视觉理解(GLM-4V-Plus) → 战术决策    │
│  (DeepSeek) → 指令解析 → ADB执行 → 效果评估 → 学习   │
│                                                       │
│  目标：≤3秒/轮                                         │
└──────────────────────┬──────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐  ┌──────────┐  ┌──────────┐
   │ 视觉模块 │  │ 决策模块  │  │ 学习系统  │
   │ GLM-4V  │  │ DeepSeek │  │ L1+L2    │
   └─────────┘  └──────────┘  └──────────┘
```

---

## 核心亮点

| 模块 | 技术方案 | 说明 |
|------|------|------|
| 视觉理解 | GLM-4V-Plus | 多模态大模型直接看图，零训练 |
| 战术决策 | DeepSeek + Few-shot + 知识库 | 系统提示词 + 检索相似案例注入 |
| 指令执行 | ADB + MuMuManager | 批量 tap 选中 + 双击中圈拖拽移动 |
| 自学习 L1 | SQLite 经验库 + 延迟评估 | 每轮记录决策效果，下轮检索注入 |
| 自学习 L2 | 策略压缩器 | 每15轮提炼战术规则，热更新 prompt |
| 远程部署 | 腾讯云 + Flask + SocketIO | Web 控制面板，外网可访问 |
| 容灾设计 | 主备双模型 + LLM缓存 | DeepSeek 主 / GLM-4 备，状态不变复用决策 |

---

## 项目结构

```
firefightAI/
├── main.py                      # 入口：CLI 命令行
├── dashboard_server.py          # Web 控制面板服务 (Flask + SocketIO)
├── src/
│   ├── controller/              # 主循环控制器
│   │   └── game_controller.py   # 截图→检测→决策→执行→学习
│   ├── decision/                # 决策模块
│   │   ├── commander.py         # LLM 战术指挥官
│   │   ├── parser.py            # 指令解析器
│   │   └── prompts/             # system.txt, few_shot.txt
│   ├── execution/               # 执行模块
│   │   ├── adb_utils.py         # ADB 封装
│   │   ├── executor.py          # 指令执行器
│   │   ├── mumu_manager.py      # MuMu 触控
│   │   └── mumu_ipc.py          # MuMu IPC 通信
│   ├── vision/                  # 视觉模块
│   │   ├── detector.py          # 颜色检测 (HSV)
│   │   └── ocr_reader.py        # OCR 读取
│   ├── learning/                # 学习系统
│   │   ├── battle_memory.py      # 经验库 (SQLite)
│   │   ├── outcome_eval.py       # 效果评估器
│   │   ├── memory_retriever.py   # 记忆检索器
│   │   └── strategy_compressor.py # 策略提炼器
│   ├── state/                   # 状态管理
│   │   ├── manager.py           # 状态管理器
│   │   └── models.py            # Pydantic 数据模型
│   └── screen/                  # 屏幕捕获
│       └── capture.py           # ADB piped screencap
├── config/                      # 配置文件
├── data/                        # 数据集 & 经验数据
├── scripts/                     # 辅助脚本
├── tests/                       # 测试
├── Firefight_AI_控制面板.html    # 离线控制面板
└── requirements.txt             # Python 依赖
```

---

## 快速开始

### 环境要求

- Windows 10/11
- Python 3.10+
- MuMu 模拟器 (Android 12)
- ADB 已配置

### 安装

```bash
git clone https://github.com/chenyt-Indom/firefightAI.git
cd firefightAI
pip install -r requirements.txt
```

### 配置

编辑 `config/settings.yaml`，填入：

```yaml
llm:
  api_key: "sk-xxx"           # DeepSeek API Key
  fallback_api_key: "xxx"     # GLM API Key
device:
  adb_host: "127.0.0.1"
  adb_port: 7555
```

### 运行

```bash
# 启动 AI 指挥
python main.py run --launch

# 启动 Web 控制面板
python dashboard_server.py

# 测试各组件
python main.py test --component adb
python main.py test --component yolo
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| 框架 | FastAPI, Flask, SocketIO |
| 视觉 | GLM-4V-Plus, PIL, OpenCV |
| AI | DeepSeek-V3, GLM-4-Flash |
| 数据库 | SQLite (SQLAlchemy ORM) |
| 自动化 | ADB, MuMuManager |
| 部署 | 腾讯云, Nginx, systemd |

---

## 演进历程 (160+ commits)

| 阶段 | 关键突破 |
|------|------|
| v1-v2 | YOLOv8 + ByteTrack 单位检测，颜色识别敌我 |
| v3 | DeepSeek 战术决策，ADB 批量 tap 选中 |
| v4 | 双击中圈拖拽移动，L1 经验回放学习 |
| v5 | L2 策略提炼，Web 控制面板，腾讯云部署 |
| v5.5+ | 去 YOLO/EasyOCR，GLM-4V-Plus 视觉理解，反幻觉，知识库 |

---

## 作者

**chenyt-Indom** — 准大一学生，广州民航职业技术学院 人工智能技术应用专业

- GitHub: [@chenyt-Indom](https://github.com/chenyt-Indom)

---

## License

MIT
