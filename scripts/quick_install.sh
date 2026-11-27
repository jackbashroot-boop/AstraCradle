#!/usr/bin/env bash
set -euo pipefail

# AstraCradle 1-Line Remote Installer (Linux & macOS)
# Usage: curl -fsSL https://raw.githubusercontent.com/jackbashroot-boop/AstraCradle/main/scripts/quick_install.sh | bash

REPO_OWNER="jackbashroot-boop"
REPO_NAME="AstraCradle"
BRANCH="main"
TAR_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}/archive/refs/heads/${BRANCH}.tar.gz"
INSTALL_ROOT="${HOME}/AstraCradle"

echo "============================================================"
echo "  AstraCradle 1-Step Installer (${REPO_OWNER}/${REPO_NAME})"
echo "============================================================"

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo "[1/3] Downloading latest AstraCradle from GitHub..."
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$TAR_URL" -o "$TMP_DIR/astracradle.tar.gz"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$TMP_DIR/astracradle.tar.gz" "$TAR_URL"
else
  echo "[ERROR] curl or wget is required to download AstraCradle."
  exit 1
fi

echo "[2/3] Extracting into ${INSTALL_ROOT}..."
tar -xzf "$TMP_DIR/astracradle.tar.gz" -C "$TMP_DIR"
EXTRACTED_DIR="$(find "$TMP_DIR" -maxdepth 1 -mindepth 1 -type d | head -n 1)"
if [ -z "$EXTRACTED_DIR" ] || [ ! -d "$EXTRACTED_DIR" ]; then
  echo "[ERROR] Failed to extract AstraCradle archive."
  exit 1
fi

mkdir -p "$INSTALL_ROOT"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude '.venv' --exclude '__pycache__' "$EXTRACTED_DIR/" "$INSTALL_ROOT/"
else
  cp -a "$EXTRACTED_DIR/." "$INSTALL_ROOT/"
fi

echo "[3/3] Running AstraCradle installer..."
cd "$INSTALL_ROOT"
OS_NAME="$(uname -s)"
if [ "$OS_NAME" = "Darwin" ]; then
  chmod +x Install_AstraCradle.command
  exec bash ./Install_AstraCradle.command
elif command -v fish >/dev/null 2>&1 && [ -f "install_astracradle.fish" ] && [ -f "/etc/os-release" ] && grep -qi "cachyos" /etc/os-release 2>/dev/null; then
  chmod +x install_astracradle.fish
  exec fish ./install_astracradle.fish
else
  chmod +x install_astracradle.sh
  exec bash ./install_astracradle.sh
fi
