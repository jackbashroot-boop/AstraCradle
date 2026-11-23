from __future__ import annotations

import bdb
import binascii
import compileall
import dis
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import socket
import struct
import time
from unittest.mock import patch

from astracradle.core.config import AppConfig, ConfigManager
from astracradle.core.crash_analyzer import CrashAnalyzer
from astracradle.core.diagnostics import DiagnosticsEngine
from astracradle.core.executor import GuardedExecutor
from astracradle.core.journal import ForensicJournal
from astracradle.core.models import PartitionInfo, ProcessInfo, SystemSnapshot
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.playbook import PlaybookBuilder
from astracradle.core.process_advisor import ProcessAdvisor
from astracradle.core.rollback import RollbackManager
from astracradle.core.scripts_engine import ScriptsEngine
from astracradle.core.self_heal import SelfHealEngine
from astracradle.core.telemetry import TelemetryCollector, format_bytes, format_uptime


class LineCountingDebugger(bdb.Bdb):
    """Real bdb debugger stepping line-by-line across AstraCradle modules."""

    def __init__(self) -> None:
        super().__init__()
        self.lines_executed = 0
        self.files_touched: set[str] = set()
        self.exceptions: list[str] = []

    def user_line(self, frame) -> None:
        fn = frame.f_code.co_filename
        if "astracradle" in fn:
            self.lines_executed += 1
            self.files_touched.add(Path(fn).name)

    def user_exception(self, frame, exc_info) -> None:
        fn = frame.f_code.co_filename
        if "astracradle" in fn:
            self.exceptions.append(f"{Path(fn).name}:{frame.f_lineno} {exc_info[0].__name__}")


def test_bytecode_compile_pyc_hex_and_disassembly() -> None:
    pkg_root = Path(__file__).resolve().parents[1] / "astracradle"
    assert compileall.compile_dir(str(pkg_root), force=True, quiet=1) is True

    py_files = sorted(pkg_root.rglob("*.py"))
    assert len(py_files) >= 20
    expected_magic = importlib.util.MAGIC_NUMBER

    for pf in py_files:
        pyc_path = Path(importlib.util.cache_from_source(str(pf)))
        assert pyc_path.exists()
        raw = pyc_path.read_bytes()
        assert len(raw) > 16
        assert raw[:4] == expected_magic
        _flags, _mtime, _sz = struct.unpack("<III", raw[4:16])
        hex_hdr = binascii.hexlify(raw[:16]).decode()
        assert len(hex_hdr) == 32
        assert len(hashlib.sha256(raw).hexdigest()) == 64

    instrs = list(dis.get_instructions(GuardedExecutor.execute_action))
    assert any(ins.opname == "CALL" or "CALL" in ins.opname for ins in instrs)


def test_line_by_line_bdb_debugger_trace(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    heal = SelfHealEngine(mgr, rb, journal)
    adapter = get_os_adapter()

    dbg = LineCountingDebugger()

    def traced_flow():
        cfg = mgr.load()
        snap = TelemetryCollector(cfg.excluded_processes).collect(adapter, include_dns=False)
        rep = DiagnosticsEngine().run_scan(snap, adapter, cfg)
        pb = PlaybookBuilder().build(rep, cfg)
        events, audits = heal.run_self_check(adapter, cfg)
        return pb, events, audits

    pb, events, audits = dbg.runcall(traced_flow)
    assert dbg.lines_executed > 100
    assert "self_heal.py" in dbg.files_touched
    assert "diagnostics.py" in dbg.files_touched
    assert len(events) == 7
    assert audits[0].severity == "ok"
    assert pb.health_score >= 12


def test_power_outage_poisoned_files_and_mid_write_recovery(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    exe = GuardedExecutor(mgr, rb, journal)
    heal = SelfHealEngine(mgr, rb, journal)
    ca = CrashAnalyzer(mgr, journal)
    se = ScriptsEngine(mgr, exe, journal)
    adapter = get_os_adapter()

    # Dual binary garbage on config + backup
    mgr.config_path.write_bytes(b"\xff\xfe\x00\x00GARBAGE")
    mgr.config_bak_path.write_bytes(b"\x00\x00\x00")
    assert isinstance(mgr.load(), AppConfig)

    # Type-poisoned JSON config (NaN, wrong types, nulls)
    mgr.config_path.write_text(
        json.dumps(
            {
                "excluded_processes": None,
                "quarantine_max_mb": "not-an-int",
                "disk_safety_floor_percent": "nan",
                "automation_rules": ["not_a_dict", None],
                "auto_self_heal": "invalid_bool",
            }
        ),
        encoding="utf-8",
    )
    cfg = mgr.load()
    assert isinstance(cfg.quarantine_max_mb, int)
    assert isinstance(cfg.excluded_processes, list) and len(cfg.excluded_processes) > 0
    events, _ = heal.run_self_check(adapter, cfg)
    assert len(events) == 7

    # Partial rollback ledger keeps valid records & drops corrupt items
    valid_entry = {
        "entry_id": "act-valid1",
        "timestamp": "2026-11-16T00:00:00+00:00",
        "action_id": "clean_temp_caches",
        "title": "Valid",
        "dry_run": True,
        "status": "applied",
        "summary": "ok",
    }
    mgr.rollback_path.write_text(json.dumps([valid_entry, None, {"broken": True}]), encoding="utf-8")
    loaded = rb.load_entries()
    assert len(loaded) == 1 and loaded[0].entry_id == "act-valid1"

    # Malformed non-dict forensic lines don't crash reader or self-auditor
    mgr.forensic_log_path.write_text('{"entry_id": "x"}\n[1,2,3]\n12345\n\x00\x00\n', encoding="utf-8")
    audits = journal.verify_and_audit()
    assert any(a.severity == "flaw" for a in audits)
    assert isinstance(journal.read_entries(), list)

    # Poisoned scripts, self_heal & crash history
    mgr.scripts_path.write_text('[null, {"script_id": "bad"}, "str"]', encoding="utf-8")
    assert len(se.load_scripts()) >= 4
    mgr.self_heal_log_path.write_text('[null, 42, {"event_id": "x"}]', encoding="utf-8")
    assert heal.load_ledger() == []
    mgr.crash_history_path.write_text('[null, 42, {"incident_id": "x"}]', encoding="utf-8")
    assert ca.load_history() == []


def test_internet_removed_dns_blackhole_and_reboot_chaos(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    ca = CrashAnalyzer(mgr, journal)
    tc = TelemetryCollector(cfg.excluded_processes)

    # Simulate stalled DNS blackhole (would hang 5s without bounded timeout)
    def slow_dns(*args, **kwargs):
        time.sleep(2.0)
        raise socket.gaierror(-3, "No network")

    t0 = time.perf_counter()
    with patch("socket.getaddrinfo", side_effect=slow_dns):
        lat = tc.measure_dns_latency_ms(timeout_sec=0.25)
    elapsed = time.perf_counter() - t0
    assert lat is None
    assert elapsed < 0.8

    # Simulate complete power-off & OS reboot mid-session
    ca.mark_running("Gaming / Low-Latency")
    hb = json.loads(mgr.heartbeat_path.read_text(encoding="utf-8"))
    hb["boot_time"] = float(hb["boot_time"]) - 7200.0
    mgr.heartbeat_path.write_text(json.dumps(hb), encoding="utf-8")
    report = ca.inspect_on_startup()
    assert report is not None
    assert report.crash_type == "os_crash_or_power_loss"
    assert len(report.fix_steps) >= 2


def test_symlink_escape_destructive_script_and_extreme_values(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    exe = GuardedExecutor(mgr, rb, journal)
    se = ScriptsEngine(mgr, exe, journal)

    # Symlink escape attack inside cache targeting a protected file
    secret = tmp_path / "secret.txt"
    secret.write_text("DO NOT TOUCH", encoding="utf-8")
    adapter = get_os_adapter(custom_protected_paths=[str(secret)])
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "link.txt").symlink_to(secret)
    exe.execute_action("clean_paths", adapter, cfg, params={"paths": [str(cache_dir)]}, dry_run=False)
    assert secret.exists()

    # Destructive custom script command MUST be blocked
    bad = se.add_custom_script("Bad", "custom", "Destructive", "rm -rf /", "")
    ok, msg = se.toggle_script(bad.script_id, True, adapter, cfg, [], dry_run=False)
    assert ok is False and "blocked" in msg.lower()

    # Extreme numeric inputs
    assert format_bytes(-100) == "0 B"
    assert format_bytes(float("nan")) == "0 B"
    assert format_bytes(float("inf")) == "0 B"
    assert format_uptime(-42) == "0m"


def test_randomized_fuzz_chaos_files_quarantine_scripts_and_telemetry(tmp_path: Path) -> None:
    rng = random.Random(20261116)
    adapter = get_os_adapter()

    # 1. Randomized byte-flip / truncation / type-poison fuzz across state files (20 trials)
    for trial in range(20):
        base = tmp_path / f"fuzz_{trial}" / ".astracradle"
        mgr = ConfigManager(base_dir=base)
        journal = ForensicJournal(mgr)
        rb = RollbackManager(mgr)
        exe = GuardedExecutor(mgr, rb, journal)
        heal = SelfHealEngine(mgr, rb, journal)
        ca = CrashAnalyzer(mgr, journal)
        se = ScriptsEngine(mgr, exe, journal)

        # Seed valid state first
        cfg = mgr.load()
        mgr.save(cfg)
        journal.log_event("Fuzz", "init", "seed", "chaos", "ok")
        se.load_scripts()
        ca.mark_running("Balanced Daily")

        targets = [
            mgr.config_path,
            mgr.config_bak_path,
            mgr.rollback_path,
            mgr.forensic_log_path,
            mgr.scripts_path,
            mgr.self_heal_log_path,
            mgr.crash_history_path,
            mgr.heartbeat_path,
        ]
        for p in rng.sample(targets, k=rng.randint(2, len(targets))):
            mode = rng.choice(["truncate", "bitflip", "nulls", "poison_json", "empty"])
            if mode == "empty":
                p.write_bytes(b"")
            elif mode == "nulls":
                p.write_bytes(bytes(rng.choices([0, 255, 10, 123, 34], k=rng.randint(4, 96))))
            elif mode == "truncate" and p.exists():
                raw = p.read_bytes()
                cut = rng.randint(0, max(0, len(raw) - 1))
                p.write_bytes(raw[:cut])
            elif mode == "bitflip" and p.exists():
                buf = bytearray(p.read_bytes() or b"{}")
                for _ in range(rng.randint(1, min(16, len(buf)))):
                    idx = rng.randrange(len(buf))
                    buf[idx] ^= rng.randint(1, 255)
                p.write_bytes(bytes(buf))
            else:
                poison = rng.choice(
                    [
                        "null",
                        "12345",
                        '["str", null, 99]',
                        '{"excluded_processes": 42, "quarantine_max_mb": "bad", "auto_self_heal": "no"}',
                    ]
                )
                p.write_text(poison, encoding="utf-8")

        loaded_cfg = mgr.load()
        assert isinstance(loaded_cfg, AppConfig)
        assert isinstance(rb.load_entries(), list)
        assert isinstance(journal.verify_and_audit(), list)
        assert len(se.load_scripts()) >= 1
        events, audits = heal.run_self_check(adapter, loaded_cfg)
        assert len(events) == 7 and isinstance(audits, list)
        ca.inspect_on_startup()

    # 2. Randomized quarantine + mid-rollback deletion race (10 trials)
    for trial in range(10):
        base = tmp_path / f"race_{trial}" / ".astracradle"
        mgr = ConfigManager(base_dir=base)
        cfg = mgr.load()
        journal = ForensicJournal(mgr)
        rb = RollbackManager(mgr)
        exe = GuardedExecutor(mgr, rb, journal)

        work = tmp_path / f"work_{trial}"
        work.mkdir()
        created = []
        for idx in range(rng.randint(3, 8)):
            f = work / f"tmp_{idx}_{rng.randint(100,999)}.log"
            f.write_bytes(os.urandom(rng.randint(16, 512)))
            created.append(f)

        res = exe.execute_action("clean_paths", adapter, cfg, params={"paths": [str(work)]}, dry_run=False)
        assert isinstance(res.ok, bool)
        entries = rb.load_entries()
        # Randomly vanish some quarantine items before rollback
        if entries:
            for item in entries[-1].quarantine_paths:
                qp = Path(item.get("quarantine", ""))
                if qp.exists() and rng.random() < 0.5:
                    qp.unlink()
            ok, msg = rb.rollback_entry(entries[-1].entry_id)
            assert isinstance(ok, bool) and len(msg) > 0

    # 3. Randomized shell-injection & destructive script fuzzing
    evil_cmds = [
        "  RM   -RF   /  ",
        "rm -fr /*",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "DEL /F /S /Q C:\\Windows",
        "echo hi && reboot",
        "ls; rm -rf /tmp",
        "python3 -c 'print(1)' || poweroff",
        "$(whoami)",
        "`id`",
    ]
    mgr = ConfigManager(base_dir=tmp_path / "evil" / ".astracradle")
    cfg = mgr.load()
    se = ScriptsEngine(mgr, GuardedExecutor(mgr, RollbackManager(mgr), ForensicJournal(mgr)), ForensicJournal(mgr))
    for i, cmd in enumerate(evil_cmds):
        safe, _ = se.is_command_safe(cmd)
        assert safe is False, f"Unsafe command allowed: {cmd}"
        sc = se.add_custom_script(f"Evil{i}", "custom", "Fuzzed", cmd, "")
        ok, _ = se.toggle_script(sc.script_id, True, adapter, cfg, [], dry_run=False)
        assert ok is False

    # 4. Randomized extreme telemetry + ProcessAdvisor + PlaybookBuilder fuzzing (15 trials)
    advisor = ProcessAdvisor()
    diag = DiagnosticsEngine()
    builder = PlaybookBuilder()
    cfg.consent_process_scan = True
    for _ in range(15):
        cpu = rng.choice([-5.0, 0.0, 50.0, 100.0, 140.0])
        ram = rng.choice([0.0, 12.5, 92.0, 100.0, 110.0])
        total_mem = 16 * 1024**3
        used_mem = int(total_mem * max(0.0, min(1.0, ram / 100.0)))
        procs = [
            ProcessInfo(
                pid=rng.randint(10, 99999),
                name=rng.choice(["ollama", "lmstudio", "systemd", "python3", "idle_agent", "unknown_bin"]),
                username="user",
                cpu_percent=rng.uniform(0.0, 99.0),
                memory_mb=rng.uniform(0.0, 8192.0),
                memory_percent=rng.uniform(0.0, 50.0),
                status=rng.choice(["running", "sleeping", "idle"]),
                cmdline="",
                protected=rng.random() < 0.3,
            )
            for _ in range(rng.randint(0, 12))
        ]
        snap = SystemSnapshot(
            timestamp="2026-11-16T00:00:00+00:00",
            os_family="Linux",
            os_release="6.1",
            hostname="chaos-box",
            architecture="x86_64",
            uptime_seconds=rng.choice([0, 3600, 9999999]),
            is_elevated=False,
            cpu_percent=cpu,
            cpu_per_core=[cpu],
            cpu_count_logical=rng.choice([1, 4, 16]),
            cpu_freq_mhz=3600.0,
            load_avg=(0.5, 0.4, 0.3),
            mem_total_bytes=total_mem,
            mem_used_bytes=used_mem,
            mem_available_bytes=max(0, total_mem - used_mem),
            mem_percent=ram,
            swap_total_bytes=4 * 1024**3,
            swap_used_bytes=0,
            swap_percent=rng.uniform(0.0, 100.0),
            partitions=[
                PartitionInfo(
                    device="/dev/sda1",
                    mountpoint="/",
                    fstype="ext4",
                    total_bytes=500 * 1024**3,
                    used_bytes=int(500 * 1024**3 * rng.uniform(0.1, 0.99)),
                    free_bytes=int(500 * 1024**3 * rng.uniform(0.01, 0.9)),
                    percent=rng.uniform(1.0, 99.5),
                )
            ],
            net_bytes_sent=rng.randint(0, 10**9),
            net_bytes_recv=rng.randint(0, 10**9),
            dns_latency_ms=rng.choice([None, 12.4, 9999.0]),
            battery_percent=None,
            battery_plugged=None,
            temperature_c=rng.choice([None, 42.0, 98.5]),
            process_count=len(procs),
            top_processes=procs,
        )
        recs = advisor.analyze(procs, [], cfg)
        assert isinstance(recs, list)
        rep = diag.run_scan(snap, adapter, cfg)
        pb = builder.build(rep, cfg)
        assert 0 <= pb.health_score <= 100
        assert len(pb.steps) >= 1

