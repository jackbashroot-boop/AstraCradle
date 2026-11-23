from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import psutil

from astracradle import __version__
from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.rf_bandwidth_scanner import BandwidthAndRfScanner, BluetoothWaveEmitter
from astracradle.core.wifi_channel_optimizer import WifiApObservation
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.widgets import CategoryMotionEmblem, InteractiveFluidSolarCanvas, RfBandwidthWaveChart


def test_v1_5_rf_bandwidth_wave_scanner_and_category_animations(monkeypatch) -> None:
    assert __version__ == "2.5.0"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".astracradle"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        assert cfg.motion_style == "Hybrid Solar + Fluid"
        assert cfg.animations_enabled is True
        j = ForensicJournal(cfg_mgr)

        # 1. Bandwidth & RF multi-band wave scanner + delta RX/TX calculation
        scanner = BandwidthAndRfScanner(j)
        aps = [
            WifiApObservation("HomeMesh", "AA:BB:CC:00:00:01", 6, 2437, "2.4GHz", 75, 144, in_use=True),
            WifiApObservation("HomeMesh", "AA:BB:CC:00:00:05", 149, 5745, "5GHz", 88, 866, in_use=False),
            WifiApObservation("Studio6E", "AA:BB:CC:00:00:06", 37, 6135, "6GHz", 62, 2400, in_use=False),
        ]
        monkeypatch.setattr(
            scanner,
            "_scan_bluetooth_emitters",
            lambda: [BluetoothWaveEmitter("TV-Soundbar", "11:22:33:44:55:66", 2426, 60)],
        )

        scanner.sample_bandwidth_only()
        snap = scanner.scan_all(rescan_rf=False, include_ping=False, aps_override=aps)
        assert snap.rf_24_density > 0.0
        assert snap.rf_5_density > 0.0
        assert snap.rf_6_density > 0.0
        assert len(snap.rf_channels) == 3
        assert len(snap.wave_history_rx) >= 5

        # Chaos: splice psutil.net_io_counters to raise OSError -> graceful fallback
        monkeypatch.setattr(psutil, "net_io_counters", lambda pernic=True: (_ for _ in ()).throw(OSError("NIC gone")))
        rx_f, tx_f, link_f, if_f = scanner.sample_bandwidth_only()
        assert rx_f == 0.0 and tx_f == 0.0 and link_f == 0 and if_f == []
        monkeypatch.undo()

        # 2. GUI: Special Category "rf_waves" + CategoryMotionEmblem + InteractiveFluidSolarCanvas
        app = AstraCradleApp(base_dir=base, show_startup_popups=False)
        try:
            app.update()
            assert "rf_waves" in app.nav_buttons
            assert hasattr(app, "category_emblem")
            assert hasattr(app, "hero_motion_canvas")

            # Cycle through ALL 13 categories and verify bespoke emblem animation renders cleanly
            for cat in CategoryMotionEmblem.CATEGORIES:
                app.switch_nav(cat)
                app.category_emblem.draw()
                app.update()
                assert app.category_emblem.category == cat

            # Render Bandwidth & RF Waves special category with wave chart across band filters
            app._latest_rf_snap = snap
            app.switch_nav("rf_waves")
            app.update()
            assert hasattr(app, "rf_wave_chart")
            for bflt in ("All Bands", "2.4 GHz", "5 GHz", "6 GHz", "Bandwidth Only"):
                app.rf_wave_chart.set_band_filter(bflt)
                app.rf_wave_chart.draw()

            # Exercise InteractiveFluidSolarCanvas (Black & White fluid drag + Solar System Orrery modes)
            for mode in InteractiveFluidSolarCanvas.MODES:
                app.hero_motion_canvas.set_mode(mode)
                app.hero_motion_canvas._apply_mouse_impulse(95.0, 58.0, 4.5)
                app.hero_motion_canvas.draw()
                app.update()

            # Save Settings with Fluid Crest mode & verify persistence
            app.switch_nav("settings")
            app.set_motion_style_var.set("Fluid Crest (Black & White Style)")
            app.on_save_settings()
            app.update()
            assert cfg_mgr.load().motion_style == "Fluid Crest (Black & White Style)"
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
