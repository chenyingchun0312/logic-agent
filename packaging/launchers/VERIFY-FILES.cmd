@echo off
setlocal
set "HERE=%~dp0"
"%HERE%runtime\python\python.exe" -B "%HERE%scripts\verify-files.py" "%HERE%."
if errorlevel 1 pause
endlocal
