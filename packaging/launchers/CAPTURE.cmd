@echo off
setlocal
set "HERE=%~dp0"
set "DSLAB_EXE=%HERE%runtime\bin\dslab.exe"
set "DSLAB_RES_DIR=%HERE%runtime\bin\res"
"%HERE%runtime\bin\dslab.exe" capture once %*
if errorlevel 1 pause
endlocal
