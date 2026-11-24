#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================================"
echo "  AstraCradle v2.5.1 — Dyson-Core OS Equilibrium Studio"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] Python 3.10+ is required."
  echo "Install Python from https://www.python.org/downloads/ or run: brew install python python-tk"
  read -r -p "Press Enter to exit..."
  exit 1
fi

if ! python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
  echo "[ERROR] Python 3.10+ is required (found $(python3 --version))."
  read -r -p "Press Enter to exit..."
  exit 1
fi

if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "[INFO] Python Tkinter UI toolkit is missing. Trying Homebrew (brew install python-tk)..."
  if command -v brew >/dev/null 2>&1; then
    brew install python-tk || true
  fi
  if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "[ERROR] Please install python-tk (e.g. 'brew install python-tk') and double-click this script again."
    read -r -p "Press Enter to exit..."
    exit 1
  fi
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/4] Creating isolated virtual environment (.venv)..."
  python3 -m venv .venv
fi

echo "[2/4] Installing AstraCradle v2.5.1..."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e .

echo "[3/4] Verifying self-healing invariants & creating Applications shortcut..."
./.venv/bin/python -m astracradle --self-check
ADD_DESKTOP="n"
if [ -t 0 ]; then
  read -r -p "Create an AstraCradle icon on your Desktop? [y/N] " ADD_DESKTOP || true
fi
if [[ "${ADD_DESKTOP:-n}" =~ ^[Yy]$ ]]; then
  ./.venv/bin/python -m astracradle --install-shortcuts --desktop-icon
else
  ./.venv/bin/python -m astracradle --install-shortcuts
fi

echo "[4/4] Setup complete! Launching AstraCradle v2.5.1..."
if [[ "${1:-}" != "--no-launch" ]]; then
  if [ -x ".venv/bin/astracradle-gui" ]; then
    nohup ./.venv/bin/astracradle-gui >/dev/null 2>&1 &
  else
    nohup ./.venv/bin/python -m astracradle >/dev/null 2>&1 &
  fi
fi
