@echo off
setlocal
set "HERE=%~dp0"
set "DSLAB_EXE=%HERE%bin\dslab.exe"
set "DSLAB_RES_DIR=%HERE%bin\res"
set "LOGIC_AGENT_DECODER_EXE=%HERE%bin\sigrok-cli.exe"
set "SIGROKDECODE_DIR=%HERE%decoders"
set "PYTHONDONTWRITEBYTECODE=1"
"%HERE%python\python.exe" -m logic_agent.cli %*
