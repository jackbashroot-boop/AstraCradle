from __future__ import annotations

import os
from pathlib import Path
import time

from astracradle.core.autopilot import AutopilotEngine
from astracradle.core.config import ConfigManager
from astracradle.core.diagnostics import DiagnosticsEngine
from astracradle.core.executor import GuardedExecutor
from astracradle.core.journal import ForensicJournal
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.playbook import PlaybookBuilder
from astracradle.core.rollback import RollbackManager
from astracradle.core.self_heal import SelfHealEngine
from astracradle.core.telemetry import TelemetryCollector


def test_protected_paths_enforced_across_all_os_adapters(tmp_path: Path) -> None:
    custom_dir = tmp_path / "important_vault"
    custom_dir.mkdir()

    for os_name, dangerous_paths in [
        ("Linux", ["/usr/bin", "/etc/passwd", str(Path.home() / "Documents")]),
        ("Darwin", ["/System/Library", "/Applications", str(Path.home() / "Desktop")]),
        ("Windows", ["C:\\Windows\\System32", "C:\\Program Files", str(Path.home() / "Documents")]),
    ]:
        adapter = get_os_adapter(override=os_name, custom_protected_paths=[str(custom_dir)])
        assert adapter.is_path_protected(custom_dir) is True
        for dp in dangerous_paths:
            assert adapter.is_path_protected(dp) is True


def test_dry_run_quarantine_rollback_and_playbook(tmp_path: Path) -> None:
    state_root = tmp_path / "ac_state"
    scratch_dir = tmp_path / "scratch_cache"
    scratch_dir.mkdir()
    sample = scratch_dir / "stale_cache.bin"
    sample.write_bytes(b"astracradle-quarantine-test")
    old = time.time() - 3600
    os.utime(sample, (old, old))

    mgr = ConfigManager(base_dir=state_root)
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    exe = GuardedExecutor(mgr, rb, journal)
    adapter = get_os_adapter()

    dry_res = exe.execute_action("clean_paths", adapter, cfg, params={"paths": [str(scratch_dir)]}, dry_run=True)
    assert dry_res.ok and dry_res.dry_run and sample.exists()

    live_res = exe.execute_action("clean_paths", adapter, cfg, params={"paths": [str(scratch_dir)]}, dry_run=False)
    assert live_res.ok and not sample.exists() and live_res.entry_id is not None

    ok_rb, _ = rb.rollback_entry(live_res.entry_id)
    assert ok_rb and sample.read_bytes() == b"astracradle-quarantine-test"

    snap = TelemetryCollector(cfg.excluded_processes).collect(adapter)
    report = DiagnosticsEngine().run_scan(snap, adapter, cfg)
    pb = PlaybookBuilder().build(report, cfg)
    md_path = PlaybookBuilder().export(pb, mgr.playbooks_dir, fmt="md")
    assert md_path.exists()

    auto = AutopilotEngine(mgr, exe, SelfHealEngine(mgr, rb, journal), journal)
    assert len(auto.evaluate_once(snap, adapter, cfg)) >= 1
