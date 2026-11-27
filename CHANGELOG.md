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

### Hermes AI Chat Delete & Stability Fixes
- Added **Delete Chat** button next to **+ New Chat** in Hermes AI.
- Fixed crash when deleting all chat sessions including `"default"` by deferring widget rebuilds safely after button/dialog callbacks and guarding empty `"default"` deletes.
- Added `BUG_REPORTS.txt` in the repository root for tracking user/QA bug reports and regression tests.

### In-App GitHub Updater & Settings Preservation
- Updated `ModuleAndUpdateManager.pull_from_github_and_update()` to pull `.git` clones directly (`git fetch` / `git pull` + editable pip sync) before zip fallback.
- Preserves live user settings (`config.json`, Hermes memories/library, AstraShield rules) across updates and automatically restarts into the newly updated build.

### AstraShield Smart TV & Ad-Blocking Improvements
- Expanded Samsung Tizen ad & ACR blocklist (`ads.samsungads.com`, `samsungtvads.com`, `acr-us-prd.samsungcloud.tv`, `log-config.samsungacr.com`) while keeping essential Samsung TV app/store/clock domains allowlisted.
- Added UDP + TCP port `53 -> 5353` redirect (`nftables` / `iptables`) for LAN TV Mode and Pass-Through / clean-restore mode.
