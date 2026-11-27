from __future__ import annotations

import hashlib
import json
from pathlib import Path

from astracradle.core.config import ConfigManager
from astracradle.core.crash_analyzer import CrashAnalyzer
from astracradle.core.journal import ForensicJournal
from astracradle.core.updater import ModuleAndUpdateManager


def test_crash_post_mortem_and_unhandled_exception(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path)
    journal = ForensicJournal(mgr)
    ca = CrashAnalyzer(mgr, journal)

    # Clean run should report None on next startup
    ca.mark_running("Balanced Daily")
    ca.mark_clean_shutdown()
    assert ca.inspect_on_startup() is None

    # Simulate crash & verify post-mortem diagnosis
    report = ca.simulate_crash_and_diagnose("3D Render & Benchmark")
    assert report.crash_type == "simulated_crash"
    assert len(report.fix_steps) >= 1
    assert len(ca.load_history()) >= 1

    # Record unhandled exception
    try:
        raise RuntimeError("Simulated GPU driver hang")
    except RuntimeError as exc:
        log_f = ca.record_unhandled_exception(exc)
        assert log_f.exists()

    exc_report = ca.inspect_on_startup()
    assert exc_report is not None
    assert exc_report.crash_type == "python_exception"


def test_modular_plugins_and_verified_seamless_update(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path)
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    upd = ModuleAndUpdateManager(mgr, journal)

    mod = upd.register_sample_plugin("SMART NVMe Health", "Extended NVMe wear telemetry module")
    assert any(m.module_id == mod.module_id for m in upd.list_modules())

    # Stage an authentic update package
    pkg_file = mgr.updates_dir / "patch_2_9_0.json"
    pkg_bytes = b'{"patch": "improved-3d-benchmark-heuristics"}'
    pkg_file.write_bytes(pkg_bytes)
    digest = hashlib.sha256(pkg_bytes).hexdigest()

    manifest = {
        "version": "2.9.0",
        "notes": "Improved 3D render & AI detector heuristics",
        "package_file": pkg_file.name,
        "sha256": digest,
    }
    (mgr.updates_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    check = upd.check_for_updates(cfg)
    assert check.update_available is True
    ok, msg = upd.stage_and_apply_local_update(cfg)
    assert ok is True
    assert "2.9.0" in msg

    # Tampered package must be rejected
    pkg_file.write_bytes(b'{"patch": "TAMPERED"}')
    ok_bad, msg_bad = upd.stage_and_apply_local_update(cfg)
    assert ok_bad is False
    assert "SHA-256" in msg_bad
