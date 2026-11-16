from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import pytest


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Requires X11/Xvfb DISPLAY")
def test_native_gui_renders_all_pages_and_animated_gif(tmp_path: Path) -> None:
    from astracradle.ui.app import AstraCradleApp

    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    app = AstraCradleApp(
        base_dir=tmp_path / "ac_gui",
        assets_dir=assets_dir,
        show_startup_popups=False,
    )
    app.geometry("1300x830+0+0")
    app.update_idletasks()
    app.update()

    deadline = time.time() + 3.0
    while time.time() < deadline and app.latest_playbook is None:
        app.update()
        time.sleep(0.05)

    assert app.latest_snapshot is not None
    assert app.latest_playbook is not None
    assert hasattr(app, "sidebar_gif") and len(app.sidebar_gif._frames) > 10

    # Exercise all 7 views + Easy Guide modal
    for nav in ("playbook", "hunter", "scripts", "modules", "safeguards", "settings", "dashboard"):
        app.switch_nav(nav)
        app.update()

    app.open_easy_guide()
    app.update()

    # Capture Toggleable Scripts view
    app.switch_nav("scripts")
    app.update_idletasks()
    app.update()
    time.sleep(0.15)
    app.update()
    subprocess.run(
        ["import", "-window", "root", "-crop", "1300x830+0+0", "+repage", str(Path(__file__).resolve().parent / "gui_scripts.png")],
        timeout=5,
        check=False,
    )

    # Capture Safeguards / Forensic / Crash Post-Mortem view
    app.switch_nav("safeguards")
    app.on_simulate_self_heal()
    app.update_idletasks()
    app.update()
    time.sleep(0.15)
    app.update()
    subprocess.run(
        ["import", "-window", "root", "-crop", "1300x830+0+0", "+repage", str(Path(__file__).resolve().parent / "gui_safeguards.png")],
        timeout=5,
        check=False,
    )

    # Capture Command Deck view
    app.hide_toast()
    app.switch_nav("dashboard")
    app.update_idletasks()
    app.update()
    time.sleep(0.15)
    app.update()
    subprocess.run(
        ["import", "-window", "root", "-crop", "1300x830+0+0", "+repage", str(Path(__file__).resolve().parent / "gui_command_deck.png")],
        timeout=5,
        check=False,
    )

    app.on_close()
