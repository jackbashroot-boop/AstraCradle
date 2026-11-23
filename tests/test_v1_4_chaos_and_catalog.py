from __future__ import annotations

import ast
import bdb
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from astracradle import __version__
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.rollback import RollbackManager
from astracradle.core.system_catalog_scanner import KNOWN_ISSUES_DB, SystemCatalogAndHealer
from astracradle.core.wifi_channel_optimizer import WifiApObservation, WifiChannelOptimizer
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.modals import YesNoActionDialog
from astracradle.theme import get_palette, get_ui_fonts


def test_v1_4_wifi_recommend_yes_no_scanner_and_catalog(monkeypatch) -> None:
    assert __version__ == "2.5.0"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        drive_a = root / "drive_c"
        drive_b = root / "drive_d"
        fake_home = drive_a / "home"
        app_dir_1 = fake_home / ".local" / "share" / "applications"
        for d in (drive_a, drive_b, fake_home, app_dir_1):
            d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        # Populate files across 2 simulated drives + known bugs
        (drive_a / "notes.txt").write_text("healthy text", encoding="utf-8")
        bad_json = fake_home / ".cache" / "broken_state.json"
        bad_json.parent.mkdir(parents=True, exist_ok=True)
        bad_json.write_text("{corrupt_json: [1, 2", encoding="utf-8")

        stale_lock = drive_b / ".cache" / "stale_app.lock"
        stale_lock.parent.mkdir(parents=True, exist_ok=True)
        stale_lock.write_text("999999", encoding="utf-8")
        old_ts = time.time() - 7200
        os.utime(stale_lock, (old_ts, old_ts))

        broken_link = drive_b / "dead_shortcut"
        broken_link.symlink_to(drive_b / "missing_target_xyz")

        world_sh = fake_home / ".local" / "bin" / "run_me.sh"
        world_sh.parent.mkdir(parents=True, exist_ok=True)
        world_sh.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        os.chmod(world_sh, 0o777)

        # Initial installed program (valid desktop file)
        (app_dir_1 / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\nExec=firefox\n", encoding="utf-8")

        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        cfg = cfg_mgr.load()
        assert cfg.wifi_prompt_before_apply is True
        assert cfg.confirm_important_prompts is True
        assert cfg.catalog_passive_scan is True

        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        adapter = get_os_adapter(custom_protected_paths=[str(root / "protected")])

        scanner = SystemCatalogAndHealer(cfg_mgr, rb, journal)

        # 1. Full multi-drive scan + quiet auto-fix of safe bugs
        summ = scanner.run_full_scan(
            adapter, cfg, scan_roots=[drive_a, drive_b], max_files=500, quiet_autofix=True, dry_run=False
        )
        assert len(summ.drives_scanned) >= 2
        assert summ.files_cataloged >= 4
        assert summ.programs_cataloged >= 1
        assert summ.quiet_fixes_applied >= 3
        assert not broken_link.exists()
        assert not bad_json.exists()
        assert not stale_lock.exists()
        assert (os.stat(world_sh).st_mode & 0o002) == 0
        assert len(KNOWN_ISSUES_DB) >= 10

        # 2. Newly installed program detection + passive incremental batch
        (app_dir_1 / "blender.desktop").write_text("[Desktop Entry]\nName=Blender\nExec=blender\n", encoding="utf-8")
        (drive_b / "fresh_file.log").write_text("log line\n", encoding="utf-8")
        p_summ = scanner.run_passive_batch(adapter, cfg, batch_size=50, quiet_autofix=True, check_new_programs=True)
        assert any("blender" in p.lower() for p in p_summ.newly_installed_programs)
        assert p_summ.files_checked_this_pass >= 1

        # 3. Wi-Fi less-trafficked channel recommender
        wopt = WifiChannelOptimizer(journal, state_dir=cfg_mgr.base_dir)
        aps = [
            WifiApObservation("StudioWiFi", "AA:BB:CC:00:00:01", 6, 2437, "2.4GHz", 70, 144, in_use=True),
            WifiApObservation("Busy1", "11:11:11:11:11:01", 6, 2437, "2.4GHz", 90),
            WifiApObservation("Busy2", "11:11:11:11:11:02", 7, 2442, "2.4GHz", 85),
            WifiApObservation("StudioWiFi", "AA:BB:CC:00:00:05", 149, 5745, "5GHz", 88, 866, in_use=False),
        ]
        monkeypatch.setattr(
            wopt,
            "scan_wifi_environment",
            lambda rescan=True: (aps, {"interface": "wlan0", "powersave": "on", "os": "Linux"}),
        )
        rec = wopt.recommend_channels(prefer_5ghz=True, disable_powersave=True, rescan=True)
        assert rec["has_recommendation"] is True
        assert rec["recommended_channel"] == 149
        assert "149" in rec["summary"]

        # 4. GUI: Special Category "scanner" + 1-Button Yes/No Dialog
        app = AstraCradleApp(base_dir=cfg_mgr.base_dir, show_startup_popups=False)
        try:
            app.update()
            assert "scanner" in app.nav_buttons
            app.switch_nav("scanner")
            app.update()
            assert "System Scanner" in app.topbar_title.cget("text")

            app.switch_nav("wifi")
            app._latest_wifi_rec = rec
            app._render_wifi_page()
            app.update()

            dlg = YesNoActionDialog(
                app,
                get_palette("Obsidian Horology"),
                get_ui_fonts(1.0),
                title_text="Test Yes/No",
                summary="Switch to Channel 149?",
                detail_lines=["Lower congestion on 5 GHz"],
            )
            dlg.update()
            assert dlg.approved is False
            dlg._choose_yes()
            assert dlg.approved is True
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass


def test_v1_4_chaos_barrage_splicing_debugger_and_ast_audit(monkeypatch) -> None:
    """Brutal chaos barrage: corrupted DB self-heal, multithreaded stress, splicing, bdb debugger, AST audit."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fake_home = root / "home"
        drive_x = root / "drive_x"
        fake_home.mkdir(parents=True, exist_ok=True)
        drive_x.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        adapter = get_os_adapter(custom_protected_paths=[str(root / "protected")])

        # 1. Chaos: corrupt SQLite DB header -> SystemCatalogAndHealer must recover automatically
        scanner = SystemCatalogAndHealer(cfg_mgr, rb, journal)
        scanner.db_path.write_bytes(b"CORRUPT_HEADER_BYTES_FOR_CHAOS_BARRAGE_0123456789")
        # Create cyclic symlink & bizarre unicode file names
        (drive_x / "unicode_⚡_file.json").write_text('{"valid": true}', encoding="utf-8")
        c1 = drive_x / "cycle_a"
        c2 = drive_x / "cycle_b"
        c1.symlink_to(c2)
        c2.symlink_to(c1)

        scanner2 = SystemCatalogAndHealer(cfg_mgr, rb, journal)
        summ = scanner2.run_full_scan(
            adapter, cfg, scan_roots=[drive_x], max_files=200, quiet_autofix=False, dry_run=True
        )
        assert summ.files_cataloged >= 1
        with sqlite3.connect(scanner2.db_path) as conn:
            res_check = conn.execute("PRAGMA quick_check").fetchone()[0]
            assert res_check == "ok"

        # 2. Chaos: 6-thread concurrent read/write contention on SQLite WAL catalog
        errors: list[str] = []

        def _hammer(tid: int) -> None:
            try:
                (drive_x / f"worker_{tid}.txt").write_text(f"payload {tid}", encoding="utf-8")
                s = scanner2.run_passive_batch(adapter, cfg, batch_size=20, quiet_autofix=False, dry_run=True)
                assert s.files_cataloged >= 0
                st = scanner2.get_catalog_stats()
                assert "files_cataloged" in st
            except Exception as exc:
                errors.append(f"thread-{tid}: {exc!r}")

        threads = [threading.Thread(target=_hammer, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        assert not errors, f"Concurrency failures: {errors}"

        # 3. Function Splicing & Fault Injection on Wi-Fi optimizer + os.scandir
        wopt = WifiChannelOptimizer(journal, state_dir=cfg_mgr.base_dir)
        spliced_aps = [
            WifiApObservation("Weird", "00:00:00:00:00:00", 0, 0, "2.4GHz", 150, in_use=True),
            WifiApObservation("Weird2", "FF:FF:FF:FF:FF:FF", 999, 9999, "6GHz", -10),
        ]
        monkeypatch.setattr(wopt, "scan_wifi_environment", lambda rescan=True: (spliced_aps, {"interface": "", "powersave": None}))
        rec_spliced = wopt.recommend_channels()
        assert isinstance(rec_spliced, dict) and "summary" in rec_spliced

        real_scandir = os.scandir

        def _faulty_scandir(path="."):
            if "drive_x" in str(path):
                raise PermissionError("Spliced permission denial")
            return real_scandir(path)

        monkeypatch.setattr(os, "scandir", _faulty_scandir)
        s_perm = scanner2.run_full_scan(adapter, cfg, scan_roots=[drive_x], quiet_autofix=False, dry_run=True)
        assert isinstance(s_perm.elapsed_sec, float)
        monkeypatch.setattr(os, "scandir", real_scandir)

        # 4. Debugger Execution (Python bdb.Bdb line tracer)
        class _TraceAuditDebugger(bdb.Bdb):
            def __init__(self) -> None:
                super().__init__()
                self.lines_stepped = 0

            def user_line(self, frame) -> None:
                self.lines_stepped += 1

        dbg = _TraceAuditDebugger()
        res_dbg = dbg.runcall(scanner2.run_passive_batch, adapter, cfg, 25, False, True, True)
        assert res_dbg.files_cataloged >= 0
        assert dbg.lines_stepped >= 10

        # 5. AST Static Security & Syntax Audit across entire astracradle package
        pkg_root = Path(__file__).resolve().parents[1] / "astracradle"
        for py_file in pkg_root.rglob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            raise AssertionError(f"Unsafe shell=True found in {py_file}:{node.lineno}")
