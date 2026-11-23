@echo off
rem 含自動巡檢的 docker compose 包裝（Windows），例：dcx.bat logs -f explorer
cd /d "%~dp0"
for /f "usebackq delims=" %%P in ("..\secrets\db_password.txt") do (set "WREN_PG_PASSWORD=%%P" & goto :pw)
:pw
set "MCP_TOKEN=not-yet-generated"
if exist ..\secrets\mcp_token.txt set /p MCP_TOKEN=<..\secrets\mcp_token.txt
set "EXPLORER_WEB_TOKEN=not-yet-generated"
if exist ..\secrets\explorer_web_token.txt set /p EXPLORER_WEB_TOKEN=<..\secrets\explorer_web_token.txt
docker compose -f docker-compose.yml -f docker-compose.explorer.yml %*
