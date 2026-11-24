@echo off
cd /d "%~dp0"
echo ============================================================
echo  Gerber Bear - build the Windows installer (5-10 minutes)
echo ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File build_installer.ps1 %*
echo.
pause
