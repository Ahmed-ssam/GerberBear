@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Run check_install.bat first: it sets up the Python environment.
  pause
  exit /b 1
)
echo ============================================================
echo  Gerber Bear - benchmark (real AI, about 15-25 minutes)
echo  Uses the AI key saved in the app. Nothing is ordered or uploaded.
echo ============================================================
".venv\Scripts\python.exe" app\benchmark.py %*
echo.
echo ============================================================
echo  Finished. Report: reports\benchmark\benchmark_report.txt
echo  Open reports\benchmark\benchmark_report.txt for the details.
echo ============================================================
pause
