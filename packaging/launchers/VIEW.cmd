@echo off
setlocal
set "HERE=%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
if "%~1"=="" (
  echo No capture id supplied. Recent captures:
  call "%HERE%runtime\logic-agent.cmd" captures --data-dir "%HERE%.logic_agent_data"
  echo.
  echo Usage: VIEW.cmd CAPTURE_ID [--output PATH] [--no-open]
  echo A capture.vcd file can also be opened directly.
  pause
  exit /b 2
)
call "%HERE%runtime\logic-agent.cmd" view %* --data-dir "%HERE%.logic_agent_data"
if errorlevel 1 pause
endlocal
