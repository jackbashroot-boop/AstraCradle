#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "========================================================"
echo "  AstraCradle Clean Uninstaller (Zero-Damage Removal)"
echo "========================================================"

PY_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PY_BIN="./.venv/bin/python"
fi

"$PY_BIN" -m astracradle --uninstall --uninstall-package

if [ -d ".venv" ]; then
  echo "Removing isolated AstraCradle .venv directory..."
  rm -rf ".venv"
fi

echo "AstraCradle uninstall complete. Your personal files and OS were not modified."
