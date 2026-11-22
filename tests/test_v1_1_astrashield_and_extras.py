"""Tests for AstraShield (Pi-hole DNS/Hosts blocker), Snapshot Diff, Startup Delay, Network/RAM/Battery, Safe Fix & Workflows."""

from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile

from astracradle.core.adblock_shield import AstraShieldManager, _parse_dns_qname
from astracradle.core.config import ConfigManager
from astracradle.core.executor import GuardedExecutor
from astracradle.core.journal import ForensicJournal
from astracradle.core.lifecycle import LifecycleManager
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.pc_extras import (
    BaselineDiffEngine,
    BatteryAndPowerInspector,
    MemoryStandbyManager,
    NetworkAndDnsInspector,
    SafeBeginnerFixer,
    StartupDelayManager,
)
from astracradle.core.rollback import RollbackManager
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.tray import TrayController


def _build_dns_query(domain: str, qtype: int = 1) -> bytes:
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in domain.split(".")) + b"\x00"
    question = qname + struct.pack("!HH", qtype, 1)
    return header + question


def test_astrashield_pihole_dns_packet_allowlist_and_hosts_toggle() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "state"
        hosts_file = Path(td) / "hosts"
        hosts_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")

        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        shield = AstraShieldManager(cfg_mgr, rb, j, hosts_path=hosts_file)

        # 1) Hosts sinkhole toggle ON/OFF with 100% clean restore
        ok_on, msg_on = shield.enable_hosts_sinkhole(["ads", "trackers"], dry_run=False)
        assert ok_on, msg_on
        assert shield.is_hosts_active()
        txt = hosts_file.read_text(encoding="utf-8")
        assert "0.0.0.0 doubleclick.net" in txt

        ok_off, _ = shield.disable_hosts_sinkhole()
        assert ok_off
        assert not shield.is_hosts_active()
        assert hosts_file.read_text(encoding="utf-8").strip() == "127.0.0.1 localhost"

        # 2) Real UDP DNS wire packet sinkhole + allowlist override
        pkt_ad = _build_dns_query("ads.doubleclick.net", qtype=1)
        qname, qtype, _ = _parse_dns_qname(pkt_ad)  # type: ignore[misc]
        assert qname == "ads.doubleclick.net" and qtype == 1

        ok_srv, _ = shield.start_dns_sinkhole(host="127.0.0.1", port=0)
        assert ok_srv and shield.dns_running
        reply = shield.handle_dns_packet(pkt_ad, allow_upstream=False)
        assert reply is not None
        assert reply[-4:] == b"\x00\x00\x00\x00"  # Sinkholed to 0.0.0.0

        # Allowlist override
        ok_al, _ = shield.add_custom_domain("ads.doubleclick.net", allow=True)
        assert ok_al
        blk, reason = shield.is_domain_blocked("ads.doubleclick.net")
        assert not blk and "allowlist" in reason

        st = shield.status()
        assert st.total_queries >= 1 and st.blocked_queries >= 1
        shield.stop_dns_sinkhole()
        assert not shield.dns_running


def test_snapshot_diff_startup_delay_network_ram_battery_and_safe_fix() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "state"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        ex = GuardedExecutor(cfg_mgr, rb, j)
        adapter = get_os_adapter("auto")

        # 1) Snapshot Diff
        diff_eng = BaselineDiffEngine(cfg_mgr, j)
        s1 = Path(td) / "s1.json"
        s2 = Path(td) / "s2.json"
        s1.write_text(json.dumps({"label": "Fast PC", "installed_apps": ["firefox"], "startup_items": ["user:a"], "disk_used_gb": 50.0, "ram_percent": 30.0, "active_profile": "Balanced Daily"}))
        s2.write_text(json.dumps({"label": "Today", "installed_apps": ["firefox", "bloatware"], "startup_items": ["user:a", "user:updater"], "disk_used_gb": 54.5, "ram_percent": 55.0, "active_profile": "Gaming / Low-Latency"}))
        res = diff_eng.diff_snapshots(s1, s2)
        assert res.added_apps == ["bloatware"]
        assert res.added_startup == ["user:updater"]
        assert res.disk_delta_gb == 4.5

        # 2) Startup Delay 45s
        desk = Path(td) / "helper.desktop"
        desk.write_text("[Desktop Entry]\nName=Helper\nExec=/usr/bin/helper\n", encoding="utf-8")
        ok_d, msg_d = StartupDelayManager(cfg_mgr, rb, j).delay_startup_entry(desk, delay_seconds=45)
        assert ok_d and "45s" in msg_d

        # 3) Network hogs + Fast-DNS + Standby RAM trim + Battery
        net = NetworkAndDnsInspector(j)
        _ = net.scan_network_hogs(limit=5)
        assert "1.1.1.1" in net.apply_dns_preset_command("Cloudflare (1.1.1.1)")

        ok_ram, ram_msg, _ = MemoryStandbyManager(j).trim_standby_ram(dry_run=True)
        assert ok_ram and "Dry-run" in ram_msg

        bat = BatteryAndPowerInspector().inspect()
        assert isinstance(bat.summary, str) and len(bat.summary) > 5

        # 4) 1-Click Fix Everything Safe + Weekly Digest
        fixer = SafeBeginnerFixer(cfg_mgr, ex, rb, j)
        ok_f, f_sum, f_notes = fixer.run_fix_everything_safe(adapter, cfg, dry_run=True)
        assert ok_f and len(f_notes) == 4 and "Fix Everything Safe" in f_sum
        digest = fixer.build_weekly_digest(adblock_blocked_count=17)
        assert "17 tracker/ad queries blocked" in digest


def test_login_autostart_tray_menu_and_workflows(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / ".github" / "workflows" / "ci.yml").exists()
    assert (repo_root / ".github" / "workflows" / "release.yml").exists()

    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td) / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))

        cfg_mgr = ConfigManager(base_dir=Path(td) / "state")
        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        lm = LifecycleManager(cfg_mgr, rb, j)

        ok_on, _ = lm.set_launch_at_login(True, repo_root, start_minimized=True, os_override="Linux")
        assert ok_on
        autostart = fake_home / ".config" / "autostart" / "astracradle.desktop"
        assert autostart.exists() and "--minimized" in autostart.read_text(encoding="utf-8")

        ok_off, _ = lm.set_launch_at_login(False, repo_root, os_override="Linux")
        assert ok_off and not autostart.exists()

    # Tray rich callbacks construct cleanly
    t = TrayController(
        repo_root / "assets" / "astracradle_emblem_64.png",
        on_show=lambda: None,
        on_scan=lambda: None,
        on_quit=lambda: None,
        on_fix_safe=lambda: None,
        on_profile=lambda _p: None,
        on_toggle_probalance=lambda: None,
        on_toggle_astrashield=lambda: None,
    )
    assert callable(t.on_fix_safe) and callable(t.on_toggle_astrashield)


def test_cli_help_parses_cleanly() -> None:
    import pytest
    from astracradle.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_gui_renders_astrashield_and_new_settings() -> None:
    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=Path(td), show_startup_popups=False)
        try:
            app._on_module_tab("Privacy & Shield")
            app.switch_nav("modules")
            app.update()
            app._on_module_tab("Glitch & OS Tools")
            app.update()
            app.switch_nav("settings")
            app.set_start_min_var.set(True)
            app.set_ram_guard_var.set(True)
            app.on_save_settings()
            assert app.cfg_mgr.load().start_minimized is True
            assert app.cfg_mgr.load().ram_guard_enabled is True
            assert hasattr(app, "elevation") and callable(app.elevation.password_prompt_cb)
        finally:
            if app.winfo_exists():
                app.destroy()


def test_elevation_runner_sudo_retry_pkexec_and_file_write(monkeypatch) -> None:
    from types import SimpleNamespace
    import astracradle.core.elevation as elev_mod
    from astracradle.core.elevation import ElevationRunner

    monkeypatch.setattr(elev_mod, "is_elevated", lambda: False)
    monkeypatch.setattr(elev_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(elev_mod.shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("sudo", "pkexec", "install") else None)

    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "input": kwargs.get("input")})
        if argv[0] == "sudo":
            if kwargs.get("input") == "wrongpw\n":
                return SimpleNamespace(returncode=1, stdout="", stderr="Sorry, try again.")
            if kwargs.get("input") == "goodpw\n":
                return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        if argv[0] == "pkexec":
            return SimpleNamespace(returncode=0, stdout="pkexec_ok", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="err")

    monkeypatch.setattr(elev_mod.subprocess, "run", fake_run)

    prompts: list[str] = []
    answers = iter(["wrongpw", "goodpw"])
    runner = ElevationRunner(password_prompt_cb=lambda title, detail: (prompts.append(detail), next(answers))[1])
    res = runner.run_privileged(["echo", "hi"], reason="Test sudo prompt")
    assert res.ok and res.method == "sudo" and len(prompts) == 2 and "Incorrect" in prompts[1]

    # __PKEXEC__ sentinel triggers pkexec
    pk_runner = ElevationRunner(password_prompt_cb=lambda _t, _d: "__PKEXEC__")
    pk_res = pk_runner.run_privileged(["echo", "hi"], reason="Use pkexec")
    assert pk_res.ok and pk_res.method == "pkexec"

    # Cancelled prompt returns clean error without running command
    cancel_runner = ElevationRunner(password_prompt_cb=lambda _t, _d: None)
    c_res = cancel_runner.run_privileged(["echo", "hi"], reason="Cancelled")
    assert not c_res.ok and "cancelled" in c_res.message.lower()

    # Privileged file write via install -m
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "hosts"
        w_runner = ElevationRunner(password_prompt_cb=lambda _t, _d: "goodpw")
        w_res = w_runner.write_privileged_file(target, "127.0.0.1 localhost\n", mode="644", reason="Update hosts")
        assert w_res.ok and calls[-1]["argv"][:3] == ["sudo", "-S", "-k"]


def test_privileged_toggles_use_elevation_on_permission_error(monkeypatch) -> None:
    from astracradle.core.adblock_shield import AstraShieldManager
    from astracradle.core.autoruns_audit import AutorunEntry, AutorunsAndGhostAuditor
    from astracradle.core.config import ConfigManager
    from astracradle.core.elevation import ElevationResult
    from astracradle.core.executor import GuardedExecutor
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.os_adapters import get_os_adapter
    from astracradle.core.pc_extras import MemoryStandbyManager, NetworkAndDnsInspector
    from astracradle.core.rollback import RollbackManager
    from astracradle.core.scripts_engine import ScriptsEngine

    class FakeElevation:
        def __init__(self) -> None:
            self.cmds: list[list[str]] = []
            self.writes: list[tuple[str, str]] = []

        def run_privileged(self, argv, reason="", timeout=30):
            self.cmds.append(list(argv))
            return ElevationResult(True, 0, "ok", "", message=f"Executed via sudo ({reason})", method="sudo")

        def write_privileged_file(self, target_path, content, mode="644", reason="", timeout=30):
            self.writes.append((str(target_path), content))
            return ElevationResult(True, 0, "", "", message=f"Written via sudo ({reason})", method="sudo")

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        ex = GuardedExecutor(cfg_mgr, rb, j)
        fe = FakeElevation()

        # 1. AstraShield hosts write falls back to ElevationRunner on PermissionError
        hosts_file = Path(td) / "etc_hosts"
        hosts_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        shield = AstraShieldManager(cfg_mgr, rb, j, elevation=fe)
        shield._hosts_path_override = hosts_file

        orig_write_text = Path.write_text

        def deny_hosts(self, *args, **kwargs):
            if self == hosts_file:
                raise PermissionError("read-only /etc/hosts")
            return orig_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", deny_hosts)
        ok_en, msg_en = shield.enable_hosts_sinkhole(["ads"], dry_run=False)
        assert ok_en and fe.writes, msg_en

        # 2. ScriptsEngine executes sudo custom script via ElevationRunner
        se = ScriptsEngine(cfg_mgr, ex, j, elevation=fe)
        sc = se.add_custom_script("Flush ARP", "Network", "Flush arp", "sudo ip -s -s neigh flush all", "Balanced Daily")
        ok_sc, msg_sc = se.toggle_script(sc.script_id, True, get_os_adapter(), cfg_mgr.load(), [], dry_run=False)
        assert ok_sc and any(c[0] == "ip" for c in fe.cmds), msg_sc

        # 3. NetworkAndDnsInspector.apply_dns_preset & MemoryStandbyManager.trim_standby_ram
        dns = NetworkAndDnsInspector(j)
        import astracradle.core.pc_extras as pex
        from types import SimpleNamespace
        monkeypatch.setattr(pex.platform, "system", lambda: "Linux")
        monkeypatch.setattr(pex.shutil, "which", lambda n: f"/usr/bin/{n}" if n in ("nmcli", "sync") else None)
        monkeypatch.setattr(
            pex.subprocess,
            "run",
            lambda argv, **kw: SimpleNamespace(
                returncode=(0 if argv[:2] == ["nmcli", "-t"] else 1),
                stdout=("Wired connection 1\n" if argv[:2] == ["nmcli", "-t"] else ""),
                stderr="not authorized",
            ),
        )
        ok_dns, _ = dns.apply_dns_preset("Cloudflare (1.1.1.1)", dry_run=False, elevation=fe)
        assert ok_dns and any("nmcli" in c[0] for c in fe.cmds)

        drop_path = Path("/proc/sys/vm/drop_caches")
        orig_exists = Path.exists

        def fake_exists(self):
            if self == drop_path:
                return True
            return orig_exists(self)

        def deny_drop(self, *args, **kwargs):
            if self in (hosts_file, drop_path):
                raise PermissionError("permission denied")
            return orig_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", fake_exists)
        monkeypatch.setattr(Path, "write_text", deny_drop)
        ram = MemoryStandbyManager(j)
        ok_ram, _, _ = ram.trim_standby_ram(dry_run=False, elevation=fe)
        assert ok_ram and any("/proc/sys/vm/drop_caches" in w[0] for w in fe.writes)

        # 4. AutorunsAndGhostAuditor systemd service toggle
        aa = AutorunsAndGhostAuditor(cfg_mgr, rb, j, elevation=fe)
        import astracradle.core.autoruns_audit as aum
        monkeypatch.setattr(aum.platform, "system", lambda: "Linux")
        monkeypatch.setattr(aum.shutil, "which", lambda n: "/usr/bin/systemctl" if n == "systemctl" else None)
        svc = AutorunEntry("ar_svc_test", "service", "cups.service", "systemd", "cupsd", "Printer", "low", True, True, "/etc/systemd/system/cups.service")
        ok_svc, _ = aa.toggle_autorun_entry(svc, False, get_os_adapter(), dry_run=False)
        assert ok_svc and any(c[:2] == ["systemctl", "disable"] for c in fe.cmds)


