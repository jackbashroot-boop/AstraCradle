"""Tests for AstraCradle v2.6.0: 4K responsive scaling, vertical-only scroll & permission-gated full PC discovery."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from astracradle.core.config import ConfigManager
from astracradle.core.full_discovery import FullSystemDiscoveryEngine
from astracradle.core.journal import ForensicJournal
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.modals import ConsentAutomationDialog, FullDiscoveryResultDialog


def test_responsive_geometry_up_to_4k() -> None:
    for sw, sh in ((1280, 768), (1920, 1080), (2560, 1440), (3840, 2160)):
        w, h, x, y = AstraCradleApp._compute_responsive_geometry(sw, sh)
        assert 1060 <= w <= sw
        assert 700 <= h <= sh
        assert x >= 0 and y >= 0
    w4k, h4k, _, _ = AstraCradleApp._compute_responsive_geometry(3840, 2160)
    assert w4k >= 3000 and h4k >= 1700


def test_full_system_discovery_engine_and_journal() -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        adapter = get_os_adapter("auto", cfg.custom_protected_paths)

        eng = FullSystemDiscoveryEngine(cfg_mgr, rb, journal)
        out = eng.run_full_discovery(adapter, cfg)

        assert len(out.baseline.subsystems_checked) >= 9
        assert Path(out.baseline.baseline_json_path).exists()
        saved = json.loads(Path(out.baseline.baseline_json_path).read_text(encoding="utf-8"))
        assert saved["os_platform"]
        assert saved["cpu_cores"]
        assert saved["ram_total_gb"] > 0

        loaded = eng.load_saved_baseline()
        assert loaded is not None
        assert loaded.hostname == out.baseline.hostname

        findings = journal.verify_and_audit()
        assert findings and all(f.severity == "ok" for f in findings), findings


def test_permission_gate_blocks_scan_until_consent_and_4k_resize_scroll() -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        cfg = cfg_mgr.load()
        cfg.consent_prompted = False
        cfg.first_run_scan_completed = False
        cfg_mgr.save(cfg)

        # 1. Interactive first load must NOT auto-run full discovery before explicit consent
        app = AstraCradleApp(base_dir=Path(td), show_startup_popups=True)
        try:
            app.update()
            assert "Permission Required" in app.dash_headline.cget("text")
            assert not (Path(td) / "state" / "initial_pc_baseline.json").exists()

            # Verify ConsentAutomationDialog & FullDiscoveryResultDialog render cleanly
            saved_calls = []
            dlg = ConsentAutomationDialog(
                app,
                app.palette,
                app.fonts,
                app.config_obj,
                lambda perms, mode="selected": saved_calls.append((perms, mode)),
                first_run=True,
            )
            dlg.update()
            assert dlg.winfo_exists()
            dlg._accept_all()
            assert len(saved_calls) == 1
            assert saved_calls[0][0].get("_trigger_full_discovery") is True

            # Verify async discovery queue + DysonOrbitalBeacon.set_scanning() work end-to-end
            app.config_obj.consent_telemetry = True
            app.run_full_pc_discovery_async(show_dialog=False)
            for _ in range(40):
                app.update()
                if app.config_obj.first_run_scan_completed:
                    break
                import time as _t
                _t.sleep(0.05)
            assert app.config_obj.first_run_scan_completed is True

            eng = FullSystemDiscoveryEngine(app.cfg_mgr, app.rollback_mgr, app.journal)
            out = eng.run_full_discovery(app._active_adapter(), app.config_obj)
            res_dlg = FullDiscoveryResultDialog(app, app.palette, app.fonts, out.baseline)
            res_dlg.update()
            assert res_dlg.winfo_exists()
            res_dlg.destroy()
        finally:
            app.destroy()


def test_gui_responsive_4k_resizing_and_vertical_only_scroll() -> None:
    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=Path(td), show_startup_popups=False)
        try:
            app.update()
            # Trigger layout at 1060x700, 1920x1080, and 3840x2160 logic
            for width in (1060, 1920, 3840):
                app._apply_responsive_layout(width)
                app.update_idletasks()
                sb_w = int(app.sidebar.cget("width"))
                if width == 3840:
                    assert sb_w >= 320
                    assert int(app.scan_cta.cget("width")) >= 160
                    assert int(app.dash_summary.cget("wraplength")) >= 2000
                elif width == 1060:
                    assert sb_w <= 260

            # Reset to 1060x700 and inspect all 10 pages for zero horizontal overflow & smooth vertical scroll
            app.geometry("1060x700+0+0")
            app._apply_responsive_layout(1060)
            app.update()

            for nav_key in (
                "dashboard",
                "playbook",
                "hunter",
                "scripts",
                "hardware",
                "modules",
                "probalance",
                "autoruns",
                "safeguards",
                "settings",
            ):
                app.switch_nav(nav_key)
                app.update_idletasks()
                app.update()

                # Segmented tab bars must fit within the active window width
                for seg_name in ("hw_seg", "mod_seg", "pb_seg", "auto_seg"):
                    seg = getattr(app, seg_name, None)
                    if seg is not None and seg.winfo_ismapped():
                        assert seg.winfo_x() + seg.winfo_width() <= app.winfo_width() + 2

                # Check every mapped CTkScrollableFrame inside the active page:
                # xview must NOT indicate horizontal overflow beyond canvas
                page = app.pages[nav_key]
                stack = [page]
                while stack:
                    w = stack.pop()
                    if hasattr(w, "_parent_canvas"):
                        canvas = w._parent_canvas
                        x0, x1 = canvas.xview()
                        assert x0 == 0.0 and x1 >= 0.99, (
                            f"Horizontal scroll overflow on {nav_key}: xview=({x0}, {x1})"
                        )
                        # Test vertical scrolling up & down if content taller than viewport
                        y0, y1 = canvas.yview()
                        if y1 < 1.0:
                            canvas.yview_scroll(2, "units")
                            app.update_idletasks()
                            ny0, _ = canvas.yview()
                            assert ny0 >= y0
                            canvas.yview_moveto(0.0)
                    stack.extend(w.winfo_children())

            # Also verify every subtab of modules, hardware, probalance, and autoruns for zero horizontal overflow
            for nav_key, seg_attr, tabs in (
                ("hardware", "hw_seg", ["Component Library & Presets", "Visual Pipelines & Packs", "GPU Power & Fan Curves"]),
                ("modules", "mod_seg", ["Storage", "Startup", "Processes & Firewall", "Installed Apps", "Duplicates & Bloat", "Privacy & Shield", "Glitch & OS Tools"]),
                ("probalance", "pb_seg", ["ProBalance Engine", "Auto-Revert App Rules", "Interrupt & DPC Guard"]),
                ("autoruns", "auto_seg", ["Autoruns & Services", "Ghost Driver Cleanup", "Thermal SPC Baselines"]),
            ):
                app.switch_nav(nav_key)
                for tab in tabs:
                    if seg_attr == "hw_seg":
                        app._on_hw_tab(tab)
                    elif seg_attr == "mod_seg":
                        app._on_module_tab(tab)
                    elif seg_attr == "pb_seg":
                        app._on_pb_tab(tab)
                    else:
                        app._on_auto_tab(tab)
                    app.update_idletasks()
                    app.update()
                    stack = [app.pages[nav_key]]
                    while stack:
                        w = stack.pop()
                        if hasattr(w, "_parent_canvas"):
                            x0, x1 = w._parent_canvas.xview()
                            assert x0 == 0.0 and x1 >= 0.99, (
                                f"Horizontal overflow on {nav_key}/{tab}: xview=({x0}, {x1})"
                            )
                        stack.extend(w.winfo_children())
        finally:
            app.destroy()


def test_cli_full_discovery_exit_zero() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "astracradle", "--full-discovery"],
        cwd="/home/user/astracradle",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Full Local PC Baseline" in proc.stdout
    assert "initial_pc_baseline.json" in proc.stdout


def test_cross_os_start_menu_icons_and_bundle_updater(monkeypatch) -> None:
    import zipfile
    from astracradle.core.lifecycle import LifecycleManager
    from astracradle.core.updater import ModuleAndUpdateManager

    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td) / "home"
        fake_appdata = Path(td) / "appdata"
        (fake_home / "Desktop").mkdir(parents=True)
        (fake_appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        monkeypatch.setenv("APPDATA", str(fake_appdata))

        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        lc = LifecycleManager(cfg_mgr, rb, journal)
        repo_root = Path("/home/user/astracradle")

        # 1. Linux hicolor Start Menu icons + .desktop (NO Desktop icon by default)
        lin_created = lc.install_shortcuts(repo_root, os_override="Linux")
        assert any(p.name == "astracradle.desktop" for p in lin_created)
        assert not (fake_home / "Desktop" / "AstraCradle.desktop").exists()
        assert (fake_home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "astracradle.png").exists()
        dtxt = (fake_home / ".local" / "share" / "applications" / "astracradle.desktop").read_text(encoding="utf-8")
        assert "Icon=astracradle" in dtxt and 'Exec="' in dtxt

        # 2. macOS .app bundle + .icns (explicit desktop opt-in)
        mac_created = lc.install_shortcuts(repo_root, os_override="Darwin", include_desktop_icon=True)
        assert (fake_home / "Applications" / "AstraCradle.app" / "Contents" / "Resources" / "astracradle.icns").exists()
        assert (fake_home / "Applications" / "AstraCradle.app" / "Contents" / "Info.plist").exists()
        assert len(mac_created) >= 3

        # 3. Windows Start Menu + explicit Desktop shortcut
        win_created = lc.install_shortcuts(repo_root, os_override="Windows", include_desktop_icon=True)
        assert (fake_appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "AstraCradle.bat").exists()
        assert len(win_created) >= 2

        # 4. Update bundle discovery & safe verification (v2.5.1 zip)
        upd = ModuleAndUpdateManager(cfg_mgr, journal)
        cfg = cfg_mgr.load()
        zpath = fake_home / "Downloads" / "AstraCradle-v2.5.1-bundle.zip"
        zpath.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("AstraCradle-v2.5.1/pyproject.toml", '[project]\nname = "astracradle"\nversion = "2.5.1"\n')
            zf.writestr("AstraCradle-v2.5.1/astracradle/__init__.py", '__version__ = "2.5.1"\n')

        res = upd.check_for_updates(cfg)
        assert res.update_available is True
        assert res.latest_version == "2.5.1"

        ok, msg = upd.install_update_bundle(zpath, cfg, run_pip=False)
        assert ok is True and "2.5.1" in msg
        assert (cfg_mgr.updates_dir / "applied" / zpath.name).exists()

        # Verify zip path-traversal protection
        bad_zip = Path(td) / "evil.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../../evil.txt", "nope")
        ok_bad, msg_bad = upd.install_update_bundle(bad_zip, cfg, run_pip=False)
        assert ok_bad is False and "Blocked unsafe zip path" in msg_bad


def test_tray_icon_and_minimize_to_tray_setting(monkeypatch) -> None:
    from astracradle.ui.tray import TrayController

    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=Path(td), show_startup_popups=False)
        try:
            app.switch_nav("settings")
            app.update()
            # Verify both settings switches exist and save cleanly
            app.set_tray_icon_var.set(True)
            app.set_tray_var.set(True)
            app.on_save_settings()
            reloaded = app.cfg_mgr.load()
            assert reloaded.show_tray_icon is True
            assert reloaded.minimize_to_tray is True

            # Simulate running tray icon and verify close withdraws instead of destroying
            monkeypatch.setattr(TrayController, "running", property(lambda _self: True))
            monkeypatch.setattr(app.tray, "notify", lambda _t, _m: True)
            app.on_close(force_quit=False)
            assert app.winfo_exists()

            # Force quit from tray menu closes cleanly
            app.on_close(force_quit=True)
        except Exception:
            if app.winfo_exists():
                app.destroy()
            raise


def test_github_first_then_local_fallback_updater(monkeypatch) -> None:
    from astracradle.core.updater import ModuleAndUpdateManager

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        cfg = cfg_mgr.load()
        cfg.update_repo_url = "https://github.com/ExampleOwner/AstraCradle"
        cfg_mgr.save(cfg)
        journal = ForensicJournal(cfg_mgr)
        upd = ModuleAndUpdateManager(cfg_mgr, journal)

        slug = upd.resolve_github_repo(cfg)
        assert slug == ("ExampleOwner", "AstraCradle")

        # 1) GitHub checked FIRST: returns newer GitHub release when available
        calls: list[tuple[str, str]] = []

        def fake_github_newer(owner: str, repo: str, timeout: float = 3.5):
            calls.append((owner, repo))
            return "9.9.9", "https://github.com/ExampleOwner/AstraCradle/releases/download/v9.9.9/bundle.zip", f"GitHub {owner}/{repo} release v9.9.9"

        monkeypatch.setattr(upd, "_query_github_latest", fake_github_newer)
        res_gh = upd.check_for_updates(cfg, check_remote=True)
        assert calls == [("ExampleOwner", "AstraCradle")]
        assert res_gh.update_available is True
        assert res_gh.latest_version == "9.9.9"
        assert res_gh.release_notes.startswith("https://github.com/")

        # 2) When GitHub is offline / unreachable, falls back cleanly to local staged manifest
        monkeypatch.setattr(
            upd,
            "_query_github_latest",
            lambda owner, repo, timeout=3.5: (None, None, f"GitHub {owner}/{repo} unreachable (URLError); falling back to local"),
        )
        manifest = cfg_mgr.updates_dir / "manifest.json"
        manifest.write_text('{"version": "2.5.1", "notes": "Local fallback update"}', encoding="utf-8")
        res_loc = upd.check_for_updates(cfg, check_remote=True)
        assert res_loc.update_available is True
        assert res_loc.latest_version == "2.5.1"
        assert "falling back to local" in res_loc.status_message

