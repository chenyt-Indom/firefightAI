#!/usr/bin/env python3
"""Firefight AI - Android 模拟器安装脚本

独立运行此脚本以设置Android模拟器环境：
  1. 下载Android SDK命令行工具
  2. 安装模拟器、系统镜像、平台工具
  3. 创建AVD并配置
  4. 测试模拟器启动

也可以通过前端控制面板调用：
  POST /api/emulator/install
"""

from __future__ import annotations
import os, sys, time, json, shutil, zipfile, tempfile, subprocess, argparse
from pathlib import Path

# 配置
PROJECT_ROOT = Path(__file__).parent.parent
EMULATOR_HOME = PROJECT_ROOT / "android_emulator"
ANDROID_SDK_ROOT = EMULATOR_HOME / "sdk"
AVD_NAME = "firefight_avd"
AVD_CONFIG = {
    "device": "pixel_6",
    "api_level": 33,
    "arch": "x86_64",
    "ram": 4096,
    "cores": 4,
    "resolution": "1920x1080",
    "density": 320,
}
EMULATOR_PORT = 5556

# Android SDK 命令行工具下载地址
CMDLINE_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def download_file(url: str, dest: Path):
    """下载文件，显示进度"""
    import requests
    log(f"下载: {url}")
    r = requests.get(url, stream=True, timeout=600)
    total = int(r.headers.get("content-length", 0))
    downloaded = 0
    last_pct = -1
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = int(downloaded / total * 100)
                if pct != last_pct:
                    log(f"  进度: {pct}% ({downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB)")
                    last_pct = pct
    log(f"下载完成: {dest}")


def setup_sdk():
    """下载并安装Android SDK命令行工具"""
    EMULATOR_HOME.mkdir(parents=True, exist_ok=True)
    ANDROID_SDK_ROOT.mkdir(parents=True, exist_ok=True)

    cmdline_dir = ANDROID_SDK_ROOT / "cmdline-tools" / "latest"
    sdkmanager = cmdline_dir / "bin" / "sdkmanager.bat"

    if sdkmanager.exists():
        log("SDK命令行工具已存在，跳过下载")
        return str(sdkmanager)

    tools_zip = EMULATOR_HOME / "cmdline-tools.zip"
    if not tools_zip.exists():
        log("下载Android SDK命令行工具...")
        download_file(CMDLINE_TOOLS_URL, tools_zip)

    log("解压命令行工具...")
    cmdline_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tools_zip, "r") as zf:
        for member in zf.namelist():
            parts = member.split("/", 1)
            if len(parts) < 2:
                continue
            target_path = cmdline_dir / parts[1].replace("/", "\\")
            if member.endswith("/"):
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())

    log("SDK命令行工具安装完成")
    return str(sdkmanager)


def accept_licenses(sdkmanager: str):
    """接受Android SDK许可协议"""
    log("接受许可协议...")
    subprocess.run(
        [sdkmanager, "--sdk_root=" + str(ANDROID_SDK_ROOT), "--licenses"],
        input=b"y\ny\ny\ny\ny\ny\ny\ny\n",
        capture_output=True,
        timeout=30,
    )
    log("许可协议已接受")


def install_components(sdkmanager: str):
    """安装SDK组件"""
    components = [
        "platform-tools",
        "emulator",
        f"system-images;android-{AVD_CONFIG['api_level']};default;{AVD_CONFIG['arch']}",
        f"platforms;android-{AVD_CONFIG['api_level']}",
    ]

    for comp in components:
        log(f"安装组件: {comp}")
        result = subprocess.run(
            [sdkmanager, "--sdk_root=" + str(ANDROID_SDK_ROOT), comp],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            log(f"  警告: {comp} 安装可能失败")
            if result.stderr:
                log(f"  {result.stderr[:200]}")
        else:
            log(f"  {comp} 安装完成")


def create_avd(sdkmanager_path: str):
    """创建AVD"""
    cmdline_dir = Path(sdkmanager_path).parent
    avdmanager = cmdline_dir / "avdmanager.bat"

    if not avdmanager.exists():
        log(f"错误: avdmanager未找到: {avdmanager}")
        return False

    avd_dir = Path.home() / ".android" / "avd" / f"{AVD_NAME}.avd"
    if avd_dir.exists():
        log(f"AVD已存在: {AVD_NAME}")
        return True

    log(f"创建AVD: {AVD_NAME}")
    result = subprocess.run(
        [
            str(avdmanager), "create", "avd",
            "-n", AVD_NAME,
            "-k", f"system-images;android-{AVD_CONFIG['api_level']};default;{AVD_CONFIG['arch']}",
            "-d", AVD_CONFIG["device"],
            "-f",
        ],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0:
        log(f"AVD创建失败: {result.stderr[:300]}")
        return False

    log(f"AVD {AVD_NAME} 创建成功")
    return True


def configure_avd():
    """配置AVD参数"""
    avd_dir = Path.home() / ".android" / "avd" / f"{AVD_NAME}.avd"
    config_ini = avd_dir / "config.ini"

    if not config_ini.exists():
        log("config.ini 不存在，跳过配置")
        return

    log("配置AVD参数...")
    custom_config = {
        "hw.ramSize": str(AVD_CONFIG["ram"]),
        "hw.cpu.ncore": str(AVD_CONFIG["cores"]),
        "hw.lcd.width": AVD_CONFIG["resolution"].split("x")[0],
        "hw.lcd.height": AVD_CONFIG["resolution"].split("x")[1],
        "hw.lcd.density": str(AVD_CONFIG["density"]),
        "hw.keyboard": "yes",
        "disk.dataPartition.size": "8G",
        "hw.gpu.enabled": "yes",
        "hw.gpu.mode": "host",
    }

    config_lines = config_ini.read_text().split("\n")
    existing_keys = set()
    new_lines = []

    for line in config_lines:
        if "=" in line:
            k = line.split("=", 1)[0].strip()
            existing_keys.add(k)
            if k in custom_config:
                new_lines.append(f"{k}={custom_config[k]}")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    for k, v in custom_config.items():
        if k not in existing_keys:
            new_lines.append(f"{k}={v}")

    config_ini.write_text("\n".join(new_lines))
    log("AVD配置完成")


def test_emulator():
    """测试模拟器启动"""
    emu_exe = ANDROID_SDK_ROOT / "emulator" / "emulator.exe"
    if not emu_exe.exists():
        log(f"模拟器未找到: {emu_exe}")
        return False

    platform_tools = ANDROID_SDK_ROOT / "platform-tools"
    adb_exe = platform_tools / "adb.exe" if platform_tools.exists() else "adb"

    log(f"启动模拟器测试 (端口={EMULATOR_PORT})...")
    cmd = [
        str(emu_exe), "-avd", AVD_NAME,
        "-no-window", "-no-audio",
        "-gpu", "swiftshader_indirect",
        "-netdelay", "none", "-netspeed", "full",
        "-port", str(EMULATOR_PORT),
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log("等待模拟器启动...")

    # 等待启动
    subprocess.run([str(adb_exe), "start-server"], capture_output=True, text=True, timeout=5)

    waited = 0
    started = False
    while waited < 120:
        time.sleep(3)
        waited += 3
        r = subprocess.run([str(adb_exe), "devices"], capture_output=True, text=True, timeout=5)
        if f"localhost:{EMULATOR_PORT}" in r.stdout and "device" in r.stdout:
            started = True
            break
        log(f"  等待中... {waited}s")

    if started:
        log(f"模拟器启动成功! 端口: {EMULATOR_PORT}")
        subprocess.run([str(adb_exe), "connect", f"localhost:{EMULATOR_PORT}"], capture_output=True, text=True, timeout=10)
        log("ADB已连接")
    else:
        log("模拟器启动超时")

    # 停止模拟器
    log("停止模拟器...")
    process.terminate()
    try:
        process.wait(timeout=10)
    except:
        process.kill()
    log("模拟器已停止")

    return started


def main():
    parser = argparse.ArgumentParser(description="Firefight AI Android 模拟器安装脚本")
    parser.add_argument("--skip-download", action="store_true", help="跳过下载（如果已下载）")
    parser.add_argument("--test", action="store_true", help="安装后测试启动")
    parser.add_argument("--port", type=int, default=5556, help=f"模拟器ADB端口 (默认: 5556)")
    args = parser.parse_args()

    global EMULATOR_PORT
    EMULATOR_PORT = args.port

    log("=" * 60)
    log("  Firefight AI - Android 模拟器安装脚本")
    log("=" * 60)
    log(f"  项目目录: {PROJECT_ROOT}")
    log(f"  SDK目录: {ANDROID_SDK_ROOT}")
    log(f"  AVD名称: {AVD_NAME}")
    log(f"  AVD配置: {json.dumps(AVD_CONFIG, indent=2)}")
    log("=" * 60)

    try:
        # 1. 安装SDK
        sdkmanager = setup_sdk()

        # 2. 接受许可
        accept_licenses(sdkmanager)

        # 3. 安装组件
        install_components(sdkmanager)

        # 4. 创建AVD
        if create_avd(sdkmanager):
            configure_avd()

        # 5. 测试
        if args.test:
            log("")
            log("=" * 60)
            test_emulator()

        log("")
        log("=" * 60)
        log("  安装完成!")
        log(f"  模拟器可执行文件: {ANDROID_SDK_ROOT / 'emulator' / 'emulator.exe'}")
        log(f"  ADB: {ANDROID_SDK_ROOT / 'platform-tools' / 'adb.exe'}")
        log(f"  启动命令: emulator -avd {AVD_NAME} -no-window -no-audio -gpu swiftshader_indirect -port {EMULATOR_PORT}")
        log("=" * 60)

    except KeyboardInterrupt:
        log("用户中断")
    except Exception as e:
        log(f"安装失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()