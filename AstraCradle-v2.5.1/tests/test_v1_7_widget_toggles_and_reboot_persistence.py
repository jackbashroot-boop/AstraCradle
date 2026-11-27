from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from types import SimpleNamespace

from astracradle import __version__
from astracradle.core.config import ConfigManager
from astracradle.core.elevation import ElevationResult, ElevationRunner
from astracradle.core.executor import GuardedExecutor
from astracradle.core.journal import ForensicJournal
from astracradle.core.lifecycle import LifecycleManager
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.core.scripts_engine import ScriptsEngine
from astracradle.ui.app import AstraCradleApp


def test_v1_7_desktop_widget_all_toggles_sections_and_reboot_persistence(monkeypatch) -> None:
    assert __version__ == "2.5.1"
    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td) / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        exec_eng = GuardedExecutor(cfg_mgr, rb, journal)
        scripts_eng = ScriptsEngine(cfg_mgr, exec_eng, journal)
        lc = LifecycleManager(cfg_mgr, rb, journal, scripts_eng)
        repo_root = Path("/home/user/astracradle")

        # 1. Cross-OS Desktop Widget autostart entries (Linux, Darwin, Windows)
        for os_name, expected_path in (
            ("Linux", fake_home / ".config" / "autostart" / "astracradle.desktop"),
            ("Darwin", fake_home / "Library" / "LaunchAgents" / "local.astracradle.studio.plist"),
            ("Windows", fake_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AstraCradle.bat"),
        ):
            ok, _ = lc.set_launch_at_login(
                True,
                repo_root,
                start_minimized=True,
                os_override=os_name,
                start_widget=True,
            )
            assert ok is True and expected_path.exists()
            txt = expected_path.read_text(encoding="utf-8")
            assert "--minimized" in txt and "--widget" in txt

        # 2. Cross-OS Root/Sudo Automated Reboot Persistence payloads + password prompt flow
        for os_name, expect_marker in (
            ("Linux", "/etc/systemd/system/astracradle-reboot.service"),
            ("Darwin", "/Library/LaunchDaemons/local.astracradle.reboot.plist"),
            ("Windows", "AstraCradleRebootToggles"),
        ):
            tgt, payload = lc.render_reboot_payload(os_override=os_name)
            assert expect_marker in tgt
            assert "--apply-boot-toggles" in payload

        prompted = {"count": 0}
        elev_calls: list[list[str]] = []

        def _fake_prompt(title: str, detail: str) -> str | None:
            prompted["count"] += 1
            return "test-sudo-secret"

        elev = ElevationRunner(password_prompt_cb=_fake_prompt)

        def _fake_write(target_path: Path, content: str, reason: str, mode: str = "644") -> ElevationResult:
            assert elev.password_prompt_cb is not None
            pw = elev.password_prompt_cb("Sudo Required", reason)
            assert pw == "test-sudo-secret"
            elev_calls.append(["write", str(target_path)])
            return ElevationResult(True, 0, "", "", message="Mocked sudo write ok", method="sudo")

        def _fake_run(argv: list[str], reason: str, title: str = "", stdin_text: str = "", timeout: int = 20) -> ElevationResult:
            elev_calls.append(list(argv))
            return ElevationResult(True, 0, "", "", message="Mocked sudo run ok", method="sudo")

        monkeypatch.setattr(elev, "write_privileged_file", _fake_write)
        monkeypatch.setattr(elev, "run_privileged", _fake_run)
        ok_rb, msg_rb = lc.install_reboot_persistence(cfg, elevation=elev, os_override="Linux")
        assert ok_rb is True and prompted["count"] >= 1
        assert cfg_mgr.load().reboot_persistence_enabled is True
        # Ensure sudo password is NEVER persisted on disk
        raw_cfg = cfg_mgr.config_path.read_text(encoding="utf-8")
        assert "test-sudo-secret" not in raw_cfg

        # 3. Boot toggle re-application (scripts + dry-run safe checks)
        scripts = scripts_eng.load_scripts()
        target_sc = scripts[0]
        target_sc.enabled = True
        scripts_eng.save_scripts(scripts)
        cfg.dry_run_default = True
        cfg.wifi_auto_optimize = True
        cfg_mgr.save(cfg)
        ok_boot, notes_boot = lc.apply_boot_toggles(get_os_adapter(), cfg, dry_run=True, elevation=elev)
        assert ok_boot is True and any(target_sc.name in n for n in notes_boot)
        assert not any("skipped:" in n for n in notes_boot)

        # 4. Full GUI Test: DesktopTelemetryWidget + ALL 13 Sections + Every Toggle Persisting Across Reboot
        app1 = AstraCradleApp(base_dir=cfg_mgr.base_dir, show_startup_popups=False)
        try:
            app1.update()

            # Visit all 13 sections cleanly
            for nav_key in (
                "dashboard",
                "playbook",
                "hunter",
                "scripts",
                "hardware",
                "modules",
                "wifi",
                "rf_waves",
                "scanner",
                "probalance",
                "autoruns",
                "safeguards",
                "settings",
            ):
                app1.switch_nav(nav_key)
                app1.update()

            # Launch & exercise Cross-OS DesktopTelemetryWidget
            app1.show_desktop_widget()
            app1.update()
            w = app1._desktop_widget
            assert w is not None and w.winfo_exists()
            w.update_telemetry(24.5, 51.2, 42.0, 1.25, 0.45, 95, shield_on=True, wifi_label="Wi-Fi Auto")
            w.toggle_expanded()
            assert w.expanded is False
            w.toggle_expanded()
            assert w.expanded is True
            w.toggle_pin()
            assert w.always_on_top is False
            w.toggle_pin()
            assert w.always_on_top is True
            w._on_press(SimpleNamespace(x_root=100, y_root=100))
            w._on_drag(SimpleNamespace(x_root=180, y_root=150))
            w._on_release()
            app1.update()

            # Flip Settings toggles & save
            app1.switch_nav("settings")
            app1.set_simple_mode_var.set(False)
            app1.set_desktop_widget_var.set(True)
            app1.set_widget_top_var.set(True)
            app1.set_ram_guard_var.set(True)
            app1.set_probalance_var.set(True)
            app1.set_badusb_var.set(False)
            app1.set_wifi_auto_var.set(False)
            app1.set_boot_splash_var.set(False)
            app1.set_floating_orb_var.set(False)
            app1.set_hover_icon_var.set(False)
            app1.set_solar_backdrop_var.set(False)
            app1.set_quiet.set(True)
            app1.set_privacy_var.set(True)
            app1.set_confirm_prompts_var.set(False)
            app1.on_save_settings()
            app1.update()
        finally:
            try:
                app1.on_close(force_quit=True)
            except Exception:
                pass

        # Simulate Reboot: launch fresh AstraCradleApp reading persisted config.json
        app2 = AstraCradleApp(base_dir=cfg_mgr.base_dir, show_startup_popups=False, start_widget=True)
        try:
            app2.update()
            reloaded = app2.config_obj
            assert reloaded.simple_mode is False
            assert reloaded.desktop_widget_enabled is True
            assert reloaded.desktop_widget_always_on_top is True
            assert reloaded.ram_guard_enabled is True
            assert reloaded.probalance_enabled is True
            assert reloaded.badusb_guard_enabled is False
            assert reloaded.wifi_auto_optimize is False
            assert reloaded.boot_star_gather_splash is False
            assert reloaded.floating_orb_on_minimize is False
            assert reloaded.hover_icon_animation is False
            assert reloaded.solar_backdrop_enabled is False
            assert reloaded.quiet_hours_enabled is True
            assert reloaded.consent_privacy_scan is True
            assert reloaded.confirm_important_prompts is False
            assert app2._desktop_widget is not None and app2._desktop_widget.winfo_exists()
            app2._desktop_widget._handle_close()
            assert app2.config_obj.desktop_widget_enabled is False
        finally:
            try:
                app2.on_close(force_quit=True)
            except Exception:
                pass


def test_v1_7_no_unused_imports_in_astracradle_package() -> None:
    pkg_dir = Path("/home/user/astracradle/astracradle")
    for py in sorted(pkg_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        imported: dict[str, int] = {}
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported[a.asname or a.name.split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                for a in node.names:
                    if a.name != "*":
                        imported[a.asname or a.name] = node.lineno
            elif isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        unused = [(k, ln) for k, ln in imported.items() if k not in used]
        assert not unused, f"Unused imports in {py}: {unused}"
