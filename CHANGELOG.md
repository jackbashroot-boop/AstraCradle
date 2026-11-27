# AstraCradle Changelog

All notable updates and fixes to **AstraCradle** (`v2.5.1+`) are documented here.

---

## [2.5.1] — Latest Updates

### Full Clean Restart & Changelog Link on Updates
- **Full Process & Taskbar/Tray Shutdown on Restart**: `restart_application()` closes the desktop widget, hides and stops the system tray/taskbar icon (`TrayController.stop()`), releases the single-instance lock, waits until the old PID has completely exited, and only then launches the fresh AstraCradle instance **after** the update has 100% finished.
- **Repo Changelog Link**: GitHub update prompts point directly to `CHANGELOG.md` in the repository so you can inspect changes on demand instead of reading commit dumps in popups.

### Bluetooth Device & Battery Tracker (< 20% Important Alerts)
- Tracks Bluetooth devices (`bluetoothctl`, `upower`, `/sys/class/power_supply`, macOS `system_profiler`) and persists battery history in `bluetooth_battery_tracker.json`.
- Polls automatically every 60s and triggers an **important alert toast + desktop tray notification** whenever any Bluetooth device battery drops below **20%** (with recharge hysteresis at 25%).
- Displays live and tracked Bluetooth device battery badges in **Clean & Protect → Smart TV, Bluetooth & Network Devices** and `python3 -m astracradle --scan-tv`.

### Hermes AI On-The-Fly Shell & Preference Adaptation (Fish / Zsh / Bash / Standing Rules)
- Added `astra_adapt_preferences` tool and `hermes_preferred_shell` config so Hermes adapts on the fly when told to switch shells (e.g., *"use Fish instead of bash for all commands"*) or given new standing rules, persisting them into `config.json`, `USER.md`, and `MEMORY.md`.
- Updated `shell_exec` and `_run_sudo_prompted` to execute directly through the active preferred shell (`fish -c`, `zsh -c`, `bash -c`, etc.) instead of hardcoded `/bin/sh`, plus turn-0 auto-grounding so Hermes applies live shell/rule preferences immediately instead of refusing.

### Hermes AI Full PC File Catalog, Passive Idle Scanner & Pre-Activation Guard
- Added `PcFileGuardian` (`astracradle/core/file_guardian.py`) and the `astra_file_guardian` Hermes tool so Hermes can catalog PC files/drives, capture full system/hardware/package/port inventory, passively scan during idle time (`cpu < 25%` and not gaming), verify incoming files (`Downloads`/`Desktop`/`autostart`/`systemd user`), and warn the user **before** risky files or startup units are turned on.
- Updated Hermes prompts, tool routing, short-retry intent inheritance (`"Try again"` / `"Go ahead"`), and universal turn-0/fallback grounding across Wi-Fi, diagnostics, startup, remote tasks, Smart TV/LAN, and file catalog prompts so Astra/Hermes runs live AstraCradle tools directly instead of claiming it lacks OS/hardware access.

### Hermes AI Chat Delete, Live Working Indicator & Stability Fixes
- Added **Delete Chat** button next to **+ New Chat** in Hermes AI.
- Added an animated `Working... (Xs)` indicator (cycling `.` / `..` / `...` + elapsed timer) in both the active chat bubble and a live status bar above the input box, and preserved live streaming/step updates even when switching AstraCradle menus or tabs while Hermes is working.
- Fixed crash when deleting all chat sessions including `"default"` by deferring widget rebuilds safely after button/dialog callbacks and guarding empty `"default"` deletes.
- Added `BUG_REPORTS.txt` in the repository root for tracking user/QA bug reports and regression tests.

### In-App GitHub Updater & Settings Preservation
- Updated `ModuleAndUpdateManager.pull_from_github_and_update()` to pull `.git` clones directly (`git fetch` / `git pull` + editable pip sync) before zip fallback.
- Preserves live user settings (`config.json`, Hermes memories/library, AstraShield rules) across updates and automatically restarts into the newly updated build.

### AstraShield Smart TV & Ad-Blocking Improvements
- Expanded Samsung Tizen ad & ACR blocklist (`ads.samsungads.com`, `samsungtvads.com`, `acr-us-prd.samsungcloud.tv`, `log-config.samsungacr.com`) while keeping essential Samsung TV app/store/clock domains allowlisted.
- Added UDP + TCP port `53 -> 5353` redirect (`nftables` / `iptables`) for LAN TV Mode and Pass-Through / clean-restore mode.
