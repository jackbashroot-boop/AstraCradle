@echo off
cd /d "%~dp0"
if exist "dist\AstraCradle\AstraCradle.exe" (
    start "" "dist\AstraCradle\AstraCradle.exe" %*
    exit /b 0
)
if exist ".venv\Scripts\astracradle-gui.exe" (
    start "" ".venv\Scripts\astracradle-gui.exe" %*
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m astracradle %*
    exit /b 0
)
start "" pythonw -m astracradle %*
