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
    cfg_mgr = ConfigManager(base_dir=args.config_dir)
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

    if args.quit:
        from .core.single_instance import SingleInstanceManager

        killed = SingleInstanceManager(cfg_mgr).terminate_existing_instances(timeout_sec=3.0)
        if killed:
            print(f"Closed {len(killed)} running AstraCradle instance(s): {killed}")
        else:
            print("No running AstraCradle GUI instance found.")
        return 0

    if args.apply_boot_toggles:
        ok, notes = lifecycle.apply_boot_toggles(adapter, cfg, dry_run=cfg.dry_run_default)
        for ln in notes:
            print("  •", ln)
        return 0 if ok else 1

    if args.widget_status:
        import json as _json
        from .core.adblock_shield import AstraShieldManager

        snap = TelemetryCollector(cfg.excluded_processes).collect(adapter, include_dns=False)
        rep = DiagnosticsEngine().run_scan(snap, adapter, cfg)
        sh_st = AstraShieldManager(cfg_mgr, rollback_mgr, journal).status()
        cls_name = "good" if rep.health_score >= 80 else ("warn" if rep.health_score >= 55 else "crit")
        payload = {
            "text": f"✦ {rep.health_score} · CPU {snap.cpu_percent:.0f}% · RAM {snap.mem_percent:.0f}%",
            "tooltip": (
                f"AstraCradle v{__version__}\n"
                f"Health: {rep.health_score}/100 ({cfg.active_profile})\n"
                f"CPU: {snap.cpu_percent:.1f}% | RAM: {snap.mem_percent:.1f}%\n"
                f"AstraShield: {'ON' if (sh_st.hosts_active or sh_st.dns_server_running) else 'OFF'}"
            ),
            "class": cls_name,
            "percentage": rep.health_score,
        }
        print(_json.dumps(payload))
        return 0

    if args.bufferbloat_test:
        from .core.rf_bandwidth_scanner import BandwidthAndRfScanner

        bb = BandwidthAndRfScanner(journal).run_bufferbloat_check()
        print(
            f"Bufferbloat Grade {bb.grade} | Idle {bb.idle_rtt_ms:.1f} ms | "
            f"Loaded {bb.loaded_rtt_ms:.1f} ms (+{bb.bloat_delta_ms:.1f} ms) | Jitter {bb.jitter_ms:.1f} ms"
        )
        print(" ", bb.recommendation)
        return 0

    if args.export_profile:
        from .core.safeguard_plus import PortableProfileBackup

        ok, msg, _ = PortableProfileBackup.export_profile_archive(cfg_mgr, Path(args.export_profile), journal)
        print(msg)
        return 0 if ok else 1

    if args.import_profile:
        from .core.safeguard_plus import PortableProfileBackup

        ok, msg = PortableProfileBackup.import_profile_archive(Path(args.import_profile), cfg_mgr, journal)
        print(msg)
        return 0 if ok else 1

    if args.failover_status or args.failover_tick or args.failover_watch:
        import time as _ft
        from .core.network_failover import NetworkFailoverEngine

        nfe = NetworkFailoverEngine(cfg_mgr, journal)
        if args.failover_watch:
            print(
                f"Watching Ethernet/Wi-Fi Failover every {cfg.failover_ping_interval_sec}s "
                f"(recovery stability: {cfg.failover_recovery_stable_sec}s)... Ctrl+C to stop."
            )
            try:
                while True:
                    snap = nfe.evaluate_step(cfg)
                    print(
                        f"[{snap.state.upper()}] active={snap.active_iface} primary={snap.primary_iface} "
                        f"backup={snap.backup_iface} ping='{snap.last_probe_reason}' "
                        f"stable={snap.stable_elapsed_sec:.0f}/{snap.recovery_stable_sec:.0f}s"
                    )
                    _ft.sleep(max(2.0, float(cfg.failover_ping_interval_sec)))
            except KeyboardInterrupt:
                return 0
        snap = nfe.evaluate_step(cfg) if args.failover_tick else nfe.current_snapshot(cfg)
        print(
            f"Failover [{snap.state.upper()}] | Active: {snap.active_iface} | "
            f"Primary: {snap.primary_iface} -> Backup: {snap.backup_iface} | "
            f"Ping Interval: {cfg.failover_ping_interval_sec}s | Recovery Window: {cfg.failover_recovery_stable_sec}s"
        )
        return 0

    if args.deps_status:
        from .core.dependency_library import DependencyLibraryManager

        dlm = DependencyLibraryManager(journal)
        sh_kind = dlm.detect_user_shell()
        print(f"AstraCradle Companion Dependency Library (detected shell: {sh_kind}):")
        for st in dlm.inspect_all(shell_pref=sh_kind):
            print(f"  [{'OK' if st.installed else 'MISSING'}] {st.name} -> {st.binary_path or st.recommended_cmd}")
        return 0

    if args.install_deps:
        from .core.dependency_library import DependencyLibraryManager

        dlm = DependencyLibraryManager(journal)
        permitted = bool(args.yes or cfg.consent_auto_install_deps)
        sh_kind = args.shell_kind or dlm.detect_user_shell()
        outs = dlm.install_all_missing(permitted=permitted, shell_kind=sh_kind, dry_run=cfg.dry_run_default)
        if not outs:
            print("All tracked companion dependencies are already installed.")
        for oc in outs:
            print(f"  [{'OK' if oc.ok else 'SKIP'}] {oc.name}: {oc.message}\n    Cmd: {oc.commands_used}")
        return 0 if all(o.ok for o in outs) else 1

    if args.help_topic:
        from .core.i18n import search_help_topics

        hits = search_help_topics(args.help_topic)
        if not hits:
            print(f"No offline help topics matched '{args.help_topic}'.")
            return 0
        for t in hits[:5]:
            print(f"[{t.category}] {t.title}\n  {t.summary}\n  {t.details}\n")
        return 0

    if args.lookup_pid:
        from .core.process_lookup import ProcessIntelligenceLookup

        pil = ProcessIntelligenceLookup(cfg_mgr)
        target = str(args.lookup_pid).strip()
        allow_web = not bool(args.no_web)
        res = pil.lookup(int(target) if target.isdigit() else target, allow_web=allow_web)
        pid_tag = f"PID {res.pid} · " if res.pid is not None else ""
        print(f"[{res.status.upper()} | {res.source_name}] {pid_tag}{res.name}")
        print(f"  Description : {res.brief_description}")
        if res.cmdline:
            print(f"  Command Line: {res.cmdline}")
        if res.safe_note:
            print(f"  Safety Note : {res.safe_note}")
        return 0

    if args.hermes_status or args.hermes_setup or args.hermes_ask:
        from .core.hermes_agent import HermesAgentManager, recommend_hermes_models

        hm = HermesAgentManager(cfg_mgr, journal)
        if args.hermes_local_only:
            cfg.hermes_local_only = True
            cfg.hermes_mode = "local_gpu"
            cfg.hermes_provider = "ollama"
        if args.hermes_mode:
            cfg.hermes_mode = args.hermes_mode
        if args.hermes_provider:
            cfg.hermes_provider = args.hermes_provider
        if args.hermes_model:
            cfg.hermes_model = args.hermes_model
        if args.hermes_permission:
            cfg.hermes_permission_level = args.hermes_permission
            cfg.hermes_allow_shell = args.hermes_permission in ("standard_user", "sudo_with_prompt")

        if args.hermes_setup:
            ok_h, msg_h, st_h = hm.install_and_setup_hermes(
                cfg,
                api_key=str(args.hermes_api_key or ""),
                install_upstream_pkg=True,
                pull_local_model=True,
                progress_cb=lambda step, _pct, det: print(f"[{step}] {det}"),
            )
            print(f"Hermes Setup: {'OK' if ok_h else 'WARN'} | {st_h.summary}\n{msg_h}")
            return 0 if ok_h else 1

        if args.hermes_ask:
            res = hm.run_prompt(args.hermes_ask, cfg)
            if res.ok:
                print(res.reply)
                if res.skill_created:
                    print(f"\n[Auto-Learned Skill] {res.skill_created}")
                return 0
            print(f"Hermes Error: {res.error}")
            return 1

        rec = recommend_hermes_models()
        st = hm.get_status(cfg)
        print(f"Hermes OS AI Status : {st.summary}")
        print(f"Hermes Home         : {st.hermes_home}")
        print(f"Detected GPU        : {rec.gpu_name} ({rec.gpu_vram_gb:.1f} GB VRAM, {rec.system_ram_gb:.0f} GB RAM)")
        print(f"Recommended Local   : {rec.recommended_local_model} ({rec.recommended_context_tokens // 1024}K ctx)")
        print(f"Recommended Free    : {rec.optimal_free_provider} -> {rec.optimal_free_model} (0 GB VRAM)")
        print(f"Skills / Cron / Sess: {st.skills_count} skill(s) | {st.cron_count} cron job(s) | {st.sessions_count} session(s)")
        return 0

    if args.commands_list:
        from .core.command_db import CommandDatabaseManager

        cdb = CommandDatabaseManager(cfg_mgr, journal)
        for rec in cdb.list_commands():
            print(f"[{rec.category} | {rec.platform}] {rec.title}\n  {rec.script}")
        return 0

    if args.proximity_connect:
        from .core.mobile_continuity import DeviceRegistryManager, MobileCompanionEngine

        reg = DeviceRegistryManager(cfg_mgr, journal)
        comp = MobileCompanionEngine(cfg_mgr, reg, journal)
        outcomes = comp.auto_connect_proximity_devices(cfg)
        if not outcomes:
            print("No paired Tailscale/Bluetooth/ADB or designated devices found to auto-connect.")
        for oc in outcomes:
            print(f"[{'OK' if oc.connected else 'WAIT'}] {oc.device_name} ({oc.transport_used}): {oc.message}")
        return 0

    if args.tailscale_status:
        from .core.astrabeam import AppleDeviceScanner, SunshineMoonlightBridge, TailscaleDiscovery

        ts = TailscaleDiscovery.get_status()
        sm = SunshineMoonlightBridge.inspect(ts)
        ap = AppleDeviceScanner.scan(ts)
        print(ts.summary)
        if ts.magic_dns:
            print(f"  MagicDNS: {ts.magic_dns}")
        for peer in ts.peers:
            print(f"  [{'ON' if peer.online else 'OFF'}] {peer.hostname} ({peer.os_name}) -> {peer.tailscale_ip}")
        for ad in ap:
            print(f"  [APPLE:{ad.device_kind.upper()}] {ad.name} ({ad.transport}: {ad.address})")
        print(f"  Sunshine: {'Running' if sm.sunshine_running else ('Installed' if sm.sunshine_installed else 'Not installed')} | Moonlight: {'Installed' if sm.moonlight_installed else 'Not installed'}")
        return 0

    if args.beam_host:
        import time as _t
        from .core.astrabeam import AstraBeamServer, TailscaleDiscovery

        ts = TailscaleDiscovery.get_status()
        bind_ip = ts.self_ip if (cfg.astrabeam_tailscale_only and ts.running and ts.self_ip) else "0.0.0.0"
        srv = AstraBeamServer(
            journal,
            host=bind_ip,
            port=cfg.astrabeam_port,
            pin=args.beam_pin or cfg.astrabeam_pin,
            fps=cfg.astrabeam_fps,
            quality=cfg.astrabeam_quality,
            scale_pct=cfg.astrabeam_scale_pct,
            tailscale_only=cfg.astrabeam_tailscale_only,
            tls_enabled=cfg.astrabeam_tls_enabled,
            cert_dir=cfg_mgr.base_dir / "certs",
            lockout_seconds=cfg.astrabeam_lockout_seconds,
        )
        ok, msg = srv.start()
        print(msg)
        if srv.cert_fingerprint_sha256:
            print(f"  TLS SHA-256 Fingerprint: {srv.cert_fingerprint_sha256}")
        if not ok:
            return 1
        try:
            while True:
                _t.sleep(1.0)
        except KeyboardInterrupt:
            srv.stop()
            return 0

    if args.beam_connect:
        from .core.astrabeam import AstraBeamClient

        raw_target = str(args.beam_connect).strip()
        host_p, _, port_p = raw_target.partition(":")
        client = AstraBeamClient(host_p, int(port_p) if port_p.isdigit() else cfg.astrabeam_port, args.beam_pin or "")
        ok, msg = client.pair()
        print(msg)
        if not ok:
            return 1
        frame = client.fetch_frame()
        print(f"Fetched frame: {len(frame) if frame else 0} bytes ({client.last_rtt_ms:.1f} ms RTT)")
        return 0 if frame else 1

    if args.benchmark_report:
        from .core.rf_bandwidth_scanner import BandwidthAndRfScanner
        from .core.telemetry_history import TelemetryHistoryDB

        snap = TelemetryCollector(cfg.excluded_processes).collect(adapter, include_dns=False)
        rep = DiagnosticsEngine().run_scan(snap, adapter, cfg)
        bb = BandwidthAndRfScanner(journal).run_bufferbloat_check()
        db = TelemetryHistoryDB(cfg_mgr)
        row = db.record_weekly_benchmark(snap, rep.health_score, bufferbloat_grade=bb.grade)
        regs = db.detect_regressions()
        print(f"Benchmark saved: Health {row['health_score']}/100 | Idle RAM {row['idle_ram_mb']} MB | Bloat {bb.grade}")
        for r in regs:
            print("  [REGRESSION]", r)
        return 0

    if args.setup_reboot:
        import getpass
        from .core.elevation import ElevationRunner

        def _cli_sudo_prompt(title: str, detail: str) -> str | None:
            print(f"\n[{title}]\n{detail}")
            try:
                pw = getpass.getpass("Enter root/sudo password (leave empty to cancel): ")
            except (EOFError, KeyboardInterrupt):
                return None
            return pw if pw else None

        elev = ElevationRunner(password_prompt_cb=None if args.yes else _cli_sudo_prompt)
        ok, msg = lifecycle.install_reboot_persistence(cfg, elevation=elev, dry_run=cfg.dry_run_default)
        print(msg)
        return 0 if ok else 1

    if args.show_install_memory:
        from .core.updater import ModuleAndUpdateManager

        upd = ModuleAndUpdateManager(cfg_mgr, journal)
        mem = upd.load_install_memory()
        print("AstraCradle Persistent Machine Update Memory:")
        print(f"  Memory File : {upd.install_memory_path}")
        print(f"  Install Dir : {mem.get('install_dir') or '(not recorded yet)'}")
        print(f"  Method      : {mem.get('install_method') or '(none)'}")
        print(f"  Last Build  : {mem.get('last_build_display') or '(none)'}")
        print(f"  Desktop Icon: {mem.get('desktop_icon')}")
        print(f"  Steps Saved : {', '.join(mem.get('remembered_steps') or []) or 'none'}")
        return 0

    if args.install_shortcuts:
        from .core.updater import ModuleAndUpdateManager

        pkg_root = Path(__file__).resolve().parent
        repo_root = Path(__file__).resolve().parents[1]
        base_root = repo_root if (repo_root / "assets").exists() else pkg_root
        upd = ModuleAndUpdateManager(cfg_mgr, journal)
        mem = upd.load_install_memory()
        if args.desktop_icon:
            want_desk = True
        elif args.no_desktop_icon:
            want_desk = False
        elif args.reapply_memory and mem.get("desktop_icon") is not None:
            want_desk = bool(mem.get("desktop_icon"))
        else:
            want_desk = False
        created = lifecycle.install_shortcuts(base_root, include_desktop_icon=want_desk)
        upd.record_install_memory(
            install_dir=(Path(args.install_dir) if args.install_dir else base_root),
            install_method=(args.install_method or ""),
            desktop_icon=want_desk,
            shortcuts_installed=True,
            shell_links_installed=True,
            step_note="Installed/updated App Menu shortcuts",
            config=cfg,
        )
        print(f"Created {len(created)} AstraCradle shortcut(s) (Desktop Icon={'YES' if want_desk else 'NO'}):")
        for c in created:
            print(f"  - {c}")
        if args.reapply_memory:
            from .core.wifi_channel_optimizer import WifiChannelOptimizer

            reapplied, _ = upd.reapply_remembered_setup(
                cfg, lifecycle, repo_dir=base_root, wifi_optimizer=WifiChannelOptimizer(journal)
            )
            for rline in reapplied:
                print(f"  [Memory] {rline}")
        return 0

    if args.record_install:
        from .core.updater import ModuleAndUpdateManager
        from .core.wifi_channel_optimizer import WifiChannelOptimizer

        upd = ModuleAndUpdateManager(cfg_mgr, journal)
        target_dir = Path(args.install_dir) if args.install_dir else Path(__file__).resolve().parents[1]
        desk_opt = True if args.desktop_icon else (False if args.no_desktop_icon else None)
        mem = upd.record_install_memory(
            install_dir=target_dir,
            install_method=(args.install_method or "installer"),
            desktop_icon=desk_opt,
            shell_links_installed=True,
            step_note="Recorded installer completion on this machine",
            config=cfg,
        )
        if args.reapply_memory:
            reapplied, mem = upd.reapply_remembered_setup(
                cfg, lifecycle, repo_dir=target_dir, wifi_optimizer=WifiChannelOptimizer(journal)
            )
            for rline in reapplied:
                print(f"  [Memory] {rline}")
        print(
            f"Machine Update Memory saved -> {upd.install_memory_path} "
            f"(steps: {', '.join(mem.get('remembered_steps') or []) or 'none'})"
        )
        return 0

    if args.mesh_status or args.mesh_plan is not None:
        from .core.wifi_mesh import WifiMeshAssistantEngine

        wma = WifiMeshAssistantEngine(cfg_mgr, journal)
        adapters = wma.probe_adapters()
        print(f"PC Wi-Fi Mesh Assistant ({len(adapters)} radio(s) detected):")
        for ad in adapters:
            print(
                f"  - {ad.iface} ({ad.phy}) | AP={ad.supports_ap} | 802.11s={ad.supports_mesh_point} | "
                f"AP+STA={ad.supports_ap_sta_concurrent} | Bands={','.join(ad.bands)} | SSID='{ad.current_ssid}'"
            )
        if args.mesh_plan is not None:
            plan = wma.build_mesh_plan(
                uplink_input=args.mesh_plan,
                mesh_ssid=args.mesh_ssid or cfg.wifi_mesh_ssid,
                band=args.mesh_band or cfg.wifi_mesh_band,
                adapters=adapters,
            )
            print(f"\nMesh Plan [{plan.mode}] | Ready={plan.ok} | Channel={plan.recommended_channel} ({plan.band})")
            print(f"  Mesh SSID : {plan.mesh_ssid} (Passphrase: {plan.passphrase})")
            for n in plan.notes:
                print(f"  • {n}")
            if plan.fish_commands:
                print("  Fish Commands:")
                for fc in plan.fish_commands:
                    print(f"    {fc}")
        return 0

    if args.uninstall:
        delete_data = args.delete_data
        if not args.yes and sys.stdin.isatty():
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
        events, audits = self_heal.run_full_self_heal(adapter, cfg)
        print(f"{__app_name__} v{__version__} — Full Self-Heal Check ({len(events)} invariants):")
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
            if ok and not cfg.dry_run_default:
                cfg.astrashield_enabled = True
                cfg_mgr.save(cfg)
            print(msg)
            return 0 if ok else 1
        if args.astrashield == "auto-on":
            cfg.astrashield_auto_start = True
            cfg.astrashield_dns_enabled = True
            cfg.astrashield_lan_tv_mode = True
            cfg.astrashield_tv_auto_failsafe = True
            ok, msg = sh.enable_hosts_sinkhole(cfg.astrashield_categories, dry_run=cfg.dry_run_default)
            if ok and not cfg.dry_run_default:
                cfg.astrashield_enabled = True
            cfg_mgr.save(cfg)
            print(f"Auto-Start Sinkhole + TV Failsafe configured ({msg}).")
            return 0 if ok else 1
        if args.astrashield == "off":
            ok, msg = sh.disable_hosts_sinkhole()
            cfg.astrashield_enabled = False
            cfg_mgr.save(cfg)
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
            f"auto_start={cfg.astrashield_auto_start}, tv_failsafe={cfg.astrashield_tv_auto_failsafe}, "
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

    if args.scan_tv:
        from .core.adblock_shield import AstraShieldManager
        from .core.lan_tv_shield import LanTvAdShield

        sh = AstraShieldManager(cfg_mgr, rollback_mgr, journal)
        tv = LanTvAdShield(journal)
        lan_ip = sh.get_lan_ipv4()
        gw_ip = tv.get_default_gateway_ip()
        devs = tv.scan_lan_devices(sh, ssdp_timeout=0.6)
        bdevs = tv.scan_bluetooth_devices()
        print(
            f"LAN AstraShield TV Primary DNS: {lan_ip} | Failsafe Secondary DNS: {gw_ip}\n"
            f"Discovered {len(devs)} Smart TV / LAN device(s):"
        )
        for dev in devs:
            print(f"  [{dev.brand}] {dev.ip} ({dev.mac}) — {dev.protection_status}")
        print(f"Discovered {len(bdevs)} Bluetooth device(s):")
        for bt in bdevs:
            st = "Connected" if bt.connected else ("Paired" if bt.paired else "Available")
            print(f"  [BT:{bt.device_kind.upper()}] {bt.name} ({bt.mac}) — {st}")
        return 0

    if args.tv_restore:
        from .core.adblock_shield import AstraShieldManager
        from .core.lan_tv_shield import LanTvAdShield

        sh = AstraShieldManager(cfg_mgr, rollback_mgr, journal)
        tv = LanTvAdShield(journal)
        _, msg = tv.restore_tv_settings(reason="cli_restore", shield=sh)
        print(msg)
        return 0

    if args.wifi_recommend or args.wifi_scan or args.wifi_optimize or args.wifi_watch or args.wifi_install_cachyos or args.wifi_restore:
        import time as _time
        from .core.wifi_channel_optimizer import WifiChannelOptimizer

        wopt = WifiChannelOptimizer(journal)
        if args.wifi_recommend:
            rec = wopt.recommend_channels(
                prefer_5ghz=cfg.wifi_prefer_5ghz,
                disable_powersave=cfg.wifi_disable_powersave,
                rescan=True,
            )
            print(rec["summary"])
            for d in rec["details"]:
                print("  •", d)
            if rec.get("has_recommendation"):
                if args.yes:
                    res = wopt.optimize_connection(
                        dry_run=cfg.dry_run_default,
                        prefer_5ghz=cfg.wifi_prefer_5ghz,
                        disable_powersave=cfg.wifi_disable_powersave,
                        rescan=False,
                    )
                    print(f"[{res.interface}] {res.action_taken}")
                else:
                    try:
                        ans = input("Apply recommended low-traffic Wi-Fi channel now? [y/N]: ").strip().lower()
                    except EOFError:
                        ans = "n"
                    if ans in ("y", "yes"):
                        res = wopt.optimize_connection(
                            dry_run=cfg.dry_run_default,
                            prefer_5ghz=cfg.wifi_prefer_5ghz,
                            disable_powersave=cfg.wifi_disable_powersave,
                            rescan=False,
                        )
                        print(f"[{res.interface}] {res.action_taken}")
                    else:
                        print("Kept current Wi-Fi channel/BSSID.")
            return 0
        if args.wifi_restore:
            ok_w, msg_w = wopt.restore_wifi_settings()
            print(msg_w)
            return 0 if ok_w else 1
        if args.wifi_install_cachyos:
            ok_i, items = wopt.install_cachyos_fish_integration(
                install_dispatcher=bool(args.yes),
                enable_timer=True,
                interval_min=cfg.wifi_check_interval_min,
            )
            print(f"CachyOS + Fish Wi-Fi Channel Auto-Optimizer installed ({len(items)} component(s)):")
            for it in items:
                print("  •", it)
            return 0 if ok_i else 1
        if args.wifi_watch:
            mins = max(1, int(cfg.wifi_check_interval_min))
            min_hold_sec = max(600.0, float(getattr(cfg, "wifi_min_channel_hold_min", 10)) * 60.0)
            print(f"AstraWiFi Watchdog active (checking every {mins}m, min channel hold {int(min_hold_sec // 60)}m; Ctrl+C to stop)...")
            try:
                while True:
                    if getattr(cfg, "consent_wifi_auto_channel", False) or not getattr(cfg, "wifi_prompt_before_apply", True):
                        res = wopt.optimize_connection(
                            dry_run=cfg.dry_run_default,
                            prefer_5ghz=cfg.wifi_prefer_5ghz,
                            disable_powersave=cfg.wifi_disable_powersave,
                            min_hold_sec=min_hold_sec,
                        )
                        print(f"[{res.interface}] {res.action_taken}")
                    else:
                        rec = wopt.recommend_channels(
                            prefer_5ghz=cfg.wifi_prefer_5ghz,
                            disable_powersave=cfg.wifi_disable_powersave,
                            min_hold_sec=min_hold_sec,
                        )
                        print(f"[Recommendation] {rec['summary']}")
                    _time.sleep(mins * 60)
            except KeyboardInterrupt:
                return 0
        if args.wifi_optimize:
            min_hold_sec = max(600.0, float(getattr(cfg, "wifi_min_channel_hold_min", 10)) * 60.0)
            if not (getattr(cfg, "consent_wifi_auto_channel", False) or not getattr(cfg, "wifi_prompt_before_apply", True) or bool(args.yes)):
                rec = wopt.recommend_channels(
                    prefer_5ghz=cfg.wifi_prefer_5ghz,
                    disable_powersave=cfg.wifi_disable_powersave,
                    min_hold_sec=min_hold_sec,
                )
                print(f"[Permission required for auto-switch; use --yes or grant permission] {rec['summary']}")
                return 0
            res = wopt.optimize_connection(
                dry_run=cfg.dry_run_default,
                prefer_5ghz=cfg.wifi_prefer_5ghz,
                disable_powersave=cfg.wifi_disable_powersave,
                min_hold_sec=min_hold_sec,
            )
            print(f"[{res.interface}] {res.action_taken}")
            for n in res.notes:
                print("  •", n)
            return 0
        aps, meta = wopt.scan_wifi_environment(rescan=True)
        scores = wopt.score_channels(aps)
        print(f"Wi-Fi Interface: {meta.get('interface')} | PowerSave: {meta.get('powersave')} | APs: {len(aps)}")
        for band_name in ("2.4GHz", "5GHz", "6GHz"):
            rec = next((c for c in scores[band_name] if c.recommended), None)
            if rec:
                print(f"  Best {band_name}: Ch {rec.channel} ({rec.freq_mhz} MHz) — {rec.grade} (Interference {rec.interference_score})")
        for ap in aps[:15]:
            mark = "*" if ap.in_use else " "
            print(f"  {mark} [{ap.band}] Ch {ap.channel:<3} ({ap.signal_pct:>3}%) {ap.ssid} ({ap.bssid})")
        return 0

    if args.scan_rf_waves:
        import time as _time
        from .core.rf_bandwidth_scanner import BandwidthAndRfScanner

        rf = BandwidthAndRfScanner(journal)
        rf.sample_bandwidth_only()
        _time.sleep(0.15)
        snap = rf.scan_all(rescan_rf=True, include_ping=True)
        print(
            f"Bandwidth & RF Waves: RX {snap.total_rx_mbps:.3f} Mbps | TX {snap.total_tx_mbps:.3f} Mbps | "
            f"Max Link {snap.max_link_mbps or '--'} Mbps | Latency {snap.latency_ms:.1f} ms (Jitter {snap.jitter_ms:.1f} ms)"
        )
        print(
            f"  RF Density -> 2.4 GHz: {snap.rf_24_density:.0f}% | 5 GHz: {snap.rf_5_density:.0f}% | "
            f"6 GHz: {snap.rf_6_density:.0f}% | Bluetooth: {snap.bt_count}"
        )
        for iface in snap.interfaces[:8]:
            print(f"  NIC {iface.name:<10} up={iface.is_up} rx={iface.rx_mbps:.3f}Mbps tx={iface.tx_mbps:.3f}Mbps err={iface.drop_err_count}")
        return 0

    if args.scan_catalog or args.passive_check:
        from .core.system_catalog_scanner import SystemCatalogAndHealer

        scanner = SystemCatalogAndHealer(cfg_mgr, rollback_mgr, journal)
        if args.scan_catalog:
            summ = scanner.run_full_scan(
                adapter,
                cfg,
                quiet_autofix=bool(args.catalog_autofix or cfg.catalog_quiet_autofix),
                dry_run=cfg.dry_run_default,
            )
        else:
            summ = scanner.run_passive_batch(
                adapter,
                cfg,
                quiet_autofix=bool(args.catalog_autofix or cfg.catalog_quiet_autofix),
                check_new_programs=cfg.catalog_watch_new_apps,
                dry_run=cfg.dry_run_default,
            )
        print(
            f"System Catalog ({summ.mode}): {len(summ.drives_scanned)} drive(s) | "
            f"{summ.files_checked_this_pass} checked this pass | {summ.files_cataloged} total cataloged | "
            f"{summ.programs_cataloged} programs | {summ.quiet_fixes_applied} quiet fix(es) in {summ.elapsed_sec:.2f}s"
        )
        if summ.newly_installed_programs:
            print(f"  New programs cataloged: {', '.join(summ.newly_installed_programs[:12])}")
        for it in summ.issues_found[:15]:
            print(f"  [{it.status.upper()}] {it.title}: {it.target_path}")
        return 0

    if args.conflicts:
        from .core.pc_extras import ConflictProgramDetector

        cd = ConflictProgramDetector(journal)
        found = cd.scan_conflicts()
        print(f"Conflicting Background Programs ({len(found)} detected):")
        for cf in found:
            print(f"  [{cf.severity.upper()}] {cf.name} ({cf.service_unit or f'PID {cf.pid}'}) — {cf.reason}")
        return 0

    if args.list_modules:
        from .core.modules import ASTRA_MODULES, INSTALL_BUNDLE_PRESETS, normalize_enabled_modules

        active = set(normalize_enabled_modules(getattr(cfg, "enabled_modules", None)))
        print(f"AstraCradle Modules (Bundle: {getattr(cfg, 'install_bundle_preset', 'Full Suite (Everything)')}):")
        for idx, m in enumerate(ASTRA_MODULES, start=1):
            st = "LOCKED ON" if m.locked else ("ENABLED" if m.module_id in active else "DISABLED")
            print(f"  {idx}. [{st}] {m.module_id} — {m.name} ({m.footprint_label})")
        print("\nBundle Presets (--modules <key>):")
        for pname, spec in INSTALL_BUNDLE_PRESETS.items():
            print(f"  - {spec['short_key']}: {pname} ({len(spec['modules'])} modules) — {spec['tagline']}")
        return 0

    if args.modules:
        from .core.modules import resolve_preset_or_modules
        from .core.updater import ModuleAndUpdateManager

        mods, pname = resolve_preset_or_modules(args.modules)
        cfg.apply_module_selection(mods, preset_name=pname, sync_simple_mode=True)
        cfg.setup_wizard_completed = True
        cfg_mgr.save(cfg)
        ModuleAndUpdateManager(cfg_mgr, journal).record_install_memory(
            install_method="cli_modules",
            step_note=f"Selected {len(mods)} module(s) ({pname})",
            config=cfg,
        )
        print(f"Configured {len(mods)} AstraCradle module(s) [{pname}]: {', '.join(mods)}")
        return 0

    if args.setup_wizard:
        from .core.modules import (
            ASTRA_MODULES,
            INSTALL_BUNDLE_PRESETS,
            normalize_enabled_modules,
            resolve_preset_or_modules,
        )
        from .core.updater import ModuleAndUpdateManager

        preset_list = list(INSTALL_BUNDLE_PRESETS.items())
        print("============================================================")
        print("  AstraCradle — Modular First-Install Setup Wizard (CLI)")
        print("============================================================")
        for i, (pname, spec) in enumerate(preset_list, start=1):
            print(f"  [{i}] {pname} ({len(spec['modules'])}/{len(ASTRA_MODULES)} modules) — {spec['tagline']}")
        print(f"  [{len(preset_list) + 1}] Custom (Pick individual modules)")

        if sys.stdin.isatty():
            choice = input(f"Choose bundle [1-{len(preset_list) + 1}, default 1]: ").strip() or "1"
            if choice.isdigit() and 1 <= int(choice) <= len(preset_list):
                pname, spec = preset_list[int(choice) - 1]
                mods = list(spec["modules"])
            elif choice.isdigit() and int(choice) == len(preset_list) + 1:
                print("\nAvailable optional modules (core_diagnostics is always enabled):")
                opt_mods = [m for m in ASTRA_MODULES if not m.locked]
                for j, m in enumerate(opt_mods, start=1):
                    print(f"  {j}. {m.module_id} — {m.name}")
                raw_nums = input("Enter numbers or module IDs to ENABLE (comma-separated, or 'all'): ").strip() or "all"
                if raw_nums.lower() == "all":
                    mods, pname = resolve_preset_or_modules("all")
                else:
                    picked: list[str] = []
                    for tok in raw_nums.split(","):
                        t = tok.strip()
                        if t.isdigit() and 1 <= int(t) <= len(opt_mods):
                            picked.append(opt_mods[int(t) - 1].module_id)
                        elif t:
                            picked.append(t)
                    mods = normalize_enabled_modules(picked)
                    pname = "Custom"
            else:
                mods, pname = resolve_preset_or_modules(choice)

            def _ask_yn(prompt: str, default_yes: bool = True) -> bool:
                tag = "[Y/n]" if default_yes else "[y/N]"
                ans = input(f"  {prompt} {tag}: ").strip().lower()
                if not ans:
                    return default_yes
                return ans.startswith("y")

            print("\n--- Plain-English Permissions (100% local, zero cloud uploads) ---")
            p_mode = input("Permissions: [1] Recommended (scan health/apps/caches)  [2] Allow All  [3] Minimal  [4] Ask each [1]: ").strip() or "1"
            if p_mode == "2":
                perms = {
                    "consent_telemetry": True, "consent_process_scan": True, "consent_installed_apps": True,
                    "consent_bloat_scan": True, "consent_privacy_scan": True, "consent_crash_diagnostics": True,
                    "consent_auto_remediate": True, "consent_wifi_auto_channel": True, "consent_auto_install_deps": True,
                }
            elif p_mode == "3":
                perms = {
                    "consent_telemetry": False, "consent_process_scan": False, "consent_installed_apps": False,
                    "consent_bloat_scan": False, "consent_privacy_scan": False, "consent_crash_diagnostics": False,
                    "consent_auto_remediate": False, "consent_wifi_auto_channel": False, "consent_auto_install_deps": False,
                }
            elif p_mode == "4":
                perms = {
                    "consent_telemetry": _ask_yn("Allow reading PC hardware & health sensors?", True),
                    "consent_process_scan": _ask_yn("Allow checking running apps & idle AI helpers?", True),
                    "consent_installed_apps": _ask_yn("Allow inspecting installed apps & startup items?", True),
                    "consent_bloat_scan": _ask_yn("Allow scanning temp files & oversized caches?", True),
                    "consent_privacy_scan": _ask_yn("Allow scanning browser caches (keeps passwords/logins)?", False),
                    "consent_crash_diagnostics": _ask_yn("Allow reading local crash & freeze clues?", True),
                    "consent_auto_remediate": _ask_yn("Allow auto-cleaning safe stale caches to Undo Vault?", True),
                    "consent_wifi_auto_channel": _ask_yn("Allow auto-switching to a cleaner Wi-Fi channel?", False),
                    "consent_auto_install_deps": _ask_yn("Allow auto-installing missing helper tools when needed?", False),
                }
            else:
                perms = {
                    "consent_telemetry": True, "consent_process_scan": True, "consent_installed_apps": True,
                    "consent_bloat_scan": True, "consent_privacy_scan": False, "consent_crash_diagnostics": True,
                    "consent_auto_remediate": True, "consent_wifi_auto_channel": False, "consent_auto_install_deps": False,
                }

            print("\n--- Everyday Preferences ---")
            prof_pick = input("PC Mode: [1] Everyday Balanced  [2] Gaming  [3] Battery Saver  [4] Read-Only Safe [1]: ").strip() or "1"
            prof_map = {"1": "Balanced Daily", "2": "Gaming / Low-Latency", "3": "Battery Endurance", "4": "Minimal Safe Mode"}
            wizard_choices: dict[str, Any] = {
                "enabled_modules": mods,
                "preset_name": pname,
                "active_profile": prof_map.get(prof_pick, "Balanced Daily"),
                "simple_mode": _ask_yn("Keep menus simple and beginner-friendly?", True),
                "show_tray_icon": _ask_yn("Show AstraCradle icon in the system tray?", True),
                "launch_at_login": _ask_yn("Start AstraCradle automatically when you log in?", False),
                "confirm_important_prompts": _ask_yn("Ask Yes/No before making major system or Wi-Fi changes?", True),
                **perms,
            }
            cfg.apply_wizard_choices(wizard_choices)
            try:
                LifecycleManager(cfg_mgr, rollback_mgr, journal).set_launch_at_login(bool(cfg.launch_at_login))
            except Exception:
                pass
        else:
            mods = list(cfg.enabled_modules)
            pname = str(getattr(cfg, "install_bundle_preset", "Full Suite (Everything)"))
            cfg.apply_module_selection(mods, preset_name=pname, sync_simple_mode=True)
            cfg.setup_wizard_completed = True
            cfg.consent_prompted = True

        cfg_mgr.save(cfg)
        ModuleAndUpdateManager(cfg_mgr, journal).record_install_memory(
            install_method="cli_setup_wizard",
            step_note=f"Setup Wizard configured {len(mods)} module(s) ({pname})",
            config=cfg,
        )
        print(f"All choices saved & configured: {pname} ({cfg.active_profile}) -> {', '.join(mods)}")
        return 0

    if args.comprehensive_setup:
        from .core.config import COMPREHENSIVE_SETUPS

        preset = args.comprehensive_setup
        if preset not in COMPREHENSIVE_SETUPS:
            print(f"Available comprehensive setups: {', '.join(COMPREHENSIVE_SETUPS.keys())}")
            return 1
        applied = cfg.apply_comprehensive_setup(preset)
        cfg_mgr.save(cfg)
        print(f"Applied Comprehensive Setup '{preset}' ({len(applied)} settings).")
        return 0

    if args.export_crash_log:
        from dataclasses import asdict
        from .core.adblock_shield import AstraShieldManager
        from .core.lan_tv_shield import LanTvAdShield
        from .core.safeguard_plus import AIDiagnosticArchiveExporter

        sh = AstraShieldManager(cfg_mgr, rollback_mgr, journal)
        tv = LanTvAdShield(journal)
        events, audits = self_heal.run_self_check(adapter, cfg)
        res = AIDiagnosticArchiveExporter(cfg_mgr, journal).export_forensic_crash_log(
            config_obj=cfg,
            self_heal_events=events,
            self_audit=audits,
            shield_status=asdict(sh.status()),
            tv_failsafe_state=tv.load_failsafe_state(),
        )
        print(f"Paste-Ready Forensic & Crash Log ({res['line_count']} lines) -> {res['path']}")
        print(f"Latest mirror -> {res['latest_path']}")
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


def _ensure_valid_stdio(cfg_dir: str | None = None) -> None:
    """Windows pythonw.exe / gui-script wrappers set sys.stdout & sys.stderr to None; redirect safely."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        base = Path(cfg_dir).expanduser() if cfg_dir else (Path.home() / ".astracradle")
        log_dir = base / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "gui_stdio.log", "a", encoding="utf-8", errors="replace")
    except Exception:
        import os as _os
        log_file = open(_os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log_file
    if sys.stderr is None:
        sys.stderr = log_file


def main(argv: list[str] | None = None) -> int:
    _ensure_valid_stdio()
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
        choices=["on", "auto-on", "off", "status", "sync-pihole", "youtube-prune"],
        default=None,
        help="Toggle, configure auto-start, sync Pi-hole lists, export YouTube ad-pruner, or inspect AstraShield.",
    )
    parser.add_argument("--scan-usb", action="store_true", help="List all USB & peripheral hardware and run BadUSB check.")
    parser.add_argument("--scan-tv", action="store_true", help="Discover Smart TVs, network devices & Bluetooth devices for easy connection.")
    parser.add_argument("--tv-restore", action="store_true", help="Failsafe reset: restore any connected TV DNS/ADB overrides back to stock.")
    parser.add_argument("--wifi-scan", action="store_true", help="Scan Wi-Fi spectrum and rank cleanest 2.4/5/6 GHz channels.")
    parser.add_argument("--wifi-recommend", action="store_true", help="Recommend less-trafficked Wi-Fi channel and prompt Yes/No to apply.")
    parser.add_argument("--wifi-optimize", action="store_true", help="Optimize Wi-Fi connection to cleanest channel/BSSID & disable power-save.")
    parser.add_argument("--wifi-watch", action="store_true", help="Run periodic Wi-Fi traffic congestion watchdog loop.")
    parser.add_argument("--wifi-install-cachyos", action="store_true", help="Install CachyOS Fish shell functions, systemd user timer & NM on-connect hook.")
    parser.add_argument("--wifi-restore", action="store_true", help="Restore NetworkManager Wi-Fi band/BSSID/power-save defaults.")
    parser.add_argument("--scan-rf-waves", action="store_true", help="Scan live RX/TX bandwidth & 2.4/5/6 GHz + Bluetooth radio waves.")
    parser.add_argument("--scan-catalog", action="store_true", help="Scan all mounted drives & programs into SQLite catalog and check Known-Issues DB.")
    parser.add_argument("--passive-check", action="store_true", help="Run low-CPU passive incremental file check & detect newly installed programs.")
    parser.add_argument("--catalog-autofix", action="store_true", help="Quietly auto-fix safe known issues during catalog/passive scan.")
    parser.add_argument("--conflicts", action="store_true", help="Check for overlapping DNS, fan, or optimizer tools.")
    parser.add_argument("--export-crash-log", action="store_true", help="Export a paste-ready Forensic & Crash Vault log file.")
    parser.add_argument("--comprehensive-setup", type=str, default=None, help="Apply a comprehensive studio setup preset (e.g. 'Recommended All-Rounder').")
    parser.add_argument("--list-modules", action="store_true", help="List modular AstraCradle features, enabled state, and bundle presets.")
    parser.add_argument("--modules", type=str, default=None, help="Enable specific modules (comma-separated IDs or preset: full, essential, streamer, privacy, minimal).")
    parser.add_argument("--setup-wizard", action="store_true", help="Run the interactive terminal modular setup wizard.")
    parser.add_argument("--gui-setup-wizard", action="store_true", help="Launch the GUI and open the step-by-step Modular Setup Wizard.")
    parser.add_argument("--export-ai-bundle", action="store_true", help="Consolidate old logs and export an AI-ready diagnostic report.")
    parser.add_argument("--minimized", action="store_true", help="Start AstraCradle minimized in the bottom-right system tray.")
    parser.add_argument("--quit", action="store_true", help="Gracefully close any running AstraCradle GUI instance and exit.")
    parser.add_argument("--widget", action="store_true", help="Launch AstraCradle with the cross-OS floating Desktop Widget active.")
    parser.add_argument("--widget-status", action="store_true", help="Print Waybar/Polybar-ready JSON telemetry status and exit.")
    parser.add_argument("--bufferbloat-test", action="store_true", help="Measure gateway/DNS idle vs loaded RTT and grade bufferbloat.")
    parser.add_argument("--export-profile", type=str, default=None, help="Export checksummed .astracradle-profile backup archive.")
    parser.add_argument("--import-profile", type=str, default=None, help="Import checksummed .astracradle-profile backup archive.")
    parser.add_argument("--tailscale-status", action="store_true", help="Inspect Tailscale mesh IP, peers & Sunshine/Moonlight readiness.")
    parser.add_argument("--beam-host", action="store_true", help="Run AstraBeam Tailscale remote desktop & MJPEG stream host.")
    parser.add_argument("--beam-connect", type=str, default=None, help="Connect/test pairing with remote AstraBeam host (IP[:port]).")
    parser.add_argument("--beam-pin", type=str, default="", help="Pairing PIN for --beam-host or --beam-connect.")
    parser.add_argument("--benchmark-report", action="store_true", help="Record weekly benchmark snapshot & detect RAM/health regressions.")
    parser.add_argument("--setup-reboot", action="store_true", help="Prompt for sudo/admin password and install automated boot persistence for all saved toggles.")
    parser.add_argument("--apply-boot-toggles", action="store_true", help="Re-apply all saved AstraShield, Wi-Fi, and Script toggles (used on OS reboot).")
    parser.add_argument("--config-dir", type=str, default=None, help="Override AstraCradle config/state directory.")
    parser.add_argument("--self-check", action="store_true", help="Run silent 7-invariant self-heal & forensic audit.")
    parser.add_argument("--install-shortcuts", action="store_true", help="Create App Menu shortcuts (add --desktop-icon to also place a Desktop icon).")
    parser.add_argument("--desktop-icon", action="store_true", help="Include a Desktop shortcut when running --install-shortcuts.")
    parser.add_argument("--no-desktop-icon", action="store_true", help="Explicitly remember menu-only shortcuts without a Desktop icon.")
    parser.add_argument("--record-install", action="store_true", help="Persist machine install/update memory for seamless future updates.")
    parser.add_argument("--reapply-memory", action="store_true", help="Re-apply previously remembered setup steps on this machine.")
    parser.add_argument("--show-install-memory", action="store_true", help="Print persistent machine update memory.")
    parser.add_argument("--install-method", type=str, default=None, help="Installer method tag stored in machine memory.")
    parser.add_argument("--install-dir", type=str, default=None, help="Install/source directory stored in machine memory.")
    parser.add_argument("--mesh-status", action="store_true", help="Inspect PC Wi-Fi radios for AP+STA / 802.11s / Dual-Radio Mesh capability.")
    parser.add_argument("--mesh-plan", type=str, default=None, help="Generate a PC Wi-Fi Mesh plan from a source Wi-Fi SSID/interface or Ethernet uplink.")
    parser.add_argument("--mesh-ssid", type=str, default=None, help="Mesh Array SSID name for --mesh-plan.")
    parser.add_argument("--mesh-band", type=str, default=None, help="Preferred Mesh band ('5 GHz', '2.4 GHz', '6 GHz') for --mesh-plan.")
    parser.add_argument("--check-updates", action="store_true", help="Check GitHub repo first, then local bundles, and apply updates.")
    parser.add_argument("--set-update-repo", type=str, default=None, help="Set default GitHub repo URL checked before local updates.")
    parser.add_argument("--install-update", type=str, default=None, help="Install a newer AstraCradle-vX.Y.Z-bundle.zip/.tar.gz update.")
    parser.add_argument("--uninstall", action="store_true", help="Run safe interactive clean uninstaller.")
    parser.add_argument("--delete-data", action="store_true", help="Used with --uninstall --yes to delete all profiles & data.")
    parser.add_argument("--uninstall-package", action="store_true", help="Used with --uninstall to pip-uninstall astracradle.")
    parser.add_argument("--yes", action="store_true", help="Confirm non-interactive uninstall.")
    parser.add_argument("--commands-list", action="store_true", help="List install & custom commands stored in commands.db.")
    parser.add_argument("--deps-status", action="store_true", help="Show Dependency Library status with Linux Bash/Fish install commands.")
    parser.add_argument("--failover-status", action="store_true", help="Show Ethernet/Wi-Fi failover configuration & state.")
    parser.add_argument("--failover-tick", action="store_true", help="Run one immediate ping & failover evaluation step.")
    parser.add_argument("--failover-watch", action="store_true", help="Continuously run Ethernet/Wi-Fi failover & 5-minute recovery loop.")
    parser.add_argument("--install-deps", action="store_true", help="Auto-install & setup missing companion programs (requires --yes permission).")
    parser.add_argument("--shell-kind", type=str, choices=["bash", "fish"], default=None, help="Preferred shell syntax for dependency installer.")
    parser.add_argument("--help-topic", type=str, default=None, help="Search offline troubleshooting knowledge base by keyword.")
    parser.add_argument("--lookup-pid", type=str, default=None, help="Look up a numeric PID# or process name for a plain-English web/KB description.")
    parser.add_argument("--no-web", action="store_true", help="Disable live web search fallback when using --lookup-pid.")
    parser.add_argument("--proximity-connect", action="store_true", help="Auto-connect paired/designated iOS, Watch & Android devices.")
    parser.add_argument("--hermes-status", action="store_true", help="Show Hermes OS AI status, GPU-matched local model, and free cloud recommendation.")
    parser.add_argument("--hermes-setup", action="store_true", help="Automatically install & set up Hermes OS AI (local GPU Ollama or free/own cloud API).")
    parser.add_argument("--hermes-local-only", action="store_true", help="Lock Hermes OS AI to 100% offline local-only GPU mode (Ollama).")
    parser.add_argument("--hermes-ask", type=str, default=None, help="Run a single prompt through native Hermes OS AI with AstraCradle tools.")
    parser.add_argument("--hermes-mode", type=str, choices=["local_gpu", "free_cloud", "custom_api"], default=None, help="Hermes backend mode.")
    parser.add_argument("--hermes-provider", type=str, default=None, help="Hermes provider (ollama, openrouter_free, gemini_free, groq_free, nous_portal, openai, anthropic, custom).")
    parser.add_argument("--hermes-model", type=str, default=None, help="Override Hermes model tag.")
    parser.add_argument(
        "--hermes-permission",
        type=str,
        choices=["read_only", "safe_user", "standard_user", "sudo_with_prompt"],
        default=None,
        help="Hermes AI system permission level (up to just below sudo, or sudo when password typed).",
    )
    parser.add_argument("--hermes-api-key", type=str, default=None, help="API key to save in ~/.astracradle/hermes/.env during --hermes-setup.")
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
        or args.scan_tv
        or args.tv_restore
        or args.wifi_scan
        or args.wifi_recommend
        or args.wifi_optimize
        or args.wifi_watch
        or args.wifi_install_cachyos
        or args.wifi_restore
        or args.scan_rf_waves
        or args.scan_catalog
        or args.hermes_status
        or args.hermes_setup
        or args.hermes_ask
        or args.passive_check
        or args.conflicts
        or args.export_crash_log
        or args.comprehensive_setup
        or args.list_modules
        or args.modules
        or args.setup_wizard
        or args.export_ai_bundle
        or args.self_check
        or args.install_shortcuts
        or args.record_install
        or args.show_install_memory
        or args.mesh_status
        or args.mesh_plan is not None
        or args.setup_reboot
        or args.apply_boot_toggles
        or args.widget_status
        or args.bufferbloat_test
        or args.export_profile
        or args.import_profile
        or args.tailscale_status
        or args.commands_list
        or args.failover_status
        or args.failover_tick
        or args.failover_watch
        or args.deps_status
        or args.install_deps
        or args.help_topic
        or args.lookup_pid
        or args.proximity_connect
        or args.beam_host
        or args.beam_connect
        or args.benchmark_report
        or args.check_updates
        or args.set_update_repo
        or args.install_update
        or args.uninstall
        or args.apply_preset
        or args.run_pipeline
        or args.quit
    ):
        return run_cli(args)

    cfg_mgr = ConfigManager(base_dir=args.config_dir)
    journal = ForensicJournal(cfg_mgr)
    crash_analyzer = CrashAnalyzer(cfg_mgr, journal)

    def _excepthook(exc_type, exc_val, exc_tb):
        crash_analyzer.record_unhandled_exception(exc_val)
        sys.__excepthook__(exc_type, exc_val, exc_tb)

    sys.excepthook = _excepthook

    from .ui.app import launch_gui

    try:
        launch_gui(
            base_dir=args.config_dir,
            start_minimized=bool(args.minimized),
            start_widget=bool(args.widget),
            open_setup_wizard=bool(args.gui_setup_wizard),
        )
    except Exception as exc:
        import traceback

        crash_path = crash_analyzer.record_unhandled_exception(exc)
        tb = traceback.format_exc()
        sys.stderr.write(f"AstraCradle failed to start: {exc}\nLog: {crash_path}\n{tb}\n")
        if sys.platform.startswith("win"):
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"AstraCradle failed to start:\n{exc}\n\nCrash log saved to:\n{crash_path}\n\nTry running run_astracradle.bat or Install_AstraCradle.bat to repair dependencies.",
                    "AstraCradle Startup Error",
                    0x10,
                )
            except Exception:
                pass
        return 1
    return 0


def wifi_entry() -> int:
    """Standalone console entrypoint `astracradle-wifi` for Fish/CachyOS shells."""
    argv = sys.argv[1:] or ["--wifi-optimize"]
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
