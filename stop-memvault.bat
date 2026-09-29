@echo off
chcp 65001 >nul
echo ============================================
echo   停止 MemVault 服务（端口 8780）
echo ============================================

set KILLED=0
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr :8780 ^| findstr LISTENING') do (
  echo 终止进程 PID %%P
  taskkill /PID %%P /F >nul 2>&1
  set KILLED=1
)
if "%KILLED%"=="0" (
  echo 没有发现运行中的 MemVault 服务
) else (
  echo 已停止
)
pause
