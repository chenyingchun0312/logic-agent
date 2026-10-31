@echo off
setlocal
set "HERE=%~dp0"
set "DSLAB_RES_DIR=%HERE%runtime\bin\res"
set "DSLAB_FIRMWARE=%HERE%firmware\DSLogicPlus.fw"
"%HERE%runtime\bin\dslab.exe" firmware status --json
pause
endlocal
