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
from .elevation import ElevationRunner
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
            resolved_desk = self._resolve_windows_desktop(home)
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
            if resolved_desk is not None and resolved_desk != desktop:
                paths.extend(
                    [
                        resolved_desk / "AstraCradle.lnk",
                        resolved_desk / "AstraCradle.bat",
                    ]
                )
        return paths

    @staticmethod
    def _resolve_windows_desktop(home: Path) -> Path | None:
        """Resolves the active Windows Desktop folder (supports Windows 11 OneDrive Desktop redirection)."""
        if platform.system() == "Windows":
            try:
                import ctypes

                buf = ctypes.create_unicode_buffer(260)
                if ctypes.windll.shell32.SHGetFolderPathW(0, 0x0010, 0, 0, buf) == 0 and buf.value:  # type: ignore[attr-defined]
                    p = Path(buf.value)
                    if p.exists():
                        return p
            except Exception:
                pass
        for cand in (home / "OneDrive" / "Desktop", home / "Desktop"):
            if cand.exists():
                return cand
        return home / "Desktop"

    def install_shortcuts(
        self,
        repo_root: Path,
        os_override: str | None = None,
        include_desktop_icon: bool = False,
    ) -> list[Path]:
        """Creates App Menu shortcuts (and Desktop icon only when explicitly requested via include_desktop_icon=True)."""
        sys_name = os_override or platform.system()
        home = Path.home()
        created: list[Path] = []
        pkg_assets = Path(__file__).resolve().parents[1] / "assets"
        repo_assets = repo_root / "assets"
        assets_base = pkg_assets if pkg_assets.exists() else repo_assets
        icon_png = assets_base / "astracradle_emblem_256.png"
        icon_ico = assets_base / "astracradle.ico"
        icon_icns = assets_base / "astracradle.icns"

        if sys_name == "Linux":
            # Install multi-size XDG hicolor & pixmaps icons so KDE/GNOME/CachyOS menus & taskbars render crisp AstraCradle artwork
            hicolor_base = home / ".local" / "share" / "icons" / "hicolor"
            hicolor_theme = hicolor_base / "index.theme"
            if not hicolor_theme.exists():
                sys_hicolor_theme = Path("/usr/share/icons/hicolor/index.theme")
                try:
                    hicolor_base.mkdir(parents=True, exist_ok=True)
                    if sys_hicolor_theme.exists():
                        shutil.copy2(sys_hicolor_theme, hicolor_theme)
                    else:
                        hicolor_theme.write_text(
                            "[Icon Theme]\n"
                            "Name=Hicolor\n"
                            "Comment=Fallback icon theme\n"
                            "Directories=32x32/apps,64x64/apps,128x128/apps,256x256/apps\n\n"
                            "[32x32/apps]\nSize=32\nContext=Applications\nType=Fixed\n\n"
                            "[64x64/apps]\nSize=64\nContext=Applications\nType=Fixed\n\n"
                            "[128x128/apps]\nSize=128\nContext=Applications\nType=Fixed\n\n"
                            "[256x256/apps]\nSize=256\nContext=Applications\nType=Fixed\n",
                            encoding="utf-8",
                        )
                except OSError:
                    pass
            for sz, fname in (
                ("32x32", "astracradle_emblem_32.png"),
                ("64x64", "astracradle_emblem_64.png"),
                ("128x128", "astracradle_emblem_128.png"),
                ("256x256", "astracradle_emblem_256.png"),
            ):
                src_icon = assets_base / fname
                if src_icon.exists():
                    for app_icon_name in ("astracradle.png", "Astracradle.png"):
                        dest_icon = hicolor_base / sz / "apps" / app_icon_name
                        dest_icon.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(src_icon, dest_icon)
                        except OSError:
                            pass
            for extra_icon_dir in (home / ".local" / "share" / "pixmaps", home / ".local" / "share" / "icons"):
                if icon_png.exists():
                    try:
                        extra_icon_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(icon_png, extra_icon_dir / "astracradle.png")
                    except OSError:
                        pass
                hover_gif = assets_base / "astracradle_hover.gif"
                if hover_gif.exists():
                    try:
                        extra_icon_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(hover_gif, extra_icon_dir / "astracradle-hover.gif")
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
                "StartupWMClass=AstraCradle\n"
                "Terminal=false\n"
                "Categories=Utility;System;\n"
                "Keywords=system;optimizer;diagnostics;telemetry;astracradle;\n"
                "Actions=Open;Diagnose;FixSafe;Quit;\n\n"
                "[Desktop Action Open]\n"
                "Name=Open AstraCradle\n"
                f"Exec={exec_cmd}\n\n"
                "[Desktop Action Diagnose]\n"
                "Name=Run Equilibrium Scan\n"
                f"Exec={exec_cmd} --diagnose\n\n"
                "[Desktop Action FixSafe]\n"
                "Name=1-Click Fix Everything Safe\n"
                f"Exec={exec_cmd} --fix-safe\n\n"
                "[Desktop Action Quit]\n"
                "Name=Quit AstraCradle\n"
                f"Exec={exec_cmd} --quit\n"
            )
            linux_targets = [home / ".local" / "share" / "applications" / "astracradle.desktop"]
            if include_desktop_icon:
                linux_targets.append(home / "Desktop" / "AstraCradle.desktop")
            for target in linux_targets:
                if target.parent.exists() or ".local" in str(target):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(desktop_content, encoding="utf-8")
                    try:
                        target.chmod(0o755)
                    except OSError:
                        pass
                    created.append(target)

            # Refresh existing autostart entry so upgrades never keep launching an older v1.x path
            autostart_desktop = home / ".config" / "autostart" / "astracradle.desktop"
            if autostart_desktop.exists():
                try:
                    prev_txt = autostart_desktop.read_text(encoding="utf-8", errors="replace")
                    extra_flags = ""
                    if "--minimized" in prev_txt:
                        extra_flags += " --minimized"
                    if "--widget" in prev_txt:
                        extra_flags += " --widget"
                    autostart_exec = f"{exec_cmd}{extra_flags}"
                    autostart_body = desktop_content.replace(f"Exec={exec_cmd}\n", f"Exec={autostart_exec}\n", 1)
                    autostart_desktop.write_text(autostart_body, encoding="utf-8")
                    autostart_desktop.chmod(0o755)
                except Exception:
                    pass

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
            mac_targets = [home / "Applications" / "AstraCradle.command"]
            if include_desktop_icon:
                mac_targets.append(home / "Desktop" / "AstraCradle.command")
            for target in mac_targets:
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
            venv_py = repo_root / ".venv" / "Scripts" / "python.exe"
            if standalone_exe.exists():
                target_exe = str(standalone_exe)
                target_args = ""
            elif venv_py.exists():
                target_exe = str(venv_py)
                target_args = "-m astracradle"
            else:
                target_exe = str(py_exe)
                target_args = "-m astracradle"

            bat_content = (
                "@echo off\r\n"
                f'cd /d "{repo_root}"\r\n'
                f'if exist "{target_exe}" (\r\n'
                f'    start "AstraCradle" "{target_exe}" {target_args}\r\n'
                f') else if exist "{repo_root}\\run_astracradle.bat" (\r\n'
                f'    call "{repo_root}\\run_astracradle.bat"\r\n'
                ") else (\r\n"
                '    echo AstraCradle launcher not found. Please re-run Install_AstraCradle.bat.\r\n'
                "    pause\r\n"
                ")\r\n"
            )
            appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
            start_menu = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            win_desktop = self._resolve_windows_desktop(home)
            win_bat_targets = [start_menu / "AstraCradle.bat"]
            if include_desktop_icon and win_desktop is not None:
                win_bat_targets.append(win_desktop / "AstraCradle.bat")
            for target in win_bat_targets:
                if target.parent.exists():
                    target.write_text(bat_content, encoding="utf-8")
                    created.append(target)
            # Create native Start Menu (and optional Desktop) .lnk shortcuts using verified python.exe runtime
            if platform.system() == "Windows":
                win_lnk_targets = [start_menu / "AstraCradle.lnk"]
                if include_desktop_icon and win_desktop is not None:
                    win_lnk_targets.append(win_desktop / "AstraCradle.lnk")
                for lnk in win_lnk_targets:
                    if lnk.parent.exists():
                        ps = (
                            f'$ws = New-Object -ComObject WScript.Shell; '
                            f'$s = $ws.CreateShortcut("{lnk}"); '
                            f'$s.TargetPath = "{target_exe}"; '
                            f'$s.Arguments = "{target_args}"; '
                            f'$s.WorkingDirectory = "{repo_root}"; '
                            f'$s.WindowStyle = 7; '
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
        repo_root: Path | None = None,
        start_minimized: bool = False,
        os_override: str | None = None,
        start_widget: bool = False,
    ) -> tuple[bool, str]:
        """Configures or removes OS login autostart (optionally passing --minimized / --widget)."""
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        sys_name = os_override or platform.system()
        home = Path.home()
        flags = (" --minimized" if start_minimized else "") + (" --widget" if start_widget else "")

        if sys_name == "Linux":
            autostart_file = home / ".config" / "autostart" / "astracradle.desktop"
            if not enabled:
                autostart_file.unlink(missing_ok=True)
                return True, "Removed Linux login autostart entry."
            autostart_file.parent.mkdir(parents=True, exist_ok=True)
            gui_bin = Path(sys.executable).parent / "astracradle-gui"
            exec_cmd = f'"{gui_bin}"{flags}' if gui_bin.exists() else f'"{sys.executable}" -m astracradle{flags}'
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
            if start_widget:
                prog_args += "    <string>--widget</string>\n"
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
            venv_py = repo_root / ".venv" / "Scripts" / "python.exe"
            target_exe = str(venv_py if venv_py.exists() else py_exe)
            args = "-m astracradle"
            if start_minimized:
                args = (args + " --minimized").strip()
            if start_widget:
                args = (args + " --widget").strip()
            bat_file.write_text(f'@echo off\r\ncd /d "{repo_root}"\r\nstart "AstraCradle" "{target_exe}" {args}\r\n', encoding="utf-8")
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
        """Safely reverts active scripts, optionally restores quarantine, removes shortcuts/hooks/links, and deletes all AstraCradle data."""
        summary = UninstallSummary(ok=True)
        home = Path.home()

        # 0. Gracefully stop any other running AstraCradle GUI/tray instance first
        try:
            from .single_instance import SingleInstanceManager
            terminated = SingleInstanceManager(self.cfg_mgr).terminate_existing_instances(timeout_sec=2.0)
            if terminated:
                summary.notes.append(f"Stopped {terminated} running AstraCradle instance(s).")
        except Exception:
            pass

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

        # 3. Remove Desktop & App Menu shortcuts + Login Autostart
        summary.removed_shortcuts = self.remove_shortcuts(extra_paths=extra_shortcut_paths)
        if summary.removed_shortcuts:
            summary.notes.append(f"Removed {len(summary.removed_shortcuts)} AstraCradle shortcut(s).")
        try:
            ok_login, msg_login = self.set_launch_at_login(False)
            if ok_login and ("Removed" in msg_login):
                summary.notes.append(msg_login)
        except Exception:
            pass

        # 3b. Remove global CLI symlinks (~/.local/bin/astracradle*) and CachyOS/Fish Wi-Fi hooks/timers
        removed_cli: list[str] = []
        local_bin = home / ".local" / "bin"
        for bin_name in ("astracradle", "astracradle-gui", "astracradle-wifi"):
            cand = local_bin / bin_name
            try:
                if cand.is_symlink() or cand.exists():
                    is_ac_wrapper = cand.is_symlink() or ("astracradle" in cand.read_text(encoding="utf-8", errors="ignore"))
                    if is_ac_wrapper:
                        cand.unlink(missing_ok=True)
                        removed_cli.append(str(cand))
            except OSError:
                pass
        if removed_cli:
            summary.notes.append(f"Removed {len(removed_cli)} CLI launcher(s) from ~/.local/bin.")

        fish_wifi = home / ".config" / "fish" / "conf.d" / "astracradle_wifi.fish"
        sd_user = home / ".config" / "systemd" / "user"
        wifi_svc = sd_user / "astracradle-wifi-opt.service"
        wifi_tmr = sd_user / "astracradle-wifi-opt.timer"
        if wifi_tmr.exists() and shutil.which("systemctl"):
            try:
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", "astracradle-wifi-opt.timer"],
                    capture_output=True,
                    timeout=3,
                    check=False,
                )
            except Exception:
                pass
        removed_hooks = 0
        for hook_p in (fish_wifi, wifi_svc, wifi_tmr):
            if hook_p.exists():
                try:
                    hook_p.unlink(missing_ok=True)
                    removed_hooks += 1
                except OSError:
                    pass
        if removed_hooks:
            summary.notes.append(f"Removed {removed_hooks} Fish/systemd Wi-Fi hook file(s).")

        # 3c. Remove any background schedule units/tasks, reboot persistence, TV failsafe & AstraShield hosts sinkhole
        try:
            from .scheduler import BackgroundSchedulerManager
            BackgroundSchedulerManager(self.cfg_mgr, self.journal).remove_schedule()
        except Exception:
            pass
        try:
            self.remove_reboot_persistence(elevation=None)
        except Exception:
            pass
        try:
            from .adblock_shield import AstraShieldManager
            shield_mgr = AstraShieldManager(self.cfg_mgr, self.rollback_mgr, self.journal)
            shield_notes = shield_mgr.cleanup_on_uninstall()
            summary.notes.extend(shield_notes)
            from .lan_tv_shield import LanTvAdShield
            tv_shield = LanTvAdShield(self.journal)
            tv_st = tv_shield.load_failsafe_state()
            if tv_st and tv_st.get("active"):
                _, tv_msg = tv_shield.restore_tv_settings(reason="uninstall", shield=shield_mgr)
                summary.notes.append(tv_msg)
        except Exception:
            pass

        # 3c. Cleanly stop Hermes OS AI gateway/runtime if active
        try:
            from .hermes_agent import HermesAgentManager

            hm = HermesAgentManager(self.cfg_mgr, self.journal)
            hm_notes = hm.cleanup_for_uninstall(delete_user_data=delete_user_data)
            summary.notes.extend(hm_notes)
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

        # 6. Delete ALL AstraCradle profiles, configs, certs, install memory, logs, quarantine & data if requested
        if delete_user_data:
            target = self.cfg_mgr.base_dir
            if self._is_safe_astracradle_dir(target):
                if target.exists():
                    shutil.rmtree(target, ignore_errors=False)
                summary.deleted_data_dir = True
                summary.notes.append(f"Deleted all AstraCradle profiles, certs, install memory, logs, and data at {target}.")
            else:
                summary.ok = False
                summary.notes.append(f"Guardrail refused to delete unsafe directory: {target}")
        else:
            # Clean transient wizard drafts/temp files while preserving user configs & profiles
            try:
                (self.cfg_mgr.state_dir / "setup_wizard_draft.json").unlink(missing_ok=True)
            except OSError:
                pass
            summary.notes.append(f"Kept user profiles and data at {self.cfg_mgr.base_dir} as requested.")

        return summary

    # ------------------------------------------------------------------
    # Cross-OS Root/Sudo Automated Reboot Persistence & Toggle Re-Apply
    # ------------------------------------------------------------------
    def reboot_persistence_status(self, os_override: str | None = None) -> tuple[bool, str]:
        sys_name = os_override or platform.system()
        if sys_name == "Linux":
            svc = Path("/etc/systemd/system/astracradle-reboot.service")
            return svc.exists(), str(svc)
        if sys_name == "Darwin":
            plist = Path("/Library/LaunchDaemons/local.astracradle.reboot.plist")
            return plist.exists(), str(plist)
        if sys_name == "Windows":
            if platform.system() == "Windows":
                try:
                    proc = subprocess.run(
                        ["schtasks", "/Query", "/TN", "AstraCradleRebootToggles"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    return proc.returncode == 0, "Task: AstraCradleRebootToggles"
                except Exception:
                    return False, "Task: AstraCradleRebootToggles"
            return False, "Task: AstraCradleRebootToggles"
        return False, "Unsupported OS"

    def render_reboot_payload(self, os_override: str | None = None) -> tuple[str, str]:
        """Returns (target_path_or_task, payload_content_or_command) for system-level boot survival."""
        sys_name = os_override or platform.system()
        py = sys.executable or "python3"
        cfg_dir = str(self.cfg_mgr.base_dir)
        if sys_name == "Darwin":
            plist = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                "  <key>Label</key><string>local.astracradle.reboot</string>\n"
                "  <key>ProgramArguments</key><array>\n"
                f"    <string>{py}</string>\n"
                "    <string>-m</string>\n"
                "    <string>astracradle</string>\n"
                "    <string>--apply-boot-toggles</string>\n"
                "    <string>--config-dir</string>\n"
                f"    <string>{cfg_dir}</string>\n"
                "  </array>\n"
                "  <key>RunAtLoad</key><true/>\n"
                "</dict></plist>\n"
            )
            return "/Library/LaunchDaemons/local.astracradle.reboot.plist", plist
        if sys_name == "Windows":
            cmd = (
                f'schtasks /Create /SC ONSTART /RU SYSTEM /RL HIGHEST /TN "AstraCradleRebootToggles" '
                f'/TR "\\"{py}\\" -m astracradle --apply-boot-toggles --config-dir \\"{cfg_dir}\\"" /F'
            )
            return "AstraCradleRebootToggles", cmd
        service = (
            "[Unit]\n"
            "Description=AstraCradle Automated Reboot Toggle & Shield Persistence\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f'ExecStart="{py}" -m astracradle --apply-boot-toggles --config-dir "{cfg_dir}"\n'
            "RemainAfterExit=yes\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        return "/etc/systemd/system/astracradle-reboot.service", service

    def install_reboot_persistence(
        self,
        config: AppConfig,
        elevation: ElevationRunner | None = None,
        os_override: str | None = None,
        dry_run: bool = False,
    ) -> tuple[bool, str]:
        """Installs OS-native root/admin boot persistence using sudo password / UAC so toggles survive reboot."""
        sys_name = os_override or platform.system()
        target, payload = self.render_reboot_payload(os_override=sys_name)
        if dry_run:
            return True, f"[DRY-RUN] Would install reboot persistence ({target})."
        elev = elevation or ElevationRunner()

        if sys_name == "Windows":
            py = sys.executable or "python3"
            cfg_dir = str(self.cfg_mgr.base_dir)
            res = elev.run_privileged(
                [
                    "schtasks",
                    "/Create",
                    "/SC",
                    "ONSTART",
                    "/RU",
                    "SYSTEM",
                    "/RL",
                    "HIGHEST",
                    "/TN",
                    "AstraCradleRebootToggles",
                    "/TR",
                    f'"{py}" -m astracradle --apply-boot-toggles --config-dir "{cfg_dir}"',
                    "/F",
                ],
                reason="Register AstraCradle automated boot task so your toggles survive reboot.",
                title="Administrator Permission for Reboot Persistence",
            )
            if res.ok:
                config.reboot_persistence_enabled = True
                self.cfg_mgr.save(config)
            return res.ok, res.message or ("Installed Windows boot task." if res.ok else res.stderr)

        if sys_name == "Darwin":
            target_path = Path(target)
            wres = elev.write_privileged_file(
                target_path,
                payload,
                reason="Enter your macOS sudo password to install the AstraCradle LaunchDaemon so toggles survive reboot.",
            )
            if not wres.ok:
                return False, wres.message or wres.stderr
            lres = elev.run_privileged(
                ["launchctl", "load", "-w", str(target_path)],
                reason="Enable AstraCradle LaunchDaemon at macOS startup.",
                title="Sudo Required to Enable LaunchDaemon",
            )
            config.reboot_persistence_enabled = bool(wres.ok)
            self.cfg_mgr.save(config)
            return True, f"Installed macOS LaunchDaemon at {target_path} ({lres.message})."

        # Linux systemd + cron @reboot fallback
        svc_path = Path(target)
        wres = elev.write_privileged_file(
            svc_path,
            payload,
            reason="Enter your sudo password to install astracradle-reboot.service so all toggles survive Linux reboot.",
        )
        if not wres.ok:
            return False, wres.message or wres.stderr

        py = sys.executable or "python3"
        cfg_dir = str(self.cfg_mgr.base_dir)
        if shutil.which("systemctl"):
            elev.run_privileged(
                ["systemctl", "daemon-reload"],
                reason="Reload systemd units for AstraCradle reboot persistence.",
                title="Sudo Required for systemd daemon-reload",
            )
            eres = elev.run_privileged(
                ["systemctl", "enable", "astracradle-reboot.service"],
                reason="Enable astracradle-reboot.service on boot.",
                title="Sudo Required to Enable Boot Service",
            )
            if eres.ok:
                config.reboot_persistence_enabled = True
                self.cfg_mgr.save(config)
                self.journal.log_event(
                    "LifecycleManager",
                    "install_reboot_persistence",
                    f"Enabled {svc_path}",
                    "User configured root reboot persistence for toggles",
                    "ok",
                )
                return True, f"Installed & enabled {svc_path} — toggles will survive every reboot."

        # Fallback: cron @reboot entry
        cron_line = f'@reboot "{py}" -m astracradle --apply-boot-toggles --config-dir "{cfg_dir}"\n'
        cres = elev.write_privileged_file(
            Path("/etc/cron.d/astracradle-reboot"),
            f"SHELL=/bin/sh\nPATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n{cron_line}",
            reason="Install /etc/cron.d/astracradle-reboot so toggles survive reboot.",
        )
        if cres.ok:
            config.reboot_persistence_enabled = True
            self.cfg_mgr.save(config)
        return cres.ok, (
            "Installed /etc/cron.d/astracradle-reboot — toggles will survive every reboot."
            if cres.ok
            else (cres.message or cres.stderr)
        )

    def remove_reboot_persistence(
        self,
        elevation: ElevationRunner | None = None,
        os_override: str | None = None,
    ) -> tuple[bool, str]:
        sys_name = os_override or platform.system()
        elev = elevation or ElevationRunner()
        if sys_name == "Windows":
            if platform.system() == "Windows":
                res = elev.run_privileged(
                    ["schtasks", "/Delete", "/TN", "AstraCradleRebootToggles", "/F"],
                    reason="Remove AstraCradle Windows boot task.",
                )
                return res.ok, res.message
            return True, "No Windows boot task on host."
        if sys_name == "Darwin":
            plist = Path("/Library/LaunchDaemons/local.astracradle.reboot.plist")
            if not plist.exists():
                return True, "No macOS reboot daemon installed."
            elev.run_privileged(["launchctl", "unload", "-w", str(plist)], reason="Unload AstraCradle LaunchDaemon.")
            res = elev.run_privileged(["rm", "-f", str(plist)], reason="Remove AstraCradle LaunchDaemon.")
            return res.ok, res.message
        svc = Path("/etc/systemd/system/astracradle-reboot.service")
        cron_f = Path("/etc/cron.d/astracradle-reboot")
        if not svc.exists() and not cron_f.exists():
            return True, "No system reboot unit installed."
        if svc.exists() and shutil.which("systemctl"):
            elev.run_privileged(["systemctl", "disable", "astracradle-reboot.service"], reason="Disable AstraCradle boot service.")
        res = elev.run_privileged(["rm", "-f", str(svc), str(cron_f)], reason="Remove AstraCradle boot service files.")
        return res.ok, res.message or "Removed reboot persistence service."

    def apply_boot_toggles(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        *,
        dry_run: bool = False,
        elevation: ElevationRunner | None = None,
    ) -> tuple[bool, list[str]]:
        """Re-applies all persisted AstraShield, Wi-Fi, Schedule, and Script toggles after reboot."""
        notes: list[str] = []
        ok_all = True
        try:
            from .crash_analyzer import CrashAnalyzer
            ca = CrashAnalyzer(self.cfg_mgr, self.journal)
            if not dry_run:
                ca.mark_boot_tweak_pending()
        except Exception:
            ca = None

        # 1. AstraShield hosts sinkhole & DNS sinkhole
        if config.astrashield_enabled:
            try:
                from .adblock_shield import AstraShieldManager

                sh = AstraShieldManager(self.cfg_mgr, self.rollback_mgr, self.journal, elevation=elevation)
                ok, msg = sh.enable_hosts_sinkhole(
                    config.astrashield_categories,
                    dry_run=dry_run,
                )
                notes.append(f"AstraShield Hosts: {msg}")
                ok_all = ok_all and ok
            except Exception as exc:
                notes.append(f"AstraShield Hosts skipped: {exc}")

        # 2. Wi-Fi best-channel / powersave toggle
        if config.wifi_auto_optimize:
            try:
                from .wifi_channel_optimizer import WifiChannelOptimizer

                wopt = WifiChannelOptimizer(self.journal)
                res = wopt.optimize_connection(
                    dry_run=dry_run,
                    prefer_5ghz=config.wifi_prefer_5ghz,
                    disable_powersave=config.wifi_disable_powersave,
                )
                notes.append(f"Wi-Fi Optimizer: {getattr(res, 'action_taken', 'ok')}")
            except Exception as exc:
                notes.append(f"Wi-Fi Optimizer skipped: {exc}")

        # 3. Saved toggleable Optimization Scripts (scripts.json where enabled == True)
        if self.scripts_engine is not None:
            prev_elev = self.scripts_engine.elevation
            if elevation is not None:
                self.scripts_engine.elevation = elevation
            try:
                for sc in self.scripts_engine.load_scripts():
                    if sc.enabled:
                        try:
                            sok, smsg = self.scripts_engine.toggle_script(
                                sc.script_id,
                                True,
                                adapter,
                                config,
                                [],
                                dry_run=dry_run,
                            )
                            notes.append(f"Script {sc.name}: {smsg}")
                            ok_all = ok_all and sok
                        except Exception as exc:
                            notes.append(f"Script {sc.name} skipped: {exc}")
            finally:
                self.scripts_engine.elevation = prev_elev

        # 4. Background user schedule
        if config.background_schedule_enabled and not dry_run:
            try:
                from .scheduler import BackgroundSchedulerManager

                sched = BackgroundSchedulerManager(self.cfg_mgr, self.journal)
                if not sched.schedule_status().installed:
                    _, smsg = sched.install_schedule(config.schedule_interval_minutes)
                    notes.append(f"Schedule: {smsg}")
            except Exception as exc:
                notes.append(f"Schedule skipped: {exc}")

        self.journal.log_event(
            "LifecycleManager",
            "apply_boot_toggles",
            "; ".join(notes) or "No active boot toggles needed re-applying",
            "Restore all persisted toggle selections across reboot",
            "ok" if ok_all else "warning",
        )
        if ca is not None and not dry_run:
            ca.clear_boot_tweak_pending()
        return ok_all, notes

