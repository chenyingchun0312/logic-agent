@echo off
setlocal
set "HERE=%~dp0"
set "DSLAB_RES_DIR=%HERE%bin\res"
"%HERE%bin\dslab.exe" %*
