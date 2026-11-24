from __future__ import annotations

import os
from pathlib import Path
import time

from astracradle.core.config import ConfigManager
from astracradle.core.executor import GuardedExecutor
from astracradle.core.journal import ForensicJournal
from astracradle.core.lifecycle import LifecycleManager
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.core.scripts_engine import ScriptsEngine


def test_uninstall_restores_quarantine_reverts_scripts_and_deletes_all_data(tmp_path: Path) -> None:
    data_dir = tmp_path / ".astracradle"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sample = scratch / "temp_cache.tmp"
    sample.write_bytes(b"restore-me-on-uninstall")
    old = time.time() - 3600
    os.utime(sample, (old, old))

    mgr = ConfigManager(base_dir=data_dir)
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    exe = GuardedExecutor(mgr, rb, journal)
    scripts = ScriptsEngine(mgr, exe, journal)
    lifecycle = LifecycleManager(mgr, rb, journal, scripts)
    adapter = get_os_adapter()

    # Enable a toggle script and quarantine a file
    scripts.toggle_script("script_gaming_max", True, adapter, cfg, [], dry_run=False)
    res = exe.execute_action("clean_paths", adapter, cfg, params={"paths": [str(scratch)]}, dry_run=False)
    assert res.ok and not sample.exists()

    # Create a simulated shortcut file
    fake_shortcut = tmp_path / "AstraCradle.desktop"
    fake_shortcut.write_text("[Desktop Entry]\nName=AstraCradle\n", encoding="utf-8")

    summary = lifecycle.uninstall_astracradle(
        adapter,
        cfg,
        delete_user_data=True,
        restore_quarantine_first=True,
        uninstall_pip_package=False,
        extra_shortcut_paths=[fake_shortcut],
    )

    assert summary.ok is True
    assert summary.reverted_scripts == 1
    assert summary.restored_quarantine_entries == 1
    assert sample.exists() and sample.read_bytes() == b"restore-me-on-uninstall"
    assert str(fake_shortcut) in summary.removed_shortcuts
    assert not fake_shortcut.exists()
    assert summary.deleted_data_dir is True
    assert not data_dir.exists()


def test_uninstall_keep_data_preserves_profiles_and_blocks_unsafe_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / ".astracradle"
    mgr = ConfigManager(base_dir=data_dir)
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    lifecycle = LifecycleManager(mgr, rb, journal)
    adapter = get_os_adapter()

    summary_keep = lifecycle.uninstall_astracradle(
        adapter, cfg, delete_user_data=False, restore_quarantine_first=False
    )
    assert summary_keep.ok is True
    assert summary_keep.deleted_data_dir is False
    assert data_dir.exists()

    # Safety guardrail must refuse unsafe non-astracradle dirs like Home
    assert LifecycleManager._is_safe_astracradle_dir(Path.home()) is False
    assert LifecycleManager._is_safe_astracradle_dir(Path.home() / "Documents") is False
