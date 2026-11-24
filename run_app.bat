@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run check_install.bat first - it sets up the Python environment.
  pause
  exit /b 1
)
cd app
start "" "..\.venv\Scripts\pythonw.exe" -m ai_pcb_designer.app.main %*
