@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Noor is not set up yet. Run setup_noor.bat first.
  pause
  exit /b 1
)

start "" wscript.exe "%~dp0run_noor_silent.vbs"
exit /b 0
