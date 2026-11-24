"""Tests for v2.5.0: Tray latin-1 fix, Crash log archiving, Opt-in Desktop Icon & PC Wi-Fi Mesh Assistant."""

from __future__ import annotations

from pathlib import Path
import tempfile

from astracradle import __version__
from astracradle.__main__ import main
from astracradle.core.config import ConfigManager
from astracradle.core.crash_analyzer import CrashAnalyzer
from astracradle.core.journal import ForensicJournal
from astracradle.core.lifecycle import LifecycleManager
from astracradle.core.rollback import RollbackManager
from astracradle.core.wifi_mesh import MeshAdapterCapability, WifiMeshAssistantEngine
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.tray import TrayController


def test_tray_latin1_sanitization_and_crash_log_archiving() -> None:
    assert __version__ == "2.5.1"

    # 1. Verify tray title/notification strings never fail latin-1 encoding (X11 WM_NAME fix)
    bad_str = "AstraCradle — Dyson-Core · ✦ Status"
    safe = TrayController._safe_tray_text(bad_str)
    safe.encode("latin-1")  # Must not raise UnicodeEncodeError
    assert "\u2014" not in safe

    # 2. Verify CrashAnalyzer archives crash_*.log once diagnosed so it does not re-pop forever
    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        journal = ForensicJournal(cfg_mgr)
        ca = CrashAnalyzer(cfg_mgr, journal)

        try:
            raise ValueError("One-time historical test traceback")
        except ValueError as exc:
            ca.record_unhandled_exception(exc)

        rep1 = ca.inspect_on_startup()
        assert rep1 is not None
        assert rep1.crash_type == "python_exception"
        assert list(cfg_mgr.crash_dir.glob("crash_*.log")) == []
        assert len(list((cfg_mgr.crash_dir / "archived").glob("crash_*.log"))) == 1

        ca.mark_running("Balanced Daily")
        ca.mark_clean_shutdown()
        assert ca.inspect_on_startup() is None


def test_desktop_icon_opt_in_default_off_and_wmclass(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td) / "home"
        (fake_home / "Desktop").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        lc = LifecycleManager(cfg_mgr, rb, journal)
        repo_root = Path("/home/user/astracradle")

        # Default install_shortcuts must NOT create Desktop icon without explicit consent
        created_menu_only = lc.install_shortcuts(repo_root, os_override="Linux")
        assert (fake_home / ".local" / "share" / "applications" / "astracradle.desktop").exists()
        assert not (fake_home / "Desktop" / "AstraCradle.desktop").exists()
        dtxt = (fake_home / ".local" / "share" / "applications" / "astracradle.desktop").read_text(encoding="utf-8")
        assert "StartupWMClass=AstraCradle" in dtxt
        assert len(created_menu_only) == 1

        # Explicit include_desktop_icon=True adds Desktop icon
        created_with_desk = lc.install_shortcuts(repo_root, os_override="Linux", include_desktop_icon=True)
        assert (fake_home / "Desktop" / "AstraCradle.desktop").exists()
        assert len(created_with_desk) == 2

        # Verify Fish & Bash installers prompt before creating Desktop icon and link ~/.local/bin
        fish_txt = (repo_root / "install_astracradle.fish").read_text(encoding="utf-8")
        sh_txt = (repo_root / "install_astracradle.sh").read_text(encoding="utf-8")
        assert "--desktop-icon" in fish_txt and "[y/N]" in fish_txt and ".local/bin" in fish_txt
        assert "--desktop-icon" in sh_txt and "[y/N]" in sh_txt and ".local/bin" in sh_txt


def test_wifi_mesh_assistant_modes_ui_and_cli(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        journal = ForensicJournal(cfg_mgr)
        wma = WifiMeshAssistantEngine(cfg_mgr, journal)

        # 1. Probe parsing from real-style `iw dev` + `iw list` + `nmcli`
        iw_dev = "phy#0\n\tInterface wlan0\n\t\tssid HomeNet\n"
        iw_list = (
            "Wiphy phy0\n"
            "\tBand 1:\n\tBand 2:\n"
            "\tSupported interface modes:\n"
            "\t\t * managed\n\t\t * AP\n\t\t * mesh point\n"
            "\tvalid interface combinations:\n"
            "\t\t * #{ managed } <= 1, #{ AP } <= 1\n"
        )
        nm_out = "wlan0:wifi:connected:HomeNet\n"
        adapters = wma.probe_adapters(iw_dev_out=iw_dev, iw_list_out=iw_list, nmcli_out=nm_out)
        assert len(adapters) == 1
        assert adapters[0].supports_ap is True
        assert adapters[0].supports_mesh_point is True
        assert adapters[0].supports_ap_sta_concurrent is True

        # 2. Single-radio AP+STA plan
        plan_ap = wma.build_mesh_plan(
            uplink_input="HomeNet",
            mesh_ssid="AstraMesh-Home",
            band="5 GHz",
            adapters=adapters,
        )
        assert plan_ap.ok is True
        assert plan_ap.mode == "concurrent_ap_sta_relay"
        assert any("astra_ap0" in cmd for cmd in plan_ap.fish_commands)

        # 3. Dual-radio mesh plan when 2 Wi-Fi NICs exist
        dual = [
            adapters[0],
            MeshAdapterCapability(iface="wlan1", phy="phy1", supports_ap=True, bands=["2.4 GHz", "5 GHz"]),
        ]
        plan_dual = wma.build_mesh_plan(
            uplink_input="HomeNet",
            mesh_ssid="AstraMesh-Dual",
            band="5 GHz",
            adapters=dual,
        )
        assert plan_dual.ok is True
        assert plan_dual.mode == "dual_nic_mesh"
        assert plan_dual.mesh_iface == "wlan1"

        ok_dry, msg_dry = wma.apply_mesh_plan(plan_dual, dry_run=True)
        assert ok_dry is True and "DRY-RUN" in msg_dry

        # 4. GUI Wi-Fi page mounts Mesh Assistant cleanly
        app = AstraCradleApp(base_dir=Path(td), show_startup_popups=False)
        try:
            app.switch_nav("wifi")
            app.update()
            assert hasattr(app, "mesh_uplink_var")
            assert hasattr(app, "mesh_ssid_var")
            assert hasattr(app, "mesh_plan_lbl")
        finally:
            if app.winfo_exists():
                app.destroy()

        # 5. CLI --mesh-status & --mesh-plan
        rc = main(["--config-dir", td, "--mesh-status"])
        assert rc == 0
        rc2 = main(["--config-dir", td, "--mesh-plan", "eth0", "--mesh-ssid", "AstraMesh-CLI"])
        assert rc2 == 0
        out = capsys.readouterr().out
        assert "AstraMesh-CLI" in out
