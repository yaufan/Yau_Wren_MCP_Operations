@echo off
rem docker compose 的包裝（Windows）：先載入 ..\secrets 的密碼與 token，例：dc.bat ps
cd /d "%~dp0"
for /f "usebackq delims=" %%P in ("..\secrets\db_password.txt") do (set "WREN_PG_PASSWORD=%%P" & goto :pw)
:pw
set "MCP_TOKEN=(not-yet-generated)"
if exist ..\secrets\mcp_token.txt set /p MCP_TOKEN=<..\secrets\mcp_token.txt
docker compose %*
