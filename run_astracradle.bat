@echo off
cd /d "%~dp0"
if exist "dist\AstraCradle\AstraCradle.exe" (
    start "" "dist\AstraCradle\AstraCradle.exe" %*
    exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
    echo AstraCradle virtual environment is not set up yet. Running Install_AstraCradle.bat...
    call "%~dp0Install_AstraCradle.bat"
    exit /b %errorlevel%
)
".venv\Scripts\python.exe" -c "import astracradle.ui.app, customtkinter, tkinter" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] AstraCradle GUI dependencies are incomplete or damaged. Running repair install...
    call "%~dp0Install_AstraCradle.bat"
    exit /b %errorlevel%
)
start "AstraCradle" ".venv\Scripts\python.exe" -m astracradle %*
