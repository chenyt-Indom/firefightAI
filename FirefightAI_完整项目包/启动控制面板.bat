@echo off
title Firefight AI 控制面板
echo ============================================
echo   🔥 Firefight AI 控制面板 启动中...
echo ============================================
echo.
echo [1/2] 启动 AI 服务端...
start "Firefight AI Server" /MIN cmd /c "cd /d C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI && C:\Users\19853\.workbuddy\binaries\python\versions\3.13.12\python.exe dashboard_server.py --port 5000"
echo [2/2] 等待服务就绪...
timeout /t 3 /nobreak >nul
echo [3/3] 打开控制面板...
start "" "C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\Firefight_AI_控制面板.html"
echo.
echo ✅ 控制面板已启动!
echo    - AI服务: http://localhost:5000
echo    - 面板文件: 双击 Firefight_AI_控制面板.html
echo    - 输入指令 AI 会自动分析并执行
echo ============================================
pause
