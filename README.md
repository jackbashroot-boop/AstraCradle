# AstraCradle — Local PC Optimizer, System Diagnostics & Self-Healing Studio (v2.5.0)

**Free, 100% local cross-platform PC optimizer, system diagnostics monitor, CPU affinity manager, thermal & DPC latency analyzer, and safe cleaner for Windows 10/11, macOS, and Linux.**

> **Supported Platforms:** Windows 10 / 11 · macOS (Apple Silicon & Intel) · Linux (Ubuntu, Debian, Fedora, Arch, Mint, CachyOS) · **100% Local Native Desktop GUI (Zero Cloud / Zero Web App)**

**AstraCradle** is a modular, 100% local native desktop studio (zero web UI) built for **Windows**, **macOS**, and **Linux**. Designed to be effortless for beginners and deeply capable for power users, AstraCradle v2.5.0 includes 1-click setup, responsive scaling up to **4K (`3840x2160`)**, smooth vertical-only scrolling, an explicit **first-load permission-gated Full PC Baseline Discovery**, an interactive 3-step **Easy Guide**, live **GPU/VRAM & thermal throttling** telemetry, a **multi-resolution telemetry history graph (`60s / 5m / 30m / 1hr / 30d`)**, **PC Component Library & savable CPU-affinity/I/O presets** (Intel Hybrid P/E cores & AMD dual-CCD X3D), **customizable multi-stage Optimization Pipelines + local file/CLI hook connectors**, **ProBalance Foreground Boost & Auto-Reverted App Profiles**, **Interrupt Affinity & DPC Latency Guard**, **Interactive Draft-vs-Applied GPU Fan Curve Editor**, **Workload-Aware Welford Thermal SPC Baselines**, **Autoruns Startup/Task/Service Audit + Ghost Driver Cleanup**, **Evidence & Confidence % diagnostic scoring**, **1-click Everyday PC Glitch troubleshooters**, **Dry-Run file diff previews + instant Toast Undo + optional zero-pass shredder**, **48h driver/update crash blame**, **`.astracradle-pack.json` SHA-256 pack sharing**, automated native installer generators (`.iss`/`.dmg`/`.AppImage`), bottom-right system tray + minimize-to-tray support, and a zero-damage Clean Uninstaller.

| Feature | Windows 10 / 11 | macOS | Linux |
|---|---|---|---|
| **1-Click Installer** | `Install_AstraCradle.bat` | `Install_AstraCradle.command` | `install_astracradle.sh` |
| **App Menu / Start Menu GUI** | `.lnk` (`astracradle-gui.exe`, no console) | `~/Applications/AstraCradle.app` | `.desktop` + hicolor icon (`Terminal=false`) |
| **System Tray & Minimize-to-Tray** | Bottom-right notification area | Top-right menu bar | Desktop panel / AppIndicator |
| **Safe Uninstaller** | `Uninstall_AstraCradle.bat` | `Uninstall_AstraCradle.command` | `uninstall_astracradle.sh` |

---

## Brand Identity, Animated Artwork & Accessibility

- **Verified Original Name:** `AstraCradle` — untaken across software & web. Evokes a Dyson-sphere containment lattice cradling a dim star.
- **Custom Static & Animated Artwork:**
  - `assets/astracradle_emblem.png` — Intricate brass-lattice Dyson sphere cradling a dim amber-cyan dwarf star suspended inside dark titanium gyroscope rings.
  - `assets/astracradle_animated.gif` & `assets/astracradle_badge_animated.gif` — Multi-frame dynamic animated GIFs played live inside the native UI.
  - `assets/bg_command.png` & `assets/bg_vault.png` — Subtle Dyson blueprint & hex-lattice page backdrops.
- **Color Schemes & Accessibility:** **Obsidian Horology** (dark titanium), **Atelier Alabaster** (daytime light mode), **High Contrast** (WCAG AAA contrast mode), UI text scaling (`Auto (Monitor)` / `90%–175%` up to 4K `3840x2160`), `en`/`es`/`fr` localization, **Hover Tooltips**, and keyboard shortcuts (`F1` / `Ctrl+F` Help Search, `Ctrl+R` Scan, `Ctrl+1..0` Navigation).

---

## Effortless Setup, Daily Use & Clean Uninstall

### 1-Click Setup (No Computer Knowledge Required)
> **New to computers or GitHub?** Read the super detailed, step-by-step **[Beginner's Installation Guide (`INSTALL_GUIDE.md`)](INSTALL_GUIDE.md)** covering every click on **Windows**, **macOS**, and **Linux**.

- **Windows:** Extract the ZIP folder, then double-click `Install_AstraCradle.bat` (creates an isolated `.venv`, installs dependencies + `astracradle-gui.exe`, creates zero-console Desktop & Start Menu shortcuts, and launches AstraCradle).
- **macOS:** Extract the ZIP folder, right-click `Install_AstraCradle.command` -> **Open** (builds `~/Applications/AstraCradle.app` and launches AstraCradle).
- **Linux:** Run `./install_astracradle.sh` (installs `astracradle-gui`, `.desktop` menu shortcuts + hicolor icon, and launches AstraCradle).

### Install Directly From GitHub (fish / bash)
```fish
git clone https://github.com/<your-username>/AstraCradle.git
cd AstraCradle
# Linux / macOS:
bash ./install_astracradle.sh
# Windows PowerShell:
# .\Install_AstraCradle.bat
```

### Updates (Checks GitHub Repo First, Then Local)
- Under **Settings & Updates**, AstraCradle checks your configured **Default GitHub Repo** (`update_repo_url` / cloned `origin`) **first**, and automatically falls back to local staged manifests or `AstraCradle-v*.zip` bundles in `~/.astracradle/updates` and `~/Downloads`.
- From terminal: `astracradle --check-updates` (or `astracradle --set-update-repo https://github.com/<your-username>/AstraCradle`).

### System Tray & Minimize-to-Tray
- AstraCradle includes a cross-OS bottom-right system tray / menu-bar icon (`Open AstraCradle`, `Run Equilibrium Scan`, `Quit AstraCradle`).
- Open **Settings & Updates** to toggle **Bottom-Right System Tray Icon** and **Minimize to System Tray (Hide from Taskbar)** independently.

### Simple Daily Use
1. Click **Easy Guide** in the top bar anytime for a plain-English 3-step walkthrough.
2. Click **Deep Scan & Playbook** to get a 0–100 score and step-by-step recommendations.
3. Open **AI & Process Hunter** to spot and turn off unused local AI chatbots or dormant background helpers.
4. Open **Toggleable Scripts** to flip **Gaming Max-FPS**, **3D Benchmarking & Rendering**, **Massive Data & Code Compilation**, or **Silence Idle AI Chatbots** ON/OFF with a single switch.

### Zero-Damage Clean Uninstall
- Click **Uninstall AstraCradle...** inside **Settings & Updates**, run `Uninstall_AstraCradle.bat` / `Uninstall_AstraCradle.command` / `./uninstall_astracradle.sh`, or run `astracradle --uninstall`.
- Automatically reverts any active toggle scripts back to `Balanced Daily`, offers to restore any quarantined files back to their original locations, removes AstraCradle shortcuts & isolated `.venv`, and **explicitly asks whether to delete ALL AstraCradle profiles, settings, logs, and saved data** (`~/.astracradle`) — leaving your personal files and OS untouched.

---

## CLI & Testing

```bash
cd astracradle
pip install -e .
astracradle                  # Launch native local GUI
astracradle --self-check     # 7-invariant self-heal & forensic self-audit
astracradle --diagnose       # 8-subsystem scan & Playbook
astracradle --uninstall      # Interactive safe uninstaller
pytest -v tests/
```
