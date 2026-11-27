#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

NO_LAUNCH=0
DESKTOP_ICON=0
PASS_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--no-launch" ]]; then
    NO_LAUNCH=1
  elif [[ "$arg" == "--desktop-icon" ]]; then
    DESKTOP_ICON=1
  else
    PASS_ARGS+=("$arg")
  fi
done

echo "============================================================"
echo "  AstraCradle v2.5.1 — Dyson-Core OS Equilibrium Studio"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 was not found. Please install Python 3.10+ and python3-tk first."
  exit 1
fi

if ! python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
  echo "[ERROR] Python 3.10+ is required (found $(python3 --version))."
  exit 1
fi

if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "[ERROR] python3-tk is missing. Install it first (e.g. 'sudo apt install python3-tk python3-venv') and re-run."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/5] Creating virtual environment (.venv)..."
  python3 -m venv --system-site-packages .venv
elif [ -f ".venv/pyvenv.cfg" ]; then
  sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg 2>/dev/null || true
fi

echo "[2/5] Installing AstraCradle v2.5.1 and dependencies..."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e .

echo "[3/5] Linking global CLI launchers into ~/.local/bin..."
mkdir -p "$HOME/.local/bin"
for bin_name in astracradle astracradle-gui astracradle-wifi; do
  if [ -x "$(pwd)/.venv/bin/$bin_name" ]; then
    ln -sf "$(pwd)/.venv/bin/$bin_name" "$HOME/.local/bin/$bin_name"
  fi
done

echo "[4/5] Running self-check & App Menu setup (using Machine Update Memory)..."
./.venv/bin/python -m astracradle --self-check
MEM_DESK="$(./.venv/bin/python -c "import json,pathlib; p=pathlib.Path.home()/'.astracradle/machine_install_memory.json'; d=json.loads(p.read_text()) if p.exists() else {}; v=d.get('desktop_icon'); print('yes' if v is True else ('no' if v is False else 'ask'))" 2>/dev/null || echo ask)"
if [ "$DESKTOP_ICON" -eq 1 ]; then
  ./.venv/bin/python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method bash --install-dir "$(pwd)"
elif [ "$MEM_DESK" = "yes" ]; then
  echo "[Memory] Remembered Desktop Icon preference from previous install: YES"
  ./.venv/bin/python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method bash --install-dir "$(pwd)"
elif [ "$MEM_DESK" = "no" ]; then
  echo "[Memory] Remembered Desktop Icon preference from previous install: Menu Only"
  ./.venv/bin/python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method bash --install-dir "$(pwd)"
elif [ -t 0 ]; then
  read -r -p "Create an AstraCradle icon on your Desktop? [y/N] " ans || true
  if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
    ./.venv/bin/python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method bash --install-dir "$(pwd)"
  else
    ./.venv/bin/python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method bash --install-dir "$(pwd)"
  fi
else
  ./.venv/bin/python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method bash --install-dir "$(pwd)"
fi

echo "[5/5] Setup complete!"
if [ "$NO_LAUNCH" -eq 0 ]; then
  echo "Launching AstraCradle v2.5.1 (detached GUI — safe to close this terminal)..."
  if [ -x ".venv/bin/astracradle-gui" ] && [ "${#PASS_ARGS[@]}" -eq 0 ]; then
    nohup ./.venv/bin/astracradle-gui >/dev/null 2>&1 &
  else
    nohup ./.venv/bin/python -m astracradle "${PASS_ARGS[@]}" >/dev/null 2>&1 &
  fi
fi
