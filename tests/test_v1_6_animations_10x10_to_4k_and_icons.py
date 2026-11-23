from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from astracradle import __version__
from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.lifecycle import LifecycleManager
from astracradle.core.rollback import RollbackManager
from astracradle.theme import get_palette, get_ui_fonts
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.modals import (
    DysonStarGatherSplash,
    EasyGuideDialog,
    FloatingMiniOrbWindow,
    HelpSearchDialog,
    YesNoActionDialog,
)


def test_v1_6_boot_splash_hover_gif_icons_drag_inertia_and_10x10_to_4k(monkeypatch) -> None:
    assert __version__ == "2.5.0"
    with tempfile.TemporaryDirectory() as td:
        fake_home = Path(td) / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        cfg_mgr = ConfigManager(base_dir=fake_home / ".astracradle")
        cfg = cfg_mgr.load()
        assert cfg.boot_star_gather_splash is True
        assert cfg.floating_orb_on_minimize is True
        assert cfg.hover_icon_animation is True
        assert cfg.solar_backdrop_enabled is True

        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        lc = LifecycleManager(cfg_mgr, rb, journal)
        repo_root = Path("/home/user/astracradle")

        # 1. Verify updated desktop icons + animated hover GIF exist and install cleanly
        assets_dir = repo_root / "astracradle" / "assets"
        hover_gif = assets_dir / "astracradle_hover.gif"
        assert hover_gif.exists()
        with Image.open(hover_gif) as gim:
            assert getattr(gim, "n_frames", 1) >= 12

        created = lc.install_shortcuts(repo_root, os_override="Linux")
        assert any(p.name == "astracradle.desktop" for p in created)
        assert (fake_home / ".local" / "share" / "pixmaps" / "astracradle-hover.gif").exists()
        assert (fake_home / ".local" / "share" / "icons" / "astracradle-hover.gif").exists()

        # 2. Launch Studio + test Option A Boot Splash, Floating Mini Orb, Hover Icon Anim, Window Drag Inertia, and 10x10 -> 4K sweep
        app = AstraCradleApp(base_dir=cfg_mgr.base_dir, show_startup_popups=False)
        try:
            app.update()
            assert hasattr(app, "solar_backdrop")
            assert len(app._hover_icon_frames) >= 12

            # Option A Dyson Star-Gather Boot Splash
            done_flag = {"done": False}
            splash = DysonStarGatherSplash(
                app,
                get_palette("Obsidian Horology"),
                get_ui_fonts(1.0),
                duration_ms=200,
                on_done=lambda: done_flag.__setitem__("done", True),
            )
            splash._on_motion(SimpleNamespace(x=210, y=125))
            splash._tick()
            splash.update()
            splash.finish()
            assert done_flag["done"] is True

            # Live hover icon GIF cycling + tray pulse
            app._start_hover_icon_anim()
            app._step_hover_icon_anim()
            app._stop_hover_icon_anim()

            # Floating Minimized / Desktop Hover Orb
            restored = {"hit": False}
            orb = FloatingMiniOrbWindow(
                app,
                get_palette("Obsidian Horology"),
                on_restore=lambda: restored.__setitem__("hit", True),
                gif_path=hover_gif,
            )
            orb.set_score(96)
            orb._hover = True
            orb._tick()
            orb._on_press(SimpleNamespace(x=20, y=20))
            orb._on_release(None)
            assert restored["hit"] is True
            orb.destroy()

            # Minimized pause / resume saves CPU
            app._set_animations_paused(True)
            assert app.gauge._paused is True
            assert app.solar_backdrop._paused is True
            app._set_animations_paused(False)
            assert app.gauge._paused is False
            assert app.solar_backdrop._paused is False

            # Window move/drag fluid inertia + solar backdrop parallax
            app._on_root_configure(SimpleNamespace(widget=app, x=100, y=100, width=1280, height=800))
            app._on_root_configure(SimpleNamespace(widget=app, x=155, y=132, width=1280, height=800))
            app.solar_backdrop.draw()

            # Extreme Window Size Sweep: 10x10 up to 4K (3840x2160) across categories
            sizes = [
                (10, 10),
                (48, 48),
                (180, 140),
                (320, 240),
                (640, 480),
                (1024, 720),
                (1920, 1080),
                (2560, 1440),
                (3840, 2160),
            ]
            for idx, (w, h) in enumerate(sizes):
                app.geometry(f"{w}x{h}+0+0")
                app._apply_responsive_layout(w)
                cat = ("dashboard", "rf_waves", "wifi", "scanner", "hardware", "modules", "settings", "playbook", "dashboard")[idx]
                app.switch_nav(cat)
                app.update()
                if w < 340:
                    assert not app.sidebar.winfo_ismapped()
                else:
                    assert app.sidebar.winfo_ismapped()

            # Verify all core popup windows render cleanly & close without error
            for dlg in (
                YesNoActionDialog(app, app.palette, app.fonts, "Check", "Proceed?"),
                EasyGuideDialog(app, app.palette, app.fonts),
                HelpSearchDialog(app, app.palette, app.fonts, app.i18n),
            ):
                dlg.update()
                dlg.destroy()

            # Settings toggles for new animation options persist
            app.switch_nav("settings")
            app.set_boot_splash_var.set(False)
            app.set_floating_orb_var.set(False)
            app.set_hover_icon_var.set(False)
            app.set_solar_backdrop_var.set(False)
            app.on_save_settings()
            app.update()
            saved = cfg_mgr.load()
            assert saved.boot_star_gather_splash is False
            assert saved.floating_orb_on_minimize is False
            assert saved.hover_icon_animation is False
            assert saved.solar_backdrop_enabled is False
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
