"""Idiot-proof cross-platform Setup, Shortcut Installer, and Zero-Damage Uninstaller."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from .config import AppConfig, ConfigManager
from .journal import ForensicJournal
from .os_adapters import BaseOSAdapter
from .rollback import RollbackManager
from .scripts_engine import ScriptsEngine


@dataclass
class UninstallSummary:
    ok: bool
    reverted_scripts: int = 0
    restored_quarantine_entries: int = 0
    removed_shortcuts: list[str] = field(default_factory=list)
    deleted_data_dir: bool = False
    uninstalled_package: bool = False
    notes: list[str] = field(default_factory=list)


class LifecycleManager:
    """Handles zero-knowledge installation, desktop shortcuts, and clean uninstallation."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
        scripts_engine: ScriptsEngine | None = None,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.rollback_mgr = rollback_mgr
        self.journal = journal
        self.scripts_engine = scripts_engine

    @staticmethod
    def _is_safe_astracradle_dir(target: Path) -> bool:
        resolved = target.expanduser().resolve()
        home = Path.home().resolve()
        if resolved in (Path(resolved.anchor), home, home / "Documents", home / "Desktop", home / "Downloads"):
            return False
        # Must either be named .astracradle / contain state/config.json or be a test temp dir
        has_marker = (resolved / "state").exists() or "astracradle" in resolved.name.lower()
        return has_marker

    def shortcut_paths(self, os_override: str | None = None) -> list[Path]:
        sys_name = os_override or platform.system()
        home = Path.home()
        paths: list[Path] = []
        if sys_name == "Linux":
            paths.extend(
                [
                    home / ".local" / "share" / "applications" / "astracradle.desktop",
                    home / "Desktop" / "AstraCradle.desktop",
                    home / ".config" / "autostart" / "astracradle.desktop",
                ]
            )
            for sz in ("32x32", "64x64", "128x128", "256x256"):
                paths.append(home / ".local" / "share" / "icons" / "hicolor" / sz / "apps" / "astracradle.png")
        elif sys_name == "Darwin":
            paths.extend(
                [
                    home / "Applications" / "AstraCradle.app",
                    home / "Applications" / "AstraCradle.command",
                    home / "Desktop" / "AstraCradle.command",
                    home / "Library" / "LaunchAgents" / "local.astracradle.studio.plist",
                ]
            )
        elif sys_name == "Windows":
            desktop = home / "Desktop"
            appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
            start_menu = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            startup_dir = start_menu / "Startup"
            paths.extend(
                [
                    desktop / "AstraCradle.lnk",
                    desktop / "AstraCradle.bat",
                    start_menu / "AstraCradle.lnk",
                    start_menu / "AstraCradle.bat",
                    startup_dir / "AstraCradle.lnk",
                    startup_dir / "AstraCradle.bat",
                ]
            )
        return paths

    def install_shortcuts(self, repo_root: Path, os_override: str | None = None) -> list[Path]:
        """Creates friendly 1-click Desktop & App Menu shortcuts for zero-knowledge users."""
        sys_name = os_override or platform.system()
        home = Path.home()
        created: list[Path] = []
        icon_png = repo_root / "assets" / "astracradle_emblem_256.png"
        icon_ico = repo_root / "assets" / "astracradle.ico"
        icon_icns = repo_root / "assets" / "astracradle.icns"

        if sys_name == "Linux":
            # Install multi-size XDG hicolor & pixmaps icons so KDE/GNOME/CachyOS menus & taskbars render crisp AstraCradle artwork
            hicolor_base = home / ".local" / "share" / "icons" / "hicolor"
            for sz, fname in (
                ("32x32", "astracradle_emblem_32.png"),
                ("64x64", "astracradle_emblem_64.png"),
                ("128x128", "astracradle_emblem_128.png"),
                ("256x256", "astracradle_emblem_256.png"),
            ):
                src_icon = repo_root / "assets" / fname
                if src_icon.exists():
                    for app_icon_name in ("astracradle.png", "Astracradle.png"):
                        dest_icon = hicolor_base / sz / "apps" / app_icon_name
                        dest_icon.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(src_icon, dest_icon)
                        except OSError:
                            pass
            pixmaps_dir = home / ".local" / "share" / "pixmaps"
            if icon_png.exists():
                try:
                    pixmaps_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(icon_png, pixmaps_dir / "astracradle.png")
                except OSError:
                    pass

            standalone_bin = repo_root / "dist" / "AstraCradle" / "AstraCradle"
            gui_bin = Path(sys.executable).parent / "astracradle-gui"
            if standalone_bin.exists():
                exec_cmd = f'"{standalone_bin}"'
            elif gui_bin.exists():
                exec_cmd = f'"{gui_bin}"'
            else:
                exec_cmd = f'"{sys.executable}" -m astracradle'

            icon_value = "astracradle" if (hicolor_base / "256x256" / "apps" / "astracradle.png").exists() else str(icon_png)
            desktop_content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=AstraCradle\n"
                "GenericName=System Optimizer & Diagnostics\n"
                "Comment=Dyson-Core OS Optimizer & Self-Healing Studio\n"
                f"Exec={exec_cmd}\n"
                f"Path={repo_root}\n"
                f"Icon={icon_value}\n"
                "StartupWMClass=Astracradle\n"
                "Terminal=false\n"
                "Categories=Utility;System;\n"
                "Keywords=system;optimizer;diagnostics;telemetry;astracradle;\n"
            )
            for target in (
                home / ".local" / "share" / "applications" / "astracradle.desktop",
                home / "Desktop" / "AstraCradle.desktop",
            ):
                if target.parent.exists() or ".local" in str(target):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(desktop_content, encoding="utf-8")
                    try:
                        target.chmod(0o755)
                    except OSError:
                        pass
                    created.append(target)

            # Refresh desktop & icon caches quietly if available
            for cmd in (
                ["update-desktop-database", str(home / ".local" / "share" / "applications")],
                ["gtk-update-icon-cache", "-f", "-t", str(hicolor_base)],
                ["kbuildsycoca6"],
                ["kbuildsycoca5"],
            ):
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)
                    except Exception:
                        pass

        elif sys_name == "Darwin":
            # Build native macOS .app bundle inside ~/Applications with .icns icon
            app_bundle = home / "Applications" / "AstraCradle.app"
            contents = app_bundle / "Contents"
            macos_dir = contents / "MacOS"
            res_dir = contents / "Resources"
            try:
                macos_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(parents=True, exist_ok=True)
                if icon_icns.exists():
                    shutil.copy2(icon_icns, res_dir / "astracradle.icns")
                elif icon_png.exists():
                    shutil.copy2(icon_png, res_dir / "astracradle.png")
                plist = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleName</key><string>AstraCradle</string>\n'
                    '  <key>CFBundleDisplayName</key><string>AstraCradle</string>\n'
                    '  <key>CFBundleIdentifier</key><string>local.astracradle.studio</string>\n'
                    '  <key>CFBundleExecutable</key><string>AstraCradle</string>\n'
                    '  <key>CFBundleIconFile</key><string>astracradle.icns</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '</dict></plist>\n'
                )
                (contents / "Info.plist").write_text(plist, encoding="utf-8")
                gui_bin = Path(sys.executable).parent / "astracradle-gui"
                mac_exec = f'"{gui_bin}"' if gui_bin.exists() else f'"{sys.executable}" -m astracradle'
                launcher = macos_dir / "AstraCradle"
                launcher.write_text(
                    f'#!/usr/bin/env bash\ncd "{repo_root}"\nexec {mac_exec}\n',
                    encoding="utf-8",
                )
                launcher.chmod(0o755)
                created.append(app_bundle)
            except OSError:
                pass

            cmd_content = (
                "#!/usr/bin/env bash\n"
                f'cd "{repo_root}"\n'
                f'"{sys.executable}" -m astracradle\n'
            )
            for target in (
                home / "Applications" / "AstraCradle.command",
                home / "Desktop" / "AstraCradle.command",
            ):
                if target.parent.exists() or "Applications" in str(target):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(cmd_content, encoding="utf-8")
                    try:
                        target.chmod(0o755)
                    except OSError:
                        pass
                    created.append(target)

        elif sys_name == "Windows":
            standalone_exe = repo_root / "dist" / "AstraCradle" / "AstraCradle.exe"
            py_exe = Path(sys.executable)
            gui_exe_script = py_exe.parent / "astracradle-gui.exe"
            pyw_exe = py_exe.parent / "pythonw.exe"
            if standalone_exe.exists():
                target_exe = str(standalone_exe)
                target_args = ""
            elif gui_exe_script.exists():
                target_exe = str(gui_exe_script)
                target_args = ""
            else:
                target_exe = str(pyw_exe if pyw_exe.exists() else py_exe)
                target_args = "-m astracradle"

            bat_content = (
                "@echo off\r\n"
                f'cd /d "{repo_root}"\r\n'
                f'start "" "{target_exe}" {target_args}\r\n'
            )
            appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
            start_menu = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for target in (home / "Desktop" / "AstraCradle.bat", start_menu / "AstraCradle.bat"):
                if target.parent.exists():
                    target.write_text(bat_content, encoding="utf-8")
                    created.append(target)
            # Create native Start Menu & Desktop .lnk shortcuts launching zero-console GUI executable with AstraCradle.ico
            if platform.system() == "Windows":
                for lnk in (home / "Desktop" / "AstraCradle.lnk", start_menu / "AstraCradle.lnk"):
                    if lnk.parent.exists():
                        ps = (
                            f'$ws = New-Object -ComObject WScript.Shell; '
                            f'$s = $ws.CreateShortcut("{lnk}"); '
                            f'$s.TargetPath = "{target_exe}"; '
                            f'$s.Arguments = "{target_args}"; '
                            f'$s.WorkingDirectory = "{repo_root}"; '
                            f'$s.IconLocation = "{icon_ico},0"; '
                            f'$s.Description = "AstraCradle — Dyson-Core OS Optimizer & Self-Healing Studio"; '
                            f'$s.Save()'
                        )
                        try:
                            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=5, check=False)
                            if lnk.exists():
                                created.append(lnk)
                        except Exception:
                            pass

        self.journal.log_event(
            component="LifecycleManager",
            action="install_shortcuts",
            how=f"Created {len(created)} desktop/launcher shortcut(s) for {sys_name}",
            why="Provide 1-click startup for non-technical users",
            outcome="ok",
        )
        return created

    def set_launch_at_login(
        self,
        enabled: bool,
        repo_root: Path,
        start_minimized: bool = False,
        os_override: str | None = None,
    ) -> tuple[bool, str]:
        """Configures or removes OS login autostart (optionally passing --minimized to tray)."""
        sys_name = os_override or platform.system()
        home = Path.home()
        min_flag = " --minimized" if start_minimized else ""

        if sys_name == "Linux":
            autostart_file = home / ".config" / "autostart" / "astracradle.desktop"
            if not enabled:
                autostart_file.unlink(missing_ok=True)
                return True, "Removed Linux login autostart entry."
            autostart_file.parent.mkdir(parents=True, exist_ok=True)
            gui_bin = Path(sys.executable).parent / "astracradle-gui"
            exec_cmd = f'"{gui_bin}"{min_flag}' if gui_bin.exists() else f'"{sys.executable}" -m astracradle{min_flag}'
            autostart_file.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=AstraCradle\n"
                f"Exec={exec_cmd}\n"
                f"Path={repo_root}\n"
                "Icon=astracradle\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
            autostart_file.chmod(0o755)
            return True, f"Enabled Linux login autostart ({'minimized to tray' if start_minimized else 'normal window'})."

        if sys_name == "Darwin":
            plist_file = home / "Library" / "LaunchAgents" / "local.astracradle.studio.plist"
            if not enabled:
                plist_file.unlink(missing_ok=True)
                return True, "Removed macOS LaunchAgent login autostart."
            plist_file.parent.mkdir(parents=True, exist_ok=True)
            gui_bin = Path(sys.executable).parent / "astracradle-gui"
            prog_args = (
                f"    <string>{gui_bin}</string>\n"
                if gui_bin.exists()
                else f"    <string>{sys.executable}</string>\n    <string>-m</string>\n    <string>astracradle</string>\n"
            )
            if start_minimized:
                prog_args += "    <string>--minimized</string>\n"
            plist_file.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                "  <key>Label</key><string>local.astracradle.studio</string>\n"
                "  <key>ProgramArguments</key>\n  <array>\n"
                f"{prog_args}"
                "  </array>\n"
                "  <key>RunAtLoad</key><true/>\n"
                "</dict></plist>\n",
                encoding="utf-8",
            )
            return True, f"Enabled macOS login autostart ({'minimized' if start_minimized else 'normal'})."

        if sys_name == "Windows":
            appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
            startup_dir = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            bat_file = startup_dir / "AstraCradle.bat"
            lnk_file = startup_dir / "AstraCradle.lnk"
            if not enabled:
                bat_file.unlink(missing_ok=True)
                lnk_file.unlink(missing_ok=True)
                return True, "Removed Windows Startup folder entry."
            startup_dir.mkdir(parents=True, exist_ok=True)
            py_exe = Path(sys.executable)
            gui_exe = py_exe.parent / "astracradle-gui.exe"
            pyw_exe = py_exe.parent / "pythonw.exe"
            target_exe = str(gui_exe if gui_exe.exists() else (pyw_exe if pyw_exe.exists() else py_exe))
            args = "" if gui_exe.exists() else "-m astracradle"
            if start_minimized:
                args = (args + " --minimized").strip()
            bat_file.write_text(f'@echo off\r\ncd /d "{repo_root}"\r\nstart "" "{target_exe}" {args}\r\n', encoding="utf-8")
            return True, f"Enabled Windows login autostart ({'minimized to tray' if start_minimized else 'normal'})."

        return False, f"Unsupported OS for login autostart: {sys_name}"

    def remove_shortcuts(self, extra_paths: list[Path] | None = None) -> list[str]:
        removed: list[str] = []
        candidates = self.shortcut_paths() + (extra_paths or [])
        for p in candidates:
            try:
                if p.exists() and "astracradle" in p.name.lower():
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
                    removed.append(str(p))
            except OSError:
                continue
        return removed

    def uninstall_astracradle(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        delete_user_data: bool = True,
        restore_quarantine_first: bool = True,
        uninstall_pip_package: bool = False,
        extra_shortcut_paths: list[Path] | None = None,
    ) -> UninstallSummary:
        """Safely reverts active scripts, optionally restores quarantine, removes shortcuts, and deletes all AstraCradle data."""
        summary = UninstallSummary(ok=True)

        # 1. Revert any enabled toggleable scripts cleanly
        if self.scripts_engine is not None:
            for sc in self.scripts_engine.load_scripts():
                if sc.enabled:
                    ok, _ = self.scripts_engine.toggle_script(
                        sc.script_id, False, adapter, config, [], dry_run=False
                    )
                    if ok:
                        summary.reverted_scripts += 1
            if summary.reverted_scripts:
                summary.notes.append(f"Safely reverted {summary.reverted_scripts} active optimization script(s).")

        # 2. Optionally restore quarantined items before deleting AstraCradle data
        if restore_quarantine_first:
            for entry in self.rollback_mgr.load_entries():
                if not entry.dry_run and entry.status == "applied" and entry.quarantine_paths:
                    ok, _ = self.rollback_mgr.rollback_entry(entry.entry_id)
                    if ok:
                        summary.restored_quarantine_entries += 1
            if summary.restored_quarantine_entries:
                summary.notes.append(
                    f"Restored {summary.restored_quarantine_entries} quarantined batch(es) to original locations."
                )

        # 3. Remove Desktop & App Menu shortcuts
        summary.removed_shortcuts = self.remove_shortcuts(extra_paths=extra_shortcut_paths)
        if summary.removed_shortcuts:
            summary.notes.append(f"Removed {len(summary.removed_shortcuts)} AstraCradle shortcut(s).")

        # 3b. Remove any background schedule units/tasks & AstraShield hosts sinkhole cleanly
        try:
            from .scheduler import BackgroundSchedulerManager
            BackgroundSchedulerManager(self.cfg_mgr, self.journal).remove_schedule()
        except Exception:
            pass
        try:
            from .adblock_shield import AstraShieldManager
            shield_notes = AstraShieldManager(self.cfg_mgr, self.rollback_mgr, self.journal).cleanup_on_uninstall()
            summary.notes.extend(shield_notes)
        except Exception:
            pass

        # 4. Log final uninstall event before optional directory deletion
        self.journal.log_event(
            component="LifecycleManager",
            action="uninstall",
            how=f"delete_user_data={delete_user_data}, restore_quarantine={restore_quarantine_first}",
            why="User requested clean AstraCradle uninstall",
            outcome="ok",
        )

        # 5. Uninstall Python package if requested
        if uninstall_pip_package:
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "uninstall", "-y", "astracradle"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode == 0:
                    summary.uninstalled_package = True
                    summary.notes.append("Uninstalled 'astracradle' Python package cleanly.")
            except Exception as exc:
                summary.notes.append(f"Package uninstall skipped ({exc}).")

        # 6. Delete ALL AstraCradle profiles, configs, logs, quarantine & data if requested
        if delete_user_data:
            target = self.cfg_mgr.base_dir
            if self._is_safe_astracradle_dir(target):
                if target.exists():
                    shutil.rmtree(target, ignore_errors=False)
                summary.deleted_data_dir = True
                summary.notes.append(f"Deleted all AstraCradle profiles, logs, and data at {target}.")
            else:
                summary.ok = False
                summary.notes.append(f"Guardrail refused to delete unsafe directory: {target}")
        else:
            summary.notes.append(f"Kept user profiles and data at {self.cfg_mgr.base_dir} as requested.")

        return summary
