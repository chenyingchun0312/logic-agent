@echo off
setlocal
set "HERE=%~dp0"
call "%HERE%runtime\logic-agent-mcp.cmd" %*
endlocal
