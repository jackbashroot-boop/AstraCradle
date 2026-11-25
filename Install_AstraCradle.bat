@echo off
setlocal enabledelayedexpansion
title AstraCradle v2.5.1 — Easy 1-Click Installer
cd /d "%~dp0"
echo ============================================================
echo   AstraCradle v2.5.1 — Dyson-Core OS Equilibrium Studio
echo ============================================================
echo.

set "PY_EXE="
set "PY_ARGS="
call :FIND_PYTHON
if not defined PY_EXE (
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo Python 3.10+ with Tk was not found. Installing Python 3.12 via winget...
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        call :FIND_PYTHON
    )
)

if not defined PY_EXE (
    echo [ERROR] Python 3.10+ ^(with Tkinter^) is required.
    echo Please install Python from https://www.python.org/downloads/
    echo ^(Check "Add Python to PATH" and "tcl/tk and IDLE", then re-run this file.^)
    pause
    exit /b 1
)

echo Using Python: "%PY_EXE%" %PY_ARGS%

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m pip --version >nul 2>&1
    if errorlevel 1 (
        echo [Recover] Detected incomplete .venv from an interrupted install — recreating...
        rmdir /s /q ".venv"
    ) else (
        ".venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1
        if errorlevel 1 (
            echo [Recover] Recreating .venv with working Tkinter support...
            rmdir /s /q ".venv"
        )
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating isolated virtual environment ^(.venv^)...
    "%PY_EXE%" %PY_ARGS% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/4] Installing AstraCradle v2.5.1 and local libraries...
call ".venv\Scripts\activate.bat"
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -e .
if errorlevel 1 (
    echo [ERROR] Package installation failed. Check internet connection for initial dependencies and retry.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import tkinter, customtkinter, PIL, psutil, pystray, astracradle.ui.app"
if errorlevel 1 (
    echo [ERROR] Required GUI dependencies failed to import ^(tkinter / customtkinter / pystray^).
    echo Try reinstalling Python from python.org with "tcl/tk and IDLE" enabled, then re-run Install_AstraCradle.bat.
    pause
    exit /b 1
)

echo [3/4] Running self-check and creating Start Menu shortcut...
".venv\Scripts\python.exe" -m astracradle --self-check
set "MEM_DESK=ask"
for /f "usebackq delims=" %%M in (`".venv\Scripts\python.exe" -c "import json,pathlib; p=pathlib.Path.home()/'.astracradle/machine_install_memory.json'; d=json.loads(p.read_text()) if p.exists() else {}; v=d.get('desktop_icon'); print('yes' if v is True else ('no' if v is False else 'ask'))" 2^>nul`) do set "MEM_DESK=%%M"
if /i "%MEM_DESK%"=="yes" (
    echo [Memory] Remembered Desktop Icon preference: YES
    ".venv\Scripts\python.exe" -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
) else if /i "%MEM_DESK%"=="no" (
    echo [Memory] Remembered Desktop Icon preference: Menu Only
    ".venv\Scripts\python.exe" -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
) else (
    set "ADD_DESKTOP="
    set /p "ADD_DESKTOP=Create an AstraCradle icon on your Desktop? [y/N]: "
    if /i "!ADD_DESKTOP!"=="y" (
        ".venv\Scripts\python.exe" -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
    ) else (
        ".venv\Scripts\python.exe" -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method bat --install-dir "%CD%"
    )
)

echo [4/4] Setup complete! Launching AstraCradle v2.5.1...
if exist ".venv\Scripts\astracradle-gui.exe" (
    start "" ".venv\Scripts\astracradle-gui.exe"
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m astracradle
) else (
    start "" ".venv\Scripts\python.exe" -m astracradle
)
exit /b 0

:FIND_PYTHON
py -3 -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_EXE=py"
    set "PY_ARGS=-3"
    exit /b 0
)
if exist "C:\Windows\py.exe" (
    "C:\Windows\py.exe" -3 -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY_EXE=C:\Windows\py.exe"
        set "PY_ARGS=-3"
        exit /b 0
    )
)
for %%P in (
    "%LocalAppData%\Programs\Python\Python314\python.exe"
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python314\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Program Files\Python310\python.exe"
) do (
    if not defined PY_EXE if exist "%%~P" (
        "%%~P" -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY_EXE=%%~P"
            set "PY_ARGS="
            exit /b 0
        )
    )
)
where python >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%P in ('where python') do (
        if not defined PY_EXE (
            echo "%%~P" | findstr /i "WindowsApps" >nul
            if errorlevel 1 (
                "%%~P" -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
                if !errorlevel! equ 0 (
                    set "PY_EXE=%%~P"
                    set "PY_ARGS="
                    exit /b 0
                )
            )
        )
    )
)
exit /b 0
