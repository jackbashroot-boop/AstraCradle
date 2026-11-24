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
        assert len(frames) == 24
        assert frames[0].getpixel((0, 0))[3] == 0

    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=td, show_startup_popups=False)
        try:
            app.update()
            assert len(app._hover_icon_frames) == 24
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
        finally:
            app.destroy()

