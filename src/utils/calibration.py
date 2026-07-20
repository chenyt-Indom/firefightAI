"""UI校准工具 - 辅助用户标注游戏UI元素位置,生成ui_layout.yaml"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import cv2
import yaml
from loguru import logger

from src.screen.capture import ScreenCapture
from src.execution.adb_utils import ADBUtils


class CalibrationTool:
    """UI校准工具 - 交互式标注UI元素位置"""

    def __init__(self, adb: ADBUtils, capture: ScreenCapture):
        self.adb = adb
        self.capture = capture
        self._points: dict[str, list[tuple[int, int]]] = {}
        self._current_label: Optional[str] = None
        self._frame: Optional[cv2.Mat] = None

    def run(self) -> dict:
        """运行校准流程

        引导用户依次标注:
        1. 暂停按钮位置
        2. 资源面板区域
        3. 命令按钮位置
        4. 小地图区域
        """
        print("\n" + "=" * 60)
        print(" Firefight AI - UI校准工具")
        print("=" * 60)
        print("\n本工具将引导你标注游戏UI元素的位置。")
        print("请确保手机已连接,游戏正在运行(任意界面)。")
        print("操作方式: 鼠标点击画面中的对应位置")
        print("按 'q' 跳过当前标注, 按 's' 保存并退出\n")

        if not self.adb.ensure_connected():
            print("设备连接失败,请检查ADB连接")
            return {}

        self.capture.start()
        time.sleep(1)

        # 抓取一帧用于标注
        frame = self.capture.grab_latest_frame()
        if frame is None:
            print("无法获取画面,请检查scrcpy或ADB连接")
            self.capture.stop()
            return {}

        self._frame = frame.copy()
        h, w = frame.shape[:2]
        print(f"画面分辨率: {w}x{h}")

        # 校准项列表
        items = [
            ("pause_button", "暂停按钮(通常右上角)", "point"),
            ("move_button", "移动按钮(底部命令栏)", "point"),
            ("attack_button", "攻击按钮(底部命令栏)", "point"),
            ("stop_button", "停止按钮(底部命令栏)", "point"),
            ("retreat_button", "撤退按钮(底部命令栏)", "point"),
            ("attack_ground_button", "地面攻击按钮(底部命令栏)", "point"),
            ("minimap_center", "小地图中心点", "point"),
            ("resource_panel_top_left", "资源面板左上角", "point"),
            ("resource_panel_bottom_right", "资源面板右下角", "point"),
        ]

        for item_id, description, item_type in items:
            point = self._get_point(item_id, description)
            if point is not None:
                self._points[item_id] = point
                print(f"  {item_id}: ({point[0]}, {point[1]})")

        self.capture.stop()
        cv2.destroyAllWindows()

        # 生成校准配置
        layout = self._generate_layout(w, h)
        return layout

    def _get_point(self, item_id: str, description: str) -> Optional[tuple[int, int]]:
        """获取用户点击的坐标"""
        if self._frame is None:
            return None

        display = self._frame.copy()
        point = None

        def mouse_callback(event, x, y, flags, param):
            nonlocal point
            if event == cv2.EVENT_LBUTTONDOWN:
                point = (x, y)

        window_name = f"校准: {description} (点击标注,按q跳过,s保存退出)"
        cv2.namedWindow(window_name)
        cv2.setMouseCallback(window_name, mouse_callback)

        print(f"\n标注: {description}")
        print(f"请在画面窗口中点击对应位置...")

        while True:
            display = self._frame.copy()
            h, w = display.shape[:2]

            # 绘制提示文字
            cv2.putText(
                display, f"标注: {description}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
            )
            cv2.putText(
                display, "左键点击标注 | q=跳过 | s=保存退出",
                (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
            )

            if point is not None:
                cv2.circle(display, point, 8, (0, 0, 255), -1)
                cv2.putText(
                    display, f"({point[0]}, {point[1]})",
                    (point[0] + 15, point[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1,
                )

            cv2.imshow(window_name, display)
            key = cv2.waitKey(50) & 0xFF

            if key == ord('q'):
                point = None
                break
            elif key == ord('s'):
                break
            elif point is not None:
                break

        cv2.destroyWindow(window_name)
        return point

    def _generate_layout(self, screen_w: int, screen_h: int) -> dict:
        """生成ui_layout.yaml配置"""
        layout = {
            "pause_button": {
                "x": 0.95,
                "y": 0.05,
                "description": "游戏暂停/恢复按钮,通常位于右上角",
            },
            "resource_panel": {
                "credits_region": [0.05, 0.02, 0.15, 0.06],
                "population_region": [0.20, 0.02, 0.30, 0.06],
            },
            "unit_selection": {
                "battlefield_region": [0.05, 0.10, 0.80, 0.90],
                "double_tap_interval": 0.3,
            },
            "command_panel": {
                "move_button": [0.05, 0.92, 0.12, 0.98],
                "attack_button": [0.15, 0.92, 0.22, 0.98],
                "stop_button": [0.25, 0.92, 0.32, 0.98],
                "retreat_button": [0.35, 0.92, 0.42, 0.98],
                "attack_ground_button": [0.45, 0.92, 0.52, 0.98],
            },
            "unit_info_panel": {
                "health_bar_region": [0.02, 0.10, 0.25, 0.15],
                "unit_type_region": [0.02, 0.15, 0.25, 0.20],
                "ammo_region": [0.02, 0.20, 0.25, 0.25],
            },
            "minimap": {
                "region": [0.85, 0.75, 1.0, 1.0],
                "center_x": 0.925,
                "center_y": 0.875,
            },
            "game_over": {
                "victory_text_region": [0.30, 0.35, 0.70, 0.55],
                "defeat_text_region": [0.30, 0.35, 0.70, 0.55],
                "continue_button": [0.45, 0.60, 0.55, 0.68],
            },
            "deployment": {
                "unit_card_region": [0.02, 0.80, 0.98, 0.98],
                "deploy_button": [0.85, 0.92, 0.95, 0.98],
                "points_region": [0.70, 0.02, 0.85, 0.06],
            },
        }

        # 用实际标注点更新坐标
        if "pause_button" in self._points:
            px, py = self._points["pause_button"]
            layout["pause_button"]["x"] = round(px / screen_w, 3)
            layout["pause_button"]["y"] = round(py / screen_h, 3)

        # 保存到文件
        output_path = Path("config/ui_layout.yaml")
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(layout, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        print(f"\n校准配置已保存到: {output_path.absolute()}")
        return layout


def run_calibration(adb_host: str = "192.168.1.100", adb_port: int = 5555) -> None:
    """快速启动校准工具"""
    adb = ADBUtils(host=adb_host, port=adb_port)
    capture = ScreenCapture(adb=adb)
    tool = CalibrationTool(adb, capture)
    tool.run()