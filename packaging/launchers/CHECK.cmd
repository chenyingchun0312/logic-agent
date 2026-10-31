@echo off
setlocal
set "HERE=%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\check.ps1" -Root "%HERE%." %*
if errorlevel 1 pause
endlocal
