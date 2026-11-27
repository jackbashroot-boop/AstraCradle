#!/usr/bin/env fish
# AstraCradle v2.5.1 — Native Fish Shell Full Installer (Linux / CachyOS / Arch / Ubuntu / Fedora)
cd (dirname (status -f))

set -l no_launch 0
set -l desktop_icon 0
set -l pass_args
for arg in $argv
    if test "$arg" = "--no-launch"
        set no_launch 1
    else if test "$arg" = "--desktop-icon"
        set desktop_icon 1
    else
        set -a pass_args $arg
    end
end

echo "============================================================"
echo "  AstraCradle v2.5.1 — Fish Full Installer & Companion Setup"
echo "============================================================"

if not command -q python3
    echo "[ERROR] python3 missing. Install Python 3.10+ & tk first."
    exit 1
end

if not python3 -c "import tkinter" 2>/dev/null; or not python3 -c "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')" 2>/dev/null
    echo "[1/6] Installing Python/Tk, native tray & system dependencies..."
    if command -q pacman
        sudo pacman -Syu --needed --noconfirm python python-pip tk openssl bluez bluez-utils networkmanager iw android-tools scrcpy flatpak curl libayatana-appindicator python-gobject
    else if command -q apt-get
        sudo apt-get update; and sudo apt-get install -y python3 python3-venv python3-tk openssl bluez rfkill network-manager iw android-tools-adb scrcpy flatpak curl gir1.2-ayatanaappindicator3-0.1 python3-gi
    else if command -q dnf
        sudo dnf install -y python3 python3-tkinter openssl bluez NetworkManager iw android-tools flatpak curl libayatana-appindicator-gtk3 python3-gobject
    end
end

if not test -x .venv/bin/python
    echo "[2/6] Creating virtual environment (.venv)..."
    python3 -m venv --system-site-packages .venv
else if test -f .venv/pyvenv.cfg
    sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg 2>/dev/null; or true
end

echo "[3/6] Installing AstraCradle v2.5.1..."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e .

echo "[4/6] Linking global Fish/CLI launchers into ~/.local/bin..."
mkdir -p ~/.local/bin
for bin_name in astracradle astracradle-gui astracradle-wifi
    if test -x (pwd)/.venv/bin/$bin_name
        ln -sf (pwd)/.venv/bin/$bin_name ~/.local/bin/$bin_name
    end
end
if not contains ~/.local/bin $PATH
    fish_add_path ~/.local/bin
end

echo "[5/6] Running full self-check & App Menu setup (using Machine Update Memory)..."
./.venv/bin/python -m astracradle --self-check
set -l mem_desk (./.venv/bin/python -c "import json,pathlib; p=pathlib.Path.home()/'.astracradle/machine_install_memory.json'; d=json.loads(p.read_text()) if p.exists() else {}; v=d.get('desktop_icon'); print('yes' if v is True else ('no' if v is False else 'ask'))" 2>/dev/null)
if test $desktop_icon -eq 1
    ./.venv/bin/python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method fish --install-dir (pwd)
else if test "$mem_desk" = "yes"
    echo "[Memory] Remembered Desktop Icon preference from previous install: YES"
    ./.venv/bin/python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method fish --install-dir (pwd)
else if test "$mem_desk" = "no"
    echo "[Memory] Remembered Desktop Icon preference from previous install: Menu Only (No Desktop Icon)"
    ./.venv/bin/python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method fish --install-dir (pwd)
else if isatty stdin
    read -l -P "Create an AstraCradle icon on your Desktop? [y/N] " ans
    if string match -qr '^[Yy]' -- "$ans"
        ./.venv/bin/python -m astracradle --install-shortcuts --desktop-icon --reapply-memory --install-method fish --install-dir (pwd)
    else
        ./.venv/bin/python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method fish --install-dir (pwd)
    end
else
    ./.venv/bin/python -m astracradle --install-shortcuts --no-desktop-icon --reapply-memory --install-method fish --install-dir (pwd)
end

if test $no_launch -eq 0
    echo "[6/6] Launching AstraCradle..."
    if test -x .venv/bin/astracradle-gui; and test (count $pass_args) -eq 0
        nohup ./.venv/bin/astracradle-gui >/dev/null 2>&1 &
    else
        nohup ./.venv/bin/python -m astracradle $pass_args >/dev/null 2>&1 &
    end
end
