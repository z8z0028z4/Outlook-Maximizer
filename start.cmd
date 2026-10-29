@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
python app.py %*
if errorlevel 1 pause
