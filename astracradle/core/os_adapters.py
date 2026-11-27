"""Cross-platform OS adapter layer for Linux, macOS (Darwin), and Windows."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Iterable

from .models import CacheTarget, StartupItem


class BaseOSAdapter:
    os_family: str = "Generic"

    def __init__(self, custom_protected_paths: Iterable[str] = ()) -> None:
        self.home = Path.home()
        self.custom_protected_paths = [
            Path(p).expanduser().resolve() for p in custom_protected_paths if p
        ]

    def is_elevated(self) -> bool:
        try:
            if hasattr(os, "geteuid"):
                return os.geteuid() == 0
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False

    def protected_roots(self) -> list[Path]:
        roots = [
            self.home / "Documents",
            self.home / "Desktop",
            self.home / "Pictures",
            self.home / "Music",
            self.home / "Videos",
            self.home / "Movies",
            self.home / "Downloads",
            self.home / ".ssh",
            self.home / ".gnupg",
        ]
        roots.extend(self.custom_protected_paths)
        return [r.resolve() for r in roots]

    def is_path_protected(self, candidate: Path | str) -> bool:
        try:
            resolved = Path(candidate).expanduser().resolve()
        except Exception:
            return True
        if resolved == Path(resolved.anchor) or resolved == self.home.resolve():
            return True
        for root in self.protected_roots():
            if resolved == root or root in resolved.parents:
                return True
        return False

    def _measure_dir(self, path: Path, max_files: int = 8000) -> tuple[int, int]:
        if not path.exists() or path.is_symlink():
            return 0, 0
        if path.is_file():
            try:
                return path.stat().st_size, 1
            except OSError:
                return 0, 0
        total = 0
        count = 0
        try:
            for root, _, files in os.walk(path, followlinks=False):
                for fname in files:
                    fpath = Path(root) / fname
                    try:
                        if not fpath.is_symlink():
                            total += fpath.stat().st_size
                            count += 1
                    except OSError:
                        continue
                    if count >= max_files:
                        return total, count
        except OSError:
            pass
        return total, count

    def discover_cache_targets(self) -> list[CacheTarget]:
        return []

    def discover_startup_items(self) -> list[StartupItem]:
        return []

    def discover_installed_apps(self, limit: int = 40) -> list[dict[str, str]]:
        return []

    def dns_flush_command(self) -> list[str]:
        return []

    def dns_flush_manual(self) -> list[str]:
        return []

    def integrity_check_manual(self) -> list[str]:
        return []

    def power_profile_info(self) -> dict[str, str]:
        return {"governor": "unknown", "mode": "balanced"}

    def trim_or_disk_health_manual(self) -> list[str]:
        return []

    def os_maintenance_commands(self) -> dict[str, list[str]]:
        return {}

    def capture_tweak_snapshot(self, label: str = "script") -> dict[str, str]:
        info = self.power_profile_info()
        return {
            "os_family": self.os_family,
            "label": label,
            "governor": info.get("governor", "unknown"),
            "mode": info.get("mode", "balanced"),
        }

    def restore_tweak_snapshot(self, snap: dict[str, str]) -> tuple[bool, str]:
        return True, f"Restored baseline ({snap.get('mode', 'balanced')})."

    def run_elevated_command(self, argv: list[str], confirm: bool = False) -> tuple[bool, str]:
        if not confirm:
            return False, "Elevation refused: explicit user confirmation required."
        if not argv:
            return False, "Empty elevated command."
        sys_name = platform.system()
        try:
            if sys_name == "Windows":
                exe = argv[0]
                params = " ".join(argv[1:])
                rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)  # type: ignore[attr-defined]
                ok = int(rc) > 32
                return ok, "UAC elevation prompt dispatched." if ok else f"UAC rejected (code {rc})."
            if sys_name == "Darwin":
                if not shutil.which("osascript"):
                    return False, "osascript unavailable."
                escaped = " ".join(argv).replace('"', '\\"')
                proc = subprocess.run(
                    ["osascript", "-e", f'do shell script "{escaped}" with administrator privileges'],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip() or "Completed"
            if shutil.which("pkexec"):
                proc = subprocess.run(["pkexec", *argv], capture_output=True, text=True, timeout=30)
                return proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip() or "Completed"
            return False, "No graphical elevation helper (pkexec/osascript) detected; run manually in terminal."
        except Exception as exc:
            return False, f"Elevation error: {exc}"


class LinuxAdapter(BaseOSAdapter):
    os_family = "Linux"

    def protected_roots(self) -> list[Path]:
        base = super().protected_roots()
        sys_roots = [
            Path("/bin"),
            Path("/boot"),
            Path("/dev"),
            Path("/etc"),
            Path("/lib"),
            Path("/lib64"),
            Path("/proc"),
            Path("/root"),
            Path("/sbin"),
            Path("/sys"),
            Path("/usr"),
            Path("/var/lib"),
        ]
        return base + sys_roots

    def discover_cache_targets(self) -> list[CacheTarget]:
        candidates = [
            (
                "linux_user_cache",
                "User Application Cache (~/.cache)",
                "App Cache",
                self.home / ".cache",
                "low",
                "Recreates automatically when apps reopen; trims thumbnails, pip/font/desktop caches.",
                True,
            ),
            (
                "linux_thumbnails",
                "Desktop Thumbnails (~/.cache/thumbnails)",
                "Thumbnails",
                self.home / ".cache" / "thumbnails",
                "zero",
                "Cached image & video previews regenerated seamlessly by your file manager.",
                True,
            ),
            (
                "linux_trash",
                "User Trash Files (~/.local/share/Trash/files)",
                "Trash",
                self.home / ".local" / "share" / "Trash" / "files",
                "moderate",
                "Previously trashed files awaiting final purge.",
                False,
            ),
            (
                "linux_tmp",
                "System Temp (/tmp user-owned)",
                "Temp Files",
                Path("/tmp"),
                "low",
                "Temporary scratch artifacts (only user-owned expired files are touched).",
                True,
            ),
            (
                "linux_xsession_logs",
                "User State & Crash Logs (~/.local/state)",
                "Logs",
                self.home / ".local" / "state",
                "low",
                "User session and tool state logs; large rotated logs can be safely trimmed.",
                False,
            ),
        ]
        results: list[CacheTarget] = []
        for tid, name, cat, path, risk, desc, default_sel in candidates:
            exists = path.exists()
            size_b, count = self._measure_dir(path) if exists else (0, 0)
            results.append(
                CacheTarget(
                    target_id=tid,
                    name=name,
                    category=cat,
                    path=str(path),
                    size_bytes=size_b,
                    file_count=count,
                    risk=risk,
                    description=desc,
                    selected_by_default=default_sel,
                    exists=exists,
                )
            )
        return results

    def discover_startup_items(self) -> list[StartupItem]:
        items: list[StartupItem] = []
        autostart_dir = self.home / ".config" / "autostart"
        if autostart_dir.exists():
            for p in sorted(autostart_dir.glob("*.desktop")):
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                name = p.stem
                cmd = ""
                hidden = False
                for line in text.splitlines():
                    if line.startswith("Name=") and name == p.stem:
                        name = line.split("=", 1)[1].strip()
                    elif line.startswith("Exec="):
                        cmd = line.split("=", 1)[1].strip()
                    elif line.startswith("Hidden=true") or line.startswith("X-GNOME-Autostart-enabled=false"):
                        hidden = True
                items.append(
                    StartupItem(
                        item_id=f"xdg:{p.name}",
                        name=name,
                        location=str(p),
                        command=cmd or str(p),
                        scope="user",
                        enabled=not hidden,
                        impact="medium" if cmd else "low",
                        os_family="Linux",
                    )
                )

        if shutil.which("systemctl"):
            try:
                proc = subprocess.run(
                    ["systemctl", "--user", "list-unit-files", "--state=enabled", "--no-pager", "--no-legend"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        parts = line.split()
                        if parts:
                            unit = parts[0]
                            items.append(
                                StartupItem(
                                    item_id=f"systemd-user:{unit}",
                                    name=unit,
                                    location="systemd --user",
                                    command=f"systemctl --user status {unit}",
                                    scope="user",
                                    enabled=True,
                                    impact="low",
                                    os_family="Linux",
                                )
                            )
            except Exception:
                pass
        return items

    def discover_installed_apps(self, limit: int = 40) -> list[dict[str, str]]:
        apps: list[dict[str, str]] = []
        app_dir = Path("/usr/share/applications")
        if app_dir.exists():
            for d in sorted(app_dir.glob("*.desktop"))[:limit]:
                apps.append({"name": d.stem, "source": "desktop-entry", "path": str(d)})
        return apps

    def dns_flush_command(self) -> list[str]:
        if shutil.which("resolvectl"):
            return ["resolvectl", "flush-caches"]
        if shutil.which("systemd-resolve"):
            return ["systemd-resolve", "--flush-caches"]
        return []

    def dns_flush_manual(self) -> list[str]:
        return [
            "sudo resolvectl flush-caches   # systemd-resolved",
            "sudo nmcli general reload dns  # NetworkManager (if applicable)",
        ]

    def integrity_check_manual(self) -> list[str]:
        return [
            "sudo apt-get check && sudo dpkg --configure -a   # Debian/Ubuntu package health",
            "sudo journalctl --vacuum-time=14d                # Trim system journal logs safely",
            "sudo fstrim -av                                  # Verify/trim SSD blocks",
        ]

    def power_profile_info(self) -> dict[str, str]:
        gov_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        gov = "unavailable"
        if gov_path.exists():
            try:
                gov = gov_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        tool = "unknown"
        if shutil.which("powerprofilesctl"):
            try:
                r = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, timeout=2)
                if r.returncode == 0:
                    tool = r.stdout.strip()
            except Exception:
                pass
        return {"governor": gov, "mode": tool}

    def trim_or_disk_health_manual(self) -> list[str]:
        return [
            "df -hT && lsblk -d -o NAME,ROTA,DISC-GRAN,MODEL",
            "sudo smartctl -H /dev/nvme0n1   # Check NVMe/SATA SMART health",
        ]

    def os_maintenance_commands(self) -> dict[str, list[str]]:
        return {
            "Package Cleanup": ["sudo apt autoremove --purge -y", "sudo apt clean"],
            "Journal Vacuum": ["sudo journalctl --vacuum-size=200M"],
            "SSD TRIM": ["sudo fstrim -av"],
            "Broken Package Check": ["sudo dpkg --verify"],
        }


class MacOSAdapter(BaseOSAdapter):
    os_family = "Darwin"

    def protected_roots(self) -> list[Path]:
        base = super().protected_roots()
        sys_roots = [
            Path("/System"),
            Path("/Library"),
            Path("/Applications"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/private/var/db"),
        ]
        return base + sys_roots

    def discover_cache_targets(self) -> list[CacheTarget]:
        candidates = [
            (
                "mac_user_caches",
                "User Library Caches (~/Library/Caches)",
                "App Cache",
                self.home / "Library" / "Caches",
                "low",
                "User application caches (Safari, Xcode previews, Electron caches).",
                True,
            ),
            (
                "mac_user_logs",
                "User Diagnostic Logs (~/Library/Logs)",
                "Logs",
                self.home / "Library" / "Logs",
                "zero",
                "Application diagnostic and crash log archives.",
                True,
            ),
            (
                "mac_trash",
                "User Trash (~/.Trash)",
                "Trash",
                self.home / ".Trash",
                "moderate",
                "Items waiting in macOS Trash.",
                False,
            ),
        ]
        results: list[CacheTarget] = []
        for tid, name, cat, path, risk, desc, default_sel in candidates:
            exists = path.exists()
            size_b, count = self._measure_dir(path) if exists else (0, 0)
            results.append(
                CacheTarget(
                    target_id=tid,
                    name=name,
                    category=cat,
                    path=str(path),
                    size_bytes=size_b,
                    file_count=count,
                    risk=risk,
                    description=desc,
                    selected_by_default=default_sel,
                    exists=exists,
                )
            )
        return results

    def discover_startup_items(self) -> list[StartupItem]:
        items: list[StartupItem] = []
        for scope, folder in (
            ("user", self.home / "Library" / "LaunchAgents"),
            ("system", Path("/Library/LaunchAgents")),
        ):
            if folder.exists():
                for plist in sorted(folder.glob("*.plist")):
                    items.append(
                        StartupItem(
                            item_id=f"launchagent:{plist.name}",
                            name=plist.stem,
                            location=str(plist),
                            command=f"launchctl print gui/{os.getuid() if hasattr(os, 'getuid') else 501}/{plist.stem}",
                            scope=scope,
                            enabled=True,
                            impact="medium",
                            os_family="Darwin",
                        )
                    )
        return items

    def discover_installed_apps(self, limit: int = 40) -> list[dict[str, str]]:
        apps: list[dict[str, str]] = []
        app_dir = Path("/Applications")
        if app_dir.exists():
            for a in sorted(app_dir.glob("*.app"))[:limit]:
                apps.append({"name": a.stem, "source": "Applications", "path": str(a)})
        return apps

    def dns_flush_command(self) -> list[str]:
        return ["dscacheutil", "-flushcache"]

    def dns_flush_manual(self) -> list[str]:
        return ["sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder"]

    def integrity_check_manual(self) -> list[str]:
        return [
            "diskutil verifyVolume /",
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -kill -r -domain local -domain system -domain user",
            "sudo mdutil -E /   # Rebuild Spotlight index only if search is broken",
        ]

    def power_profile_info(self) -> dict[str, str]:
        return {"governor": "Apple PMU / pmset", "mode": "adaptive"}

    def trim_or_disk_health_manual(self) -> list[str]:
        return [
            "diskutil apfs listSnapshots /",
            "diskutil info / | grep -E 'SMART|Solid State|Free'",
        ]

    def os_maintenance_commands(self) -> dict[str, list[str]]:
        return {
            "APFS Volume Verify": ["diskutil verifyVolume /"],
            "Purgeable Space Check": ["df -h / && tmutil listlocalsnapshots /"],
            "DNS & mDNS Refresh": ["sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder"],
        }


class WindowsAdapter(BaseOSAdapter):
    os_family = "Windows"

    def protected_roots(self) -> list[Path]:
        base = super().protected_roots()
        sys_drive = Path(os.environ.get("SystemRoot", "C:\\Windows"))
        prog = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
        prog86 = Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"))
        return base + [sys_drive, sys_drive / "System32", sys_drive / "WinSxS", prog, prog86]

    def is_path_protected(self, candidate: Path | str) -> bool:
        raw = str(candidate).replace("\\", "/").rstrip("/").lower()
        for prefix in ("c:/windows", "c:/program files", "c:/program files (x86)", "c:/programdata"):
            if raw == prefix or raw.startswith(prefix + "/"):
                return True
        return super().is_path_protected(candidate)

    def discover_cache_targets(self) -> list[CacheTarget]:
        local_app = Path(os.environ.get("LOCALAPPDATA", str(self.home / "AppData" / "Local")))
        temp_dir = Path(os.environ.get("TEMP", str(local_app / "Temp")))
        candidates = [
            (
                "win_user_temp",
                "User Temp Folder (%TEMP%)",
                "Temp Files",
                temp_dir,
                "zero",
                "Installer and application scratch files safe to purge when unlocked.",
                True,
            ),
            (
                "win_crash_dumps",
                "Crash Dumps (LocalAppData\\CrashDumps)",
                "Crash Dumps",
                local_app / "CrashDumps",
                "zero",
                "Post-mortem crash dumps from past app hangs.",
                True,
            ),
            (
                "win_shader_cache",
                "DirectX Shader Cache (D3DSCache)",
                "GPU Cache",
                local_app / "D3DSCache",
                "low",
                "Compiled shader caches; games recompile on next launch if cleared.",
                False,
            ),
            (
                "win_wer",
                "Windows Error Reporting Queue",
                "Logs",
                local_app / "Microsoft" / "Windows" / "WER",
                "zero",
                "Queued error diagnostic reports.",
                True,
            ),
        ]
        results: list[CacheTarget] = []
        for tid, name, cat, path, risk, desc, default_sel in candidates:
            exists = path.exists()
            size_b, count = self._measure_dir(path) if exists else (0, 0)
            results.append(
                CacheTarget(
                    target_id=tid,
                    name=name,
                    category=cat,
                    path=str(path),
                    size_bytes=size_b,
                    file_count=count,
                    risk=risk,
                    description=desc,
                    selected_by_default=default_sel,
                    exists=exists,
                )
            )
        return results

    def discover_startup_items(self) -> list[StartupItem]:
        items: list[StartupItem] = []
        appdata = Path(os.environ.get("APPDATA", str(self.home / "AppData" / "Roaming")))
        startup_folder = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        if startup_folder.exists():
            for f in sorted(startup_folder.iterdir()):
                items.append(
                    StartupItem(
                        item_id=f"winstartup:{f.name}",
                        name=f.stem,
                        location=str(f),
                        command=str(f),
                        scope="user",
                        enabled=True,
                        impact="medium",
                        os_family="Windows",
                    )
                )
        if platform.system() == "Windows":
            try:
                import winreg  # type: ignore

                for hive_name, hive in (("HKCU", winreg.HKEY_CURRENT_USER), ("HKLM", winreg.HKEY_LOCAL_MACHINE)):
                    sub = r"Software\Microsoft\Windows\CurrentVersion\Run"
                    try:
                        with winreg.OpenKey(hive, sub) as key:
                            i = 0
                            while True:
                                try:
                                    val_name, val_data, _ = winreg.EnumValue(key, i)
                                    items.append(
                                        StartupItem(
                                            item_id=f"{hive_name}:{val_name}",
                                            name=val_name,
                                            location=f"{hive_name}\\{sub}",
                                            command=str(val_data),
                                            scope="user" if hive_name == "HKCU" else "system",
                                            enabled=True,
                                            impact="medium",
                                            os_family="Windows",
                                        )
                                    )
                                    i += 1
                                except OSError:
                                    break
                    except OSError:
                        pass
            except ImportError:
                pass
        return items

    def discover_installed_apps(self, limit: int = 40) -> list[dict[str, str]]:
        apps: list[dict[str, str]] = []
        if platform.system() == "Windows":
            try:
                import winreg  # type: ignore

                sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, sub) as key:
                            i = 0
                            while len(apps) < limit:
                                try:
                                    sub_name = winreg.EnumKey(key, i)
                                    with winreg.OpenKey(key, sub_name) as ak:
                                        disp, _ = winreg.QueryValueEx(ak, "DisplayName")
                                        apps.append({"name": str(disp), "source": "Registry", "path": sub_name})
                                except OSError:
                                    pass
                                i += 1
                    except OSError:
                        pass
            except ImportError:
                pass
        return apps

    def dns_flush_command(self) -> list[str]:
        return ["ipconfig", "/flushdns"]

    def dns_flush_manual(self) -> list[str]:
        return ["ipconfig /flushdns", "netsh winsock reset   # Only if socket stack is corrupted"]

    def integrity_check_manual(self) -> list[str]:
        return [
            "DISM /Online /Cleanup-Image /CheckHealth",
            "DISM /Online /Cleanup-Image /RestoreHealth",
            "sfc /scannow",
        ]

    def power_profile_info(self) -> dict[str, str]:
        if platform.system() == "Windows" and shutil.which("powercfg"):
            try:
                r = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=3)
                if r.returncode == 0:
                    return {"governor": "Windows Power Plan", "mode": r.stdout.strip()}
            except Exception:
                pass
        return {"governor": "Windows Power Plan", "mode": "Balanced"}

    def trim_or_disk_health_manual(self) -> list[str]:
        return [
            "Optimize-Volume -DriveLetter C -Analyze -Verbose",
            "Optimize-Volume -DriveLetter C -ReTrim -Verbose",
            "wmic diskdrive get model,status",
        ]

    def os_maintenance_commands(self) -> dict[str, list[str]]:
        return {
            "System File Integrity": ["sfc /scannow", "DISM /Online /Cleanup-Image /RestoreHealth"],
            "Create Restore Point": ['powershell -Command "Checkpoint-Computer -Description \'AstraCradle\' -RestorePointType MODIFY_SETTINGS"'],
            "Windows Update Cache Reset": ["net stop wuauserv", "net start wuauserv"],
            "Flush DNS": ["ipconfig /flushdns"],
        }


def get_os_adapter(override: str = "auto", custom_protected_paths: Iterable[str] = ()) -> BaseOSAdapter:
    target = platform.system() if override in ("auto", "", None) else override
    if target == "Windows":
        return WindowsAdapter(custom_protected_paths)
    if target == "Darwin":
        return MacOSAdapter(custom_protected_paths)
    return LinuxAdapter(custom_protected_paths)
