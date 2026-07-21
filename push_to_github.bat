@echo off
chcp 65001 >nul
echo ========================================
echo   FirefightAI GitHub 自动推送
echo ========================================
cd /d "%~dp0"
set GIT_TERMINAL_PROMPT=0

:: 使用SSH方式推送（不需要Token，更安全）
:: 确保已配置SSH密钥: ssh-keygen -t rsa -b 4096 -C "your_email@example.com"
:: 并将公钥添加到GitHub: https://github.com/settings/keys
git remote set-url origin git@github.com:chenyt-Indom/firefightAI.git 2>nul

echo [1/3] 检查Git状态...
git status --short

echo.
echo [2/3] 提交所有变更...
git add -A
git commit -m "自动推送: 所有最新修改 %date% %time%" 2>nul
if %errorlevel% neq 0 (
    echo 没有新变更需要提交，尝试推送已有提交...
)

echo.
echo [3/3] 推送到GitHub...
echo 正在推送，请耐心等待（可能需要几分钟）...
git push origin master

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   推送成功！ %date% %time%
    echo ========================================
) else (
    echo.
    echo ========================================
    echo   推送失败，请检查网络连接
    echo   如果网络受限，请使用VPN或代理后重试
    echo ========================================
)
pause