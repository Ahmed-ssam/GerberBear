@echo off
rem Same as run_app.bat but keeps a console window open with the log (for troubleshooting).
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
cd app
"..\.venv\Scripts\python.exe" -m ai_pcb_designer.app.main %*
pause
