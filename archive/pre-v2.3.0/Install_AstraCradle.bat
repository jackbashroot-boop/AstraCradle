@echo off
setlocal enabledelayedexpansion
title AstraCradle v1.0.0 — Easy 1-Click Installer
cd /d "%~dp0"
echo ============================================================
echo   AstraCradle v1.0.0 — Dyson-Core OS Equilibrium Studio
echo ============================================================
echo.

set "PY_CMD="
for %%P in ("python" "py -3") do (
    if not defined PY_CMD (
        %%~P -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
        if !errorlevel! equ 0 set "PY_CMD=%%~P"
    )
)

if not defined PY_CMD (
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo Python 3.10+ was not found. Attempting automatic install via Windows winget...
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        for %%P in ("python" "py -3" "%LocalAppData%\Programs\Python\Python312\python.exe") do (
            if not defined PY_CMD (
                %%~P -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
                if !errorlevel! equ 0 set "PY_CMD=%%~P"
            )
        )
    )
)

if not defined PY_CMD (
    echo [ERROR] Python 3.10 or newer is required.
    echo Please install Python from https://www.python.org/downloads/
    echo ^(Check the box "Add Python to PATH" during setup, then double-click this file again.^)
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating isolated virtual environment ^(.venv^)...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/4] Installing AstraCradle v1.0.0 and local libraries...
call ".venv\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e .
if errorlevel 1 (
    echo [ERROR] Package installation failed. Check internet connection for initial dependencies and retry.
    pause
    exit /b 1
)

echo [3/4] Running 7-invariant self-check and creating Desktop / Start Menu shortcuts...
python -m astracradle --self-check
python -m astracradle --install-shortcuts

echo [4/4] Setup complete! Launching AstraCradle v1.0.0...
if exist ".venv\Scripts\astracradle-gui.exe" (
    start "" ".venv\Scripts\astracradle-gui.exe"
) else (
    start "" ".venv\Scripts\pythonw.exe" -m astracradle
)
exit /b 0
