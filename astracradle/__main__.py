"""CLI & native desktop GUI entrypoint for AstraCradle."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __app_name__, __tagline__, __version__
from .core.config import ConfigManager, SITUATION_PROFILES
from .core.crash_analyzer import CrashAnalyzer
from .core.diagnostics import DiagnosticsEngine
from .core.executor import GuardedExecutor
from .core.hardware_library import HardwareLibraryManager
from .core.journal import ForensicJournal
from .core.lifecycle import LifecycleManager
from .core.os_adapters import get_os_adapter
from .core.pipelines import PipelineEngine
from .core.playbook import PlaybookBuilder
from .core.rollback import RollbackManager
from .core.scripts_engine import ScriptsEngine
from .core.self_heal import SelfHealEngine
from .core.telemetry import TelemetryCollector


def run_cli(args: argparse.Namespace) -> int:
    cfg_mgr = ConfigManager()
    cfg = cfg_mgr.load()
    if args.profile and args.profile in SITUATION_PROFILES:
        cfg.apply_profile(args.profile)
        cfg_mgr.save(cfg)

    adapter = get_os_adapter(
        override=args.os or cfg.os_adapter_override,
        custom_protected_paths=cfg.custom_protected_paths,
    )
    journal = ForensicJournal(cfg_mgr)
    rollback_mgr = RollbackManager(cfg_mgr)
    executor = GuardedExecutor(cfg_mgr, rollback_mgr, journal)
    scripts_engine = ScriptsEngine(cfg_mgr, executor, journal)
    lifecycle = LifecycleManager(cfg_mgr, rollback_mgr, journal, scripts_engine)
    self_heal = SelfHealEngine(cfg_mgr, rollback_mgr, journal)

    if args.install_shortcuts:
        pkg_root = Path(__file__).resolve().parent
        repo_root = Path(__file__).resolve().parents[1]
        base_root = repo_root if (repo_root / "assets").exists() else pkg_root
        created = lifecycle.install_shortcuts(base_root)
        print(f"Created {len(created)} AstraCradle shortcut(s):")
        for c in created:
            print(f"  - {c}")
        return 0

    if args.uninstall:
        delete_data = args.delete_data
        if not args.yes:
            ans = input(
                f"Delete ALL AstraCradle profiles, settings, logs & data in {cfg_mgr.base_dir}? [y/N]: "
            ).strip().lower()
            delete_data = ans in ("y", "yes")
        summary = lifecycle.uninstall_astracradle(
            adapter,
            cfg,
            delete_user_data=delete_data,
            restore_quarantine_first=True,
            uninstall_pip_package=args.uninstall_package,
        )
        for note in summary.notes:
            print(" -", note)
        return 0 if summary.ok else 1

    if args.set_update_repo:
        cfg.update_repo_url = args.set_update_repo.strip()
        cfg_mgr.save(cfg)
        print(f"Default update GitHub repo saved -> {cfg.update_repo_url}")
        if not args.check_updates:
            return 0

    if args.check_updates:
        from .core.updater import ModuleAndUpdateManager

        upd = ModuleAndUpdateManager(cfg_mgr, journal)
        repo_root = Path(__file__).resolve().parents[1]
        res = upd.check_for_updates(cfg, check_remote=True, repo_dir=repo_root)
        print(res.status_message)
        if res.update_available:
            ok, msg = upd.pull_from_github_and_update(cfg, repo_dir=repo_root)
            print(msg)
            if ok:
                lifecycle.install_shortcuts(repo_root)
            return 0 if ok else 1
        return 0

    if args.install_update:
        from .core.updater import ModuleAndUpdateManager

        upd = ModuleAndUpdateManager(cfg_mgr, journal)
        ok, msg = upd.install_update_bundle(Path(args.install_update), cfg)
        print(msg)
        if ok:
            lifecycle.install_shortcuts(Path(__file__).resolve().parents[1])
        return 0 if ok else 1

    if args.self_check:
        events, audits = self_heal.run_self_check(adapter, cfg)
        print(f"{__app_name__} v{__version__} — Self-Heal Check ({len(events)} invariants):")
        for ev in events:
            print(f"  [{ev.status.upper()}] {ev.check_name}: {ev.details}")
        print("Forensic Self-Audit:")
        for af in audits:
            print(f"  [{af.severity.upper()}] {af.title}: {af.explanation}")
        return 0

    if args.apply_preset:
        hw_mgr = HardwareLibraryManager(cfg_mgr, journal)
        presets = {p.preset_id: p for p in hw_mgr.load_presets()}
        pr = presets.get(args.apply_preset)
        if not pr:
            print(f"Preset '{args.apply_preset}' not found.")
            return 1
        ok, msg = hw_mgr.apply_preset(pr, cfg, dry_run=cfg.dry_run_default)
        cfg.active_preset_id = pr.preset_id
        cfg_mgr.save(cfg)
        print(msg)
        return 0 if ok else 1

    if args.run_pipeline:
        hw_mgr = HardwareLibraryManager(cfg_mgr, journal)
        pipe_mgr = PipelineEngine(cfg_mgr, executor, scripts_engine, hw_mgr, journal)
        telemetry = TelemetryCollector(cfg.excluded_processes)
        snap = telemetry.collect(adapter, include_dns=False)
        ok, summary, logs = pipe_mgr.run_pipeline(
            args.run_pipeline, adapter, cfg, snap.top_processes, dry_run=cfg.dry_run_default
        )
        print(summary)
        for ln in logs:
            print(" -", ln)
        return 0 if ok else 1

    if args.full_discovery:
        from .core.full_discovery import FullSystemDiscoveryEngine

        engine = FullSystemDiscoveryEngine(cfg_mgr, rollback_mgr, journal)
        out = engine.run_full_discovery(adapter, cfg)
        print(f"{__app_name__} v{__version__} — Full Local PC Baseline ({len(out.baseline.subsystems_checked)} subsystems):")
        for ln in out.baseline.summary_lines:
            print("  •", ln)
        print(f"Saved baseline -> {out.baseline.baseline_json_path}")
        return 0

    if args.fix_safe:
        from .core.pc_extras import SafeBeginnerFixer

        fixer = SafeBeginnerFixer(cfg_mgr, executor, rollback_mgr, journal)
        ok, summary, notes = fixer.run_fix_everything_safe(adapter, cfg, dry_run=cfg.dry_run_default)
        print(summary)
        for ln in notes:
            print("  •", ln)
        return 0 if ok else 1

    if args.battery_report:
        from .core.pc_extras import BatteryAndPowerInspector

        rep = BatteryAndPowerInspector().inspect()
        print(rep.summary)
        for wl in rep.wake_locks:
            print("  wake-lock:", wl)
        return 0

    if args.diff_snapshots:
        from .core.pc_extras import BaselineDiffEngine

        de = BaselineDiffEngine(cfg_mgr, journal)
        snaps = de.list_snapshots()
        if len(snaps) < 2:
            out = de.capture_snapshot(adapter, cfg)
            print(f"Captured snapshot -> {out.name} (need 2 snapshots to diff).")
            return 0
        diff = de.diff_snapshots(snaps[-2], snaps[-1])
        for ln in diff.summary_lines:
            print(ln)
        return 0

    if args.astrashield:
        from .core.adblock_shield import AstraShieldManager

        sh = AstraShieldManager(cfg_mgr, rollback_mgr, journal)
        if args.astrashield == "on":
            ok, msg = sh.enable_hosts_sinkhole(cfg.astrashield_categories, dry_run=cfg.dry_run_default)
            print(msg)
            return 0 if ok else 1
        if args.astrashield == "off":
            ok, msg = sh.disable_hosts_sinkhole()
            print(msg)
            return 0 if ok else 1
        if args.astrashield == "sync-pihole":
            ok, msg, _ = sh.download_pihole_blocklists(timeout=12.0)
            print(msg)
            return 0 if ok else 1
        if args.astrashield == "youtube-prune":
            js_p, flt_p = sh.export_youtube_ad_prune_assets()
            print(f"Exported YouTube Safe Ad-Prune userscript -> {js_p}")
            print(f"Exported uBlock/AdGuard filters -> {flt_p}")
            return 0
        st = sh.status()
        print(
            f"AstraShield: hosts_active={st.hosts_active}, dns_running={st.dns_server_running}, "
            f"domains={st.blocked_domain_count} (pihole_cache={st.pihole_cached_count}), blocked={st.blocked_queries}/{st.total_queries}"
        )
        return 0

    if args.scan_usb:
        from .core.usb_guard import UsbAndPeripheralGuard

        ug = UsbAndPeripheralGuard(cfg_mgr, journal)
        devs = ug.scan_all_peripherals()
        print(f"Attached Hardware & Peripherals ({len(devs)} detected, BadUSB Guard={'ON' if cfg.badusb_guard_enabled else 'OFF'}):")
        for d in devs:
            if d.bus == "usb":
                safe, reasons = ug.evaluate_badusb(d, cfg)
                verdict = "CLEARED" if safe else f"BLOCKED ({'; '.join(reasons)})"
            else:
                verdict = "OK"
            print(f"  [{d.bus.upper()}] {d.vendor} {d.name} ({d.plain_description}) -> {verdict}")
        return 0

    if args.conflicts:
        from .core.pc_extras import ConflictProgramDetector

        cd = ConflictProgramDetector(journal)
        found = cd.scan_conflicts()
        print(f"Conflicting Background Programs ({len(found)} detected):")
        for cf in found:
            print(f"  [{cf.severity.upper()}] {cf.name} ({cf.service_unit or f'PID {cf.pid}'}) — {cf.reason}")
        return 0

    if args.export_ai_bundle:
        from dataclasses import asdict
        from .core.adblock_shield import AstraShieldManager
        from .core.advanced_controls import GpuPowerAndFanController
        from .core.pc_extras import ConflictProgramDetector
        from .core.safeguard_plus import AIDiagnosticArchiveExporter

        telemetry = TelemetryCollector(cfg.excluded_processes)
        snap = telemetry.collect(adapter, include_dns=True)
        sensors = TelemetryCollector.collect_detailed_sensors()
        gpu_live = GpuPowerAndFanController.probe_live_power_and_fan()
        conflicts = [asdict(c) for c in ConflictProgramDetector(journal).scan_conflicts()]
        shield_st = asdict(AstraShieldManager(cfg_mgr, rollback_mgr, journal).status())
        exporter = AIDiagnosticArchiveExporter(cfg_mgr, journal)
        res = exporter.export_ai_handoff_bundle(
            snapshot_data=asdict(snap),
            sensors_data=sensors,
            gpu_data=gpu_live,
            conflicts_data=conflicts,
            shield_status=shield_st,
        )
        print(f"AI Diagnostic Markdown Report -> {res['markdown']}")
        print(f"AI Diagnostic JSON Bundle     -> {res['json']}")
        print(f"AI Diagnostic Zip Archive     -> {res['zip']}")
        print(f"Anomalies flagged: {res['anomaly_count']} | Old files archived: {res['archived_count']}")
        return 0

    telemetry = TelemetryCollector(cfg.excluded_processes)
    snap = telemetry.collect(adapter, include_dns=True)
    report = DiagnosticsEngine().run_scan(snap, adapter, cfg)
    pb = PlaybookBuilder().build(report, cfg)

    if args.export_md:
        out = PlaybookBuilder().export(pb, cfg_mgr.playbooks_dir, fmt="md")
        print(f"Playbook exported to: {out}")

    print(PlaybookBuilder.to_markdown(pb))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="astracradle",
        description=f"{__app_name__} v{__version__} — {__tagline__}",
    )
    parser.add_argument("--diagnose", action="store_true", help="Run CLI 8-subsystem diagnostics & print playbook.")
    parser.add_argument("--full-discovery", action="store_true", help="Run comprehensive local PC discovery across all subsystems.")
    parser.add_argument("--fix-safe", action="store_true", help="Run 1-Click 'Fix Everything Safe' reversible optimization pass.")
    parser.add_argument("--battery-report", action="store_true", help="Inspect laptop battery wear percent, cycle count, watts & wake-locks.")
    parser.add_argument("--diff-snapshots", action="store_true", help="Compare latest 2 'What Changed' PC snapshots.")
    parser.add_argument(
        "--astrashield",
        choices=["on", "off", "status", "sync-pihole", "youtube-prune"],
        default=None,
        help="Toggle, sync Pi-hole lists, export YouTube ad-pruner, or inspect AstraShield.",
    )
    parser.add_argument("--scan-usb", action="store_true", help="List all USB & peripheral hardware and run BadUSB check.")
    parser.add_argument("--conflicts", action="store_true", help="Check for overlapping DNS, fan, or optimizer tools.")
    parser.add_argument("--export-ai-bundle", action="store_true", help="Consolidate old logs and export an AI-ready diagnostic report.")
    parser.add_argument("--minimized", action="store_true", help="Start AstraCradle minimized in the bottom-right system tray.")
    parser.add_argument("--self-check", action="store_true", help="Run silent 7-invariant self-heal & forensic audit.")
    parser.add_argument("--install-shortcuts", action="store_true", help="Create Desktop & Menu shortcuts for 1-click launch.")
    parser.add_argument("--check-updates", action="store_true", help="Check GitHub repo first, then local bundles, and apply updates.")
    parser.add_argument("--set-update-repo", type=str, default=None, help="Set default GitHub repo URL checked before local updates.")
    parser.add_argument("--install-update", type=str, default=None, help="Install a newer AstraCradle-vX.Y.Z-bundle.zip/.tar.gz update.")
    parser.add_argument("--uninstall", action="store_true", help="Run safe interactive clean uninstaller.")
    parser.add_argument("--delete-data", action="store_true", help="Used with --uninstall --yes to delete all profiles & data.")
    parser.add_argument("--uninstall-package", action="store_true", help="Used with --uninstall to pip-uninstall astracradle.")
    parser.add_argument("--yes", action="store_true", help="Confirm non-interactive uninstall.")
    parser.add_argument("--export-md", action="store_true", help="Export generated playbook Markdown.")
    parser.add_argument("--profile", type=str, default=None, help="Apply a Situation Profile before scanning.")
    parser.add_argument("--apply-preset", type=str, default=None, help="Apply a Hardware & Priority Preset by ID.")
    parser.add_argument("--run-pipeline", type=str, default=None, help="Run a multi-stage Optimization Pipeline by ID.")
    parser.add_argument(
        "--os",
        type=str,
        choices=["auto", "Linux", "Darwin", "Windows"],
        default=None,
        help="Override OS adapter for cross-OS playbook inspection.",
    )
    args = parser.parse_args(argv)

    if (
        args.diagnose
        or args.full_discovery
        or args.fix_safe
        or args.battery_report
        or args.diff_snapshots
        or args.astrashield
        or args.scan_usb
        or args.conflicts
        or args.export_ai_bundle
        or args.self_check
        or args.install_shortcuts
        or args.check_updates
        or args.set_update_repo
        or args.install_update
        or args.uninstall
        or args.apply_preset
        or args.run_pipeline
    ):
        return run_cli(args)

    cfg_mgr = ConfigManager()
    journal = ForensicJournal(cfg_mgr)
    crash_analyzer = CrashAnalyzer(cfg_mgr, journal)

    def _excepthook(exc_type, exc_val, exc_tb):
        crash_analyzer.record_unhandled_exception(exc_val)
        sys.__excepthook__(exc_type, exc_val, exc_tb)

    sys.excepthook = _excepthook

    from .ui.app import launch_gui

    launch_gui(start_minimized=bool(args.minimized))
    return 0


if __name__ == "__main__":
    sys.exit(main())
