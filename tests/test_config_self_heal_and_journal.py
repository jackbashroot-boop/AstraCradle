from __future__ import annotations

import json
import os
from pathlib import Path
import time

from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.core.self_heal import SelfHealEngine


def test_self_heal_and_forensic_journal_chain(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path)
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    heal = SelfHealEngine(mgr, rb, journal)
    adapter = get_os_adapter()

    cfg = mgr.load()
    cfg.active_profile = "3D Render & Benchmark"
    mgr.save(cfg)

    journal.log_event("TestSuite", "step_one", "Created baseline config", "Verify chained logging", "ok")
    journal.log_event("TestSuite", "step_two", "Updated profile", "Verify SHA-256 continuity", "ok")

    # Inject multiple faults
    mgr.config_path.write_text("{CORRUPT:::", encoding="utf-8")
    mgr.rollback_path.write_text("BAD_JSON", encoding="utf-8")
    mgr.playbooks_dir.rmdir()
    orphan = mgr.state_dir / "old_write.tmp"
    orphan.write_text("temp", encoding="utf-8")
    old_t = time.time() - 60
    os.utime(orphan, (old_t, old_t))

    events, audits = heal.run_self_check(adapter, cfg)
    statuses = {e.check_name: e for e in events}

    assert statuses["Workspace Tree Integrity"].repaired_automatically is True
    assert statuses["Configuration & Schema Guard"].repaired_automatically is True
    assert mgr.load().active_profile == "3D Render & Benchmark"
    assert statuses["Rollback Ledger Integrity"].repaired_automatically is True
    assert statuses["Orphaned Temp Artifact Sweep"].repaired_automatically is True
    assert audits[0].severity == "ok"


def test_forensic_self_audit_detects_tampering_and_recurring_errors(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path)
    journal = ForensicJournal(mgr)

    journal.log_event("Executor", "flush_dns", "Attempt 1", "Test recurring error", "error")
    journal.log_event("Executor", "flush_dns", "Attempt 2", "Test recurring error", "error")
    # Append malformed line
    with mgr.forensic_log_path.open("a", encoding="utf-8") as fh:
        fh.write("{corrupt_jsonl_line\n")

    audits = journal.verify_and_audit()
    severities = {a.severity for a in audits}
    assert "flaw" in severities
    assert "error" in severities
