@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist reports mkdir reports
echo ============================================================
echo  Gerber Bear - installation check
echo ============================================================
if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment in .venv ...
  py -3.12 -m venv .venv >nul 2>&1
  if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv >nul 2>&1
  if not exist ".venv\Scripts\python.exe" python -m venv .venv >nul 2>&1
)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Python was not found. Install Python 3.12 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this file again.
  echo Python not found > reports\check_report.txt
  pause
  exit /b 1
)
echo Installing / updating packages (first run takes 2-5 minutes) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip > reports\setup.log 2>&1
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r app\requirements-win.txt >> reports\setup.log 2>&1
if errorlevel 1 (
  echo.
  echo Package installation failed. Last lines of reports\setup.log:
  powershell -NoProfile -Command "Get-Content reports\setup.log -Tail 25"
  pause
  exit /b 1
)
echo Packages OK.
echo.
".venv\Scripts\python.exe" app\check_install.py %*
echo.
echo ============================================================
echo  Finished. Report: reports\check_report.txt
echo  Open reports\check_report.txt for the details.
echo ============================================================
pause
