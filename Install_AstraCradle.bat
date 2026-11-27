@echo off
setlocal enabledelayedexpansion
title AstraCradle v2.5.1 — Easy 1-Click Installer
cd /d "%~dp0"
echo ============================================================
echo   AstraCradle v2.5.1 — Dyson-Core OS Equilibrium Studio
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

echo [2/4] Installing AstraCradle v2.5.1 and local libraries...
call ".venv\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e .
if errorlevel 1 (
    echo [ERROR] Package installation failed. Check internet connection for initial dependencies and retry.
    pause
    exit /b 1
)

echo [3/4] Running 7-invariant self-check and creating Start Menu shortcut...
python -m astracradle --self-check
set "MEM_DESK=ask"
for /f "usebackq delims=" %%M in (`python -c "import json,pathlib; p=pathlib.Path.home()/'.astracradle/machine_install_memory.json'; d=json.loads(p.read_text()) if p.exists() else {}; v=d.get('desktop_icon'); print('yes' if v is True else ('no' if v is False else 'ask'))" 2^>nul`) do set "MEM_DESK=%%M"
if /i "%MEM_DESK%"=="yes" (
    echo [Memory] Remembered Desktop Icon preference: YES
    python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
) else if /i "%MEM_DESK%"=="no" (
    echo [Memory] Remembered Desktop Icon preference: Menu Only
    python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
) else (
    set "ADD_DESKTOP="
    set /p "ADD_DESKTOP=Create an AstraCradle icon on your Desktop? [y/N]: "
    if /i "!ADD_DESKTOP!"=="y" (
        python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
    ) else (
        python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
    )
)

echo [4/4] Setup complete! Launching AstraCradle v2.5.1...
if exist ".venv\Scripts\astracradle-gui.exe" (
    start "" ".venv\Scripts\astracradle-gui.exe"
) else (
    start "" ".venv\Scripts\pythonw.exe" -m astracradle
)
exit /b 0
