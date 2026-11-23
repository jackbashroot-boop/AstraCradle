from __future__ import annotations

import json
from pathlib import Path
import ssl
import tempfile
import urllib.request

from astracradle import __version__
from astracradle.core.astrabeam import (
    AppleDeviceScanner,
    AstraBeamClient,
    AstraBeamServer,
    SunshineMoonlightBridge,
    TailscalePeer,
    TailscaleStatus,
)
from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.ui.app import AstraCradleApp


def test_v2_0_zero_trust_tls_mobile_pwa_and_apple_watch() -> None:
    assert __version__ == "2.5.0"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".astracradle"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg_mgr.load()
        journal = ForensicJournal(cfg_mgr)

        # 1. Hardened Sunshine Tailscale Config (mandatory encryption + UPnP disabled)
        sun_conf = Path(td) / "sunshine.conf"
        ok_sun, _ = SunshineMoonlightBridge.apply_hardened_tailscale_config("100.88.1.5", conf_path=sun_conf)
        assert ok_sun is True and sun_conf.exists()
        sun_txt = sun_conf.read_text(encoding="utf-8")
        assert "upnp = disabled" in sun_txt and "lan_encryption_mode = 2" in sun_txt and "wan_encryption_mode = 2" in sun_txt

        # 2. Apple Watch & iPhone Discovery (Tailscale iOS peer)
        ts = TailscaleStatus(
            installed=True,
            running=True,
            backend_state="Running",
            self_ip="100.88.1.5",
            self_hostname="home-pc",
            peers=[
                TailscalePeer("My-iPhone", "my-iphone.ts.net", "100.88.1.9", "iOS", True),
            ],
        )
        apple_devs = AppleDeviceScanner.scan(ts)
        assert any(d.name == "My-iPhone" and d.device_kind == "iphone" and d.transport == "tailscale" for d in apple_devs)

        # 3. Live TLS 1.2+ Server, Brute-Force Lockout, IP-Bound Token, Watch API & Clipboard Sync
        watch_actions: list[tuple[str, str]] = []
        srv = AstraBeamServer(
            journal,
            host="127.0.0.1",
            port=0,
            pin="918273",
            fps=20,
            tailscale_only=True,
            tls_enabled=True,
            cert_dir=base / "certs",
            max_failed_pins=5,
            lockout_seconds=120,
            watch_summary_provider=lambda: {"hostname": "home-pc", "health_score": 96, "cpu_percent": 11.2, "mem_percent": 34.0},
            watch_action_handler=lambda a, v: (watch_actions.append((a, v)) is None, f"Executed {a}"),
        )
        ok_s, _ = srv.start()
        assert ok_s is True and srv.tls_active is True and bool(srv.cert_fingerprint_sha256)
        try:
            # Verify manifest.webmanifest & /watch HTML over TLS
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(f"https://127.0.0.1:{srv.port}/manifest.webmanifest", timeout=3.0, context=ctx) as resp:
                man = json.loads(resp.read().decode("utf-8"))
                assert man["display"] == "standalone"
                assert resp.headers.get("X-Frame-Options") == "DENY"

            # Verify brute-force lockout after 5 wrong PINs
            bad = AstraBeamClient("127.0.0.1", srv.port, pin="000000", use_tls=True)
            for _ in range(5):
                ok_b, _ = bad.pair()
                assert ok_b is False
            assert srv.is_ip_locked_out("127.0.0.1") is True
            ok_locked, msg_locked = AstraBeamClient("127.0.0.1", srv.port, pin="918273", use_tls=True).pair()
            assert ok_locked is False and "locked" in msg_locked.lower()

            # Clear lockout for clean valid TLS pairing test
            srv._locked_until.clear()
            srv._failed_attempts.clear()

            client = AstraBeamClient("127.0.0.1", srv.port, pin="918273", use_tls=True)
            ok_p, _ = client.pair()
            assert ok_p is True and client.token

            # Token must reject spoofed/different IP
            assert srv.verify_token(client.token, "127.0.0.1") is True
            assert srv.verify_token(client.token, "100.64.99.99") is False

            # Watch summary & action + Clipboard sync over TLS
            ws = client.get_watch_summary()
            assert ws is not None and ws["health_score"] == 96
            ok_act, msg_act = client.trigger_watch_action("fix_safe")
            assert ok_act is True and "Executed fix_safe" in msg_act and watch_actions == [("fix_safe", "")]

            assert client.push_clipboard("tailscale-handoff-secret") is True
            assert client.pull_clipboard() == "tailscale-handoff-secret"

            # Fetch live encrypted JPEG frame & revoke token
            jpg = client.fetch_frame()
            assert jpg is not None and jpg[:2] == b"\xff\xd8"
            assert client.revoke() is True
            assert client.fetch_frame() is None
        finally:
            srv.stop()

        # 4. GUI Remote Beam page render
        app = AstraCradleApp(base_dir=base, show_startup_popups=False)
        try:
            app.update()
            app.switch_nav("remote_beam")
            app.update()
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
