from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from astracradle import __version__
from astracradle.core.command_db import CommandDatabaseManager
from astracradle.core.config import ConfigManager
from astracradle.core.dependency_library import DependencyLibraryManager
from astracradle.core.i18n import get_help_categories, search_help_topics
from astracradle.core.journal import ForensicJournal
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.core.self_heal import SelfHealEngine
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.modals import HelpSearchDialog


def test_v2_2_linux_bash_fish_deps_autoinstall_and_offline_help(monkeypatch) -> None:
    assert __version__ == "2.5.1"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".astracradle"
        cfg_mgr = ConfigManager(base_dir=base)
        cfg = cfg_mgr.load()
        rb = RollbackManager(cfg_mgr)
        journal = ForensicJournal(cfg_mgr)
        heal = SelfHealEngine(cfg_mgr, rb, journal)
        adapter = get_os_adapter()

        # 1. Command DB includes full Linux Bash & Linux Fish categories
        cdb = CommandDatabaseManager(cfg_mgr, journal)
        cats = cdb.get_categories()
        assert "Linux Bash" in cats and "Linux Fish" in cats
        bash_cmds = cdb.list_commands(category="Linux Bash")
        fish_cmds = cdb.list_commands(category="Linux Fish")
        assert len(bash_cmds) >= 3 and len(fish_cmds) >= 3
        assert any("flatpak install" in c.script and "Sunshine" in c.script for c in bash_cmds)
        assert any("if command -q" in c.script and "; and" in c.script for c in fish_cmds)

        # 2. Dependency Library inspection & Full Bash/Fish stack scripts
        dlm = DependencyLibraryManager(journal)
        statuses = dlm.inspect_all(shell_pref="fish")
        dep_ids = {s.dep_id for s in statuses}
        assert {"tailscale", "sunshine", "moonlight", "adb", "scrcpy", "openssl", "bluetoothctl", "nmcli", "flatpak"} <= dep_ids
        assert "if command -q pacman" in dlm.full_stack_script("fish")
        assert "#!/usr/bin/env bash" in dlm.full_stack_script("bash")

        # 3. Permission Gate blocks install unless permitted=True
        denied = dlm.install_and_setup("sunshine", permitted=False, shell_kind="fish")
        assert denied.ok is False and "permission required" in denied.message.lower()
        assert "flatpak" in denied.commands_used

        # With permission granted + mocked subprocess, Sunshine installs AND auto-hardens sunshine.conf
        sun_conf = Path(td) / "sunshine.conf"
        with monkeypatch.context() as m:
            m.setattr(
                subprocess,
                "run",
                lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout="ok", stderr=""),
            )
            m.setattr(dlm, "inspect_dependency", lambda spec, shell_pref=None: type("S", (), {
                "installed": False,
                "binary_path": "",
                "recommended_cmd": spec.linux_fish_cmd,
            })())
            ok_inst = dlm.install_and_setup(
                "sunshine",
                permitted=True,
                shell_kind="fish",
                tailscale_ip="100.88.1.5",
                sunshine_conf_path=sun_conf,
            )
        assert ok_inst.ok is True
        assert "if command -q" in ok_inst.commands_used
        assert sun_conf.exists() and "upnp = disabled" in sun_conf.read_text(encoding="utf-8")

        # 4. Offline Help / Troubleshooting Knowledge Base (Keyword Search + Category Dropdown)
        h_cats = get_help_categories()
        assert "Troubleshoot: Streaming & Tailscale" in h_cats
        assert "Troubleshoot: Linux Bash & Fish" in h_cats
        assert any("Black Screen" in t.title for t in search_help_topics("sunshine wayland"))
        assert any("Fish" in t.title for t in search_help_topics("", category="Troubleshoot: Linux Bash & Fish"))

        # 5. Self-Heal includes Companion Dependency Library Guard (13 invariants total)
        events, _ = heal.run_full_self_heal(adapter, cfg, command_db=cdb)
        assert any(e.check_name == "Companion Dependency Library Guard" for e in events)

        # 6. GUI Command, Dependency & Offline Help Vault + HelpSearchDialog dropdown
        app = AstraCradleApp(base_dir=base, show_startup_popups=False)
        try:
            app.update()
            app.switch_nav("cmd_vault")
            app._on_toggle_dep_install_consent(True)
            app._on_vault_help_filter("Troubleshoot: iOS & Apple Watch", "429")
            app.update()
            assert app.config_obj.consent_auto_install_deps is True

            dlg = HelpSearchDialog(app, app.palette, app.fonts, on_navigate=app.switch_nav, initial_query="bluetooth")
            app.update()
            dlg.cat_var.set("Troubleshoot: Android & Bluetooth")
            dlg._refresh_results()
            app.update()
            dlg.destroy()
        finally:
            try:
                app.on_close(force_quit=True)
            except Exception:
                pass
