from __future__ import annotations

import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from astracradle import __version__
from astracradle.core.bloat_analyzer import BloatAnalyzer
from astracradle.core.config import ConfigManager
from astracradle.core.crash_analyzer import CrashAnalyzer
from astracradle.core.crypto import derive_public_key, sign_ed25519, verify_ed25519
from astracradle.core.diagnostics import DiagnosticReport
from astracradle.core.executor import GuardedExecutor
from astracradle.core.i18n import Translator, search_help_topics
from astracradle.core.journal import ForensicJournal
from astracradle.core.models import CacheTarget, PartitionInfo, ProcessInfo, ProcessRecommendation, SystemSnapshot
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.rollback import RollbackManager
from astracradle.core.scheduler import BackgroundSchedulerManager
from astracradle.core.scripts_engine import ScriptsEngine
from astracradle.core.updater import ModuleAndUpdateManager
from astracradle.theme import get_palette, get_ui_fonts
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.modals import HelpSearchDialog
from astracradle.ui.tray import TrayController
from astracradle.ui.widgets import HoverTooltip


def test_ed25519_rfc8032_vector_and_signed_update(tmp_path: Path) -> None:
    # RFC 8032 Section 7.1 Test Vector 1 (empty message)
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk_expected = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig_expected = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert derive_public_key(sk) == pk_expected
    assert sign_ed25519(sk, b"") == sig_expected
    assert verify_ed25519(pk_expected, b"", sig_expected) is True

    # Tampered signature or message fails
    bad_sig = bytearray(sig_expected)
    bad_sig[0] ^= 0x01
    assert verify_ed25519(pk_expected, b"", bytes(bad_sig)) is False
    assert verify_ed25519(pk_expected, b"tampered", sig_expected) is False

    # Signed update workflow + drop-in .py plugin AST discovery
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    upd = ModuleAndUpdateManager(mgr, journal)

    # Drop-in valid + broken .py plugins
    (mgr.plugins_dir / "good_pack.py").write_text(
        'PLUGIN_META = {"module_id": "py_good", "name": "GPU Telemetry Pack", "version": "1.2.0", "kind": "diagnostic", "description": "Extra GPU sensors"}\n'
        'raise RuntimeError("Should NOT execute during AST discovery!")\n',
        encoding="utf-8",
    )
    (mgr.plugins_dir / "broken_pack.py").write_text("def bad_syntax(:\n", encoding="utf-8")
    mods = upd.list_modules()
    assert any(m.module_id == "py_good" and m.name == "GPU Telemetry Pack" for m in mods)

    # Valid Ed25519 signed update
    pkg = mgr.updates_dir / "patch_2_3.zip"
    pkg_bytes = b"PK\x03\x04ASTRACRADLE_SIGNED_PATCH"
    pkg.write_bytes(pkg_bytes)
    import hashlib

    digest = hashlib.sha256(pkg_bytes).hexdigest()
    sig = sign_ed25519(sk, pkg_bytes)
    manifest = {
        "version": "2.5.1",
        "package_file": pkg.name,
        "sha256": digest,
        "public_key_hex": pk_expected.hex(),
        "signature_hex": sig.hex(),
    }
    (mgr.updates_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ok, msg = upd.stage_and_apply_local_update(cfg)
    assert ok is True and "Ed25519" in msg

    # Forged signature must abort update
    manifest["signature_hex"] = bytes(bad_sig).hex()
    (mgr.updates_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ok2, msg2 = upd.stage_and_apply_local_update(cfg)
    assert ok2 is False and "signature" in msg2.lower()


def test_bloat_duplicate_finder_quarantine_and_rollback(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    exe = GuardedExecutor(mgr, rb, journal)
    adapter = get_os_adapter(custom_protected_paths=[str(tmp_path / "protected_vault")])

    scan_dir = tmp_path / "downloads"
    scan_dir.mkdir()
    payload_a = b"A" * 4096 + b"UNIQUE_ONE"
    payload_b = b"B" * 4096 + b"UNIQUE_TWO"
    (scan_dir / "orig.bin").write_bytes(payload_a)
    (scan_dir / "orig_copy1.bin").write_bytes(payload_a)
    (scan_dir / "orig_copy2.bin").write_bytes(payload_a)
    (scan_dir / "other.bin").write_bytes(payload_b)
    (scan_dir / "symlink_trap.bin").symlink_to(scan_dir / "orig.bin")

    ba = BloatAnalyzer()
    groups = ba.find_duplicates([scan_dir], adapter, min_bytes=1024)
    assert len(groups) == 1
    assert len(groups[0].duplicate_paths) == 2
    assert groups[0].reclaimable_bytes == len(payload_a) * 2

    # Quarantine duplicate extras cleanly
    res = exe.execute_action(
        "clean_duplicates",
        adapter,
        cfg,
        params={"paths": groups[0].duplicate_paths},
        dry_run=False,
    )
    assert res.ok is True and res.files_affected == 2
    assert Path(groups[0].keep_path).exists()
    assert not any(Path(dp).exists() for dp in groups[0].duplicate_paths)

    # Protected paths refused by clean_duplicates
    prot_dir = tmp_path / "protected_vault"
    prot_dir.mkdir()
    prot_file = prot_dir / "precious.bin"
    prot_file.write_bytes(payload_a)
    res_prot = exe.execute_action("clean_duplicates", adapter, cfg, params={"paths": [str(prot_file)]}, dry_run=False)
    assert prot_file.exists() and res_prot.files_affected == 0

    # Rollback restores duplicates
    assert res.entry_id is not None
    ok_rb, _ = rb.rollback_entry(res.entry_id)
    assert ok_rb is True
    assert all(Path(dp).exists() for dp in groups[0].duplicate_paths)

    # Installed apps + folder bloat never crash
    assert isinstance(ba.scan_installed_apps(cfg, limit=5), list)
    assert isinstance(ba.analyze_folder_bloat(tmp_path, top_n=4), list)


def test_scheduler_alerts_tweak_snapshots_elevation_and_crash_codes(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    cfg = mgr.load()
    journal = ForensicJournal(mgr)
    rb = RollbackManager(mgr)
    exe = GuardedExecutor(mgr, rb, journal)
    se = ScriptsEngine(mgr, exe, journal)
    adapter = get_os_adapter()

    # Tweak snapshot created on script enable and restored on disable
    ok, _ = se.toggle_script("script_gaming_max", True, adapter, cfg, [], dry_run=False)
    assert ok is True
    snap_path = mgr.snapshots_dir / "tweak_script_gaming_max.json"
    assert snap_path.exists()
    ok_off, _ = se.toggle_script("script_gaming_max", False, adapter, cfg, [], dry_run=False)
    assert ok_off is True
    assert not snap_path.exists()

    # Elevation refuses without confirm=True
    elev_ok, elev_msg = adapter.run_elevated_command(["echo", "hi"], confirm=False)
    assert elev_ok is False and "confirmation" in elev_msg.lower()

    # Scheduler payloads + threshold alerts
    sched = BackgroundSchedulerManager(mgr, journal)
    payloads = sched.render_schedule_payload(120)
    assert "OnUnitActiveSec=120min" in payloads["linux_timer"]
    assert "StartInterval" in payloads["macos_plist"]
    assert "schtasks /Create" in payloads["windows_cmd"]

    snap = SystemSnapshot(
        timestamp="2026-11-16T00:00:00+00:00",
        os_family="Linux",
        os_release="6.1",
        hostname="test-host",
        architecture="x86_64",
        uptime_seconds=1200,
        is_elevated=False,
        cpu_percent=15.0,
        cpu_per_core=[15.0],
        cpu_count_logical=4,
        cpu_freq_mhz=3200.0,
        load_avg=(0.4, 0.3, 0.2),
        mem_total_bytes=16 * 1024**3,
        mem_used_bytes=15 * 1024**3,
        mem_available_bytes=1 * 1024**3,
        mem_percent=93.0,
        swap_total_bytes=2 * 1024**3,
        swap_used_bytes=0,
        swap_percent=0.0,
        partitions=[PartitionInfo("/dev/sda1", "/", "ext4", 100 * 1024**3, 96 * 1024**3, 4 * 1024**3, 96.0)],
        net_bytes_sent=100,
        net_bytes_recv=100,
        dns_latency_ms=10.0,
        battery_percent=None,
        battery_plugged=None,
        temperature_c=55.0,
        process_count=1,
        top_processes=[ProcessInfo(999, "ollama", "user", 0.1, 2048.0, 12.0, "sleeping")],
    )
    rep = DiagnosticReport(
        snapshot=snap,
        health_score=70,
        cache_targets=[
            CacheTarget("c1", "Temp", "Temp", "/tmp", 700 * 1024 * 1024, 12, "zero", "Temp", True, True)
        ],
        startup_items=[],
        recommendations=[
            ProcessRecommendation("ai-1", "ai_chatbot", "ollama", 999, 2048.0, 0.1, "high", "idle", "terminate", "ollama")
        ],
        findings=[],
        power_info={"governor": "unknown", "mode": "balanced"},
    )
    alerts = sched.evaluate_threshold_alerts(snap, rep, cfg)
    alert_ids = {a.alert_id for a in alerts}
    assert "disk_/" in alert_ids and "reclaimable_spike" in alert_ids and "ram_ai_pressure" in alert_ids

    # Crash signature translation
    notes = CrashAnalyzer.translate_os_crash_signatures(
        "Event ID: 41 Kernel-Power\nOut of memory: Killed process 4211 (ollama)\nGPU HANG on ring 0"
    )
    assert len(notes) >= 3


def test_i18n_high_contrast_help_search_packaging_and_gui_tooltips(tmp_path: Path) -> None:
    assert __version__ == "2.5.1"
    # High Contrast theme + scaled fonts
    hc = get_palette("High Contrast")
    assert hc.bg_canvas == "#000000" and hc.text_primary == "#FFFFFF"
    fonts = get_ui_fonts(1.25)
    assert fonts["body"][1] > get_ui_fonts(1.0)["body"][1]

    # Localization
    tr = Translator("es")
    assert tr.t("nav.dashboard") == "Panel Central"
    tr.set_language("fr")
    assert tr.t("btn.help_search") == "Aide / Chercher"
    tr.set_language("zz_invalid")
    assert tr.t("btn.help_search") == "Help / Search"

    # Help search queries + randomized fuzzing
    assert any(t.topic_id == "crash_post_mortem" for t in search_help_topics("event 41"))
    assert any(t.topic_id == "duplicates_bloat" for t in search_help_topics("sha256 duplicate"))
    rng = random.Random(42)
    for _ in range(25):
        garbage = "".join(rng.choices("abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()\n\t\x00 ", k=24))
        assert isinstance(search_help_topics(garbage), list)

    # PyInstaller packaging dry-run check
    pkg_script = Path(__file__).resolve().parents[1] / "packaging" / "build_standalone.py"
    proc = subprocess.run([sys.executable, str(pkg_script), "--dry-run"], capture_output=True, text=True)
    assert proc.returncode == 0 and "[DRY RUN]" in proc.stdout

    # Tray graceful init
    tray = TrayController(Path("nonexistent.png"), lambda: None, lambda: None, lambda: None)
    tray.notify("Test", "Hello")
    tray.stop()

    # GUI smoke: HelpSearchDialog + HoverTooltip + Bloat tab under Xvfb
    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    app = AstraCradleApp(base_dir=tmp_path / ".astracradle_gui", assets_dir=assets_dir, show_startup_popups=False)
    app.update()
    app.switch_nav("modules")
    app._on_module_tab("Duplicates & Bloat")
    app.update()

    tip = HoverTooltip(app.help_btn, "Tooltip test", app.palette, delay_ms=10)
    tip.show()
    app.update()
    tip.hide()

    dlg = HelpSearchDialog(app, app.palette, app.fonts, on_navigate=app.switch_nav, initial_query="quarantine")
    app.update()
    assert dlg._selected_topic is not None
    dlg.destroy()
    app.on_close()
