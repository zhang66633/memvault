@echo off
chcp 65001 >nul
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

echo ============================================
echo   MemVault 一键启动
echo ============================================

rem --- check 8780 ---
"%ROOT%.venv\Scripts\python.exe" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',8780))==0 else 1)"
if %errorlevel%==0 (
  echo [跳过] MemVault 已在运行 http://127.0.0.1:8780
) else (
  echo [启动] MemVault 服务（新窗口运行，关闭该窗口即停止服务）
  start "MemVault Server" cmd /k "cd /d "%ROOT%" && "%ROOT%.venv\Scripts\python.exe" run_server.py"
  echo 等待服务就绪 ...
  "%ROOT%.venv\Scripts\python.exe" -c "import socket,time
for _ in range(40):
    s=socket.socket(); s.settimeout(0.5)
    if s.connect_ex(('127.0.0.1',8780))==0: s.close(); break
    s.close(); time.sleep(0.5)"
)

echo 打开可视化控制台 ...
start "" "http://127.0.0.1:8780/dashboard/"
echo.
echo 完成：API http://127.0.0.1:8780  控制台 /dashboard/
echo 停止服务请双击 stop-memvault.bat（或关闭 MemVault Server 窗口）
endlocal
