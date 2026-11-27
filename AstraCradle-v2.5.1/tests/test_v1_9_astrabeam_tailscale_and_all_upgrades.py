from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from astracradle import __version__
from astracradle.core.adblock_shield import AstraShieldManager
from astracradle.core.astrabeam import (
    AstraBeamClient,
    AstraBeamServer,
    SunshineMoonlightBridge,
    TailscaleDiscovery,
)
from astracradle.core.autopilot import AutopilotEngine
from astracradle.core.config import ConfigManager
from astracradle.core.executor import GuardedExecutor
from astracradle.core.hotkeys import GlobalHotkeyManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rf_bandwidth_scanner import BandwidthAndRfScanner
from astracradle.core.rollback import RollbackManager
from astracradle.core.self_heal import SelfHealEngine
from astracradle.core.telemetry import TelemetryCollector
from astracradle.core.telemetry_history import TelemetryHistoryDB
from astracradle.core.updater import ModuleAndUpdateManager
from astracradle.ui.app import AstraCradleApp


def _build_dns_query(domain: str) -> bytes:
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(part)]) + part.encode("ascii") for part in domain.split(".")) + b"\x00"
    return header + qname + struct.pack("!HH", 1, 1)


def test_v1_9_astrabeam_tailscale_and_all_11_upgrades(monkeypatch) -> None:
    assert __version__ == "2.5.1"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".astracradle"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        exec_eng = GuardedExecutor(cfg_mgr, rb, journal)
        sh_eng = SelfHealEngine(cfg_mgr, rb, journal)
        autopilot = AutopilotEngine(cfg_mgr, exec_eng, sh_eng, journal)

        # 1. Tailscale ACL & Peer Parsing + Sunshine/Moonlight Bridge
        assert TailscaleDiscovery.is_tailscale_or_loopback("100.88.12.4") is True
        assert TailscaleDiscovery.is_tailscale_or_loopback("127.0.0.1") is True
        assert TailscaleDiscovery.is_tailscale_or_loopback("8.8.8.8") is False

        fake_ts_json = json.dumps(
            {
                "BackendState": "Running",
                "Self": {"HostName": "gaming-rig", "DNSName": "gaming-rig.tail123.ts.net.", "TailscaleIPs": ["100.100.1.10"]},
                "Peer": {
                    "k1": {"HostName": "laptop-travel", "DNSName": "laptop-travel.tail123.ts.net.", "TailscaleIPs": ["100.100.1.22"], "OS": "linux", "Online": True}
                },
            }
        )
        with monkeypatch.context() as m:
            m.setattr(TailscaleDiscovery, "find_tailscale_bin", staticmethod(lambda: "/usr/bin/tailscale"))
            m.setattr(
                subprocess,
                "run",
                lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout=fake_ts_json, stderr=""),
            )
            ts = TailscaleDiscovery.get_status()
        assert ts.running is True and ts.self_ip == "100.100.1.10" and len(ts.peers) == 1
        assert "upnp = disabled" in SunshineMoonlightBridge.generate_tailscale_sunshine_conf(ts.self_ip)

        # 2. Live AstraBeam Server + Client End-to-End Streaming, PIN Auth & Input
        srv = AstraBeamServer(journal, host="127.0.0.1", port=0, pin="246810", fps=20, tailscale_only=True)
        ok_s, _ = srv.start()
        assert ok_s is True and srv.port > 0
        try:
            bad_client = AstraBeamClient("127.0.0.1", srv.port, pin="000000")
            ok_bad, _ = bad_client.pair()
            assert ok_bad is False

            client = AstraBeamClient("127.0.0.1", srv.port, pin="246810")
            ok_pair, _ = client.pair()
            assert ok_pair is True and client.token

            frame = client.fetch_frame()
            assert frame is not None and frame[:2] == b"\xff\xd8"  # valid JPEG SOI
            assert client.send_input({"kind": "click", "button": "left", "x": 0.5, "y": 0.5}) is True
            assert srv.inputs_handled >= 1 and srv.last_input is not None
        finally:
            srv.stop()

        # 3. Custom Rules Builder (If process_running -> switch_profile)
        rule = autopilot.add_custom_rule(
            cfg,
            name="Blender 3D Rule",
            condition_type="process_running",
            condition_value="blender",
            action_type="switch_profile",
            action_value="Gaming / Low-Latency",
        )
        snap = TelemetryCollector().collect(get_os_adapter(), include_dns=False)
        fired = autopilot.evaluate_custom_rules(snap, cfg, running_process_names={"blender", "python3"})
        assert len(fired) == 1 and cfg_mgr.load().active_profile == "Gaming / Low-Latency"
        assert autopilot.toggle_custom_rule(cfg, rule["rule_id"]) is True
        assert autopilot.delete_custom_rule(cfg, rule["rule_id"]) is True

        # 4. AstraShield Temporary 5m Pause + DoH Query Timing
        hosts_file = Path(td) / "hosts"
        hosts_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        shield = AstraShieldManager(cfg_mgr, rb, journal, hosts_path=hosts_file)
        shield.doh_enabled = True
        pkt = _build_dns_query("example.org")
        fake_resp = pkt[:2] + b"\x81\x80" + pkt[4:]
        monkeypatch.setattr(AstraShieldManager, "_query_upstream_doh", staticmethod(lambda p, provider="cloudflare", timeout=2.5: fake_resp))
        out_pkt = shield.handle_dns_packet(pkt, allow_upstream=True)
        assert out_pkt == fake_resp
        feed = shield.recent_query_feed(limit=5)
        assert feed and feed[0].action == "doh" and feed[0].rtt_ms is not None

        shield.pause_temporarily(minutes=5)
        assert shield.is_paused() is True
        shield.resume_from_pause()
        assert shield.is_paused() is False

        # 5. Per-Process I/O Metering & Weekly Benchmark Regression Detection
        socks = BandwidthAndRfScanner(journal).scan_process_sockets(limit=5)
        assert isinstance(socks, list)

        db = TelemetryHistoryDB(cfg_mgr)
        s1 = replace(snap, mem_used_bytes=4 * 1024**3)
        s2 = replace(snap, mem_used_bytes=6 * 1024**3)
        db.record_weekly_benchmark(s1, 94, bufferbloat_grade="A+", now=1000.0)
        db.record_weekly_benchmark(s2, 80, bufferbloat_grade="B", now=2000.0)
        regs = db.detect_regressions(ram_growth_mb_threshold=512.0)
        assert len(regs) == 2 and "Idle RAM grew" in regs[0]

        # 6. Updater .zip.sha256 Verification
        upd = ModuleAndUpdateManager(cfg_mgr, journal)
        assert upd._fetch_expected_sha256("https://example.invalid/not-a-zip.tar.gz") is None

        # 7. Global Hotkeys graceful init/stop
        gh = GlobalHotkeyManager()
        gh.stop()

        # 8. GUI Verification: Remote Beam tab, Modular RF Waves & Settings Custom Rules + Benchmarks
        app = AstraCradleApp(base_dir=base, show_startup_popups=False)
        try:
            app.update()
            assert "remote_beam" in app.nav_buttons
            app.switch_nav("remote_beam")
            app.switch_nav("rf_waves")
            app.switch_nav("settings")
            app.update()
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
