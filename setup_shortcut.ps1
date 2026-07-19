# Firefight AI 桌面快捷方式创建脚本
# 运行方式: 右键 -> 使用 PowerShell 运行, 或
#          powershell -ExecutionPolicy Bypass -File "setup_shortcut.ps1"

$ErrorActionPreference = "Stop"

$ProjectDir = "d:\firefightAI\zhanluxt"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Firefight AI 战术指挥系统.lnk"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Firefight AI 桌面快捷方式创建工具" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 检查项目目录
if (-not (Test-Path $ProjectDir)) {
    Write-Host "[错误] 项目目录不存在: $ProjectDir" -ForegroundColor Red
    Read-Host "按 Enter 退出"
    exit 1
}

# 检查 launch.bat
$LaunchBat = Join-Path $ProjectDir "launch.bat"
if (-not (Test-Path $LaunchBat)) {
    Write-Host "[错误] launch.bat 不存在: $LaunchBat" -ForegroundColor Red
    Read-Host "按 Enter 退出"
    exit 1
}

# 检查 Python
try {
    $pythonVersion = py -3 --version 2>&1
    Write-Host "[OK] Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[警告] 未检测到 Python, 请确保已安装 Python 3.8+" -ForegroundColor Yellow
}

# 创建快捷方式
Write-Host ""
Write-Host "正在创建桌面快捷方式..." -ForegroundColor Yellow

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)

$Shortcut.TargetPath = "cmd.exe"
$Shortcut.Arguments = "/c `"`"$LaunchBat`"`""
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.Description = "Firefight AI 战术指挥系统 v3.0"
$Shortcut.IconLocation = "shell32.dll,13"  # 使用系统图标
$Shortcut.WindowStyle = 1  # 正常窗口

$Shortcut.Save()

Write-Host ""
Write-Host "[成功] 快捷方式已创建!" -ForegroundColor Green
Write-Host "  位置: $ShortcutPath" -ForegroundColor White
Write-Host ""
Write-Host "双击桌面的 'Firefight AI 战术指挥系统' 即可启动!" -ForegroundColor Cyan
Write-Host ""
Read-Host "按 Enter 退出"