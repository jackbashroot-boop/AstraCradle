"""Tests for YouTube Safe Ad-Prune, Pi-hole Sync, Conflict Guard, BadUSB Gate, Icons/Tray, Sensors & AI Bundle."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import zipfile

from astracradle.core.adblock_shield import AstraShieldManager
from astracradle.core.config import AppConfig, ConfigManager
from astracradle.core.elevation import ElevationResult
from astracradle.core.full_discovery import FullSystemDiscoveryEngine
from astracradle.core.journal import ForensicJournal
from astracradle.core.lifecycle import LifecycleManager
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.pc_extras import ConflictProgramDetector, ConflictProgramInfo
from astracradle.core.rollback import RollbackManager
from astracradle.core.safeguard_plus import AIDiagnosticArchiveExporter
from astracradle.core.telemetry import TelemetryCollector
from astracradle.core.usb_guard import PeripheralDevice, UsbAndPeripheralGuard
from astracradle.ui.app import AstraCradleApp


def test_youtube_safe_prune_and_pihole_sync(monkeypatch) -> None:
    import astracradle.core.adblock_shield as ab_mod

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        shield = AstraShieldManager(cfg_mgr, rb, j, hosts_path=Path(td) / "hosts")

        # 1. YouTube player JSON payload pruner keeps streamingData & videoDetails intact
        sample_payload = json.dumps(
            {
                "videoDetails": {"videoId": "dQw4w9WgXcQ", "title": "Test Video"},
                "streamingData": {"formats": [{"itag": 18, "url": "https://rr1---sn.googlevideo.com/videoplayback"}]},
                "adPlacements": [{"adPlacementRenderer": {"kind": "pre-roll"}}],
                "playerAds": [{"playerLegacyDesktopWatchAdsRenderer": {}}],
                "adSlots": [{"adSlotRenderer": {}}],
            }
        )
        pruned_txt, pruned_cnt = shield.prune_youtube_player_payload(sample_payload)
        pruned_obj = json.loads(pruned_txt)
        assert pruned_cnt == 3
        assert pruned_obj["adPlacements"] == [] and pruned_obj["playerAds"] == [] and pruned_obj["adSlots"] == []
        assert pruned_obj["streamingData"]["formats"][0]["itag"] == 18
        assert pruned_obj["videoDetails"]["videoId"] == "dQw4w9WgXcQ"

        # 2. YouTube playback domains are protected while ad domains are blocked
        assert shield.is_domain_blocked("googleads.g.doubleclick.net")[0] is True
        assert shield.is_domain_blocked("ads.youtube.com")[0] is True
        assert shield.is_domain_blocked("www.youtube.com")[0] is False
        assert shield.is_domain_blocked("s.youtube.com")[0] is False
        assert shield.is_domain_blocked("manifest.googlevideo.com")[0] is False
        assert shield.is_domain_blocked("i.ytimg.com")[0] is False

        # 3. Pi-hole feed download + parse + allowlist protection
        fake_feeds = {
            "stevenblack_unified": "0.0.0.0 doubleclick.net\n0.0.0.0 ad.example.org\n0.0.0.0 www.youtube.com\n",
            "hagezi_pro": "tracker.example.net\n||telemetry.example.com^\n",
            "youtube_ignore_pihole": "safe.example.org\n",
        }

        class FakeResp:
            def __init__(self, body: bytes) -> None:
                self._b = body

            def read(self, _n: int = -1) -> bytes:
                return self._b

            def __enter__(self):
                return self

            def __exit__(self, *_a) -> None:
                return None

        def fake_urlopen(req, timeout=12.0):
            url = req.full_url
            if "StevenBlack" in url:
                return FakeResp(fake_feeds["stevenblack_unified"].encode())
            if "hagezi" in url:
                return FakeResp(fake_feeds["hagezi_pro"].encode())
            if "ignore.list" in url:
                return FakeResp(fake_feeds["youtube_ignore_pihole"].encode())
            return FakeResp(b"0.0.0.0 safe.example.org\n")

        monkeypatch.setattr(ab_mod.urllib.request, "urlopen", fake_urlopen)
        ok, msg, cnt = shield.download_pihole_blocklists(
            source_keys=["stevenblack_unified", "hagezi_pro", "youtube_ignore_pihole"],
            timeout=5.0,
        )
        assert ok and cnt >= 4, msg
        assert shield.is_domain_blocked("ad.example.org")[0] is True
        assert shield.is_domain_blocked("telemetry.example.com")[0] is True
        assert shield.is_domain_blocked("safe.example.org")[0] is False
        assert shield.is_domain_blocked("www.youtube.com")[0] is False

        # 4. Browser userscript & uBlock filter export
        js_p, flt_p = shield.export_youtube_ad_prune_assets()
        assert js_p.exists() and "adPlacements" in js_p.read_text(encoding="utf-8")
        assert flt_p.exists() and "json-prune" in flt_p.read_text(encoding="utf-8")


def test_badusb_guard_default_on_peripheral_ping_and_block(monkeypatch) -> None:
    cfg = AppConfig()
    assert cfg.badusb_guard_enabled is True

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg_mgr = ConfigManager(base_dir=root / "cfg")
        j = ForensicJournal(cfg_mgr)
        sys_usb = root / "sys_usb"
        sys_usb.mkdir(parents=True)

        def make_usb_node(name: str, vid: str, pid: str, vendor: str, product: str, ifaces: list[tuple[str, str]], auth: str = "1") -> Path:
            d = sys_usb / name
            d.mkdir(parents=True)
            (d / "idVendor").write_text(vid, encoding="utf-8")
            (d / "idProduct").write_text(pid, encoding="utf-8")
            (d / "manufacturer").write_text(vendor, encoding="utf-8")
            (d / "product").write_text(product, encoding="utf-8")
            (d / "serial").write_text(f"SER-{name}", encoding="utf-8")
            (d / "authorized").write_text(auth, encoding="utf-8")
            for idx, (ic, ip) in enumerate(ifaces):
                sub = d / f"{name}:1.{idx}"
                sub.mkdir()
                (sub / "bInterfaceClass").write_text(ic, encoding="utf-8")
                (sub / "bInterfaceProtocol").write_text(ip, encoding="utf-8")
                (sub / "bInterfaceSubClass").write_text("01" if ip == "01" else "00", encoding="utf-8")
            return d

        # Initial baseline device: standard Logitech mouse (HID mouse only -> safe)
        make_usb_node("1-1", "046d", "c077", "Logitech", "USB Optical Mouse", [("03", "02")], auth="1")

        import astracradle.core.usb_guard as ug_mod
        monkeypatch.setattr(ug_mod.platform, "system", lambda: "Linux")

        guard = UsbAndPeripheralGuard(cfg_mgr, j, usb_sysfs_root=sys_usb)
        initial_evs = guard.poll_and_guard(cfg, dry_run=False)
        assert initial_evs == []  # baseline seeded quietly

        # Plug in a safe USB flash drive -> pinged, cleared & authorized
        make_usb_node("1-2", "0781", "5583", "SanDisk", "Ultra Fit", [("08", "50")], auth="0")
        evs1 = guard.poll_and_guard(cfg, dry_run=False)
        assert any(e.event_type == "new_device_cleared" and "SanDisk" in e.message for e in evs1)
        assert (sys_usb / "1-2" / "authorized").read_text(encoding="utf-8").strip() == "1"

        # Plug in a BadUSB composite HID + Storage Teensy device -> pinged & BLOCKED (authorized stays 0!)
        make_usb_node("1-3", "16c0", "0486", "Teensyduino", "USB Keyboard + Disk", [("03", "01"), ("08", "50")], auth="1")
        evs2 = guard.poll_and_guard(cfg, dry_run=False)
        assert any(e.event_type == "new_device_blocked" and "Teensyduino" in e.message for e in evs2)
        assert (sys_usb / "1-3" / "authorized").read_text(encoding="utf-8").strip() == "0"

        # Explicit trust override allows the board
        bad_dev = [d for d in guard.scan_usb_devices() if d.vid == "16c0"][0]
        ok_t, _ = guard.trust_device(bad_dev, cfg, cfg_mgr)
        assert ok_t and (sys_usb / "1-3" / "authorized").read_text(encoding="utf-8").strip() == "1"


def test_conflict_detector_and_ai_diagnostic_bundle(monkeypatch) -> None:
    class FakeElev:
        def __init__(self) -> None:
            self.cmds: list[list[str]] = []

        def run_privileged(self, argv, reason="", timeout=30):
            self.cmds.append(list(argv))
            return ElevationResult(True, 0, "stopped", "", message="Stopped via sudo", method="sudo")

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        cfg_mgr.ensure_directories()
        j = ForensicJournal(cfg_mgr)
        fe = FakeElev()

        cd = ConflictProgramDetector(j, elevation=fe)
        import astracradle.core.pc_extras as pex
        monkeypatch.setattr(pex.platform, "system", lambda: "Linux")
        monkeypatch.setattr(pex.shutil, "which", lambda n: "/usr/bin/systemctl" if n == "systemctl" else None)
        cf = ConflictProgramInfo("pihole-ftl", "Pi-hole FTL DNS", "dns_adblock", None, "pihole-FTL.service", "Competes for :53", "high")
        ok_s, _ = cd.stop_conflict(cf, dry_run=False)
        assert ok_s and any(c[:2] == ["systemctl", "stop"] for c in fe.cmds)

        # Seed 14 snapshots so consolidation archives 4 older ones
        for i in range(14):
            p = cfg_mgr.snapshots_dir / f"snap_{i:02d}.json"
            p.write_text(json.dumps({"i": i}), encoding="utf-8")

        j.log_event("TestComp", "sample_err", "Simulated warning for AI log", "Test anomaly", "warning")
        exporter = AIDiagnosticArchiveExporter(cfg_mgr, j)
        res = exporter.export_ai_handoff_bundle(
            snapshot_data={"mem_percent": 91.5, "swap_percent": 20.0, "partitions": [{"mountpoint": "/", "percent": 55.0}]},
            sensors_data={"temperatures": [{"chip": "k10temp", "label": "Tctl", "current_c": 88.0, "status": "warn"}], "fans": []},
            gpu_data={"name": "Test GPU", "temp_c": "64", "power_draw_w": "120", "fan_percent": "45"},
            conflicts_data=[{"name": "Pi-hole FTL DNS", "severity": "high", "reason": "Port 53 conflict"}],
        )
        assert res["archived_count"] == 4
        assert Path(res["markdown"]).exists() and "AstraCradle AI Diagnostic & Forensic Handoff Report" in Path(res["markdown"]).read_text(encoding="utf-8")
        assert Path(res["json"]).exists() and Path(res["zip"]).exists()
        with zipfile.ZipFile(res["zip"]) as zf:
            assert "AI_HANDOFF_REPORT.md" in zf.namelist() and "ai_diagnostic_bundle.json" in zf.namelist()


def test_desktop_icon_sensors_full_discovery_and_command_deck_deploy(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        lm = LifecycleManager(cfg_mgr, rb, j)
        created = lm.install_shortcuts(repo_root, os_override="Linux")
        assert created
        desktop_txt = created[0].read_text(encoding="utf-8")
        assert "StartupWMClass=AstraCradle" in desktop_txt
        assert (fake_home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "astracradle.png").exists()
        assert (fake_home / ".local" / "share" / "pixmaps" / "astracradle.png").exists()

        # Detailed sensors + FullSystemDiscovery peripheral count
        sensors = TelemetryCollector.collect_detailed_sensors()
        assert "temperatures" in sensors and "fans" in sensors and "cpu_freq" in sensors

        out = FullSystemDiscoveryEngine(cfg_mgr, rb, j).run_full_discovery(get_os_adapter(), cfg_mgr.load())
        assert "USB & All Peripherals + BadUSB Audit" in out.baseline.subsystems_checked

        # GUI Command Deck Deploy Hub & Privacy/BadUSB Cards render cleanly
        app = AstraCradleApp(base_dir=fake_home / ".astracradle_gui", show_startup_popups=False)
        try:
            assert hasattr(app, "_tk_icons") and len(app._tk_icons) >= 1
            assert hasattr(app, "dash_diag_panel") and hasattr(app, "dash_deploy_frame")
            app.dry_run_var.set(True)
            ok_dep, msg_dep = app._run_single_setup_deploy("badusb_guard", quiet=True)
            assert ok_dep and "BadUSB" in msg_dep
            app._on_module_tab("Privacy & Shield")
            app.switch_nav("modules")
            app.update()
        finally:
            if app.winfo_exists():
                app.destroy()


def test_github_commit_version_tracking_simple_mode_and_command_deck_temps(monkeypatch) -> None:
    from astracradle.core.updater import ModuleAndUpdateManager

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        cfg = cfg_mgr.load()
        cfg.update_repo_url = "https://github.com/ExampleOwner/AstraCradle"
        cfg_mgr.save(cfg)
        j = ForensicJournal(cfg_mgr)
        upd = ModuleAndUpdateManager(cfg_mgr, j)

        info = upd.get_local_build_info()
        assert info["version"] == "2.5.1"
        assert "display_version" in info

        # Commit-matching GitHub push -> reports fully updated
        monkeypatch.setattr(upd, "_query_github_latest", lambda owner, repo, timeout=3.5: (None, None, "GitHub ok"))
        monkeypatch.setattr(
            upd,
            "_query_github_head_commit",
            lambda owner, repo, timeout=3.5: {
                "sha": info["commit_full"] or "abc1234567890",
                "short_sha": info["short_sha"] or "abc1234",
                "date": "2026-11-16",
                "message": "latest push",
                "zip_url": "https://github.com/ExampleOwner/AstraCradle/archive/abc1234567890.zip",
            },
        )
        res_same = upd.check_for_updates(cfg, check_remote=True)
        assert res_same.update_available is False
        assert "fully updated" in res_same.status_message.lower()

        # Newer commit pushed on GitHub -> offers update even on same 1.0.0 tag
        monkeypatch.setattr(
            upd,
            "_query_github_head_commit",
            lambda owner, repo, timeout=3.5: {
                "sha": "deadbeef1234567",
                "short_sha": "deadbee",
                "date": "2026-11-16",
                "message": "new feature push",
                "zip_url": "https://github.com/ExampleOwner/AstraCradle/archive/deadbeef1234567.zip",
            },
        )
        if info["short_sha"]:
            res_newer = upd.check_for_updates(cfg, check_remote=True)
            assert res_newer.update_available is True
            assert "deadbee" in res_newer.latest_version

        # Simple Mode hides advanced nav tabs & toggles cleanly to Advanced Mode; Settings shows live version badge
        app = AstraCradleApp(base_dir=Path(td) / "gui", show_startup_popups=False)
        try:
            app.update()
            assert app.config_obj.simple_mode is True
            assert not app.nav_buttons["hardware"].winfo_ismapped()
            assert app.nav_buttons["dashboard"].winfo_ismapped()
            assert app.nav_buttons["settings"].winfo_ismapped()

            app.simple_mode_var.set(False)
            app._on_toggle_simple_mode()
            app.update()
            assert app.config_obj.simple_mode is False
            assert app.nav_buttons["hardware"].winfo_ismapped()

            app.switch_nav("settings")
            app.update()
            assert hasattr(app, "settings_ver_lbl") and "v2.5.1" in app.settings_ver_lbl.cget("text")
            assert hasattr(app, "settings_gh_ver_lbl")

            # Applying settings must preserve custom geometry, maximized & fullscreen state
            app.geometry("1520x900+10+10")
            app.update()
            pre_w, pre_h = app.winfo_width(), app.winfo_height()
            app.set_theme_var.set("Atelier Alabaster")
            app.set_scale_var.set("125%")
            app.on_save_settings()
            app.update()
            assert abs(app.winfo_width() - pre_w) <= 8
            assert abs(app.winfo_height() - pre_h) <= 8
            assert app.config_obj.simple_mode is False

            # Also verify _restore_window_state re-applies fullscreen flag when set
            fs_calls: list[tuple[str, bool]] = []
            orig_attrs = app.attributes
            def _fake_attrs(opt=None, val=None):
                if opt == "-fullscreen" and val is None:
                    return 1
                if opt == "-fullscreen" and val is not None:
                    fs_calls.append((opt, bool(val)))
                    return None
                return orig_attrs(opt) if val is None else orig_attrs(opt, val)
            monkeypatch.setattr(app, "attributes", _fake_attrs)
            app.set_theme_var.set("Obsidian Horology")
            app.on_save_settings()
            app.update()
            assert ("-fullscreen", True) in fs_calls
        finally:
            if app.winfo_exists():
                app.destroy()


def test_lan_tv_discovery_smart_tv_youtube_ad_shield_and_stream_safety(monkeypatch) -> None:
    import struct
    from astracradle.core.adblock_shield import AstraShieldManager
    from astracradle.core.lan_tv_shield import LanTvAdShield

    def _make_dns_query(domain: str) -> bytes:
        q = b"".join(bytes([len(p)]) + p.encode("ascii") for p in domain.split(".")) + b"\x00"
        return struct.pack("!HHHHHH", 0x4321, 0x0100, 1, 0, 0, 0) + q + struct.pack("!HH", 1, 1)

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        sh = AstraShieldManager(cfg_mgr, rb, j, hosts_path=Path(td) / "hosts")
        tv_shield = LanTvAdShield(j)

        # Simulate Smart TV sending DNS queries through LAN AstraShield DNS
        # 1) TV YouTube & Smart TV ad domains MUST be blocked
        for ad_dom in ("ads.youtube.com", "googleads.g.doubleclick.net", "ad.lgappstv.com", "samsungads.com", "ads.roku.com"):
            resp = sh.handle_dns_packet(_make_dns_query(ad_dom), allow_upstream=False, client_ip="192.168.1.85")
            assert resp is not None, f"Expected sinkhole reply for {ad_dom}"

        # 2) Video streaming & Smart TV YouTube playback endpoints MUST NEVER be blocked
        for stream_dom in (
            "tv.youtube.com",
            "www.youtube.com",
            "youtubei.googleapis.com",
            "rr3---sn-a5mlrn7z.googlevideo.com",
            "manifest.googlevideo.com",
            "i.ytimg.com",
        ):
            blocked, reason = sh.is_domain_blocked(stream_dom)
            assert blocked is False, f"{stream_dom} must stay unblocked ({reason})"
            sh.handle_dns_packet(_make_dns_query(stream_dom), allow_upstream=False, client_ip="192.168.1.85")

        stats = sh.get_client_stats()
        assert stats["192.168.1.85"]["queries"] == 11
        assert stats["192.168.1.85"]["blocked"] == 5

        # 3) LAN Smart TV discovery merges ARP + SSDP + live DNS ad-block counts
        monkeypatch.setattr(
            LanTvAdShield,
            "_read_arp_table",
            staticmethod(lambda: {"192.168.1.85": "00:e0:91:11:22:33", "192.168.1.92": "54:60:09:aa:bb:cc"}),
        )
        monkeypatch.setattr(
            LanTvAdShield,
            "_probe_ssdp",
            staticmethod(lambda timeout=0.6: {"192.168.1.92": "Linux/Android Chromecast DIAL"}),
        )
        devs = tv_shield.scan_lan_devices(sh, ssdp_timeout=0.1)
        assert len(devs) == 2
        lg_tv = next(d for d in devs if d.ip == "192.168.1.85")
        assert "LG" in lg_tv.brand and lg_tv.ads_blocked == 5
        cc_tv = next(d for d in devs if d.ip == "192.168.1.92")
        assert "Chromecast" in cc_tv.brand or "Google" in cc_tv.brand

        plan = tv_shield.generate_tv_setup_plan(cc_tv, lan_ip="192.168.1.10", dns_port=5353)
        assert "192.168.1.10" in plan["dns_instruction"]
        assert "SmartTube" in plan["companion_mode"]

        # 4) Reject invalid IPv4 in ADB helper & bind LAN ephemeral port cleanly
        ok_bad, _ = tv_shield.adb_connect_tv("not-an-ip;rm -rf /")
        assert ok_bad is False
        ok_lan, msg_lan = sh.start_dns_sinkhole(host="127.0.0.1", port=0)
        assert ok_lan and "listening" in msg_lan.lower()
        sh.stop_dns_sinkhole()


def test_v1_1_autostart_failsafe_tv_bluetooth_and_restart(monkeypatch) -> None:
    from astracradle import __version__
    from astracradle.core.adblock_shield import AstraShieldManager
    from astracradle.core.lan_tv_shield import BluetoothDeviceInfo, LanDeviceInfo, LanTvAdShield
    from astracradle.ui.app import AstraCradleApp

    assert __version__ == "2.5.1"

    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "ac"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        assert cfg.astrashield_auto_start is True
        assert cfg.astrashield_tv_auto_failsafe is True

        rb = RollbackManager(cfg_mgr)
        j = ForensicJournal(cfg_mgr)
        sh = AstraShieldManager(cfg_mgr, rb, j, hosts_path=Path(td) / "hosts")
        tv_shield = LanTvAdShield(j)

        # 1) Connect & protect TV arms failsafe rollback state with router Secondary DNS
        monkeypatch.setattr(LanTvAdShield, "get_default_gateway_ip", staticmethod(lambda: "192.168.1.1"))
        dev = LanDeviceInfo(
            ip="192.168.1.88",
            mac="00:e0:91:44:55:66",
            hostname="lgtv",
            brand="LG webOS TV",
            device_type="smart_tv",
        )
        ok_tv, msg_tv, plan = tv_shield.connect_and_protect_tv(dev, lan_ip="192.168.1.10", dns_port=53)
        assert ok_tv is True
        assert "192.168.1.1" in msg_tv
        assert "192.168.1.1" in plan["router_failsafe"]
        st = tv_shield.load_failsafe_state()
        assert st is not None and st["active"] is True and st["tv_ip"] == "192.168.1.88"

        # 2) Startup failsafe detects unclosed TV session when sinkhole isn't running & resets it cleanly
        recovery_msg = tv_shield.check_and_run_startup_failsafe(sh)
        assert recovery_msg is not None and "failsafe" in recovery_msg.lower()
        st_after = tv_shield.load_failsafe_state()
        assert st_after is not None and st_after["active"] is False

        # 3) Bluetooth discovery + quick-connect validation rejects invalid MACs & parses bluetoothctl
        ok_bad_bt, _ = tv_shield.connect_bluetooth_device("not-a-mac")
        assert ok_bad_bt is False

        class _FakeProc:
            def __init__(self, stdout: str, rc: int = 0) -> None:
                self.stdout = stdout
                self.stderr = ""
                self.returncode = rc

        def _fake_bt_run(cmd, **kwargs):
            if cmd[:2] == ["bluetoothctl", "devices"]:
                return _FakeProc("Device AA:BB:CC:11:22:33 LG OLED TV Speaker\nDevice 11:22:33:44:55:66 Sony WH-1000XM5\n")
            if cmd[:2] == ["bluetoothctl", "info"]:
                return _FakeProc("Paired: yes\nTrusted: yes\nConnected: no\nIcon: audio-headphones\n")
            if cmd[:2] == ["bluetoothctl", "connect"]:
                return _FakeProc("Connection successful\n", rc=0)
            return _FakeProc("", rc=1)

        monkeypatch.setattr("astracradle.core.lan_tv_shield.shutil.which", lambda name: "/usr/bin/bluetoothctl" if name == "bluetoothctl" else None)
        monkeypatch.setattr("astracradle.core.lan_tv_shield.platform.system", lambda: "Linux")
        monkeypatch.setattr("astracradle.core.lan_tv_shield.subprocess.run", _fake_bt_run)
        bt_list = tv_shield.scan_bluetooth_devices()
        assert len(bt_list) == 2
        assert any(b.device_kind == "tv" for b in bt_list)
        assert any(b.device_kind == "audio" for b in bt_list)
        ok_conn, conn_msg = tv_shield.connect_bluetooth_device("11:22:33:44:55:66", connect=True)
        assert ok_conn is True and "successful" in conn_msg.lower()

        # 4) GUI wiring: setup-and-restart enables auto-start/failsafe, TV close failsafe resets active TV on on_close
        app = AstraCradleApp(base_dir=Path(td) / "gui", show_startup_popups=False)
        try:
            app.switch_nav("modules")
            app.update()
            app._latest_lan_devices = [dev]
            app._latest_bt_devices = [
                BluetoothDeviceInfo(mac="11:22:33:44:55:66", name="Sony WH-1000XM5", device_kind="audio", paired=True, connected=False)
            ]
            app._render_modules()
            app.update()

            app.on_setup_and_restart_astrashield()
            assert app.config_obj.astrashield_auto_start is True
            assert app.config_obj.astrashield_tv_auto_failsafe is True

            # Arm TV failsafe on GUI and confirm clean close automatically resets it
            app._on_connect_protect_tv(dev)
            assert app.lan_tv_shield.load_failsafe_state()["active"] is True
            app.on_close(force_quit=True)
            assert app.lan_tv_shield.load_failsafe_state()["active"] is False
        finally:
            try:
                if app.winfo_exists():
                    app.destroy()
            except Exception:
                pass


def test_v1_2_forensic_crash_log_export_comprehensive_setup_and_smooth_ui() -> None:
    from astracradle import __version__
    from astracradle.core.config import COMPREHENSIVE_SETUPS
    from astracradle.core.safeguard_plus import AIDiagnosticArchiveExporter
    from astracradle.ui.app import AstraCradleApp

    assert __version__ == "2.5.1"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "ac"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        j = ForensicJournal(cfg_mgr)
        j.log_event("TestComp", "sample_warn", "Sample warning detail", "Test forensic export", "warn")

        # 1) All comprehensive setups apply cleanly
        for pname in COMPREHENSIVE_SETUPS:
            applied = cfg.apply_comprehensive_setup(pname)
            assert len(applied) >= 5
            assert cfg.consent_prompted is True

        # 2) Paste-ready forensic & crash log export writes both timestamped & latest mirror
        exp = AIDiagnosticArchiveExporter(cfg_mgr, j)
        res = exp.export_forensic_crash_log(config_obj=cfg)
        assert Path(res["path"]).exists()
        assert Path(res["latest_path"]).exists()
        txt = str(res["text"])
        assert "Forensic & Crash Vault Log (Paste-Ready)" in txt
        assert "TestComp.sample_warn" in txt

        # 3) GUI: safeguards log export + comprehensive setup + topbar pulse animation
        app = AstraCradleApp(base_dir=Path(td) / "gui", show_startup_popups=False)
        try:
            app.switch_nav("safeguards")
            app.update()
            gui_res = app.on_export_forensic_crash_log(save_as=False)
            assert Path(gui_res["path"]).exists()
            assert "Paste-Ready" in gui_res["text"]

            applied_ui = app._apply_comprehensive_setup(
                "Maximum Privacy & Ad-Shield (PC + TV)",
                overrides={"badusb_guard_enabled": True},
                create_shortcut=False,
                sync_pihole=False,
            )
            assert len(applied_ui) >= 5
            assert app.config_obj.astrashield_lan_tv_mode is True
            assert app.config_obj.badusb_guard_enabled is True
            assert app.config_obj.simple_mode is False

            app.topbar_pulse.set_active(True)
            app.update()
            app.topbar_pulse.set_active(False)
            app.update()
        finally:
            try:
                if app.winfo_exists():
                    app.destroy()
            except Exception:
                pass


def test_v1_3_wifi_channel_optimizer_cachyos_fish_and_special_category(monkeypatch) -> None:
    from astracradle import __version__
    from astracradle.core.wifi_channel_optimizer import WifiApObservation, WifiChannelOptimizer
    from astracradle.ui.app import AstraCradleApp

    assert __version__ == "2.5.1"
    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td) / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        j = ForensicJournal(cfg_mgr)
        wopt = WifiChannelOptimizer(j, state_dir=cfg_mgr.base_dir)

        aps = [
            WifiApObservation("HomeMesh", "AA:BB:CC:00:00:01", 6, 2437, "2.4GHz", 72, 144, in_use=True),
            WifiApObservation("Neighbor1", "11:11:11:11:11:01", 6, 2437, "2.4GHz", 88),
            WifiApObservation("Neighbor2", "11:11:11:11:11:02", 7, 2442, "2.4GHz", 82),
            WifiApObservation("Neighbor3", "11:11:11:11:11:03", 11, 2462, "2.4GHz", 75),
            WifiApObservation("HomeMesh", "AA:BB:CC:00:00:02", 149, 5745, "5GHz", 78, 866, in_use=False),
            WifiApObservation("Neighbor5G", "22:22:22:22:22:01", 36, 5180, "5GHz", 85),
        ]
        scores = wopt.score_channels(aps)
        best24 = next(c for c in scores["2.4GHz"] if c.recommended)
        best5 = next(c for c in scores["5GHz"] if c.recommended)
        assert best24.channel == 1
        assert best5.channel != 36

        calls: list[list[str]] = []

        class _Proc:
            def __init__(self, stdout="", rc=0):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = rc

        def _fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[:3] == ["nmcli", "-t", "-f"]:
                return _Proc("HomeMesh:802-11-wireless:wlan0\n")
            return _Proc("", rc=0)

        monkeypatch.setattr("astracradle.core.wifi_channel_optimizer.shutil.which", lambda x: f"/usr/bin/{x}" if x in ("nmcli", "iw") else None)
        monkeypatch.setattr("astracradle.core.wifi_channel_optimizer.subprocess.run", _fake_run)
        monkeypatch.setattr(WifiChannelOptimizer, "detect_wifi_interface", staticmethod(lambda: "wlan0"))
        monkeypatch.setattr(WifiChannelOptimizer, "_is_powersave_enabled", staticmethod(lambda iface: True))

        res = wopt.optimize_connection(dry_run=False, prefer_5ghz=True, disable_powersave=True, aps_override=aps)
        assert res.switched is True
        assert res.best_bssid_for_ssid == "AA:BB:CC:00:00:02"
        assert any("connect" in c and "AA:BB:CC:00:00:02" in c for c in calls)
        assert any("power_save" in c and "off" in c for c in calls)
        assert wopt.rollback_path.exists()

        ok_r, _ = wopt.restore_wifi_settings()
        assert ok_r is True and not wopt.rollback_path.exists()

        ok_inst, items = wopt.install_cachyos_fish_integration(install_dispatcher=False, enable_timer=False, interval_min=10)
        assert ok_inst is True and len(items) >= 4
        assert (fake_home / ".config" / "fish" / "conf.d" / "astracradle_wifi.fish").exists()
        assert (fake_home / ".local" / "bin" / "astracradle-wifi-opt").exists()
        assert (fake_home / ".config" / "systemd" / "user" / "astracradle-wifi-opt.timer").exists()
        assert (cfg_mgr.base_dir / "scripts" / "90-astracradle-wifi-channel").exists()

        app = AstraCradleApp(base_dir=fake_home / ".astracradle_gui", show_startup_popups=False)
        try:
            app.update()
            assert app.nav_buttons["wifi"].winfo_ismapped()
            app._latest_wifi_res = res
            app.switch_nav("wifi")
            app.update()
            assert len(app.wifi_scroll.winfo_children()) >= 3
        finally:
            try:
                if app.winfo_exists():
                    app.destroy()
            except Exception:
                pass



