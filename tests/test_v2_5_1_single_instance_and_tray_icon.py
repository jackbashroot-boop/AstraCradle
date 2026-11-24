"""Tests for v2.5.1: Single-instance enforcement & crisp KDE/X11 tray/desktop icon rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PIL import Image

from astracradle.core.config import ConfigManager
from astracradle.core.crash_analyzer import CrashAnalyzer
from astracradle.core.journal import ForensicJournal
from astracradle.core.lifecycle import LifecycleManager
from astracradle.core.rollback import RollbackManager
from astracradle.core.scripts_engine import ScriptsEngine
from astracradle.core.executor import GuardedExecutor
from astracradle.core.single_instance import SingleInstanceManager
from astracradle.ui.tray import TrayController, build_net_wm_icon_data, prepare_xorg_tray_image


def test_single_instance_cmdline_classifier() -> None:
    assert SingleInstanceManager.is_gui_cmdline(["/home/u/.venv/bin/astracradle-gui"])
    assert SingleInstanceManager.is_gui_cmdline(["python3", "-m", "astracradle"])
    assert SingleInstanceManager.is_gui_cmdline(["python3", "-m", "astracradle", "--minimized"])
    assert not SingleInstanceManager.is_gui_cmdline(["python3", "-m", "astracradle", "--self-check"])
    assert not SingleInstanceManager.is_gui_cmdline(["python3", "-m", "astracradle", "--mesh-status"])
    assert not SingleInstanceManager.is_gui_cmdline(["python3", "-m", "pytest", "-q"])


def test_single_instance_replaces_existing_process_cleanly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_mgr = ConfigManager(base_dir=tmp)
        journal = ForensicJournal(cfg_mgr)

        # Spawn a dummy long-lived child holding instance.lock + heartbeat.json
        child_code = (
            "import json, signal, sys, time\n"
            "from pathlib import Path\n"
            "from astracradle.core.config import ConfigManager\n"
            "from astracradle.core.single_instance import SingleInstanceManager\n"
            "cfg = ConfigManager(base_dir=sys.argv[1])\n"
            "si = SingleInstanceManager(cfg)\n"
            "si.acquire()\n"
            "(cfg.state_dir / 'heartbeat.json').write_text(json.dumps({'clean_shutdown': False, 'ts': time.time()}))\n"
            "running = True\n"
            "def _stop(_s, _f):\n"
            "    global running\n"
            "    running = False\n"
            "signal.signal(signal.SIGTERM, _stop)\n"
            "print('READY', flush=True)\n"
            "while running:\n"
            "    time.sleep(0.05)\n"
            "si.release()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code, tmp],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready_line = proc.stdout.readline().strip() if proc.stdout else ""
            assert ready_line == "READY"

            si_new = SingleInstanceManager(cfg_mgr)
            killed = si_new.terminate_existing_instances(timeout_sec=3.0, include_process_scan=False)
            assert proc.pid in killed
            rc = proc.wait(timeout=3.0)
            assert rc is not None

            # Replacement must mark clean_shutdown=True so CrashAnalyzer does NOT raise a false crash
            report = CrashAnalyzer(cfg_mgr, journal).inspect_on_startup()
            assert report is None

            # Acquire lock for new process and confirm release only deletes own PID lock
            si_new.acquire()
            assert si_new.read_lock_pid() == os.getpid()
            si_new.lock_path.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")
            si_new.release()
            assert si_new.lock_path.exists()
            si_new.acquire()
            si_new.release()
            assert not si_new.lock_path.exists()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)


def test_tray_xorg_alpha_composite_and_net_wm_icon_payload() -> None:
    emblem = Path(__file__).resolve().parents[1] / "astracradle" / "assets" / "astracradle_emblem_64.png"
    assert emblem.exists()
    with Image.open(emblem) as rgba:
        composited = prepare_xorg_tray_image(rgba, size=(32, 32))
        assert composited.mode == "RGB"
        assert composited.size == (32, 32)
        # Outer transparent corner must match dark panel backdrop (#1E242B), NOT leaked teal/random RGB
        assert composited.getpixel((0, 0)) == (30, 36, 43)
        # Center gold core must remain crisp and distinct from backdrop
        center = composited.getpixel((16, 16))
        assert center[0] > 170 and center[1] > 120

        wm_data = build_net_wm_icon_data(rgba, sizes=(32, 64))
        assert wm_data[0] == 32 and wm_data[1] == 32
        offset_64 = 2 + 32 * 32
        assert wm_data[offset_64] == 64 and wm_data[offset_64 + 1] == 64
        assert len(wm_data) == (2 + 32 * 32) + (2 + 64 * 64)
        # Corner alpha byte in ARGB should be 0
        assert (wm_data[2] >> 24) == 0
        # Center pixel alpha byte in ARGB should be opaque (255)
        center_argb = wm_data[2 + (16 * 32 + 16)]
        assert ((center_argb >> 24) & 0xFF) == 255


def test_linux_shortcuts_install_hicolor_index_theme(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fake_home = Path(tmp) / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        cfg_mgr = ConfigManager(base_dir=Path(tmp) / "cfg")
        journal = ForensicJournal(cfg_mgr)
        rb = RollbackManager(cfg_mgr)
        ex = GuardedExecutor(cfg_mgr, rb, journal)
        sc = ScriptsEngine(cfg_mgr, ex, journal)
        lm = LifecycleManager(cfg_mgr, rb, journal, sc)

        created = lm.install_shortcuts(Path(__file__).resolve().parents[1], os_override="Linux", include_desktop_icon=False)
        assert (fake_home / ".local" / "share" / "icons" / "hicolor" / "index.theme").exists()
        assert (fake_home / ".local" / "share" / "icons" / "hicolor" / "64x64" / "apps" / "astracradle.png").exists()
        desktop_file = fake_home / ".local" / "share" / "applications" / "astracradle.desktop"
        assert desktop_file.exists()
        dtxt = desktop_file.read_text(encoding="utf-8")
        assert "Actions=Open;Diagnose;FixSafe;Quit;" in dtxt
        assert "[Desktop Action Quit]" in dtxt and "--quit" in dtxt
        assert not (fake_home / "Desktop" / "AstraCradle.desktop").exists()
        assert len(created) == 1


def test_tray_controller_xorg_patch_geometry_and_icon_data() -> None:
    from types import SimpleNamespace

    emblem = Path(__file__).resolve().parents[1] / "astracradle" / "assets" / "astracradle_emblem_64.png"
    clicks: list[tuple[int, int]] = []
    tray = TrayController(
        emblem,
        on_show=lambda: None,
        on_scan=lambda: None,
        on_quit=lambda: None,
        on_context_menu=lambda rx, ry: clicks.append((rx, ry)),
    )
    if not tray.available:
        return
    started = tray.start()
    assert started is True
    try:
        time.sleep(0.25)
        icon = tray._icon
        if icon is not None and tray.is_xorg_backend:
            geom = icon._window.get_geometry()
            assert geom.width == 32 and geom.height == 32
            icon._assert_icon_data(32, 32)
            assert icon._icon_data.mode == "RGB"
            assert icon._icon_data.size == (32, 32)
            assert icon._icon_data.getpixel((0, 0)) == (30, 36, 43)
            import Xlib.X

            bp_handler = icon._message_handlers.get(Xlib.X.ButtonPress)
            assert callable(bp_handler)
            bp_handler(SimpleNamespace(detail=3, root_x=420, root_y=1050))
            assert clicks == [(420, 1050)]
    finally:
        tray.stop()


def test_3d_hover_gif_transparency_and_taskbar_boost_animation() -> None:
    from PIL import ImageSequence
    from astracradle.ui.app import AstraCradleApp

    assert not SingleInstanceManager.is_gui_cmdline(["python3", "-m", "astracradle", "--quit"])

    hover_gif = Path(__file__).resolve().parents[1] / "astracradle" / "assets" / "astracradle_hover.gif"
    with Image.open(hover_gif) as gim:
        frames = [fr.convert("RGBA") for fr in ImageSequence.Iterator(gim)]
        assert len(frames) == 48
        assert frames[0].getpixel((0, 0))[3] == 0

    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=td, show_startup_popups=False)
        try:
            app.update()
            assert len(app._hover_icon_frames) == 48
            app._start_hover_icon_anim(boost=True)
            assert app._hover_icon_boost is True
            assert app._hover_icon_after_id is not None
            app._stop_hover_icon_anim()
            assert app._hover_icon_boost is False
            assert app._hover_icon_after_id is None
            # Right-click context menu populates Open & Quit entries
            app._show_tray_context_menu(100, 100)
            assert getattr(app, "_tray_ctx_menu", None) is not None
            labels = [
                app._tray_ctx_menu.entrycget(i, "label")
                for i in range(app._tray_ctx_menu.index("end") + 1)
                if app._tray_ctx_menu.type(i) in ("command", "cascade")
            ]
            assert "Open AstraCradle" in labels
            assert "Quit AstraCradle" in labels
            app._tray_ctx_menu.unpost()
        finally:
            app.destroy()


def test_no_font_double_scaling_and_yes_no_dialog_no_clipping() -> None:
    from astracradle.theme import get_palette, get_ui_fonts
    from astracradle.ui.app import AstraCradleApp
    from astracradle.ui.modals import YesNoActionDialog

    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=td, show_startup_popups=False)
        try:
            app.update()
            # Fonts must not be double-scaled on top of ctk.set_widget_scaling
            assert app.fonts["body"][1] == get_ui_fonts(1.0)["body"][1]

            dlg = YesNoActionDialog(
                app,
                get_palette("Obsidian Horology"),
                app.fonts,
                "Less-Traffic Wi-Fi Channel Recommended",
                "Switch 'Duffwifi' from 5GHz Ch 157 to 2.4GHz Ch 11 (F6:C1:14:F8:66:7C)?",
                detail_lines=[
                    "Cleanest RF channels right now -> 2.4GHz: ch 11 | 5GHz: ch 44 | 6GHz: ch 1.",
                    "Active link: 'Duffwifi' (F6:C1:14:FF:66:7D) (Signal 52%, Interference 88.0).",
                    "Current BSSID is already on the cleanest available channel for this SSID.",
                ],
            )
            dlg.update()
            assert dlg.winfo_width() >= 540
            assert dlg.winfo_height() >= dlg.winfo_reqheight()
            dlg.destroy()

            # Clicking Benchmark Now on Settings must rebuild cleanly without stacking duplicate Settings pages
            app.switch_nav("settings")
            app._on_capture_benchmark()
            app._on_capture_benchmark()
            app.update()
            assert len(app.pages["settings"].winfo_children()) == 1
        finally:
            app.destroy()


def test_conflict_detector_skips_kernel_threads_and_gpu_reads_amdgpu_hwmon(monkeypatch) -> None:
    from types import SimpleNamespace
    import astracradle.core.pc_extras as pex
    import astracradle.core.advanced_controls as ac_mod

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        journal = ForensicJournal(cfg_mgr)

        fake_procs = [
            SimpleNamespace(info={"pid": 143, "ppid": 2, "name": "kworker/u128:1-events_unbound", "cmdline": []}),
            SimpleNamespace(info={"pid": 994, "ppid": 1, "name": "ananicy-cpp", "cmdline": ["/usr/bin/ananicy-cpp", "start"]}),
        ]
        monkeypatch.setattr(pex.psutil, "process_iter", lambda _attrs=None: fake_procs)
        monkeypatch.setattr(pex.shutil, "which", lambda _n: None)
        cd = pex.ConflictProgramDetector(journal)
        conflicts = cd.scan_conflicts()
        ids = [c.conflict_id for c in conflicts]
        assert "unbound" not in ids
        assert "ananicy-cpp" in ids

        # Simulate Linux /sys/class/hwmon with nct6687 + amdgpu
        fake_hwmon = Path(td) / "hwmon"
        hw0 = fake_hwmon / "hwmon0"
        hw1 = fake_hwmon / "hwmon1"
        hw0.mkdir(parents=True)
        hw1.mkdir(parents=True)
        (hw0 / "name").write_text("nct6687\n", encoding="utf-8")
        (hw0 / "pwm1").write_text("128\n", encoding="utf-8")
        (hw1 / "name").write_text("amdgpu\n", encoding="utf-8")
        (hw1 / "pwm1").write_text("77\n", encoding="utf-8")
        (hw1 / "temp1_input").write_text("55000\n", encoding="utf-8")
        (hw1 / "power1_average").write_text("42500000\n", encoding="utf-8")
        (hw1 / "power1_cap").write_text("260000000\n", encoding="utf-8")

        monkeypatch.setattr(ac_mod.shutil, "which", lambda _n: None)
        monkeypatch.setattr(ac_mod.platform, "system", lambda: "Linux")
        orig_path = ac_mod.Path

        def _fake_path(p):
            return fake_hwmon if str(p) == "/sys/class/hwmon" else orig_path(p)

        monkeypatch.setattr(ac_mod, "Path", _fake_path)
        gpu = ac_mod.GpuPowerAndFanController.probe_live_power_and_fan()
        assert gpu["name"] == "amdgpu"
        assert gpu["temp_c"] == "55.0"
        assert gpu["power_draw_w"] == "42.5"
        assert gpu["fan_percent"] == "30"


def test_all_gifs_synced_smooth_loop_and_no_stutter() -> None:
    """Verifies all 3 GIFs match across asset dirs, share timing, wrap smoothly with no stutter, and play in sync."""
    from PIL import ImageChops, ImageSequence
    from astracradle.ui.app import AstraCradleApp
    from astracradle.ui.modals import FloatingMiniOrbWindow
    from astracradle.theme import get_palette

    repo_root = Path(__file__).resolve().parents[1]
    gif_names = ("astracradle_hover.gif", "astracradle_animated.gif", "astracradle_badge_animated.gif")

    for name in gif_names:
        pkg_p = repo_root / "astracradle" / "assets" / name
        root_p = repo_root / "assets" / name
        assert pkg_p.exists() and root_p.exists()
        assert pkg_p.read_bytes() == root_p.read_bytes(), f"{name} out of sync between assets/ and astracradle/assets/"

        with Image.open(pkg_p) as im:
            frames = [fr.convert("RGBA") for fr in ImageSequence.Iterator(im)]
            durs = [int(fr.info.get("duration", im.info.get("duration", 0))) for fr in ImageSequence.Iterator(im)]
            assert len(frames) == 48
            assert set(durs) == {30}
            assert im.info.get("loop", 0) == 0

            # Measure per-pixel mean absolute difference across all consecutive pairs AND the wrap seam (47 -> 0)
            deltas = []
            for i in range(len(frames)):
                a = frames[i].tobytes()
                b = frames[(i + 1) % len(frames)].tobytes()
                mean_diff = sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))
                deltas.append(mean_diff)
            wrap_diff = deltas[-1]
            max_step = max(deltas[:-1])
            assert min(deltas) > 0.25, f"{name} has duplicate/frozen frames causing stutter"
            assert wrap_diff <= max_step * 1.45, f"{name} wrap seam stutter detected ({wrap_diff:.2f} vs {max_step:.2f})"

    # Verify AnimatedGifLabel (sidebar + about) stay locked to same monotonic clock and FloatingMiniOrb updates in-place
    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=td, show_startup_popups=False)
        try:
            app.update()
            assert hasattr(app, "sidebar_gif") and hasattr(app, "about_gif")
            idx_s, _ = app.sidebar_gif._frame_for_clock()
            idx_a, _ = app.about_gif._frame_for_clock()
            assert abs(idx_s - idx_a) <= 1

            app.sidebar_gif.set_paused(True)
            assert app.sidebar_gif._after_id is None
            app.sidebar_gif.set_paused(False)
            assert app.sidebar_gif._after_id is not None

            orb = FloatingMiniOrbWindow(
                app,
                get_palette("Obsidian Horology"),
                on_restore=lambda: None,
                gif_path=repo_root / "astracradle" / "assets" / "astracradle_hover.gif",
            )
            img_item_id = orb._img_item
            assert img_item_id is not None
            time.sleep(0.06)
            orb._tick()
            assert orb._img_item == img_item_id  # Updated in place, no canvas delete/recreate stutter
            orb.destroy()
        finally:
            app.destroy()


def test_wifi_permission_auto_select_and_10min_channel_hold() -> None:
    """Verifies permission-gated Wi-Fi auto-channel selection and strict >=10 minute channel hold."""
    from unittest.mock import patch
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.wifi_channel_optimizer import WifiApObservation, WifiChannelOptimizer
    from astracradle.ui.app import AstraCradleApp

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        journal = ForensicJournal(cfg_mgr)
        opt = WifiChannelOptimizer(journal=journal, state_dir=Path(td))

        aps_t0 = [
            WifiApObservation(ssid="HomeNet", bssid="AA:BB:CC:DD:EE:01", band="2.4GHz", channel=6, freq_mhz=2437, signal_pct=70, in_use=True),
            WifiApObservation(ssid="Neighbor1", bssid="11:22:33:44:55:01", band="2.4GHz", channel=6, freq_mhz=2437, signal_pct=85, in_use=False),
            WifiApObservation(ssid="HomeNet", bssid="AA:BB:CC:DD:EE:02", band="5GHz", channel=149, freq_mhz=5745, signal_pct=82, in_use=False),
        ]

        t0 = 1_800_000_000.0
        with patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd in ("nmcli", "iw") else None), \
             patch("subprocess.run") as mrun:
            mrun.return_value.returncode = 0
            mrun.return_value.stdout = ""
            mrun.return_value.stderr = ""

            res1 = opt.optimize_connection(
                dry_run=False,
                prefer_5ghz=True,
                disable_powersave=False,
                aps_override=aps_t0,
                min_hold_sec=600.0,
                now_epoch=t0,
            )
            assert res1.switched is True
            assert res1.best_bssid_channel == 149
            assert res1.hold_active is True
            assert res1.hold_remaining_sec >= 600.0

        # 5 minutes later (300s < 600s), even if Ch 36 looks cleaner than Ch 149, 10m hold must keep Ch 149!
        aps_t5m = [
            WifiApObservation(ssid="HomeNet", bssid="AA:BB:CC:DD:EE:02", band="5GHz", channel=149, freq_mhz=5745, signal_pct=75, in_use=True),
            WifiApObservation(ssid="LoudNeighbor", bssid="11:22:33:44:55:99", band="5GHz", channel=149, freq_mhz=5745, signal_pct=95, in_use=False),
            WifiApObservation(ssid="HomeNet", bssid="AA:BB:CC:DD:EE:03", band="5GHz", channel=36, freq_mhz=5180, signal_pct=88, in_use=False),
        ]
        res_held = opt.optimize_connection(
            dry_run=False,
            prefer_5ghz=True,
            disable_powersave=False,
            aps_override=aps_t5m,
            min_hold_sec=600.0,
            now_epoch=t0 + 300.0,
        )
        assert res_held.hold_active is True
        assert res_held.switched is False
        assert 299.0 <= res_held.hold_remaining_sec <= 301.0

        rec_held = opt.recommend_channels(
            prefer_5ghz=True,
            disable_powersave=False,
            aps_override=aps_t5m,
            min_hold_sec=600.0,
            now_epoch=t0 + 300.0,
        )
        assert rec_held["hold_active"] is True
        assert rec_held["can_roam"] is False

        # After 10+ minutes (605s >= 600s), hold expires and switching to cleaner Ch 36 is permitted again
        with patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd in ("nmcli", "iw") else None), \
             patch("subprocess.run") as mrun:
            mrun.return_value.returncode = 0
            mrun.return_value.stdout = ""
            mrun.return_value.stderr = ""
            res_expired = opt.optimize_connection(
                dry_run=False,
                prefer_5ghz=True,
                disable_powersave=False,
                aps_override=aps_t5m,
                min_hold_sec=600.0,
                now_epoch=t0 + 605.0,
            )
            assert res_expired.switched is True
            assert res_expired.best_bssid_channel == 36
            assert res_expired.hold_active is True

        # Verify UI Consent wizard permission toggles wifi_prompt_before_apply=False & wifi_min_channel_hold_min>=10
        app = AstraCradleApp(base_dir=td, show_startup_popups=False)
        try:
            assert app.config_obj.consent_wifi_auto_channel is False
            assert app.config_obj.wifi_prompt_before_apply is True
            app._on_toggle_wifi_prompt(False)
            assert app.config_obj.consent_wifi_auto_channel is True
            assert app.config_obj.wifi_prompt_before_apply is False
            assert app.config_obj.wifi_min_channel_hold_min >= 10
        finally:
            app.destroy()

