"""Cross-OS Bottom-Right System Tray / Menu-Bar Icon Controller with graceful fallback."""

from __future__ import annotations

from pathlib import Path
import platform
import threading
from typing import Callable


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
    ) -> None:
        self.icon_path = self._resolve_platform_icon(icon_path)
        self.on_show = on_show
        self.on_scan = on_scan
        self.on_quit = on_quit
        self.on_fix_safe = on_fix_safe
        self.on_profile = on_profile
        self.on_toggle_probalance = on_toggle_probalance
        self.on_toggle_astrashield = on_toggle_astrashield
        self._icon = None
        self._thread: threading.Thread | None = None
        self.available = False

        try:
            import pystray  # noqa: F401
            self.available = True
        except Exception:
            self.available = False

    @staticmethod
    def _resolve_platform_icon(icon_path: Path) -> Path:
        assets_dir = icon_path.parent
        sys_name = platform.system()
        if sys_name == "Windows" and (assets_dir / "astracradle.ico").exists():
            return assets_dir / "astracradle.ico"
        if (assets_dir / "astracradle_emblem_64.png").exists():
            return assets_dir / "astracradle_emblem_64.png"
        return icon_path

    @property
    def running(self) -> bool:
        return self._icon is not None

    def start(self) -> bool:
        if not self.available or self._icon is not None:
            return False
        try:
            from PIL import Image
            import pystray

            img = Image.open(self.icon_path) if self.icon_path.exists() else Image.new("RGBA", (64, 64), (72, 216, 224, 255))
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
            items.extend(
                [
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quit AstraCradle", lambda _icon, _item: self.on_quit()),
                ]
            )
            menu = pystray.Menu(*items)
            self._icon = pystray.Icon("AstraCradle", img, "AstraCradle — OS Equilibrium Studio", menu)
            self._thread = threading.Thread(target=self._run_safe, daemon=True)
            self._thread.start()
            return True
        except Exception:
            self._icon = None
            return False

    def _run_safe(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.run()
        except Exception:
            # Headless / missing tray service fallback without crashing GUI
            self._icon = None

    def notify(self, title: str, message: str) -> bool:
        if self._icon is not None:
            try:
                self._icon.notify(message[:240], title[:64])
                return True
            except Exception:
                return False
        return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
