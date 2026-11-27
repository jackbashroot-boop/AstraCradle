"""Cross-OS Bottom-Right System Tray / Menu-Bar Icon Controller with graceful fallback."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import threading
from typing import Callable


def _has_linux_appindicator() -> bool:
    """Returns True if GI AyatanaAppIndicator3 or AppIndicator3 is importable."""
    if platform.system() != "Linux":
        return False
    try:
        import gi  # type: ignore[import-not-found]

        for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(ns, "0.1")
                __import__(f"gi.repository.{ns}")
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def prepare_xorg_tray_image(img, size: tuple[int, int] = (32, 32), bg_rgba: tuple[int, int, int, int] = (30, 36, 43, 255)):
    """Alpha-composites RGBA tray artwork onto a clean dark panel backdrop and converts to RGB.

    `pystray._xorg` calls `Image.new('RGB', ...).paste(rgba)` without an alpha mask, which leaks
    hidden alpha=0 RGB values and turns transparent emblems into a solid square on KDE/X11.
    """
    from PIL import Image

    w = max(24, int(size[0]))
    h = max(24, int(size[1]))
    rgba = img.convert("RGBA").resize((w, h), Image.LANCZOS)
    backdrop = Image.new("RGBA", (w, h), bg_rgba)
    composited = Image.alpha_composite(backdrop, rgba)
    return composited.convert("RGB")


def build_net_wm_icon_data(img, sizes: tuple[int, ...] = (32, 64)) -> list[int]:
    """Builds an X11 `_NET_WM_ICON` CARDINAL array `[width, height, ARGB...]` for RGBA tray icons."""
    from PIL import Image

    rgba_src = img.convert("RGBA")
    data: list[int] = []
    for sz in sizes:
        dim = max(16, int(sz))
        scaled = rgba_src.resize((dim, dim), Image.LANCZOS)
        data.append(dim)
        data.append(dim)
        raw = scaled.tobytes()
        for i in range(0, len(raw), 4):
            r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
            data.append(((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF))
    return data


class TrayController:
    """Native bottom-right tray / menu-bar icon integration via pystray with safe fallback."""

    def __init__(
        self,
        icon_path: Path,
        on_show: Callable[[], None],
        on_scan: Callable[[], None],
        on_quit: Callable[[], None],
        on_fix_safe: Callable[[], None] | None = None,
        on_profile: Callable[[str], None] | None = None,
        on_toggle_probalance: Callable[[], None] | None = None,
        on_toggle_astrashield: Callable[[], None] | None = None,
        on_pause_shield_5m: Callable[[], None] | None = None,
        on_toggle_widget: Callable[[], None] | None = None,
        on_context_menu: Callable[[int, int], None] | None = None,
    ) -> None:
        self.icon_path = self._resolve_platform_icon(icon_path)
        self.on_show = on_show
        self.on_scan = on_scan
        self.on_quit = on_quit
        self.on_fix_safe = on_fix_safe
        self.on_profile = on_profile
        self.on_toggle_probalance = on_toggle_probalance
        self.on_toggle_astrashield = on_toggle_astrashield
        self.on_pause_shield_5m = on_pause_shield_5m
        self.on_toggle_widget = on_toggle_widget
        self.on_context_menu = on_context_menu
        self._icon = None
        self._thread: threading.Thread | None = None
        self.available = False
        self.last_error: str = ""
        self.backend_name: str = ""

        # Prefer native AppIndicator / StatusNotifierItem on Linux KDE/GNOME when available
        if platform.system() == "Linux" and not os.environ.get("PYSTRAY_BACKEND"):
            if _has_linux_appindicator():
                os.environ["PYSTRAY_BACKEND"] = "appindicator"

        try:
            import pystray  # noqa: F401

            self.available = True
            self.backend_name = getattr(getattr(pystray, "Icon", None), "__module__", "pystray")
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)

    @staticmethod
    def _resolve_platform_icon(icon_path: Path) -> Path:
        assets_dir = icon_path.parent
        sys_name = platform.system()
        if sys_name == "Windows" and (assets_dir / "astracradle.ico").exists():
            return assets_dir / "astracradle.ico"
        if (assets_dir / "astracradle_emblem_64.png").exists():
            return assets_dir / "astracradle_emblem_64.png"
        return icon_path

    @staticmethod
    def _safe_tray_text(text: str, max_len: int = 64) -> str:
        cleaned = (
            str(text or "")
            .replace("\u2014", "-")
            .replace("\u2013", "-")
            .replace("\u00b7", "|")
            .replace("\u2726", "*")
        )
        cleaned = cleaned.encode("latin-1", errors="replace").decode("latin-1")
        return cleaned[:max_len]

    @property
    def running(self) -> bool:
        return self._icon is not None

    @property
    def is_xorg_backend(self) -> bool:
        return self.backend_name.endswith("_xorg")

    def ensure_running(self) -> bool:
        if self.running:
            return True
        return self.start()

    def update_status(self, title: str) -> None:
        if self._icon is not None:
            try:
                self._icon.title = self._safe_tray_text(title, max_len=64)
            except Exception:
                pass

    def pulse_icon_frame(self, frame_idx: int = 0) -> None:
        """Cycles animated tray icon frame safely from astracradle_hover.gif when active."""
        if self._icon is None or self.is_xorg_backend:
            # Skip live icon swaps on X11 XEmbed (`_xorg`): KDE xembedsniproxy flickers/resizes on rapid swaps
            return
        try:
            from PIL import Image, ImageSequence

            gif_path = self.icon_path.parent / "astracradle_hover.gif"
            if not gif_path.exists():
                return
            if not hasattr(self, "_gif_frames"):
                with Image.open(gif_path) as im:
                    self._gif_frames = [fr.convert("RGBA").resize((64, 64)) for fr in ImageSequence.Iterator(im)]
            if getattr(self, "_gif_frames", None):
                self._icon.icon = self._gif_frames[frame_idx % len(self._gif_frames)]
        except Exception:
            pass

    @staticmethod
    def _patch_xorg_icon(icon, rgba_img, on_context_menu: Callable[[int, int], None] | None = None) -> None:
        """Upgrades pystray._xorg window geometry (32x32 + _NET_WM_ICON), alpha-safe rendering & right-click menu."""
        from PIL import Image

        wm_icon_data = build_net_wm_icon_data(rgba_img, sizes=(32, 64))
        win = getattr(icon, "_window", None)
        disp = getattr(icon, "_display", None)
        if win is not None and disp is not None:
            try:
                import Xlib.X

                win.configure(width=32, height=32)
                win.change_attributes(
                    event_mask=(
                        Xlib.X.ExposureMask
                        | Xlib.X.StructureNotifyMask
                        | Xlib.X.ButtonPressMask
                    )
                )
                if wm_icon_data:
                    net_wm_icon = disp.intern_atom("_NET_WM_ICON")
                    cardinal = disp.intern_atom("CARDINAL")
                    win.change_property(net_wm_icon, cardinal, 32, wm_icon_data)
                disp.sync()
            except Exception:
                pass

        # Hook ButtonPress so right-click (detail == 3) opens the native Tk tray menu on X11/KDE
        handlers = getattr(icon, "_message_handlers", None)
        if isinstance(handlers, dict):
            try:
                import Xlib.X

                def _on_button_press(event) -> None:
                    detail = getattr(event, "detail", 0)
                    if detail == 1:
                        icon()
                    elif detail == 3 and on_context_menu is not None:
                        rx = int(getattr(event, "root_x", 0) or 0)
                        ry = int(getattr(event, "root_y", 0) or 0)
                        on_context_menu(rx, ry)

                handlers[Xlib.X.ButtonPress] = _on_button_press
            except Exception:
                pass

        def _patched_assert_icon_data(width, height):
            w = max(1, int(width or 32))
            h = max(1, int(height or 32))
            if getattr(icon, "_icon_data", None) is not None and icon._icon_data.size == (w, h):
                return
            rendered = prepare_xorg_tray_image(rgba_img, size=(max(24, w), max(24, h)))
            if rendered.size != (w, h):
                rendered = rendered.resize((w, h), Image.LANCZOS)
            icon._icon_data = rendered
            icon._icon_data.tostring = icon._icon_data.tobytes

        icon._assert_icon_data = _patched_assert_icon_data

    def start(self) -> bool:
        if not self.available or self._icon is not None:
            return False
        try:
            from PIL import Image
            import pystray

            rgba_img = (
                Image.open(self.icon_path).convert("RGBA")
                if self.icon_path.exists()
                else Image.new("RGBA", (64, 64), (72, 216, 224, 255))
            )
            tray_img = prepare_xorg_tray_image(rgba_img, size=(32, 32)) if self.is_xorg_backend else rgba_img

            items = [
                pystray.MenuItem("Open AstraCradle", lambda _icon, _item: self.on_show(), default=True),
                pystray.MenuItem("Run Equilibrium Scan", lambda _icon, _item: self.on_scan()),
            ]
            if self.on_fix_safe is not None:
                cb_fix = self.on_fix_safe
                items.append(pystray.MenuItem("1-Click Fix Everything Safe", lambda _icon, _item: cb_fix()))
            if self.on_profile is not None:
                cb_prof = self.on_profile
                items.append(
                    pystray.MenuItem(
                        "Switch Profile",
                        pystray.Menu(
                            pystray.MenuItem("Balanced Daily", lambda _i, _m: cb_prof("Balanced Daily")),
                            pystray.MenuItem("Gaming / Low-Latency", lambda _i, _m: cb_prof("Gaming / Low-Latency")),
                            pystray.MenuItem("Battery Endurance", lambda _i, _m: cb_prof("Battery Endurance")),
                        ),
                    )
                )
            if self.on_toggle_probalance is not None:
                cb_pb = self.on_toggle_probalance
                items.append(pystray.MenuItem("Toggle ProBalance", lambda _icon, _item: cb_pb()))
            if self.on_toggle_astrashield is not None:
                cb_sh = self.on_toggle_astrashield
                items.append(pystray.MenuItem("Toggle AstraShield (Ad/Tracker Blocker)", lambda _icon, _item: cb_sh()))
            if self.on_pause_shield_5m is not None:
                cb_ps = self.on_pause_shield_5m
                items.append(pystray.MenuItem("AstraShield: Pause 5 min", lambda _icon, _item: cb_ps()))
            if self.on_toggle_widget is not None:
                cb_wg = self.on_toggle_widget
                items.append(pystray.MenuItem("Toggle Desktop Widget", lambda _icon, _item: cb_wg()))
            items.extend(
                [
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quit AstraCradle", lambda _icon, _item: self.on_quit()),
                ]
            )
            menu = pystray.Menu(*items)
            safe_title = self._safe_tray_text("AstraCradle - OS Equilibrium Studio", max_len=64)
            self._icon = pystray.Icon("AstraCradle", tray_img, safe_title, menu)
            if self.is_xorg_backend:
                self._patch_xorg_icon(self._icon, rgba_img, on_context_menu=self.on_context_menu)
            self._thread = threading.Thread(target=self._run_safe, daemon=True)
            self._thread.start()
            return True
        except Exception as exc:
            self._icon = None
            self.last_error = str(exc)
            return False

    def _run_safe(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            def _setup(ic) -> None:
                try:
                    ic.visible = True
                except Exception:
                    pass

            icon.run(setup=_setup)
        except Exception as exc:
            # Headless / missing tray service fallback without crashing GUI
            self.last_error = str(exc)
            self._icon = None

    def notify(self, title: str, message: str) -> bool:
        icon = self._icon
        if icon is not None:
            safe_msg = self._safe_tray_text(message, max_len=240)
            safe_title = self._safe_tray_text(title, max_len=64)

            def _send() -> None:
                try:
                    icon.notify(safe_msg, safe_title)
                except Exception:
                    pass

            threading.Thread(target=_send, daemon=True).start()
            return True
        return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
