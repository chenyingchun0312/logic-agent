@echo off
setlocal
set "HERE=%~dp0"
call "%HERE%runtime\logic-agent.cmd" %*
endlocal
