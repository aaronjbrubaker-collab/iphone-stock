@echo off
title Duo Watch - PC runner
cd /d "%~dp0"

set PY=python
where python >nul 2>&1 || set PY=py

:loop
%PY% local_runner.py
echo.
echo Runner stopped. Restarting in 10 seconds... (close this window to quit)
timeout /t 10 /nobreak >nul
goto loop
