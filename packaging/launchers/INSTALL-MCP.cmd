@echo off
rem One-click MCP setup: add logic-analyzer to every AI agent found on this machine.
rem Run with no arguments to configure the agents that are installed; see docs\INSTALL.md
rem for --list, --dry-run, --remove and per-client ids.
setlocal
set "HERE=%~dp0"
call "%HERE%runtime\logic-agent.cmd" mcp-install %*
set "RC=%ERRORLEVEL%"
echo.
if "%~1"=="" goto :pause
if not "%RC%"=="0" goto :pause
endlocal & exit /b %RC%
:pause
echo Press any key to close this window.
pause >nul
endlocal & exit /b %RC%
