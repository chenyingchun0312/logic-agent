@echo off
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%scripts\self-test.ps1" -Root "%HERE%." %*
if errorlevel 1 pause
endlocal
