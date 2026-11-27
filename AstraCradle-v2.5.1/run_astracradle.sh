#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ "$#" -gt 0 ]; then
  if [ -x ".venv/bin/python" ]; then
    exec ./.venv/bin/python -m astracradle "$@"
  fi
  exec python3 -m astracradle "$@"
fi
if [ -x "dist/AstraCradle/AstraCradle" ]; then
  nohup ./dist/AstraCradle/AstraCradle >/dev/null 2>&1 &
  exit 0
fi
if [ -x ".venv/bin/astracradle-gui" ]; then
  nohup ./.venv/bin/astracradle-gui >/dev/null 2>&1 &
  exit 0
fi
if [ -x ".venv/bin/python" ]; then
  nohup ./.venv/bin/python -m astracradle >/dev/null 2>&1 &
  exit 0
fi
nohup python3 -m astracradle >/dev/null 2>&1 &
