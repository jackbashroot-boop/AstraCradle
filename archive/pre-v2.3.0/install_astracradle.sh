#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

NO_LAUNCH=0
PASS_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--no-launch" ]]; then
    NO_LAUNCH=1
  else
    PASS_ARGS+=("$arg")
  fi
done

echo "============================================================"
echo "  AstraCradle v1.0.0 — Dyson-Core OS Equilibrium Studio"
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
  echo "[1/4] Creating isolated virtual environment (.venv)..."
  python3 -m venv .venv
fi

echo "[2/4] Installing AstraCradle v1.0.0 and dependencies..."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e .

echo "[3/4] Running 7-invariant self-check & creating Desktop/Menu shortcuts..."
./.venv/bin/python -m astracradle --self-check
./.venv/bin/python -m astracradle --install-shortcuts

echo "[4/4] Setup complete!"
if [ "$NO_LAUNCH" -eq 0 ]; then
  echo "Launching AstraCradle v1.0.0 (detached GUI — safe to close this terminal)..."
  if [ -x ".venv/bin/astracradle-gui" ] && [ "${#PASS_ARGS[@]}" -eq 0 ]; then
    nohup ./.venv/bin/astracradle-gui >/dev/null 2>&1 &
  else
    nohup ./.venv/bin/python -m astracradle "${PASS_ARGS[@]}" >/dev/null 2>&1 &
  fi
fi
