"""Comprehensive v2.4.0 unit, integration, UI, packaging, and chaos test suite."""

from __future__ import annotations

import json
import os
import importlib.util
from pathlib import Path
import time

_rp_path = Path(__file__).resolve().parents[1] / "packaging" / "release_pipeline.py"
_rp_spec = importlib.util.spec_from_file_location("release_pipeline", _rp_path)
assert _rp_spec and _rp_spec.loader
_rp_mod = importlib.util.module_from_spec(_rp_spec)
_rp_spec.loader.exec_module(_rp_mod)
generate_all = _rp_mod.generate_all

from astracradle.core.config import ConfigManager
from astracradle.core.executor import GuardedExecutor
from astracradle.core.hardware_library import HardwareLibraryManager, PriorityPreset
from astracradle.core.journal import ForensicJournal
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.pipelines import (
    OptimizationPipeline,
    PipelineEngine,
    PipelineStage,
    ScriptAndPresetPackManager,
)
from astracradle.core.rollback import RollbackManager
from astracradle.core.scripts_engine import ScriptsEngine
from astracradle.core.telemetry import TelemetryCollector
from astracradle.core.telemetry_history import (
    DriverUpdateCorrelator,
    GlitchTroubleshooter,
    GpuTelemetryCollector,
    OutdatedAppsChecker,
    PreFlightSnapshotHook,
    TelemetryHistoryDB,
)


def test_component_library_and_savable_presets(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path)
    cfg = cfg_mgr.load()
    journal = ForensicJournal(cfg_mgr)
    hw = HardwareLibraryManager(cfg_mgr, journal)

    lib = hw.load_component_library()
    assert len(lib.get("cpus", [])) >= 4
    assert len(lib.get("gpus", [])) >= 4
    assert len(lib.get("storage", [])) >= 3
    assert len(lib.get("ram_tiers", [])) >= 4

    passport = hw.detect_passport()
    assert passport.cpu_logical_cores >= 1
    assert passport.ram_total_gb > 0
    txt = passport.to_clipboard_summary()
    assert "AstraCradle PC Spec Passport" in txt

    custom = PriorityPreset(
        preset_id="custom_test_x3d",
        name="Test X3D Flight Sim",
        description="Pin sim to CCD0 and background to idle IO",
        cpu_profile_id="amd_ryzen_dual_ccd_x3d",
        gpu_profile_id="nvidia_rtx_5090_4090",
        storage_id="nvme_gen4_gen5",
        ram_tier_id="ram_64gb_plus",
        background_io_class="idle",
        pin_background_to_efficiency_cores=True,
    )
    hw.save_custom_preset(custom)
    assert any(p.preset_id == "custom_test_x3d" for p in hw.load_presets())

    tuned_dry, msg = hw.apply_preset(custom, cfg, dry_run=True)
    assert tuned_dry >= 0
    assert "[DRY RUN]" in msg

    tuned_real, msg_real = hw.apply_preset(custom, cfg, dry_run=False)
    assert tuned_real >= 0
    assert "Applied" in msg_real

    ok_del, _ = hw.delete_preset("custom_test_x3d")
    assert ok_del is True
    ok_builtin, _ = hw.delete_preset("preset_intel_hybrid_gaming")
    assert ok_builtin is False  # built-in protected


def test_pipelines_local_hook_and_sha256_packs(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path)
    cfg = cfg_mgr.load()
    adapter = get_os_adapter("auto", cfg.custom_protected_paths)
    journal = ForensicJournal(cfg_mgr)
    rb = RollbackManager(cfg_mgr)
    exe = GuardedExecutor(cfg_mgr, rb, journal)
    scripts = ScriptsEngine(cfg_mgr, exe, journal)
    hw = HardwareLibraryManager(cfg_mgr, journal)
    pipes = PipelineEngine(cfg_mgr, exe, scripts, hw, journal)

    loaded = pipes.load_pipelines()
    assert len(loaded) >= 3

    custom_pipe = OptimizationPipeline(
        pipeline_id="pipe_custom_test",
        name="Custom Unit Pipeline",
        description="Test multi-stage execution",
        trigger_mode="local_hook",
        stages=[
            PipelineStage("preflight_snapshot", "Pre-Flight Snapshot"),
            PipelineStage("apply_preset", "Apply Preset", {"preset_id": "preset_intel_hybrid_gaming"}),
            PipelineStage("clean_caches", "High-RAM Cache Trim", condition="if_ram_above", condition_value=999.0),
            PipelineStage("apply_gpu_profile", "GPU Thermal Eco", {"gpu_profile_id": "gpu_quiet_eco"}),
            PipelineStage("self_heal", "Self-Heal Check"),
        ],
    )
    pipes.save_custom_pipeline(custom_pipe)
    ok, summary, logs = pipes.run_pipeline("pipe_custom_test", adapter, cfg, [], dry_run=True)
    assert ok is True
    assert len(logs) == 5
    assert any("Skipped 'High-RAM Cache Trim'" in ln for ln in logs)
    assert "DRY RUN" in summary

    ok_del_builtin, _ = pipes.delete_pipeline("pipe_smooth_gaming")
    assert ok_del_builtin is False
    ok_del_custom, _ = pipes.delete_pipeline("pipe_custom_test")
    assert ok_del_custom is True
    pipes.save_custom_pipeline(custom_pipe)

    # Local file-drop hook connector
    pipes.hook_trigger_path.write_text(
        json.dumps({"pipeline_id": "pipe_custom_test", "dry_run": True}),
        encoding="utf-8",
    )
    hook_msg = pipes.poll_local_hook_trigger(adapter, cfg, [])
    assert hook_msg is not None and "Local Hook" in hook_msg
    assert not pipes.hook_trigger_path.exists()

    # Chaos: corrupt trigger JSON should never crash
    pipes.hook_trigger_path.write_text("{corrupt_json!!!", encoding="utf-8")
    assert pipes.poll_local_hook_trigger(adapter, cfg, []) is None

    # Shareable SHA-256 pack export/import + tamper rejection
    pack_path = tmp_path / "share.astracradle-pack.json"
    ScriptAndPresetPackManager.export_pack(pack_path, cfg, hw.load_presets(), pipes.load_pipelines())
    assert pack_path.exists()
    ok_imp, msg_imp = ScriptAndPresetPackManager.import_pack(pack_path, hw, pipes, cfg)
    assert ok_imp is True
    assert "Imported" in msg_imp

    raw = json.loads(pack_path.read_text(encoding="utf-8"))
    raw["payload"]["exported_at"] = "TAMPERED"
    pack_path.write_text(json.dumps(raw), encoding="utf-8")
    ok_bad, msg_bad = ScriptAndPresetPackManager.import_pack(pack_path, hw, pipes, cfg)
    assert ok_bad is False
    assert "SHA-256" in msg_bad


def test_telemetry_history_glitch_shredder_and_dryrun_preview(tmp_path: Path) -> None:
    cfg_mgr = ConfigManager(base_dir=tmp_path / "astra_home")
    cfg = cfg_mgr.load()
    adapter = get_os_adapter("auto", cfg.custom_protected_paths)
    journal = ForensicJournal(cfg_mgr)
    rb = RollbackManager(cfg_mgr)
    exe = GuardedExecutor(cfg_mgr, rb, journal)

    gpu = GpuTelemetryCollector.collect()
    assert gpu.name != ""

    snap = TelemetryCollector(cfg.excluded_processes).collect(adapter, include_dns=False)
    hdb = TelemetryHistoryDB(cfg_mgr)
    hdb.record(snap, 92)
    pts = hdb.query_recent(days=30, limit=50)
    assert len(pts) >= 1 and pts[-1].health_score == 92

    # Driver correlation + PreFlight + Outdated opt-in gate + Glitch fixer
    _ = DriverUpdateCorrelator.recent_changes(limit=3)
    ok_snap, snap_msg = PreFlightSnapshotHook.check_or_prepare(dry_run=True)
    assert ok_snap is True and "Pre-Flight" in snap_msg

    ok_gate, _ = OutdatedAppsChecker.check(consent=False)
    assert ok_gate is False
    assert len(GlitchTroubleshooter.list_troubleshooters()) == 4
    ok_g, _ = GlitchTroubleshooter.run("refresh_audio_spool", adapter, exe, cfg, journal, dry_run=True)
    assert ok_g is True

    # Dry-run diff preview + real quarantine + confirmed zero-pass shredder
    scratch = tmp_path / "scratch_cache"
    scratch.mkdir()
    sample = scratch / "stale_shader.bin"
    sample.write_bytes(b"SECRET_SHADER_DATA" * 20)
    old_t = time.time() - 900
    os.utime(sample, (old_t, old_t))

    dry_res = exe.execute_action("clean_paths", adapter, cfg, params={"paths": [str(scratch)]}, dry_run=True)
    assert dry_res.dry_run is True
    assert dry_res.files_affected == 1
    assert len(dry_res.preview_items) == 1

    real_res = exe.execute_action("clean_paths", adapter, cfg, params={"paths": [str(scratch)]}, dry_run=False)
    assert real_res.dry_run is False
    assert real_res.entry_id is not None
    assert not sample.exists()

    # Without confirmation shred must refuse
    ok_no, _ = rb.shred_quarantine_entry(real_res.entry_id, confirm_irreversible=False)
    assert ok_no is False
    # With confirmation zero-pass shred permanently erases quarantined file
    ok_shred, msg_shred = rb.shred_quarantine_entry(real_res.entry_id, confirm_irreversible=True)
    assert ok_shred is True and "shredded" in msg_shred.lower()


def test_installer_generators_and_xvfb_v2_4_ui(tmp_path: Path) -> None:
    out_dir = tmp_path / "installers"
    created = generate_all(out_dir)
    assert len(created) == 3
    assert any(p.name == "AstraCradle.iss" for p in created)
    assert any(p.name == "build_macos_dmg.sh" for p in created)
    assert any(p.name == "build_linux_appimage.sh" for p in created)

    from astracradle.core.advanced_controls import (
        FullscreenFocusDetector,
        GpuPowerAndFanController,
        GpuThermalProfile,
        NetworkFirewallController,
        StandaloneBuildRunner,
    )
    from astracradle.ui.app import AstraCradleApp
    from astracradle.ui.modals import PipelineEditorDialog

    # Verify new v2.4.0+ advanced controls
    fs = FullscreenFocusDetector.detect()
    assert fs.detection_method != ""

    mgr = ConfigManager(base_dir=tmp_path / "adv_cfg")
    j = ForensicJournal(mgr)
    fw = NetworkFirewallController(mgr, j)
    ok_fw, msg_fw, rule = fw.add_or_update_rule("steam", "throttle", rate_kbps=2048, dry_run=False)
    assert ok_fw is True and rule.rate_limit_kbps == 2048 and "2048" in msg_fw
    assert len(fw.load_rules()) == 1
    assert fw.remove_rule(rule.rule_id) is True

    from astracradle.core.i18n import search_help_topics
    assert any(t.topic_id == "hardware_presets" for t in search_help_topics("ccd0"))
    assert any(t.topic_id == "pipelines_connectors" for t in search_help_topics("pipeline"))
    assert any(t.topic_id == "gpu_power_fan" for t in search_help_topics("fan curve"))
    assert any(t.topic_id == "network_firewall" for t in search_help_topics("firewall"))
    assert any(t.topic_id == "fullscreen_focus_shredder" for t in search_help_topics("fullscreen"))

    gctrl = GpuPowerAndFanController(mgr, j)
    profs = gctrl.load_profiles()
    assert len(profs) >= 3
    custom_gp = GpuThermalProfile("gpu_custom", "Silent", 75, 45, 72, "Test quiet curve")
    gctrl.save_custom_profile(custom_gp)
    ok_gp, msg_gp, cmd_gp = gctrl.apply_profile(custom_gp, dry_run=True)
    assert ok_gp is True and "[DRY RUN]" in msg_gp and cmd_gp != ""
    assert gctrl.delete_profile("gpu_quiet_eco")[0] is False
    assert gctrl.delete_profile("gpu_custom")[0] is True

    st_bld = StandaloneBuildRunner.status()
    assert st_bld["spec_exists"] is True

    app = AstraCradleApp(base_dir=tmp_path / "ui_home", show_startup_popups=False)
    try:
        app.geometry("1060x700+0+0")
        app.update()
        # Verify all 8 sidebar pages & all sub-tabs render cleanly without overflow/errors
        for nav in ("dashboard", "playbook", "hunter", "scripts", "hardware", "modules", "safeguards", "settings"):
            app.switch_nav(nav)
            app.update()

        app.switch_nav("hardware")
        for htab in ("Component Library & Presets", "Visual Pipelines & Packs", "GPU Power & Fan Curves"):
            app.hw_seg.set(htab)
            app._on_hw_tab(htab)
            app.update()

        # Verify PipelineEditorDialog visual reorder + conditional branching
        pipes = app.pipe_mgr.load_pipelines()
        saved_holder = []
        ped = PipelineEditorDialog(app, app.palette, app.fonts, pipes[0], lambda pl: saved_holder.append(pl))
        app.update()
        ped._add_stage()
        ped._move(0, 1)
        ped._save_and_close()
        app.update()
        assert len(saved_holder) == 1

        app.switch_nav("modules")
        for tab_name in (
            "Storage",
            "Startup",
            "Processes & Firewall",
            "Installed Apps",
            "Duplicates & Bloat",
            "Privacy & Shield",
            "Glitch & OS Tools",
        ):
            app.mod_seg.set(tab_name)
            app._on_module_tab(tab_name)
            app.update()

        # Verify topbar controls stay within bounds at minimum 1060px width
        app.update_idletasks()
        assert app.scan_cta.winfo_x() + app.scan_cta.winfo_width() <= app.topbar.winfo_width() + 4

        app.show_toast("Test reversible toast", undo_entry_id="act-test123")
        app.update()
        app.hide_toast()

        # Verify live theme & language hot-reload without restarting
        app.switch_nav("settings")
        app.set_theme_var.set("High Contrast")
        app.set_lang_var.set("es")
        app.on_save_settings()
        app.update()
        assert app.palette.bg_canvas == "#000000"
        assert app.i18n.lang == "es"
    finally:
        app.on_close()
