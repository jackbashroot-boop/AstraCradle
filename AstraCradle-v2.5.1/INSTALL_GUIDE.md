# AstraCradle v2.5.1 — Beginner's Step-by-Step Installation Guide

Welcome! This guide is written for **anyone—even if you have never installed software from GitHub or a ZIP file before**. Just follow the section for your computer (**Windows**, **Mac**, or **Linux**).

> **Good to know before you start:**
> - AstraCradle runs **100% on your own computer**. Nothing is uploaded to the cloud.
> - Before AstraCradle checks any files on your PC, it will pop up a permission window asking what you want to allow.
> - Once installed, you open AstraCradle from your **Start Menu / Applications Menu** just like Steam or Discord—with **no black terminal window**.

---

## Step 0: Get AstraCradle onto Your Computer

Pick **one** of these two easy ways:

### Way 1: Download the ZIP File from GitHub (Easiest — No Commands Needed)
1. Open the AstraCradle GitHub page in your web browser.
2. Click the green **`<> Code`** button near the top right (or click **Releases** on the right side).
3. Click **Download ZIP** (or download `AstraCradle-v2.5.1-bundle.zip` from Releases).
4. Wait until the download finishes—the file will be in your **Downloads** folder.

### Way 2: Clone with Git (Optional)
If you already use Git, clone the repository to your home folder and open the folder you just downloaded.

---

## Option A: Installing on Windows 10 or Windows 11

### 1. Unzip (Extract) the Downloaded File
1. Open **File Explorer** (the yellow folder icon on your bottom bar) and click **Downloads** on the left.
2. Find the file named `AstraCradle-v2.5.1-bundle.zip` (or `AstraCradle-main.zip`).
   - **Important:** Do **not** double-click inside the ZIP and run files from there! You must **Extract** it first.
3. **Right-click** the ZIP file and click **Extract All...**.
4. A small window asks where to put the files. Click **Extract** (or change the folder to `C:\Users\YourName\AstraCradle`).
5. A new regular folder opens automatically. Double-click into it until you see **`Install_AstraCradle.bat`**.

### 2. Run the 1-Click Installer
1. Double-click **`Install_AstraCradle.bat`**.
2. **If Windows shows a blue box saying "Windows protected your PC" (SmartScreen):**
   - Click **More info**, then click **Run anyway**. *(This appears because AstraCradle is a new indie app without a paid Microsoft certificate.)*
3. A setup window opens and automatically:
   - Installs **Python** automatically using Windows (`winget`) if your PC doesn't have it yet.
     - *If `winget` isn't available and it asks you to install Python manually:* go to [python.org/downloads](https://www.python.org/downloads/), click the yellow **Download Python** button, run it, and **CHECK THE BOX at the bottom that says "Add python.exe to PATH"** before clicking **Install Now**. Then double-click `Install_AstraCradle.bat` again.
   - Creates a clean private folder (`.venv`) so nothing else on your Windows PC is touched.
   - Checks all 7 safety systems (`Self-Heal Check`).
   - Adds **AstraCradle** with its icon to your **Start Menu** and **Desktop**.
   - Opens **AstraCradle** automatically!

### 3. Opening AstraCradle Later
- Press the **Windows key** on your keyboard, type **AstraCradle**, and press **Enter**—or double-click the **AstraCradle** icon on your Desktop.
- Look at the **bottom-right corner** of your screen (near the clock, or click the small **`^`** up-arrow): you will see the **AstraCradle tray icon**. Right-click it anytime to **Open AstraCradle**, **Run Equilibrium Scan**, or **Quit AstraCradle**.
- Want closing the window (`X`) or minimizing (`—`) to tuck AstraCradle into the bottom-right tray? Open **Settings & Updates** inside AstraCradle and flip **Minimize to System Tray (Hide from Taskbar)** to **ON**.

---

## Option B: Installing on macOS (MacBook / iMac / Mac mini)

### 1. Open the Downloaded Folder
1. Click **Finder** (the smiling face icon in your Dock) and click **Downloads** on the left.
2. If you see `AstraCradle-v2.5.1-bundle.zip` and it hasn't unzipped automatically, **double-click** it. Mac will turn it into a blue folder.
3. Open that folder until you see **`Install_AstraCradle.command`**.

### 2. Run the 1-Click Installer
1. **Do NOT just double-click** the first time (macOS blocks downloaded scripts by default). Instead:
   - **Right-click** (or hold the `Control` key and click) **`Install_AstraCradle.command`**, then click **Open**.
   - If Mac shows an *"unidentified developer"* popup, click **Open** (or go to **System Settings -> Privacy & Security**, scroll down, and click **Open Anyway**).
2. **If a message says Python 3.10+ is missing:**
   - Go to [python.org/downloads/macos](https://www.python.org/downloads/macos/), download and install the latest macOS installer (double-click `.pkg` -> Continue -> Install), then **Right-click -> Open** `Install_AstraCradle.command` again.
3. The installer sets up AstraCradle cleanly inside `.venv`, creates **`AstraCradle.app`** inside your **Applications** folder (`~/Applications`), and launches AstraCradle automatically!

### 3. Opening AstraCradle Later
- Press **`Cmd + Space`** (Spotlight), type **`AstraCradle`**, and press **Return**—or open **Finder -> Go -> Home -> Applications -> AstraCradle**.
- Look in your **top-right Mac menu bar** (near Wi-Fi / Clock) for the **AstraCradle** icon to quickly open, scan, or quit.

---

## Option C: Installing on Linux (Ubuntu, Linux Mint, Debian, Fedora, Arch)

### 1. Install Python Tk Support (1-Time Prep)
Most Linux systems already have Python, but some don't include the window toolkit (`tkinter`) or `unzip` by default. Open your **Terminal** app (press `Ctrl + Alt + T` on Ubuntu/Mint) and paste the line for your Linux type, press **Enter**, and type your password when asked:

- **Ubuntu / Linux Mint / Debian / Pop!_OS:**
  ```bash
  sudo apt update && sudo apt install -y python3 python3-venv python3-tk unzip
  ```
- **Fedora:**
  ```bash
  sudo dnf install -y python3 python3-tkinter unzip
  ```
- **Arch / Manjaro / CachyOS / EndeavourOS:**
  ```bash
  sudo pacman -S --needed python tk unzip
  ```

### 2. Unzip & Install AstraCradle
Copy and paste this into Terminal (works in **bash**, **zsh**, and **fish**):

```bash
mkdir -p "$HOME/AstraCradle"
unzip -oq "$HOME/Downloads/AstraCradle-v2.5.1-bundle.zip" -d "$HOME/AstraCradle"
bash "$HOME/AstraCradle/AstraCradle-v2.5.1/install_astracradle.sh"
```

*(If you downloaded `AstraCradle-main.zip` using GitHub's green Code button instead of Releases, replace `AstraCradle-v2.5.1-bundle.zip` with `AstraCradle-main.zip` and `AstraCradle-v2.5.1` with `AstraCradle-main`.)*

### 3. Opening AstraCradle Later
- Press your **Super / Windows key**, type **AstraCradle**, and click its icon in your **Applications Menu** (just like Steam or Discord—no terminal stays open!).
- A tray icon also appears in your desktop notification bar (bottom-right on Cinnamon/KDE/XFCE, top-right on GNOME with AppIndicator).

---

## How Updating Works (GitHub First, Then Local)

1. Open **AstraCradle** and click **Settings & Updates** in the left sidebar.
2. Under **Seamless Auto-Updater**, AstraCradle checks your configured **Default GitHub Repo** online **first**.
3. If you're offline or no new GitHub release is posted yet, AstraCradle automatically checks your local **`~/Downloads`** and **`~/.astracradle/updates`** folders for any newer `AstraCradle-vX.Y.Z-bundle.zip`.
4. Click **Check Updates Now** anytime to check and apply updates while keeping all your settings and rollback history intact.

---

## How to Completely Uninstall (Zero Damage)

If you ever want to remove AstraCradle completely:

- **Easiest way (all OSes):** Open **AstraCradle -> Settings & Updates** and click **Uninstall AstraCradle...**.
- **Or use the uninstaller script inside your AstraCradle folder:**
  - **Windows:** Double-click `Uninstall_AstraCradle.bat`
  - **macOS:** Right-click -> Open `Uninstall_AstraCradle.command`
  - **Linux:** Run `bash ~/AstraCradle/AstraCradle-v2.5.1/uninstall_astracradle.sh`

During uninstall, AstraCradle automatically:
1. Reverts any active performance tweaks back to **Balanced Daily**.
2. Offers to restore any files you placed in Quarantine back to where they came from.
3. Removes its shortcuts and `.venv` environment.
4. **Explicitly asks** whether you want to keep or permanently delete all saved AstraCradle profiles, logs, and settings—leaving your personal files and operating system completely untouched.
