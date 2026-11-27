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


def test_task_progress_and_completion_toasts_for_tv_bluetooth_and_buttons() -> None:
    """Verifies in-progress feedback on task/button start and completion toast on finish (TV/BT scan, Deep Scan, buttons)."""
    import customtkinter as ctk
    from astracradle.ui.app import AstraCradleApp

    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=td, show_startup_popups=False)
        try:
            app.update()
            toasts: list[str] = []
            orig_show = app.show_toast

            def _cap_toast(msg: str, color=None, undo_entry_id=None) -> None:
                toasts.append(str(msg))
                orig_show(msg, color=color, undo_entry_id=undo_entry_id)

            app.show_toast = _cap_toast  # type: ignore[method-assign]

            # 1. Explicit TV + Bluetooth scan start -> completion toast
            app.begin_task_progress("lan_tv", "Scanning Wi-Fi/LAN for Smart TVs and Bluetooth devices...")
            assert "lan_tv" in app._active_tasks
            assert "⏳" in app.topbar_status.cget("text")
            assert any("In progress:" in t for t in toasts)

            app._ui_queue.put(("lan_tv_scanned", ["tv1"], ["bt1", "bt2"]))
            app._drain_ui_queue()
            assert "lan_tv" not in app._active_tasks
            assert any("TV & Bluetooth scan complete" in t and "1 Smart TV/LAN" in t and "2 Bluetooth" in t for t in toasts)

            # 2. Deep scan starts in-progress state and completes when scan result arrives
            app.on_deep_scan()
            assert "deep_scan" in app._active_tasks
            assert any("Deep scanning 8 subsystems" in t for t in toasts)

            # 3. Generic CTkButton click shows in-progress feedback & completion toast automatically
            app._active_tasks.clear()
            clicked_flag = {"ran": False}
            b = ctk.CTkButton(app.topbar, text="Test Action", command=lambda: clicked_flag.__setitem__("ran", True))
            b.pack(side="left")
            app.update()

            class _FakeEvt:
                widget = b

            toasts.clear()
            app._on_any_button_press_progress(_FakeEvt())
            assert "⏳ In progress: Test Action" in app.topbar_status.cget("text")
            # Wait for 35ms post-click check
            end_t = time.monotonic() + 0.12
            while time.monotonic() < end_t:
                app.update()
                time.sleep(0.015)
            assert any("✓ Test Action complete" in t for t in toasts)
        finally:
            app.destroy()


def test_persistent_machine_install_memory_and_wifi_mesh_password_toggle() -> None:
    """Verifies persistent machine update memory auto-reapply AND Wi-Fi Mesh password Hide/Show toggle."""
    from unittest.mock import patch
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.lifecycle import LifecycleManager
    from astracradle.core.rollback import RollbackManager
    from astracradle.core.scripts_engine import ScriptsEngine
    from astracradle.core.executor import GuardedExecutor
    from astracradle.core.updater import ModuleAndUpdateManager
    from astracradle.ui.app import AstraCradleApp

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        cfg = cfg_mgr.load()
        journal = ForensicJournal(cfg_mgr)
        rb = RollbackManager(cfg_mgr)
        ex = GuardedExecutor(cfg_mgr, rb, journal)
        sc = ScriptsEngine(cfg_mgr, ex, journal)
        lc = LifecycleManager(cfg_mgr, rb, journal, sc)
        upd = ModuleAndUpdateManager(cfg_mgr, journal)

        repo_root = Path(__file__).resolve().parents[1]
        mem = upd.record_install_memory(
            install_dir=repo_root,
            install_method="fish",
            desktop_icon=True,
            shortcuts_installed=True,
            cachyos_fish_wifi_hooks=True,
            shell_links_installed=True,
            step_note="Initial machine setup",
            config=cfg,
        )
        assert upd.install_memory_path.exists()
        assert mem["desktop_icon"] is True
        assert "cachyos_fish_wifi_hooks" in mem["remembered_steps"]

        # Simulate update on the same machine: remembered steps & desktop_icon=True auto-reapply without prompting
        with patch.object(lc, "install_shortcuts", return_value=[Path(td) / "astracradle.desktop"]) as m_short:
            reapplied, mem2 = upd.reapply_remembered_setup(cfg, lc, repo_dir=repo_root)
            m_short.assert_called_once_with(repo_root, include_desktop_icon=True)
            assert any("Desktop Icon: YES" in s for s in reapplied)
            assert len(mem2["update_history"]) >= 2

        # Verify Wi-Fi Mesh password Hide/Show toggle in UI (hidden by default, reveals & masks cleanly)
        app = AstraCradleApp(base_dir=td, show_startup_popups=False)
        try:
            app.switch_nav("wifi")
            app.update()
            assert hasattr(app, "mesh_pass_entry") and hasattr(app, "mesh_pass_toggle_btn")
            assert app.mesh_pass_hidden_var.get() is True
            assert app.mesh_pass_entry.cget("show") == "•"
            assert app.mesh_pass_toggle_btn.cget("text") == "Show"

            app.mesh_pass_var.set("SuperSecretMeshKey99")
            # Build plan while hidden -> plan label masks passphrase
            plan = app.wifi_mesh.build_mesh_plan(
                uplink_input="eth0",
                mesh_ssid="AstraMesh-Array",
                passphrase="SuperSecretMeshKey99",
            )
            app._latest_mesh_plan = plan
            app.mesh_pass_toggle_btn.invoke()
            app.update()
            assert app.mesh_pass_hidden_var.get() is False
            assert app.mesh_pass_entry.cget("show") == ""
            assert app.mesh_pass_toggle_btn.cget("text") == "Hide"
            assert "SuperSecretMeshKey99" in app.mesh_plan_lbl.cget("text")

            # Toggle back to hidden -> masks entry and plan label again
            app.mesh_pass_toggle_btn.invoke()
            app.update()
            assert app.mesh_pass_hidden_var.get() is True
            assert app.mesh_pass_entry.cget("show") == "•"
            assert app.mesh_pass_toggle_btn.cget("text") == "Show"
            assert "SuperSecretMeshKey99" not in app.mesh_plan_lbl.cget("text")
            assert "••••••••••••" in app.mesh_plan_lbl.cget("text")
        finally:
            app.destroy()


def test_self_heal_auto_prune_not_warning_and_autostart_refresh(monkeypatch) -> None:
    import os
    from astracradle.core.executor import GuardedExecutor
    from astracradle.core.lifecycle import LifecycleManager
    from astracradle.core.os_adapters import get_os_adapter
    from astracradle.core.rollback import RollbackManager
    from astracradle.core.scripts_engine import ScriptsEngine
    from astracradle.core.self_heal import SelfHealEngine

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        cfg = cfg_mgr.load()
        cfg.quarantine_max_mb = 1
        cfg_mgr.save(cfg)
        journal = ForensicJournal(cfg_mgr)

        # Put a >1 MB dummy item in quarantine so Quarantine & Log Cap Guard auto-prunes it
        q_item = cfg_mgr.quarantine_dir / "old_test_blob.bin"
        q_item.parent.mkdir(parents=True, exist_ok=True)
        q_item.write_bytes(b"X" * (1_200_000))
        os.utime(q_item, (1_000_000, 1_000_000))

        rb = RollbackManager(cfg_mgr)
        ex = GuardedExecutor(cfg_mgr, rb, journal)
        sc = ScriptsEngine(cfg_mgr, ex, journal)
        sh = SelfHealEngine(cfg_mgr, rb, journal)
        results, _audit = sh.run_self_check(get_os_adapter(), cfg)
        q_check = [r for r in results if r.check_name == "Quarantine & Log Cap Guard"][0]
        assert q_check.repaired_automatically is True
        entries = journal.read_entries(limit=25, newest_first=True)
        self_check_entries = [e for e in entries if e.action == "run_self_check"]
        assert self_check_entries and self_check_entries[0].outcome == "repaired"

        # Existing ~/.config/autostart/astracradle.desktop from v1.2.0 must be refreshed to current python path
        fake_home = Path(td) / "home"
        autostart_file = fake_home / ".config" / "autostart" / "astracradle.desktop"
        autostart_file.parent.mkdir(parents=True, exist_ok=True)
        autostart_file.write_text(
            "[Desktop Entry]\nName=AstraCradle\nExec=/old/v1.2.0/.venv/bin/python -m astracradle --minimized\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        lc = LifecycleManager(cfg_mgr, rb, journal, sc)
        lc.install_shortcuts(Path(__file__).resolve().parents[1], include_desktop_icon=False)
        updated = autostart_file.read_text(encoding="utf-8")
        assert "/old/v1.2.0/" not in updated
        assert "--minimized" in updated


def test_sunshine_inspect_ignores_47990_astrabeam_collision_and_restart_resets_failed(monkeypatch) -> None:
    from types import SimpleNamespace
    from astracradle.core.astrabeam import SunshineMoonlightBridge

    # Even if AstraBeam is bound on 47990, inspect() checks Sunshine ports 47989/47984 & systemd unit
    monkeypatch.setattr(SunshineMoonlightBridge, "_is_port_open", staticmethod(lambda _h, p, timeout=0.25: p == 47990))
    monkeypatch.setattr("astracradle.core.astrabeam.shutil.which", lambda name: "/usr/bin/sunshine" if name in ("sunshine", "systemctl") else None)
    monkeypatch.setattr(
        "astracradle.core.astrabeam.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=3, stdout="", stderr=""),
    )
    st = SunshineMoonlightBridge.inspect()
    assert st.sunshine_installed is True
    assert st.sunshine_running is False

    calls = []

    def _fake_run(cmd, **_kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("astracradle.core.astrabeam.subprocess.run", _fake_run)
    ok, msg = SunshineMoonlightBridge.start_or_restart_sunshine()
    assert ok is True
    assert any("reset-failed" in c for c in calls)
    assert any("restart" in c for c in calls)
    assert "47984/47989" in msg


def test_channel_hold_active_suppresses_recommendation_popup(monkeypatch) -> None:
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.wifi_channel_optimizer import WifiApObservation, WifiChannelOptimizer

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        journal = ForensicJournal(cfg_mgr)
        opt = WifiChannelOptimizer(journal)
        opt.record_channel_hold("Duffwifi", "F6:C1:14:FF:66:7D", 157, "5GHz", now_epoch=1000.0)

        monkeypatch.setattr(opt, "detect_wifi_interface", lambda: "wlan0")
        monkeypatch.setattr(opt, "_get_active_wifi_connection", lambda _i=None: ("Duffwifi", "F6:C1:14:FF:66:7D"))
        monkeypatch.setattr(opt, "_is_powersave_enabled", lambda _i: True)
        rec = opt.recommend_channels(
            aps_override=[
                WifiApObservation("Duffwifi", "F6:C1:14:FF:66:7D", 157, 5785, "5GHz", 52, 866, True),
                WifiApObservation("Duffwifi", "F6:C1:14:F8:66:7C", 44, 5220, "5GHz", 88, 1200, False),
            ],
            min_hold_sec=600.0,
            now_epoch=1200.0,
        )
        assert rec["hold_active"] is True
        assert rec["has_recommendation"] is False


def test_remote_desktop_stream_guard_suppresses_wifi_changes_and_protects_sunshine(monkeypatch) -> None:
    from types import SimpleNamespace
    from astracradle.core.astrabeam import SunshineMoonlightBridge
    from astracradle.core.config import ConfigManager, DEFAULT_PROTECTED_PROCESSES
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.wifi_channel_optimizer import WifiApObservation, WifiChannelOptimizer

    # Streaming / compositor processes must never be throttled by ProBalance
    low_prot = {p.lower() for p in DEFAULT_PROTECTED_PROCESSES}
    for proc_name in ("sunshine", "moonlight", "tailscaled", "pipewire", "kwin_wayland"):
        assert proc_name in low_prot

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        journal = ForensicJournal(cfg_mgr)
        opt = WifiChannelOptimizer(journal)

        # Simulate active Sunshine/Moonlight connection on UDP 47998
        fake_conn = SimpleNamespace(
            laddr=SimpleNamespace(ip="100.77.94.72", port=47998),
            raddr=SimpleNamespace(ip="100.77.94.88", port=51234),
            status="NONE",
        )
        monkeypatch.setattr("psutil.net_connections", lambda kind="inet": [fake_conn])
        active, reason = opt.detect_active_remote_desktop_session()
        assert active is True and "Sunshine/Moonlight" in reason

        # Even with NO active 10m hold, a live remote desktop stream suppresses channel switching & recommendations
        monkeypatch.setattr(opt, "detect_wifi_interface", lambda: "wlan0")
        monkeypatch.setattr(opt, "_get_active_wifi_connection", lambda _i=None: ("Duffwifi", "F6:C1:14:FF:66:7D"))
        monkeypatch.setattr(opt, "_is_powersave_enabled", lambda _i: True)
        aps = [
            WifiApObservation("Duffwifi", "F6:C1:14:FF:66:7D", 157, 5785, "5GHz", 52, 866, True),
            WifiApObservation("Duffwifi", "F6:C1:14:F8:66:7C", 44, 5220, "5GHz", 95, 1200, False),
        ]
        res = opt.optimize_connection(dry_run=False, aps_override=aps)
        assert res.switched is False
        assert "Stream Guard active" in res.action_taken
        rec = opt.recommend_channels(aps_override=aps)
        assert rec["has_recommendation"] is False

        # Verify Sunshine iPad/tablet optimizer picks primary kscreen-doctor output + VA-API
        kscreen_out = (
            "Output: 1 DP-5 uuid\n\tpriority 1\nOutput: 2 DP-4 uuid\n\tpriority 3\nOutput: 3 DP-3 uuid\n\tpriority 2\n"
        )
        monkeypatch.setattr(
            "astracradle.core.astrabeam.shutil.which",
            lambda n: "/usr/bin/" + n if n in ("kscreen-doctor", "sunshine") else None,
        )
        monkeypatch.setattr(
            "astracradle.core.astrabeam.subprocess.run",
            lambda *a, **kw: SimpleNamespace(returncode=0, stdout=kscreen_out, stderr=""),
        )
        assert SunshineMoonlightBridge.detect_primary_display_output() == "DP-5"
        sun_conf = Path(td) / "sunshine.conf"
        ok_opt, msg_opt = SunshineMoonlightBridge.optimize_sunshine_for_tablet("100.77.94.72", conf_path=sun_conf)
        assert ok_opt is True
        txt = sun_conf.read_text(encoding="utf-8")
        assert "output_name = DP-5" in txt
        assert "upnp = disabled" in txt


def test_desktop_widget_default_off_and_nonblocking_tray_notify() -> None:
    import time as _t
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.ui.tray import TrayController

    cfg = AppConfig()
    assert cfg.desktop_widget_enabled is False

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        loaded = cfg_mgr.load()
        assert loaded.desktop_widget_enabled is False

    # TrayController.notify must be non-blocking even if backend notify sleeps
    class _SlowIcon:
        def notify(self, _m, _t_str) -> None:
            _t.sleep(0.5)

    tc = TrayController(
        icon_path=Path(__file__).resolve().parents[1] / "astracradle" / "assets" / "astracradle_emblem.png",
        on_show=lambda: None,
        on_scan=lambda: None,
        on_quit=lambda: None,
    )
    tc._icon = _SlowIcon()
    t0 = _t.monotonic()
    assert tc.notify("Title", "Hello") is True
    assert (_t.monotonic() - t0) < 0.1


def test_iphone_16_pro_max_pwa_endpoints_and_swiftui_companion() -> None:
    import ssl
    import urllib.request
    from astracradle.core.astrabeam import AstraBeamServer
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal

    repo_root = Path(__file__).resolve().parents[1]
    ios_dir = repo_root / "mobile" / "ios" / "AstraCradleCompanion"
    for fname in ("AstraCradleCompanionApp.swift", "AstraBeamClient.swift", "ContentView.swift", "Info.plist"):
        assert (ios_dir / fname).exists(), f"Missing native iOS file {fname}"

    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=td)
        journal = ForensicJournal(cfg_mgr)
        srv = AstraBeamServer(journal, host="127.0.0.1", port=0, pin="654321", tls_enabled=False)
        ok, _ = srv.start()
        assert ok is True
        try:
            base = f"http://127.0.0.1:{srv.port}"
            html = urllib.request.urlopen(f"{base}/iphone", timeout=3).read().decode("utf-8")
            assert "iPhone 16 Pro Max" in html
            assert "viewport-fit=cover" in html
            assert "moonlight://" in html

            icon_bytes = urllib.request.urlopen(f"{base}/apple-touch-icon.png", timeout=3).read()
            assert icon_bytes[:8] == b"\x89PNG\r\n\x1a\n"

            man = urllib.request.urlopen(f"{base}/manifest.webmanifest", timeout=3).read().decode("utf-8")
            assert '"start_url": "/iphone"' in man
        finally:
            srv.stop()


def test_modular_system_presets_background_gating_and_cli() -> None:
    from astracradle.__main__ import main as cli_main
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.lifecycle import LifecycleManager
    from astracradle.core.modules import (
        ALL_MODULE_IDS,
        allowed_nav_keys,
        normalize_enabled_modules,
        resolve_preset_or_modules,
    )
    from astracradle.core.updater import ModuleAndUpdateManager

    # core_diagnostics is always retained even if omitted
    mods = normalize_enabled_modules(["remote_beam", "nonexistent_mod"])
    assert mods == ["core_diagnostics", "remote_beam"]
    navs = allowed_nav_keys(mods)
    assert "dashboard" in navs and "remote_beam" in navs and "wifi" not in navs

    # Streamer preset resolves cleanly
    s_mods, s_name = resolve_preset_or_modules("streamer")
    assert s_name == "Streamer & iPhone Remote"
    assert "remote_beam" in s_mods and "wifi_failover_mesh" in s_mods and "system_scanner" not in s_mods

    with tempfile.TemporaryDirectory() as td:
        rc = cli_main(["--config-dir", td, "--modules", "minimal"])
        assert rc == 0
        rc2 = cli_main(["--config-dir", td, "--list-modules"])
        assert rc2 == 0

        cm = ConfigManager(base_dir=td)
        cfg = cm.load()
        assert cfg.enabled_modules == ["core_diagnostics"]
        assert cfg.setup_wizard_completed is True
        # Disabled modules also shut off their background watchers
        assert cfg.astrashield_auto_start is False
        assert cfg.wifi_auto_optimize is False
        assert cfg.failover_enabled is False
        assert cfg.catalog_passive_scan is False
        assert cfg.probalance_enabled is False

        # Install memory preserves module choice across reinstall/reapply
        from astracradle.core.rollback import RollbackManager

        journal = ForensicJournal(cm)
        rb = RollbackManager(cm)
        upd = ModuleAndUpdateManager(cm, journal)
        fresh_cfg = cm.load()
        fresh_cfg.apply_module_selection(list(ALL_MODULE_IDS), preset_name="Full Suite (Everything)")
        reapplied, mem = upd.reapply_remembered_setup(fresh_cfg, LifecycleManager(cm, rb, journal))
        assert mem["enabled_modules"] == ["core_diagnostics"]
        assert cm.load().enabled_modules == ["core_diagnostics"]
        assert any("selected module" in r.lower() for r in reapplied)

        # Plain-English Setup Wizard apply_wizard_choices applies all 9 permissions & preferences cleanly
        wiz_cfg = cm.load()
        wiz_cfg.apply_wizard_choices(
            {
                "enabled_modules": ["core_diagnostics", "remote_beam", "wifi_failover_mesh"],
                "preset_name": "Streamer & iPhone Remote",
                "active_profile": "Gaming / Low-Latency",
                "theme": "High Contrast",
                "language": "es",
                "auto_update_mode": "notify",
                "simple_mode": True,
                "consent_telemetry": True,
                "consent_process_scan": True,
                "consent_installed_apps": False,
                "consent_bloat_scan": True,
                "consent_privacy_scan": True,
                "consent_crash_diagnostics": True,
                "consent_auto_remediate": True,
                "consent_wifi_auto_channel": True,
                "consent_auto_install_deps": True,
                "confirm_important_prompts": False,
                "quarantine_enabled": True,
                "auto_self_heal": True,
                "show_tray_icon": True,
                "minimize_to_tray": True,
                "desktop_widget_enabled": False,
                "launch_at_login": True,
                "astrashield_auto_start": True,  # gated OFF because privacy_shield isn't enabled
                "wifi_prefer_5ghz": True,
                "failover_enabled": True,
                "astrabeam_tailscale_only": True,
            }
        )
        cm.save(wiz_cfg)
        reloaded = cm.load()
        assert reloaded.setup_wizard_completed is True
        assert reloaded.consent_prompted is True
        assert reloaded.active_profile == "Gaming / Low-Latency"
        assert reloaded.theme == "High Contrast"
        assert reloaded.language == "es"
        assert reloaded.consent_privacy_scan is True
        assert reloaded.consent_installed_apps is False
        assert reloaded.consent_auto_install_deps is True
        assert reloaded.consent_wifi_auto_channel is True
        assert reloaded.wifi_auto_optimize is True
        assert reloaded.wifi_prompt_before_apply is False
        assert reloaded.astrashield_auto_start is False  # disabled module stays gated off
        assert reloaded.minimize_to_tray is True
        assert reloaded.launch_at_login is True


def test_catastrophic_mid_install_and_crash_recovery(monkeypatch) -> None:
    import subprocess
    from astracradle.core.config import ConfigManager
    from astracradle.core.crash_analyzer import CrashAnalyzer
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.os_adapters import get_os_adapter
    from astracradle.core.rollback import RollbackManager
    from astracradle.core.self_heal import SelfHealEngine
    from astracradle.core.updater import ModuleAndUpdateManager

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cm = ConfigManager(base_dir=base)
        cfg = cm.load()
        cfg.apply_wizard_choices(
            {
                "enabled_modules": ["core_diagnostics", "remote_beam"],
                "preset_name": "Custom",
                "consent_telemetry": True,
            }
        )
        cm.save(cfg)

        j = ForensicJournal(cm)
        rb = RollbackManager(cm)
        upd = ModuleAndUpdateManager(cm, j)
        upd.record_install_memory(install_method="fish", desktop_icon=False, shortcuts_installed=True, config=cfg)

        # 1. Simulate catastrophic power loss / SIGKILL leaving truncated config.json,
        #    corrupt machine_install_memory.json, orphaned .tmp files, and unclean heartbeat
        cm.config_path.write_text('{"theme": "Obsidian', encoding="utf-8")
        (cm.state_dir / "config.tmp").write_text("partial", encoding="utf-8")
        (cm.base_dir / "machine_install_memory.json").write_text("{corrupt_half_written", encoding="utf-8")
        (cm.base_dir / "machine_install_memory.json.tmp").write_text("half", encoding="utf-8")

        ca = CrashAnalyzer(cm, j)
        ca.mark_running("Balanced Daily")  # never called mark_clean_shutdown()

        # Verify SelfHealEngine + ConfigManager + Updater recover seamlessly from shadow backups
        events, audits = SelfHealEngine(cm, rb, j).run_self_check(get_os_adapter(), cm.load())
        assert all(ev.status in ("ok", "repaired") for ev in events if ev.check_name != "Companion Dependency Library Guard")
        recovered_cfg = cm.load()
        assert recovered_cfg.enabled_modules == ["core_diagnostics", "remote_beam"]
        assert recovered_cfg.setup_wizard_completed is True

        recovered_mem = upd.load_install_memory()
        assert recovered_mem["install_method"] == "fish"
        assert recovered_mem["enabled_modules"] == ["core_diagnostics", "remote_beam"]

        # CrashAnalyzer detects the unclean shutdown from mid-install/run
        report = ca.inspect_on_startup()
        assert report is not None

        # 2. Simulate subprocess killed mid-wizard (`SIGKILL` before finishing setup_wizard)
        repo_root = Path(__file__).resolve().parents[1]
        half_dir = Path(td) / "half_wizard"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; from astracradle.core.config import ConfigManager; "
                "from astracradle.core.journal import ForensicJournal; "
                "from astracradle.core.crash_analyzer import CrashAnalyzer; "
                f"cm=ConfigManager({str(half_dir)!r}); cfg=cm.load(); "
                "ca=CrashAnalyzer(cm, ForensicJournal(cm)); ca.mark_running('Balanced Daily'); "
                "time.sleep(10)",
            ],
            cwd=str(repo_root),
        )
        time.sleep(0.35)
        proc.kill()
        proc.wait(timeout=5)

        cm2 = ConfigManager(base_dir=half_dir)
        cfg2 = cm2.load()
        # setup_wizard_completed MUST still be False so next launch cleanly re-offers Setup Wizard
        assert cfg2.setup_wizard_completed is False
        assert CrashAnalyzer(cm2, ForensicJournal(cm2)).inspect_on_startup() is not None
        # Check fish/bash installers guard broken partial .venv recovery
        fish_txt = (repo_root / "install_astracradle.fish").read_text(encoding="utf-8")
        sh_txt = (repo_root / "install_astracradle.sh").read_text(encoding="utf-8")
        assert "m pip --version" in fish_txt and "rm -rf .venv" in fish_txt
        assert "m pip --version" in sh_txt and "rm -rf .venv" in sh_txt


def test_clean_uninstall_removes_all_new_artifacts_and_cli_uninstall(monkeypatch) -> None:
    from astracradle.__main__ import main as cli_main
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.lifecycle import LifecycleManager
    from astracradle.core.os_adapters import get_os_adapter
    from astracradle.core.rollback import RollbackManager
    from astracradle.core.updater import ModuleAndUpdateManager

    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td) / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        ac_dir = fake_home / ".astracradle"
        cm = ConfigManager(base_dir=ac_dir)
        cfg = cm.load()
        cfg.apply_wizard_choices(
            {
                "enabled_modules": ["core_diagnostics", "remote_beam", "wifi_failover_mesh"],
                "preset_name": "Streamer & iPhone Remote",
                "launch_at_login": True,
                "consent_telemetry": True,
            }
        )
        cm.save(cfg)

        j = ForensicJournal(cm)
        rb = RollbackManager(cm)
        lm = LifecycleManager(cm, rb, j)
        upd = ModuleAndUpdateManager(cm, j)
        upd.record_install_memory(install_method="fish", desktop_icon=True, shortcuts_installed=True, config=cfg)

        # Simulate all installed artifacts from recent updates
        lm.install_shortcuts(repo_root, include_desktop_icon=True, os_override="Linux")
        ok_login, _ = lm.set_launch_at_login(True, repo_root=repo_root, os_override="Linux")
        assert ok_login is True

        local_bin = fake_home / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        for bn in ("astracradle", "astracradle-gui", "astracradle-wifi"):
            (local_bin / bn).symlink_to(repo_root / "install_astracradle.fish")

        fish_hook = fake_home / ".config" / "fish" / "conf.d" / "astracradle_wifi.fish"
        fish_hook.parent.mkdir(parents=True, exist_ok=True)
        fish_hook.write_text("# wifi hook", encoding="utf-8")

        sd_user = fake_home / ".config" / "systemd" / "user"
        sd_user.mkdir(parents=True, exist_ok=True)
        wifi_svc = sd_user / "astracradle-wifi-opt.service"
        wifi_tmr = sd_user / "astracradle-wifi-opt.timer"
        wifi_svc.write_text("[Unit]", encoding="utf-8")
        wifi_tmr.write_text("[Timer]", encoding="utf-8")

        (cm.state_dir / "setup_wizard_draft.json").write_text('{"step": 2}', encoding="utf-8")
        (ac_dir / "certs").mkdir(parents=True, exist_ok=True)
        (ac_dir / "certs" / "astrabeam.crt").write_text("CERT", encoding="utf-8")

        # 1. Keep-data uninstall cleans shortcuts, autostart, CLI symlinks, Fish/systemd hooks & transient draft
        summary_keep = lm.uninstall_astracradle(
            get_os_adapter(),
            cfg,
            delete_user_data=False,
            restore_quarantine_first=True,
            uninstall_pip_package=False,
        )
        assert summary_keep.ok is True
        assert summary_keep.deleted_data_dir is False
        assert not (fake_home / ".config" / "autostart" / "astracradle.desktop").exists()
        assert not (fake_home / ".local" / "bin" / "astracradle").exists()
        assert not (fake_home / ".local" / "bin" / "astracradle-gui").exists()
        assert not (fake_home / ".local" / "bin" / "astracradle-wifi").exists()
        assert not fish_hook.exists()
        assert not wifi_svc.exists()
        assert not wifi_tmr.exists()
        assert not (cm.state_dir / "setup_wizard_draft.json").exists()
        assert ac_dir.exists() and (ac_dir / "machine_install_memory.json").exists()

        # 2. Full CLI uninstall (--uninstall --yes --delete-data) deletes ~/.astracradle completely
        rc = cli_main(["--config-dir", str(ac_dir), "--uninstall", "--yes", "--delete-data"])
        assert rc == 0
        assert not ac_dir.exists()


def test_windows_11_pythonw_null_stdio_and_installer_resilience(monkeypatch) -> None:
    import sys as _sys
    from astracradle.__main__ import _ensure_valid_stdio

    repo_root = Path(__file__).resolve().parents[1]
    bat_txt = (repo_root / "Install_AstraCradle.bat").read_text(encoding="utf-8")
    run_bat = (repo_root / "run_astracradle.bat").read_text(encoding="utf-8")
    ps1_txt = (repo_root / "scripts" / "quick_install.ps1").read_text(encoding="utf-8")

    # Verify Windows 11 Store alias bypass, Python 3.10-3.14 search, quoted paths, Tkinter check & auto-repair
    assert "Python314" in bat_txt and '"%%~P" -c "import sys, tkinter' in bat_txt
    assert '".venv\\Scripts\\python.exe" -m pip --version' in bat_txt
    assert 'start "AstraCradle" ".venv\\Scripts\\python.exe" -m astracradle' in bat_txt
    assert 'call "%~dp0Install_AstraCradle.bat"' in run_bat
    assert 'start "AstraCradle" ".venv\\Scripts\\python.exe" -m astracradle %*' in run_bat
    assert "LASTEXITCODE" in ps1_txt and "Unblock-File" in ps1_txt

    # Verify pythonw.exe / astracradle-gui.exe (sys.stdout=None, sys.stderr=None) gets safe streams
    with tempfile.TemporaryDirectory() as td:
        orig_out, orig_err = _sys.stdout, _sys.stderr
        try:
            monkeypatch.setattr(_sys, "stdout", None)
            monkeypatch.setattr(_sys, "stderr", None)
            _ensure_valid_stdio(td)
            assert _sys.stdout is not None and _sys.stderr is not None
            _sys.stderr.write("win11-stdio-ok\n")
            _sys.stderr.flush()
        finally:
            try:
                if _sys.stderr not in (None, orig_err):
                    _sys.stderr.close()
            except Exception:
                pass
            _sys.stdout = orig_out
            _sys.stderr = orig_err
        assert (Path(td) / "logs" / "gui_stdio.log").exists()


def test_first_load_windows_lag_fixes_and_pnp_throttle() -> None:
    from astracradle.core.usb_guard import UsbAndPeripheralGuard

    repo_root = Path(__file__).resolve().parents[1]
    app_txt = (repo_root / "astracradle" / "ui" / "app.py").read_text(encoding="utf-8")
    dash_txt = (repo_root / "astracradle" / "ui" / "pages" / "dashboard_snapshot.py").read_text(encoding="utf-8")
    usb_txt = (repo_root / "astracradle" / "core" / "usb_guard.py").read_text(encoding="utf-8")

    # No synchronous nvidia-smi calls on the main UI thread
    assert "GpuPowerAndFanController.probe_live_power_and_fan()" not in dash_txt
    assert "gpu_live = sensors.get(\"gpu_live\") or {}" in app_txt
    # Background worker guard + Fix Everything Safe off main thread + capped UI drain
    assert "_telemetry_in_flight" in app_txt
    assert '("fix_safe_done"' in app_txt
    assert "while processed < 8:" in app_txt
    # Windows PnP PowerShell scan throttled & hidden console
    assert "CREATE_NO_WINDOW" in usb_txt and "_win_pnp_cache" in usb_txt
    assert hasattr(UsbAndPeripheralGuard, "_win_pnp_cache")


def test_native_hermes_os_ai_gpu_recommendation_setup_tools_and_uninstall() -> None:
    import json
    from unittest.mock import patch
    from types import SimpleNamespace
    from astracradle.core.config import ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager, recommend_hermes_models
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.lifecycle import LifecycleManager
    from astracradle.core.modules import ALL_MODULE_IDS
    from astracradle.core.rollback import RollbackManager

    assert "hermes_ai" in ALL_MODULE_IDS

    # 1. Verify GPU VRAM tier auto-selection picks optimized local model + optimal free cloud model
    with patch("astracradle.core.hermes_agent.GpuTelemetryCollector.collect") as mock_gpu, patch(
        "astracradle.core.hermes_agent.GpuPowerAndFanController.probe_live_power_and_fan",
        return_value={"name": "NVIDIA GeForce RTX 4070"},
    ):
        mock_gpu.return_value = SimpleNamespace(available=True, name="NVIDIA GeForce RTX 4070", vram_total_mb=12288)
        rec_12 = recommend_hermes_models()
        assert rec_12.recommended_local_model == "qwen3:14b"
        assert rec_12.recommended_context_tokens == 32768
        assert rec_12.optimal_free_model == "qwen/qwen3-coder:free"

        mock_gpu.return_value = SimpleNamespace(available=True, name="NVIDIA GeForce RTX 4090", vram_total_mb=24576)
        rec_24 = recommend_hermes_models()
        assert rec_24.recommended_local_model == "qwen3.6:35b"
        assert rec_24.recommended_context_tokens == 65536

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=td)
        cfg = cm.load()
        assert cfg.floating_orb_on_minimize is False
        journal = ForensicJournal(cm)
        hm = HermesAgentManager(cm, journal)

        # Strict Local-Only AI forces local_gpu/ollama even if mode was previously cloud
        cfg.hermes_mode = "free_cloud"
        cfg.hermes_provider = "openrouter_free"
        cfg.hermes_local_only = True
        local_spec = hm.resolve_provider_spec(cfg)
        assert local_spec["mode"] == "local_gpu" and local_spec["provider"] == "ollama" and local_spec["local_only"] is True
        cfg.hermes_local_only = False

        # 2. Automated setup creates HERMES_HOME, MEMORY.md, USER.md, SOUL.md, skills, sessions.db, and 0600 .env
        cfg.hermes_mode = "free_cloud"
        cfg.hermes_provider = "openrouter_free"
        ok, _msg, st = hm.install_and_setup_hermes(
            cfg,
            api_key="sk-or-test-key-1234",
            install_upstream_pkg=False,
            pull_local_model=False,
        )
        assert ok is True
        assert st.ready is True
        assert st.api_key_configured is True
        assert hm.memory_path.exists()
        assert hm.user_md_path.exists()
        assert hm.soul_md_path.exists()
        assert (hm.skills_dir / "astracradle-os-bridge" / "SKILL.md").exists()
        assert hm.load_env().get("OPENROUTER_API_KEY") == "sk-or-test-key-1234"

        # 3. Verify 4-tier Hermes permission levels (read_only, safe_user, standard_user up to just below sudo, sudo_with_prompt)
        cfg.hermes_permission_level = "read_only"
        ro_mem = hm.execute_tool("memory_save", {"fact": "Should be blocked"}, cfg)
        assert ro_mem["ok"] is False and ro_mem.get("blocked") is True

        cfg.hermes_permission_level = "safe_user"
        cfg.hermes_allow_shell = False
        blocked = hm.execute_tool("shell_exec", {"command": "echo hi"}, cfg)
        assert blocked["ok"] is False and blocked.get("blocked") is True
        outside_write = hm.execute_tool("file_write", {"path": str(Path(td).parent / "outside.txt"), "content": "x"}, cfg)
        assert outside_write["ok"] is False and outside_write.get("blocked") is True

        cfg.hermes_permission_level = "standard_user"
        ok_user_sh = hm.execute_tool("shell_exec", {"command": "echo standard_user_ok"}, cfg)
        assert ok_user_sh["ok"] is True and "standard_user_ok" in ok_user_sh["stdout"]
        blocked_sudo = hm.execute_tool("shell_exec", {"command": "sudo id"}, cfg)
        assert blocked_sudo["ok"] is False and blocked_sudo.get("blocked") is True
        blocked_sudo_tool = hm.execute_tool("sudo_exec", {"command": "pacman -Qk", "reason": "Verify packages"}, cfg)
        assert blocked_sudo_tool["ok"] is False and blocked_sudo_tool.get("blocked") is True

        cfg.hermes_permission_level = "sudo_with_prompt"
        from astracradle.core.elevation import ElevationResult, ElevationRunner

        hm.elevation = ElevationRunner(password_prompt_cb=lambda _t, _r: "typed-pw")
        with patch.object(
            hm.elevation,
            "run_privileged",
            return_value=ElevationResult(True, 0, "uid=0(root)", "", cancelled=False, method="sudo"),
        ) as mock_priv:
            ok_sudo = hm.execute_tool("sudo_exec", {"command": "sudo pacman -Qk", "reason": "Check packages"}, cfg)
            assert ok_sudo["ok"] is True and "uid=0" in ok_sudo["stdout"]
            assert mock_priv.called
        hm.elevation = None
        cfg.hermes_permission_level = "safe_user"

        # 6. Regression check: no unescaped '%' in argparse help strings (Python 3.14 ValueError guard)
        import re as _re
        main_txt = (Path(__file__).resolve().parents[1] / "astracradle" / "__main__.py").read_text(encoding="utf-8")
        for m in _re.finditer(r'help="([^"]*)"', main_txt):
            assert not _re.search(r"%(?![%(])", m.group(1)), f"Unescaped % in argparse help: {m.group(1)}"

        # 4. Multi-step tool-calling loop executes native AstraCradle tools and auto-crystallizes a SKILL.md
        calls = {"n": 0}

        def _fake_llm(_spec, _key, _msgs, _tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return "", [
                    {"id": "c1", "function": {"name": "astra_system_status", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "memory_save", "arguments": json.dumps({"fact": "User has RTX 4070"})}},
                    {"id": "c3", "function": {"name": "cron_schedule", "arguments": json.dumps({"name": "Check GPU", "prompt": "Check status", "interval_minutes": 60})}},
                ]
            return "All 3 checks completed and saved to MEMORY.md.", []

        res = hm.run_prompt("Check my PC status, remember my GPU, and schedule a check", cfg, session_id="s1", llm_caller=_fake_llm)
        assert res.ok is True
        assert len(res.tool_events) == 3
        assert res.skill_created and Path(res.skill_created).exists()
        assert "User has RTX 4070" in hm.memory_path.read_text(encoding="utf-8")
        assert len(hm.list_cron_jobs()) == 1
        assert len(hm.list_sessions()) == 1

        # 5. Clean uninstall deletes ~/.astracradle/hermes completely
        from astracradle.core.os_adapters import get_os_adapter

        rb = RollbackManager(cm)
        lm = LifecycleManager(cm, rb, journal)
        with patch.object(lm, "shortcut_paths", return_value=[]), patch.object(
            lm, "set_launch_at_login", return_value=(True, "ok")
        ):
            summary = lm.uninstall_astracradle(
                get_os_adapter(),
                cfg,
                delete_user_data=True,
                restore_quarantine_first=False,
                uninstall_pip_package=False,
            )
        assert summary.deleted_data_dir is True
        assert not hm.hermes_home.exists()


def test_hermes_gpu_budgets_and_optimized_game_mode() -> None:
    from types import SimpleNamespace
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HERMES_GPU_MODES, HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    assert set(HERMES_GPU_MODES.keys()) == {"game_adaptive", "100", "75", "50", "25"}

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=td)
        cfg = AppConfig()
        assert cfg.hermes_gpu_limit_mode == "game_adaptive"
        assert cfg.hermes_vram_reserve_mb == 1024

        journal = ForensicJournal(cm)
        hm = HermesAgentManager(cm, journal)
        hm.ensure_hermes_home(cfg)

        # 1. Fixed GPU percentage budgets (100%, 75%, 50%, 25%)
        idle_gpu = SimpleNamespace(available=True, name="RTX Test", utilization_percent=5.0, vram_used_mb=500.0, vram_total_mb=8192.0)
        for mode, expected_pct in (("100", 100), ("75", 75), ("50", 50), ("25", 25)):
            cfg.hermes_gpu_limit_mode = mode
            p = hm.compute_gpu_throttle_plan(cfg, gpu=idle_gpu, processes=[])
            assert p["target_gpu_pct"] == expected_pct
            if expected_pct == 100:
                assert p["num_gpu_layers"] is None
            else:
                assert p["num_gpu_layers"] is not None and p["num_gpu_layers"] < 36

        # 2. Optimized Game Mode: never uses 100% GPU even when idle, learns fullscreen games, and preserves ~1 GB VRAM headroom
        cfg.hermes_gpu_limit_mode = "game_adaptive"
        idle_plan = hm.compute_gpu_throttle_plan(cfg, gpu=idle_gpu, processes=[])
        assert idle_plan["target_gpu_pct"] < 100
        assert idle_plan["vram_reserve_mb"] == 1024

        # Learn a custom game while running fullscreen with heavy GPU/VRAM usage
        game_gpu = SimpleNamespace(available=True, name="RTX Test", utilization_percent=88.0, vram_used_mb=6800.0, vram_total_mb=8192.0)
        learned = hm.observe_running_workload(
            [{"name": "eldenring.exe"}],
            focus_process="eldenring.exe",
            is_fullscreen=True,
            gpu=game_gpu,
        )
        assert learned is not None
        assert learned["key"] == "eldenring"
        assert learned["peak_vram_mb"] == 6800.0
        assert len(hm.list_learned_games()) == 1

        # With 6800/8192 MB used, headroom above 1024 MB reserve is only 368 MB -> forces CPU/RAM offload + cron deferral
        tight_plan = hm.compute_gpu_throttle_plan(cfg, gpu=game_gpu, processes=[{"name": "eldenring.exe"}], focus_process="eldenring.exe", is_fullscreen=True)
        assert tight_plan["active_game"] == "eldenring.exe"
        assert tight_plan["vram_headroom_mb"] == 368.0
        assert tight_plan["num_gpu_layers"] == 0
        assert tight_plan["defer_background_cron"] is True

        # With roomy VRAM (3200/8192 MB used -> 3968 MB above 1 GB reserve), still throttles GPU <= 35% while game runs
        roomy_game_gpu = SimpleNamespace(available=True, name="RTX Test", utilization_percent=52.0, vram_used_mb=3200.0, vram_total_mb=8192.0)
        roomy_plan = hm.compute_gpu_throttle_plan(cfg, gpu=roomy_game_gpu, processes=[{"name": "eldenring.exe"}])
        assert roomy_plan["target_gpu_pct"] <= 35
        assert roomy_plan["num_gpu_layers"] is not None and roomy_plan["num_gpu_layers"] > 0

        # config.yaml persists GPU budget & reserve
        yaml_txt = hm.write_config_yaml(cfg).read_text(encoding="utf-8")
        assert 'limit_mode: "game_adaptive"' in yaml_txt
        assert "vram_reserve_mb: 1024" in yaml_txt


def test_hermes_auto_integrates_already_downloaded_gpu_model(monkeypatch) -> None:
    from types import SimpleNamespace
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        # Simulate Ollama manifest already downloaded on disk (even while Ollama daemon is stopped)
        fake_manifests = Path(td) / "ollama_models" / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5"
        fake_manifests.mkdir(parents=True, exist_ok=True)
        (fake_manifests / "9b").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("OLLAMA_MODELS", str(Path(td) / "ollama_models"))

        disk_models = HermesAgentManager.scan_ollama_disk_manifests()
        assert "qwen3.5:9b" in disk_models

        cm = ConfigManager(base_dir=Path(td) / "astra")
        cfg = AppConfig()  # Default unconfigured state (free_cloud, hermes_enabled=False, no API key)
        journal = ForensicJournal(cm)
        hm = HermesAgentManager(cm, journal)

        # Mock hardware recommender to pick qwen3.5:9b for 8GB GPU
        fake_rec = SimpleNamespace(
            gpu_name="NVIDIA RTX 4060",
            gpu_vram_gb=8.0,
            system_ram_gb=16.0,
            is_apple_silicon=False,
            tier_name="Mainstream GPU (8GB VRAM)",
            recommended_local_model="qwen3.5:9b",
            local_alternatives=["qwen3.5:9b", "qwen3:8b"],
            recommended_context_tokens=32768,
            local_reason="8GB VRAM",
            local_viable=True,
            optimal_free_provider="openrouter_free",
            optimal_free_model="qwen/qwen3-coder:free",
            free_reason="Free",
        )
        monkeypatch.setattr("astracradle.core.hermes_agent.recommend_hermes_models", lambda: fake_rec)
        monkeypatch.setattr(HermesAgentManager, "list_ollama_models", staticmethod(lambda *_a, **_kw: (False, [])))
        monkeypatch.setattr(HermesAgentManager, "find_ollama_bin", staticmethod(lambda: "/usr/bin/ollama"))
        monkeypatch.setattr(hm, "ensure_ollama_running", lambda: (True, "Started local Ollama"))

        # 1. Auto-integration switches unconfigured Hermes directly to the already-downloaded GPU model
        info = hm.auto_integrate_downloaded_gpu_model(cfg, start_runtime=True)
        assert info["integrated"] is True
        assert info["model"] == "qwen3.5:9b"
        assert cfg.hermes_enabled is True
        assert cfg.hermes_local_only is True
        assert cfg.hermes_mode == "local_gpu"
        assert cfg.hermes_provider == "ollama"
        assert cfg.hermes_model == "qwen3.5:9b"
        assert hm.config_yaml_path.exists()

        # 2. Running setup again must NOT invoke `ollama pull` / re-download
        pull_called = {"called": False}
        orig_popen = subprocess.Popen

        def _fail_if_pull(cmd, *a, **kw):
            if isinstance(cmd, (list, tuple)) and "pull" in cmd:
                pull_called["called"] = True
                raise AssertionError("Should never re-download an already-downloaded model!")
            return orig_popen(cmd, *a, **kw)

        monkeypatch.setattr("astracradle.core.hermes_agent.subprocess.Popen", _fail_if_pull)
        ok_s, msg_s, st_s = hm.install_and_setup_hermes(cfg, install_upstream_pkg=False, pull_local_model=True)
        assert ok_s is True
        assert pull_called["called"] is False
        assert "skipped re-download" in msg_s.lower() or "already downloaded" in msg_s.lower()
        assert st_s.active_model == "qwen3.5:9b"

        # 3. If user explicitly configured a working cloud provider with an API key, respect it
        cfg_cloud = AppConfig(hermes_mode="free_cloud", hermes_provider="openrouter_free", hermes_local_only=False)
        hm.save_env_vars({"OPENROUTER_API_KEY": "sk-or-test-key"})
        info_cloud = hm.auto_integrate_downloaded_gpu_model(cfg_cloud, start_runtime=False)
        assert info_cloud["integrated"] is False
        assert cfg_cloud.hermes_provider == "openrouter_free"


def test_hermes_whole_pc_learning_and_integrated_fix_tools() -> None:
    from types import SimpleNamespace
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.mobile_continuity import DeviceRegistryManager
    from astracradle.core.probalance import ProBalanceEngine
    from astracradle.core.rollback import RollbackManager
    from astracradle.core.self_heal import SelfHealEngine

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=td)
        cfg = AppConfig(hermes_permission_level="safe_user")
        journal = ForensicJournal(cm)
        rb = RollbackManager(cm)
        sh = SelfHealEngine(cm, rb, journal)
        pb = ProBalanceEngine(cm, journal)
        reg = DeviceRegistryManager(cm, journal)
        reg.register_device(
            "My iPhone 16 Pro Max",
            device_type="iPhone / iPad",
            os_family="iOS",
            network_host="100.77.1.2",
            program_command="astrabeam",
            auto_connect=False,
        )

        hm = HermesAgentManager(
            cm,
            journal,
            rollback_mgr=rb,
            self_heal=sh,
            probalance=pb,
            device_registry=reg,
        )
        hm.ensure_hermes_home(cfg)

        # 1. Whole-PC & remote usage auto-learning
        gpu = SimpleNamespace(available=True, name="RTX", utilization_percent=30.0, vram_used_mb=2100.0, vram_total_mb=8192.0)
        hm.observe_running_workload(
            [{"name": "code"}, {"name": "firefox"}, {"name": "sunshine"}, {"name": "tailscaled"}],
            focus_process="code",
            is_fullscreen=False,
            gpu=gpu,
            cpu_percent=42.5,
            ram_percent=58.0,
        )
        hm.record_remote_task("My iPhone 16 Pro Max", "astrabeam_stream", True, "Connected over Tailscale")

        summ = hm.execute_tool("astra_pc_usage_summary", {}, cfg)
        assert summ["ok"] is True
        assert summ["samples_recorded"] >= 1
        assert summ["peak_cpu_pct"] == 42.5
        assert "coding_dev" in summ["workflow_categories"]
        assert "sunshine" in summ["remote_services_seen"]
        assert len(summ["paired_devices"]) == 1
        assert len(summ["recent_remote_tasks"]) == 1

        sys_prompt = hm._build_system_prompt(cfg)
        assert "Learned Whole-PC & Remote Usage Context" in sys_prompt
        assert "Action & Self-Development Assistant Guidelines" in sys_prompt

        # 2. Integrated safe PC-fixing tools
        prof_res = hm.execute_tool(
            "astra_probalance_and_profile",
            {"action": "set_profile", "profile_name": "Gaming / Low-Latency"},
            cfg,
        )
        assert prof_res["ok"] is True
        assert cfg.active_profile == "Gaming / Low-Latency"

        rem_res = hm.execute_tool("astra_remote_tasks", {"action": "status"}, cfg)
        assert rem_res["ok"] is True
        assert len(rem_res["paired_devices"]) == 1

        heal_res = hm.execute_tool("astra_self_heal_repair", {}, cfg)
        assert heal_res["ok"] is True
        assert heal_res["checks_count"] >= 7

        undo_res = hm.execute_tool("astra_rollback_undo", {"action": "list"}, cfg)
        assert undo_res["ok"] is True

        startup_res = hm.execute_tool("astra_startup_audit", {"action": "list"}, cfg)
        assert startup_res["ok"] is True

        # Read-only mode blocks mutating actions cleanly
        cfg.hermes_permission_level = "read_only"
        blocked = hm.execute_tool("astra_probalance_and_profile", {"action": "restrain_hogs"}, cfg)
        assert blocked["ok"] is False and blocked.get("blocked") is True


def test_app_init_no_unbound_elevation_and_hermes_self_dev_tools(monkeypatch) -> None:
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.ui.app import AstraCradleApp

    # 1. Guard against UnboundLocalError in AstraCradleApp.__init__ and TclError pack-before in hermes_page.py
    assert "ElevationRunner" not in AstraCradleApp.__init__.__code__.co_varnames
    hp_txt = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "pages" / "hermes_page.py").read_text(encoding="utf-8")
    assert "before=key_ent" not in hp_txt

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        cfg = AppConfig(hermes_permission_level="standard_user", hermes_allow_web_search=True)
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        hm.ensure_hermes_home(cfg)

        # 2. Create, list, grep, read (line-numbered), and surgically edit with automatic backup
        proj = Path(td) / "local_build"
        proj.mkdir(parents=True, exist_ok=True)
        target = proj / "sample_feature.py"

        w_res = hm.execute_tool("file_write", {"path": str(target), "content": "def greet():\n    return 'old'\n"}, cfg)
        assert w_res["ok"] is True

        ls_res = hm.execute_tool("list_dir", {"path": str(proj), "pattern": "*.py"}, cfg)
        assert ls_res["ok"] is True and any(e["name"] == "sample_feature.py" for e in ls_res["entries"])

        gr_res = hm.execute_tool("grep_search", {"pattern": "def greet", "path": str(proj)}, cfg)
        assert gr_res["ok"] is True and len(gr_res["matches"]) == 1

        ed_res = hm.execute_tool(
            "file_edit",
            {"path": str(target), "old_text": "return 'old'", "new_text": "return 'updated'"},
            cfg,
        )
        assert ed_res["ok"] is True and Path(ed_res["backup"]).exists()

        rd_res = hm.execute_tool("file_read", {"path": str(target), "offset": 1, "limit": 10}, cfg)
        assert rd_res["ok"] is True and "return 'updated'" in rd_res["content"]

        # 3. Web search, fetch, and URL skill install (mocked HTTP)
        monkeypatch.setattr(
            hm,
            "_web_search_impl",
            lambda q, max_results=6: [{"title": "CachyOS Optimization", "url": "https://wiki.cachyos.org", "snippet": f"Tips for {q}"}],
        )
        ws_res = hm.execute_tool("web_search", {"query": "cachyos python tuning"}, cfg)
        assert ws_res["ok"] is True and ws_res["results"][0]["title"] == "CachyOS Optimization"

        class _FakeResp:
            def __init__(self, body: str) -> None:
                self._b = body.encode("utf-8")

            def read(self, _n: int = -1) -> bytes:
                return self._b

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        monkeypatch.setattr(
            "astracradle.core.hermes_agent.urllib.request.urlopen",
            lambda *_a, **_kw: _FakeResp("<html><body><h1>Remote Skill</h1><p>Check thermals first.</p></body></html>"),
        )
        wf_res = hm.execute_tool("web_fetch", {"url": "https://example.com/guide"}, cfg)
        assert wf_res["ok"] is True and "Check thermals first." in wf_res["content"]

        sk_res = hm.execute_tool("skill_install_from_url", {"slug": "thermal-guide", "url": "https://example.com/SKILL.md"}, cfg)
        assert sk_res["ok"] is True and Path(sk_res["skill_path"]).exists()

        mem_res = hm.execute_tool("memory_read", {"target": "all"}, cfg)
        assert mem_res["ok"] is True and any(s["slug"] == "thermal-guide" for s in mem_res["skills"])

        # 4. Native AstraCradle local source locator
        dev_res = hm.execute_tool("astra_dev_self_build", {"action": "locate"}, cfg)
        assert dev_res["ok"] is True and (Path(dev_res["source_root"]) / "astracradle" / "__main__.py").exists()

        # 5. Respects hermes_allow_web_search=False & Level 2 file boundary
        cfg.hermes_allow_web_search = False
        ws_off = hm.execute_tool("web_search", {"query": "test"}, cfg)
        assert ws_off["ok"] is False and ws_off.get("blocked") is True
        cfg.hermes_permission_level = "safe_user"
        ed_blocked = hm.execute_tool(
            "file_edit",
            {"path": str(target), "old_text": "return 'updated'", "new_text": "return 'x'"},
            cfg,
        )
        assert ed_blocked["ok"] is False and ed_blocked.get("blocked") is True


def test_hermes_personas_standard_plus_ten_and_think_strip(monkeypatch) -> None:
    import io
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HERMES_PERSONAS, HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.ui.pages import hermes_page

    # 1. Verify Standard personality + 10 unique selectable identities (11 total)
    assert "astra_standard" in HERMES_PERSONAS
    assert len(HERMES_PERSONAS) == 11
    names = {spec["name"] for spec in HERMES_PERSONAS.values()}
    assert len(names) == 11
    for key, spec in HERMES_PERSONAS.items():
        for field_name in ("label", "name", "badge", "tagline", "traits", "mannerisms", "greeting", "prompt_directive"):
            assert str(spec.get(field_name) or "").strip(), f"{key} missing {field_name}"

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        cfg = AppConfig()
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        hm.ensure_hermes_home(cfg)

        # 2. Verify switching persona updates _build_system_prompt with traits & mannerisms
        for key in ("astra_standard", "kestrel_tactician", "sterling_butler", "halcyon_retro"):
            cfg.hermes_persona = key
            prompt = hm._build_system_prompt(cfg)
            spec = HERMES_PERSONAS[key]
            assert spec["name"] in prompt
            assert spec["traits"] in prompt
            assert spec["mannerisms"] in prompt

        # 3. Verify <think>...</think> tags from reasoning models like Qwen3.6 are stripped cleanly
        class _OllamaResp:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "<think>internal chain of thought</think>\nYes, I can see your PC clearly!",
                                    "tool_calls": [],
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        captured_timeouts: list[float] = []

        def _fake_urlopen(req, timeout=0):
            captured_timeouts.append(timeout)
            assert req.full_url.endswith("/api/chat")
            return _OllamaResp()

        monkeypatch.setattr("astracradle.core.hermes_agent.urllib.request.urlopen", _fake_urlopen)
        spec = hm.resolve_provider_spec(AppConfig(hermes_local_only=True, hermes_mode="local_gpu", hermes_provider="ollama", hermes_model="qwen3.6:35b"))
        txt, calls = hm._call_llm(spec, "", [{"role": "user", "content": "hi"}], [])
        assert txt == "Yes, I can see your PC clearly!"
        assert calls == []
        assert captured_timeouts and captured_timeouts[0] >= 240

    # 4. Verify UI page exposes 8 wrapped chips + full-screen chat workspace + persona helpers
    assert len(hermes_page._QUICK_ACTION_CHIPS) == 8
    assert callable(hermes_page._build_chat_workspace)
    assert callable(hermes_page._render_chat_bubbles)


def test_ui_theme_fonts_palette_and_hermes_page_render_headless(monkeypatch) -> None:
    import re
    import types
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager, HermesHardwareRecommendation, HermesSetupStatus
    from astracradle.core.journal import ForensicJournal
    from astracradle.theme import get_palette, get_ui_fonts
    from astracradle.ui.pages import hermes_page

    # 1. Static guard across ALL UI files for valid font keys and palette attributes
    pal = get_palette("dark")
    fonts = get_ui_fonts(1.0)
    ui_root = Path(__file__).resolve().parents[1] / "astracradle" / "ui"
    for pyf in ui_root.rglob("*.py"):
        txt = pyf.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:fonts|f)\["([a-z_]+)"\]', txt):
            assert m.group(1) in fonts, f"{pyf}: invalid font key {m.group(1)}"
        for m in re.finditer(r"\b(?:p|palette)\.(accent_[a-z_]+|badge_[a-z_]+|bg_[a-z_]+|text_[a-z_]+|border_[a-z_]+)\b", txt):
            assert hasattr(pal, m.group(1)), f"{pyf}: invalid ThemePalette attribute {m.group(1)}"

    # 2. Headless end-to-end execution of render_hermes_page (standard + fullscreen) and open_quick_assistant_modal
    class _FakeVar:
        def __init__(self, value=None) -> None:
            self._v = value

        def get(self):
            return self._v

        def set(self, v) -> None:
            self._v = v

    class _FakeWidget:
        def __init__(self, *_a, **_kw) -> None:
            self._children: list[_FakeWidget] = []
            self._parent = _a[0] if (_a and hasattr(_a[0], "_children")) else None
            self._parent_canvas = types.SimpleNamespace(yview_moveto=lambda *_x: None)
            if self._parent is not None:
                self._parent._children.append(self)

        def pack(self, *_a, **_kw) -> None:
            pass

        def grid(self, *_a, **_kw) -> None:
            pass

        def grid_columnconfigure(self, *_a, **_kw) -> None:
            pass

        def bind(self, *_a, **_kw) -> None:
            pass

        def configure(self, *_a, **_kw) -> None:
            pass

        def insert(self, *_a, **_kw) -> None:
            pass

        def delete(self, *_a, **_kw) -> None:
            pass

        def get(self, *_a, **_kw) -> str:
            return ""

        def set(self, *_a, **_kw) -> None:
            pass

        def focus_set(self) -> None:
            pass

        def focus_force(self) -> None:
            pass

        def lift(self) -> None:
            pass

        def destroy(self) -> None:
            if self._parent is not None and self in self._parent._children:
                self._parent._children.remove(self)

        def title(self, *_a) -> None:
            pass

        def geometry(self, *_a) -> None:
            pass

        def minsize(self, *_a) -> None:
            pass

        def transient(self, *_a) -> None:
            pass

        def winfo_exists(self) -> bool:
            return True

        def winfo_children(self):
            return list(self._children)

        def update_idletasks(self) -> None:
            pass

        def add(self, _name: str):
            w = _FakeWidget(self)
            return w

    fake_ctk = types.SimpleNamespace(
        CTkFrame=_FakeWidget,
        CTkScrollableFrame=_FakeWidget,
        CTkLabel=_FakeWidget,
        CTkButton=_FakeWidget,
        CTkOptionMenu=_FakeWidget,
        CTkEntry=_FakeWidget,
        CTkTextbox=_FakeWidget,
        CTkSwitch=_FakeWidget,
        CTkSlider=_FakeWidget,
        CTkProgressBar=_FakeWidget,
        CTkTabview=_FakeWidget,
        CTkToplevel=_FakeWidget,
    )
    fake_tk = types.SimpleNamespace(StringVar=_FakeVar, BooleanVar=_FakeVar)
    monkeypatch.setattr(hermes_page, "ctk", fake_ctk)
    monkeypatch.setattr(hermes_page, "tk", fake_tk)

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        cfg = AppConfig(hermes_local_only=True, hermes_mode="local_gpu", hermes_provider="ollama", hermes_model="qwen3.6:35b")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        hm.ensure_hermes_home(cfg)

        fake_app = types.SimpleNamespace(
            hermes_scroll=_FakeWidget(),
            palette=pal,
            fonts=fonts,
            config_obj=cfg,
            cfg_mgr=cm,
            hermes_mgr=hm,
            current_nav="hermes",
            _hermes_rec_cache=HermesHardwareRecommendation(
                gpu_name="card2",
                gpu_vram_gb=24.0,
                system_ram_gb=126.0,
                is_apple_silicon=False,
                tier_name="24GB+",
                recommended_local_model="qwen3.6:35b",
                local_alternatives=[],
                recommended_context_tokens=65536,
                local_reason="24 GB VRAM",
                local_viable=True,
                optimal_free_provider="openrouter_free",
                optimal_free_model="qwen/qwen3-coder:free",
                free_reason="free",
            ),
            _hermes_status_cache=HermesSetupStatus(
                hermes_home=str(hm.hermes_home),
                hermes_cli_installed=False,
                hermes_pkg_installed=False,
                ollama_installed=True,
                ollama_running=True,
                pulled_models=["qwen3.6:35b"],
                active_mode="local_gpu",
                active_provider="ollama",
                active_model="qwen3.6:35b",
                api_key_configured=True,
                ready=True,
                summary="READY (Local GPU · qwen3.6:35b)",
            ),
            _content_wrap=lambda pad=0: max(400, 1000 - pad),
            show_toast=lambda *_a: None,
            switch_nav=lambda *_a: None,
            after=lambda _ms, fn: None,
        )

        # Standard view
        hermes_page.render_hermes_page(fake_app)
        assert len(fake_app.hermes_scroll._children) >= 2

        # Full-screen chat view
        fake_app._hermes_fullscreen_chat = True
        hermes_page.render_hermes_page(fake_app)
        assert len(fake_app.hermes_scroll._children) == 2

        # Quick assistant modal
        hermes_page.open_quick_assistant_modal(fake_app)
        assert fake_app._quick_ai_win is not None


def test_hermes_ollama_llama_server_segfault_auto_recovery(monkeypatch) -> None:
    import io
    import urllib.error
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        cfg = AppConfig(hermes_local_only=True, hermes_mode="local_gpu", hermes_provider="ollama", hermes_model="qwen3.6:35b")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        hm.ensure_hermes_home(cfg)

        seen_options: list[dict[str, object]] = []
        seen_urls: list[str] = []

        class _OkResp:
            def read(self) -> bytes:
                return json.dumps(
                    {"message": {"role": "assistant", "content": "Recovered cleanly!", "tool_calls": []}}
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        def _flaky_ollama(req, timeout=0):
            seen_urls.append(req.full_url)
            body = json.loads(req.data.decode("utf-8"))
            assert body.get("stream") is False
            opts = dict(body.get("options") or {})
            seen_options.append(opts)
            if len(seen_options) <= 2:
                raise urllib.error.HTTPError(
                    req.full_url,
                    500,
                    "Internal Server Error",
                    None,
                    io.BytesIO(
                        b'{"error":{"message":"llama-server process has terminated: signal: segmentation fault (core dumped)"}}'
                    ),
                )
            return _OkResp()

        monkeypatch.setattr("astracradle.core.hermes_agent.urllib.request.urlopen", _flaky_ollama)
        monkeypatch.setattr("astracradle.core.hermes_agent.time.sleep", lambda *_a: None)

        spec = hm.resolve_provider_spec(cfg)
        spec["gpu_plan"] = {"num_ctx": 16384, "num_batch": 128, "num_gpu_layers": 13, "keep_alive": "1m"}
        txt, calls = hm._call_llm(spec, "", [{"role": "user", "content": "Can you see this?"}], [])

        assert len(seen_options) == 3
        assert all(u.endswith("/api/chat") for u in seen_urls)
        assert "num_gpu" in seen_options[0]
        assert "num_gpu" not in seen_options[1]
        assert seen_options[2].get("num_gpu") == 0
        assert "Recovered cleanly!" in txt
        assert "ROCR_VISIBLE_DEVICES=0" in txt
        assert calls == []




















