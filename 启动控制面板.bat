@echo off
title Firefight AI 控制面板
echo ============================================
echo   🔥 Firefight AI 控制面板 启动中...
echo ============================================
echo.
echo [1/2] 启动 AI 服务端...
start "Firefight AI Server" /MIN cmd /c "cd /d D:\firefightAI\zhanluxt && C:\Users\19853\.workbuddy\binaries\python\versions\3.13.12\python.exe dashboard_server.py --port 5000"
echo [2/2] 等待服务就绪...
timeout /t 3 /nobreak >nul
echo [3/3] 打开控制面板...
start "" "http://localhost:5000"
echo.
echo ✅ 控制面板已启动!
echo    - AI服务: http://localhost:5000
echo ============================================
pause
