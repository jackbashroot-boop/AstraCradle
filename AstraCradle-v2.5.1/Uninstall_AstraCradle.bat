@echo off
title Uninstall AstraCradle Safely
cd /d "%~dp0"
echo ========================================================
echo   AstraCradle Clean Uninstaller (Zero-Damage Removal)
echo ========================================================
echo.
set "PY_BIN=python"
if exist ".venv\Scripts\python.exe" set "PY_BIN=.venv\Scripts\python.exe"

%PY_BIN% -m astracradle --uninstall --uninstall-package

if exist ".venv" (
    echo Removing isolated AstraCradle .venv directory...
    rmdir /s /q ".venv"
)

echo.
echo AstraCradle uninstall complete. Your personal files and OS were not touched.
pause
