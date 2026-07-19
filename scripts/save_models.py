#!/usr/bin/env python
"""将训练好的模型注册到 models_registry/ 供跨机使用"""
import shutil, json, sys
from pathlib import Path

REGISTRY = Path(__file__).parent / "models_registry"
REGISTRY.mkdir(exist_ok=True)

# ── 模型定义: 添加新模型在这里加一行 ──
MODELS = [
    {
        "name": "goto_bar17",
        "source": "runs/detect/goto_bar17/weights/best.pt",
        "task": "goto bar (蓝条)检测",
        "classes": ["goto_bar"],
        "images": 17, "epochs": 100, "mAP50": 0.995, "recall": 1.0,
    },
    {
        "name": "screen_ui36",
        "source": "runs/detect/screen_ui36_best.pt",
        "task": "UI按钮/国旗检测 (选阵营界面)",
        "classes": ["You_flag", "Enemy_flag", "dropdown", "OK", "unit", "start", "flag", "other", "enemy"],
        "images": 36, "epochs": 80, "mAP50": 0.995, "precision": 0.995,
        "dataset": "data/screen_yolo/",
    },
    {
        "name": "faction_30",
        "source": "runs/detect/runs/detect/faction_30/weights/best.pt",
        "task": "阵营下拉列表17国位置检测",
        "classes": ["UN","Poland","UK","France","ODKB","USA","Japan","China","Korea","","","","","","","",""],
        "images": 30, "epochs": 80, "mAP50": 0.995,
        "dataset": "data/faction_yolo/",
    },
]

manifest_path = REGISTRY / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"created": "", "models": {}}

for m in MODELS:
    src = Path(m["source"])
    if not src.exists():
        print(f"⚠️ 跳过 {m['name']}: 源文件不存在 {src}")
        continue
    
    dst = REGISTRY / f"{m['name']}.pt"
    shutil.copy(src, dst)
    size = dst.stat().st_size / (1024*1024)
    
    info = {k: v for k, v in m.items() if k not in ("name", "source")}
    info["size_mb"] = round(size, 1)
    info["registry_file"] = f"models_registry/{m['name']}.pt"
    
    manifest["models"][m["name"]] = info
    print(f"✅ {m['name']}.pt ({size:.1f}MB) — {m['task']}")

from datetime import datetime
manifest["created"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
print(f"\n📋 已更新 manifest.json ({len(manifest['models'])}个模型)")
