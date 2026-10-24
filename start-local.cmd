@echo off
chcp 65001 >nul
echo 正在 WSL 中启动应用。浏览器访问 http://localhost:8765
wsl.exe --cd "%~dp0." bash scripts/start.sh
pause
