from __future__ import annotations

from pathlib import Path
import tempfile

from astracradle import __version__
from astracradle.core.astrabeam import AppleCompanionDevice, AstraBeamClient, AstraBeamServer, TailscalePeer, TailscaleStatus
from astracradle.core.command_db import CommandDatabaseManager
from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.mobile_continuity import (
    BluetoothHandshakeManager,
    DeviceRegistryManager,
    MobileCompanionEngine,
    WifiLinkQuality,
)
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.core.self_heal import SelfHealEngine
from astracradle.ui.app import AstraCradleApp


def test_v2_1_command_db_proximity_bt_handshake_custom_devices_and_self_heal() -> None:
    assert __version__ == "2.5.0"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".astracradle"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        heal = SelfHealEngine(cfg_mgr, rb, journal)
        adapter = get_os_adapter()

        # 1. SQLite Command & Install Script Database CRUD + Dropdown categories
        cdb = CommandDatabaseManager(cfg_mgr, journal)
        builtins = cdb.list_commands()
        assert len(builtins) >= 15
        assert any("tailscale" in c.script.lower() for c in builtins)
        assert any("Install" == c.category for c in builtins)

        custom_rec = cdb.add_command(
            title="Pair Vision Pro Beta Stream",
            category="Custom",
            platform="macOS / iOS",
            script="moonlight stream 100.64.0.9",
            description="Custom newly released headset command",
        )
        cdb.mark_copied(custom_rec.cmd_id)
        fetched = cdb.get_command(custom_rec.cmd_id)
        assert fetched is not None and fetched.use_count == 1
        ok_del_bi, _ = cdb.delete_command("install_tailscale_linux")
        assert ok_del_bi is False  # built-ins protected

        # 2. User-Designated New Released Devices & Programs
        reg = DeviceRegistryManager(cfg_mgr, journal)
        new_watch = reg.register_device(
            name="Apple Watch Ultra 4",
            device_type="Apple Watch",
            os_family="watchOS",
            bluetooth_mac="AA:BB:CC:DD:EE:11",
            port=47990,
            auto_connect=True,
        )
        new_handheld = reg.register_device(
            name="NextGen Handheld X",
            device_type="Handheld / VR / New Device",
            os_family="Linux",
            network_host="100.88.1.44",
            bluetooth_mac="AA:BB:CC:DD:EE:22",
            program_command="python3 -c 'print(1)'",
            auto_connect=True,
        )
        assert len(reg.load_devices()) == 2
        ok_launch, _ = reg.launch_designated_program(new_handheld.device_id)
        assert ok_launch is True

        # 3. Proximity Auto-Connect + Weak Wi-Fi Bluetooth Handshake Fallback
        comp = MobileCompanionEngine(cfg_mgr, reg, journal)
        ts_online = TailscaleStatus(
            installed=True,
            running=True,
            backend_state="Running",
            self_ip="100.88.1.5",
            self_hostname="pc-main",
            peers=[
                TailscalePeer("NextGen Handheld X", "handheld.ts.net", "100.88.1.44", "linux", True),
                TailscalePeer("My-iPhone-17", "iphone.ts.net", "100.88.1.77", "iOS", True),
            ],
        )
        apples = [
            AppleCompanionDevice("My-iPhone-17", "iphone", "tailscale", "100.88.1.77", True, "iOS peer"),
            AppleCompanionDevice("Paired Apple Watch", "watch", "bluetooth", "AA:BB:CC:DD:EE:33", False, "Bluetooth paired"),
        ]
        # Weak Wi-Fi (-84 dBm) forces Bluetooth Handshake Fallback for BT-paired devices!
        weak_wifi = WifiLinkQuality(connected=True, ssid="Home5G", rssi_dbm=-84, is_weak=True, reason="Weak Wi-Fi")
        outcomes = comp.auto_connect_proximity_devices(
            cfg, ts_status=ts_online, apple_devices=apples, adb_devices=[], wifi_quality=weak_wifi
        )
        by_name = {o.device_name: o for o in outcomes}
        assert by_name[new_watch.name].connected is True
        assert by_name[new_watch.name].wifi_weak_fallback is True
        assert by_name[new_watch.name].transport_used.startswith("bluetooth_")
        assert by_name["Paired Apple Watch"].connected is True
        assert by_name["Paired Apple Watch"].wifi_weak_fallback is True
        assert by_name["My-iPhone-17"].connected is True
        assert by_name["My-iPhone-17"].transport_used == "tailscale_tls"

        # 4. Live AstraBeam TLS Bluetooth Handshake Endpoint (/api/bt/handshake)
        hs = BluetoothHandshakeManager.perform_handshake(
            "AA:BB:CC:DD:EE:11", "Apple Watch Ultra 4", comp._handshake_secret, probe_connect=False
        )
        assert hs.ok is True and len(hs.challenge_hmac) == 64

        srv = AstraBeamServer(
            journal,
            host="127.0.0.1",
            port=0,
            pin="556677",
            tls_enabled=True,
            cert_dir=base / "certs",
            watch_summary_provider=lambda: {"hostname": "pc-main", "health_score": 99, "cpu_percent": 5.0, "mem_percent": 20.0},
        )
        ok_s, _ = srv.start()
        assert ok_s is True
        try:
            cli = AstraBeamClient("127.0.0.1", srv.port, pin="", use_tls=True)
            ok_bt, _ = cli.bluetooth_handshake("AA:BB:CC:DD:EE:11", challenge_hmac=hs.challenge_hmac, transport_mode=hs.transport_mode)
            assert ok_bt is True and cli.token
            ws = cli.get_watch_summary()
            assert ws is not None and ws["health_score"] == 99
        finally:
            srv.stop()

        # 5. Self-Heal Fault Injection across ALL new features:
        #    a) Corrupt commands.db SQLite file
        #    b) Corrupt devices_registry.json
        #    c) Delete TLS cert.pem
        #    d) Inject insecure UPnP/unencrypted Sunshine config
        cdb.db_path.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_0000")
        reg.registry_path.write_text("{BROKEN_REGISTRY_JSON", encoding="utf-8")
        (base / "certs" / "astrabeam_cert.pem").unlink()
        sun_conf = Path(td) / "sunshine.conf"
        sun_conf.write_text("upnp = enabled\nlan_encryption_mode = 0\n", encoding="utf-8")

        all_events, audits = heal.run_full_self_heal(
            adapter,
            cfg,
            command_db=cdb,
            device_registry=reg,
            companion=comp,
            sunshine_conf_path=sun_conf,
        )
        assert len(all_events) == 14
        assert audits[0].severity == "ok"
        ev_map = {e.check_name: e for e in all_events}
        assert "Network Failover State Guard" in ev_map
        assert "Companion Dependency Library Guard" in ev_map
        assert ev_map["Command Vault SQLite Guard"].repaired_automatically is True
        assert ev_map["Designated Device Registry Guard"].repaired_automatically is True
        assert ev_map["AstraBeam TLS Certificate Guard"].repaired_automatically is True
        assert ev_map["Sunshine Zero-Hole Config Guard"].repaired_automatically is True

        # Verify custom command & custom devices survived corruption via automatic self-heal!
        assert cdb.get_command(custom_rec.cmd_id) is not None
        assert len(reg.load_devices()) == 2
        assert "upnp = disabled" in sun_conf.read_text(encoding="utf-8")
        assert (base / "certs" / "astrabeam_cert.pem").exists()

        # 6. GUI Command Vault + Remote Beam & Devices page integration
        app = AstraCradleApp(base_dir=base, show_startup_popups=False)
        try:
            app.update()
            app.switch_nav("cmd_vault")
            app.update()
            app._on_add_vault_command("GUI Test Command", "Custom", "All", "echo gui-ok", "Added in test")
            app.update()
            assert any(c.title == "GUI Test Command" for c in app.command_db.list_commands())

            app.switch_nav("remote_beam")
            app.update()
            app._on_register_designated_device(
                "New iPhone / iPad (Tailscale + BT)",
                "iPhone 18 Pro Max",
                "100.88.1.99",
                "AA:BB:CC:DD:EE:99",
                "",
                "47990",
                "",
                True,
            )
            app._on_auto_connect_proximity_now()
            app.update()
            assert any(d.name == "iPhone 18 Pro Max" for d in app.device_registry.load_devices())
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
