from __future__ import annotations

from pathlib import Path
import tempfile

from astracradle import __version__
from astracradle.core.command_db import CommandDatabaseManager
from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.network_failover import NetworkFailoverEngine, PingProbeResult
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.core.self_heal import SelfHealEngine
from astracradle.ui.app import AstraCradleApp


def test_v2_3_ethernet_wifi_failover_5min_recovery_self_heal_and_fish() -> None:
    assert __version__ == "2.5.1"
    repo_root = Path(__file__).resolve().parents[1]
    fish_inst = repo_root / "install_astracradle.fish"
    assert fish_inst.exists()
    fish_txt = fish_inst.read_text(encoding="utf-8")
    assert "if command -q" in fish_txt and "--self-check" in fish_txt

    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".astracradle"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        cfg.failover_primary_interface = "eth0"
        cfg.failover_backup_interface = "wlan0"
        cfg.failover_ping_interval_sec = 5.0
        cfg.failover_failure_threshold = 3
        cfg.failover_min_hold_sec = 60.0
        cfg.failover_recovery_stable_sec = 300.0  # 5 minutes
        cfg_mgr.save(cfg)

        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        heal = SelfHealEngine(cfg_mgr, rb, journal)
        adapter = get_os_adapter()
        nfe = NetworkFailoverEngine(cfg_mgr, journal)

        fail_probe = lambda iface, tgts, to, ml: PingProbeResult(iface, "1.1.1.1", False, 0.0, "Timeout")
        ok_probe = lambda iface, tgts, to, ml: PingProbeResult(iface, "1.1.1.1", True, 12.4, "Healthy (12.4 ms)")

        # 1. Two failed pings enter degraded_probing without switching yet
        s1 = nfe.evaluate_step(cfg, now_ts=100.0, ping_fn=fail_probe, apply_routes=False)
        s2 = nfe.evaluate_step(cfg, now_ts=105.0, ping_fn=fail_probe, apply_routes=False)
        assert s1.state == "degraded_probing" and s2.state == "degraded_probing" and s2.active_iface == "eth0"

        # 2. Third failed ping triggers seamless failover -> wlan0
        s3 = nfe.evaluate_step(cfg, now_ts=110.0, ping_fn=fail_probe, apply_routes=False)
        assert s3.state == "failover_active" and s3.active_iface == "wlan0"

        # 3. Primary comes back briefly at t=180s -> primary_stabilizing (still on wlan0!)
        s4 = nfe.evaluate_step(cfg, now_ts=180.0, ping_fn=ok_probe, apply_routes=False)
        assert s4.state == "primary_stabilizing" and s4.active_iface == "wlan0"

        # 4. Primary drops again at t=250s -> resets 5-minute stability timer!
        s5 = nfe.evaluate_step(cfg, now_ts=250.0, ping_fn=fail_probe, apply_routes=False)
        assert s5.state == "failover_active" and s5.stable_elapsed_sec == 0.0

        # 5. Primary comes back at t=300s and stays stable < 300s (t=550s) -> still stabilizing
        nfe.evaluate_step(cfg, now_ts=300.0, ping_fn=ok_probe, apply_routes=False)
        s6 = nfe.evaluate_step(cfg, now_ts=550.0, ping_fn=ok_probe, apply_routes=False)
        assert s6.state == "primary_stabilizing" and s6.stable_elapsed_sec == 250.0 and s6.active_iface == "wlan0"

        # 6. At t=605s (305s continuous stability >= 300s) -> seamlessly transitions back to eth0!
        s7 = nfe.evaluate_step(cfg, now_ts=605.0, ping_fn=ok_probe, apply_routes=False)
        assert s7.state == "primary_active" and s7.active_iface == "eth0"

        # 7. Custom failover profile + Self-Heal fault injection on state & thresholds
        nfe.add_custom_profile(cfg, "Backup BT Link", "wlan0", "bnep0", interval_sec=8.0, recovery_sec=180.0)
        assert len(cfg.failover_custom_profiles) == 1

        nfe.state_path.write_text("{CORRUPT_FAILOVER_JSON", encoding="utf-8")
        cfg.failover_ping_interval_sec = -10.0
        cfg.failover_recovery_stable_sec = 0.0
        events, _ = heal.run_full_self_heal(adapter, cfg)
        ev_map = {e.check_name: e for e in events}
        assert ev_map["Network Failover State Guard"].repaired_automatically is True
        assert cfg.failover_ping_interval_sec == 5.0 and cfg.failover_recovery_stable_sec == 300.0

        # 8. Fish commands in CommandDB include installer + failover watch
        cdb = CommandDatabaseManager(cfg_mgr, journal)
        fish_ids = {c.cmd_id for c in cdb.list_commands(category="Linux Fish")}
        assert "linux_fish_install_astracradle" in fish_ids
        assert "linux_fish_failover_watch" in fish_ids

        # 9. GUI Wi-Fi & Failover tab controls
        app = AstraCradleApp(base_dir=base, show_startup_popups=False)
        try:
            app.update()
            app.switch_nav("wifi")
            app._on_save_failover_settings(
                True, True, "eth0", "wlan0", "1.1.1.1, 9.9.9.9", "7", "2", "200", "4", "90", "300"
            )
            app._on_add_custom_failover_pair("Desk Eth->WiFi", "eth0", "wlan0", "6", "300")
            app._on_force_failover_test()
            assert app.network_failover.state == "failover_active"
            app._on_force_recover_primary()
            assert app.network_failover.state == "primary_active"
            app.update()
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
