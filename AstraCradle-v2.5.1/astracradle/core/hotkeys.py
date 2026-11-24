"""Cross-OS system-wide global hotkey listener with graceful pynput + Wayland shortcut fallback."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Callable


class GlobalHotkeyManager:
    """Registers system-wide hotkeys (Ctrl+Alt+A/S/W) via pynput when available, else safe Tk-only fallback."""

    def __init__(self) -> None:
        self._listener = None
        self.active = False
        self.backend = "tk-window-only"
        self.last_error = ""

    def start(
        self,
        *,
        on_shield: Callable[[], None],
        on_fix_safe: Callable[[], None],
        on_widget: Callable[[], None],
        on_beam: Callable[[], None] | None = None,
    ) -> bool:
        self.stop()
        # Avoid hooking X11/Wayland when running without a real input server or if disabled
        try:
            from pynput import keyboard  # type: ignore

            mapping = {
                "<ctrl>+<alt>+a": on_shield,
                "<ctrl>+<alt>+s": on_fix_safe,
                "<ctrl>+<alt>+w": on_widget,
            }
            if on_beam is not None:
                mapping["<ctrl>+<shift>+b"] = on_beam
            listener = keyboard.GlobalHotKeys(mapping)
            listener.daemon = True
            listener.start()
            self._listener = listener
            self.active = True
            self.backend = "pynput-global"
            return True
        except Exception as exc:
            self.active = False
            self.backend = "tk-window-only"
            self.last_error = str(exc)
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self.active = False

    @staticmethod
    def export_wayland_gnome_shortcuts(python_exec: str = "astracradle") -> tuple[bool, str]:
        """Registers GNOME Wayland custom shortcuts via gsettings so hotkeys work outside X11."""
        if platform.system() != "Linux" or not shutil.which("gsettings"):
            return False, "gsettings GNOME Wayland shortcut export is only available on Linux GNOME."
        if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland" and "gnome" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
            return False, "GNOME/Wayland desktop not detected; using standard global/window hotkeys."
        base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
        entries = [
            ("astracradle-widget", "AstraCradle Toggle Widget", f"{python_exec} --widget", "<Control><Alt>w"),
        ]
        try:
            paths = [f"{base}/{slug}/" for slug, _, _, _ in entries]
            subprocess.run(
                ["gsettings", "set", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings", str(paths)],
                check=False,
                timeout=3,
            )
            for slug, name, cmd, binding in entries:
                schema = f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{base}/{slug}/"
                subprocess.run(["gsettings", "set", schema, "name", name], check=False, timeout=2)
                subprocess.run(["gsettings", "set", schema, "command", cmd], check=False, timeout=2)
                subprocess.run(["gsettings", "set", schema, "binding", binding], check=False, timeout=2)
            return True, "Registered GNOME Wayland shortcut (<Ctrl><Alt>W -> AstraCradle Widget)."
        except Exception as exc:
            return False, f"Could not register GNOME shortcuts: {exc}"
