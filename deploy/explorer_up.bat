@echo off
rem 啟動自動巡檢（Windows + Docker Desktop）。前提：已用 deploy_windows.bat 部署好 Wren MCP
chcp 65001 >nul
cd /d "%~dp0"
if not exist ..\secrets\db_password.txt (echo [失敗] 找不到 ..\secrets\db_password.txt & pause & exit /b 1)
if not exist ..\secrets\mcp_token.txt (echo [失敗] 找不到 ..\secrets\mcp_token.txt，請先跑 deploy_windows.bat & pause & exit /b 1)
docker image inspect wren-poc:0.2 >nul 2>&1 || (echo [失敗] 找不到映像 wren-poc:0.2，請先跑 deploy_windows.bat & pause & exit /b 1)
for /f "usebackq delims=" %%P in ("..\secrets\db_password.txt") do (set "WREN_PG_PASSWORD=%%P" & goto :pwdone)
:pwdone
set /p MCP_TOKEN=<..\secrets\mcp_token.txt
if not exist ..\secrets\explorer_web_token.txt (
  powershell -NoProfile -Command "$b=New-Object byte[] 12; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b|%%{$_.ToString('x2')}) -join '' | Set-Content -NoNewline -Encoding ascii '..\secrets\explorer_web_token.txt'"
  echo 已產生監控頁面通行碼：..\secrets\explorer_web_token.txt
)
set /p EXPLORER_WEB_TOKEN=<..\secrets\explorer_web_token.txt
if not exist logs mkdir logs
docker compose -f docker-compose.yml -f docker-compose.explorer.yml up -d --build --no-deps explorer > logs\explorer_up.log 2>&1
if errorlevel 1 (type logs\explorer_up.log & echo [失敗] 啟動失敗 & pause & exit /b 1)
docker compose -f docker-compose.yml -f docker-compose.explorer.yml ps explorer
echo.
echo 監控頁面：http://localhost:18090/ （通行碼在 ..\secrets\explorer_web_token.txt）
echo 每天 05:30 自動巡檢。要現在手動跑一次：dcx.bat exec explorer python run.py scan
pause
