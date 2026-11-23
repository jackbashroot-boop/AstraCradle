from __future__ import annotations

from pathlib import Path

from astracradle.core.config import ConfigManager
from astracradle.core.executor import GuardedExecutor
from astracradle.core.journal import ForensicJournal
from astracradle.core.models import ProcessInfo, StartupItem
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.process_advisor import ProcessAdvisor
from astracradle.core.rollback import RollbackManager
from astracradle.core.scripts_engine import ScriptsEngine


def test_ai_chatbot_and_idle_process_hunter() -> None:
    cfg = ConfigManager().load()
    cfg.consent_process_scan = True
    advisor = ProcessAdvisor()

    procs = [
        ProcessInfo(pid=9001, name="ollama", username="user", cpu_percent=0.1, memory_mb=1420.0, memory_percent=8.0, status="sleeping", cmdline="ollama serve"),
        ProcessInfo(pid=9002, name="lm-studio-helper", username="user", cpu_percent=0.0, memory_mb=320.0, memory_percent=2.0, status="sleeping", cmdline="lmstudio --bg"),
        ProcessInfo(pid=9003, name="systemd", username="root", cpu_percent=0.0, memory_mb=15.0, memory_percent=0.1, status="sleeping", protected=True),
        ProcessInfo(pid=9004, name="code-updater", username="user", cpu_percent=0.0, memory_mb=95.0, memory_percent=0.5, status="sleeping"),
    ]
    startups = [
        StartupItem("1", "ChatGPT Helper", "~/.config/autostart/chatgpt.desktop", "chatgpt --tray", "user", True, "medium", "Linux")
    ]

    recs = advisor.analyze(procs, startups, cfg)
    names = [r.name for r in recs]
    assert any("ollama" in n for n in names)
    assert any("lm-studio-helper" in n for n in names)
    assert any("ChatGPT Helper" in n for n in names)
    assert not any("systemd" in n for n in names)

    # Respects user consent toggle
    cfg.consent_process_scan = False
    assert advisor.analyze(procs, startups, cfg) == []


def test_toggleable_scripts_crud_and_dry_run(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path)
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    exe = GuardedExecutor(mgr, RollbackManager(mgr), journal)
    engine = ScriptsEngine(mgr, exe, journal)
    adapter = get_os_adapter()

    scripts = engine.load_scripts()
    assert len(scripts) >= 4
    ids = {s.script_id for s in scripts}
    assert {"script_gaming_max", "script_3d_render", "script_compile_data", "script_ai_hibernate"}.issubset(ids)

    # Toggle Gaming script ON then OFF
    ok, msg = engine.toggle_script("script_gaming_max", True, adapter, cfg, [], dry_run=False)
    assert ok is True
    assert mgr.load().active_profile == "Gaming / Low-Latency"

    ok_off, _ = engine.toggle_script("script_gaming_max", False, adapter, cfg, [], dry_run=False)
    assert ok_off is True
    assert mgr.load().active_profile == "Balanced Daily"

    # Add custom script & remove it
    custom = engine.add_custom_script("Blender Cycles Boost", "3d_render", "Custom render boost", "", "3D Render & Benchmark")
    assert any(s.script_id == custom.script_id for s in engine.load_scripts())
    ok_rm, _ = engine.remove_script(custom.script_id)
    assert ok_rm is True
    assert not any(s.script_id == custom.script_id for s in engine.load_scripts())
