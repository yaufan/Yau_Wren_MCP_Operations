@echo off
rem Wren MCP 部署（Windows + Docker Desktop）
rem 需要：..\secrets\db_password.txt（第一行＝資料庫密碼，請自行建立）
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .env (echo [失敗] 找不到 deploy\.env（複製 .env.example 後填資料庫主機等設定） & pause & exit /b 1)
if not exist ..\secrets\db_password.txt (echo [失敗] 找不到 ..\secrets\db_password.txt & pause & exit /b 1)
for /f "usebackq delims=" %%P in ("..\secrets\db_password.txt") do (set "WREN_PG_PASSWORD=%%P" & goto :pwdone)
:pwdone
if not exist ..\secrets\mcp_token.txt (
  powershell -NoProfile -Command "$b=New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b|%%{$_.ToString('x2')}) -join '' | Set-Content -NoNewline -Encoding ascii '..\secrets\mcp_token.txt'"
  echo 已產生 MCP token：..\secrets\mcp_token.txt（給 MaiAgent 設定用，請妥善保管）
)
set /p MCP_TOKEN=<..\secrets\mcp_token.txt
set "LOG=logs\deploy_%date:~0,4%%date:~5,2%%date:~8,2%.log"
echo == 建置並啟動（第一次約 3~5 分鐘）== 
docker compose up -d --build > "%LOG%" 2>&1
if errorlevel 1 (echo [失敗] 啟動失敗，看 %LOG% & pause & exit /b 1)
echo == 等待 wren 健康檢查 ==
set /a n=0
:wait
set /a n+=1
for /f %%S in ('docker inspect -f "{{.State.Health.Status}}" wren-poc-wren-1 2^>nul') do set "HS=%%S"
if "%HS%"=="healthy" goto ok
if %n% geq 36 goto ok
timeout /t 5 >nul
goto wait
:ok
docker compose ps >> "%LOG%" 2>&1
echo == 驗收 ==
docker compose exec -T wren python /app/tests/acceptance.py >> "%LOG%" 2>&1
type "%LOG%"
pause
