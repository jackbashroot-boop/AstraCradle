"""Comprehensive v1.0.0 unit, integration, Xvfb UI, brutal stress, and 3x chaos test suite."""

from __future__ import annotations

import bdb
import json
import os
from pathlib import Path
import random
import sqlite3
import time

import pytest

from astracradle import __version__
from astracradle.core.advanced_controls import (
    GpuPowerAndFanController,
    clamp_fan_curve_points,
)
from astracradle.core.autoruns_audit import AutorunEntry, AutorunsAndGhostAuditor
from astracradle.core.config import ConfigManager
from astracradle.core.crash_analyzer import CrashAnalyzer
from astracradle.core.diagnostics import DiagnosticsEngine
from astracradle.core.executor import GuardedExecutor
from astracradle.core.i18n import Translator, search_help_topics
from astracradle.core.journal import ForensicJournal
from astracradle.core.latency_guard import InterruptAndDpcGuard
from astracradle.core.models import PartitionInfo, ProcessInfo, SystemSnapshot
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.playbook import PlaybookBuilder
from astracradle.core.probalance import ProBalanceEngine
from astracradle.core.rollback import RollbackManager
from astracradle.core.telemetry import TelemetryCollector
from astracradle.core.telemetry_history import TelemetryHistoryDB
from astracradle.core.thermal_spc import ThermalSpcMonitor


def test_interrupt_dpc_latency_guard(tmp_path: Path) -> None:
    assert __version__ == "2.5.0"
    cfg_mgr = ConfigManager(base_dir=tmp_path / "ac_irq")
    journal = ForensicJournal(cfg_mgr)
    guard = InterruptAndDpcGuard(cfg_mgr, journal)

    rep = guard.measure_dpc_latency(samples=10, sleep_us=200)
    assert rep.samples == 10
    assert rep.p50_us >= 0.0
    assert rep.p95_us >= rep.p50_us
    assert rep.max_us >= rep.p95_us
    assert rep.grade in ("Clean", "Fair", "Elevated Jitter")
    assert len(rep.irq_sources) >= 1

    rules = guard.load_rules()
    assert len(rules) >= 3
    ok_del_builtin, _ = guard.delete_rule(rules[0].rule_id)
    assert ok_del_builtin is False

    custom = guard.save_custom_rule("Studio USB DAC", "audio", "4-5", "Keep audio crackling-free")
    assert any(r.rule_id == custom.rule_id for r in guard.load_rules())

    ok_app, msg_app, cmd_app = guard.apply_rule(custom, dry_run=True)
    assert ok_app is True and "[DRY RUN]" in msg_app and len(cmd_app) > 5

    ok_del_custom, _ = guard.delete_rule(custom.rule_id)
    assert ok_del_custom is True


def test_probalance_and_auto_revert_app_profiles(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path / "ac_pb")
    cfg = cfg_mgr.load()
    cfg.probalance_enabled = True
    cfg.probalance_cpu_threshold = 30.0
    journal = ForensicJournal(cfg_mgr)
    pb = ProBalanceEngine(cfg_mgr, journal)

    # Custom rule + built-in protection
    custom = pb.save_custom_rule("my_render_app", "3D Render & Benchmark", notes="Escalate for render")
    assert any(r.rule_id == custom.rule_id for r in pb.load_rules())
    assert pb.delete_rule("app_rule_blender")[0] is False
    assert pb.delete_rule(custom.rule_id)[0] is True

    # Protected process must NEVER be restrained even at 99% CPU
    prot_proc = ProcessInfo(pid=999991, name="systemd", username="root", cpu_percent=99.0, memory_mb=200.0, memory_percent=1.0, status="running", protected=True)
    # Non-protected current PID or harmless fake PID in dry-run + watched app 'blender'
    hog_proc = ProcessInfo(pid=os.getpid(), name="bg_indexer", username="user", cpu_percent=82.0, memory_mb=400.0, memory_percent=2.0, status="running", protected=False)
    # Wait: self PID is protected by _is_protected, so test with a safe fake PID in dry_run=True or subprocess
    import subprocess, sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        hog_child = ProcessInfo(pid=child.pid, name="bg_indexer", username="user", cpu_percent=82.0, memory_mb=50.0, memory_percent=0.5, status="running", protected=False)
        blender_proc = ProcessInfo(pid=888882, name="blender", username="user", cpu_percent=55.0, memory_mb=1200.0, memory_percent=8.0, status="running", protected=False)

        assert cfg.active_profile == "Balanced Daily"
        notes1 = pb.evaluate_tick([prot_proc, hog_child, blender_proc], "desktop", cfg, dry_run=False)
        assert child.pid in pb.restrained
        assert 999991 not in pb.restrained
        assert cfg.active_profile == "3D Render & Benchmark"
        assert pb.active_escalation is not None
        assert any("Auto-Escalated" in n for n in notes1)

        # Now blender exits AND bg_indexer drops to 5% CPU -> ProBalance releases hog AND auto-reverts profile!
        calm_child = ProcessInfo(pid=child.pid, name="bg_indexer", username="user", cpu_percent=5.0, memory_mb=50.0, memory_percent=0.5, status="running", protected=False)
        notes2 = pb.evaluate_tick([prot_proc, calm_child], "desktop", cfg, dry_run=False)
        assert child.pid not in pb.restrained
        assert pb.active_escalation is None
        assert cfg.active_profile == "Balanced Daily"
        assert any("Auto-Reverted" in n for n in notes2)

        ok_rev, _ = pb.revert_all(cfg, dry_run=False)
        assert ok_rev is True
    finally:
        child.terminate()
        child.wait(timeout=3)


def test_thermal_spc_welford_baselines_and_anomalies(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path / "ac_spc")
    journal = ForensicJournal(cfg_mgr)
    spc = ThermalSpcMonitor(cfg_mgr, journal)

    assert spc.classify_workload(4.0) == "idle"
    assert spc.classify_workload(22.0) == "light"
    assert spc.classify_workload(55.0) == "heavy"
    assert spc.classify_workload(85.0) == "gaming"
    assert spc.classify_workload(15.0, is_fullscreen=True) == "gaming"

    # Train 12 steady samples around ~52C in 'light' workload
    for idx in range(12):
        anoms = spc.update_sample("light", 51.5 + (idx % 3) * 0.5, 22.0, power_w=35.0)
        assert anoms == []

    baselines = spc.load_baselines()
    assert baselines["light"].count == 12
    assert 51.0 <= baselines["light"].mean_temp_c <= 53.5

    # Inject sudden 84C thermal spike -> must trigger SPC anomaly!
    spike = spc.update_sample("light", 84.0, 24.0, power_w=190.0)
    assert len(spike) >= 1
    assert any(a.metric == "temperature_c" and a.z_score >= 2.8 for a in spike)

    spc.reset_baselines("light")
    assert spc.load_baselines()["light"].count == 0


def test_interactive_fan_curve_editor_and_persistence(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path / "ac_fan")
    journal = ForensicJournal(cfg_mgr)
    gctrl = GpuPowerAndFanController(cfg_mgr, journal)

    clamped = clamp_fan_curve_points([[95, 120], [10, 5], [65, 60]])
    assert clamped[0] == [35, 20]
    assert clamped[-1] == [95, 100]
    assert [pt[0] for pt in clamped] == sorted(pt[0] for pt in clamped)

    ok_d, msg_d, pts = gctrl.apply_draft_curve([[40, 32], [60, 52], [80, 88]], profile_id="custom_curve", dry_run=False)
    assert ok_d is True and "Applied fan curve" in msg_d
    pid, loaded_pts = gctrl.get_applied_curve()
    assert pid == "custom_curve"
    assert loaded_pts == [[40, 32], [60, 52], [80, 88]]


def test_autoruns_audit_and_ghost_driver_cleanup(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path / "ac_auto")
    journal = ForensicJournal(cfg_mgr)
    rb = RollbackManager(cfg_mgr)
    auditor = AutorunsAndGhostAuditor(cfg_mgr, rb, journal)
    adapter = get_os_adapter("auto", [])

    items = auditor.scan_autoruns(adapter)
    assert isinstance(items, list)

    # Test reversible toggle of a synthetic user startup entry
    fake_desktop = tmp_path / "helper_app.desktop"
    fake_desktop.write_text("[Desktop Entry]\nName=Helper\nExec=/usr/bin/true\n", encoding="utf-8")
    ar = AutorunEntry(
        entry_id="ar_test",
        category="startup",
        name="Helper App",
        publisher_or_scope="user",
        command_or_path=str(fake_desktop),
        plain_english="Test helper",
        impact="low",
        enabled=True,
        can_toggle=True,
        location=str(fake_desktop),
    )
    ok_dis, msg_dis = auditor.toggle_autorun_entry(ar, False, adapter, dry_run=False)
    assert ok_dis is True and "disabled" in msg_dis.lower()
    assert not fake_desktop.exists()
    ok_en, _ = auditor.toggle_autorun_entry(ar, True, adapter, dry_run=False)
    assert ok_en is True and fake_desktop.exists()

    # Ghost driver artifact scan + quarantine + rollback
    ghost_dir = tmp_path / "drv_cache"
    ghost_dir.mkdir()
    ghost_file = ghost_dir / "old_gamepad.ghost_drv"
    ghost_file.write_text("OLD_DRIVER_DESCRIPTOR", encoding="utf-8")
    (ghost_dir / "dead_drv_link").symlink_to(tmp_path / "nonexistent_target")

    ghosts = auditor.scan_ghost_drivers(extra_search_dirs=[ghost_dir])
    file_ghosts = [g for g in ghosts if g.artifact_path]
    assert len(file_ghosts) >= 2

    for fg in file_ghosts:
        ok_cl, _ = auditor.clean_ghost_driver(fg, adapter, dry_run=False)
        assert ok_cl is True
    assert not ghost_file.exists()

    # Rollback restores quarantined ghost driver artifact cleanly
    for entry in rb.load_entries():
        if entry.action_id == "clean_ghost_driver":
            ok_rb, _ = rb.rollback_entry(entry.entry_id)
            assert ok_rb is True
    assert ghost_file.exists()


def test_multires_history_and_evidence_confidence_scoring(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path / "ac_hist")
    cfg = cfg_mgr.load()
    adapter = get_os_adapter("auto", cfg.custom_protected_paths)
    journal = ForensicJournal(cfg_mgr)
    hdb = TelemetryHistoryDB(cfg_mgr)

    snap = TelemetryCollector(cfg.excluded_processes).collect(adapter, include_dns=False)
    for idx in range(150):
        snap_copy = SystemSnapshot(**{**snap.__dict__, "cpu_percent": float(10 + (idx % 60))})
        hdb.record_tick(snap_copy, 80 + (idx % 15), persist_db=(idx % 25 == 0))

    for res in ("60s", "5m", "30m", "1hr", "30d"):
        pts = hdb.query_resolution(res, limit=120)
        assert len(pts) >= 2
        assert len(pts) <= 120

    rep = DiagnosticsEngine().run_scan(snap, adapter, cfg)
    assert len(rep.findings) >= 1
    for f in rep.findings:
        assert 35 <= f.confidence_percent <= 99
        assert len(f.evidence_signals) >= 1

    pb = PlaybookBuilder().build(rep, cfg)
    assert all(35 <= s.confidence_percent <= 99 and len(s.evidence_signals) >= 1 for s in pb.steps)

    ca = CrashAnalyzer(cfg_mgr, journal)
    cm = ca.simulate_crash_and_diagnose(cfg.active_profile)
    assert 35 <= cm.confidence_percent <= 99
    assert len(cm.evidence_scores) >= 1


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Requires X11/Xvfb DISPLAY")
def test_xvfb_v2_5_all_10_pages_and_widgets(tmp_path: Path) -> None:
    from astracradle.ui.app import AstraCradleApp

    # Verify locale key parity across en / es / fr
    en_keys = set(json.loads((Path(__file__).resolve().parents[1] / "locales" / "en.json").read_text()).keys())
    for lc in ("es", "fr"):
        lc_keys = set(json.loads((Path(__file__).resolve().parents[1] / "locales" / f"{lc}.json").read_text()).keys())
        assert en_keys - lc_keys == set()

    # Verify all 6 new Help topics are searchable
    for q in ("dpc", "probalance", "welford", "fan curve", "ghost driver", "confidence"):
        assert len(search_help_topics(q)) >= 1

    app = AstraCradleApp(base_dir=tmp_path / "ui_v25", show_startup_popups=False)
    try:
        app.geometry("1060x700+0+0")
        app.update()

        for nav in (
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
            app.switch_nav(nav)
            app.update()

        # Test multi-res telemetry chart switching on Command Deck
        app.switch_nav("dashboard")
        for res in ("60s", "5m", "30m", "1hr", "30d"):
            app.hist_res_seg.set(res)
            app._on_hist_resolution(res)
            app.update()
        assert app.history_chart.resolution == "30d"

        # Test Interactive Draft-vs-Applied FanCurveCanvas drag & apply
        app.switch_nav("hardware")
        app.hw_seg.set("GPU Power & Fan Curves")
        app._on_hw_tab("GPU Power & Fan Curves")
        app.update()
        assert hasattr(app, "fan_curve_canvas")
        evt_press = type("E", (), {"x": 180, "y": 90})()
        evt_drag = type("E", (), {"x": 200, "y": 60})()
        app.fan_curve_canvas._on_press(evt_press)
        app.fan_curve_canvas._on_drag(evt_drag)
        app.fan_curve_canvas._on_release(evt_drag)
        app.update()

        # Test all ProBalance & Latency tabs
        app.switch_nav("probalance")
        for ptab in ("ProBalance Engine", "Auto-Revert App Rules", "Interrupt & DPC Guard"):
            app.pb_seg.set(ptab)
            app._on_pb_tab(ptab)
            app.update()

        # Test all Autoruns & Baselines tabs
        app.switch_nav("autoruns")
        for atab in ("Autoruns & Services", "Ghost Driver Cleanup", "Thermal SPC Baselines"):
            app.auto_seg.set(atab)
            app._on_auto_tab(atab)
            app.update()

        # Verify topbar stays within 1060px bounds
        app.update_idletasks()
        assert app.scan_cta.winfo_x() + app.scan_cta.winfo_width() <= app.topbar.winfo_width() + 4
    finally:
        app.on_close()


def test_brutal_3x_chaos_torture_suite(tmp_path: Path) -> None:
    """Runs 3 independent full-intensity randomized chaos & debugger rounds across all v1.0.0 subsystems."""
    adapter = get_os_adapter("auto", [])

    class V25LineTracer(bdb.Bdb):
        def __init__(self) -> None:
            super().__init__()
            self.traced_files: set[str] = set()
            self.lines = 0

        def user_line(self, frame) -> None:
            fn = frame.f_code.co_filename
            if "astracradle" in fn:
                self.lines += 1
                self.traced_files.add(Path(fn).name)

    for chaos_round in range(3):
        rng = random.Random(20262500 + chaos_round * 7919)
        r_dir = tmp_path / f"chaos_round_{chaos_round}"

        # 1. Poisoned / truncated / bitflipped state files across all new v1.0.0 subsystems (12 trials per round)
        for trial in range(12):
            cfg_mgr = ConfigManager(base_dir=r_dir / f"fuzz_{trial}" / ".astracradle")
            journal = ForensicJournal(cfg_mgr)
            rb = RollbackManager(cfg_mgr)
            guard = InterruptAndDpcGuard(cfg_mgr, journal)
            pb = ProBalanceEngine(cfg_mgr, journal)
            spc = ThermalSpcMonitor(cfg_mgr, journal)
            gctrl = GpuPowerAndFanController(cfg_mgr, journal)
            hdb = TelemetryHistoryDB(cfg_mgr)

            # Seed valid files
            cfg = cfg_mgr.load()
            guard.save_rules(guard.load_rules())
            pb.save_rules(pb.load_rules())
            spc.update_sample("heavy", 62.0, 55.0, power_w=120.0)
            gctrl.apply_draft_curve([[40, 30], [80, 90]], dry_run=False)
            snap = TelemetryCollector(cfg.excluded_processes).collect(adapter, include_dns=False)
            hdb.record_tick(snap, 88, persist_db=True)

            targets = [
                cfg_mgr.config_path,
                guard.rules_path,
                pb.rules_path,
                pb.state_path,
                spc.path,
                gctrl.profiles_path,
                gctrl.applied_curve_path,
                hdb.db_path,
            ]
            for p in rng.sample(targets, k=rng.randint(3, len(targets))):
                mode = rng.choice(["empty", "nulls", "truncate", "bitflip", "poison"])
                if mode == "empty":
                    p.write_bytes(b"")
                elif mode == "nulls":
                    p.write_bytes(bytes(rng.choices([0, 255, 13, 123, 34], k=rng.randint(8, 80))))
                elif mode == "truncate" and p.exists():
                    raw = p.read_bytes()
                    p.write_bytes(raw[: rng.randint(0, max(0, len(raw) - 1))])
                elif mode == "bitflip" and p.exists():
                    buf = bytearray(p.read_bytes() or b"{}")
                    for _ in range(rng.randint(1, min(12, len(buf)))):
                        buf[rng.randrange(len(buf))] ^= rng.randint(1, 255)
                    p.write_bytes(bytes(buf))
                else:
                    p.write_text(
                        rng.choice(["null", "[]", '{"light": {"count": "NaN", "m2_temp": -999}}', "12345"]),
                        encoding="utf-8",
                    )

            # Every loader & query must survive cleanly without raising
            assert cfg_mgr.load() is not None
            assert len(guard.load_rules()) >= 1
            assert len(pb.load_rules()) >= 1
            assert len(spc.load_baselines()) == 4
            assert len(gctrl.load_profiles()) >= 1
            assert len(gctrl.get_applied_curve()[1]) >= 2
            assert isinstance(hdb.query_resolution("60s"), list)
            assert isinstance(hdb.query_resolution("30d"), list)

        # 2. Extreme NaN / Inf / out-of-bounds torture on Welford SPC & Fan Curve
        cfg_mgr = ConfigManager(base_dir=r_dir / "extreme" / ".astracradle")
        journal = ForensicJournal(cfg_mgr)
        spc = ThermalSpcMonitor(cfg_mgr, journal)
        for bad_t in (None, float("nan"), float("inf"), float("-inf"), -500.0, 9999.0, 48.0):
            for bad_cpu in (float("nan"), -20.0, 250.0, 45.0):
                res = spc.update_sample(rng.choice(["idle", "gaming", "bogus_tier"]), bad_t, bad_cpu, power_w=bad_t)
                assert isinstance(res, list)
        clamped = clamp_fan_curve_points([[-999, -50], [999, 500], ["bad", "pt"], [60, 70]])  # type: ignore[list-item]
        assert len(clamped) == 3
        assert all(35 <= t <= 95 and 20 <= f <= 100 for t, f in clamped)

        # 3. Symlink escape & protected-path refusal on Autoruns & Ghost Drivers
        rb = RollbackManager(cfg_mgr)
        auditor = AutorunsAndGhostAuditor(cfg_mgr, rb, journal)
        secret = r_dir / "protected_secret.txt"
        secret.write_text("KEEP_ME_SAFE", encoding="utf-8")
        prot_adapter = get_os_adapter("auto", [str(secret)])
        link_p = r_dir / "evil_autostart.desktop"
        link_p.symlink_to(secret)
        bad_ar = AutorunEntry("x", "startup", "EvilLink", "user", str(link_p), "evil", "low", True, True, str(link_p))
        ok_tog, _ = auditor.toggle_autorun_entry(bad_ar, False, prot_adapter, dry_run=False)
        assert ok_tog is False and secret.exists()

        # 4. ProBalance rapid launch-escalate-exit-revert storm (20 ticks per round) + protected immunity
        pb = ProBalanceEngine(cfg_mgr, journal)
        cfg = cfg_mgr.load()
        cfg.probalance_enabled = True
        for step in range(20):
            immunes = [
                ProcessInfo(1, "init", "root", 95.0, 50.0, 1.0, "running", protected=True),
                ProcessInfo(2, "systemd", "root", 98.0, 120.0, 1.0, "running", protected=True),
                ProcessInfo(3, "python3", "user", 99.0, 100.0, 1.0, "running", protected=True),
            ]
            app_list = immunes + (
                [ProcessInfo(50000 + step, "blender", "user", 85.0, 2000.0, 12.0, "running", protected=False)]
                if step % 2 == 0
                else []
            )
            pb.evaluate_tick(app_list, "desktop", cfg, dry_run=True)
            assert not any(pid in pb.restrained for pid in (1, 2, 3))
        pb.revert_all(cfg, dry_run=True)

        # 5. Live bdb line-by-line debugger trace across v1.0.0 modules
        tracer = V25LineTracer()
        guard = InterruptAndDpcGuard(cfg_mgr, journal)

        def _traced():
            r1 = guard.measure_dpc_latency(samples=6, sleep_us=150)
            spc.update_sample("idle", 41.0, 5.0)
            pb.evaluate_tick([], "desktop", cfg, dry_run=True)
            auditor.scan_ghost_drivers()
            return r1

        traced_rep = tracer.runcall(_traced)
        assert traced_rep.samples == 6
        assert tracer.lines > 50
        assert "latency_guard.py" in tracer.traced_files
        assert "thermal_spc.py" in tracer.traced_files
        assert "probalance.py" in tracer.traced_files
        assert "autoruns_audit.py" in tracer.traced_files
