#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
模型加载器 — 从 models_registry/ 加载任意已训练模型
跨机器使用: 复制整个 firefightAI/ 文件夹即可, 所有模型在 models_registry/ 下
"""
import json
from pathlib import Path
from ultralytics import YOLO

REGISTRY = Path(__file__).parent / "models_registry"

def list_models():
    """列出所有可用模型"""
    manifest = REGISTRY / "manifest.json"
    if not manifest.exists():
        print("❌ 模型注册中心未初始化, 请运行 scripts/save_models.py")
        return {}
    return json.loads(manifest.read_text(encoding="utf-8"))["models"]

def load_model(name: str, device: str = "cpu") -> tuple[YOLO, dict]:
    """加载指定模型

    Args:
        name: 模型名 (如 'screen_ui36', 'faction_30')
        device: 'cpu' 或 'cuda'

    Returns:
        (YOLO模型, 训练参数dict)
    """
    models = list_models()
    if name not in models:
        available = ", ".join(models.keys())
        raise FileNotFoundError(f"模型 '{name}' 不存在! 可用: {available}")

    info = models[name]
    model_path = REGISTRY / f"{name}.pt"
    if not model_path.exists():
        raise FileNotFoundError(f"模型文件不存在: {model_path}")

    model = YOLO(str(model_path))
    model.to(device)
    print(f"✅ 加载 {name} | {info['task']} | mAP50={info.get('mAP50','?')}")
    return model, info

def load_all_models(device: str = "cpu") -> dict[str, tuple[YOLO, dict]]:
    """加载所有模型"""
    models = list_models()
    result = {}
    for name in models:
        result[name] = load_model(name, device)
    return result

if __name__ == "__main__":
    print("📦 Firefight AI 模型注册中心")
    print(f"   路径: {REGISTRY.resolve()}\n")
    models = list_models()
    for name, info in models.items():
        print(f"  {name}: {info['task']} (mAP50={info.get('mAP50','?')}, {info.get('images','?')}图)")
    print(f"\n  使用: from model_loader import load_model")
    print(f"        model, info = load_model('faction_30')")
