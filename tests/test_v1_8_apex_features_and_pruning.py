from __future__ import annotations

from dataclasses import replace
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from astracradle import __version__
from astracradle.core.adblock_shield import AstraShieldManager, DnsQueryEvent
from astracradle.core.autopilot import AutopilotEngine
from astracradle.core.config import ConfigManager
from astracradle.core.crash_analyzer import CrashAnalyzer
from astracradle.core.executor import GuardedExecutor
from astracradle.core.journal import ForensicJournal
from astracradle.core.models import RollbackEntry
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rf_bandwidth_scanner import BandwidthAndRfScanner
from astracradle.core.rollback import RollbackManager
from astracradle.core.safeguard_plus import PortableProfileBackup
from astracradle.core.scripts_engine import ScriptsEngine
from astracradle.core.self_heal import SelfHealEngine
from astracradle.core.storage_deep import SmartDiskMonitor
from astracradle.core.telemetry import TelemetryCollector
from astracradle.ui.app import AstraCradleApp


def test_v1_8_apex_features_widget_modes_triggers_proof_and_backup(monkeypatch) -> None:
    assert __version__ == "2.5.0"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".astracradle"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        exec_eng = GuardedExecutor(cfg_mgr, rb, journal)
        sh_eng = SelfHealEngine(cfg_mgr, rb, journal)
        autopilot = AutopilotEngine(cfg_mgr, exec_eng, sh_eng, journal)
        scripts_eng = ScriptsEngine(cfg_mgr, exec_eng, journal)
        crash = CrashAnalyzer(cfg_mgr, journal)

        # 1. Per-Process Socket Attribution & Bufferbloat Check
        rf = BandwidthAndRfScanner(journal)
        socks = rf.scan_process_sockets(limit=10)
        assert isinstance(socks, list)
        bb = rf.run_bufferbloat_check(host="127.0.0.1", port=65530, samples=2, timeout=0.1)
        assert bb.grade in ("A+", "A", "B", "C") and bb.recommendation

        # 2. AstraShield Live Query Feed & 1-Click Allowlist
        hosts_file = Path(td) / "hosts"
        hosts_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        shield = AstraShieldManager(cfg_mgr, rb, journal, hosts_path=hosts_file)
        shield.recent_events.append(DnsQueryEvent("2026-11-23T00:00:00Z", "ads.example.com", "blocked", "ads"))
        feed = shield.recent_query_feed(limit=10)
        assert len(feed) == 1 and feed[0].domain == "ads.example.com"
        ok_al, _ = shield.allow_domain_live("ads.example.com")
        assert ok_al is True
        blocked_now, _ = shield.is_domain_blocked("ads.example.com")
        assert blocked_now is False

        # 3. Before/After Proof Scorecard & Rule-Based Smart Triggers
        base_snap = TelemetryCollector().collect(get_os_adapter(), include_dns=False)
        snap_pre = replace(
            base_snap,
            cpu_percent=55.0,
            mem_used_bytes=10 * 1024**3,
            mem_available_bytes=6 * 1024**3,
            mem_percent=62.5,
        )
        snap_post = replace(
            base_snap,
            cpu_percent=22.0,
            mem_used_bytes=8 * 1024**3,
            mem_available_bytes=8 * 1024**3,
            mem_percent=50.0,
        )
        base_metrics = autopilot.capture_proof_baseline(snap_pre, 78)
        proof = autopilot.compare_proof_scorecard("Fix Everything Safe", base_metrics, snap_post, 92)
        assert proof.ram_freed_mb > 1000.0 and proof.health_delta == 14 and "Freed" in proof.summary

        trigs = autopilot.evaluate_smart_triggers(
            snap_pre,
            cfg,
            gpu_temp_c=85.0,
            wifi_congestion_pct=82.0,
            battery_pct=18.0,
            on_battery=True,
        )
        rules_hit = {t["rule"] for t in trigs}
        assert {"thermal_guard", "wifi_congestion", "battery_endurance"} <= rules_hit
        assert cfg_mgr.load().active_profile == "Battery Endurance"

        # 4. Portable Profile Backup & Verified Script Pack
        arch = Path(td) / "backup.astracradle-profile"
        ok_exp, _, arch_path = PortableProfileBackup.export_profile_archive(cfg_mgr, arch, journal)
        assert ok_exp is True and arch_path.exists()
        ok_imp, _ = PortableProfileBackup.import_profile_archive(arch_path, cfg_mgr, journal)
        assert ok_imp is True

        sp_path = Path(td) / "pack.json"
        ok_sp, _, _ = scripts_eng.export_script_pack(sp_path)
        assert ok_sp is True
        ok_si, _, _ = scripts_eng.import_script_pack(sp_path)
        assert ok_si is True
        # Tampered checksum rejected
        doc = json.loads(sp_path.read_text(encoding="utf-8"))
        doc["sha256"] = "0" * 64
        sp_path.write_text(json.dumps(doc), encoding="utf-8")
        ok_bad, _, _ = scripts_eng.import_script_pack(sp_path)
        assert ok_bad is False

        # 5. Crash-Safe Boot Auto-Rollback Sentinel
        src_file = Path(td) / "temp_cache.tmp"
        src_file.write_text("junk", encoding="utf-8")
        ok_q, qmap, qsz = rb.quarantine_file("boot_rb_01", src_file)
        assert ok_q and qmap is not None
        rb.record(
            RollbackEntry(
                entry_id="boot_rb_01",
                timestamp="2026-11-23T00:00:00Z",
                action_id="clean_paths",
                title="Test Boot Tweak",
                dry_run=False,
                status="applied",
                summary="Test boot tweak quarantine",
                quarantine_paths=[qmap],
                bytes_affected=qsz,
            )
        )
        crash.mark_boot_tweak_pending()
        rb_msg = crash.check_boot_crash_auto_rollback(rb, enabled=True)
        assert rb_msg is not None and "Test Boot Tweak" in rb_msg and src_file.exists()

        # 6. Enhanced SmartDiskMonitor & CLI --widget-status / --bufferbloat-test
        assert len(SmartDiskMonitor().inspect_disks()) >= 1
        out_ws = subprocess.run(
            [sys.executable, "-m", "astracradle", "--widget-status", "--config-dir", str(base)],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        ws_json = json.loads(out_ws.stdout)
        assert "text" in ws_json and "tooltip" in ws_json and ws_json["class"] in ("good", "warn", "crit")

        # 7. GUI Widget Skin Cycling, Corner Snap, Global Hotkeys & RF Page
        app = AstraCradleApp(base_dir=base, show_startup_popups=False)
        try:
            app.update()
            app.show_desktop_widget()
            app.update()
            w = app._desktop_widget
            assert w is not None
            for expected_mode in ("Gaming", "Network", "Minimal", "Balanced"):
                w.cycle_mode()
                assert w.mode == expected_mode
            w.snap_to_corner("top-right")
            assert cfg_mgr.load().desktop_widget_mode == "Balanced"

            app.switch_nav("rf_waves")
            app._last_bufferbloat = bb
            app._render_rf_waves_page()
            app.switch_nav("modules")
            app.switch_nav("scripts")
            app.switch_nav("settings")
            app.update()
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
