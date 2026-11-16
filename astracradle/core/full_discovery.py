"""First-Load & On-Demand Full Local Computer Discovery Engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform
from typing import Any

from .advanced_controls import FullscreenFocusDetector, GpuPowerAndFanController
from .autoruns_audit import AutorunsAndGhostAuditor
from .bloat_analyzer import BloatAnalyzer
from .config import AppConfig, ConfigManager
from .diagnostics import DiagnosticReport, DiagnosticsEngine
from .hardware_library import HardwareLibraryManager
from .journal import ForensicJournal
from .latency_guard import InterruptAndDpcGuard, LatencyReport
from .models import Playbook, SystemSnapshot, utc_now_iso
from .os_adapters import BaseOSAdapter
from .perf_guardian import DeviceGlitchAdvisor, NetworkAndDnsAdvisor
from .playbook import PlaybookBuilder
from .rollback import RollbackManager
from .safeguard_plus import ConfigIntegrityShield
from .storage_deep import DevAndAIMoVaultScanner, SmartDiskMonitor
from .telemetry import TelemetryCollector, format_bytes
from .telemetry_history import (
    DriverUpdateCorrelator,
    GpuTelemetryCollector,
    PreFlightSnapshotHook,
    TelemetryHistoryDB,
)
from .thermal_spc import ThermalSpcMonitor


@dataclass
class FullSystemBaseline:
    discovered_at: str
    os_platform: str
    hostname: str
    cpu_model: str
    cpu_cores: str
    ram_total_gb: float
    gpu_name: str
    gpu_available: bool
    storage_summary: str
    smart_disks: list[dict[str, str]] = field(default_factory=list)
    health_score: int = 100
    reclaimable_bytes: int = 0
    findings_count: int = 0
    installed_apps_count: int = 0
    autoruns_count: int = 0
    ghost_drivers_count: int = 0
    irq_sources_count: int = 0
    dpc_p95_us: float = 0.0
    dpc_grade: str = "Clean"
    network_apps_count: int = 0
    hosts_status: str = "clean"
    device_checks_count: int = 0
    dev_vault_count: int = 0
    recent_os_changes_count: int = 0
    integrity_status: str = "ok"
    active_window: str = ""
    preflight_status: str = ""
    subsystems_checked: list[str] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)
    baseline_json_path: str = ""


@dataclass
class FullDiscoveryOutput:
    baseline: FullSystemBaseline
    snapshot: SystemSnapshot
    report: DiagnosticReport
    playbook: Playbook
    latency_report: LatencyReport
    autoruns: list
    ghost_drivers: list


class FullSystemDiscoveryEngine:
    """Runs a comprehensive, consent-gated local discovery sweep across all AstraCradle subsystems."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.rollback_mgr = rollback_mgr
        self.journal = journal
        self.baseline_path = cfg_mgr.state_dir / "initial_pc_baseline.json"

    def load_saved_baseline(self) -> FullSystemBaseline | None:
        if not self.baseline_path.exists():
            return None
        try:
            raw = json.loads(self.baseline_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            valid = {f.name for f in FullSystemBaseline.__dataclass_fields__.values()}
            return FullSystemBaseline(**{k: v for k, v in raw.items() if k in valid})
        except Exception:
            return None

    def save_baseline(self, baseline: FullSystemBaseline) -> Path:
        self.cfg_mgr.ensure_directories()
        baseline.baseline_json_path = str(self.baseline_path)
        tmp = self.baseline_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(baseline), indent=2), encoding="utf-8")
        os.replace(tmp, self.baseline_path)
        return self.baseline_path

    def run_full_discovery(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        history_db: TelemetryHistoryDB | None = None,
        thermal_spc: ThermalSpcMonitor | None = None,
    ) -> FullDiscoveryOutput:
        checked: list[str] = []

        # 1. Core Telemetry (CPU, RAM, Disk, Network, DNS, Processes)
        collector = TelemetryCollector(config.excluded_processes)
        snap = collector.collect(
            adapter,
            include_dns=config.consent_telemetry,
            consent_telemetry=config.consent_telemetry,
            consent_processes=config.consent_process_scan,
        )
        checked.append("Core Telemetry & DNS")

        # 2. Hardware Passport & GPU/VRAM/Power Telemetry
        hw_mgr = HardwareLibraryManager(self.cfg_mgr, self.journal)
        passport = hw_mgr.detect_passport()
        gpu = GpuTelemetryCollector.collect()
        _gpu_live = GpuPowerAndFanController.probe_live_power_and_fan()
        checked.append("Hardware Passport & GPU/VRAM")

        # 3. 8-Subsystem Diagnostics & Evidence/Confidence-Scored Playbook
        report = DiagnosticsEngine().run_scan(snap, adapter, config)
        playbook = PlaybookBuilder().build(report, config)
        checked.append("8-Subsystem Diagnostics & Playbook")

        # 4. SMART / NVMe Disk Health & AI/Dev Vault
        smart_disks = SmartDiskMonitor().inspect_disks()
        dev_vault = DevAndAIMoVaultScanner().scan(limit=12) if config.consent_bloat_scan else []
        checked.append("SMART Disk Health & AI/Dev Vault")

        # 5. Installed Applications Audit
        apps = BloatAnalyzer().scan_installed_apps(config, limit=40) if config.consent_installed_apps else []
        checked.append("Installed Applications Inventory")

        # 6. Autoruns (Startup, Scheduled Tasks, Services, Drivers) & Ghost Drivers
        auditor = AutorunsAndGhostAuditor(self.cfg_mgr, self.rollback_mgr, self.journal)
        autoruns = auditor.scan_autoruns(adapter) if config.consent_installed_apps else []
        ghosts = auditor.scan_ghost_drivers() if config.consent_telemetry else []
        checked.append("Autoruns & Ghost Driver Audit")

        # 7. Interrupt Affinity & DPC/Timer Latency Probe
        latency_guard = InterruptAndDpcGuard(self.cfg_mgr, self.journal)
        lat_rep = latency_guard.measure_dpc_latency(samples=16, sleep_us=250)
        checked.append("DPC Latency & IRQ Sources")

        # 8. Network Apps, Hosts Integrity, USB/Power Glitches, Focus Guard, Pre-Flight & 48h Updates
        net_apps = NetworkAndDnsAdvisor.sample_network_apps(limit=10) if config.consent_telemetry else []
        hosts_res = NetworkAndDnsAdvisor.audit_hosts_file()
        dev_checks = DeviceGlitchAdvisor.inspect()
        focus = FullscreenFocusDetector.detect()
        _ok_pf, pf_msg = PreFlightSnapshotHook.check_or_prepare(dry_run=True)
        recent_changes = DriverUpdateCorrelator.recent_changes(limit=8) if config.consent_crash_diagnostics else []
        shield = ConfigIntegrityShield(self.cfg_mgr, self.journal).check_or_init()
        shield_status = "modified" if any(s.status == "modified" for s in shield) else "clean"
        checked.append("Network, Hosts, Shield, Focus & 48h Driver Changes")

        # 9. Seed initial Multi-Res Telemetry & Thermal SPC baseline sample
        if history_db is not None:
            history_db.record_tick(snap, playbook.health_score, persist_db=True)
        if thermal_spc is not None and config.thermal_spc_enabled:
            tier = thermal_spc.classify_workload(snap.cpu_percent, gpu.utilization_percent, focus.is_fullscreen)
            thermal_spc.update_sample(tier, snap.temperature_c, snap.cpu_percent)
        checked.append("Telemetry History & Thermal SPC Seed")

        summary_lines = [
            f"System: {snap.hostname} · {passport.os_platform}",
            f"CPU: {passport.cpu_model} ({passport.cpu_physical_cores}C/{passport.cpu_logical_cores}T) · Current Load {snap.cpu_percent:.1f}%",
            f"Memory: {format_bytes(snap.mem_used_bytes)} / {format_bytes(snap.mem_total_bytes)} ({snap.mem_percent:.1f}% used)",
            f"GPU: {gpu.name if gpu.available else passport.gpu_summary}",
            f"Storage & SMART: {passport.storage_summary} · {len(smart_disks)} disk(s) checked · Reclaimable {format_bytes(playbook.total_reclaimable_bytes)}",
            f"Equilibrium Score: {playbook.health_score}/100 across {len(report.findings)} diagnostic finding(s)",
            f"Autoruns & Apps: {len(apps)} installed app(s), {len(autoruns)} autorun/service entry(s), {len(ghosts)} ghost driver item(s)",
            f"Latency & Network: DPC p95 {lat_rep.p95_us} us ({lat_rep.grade}) · {len(lat_rep.irq_sources)} IRQ(s) · Hosts {hosts_res.status.upper()} · {len(net_apps)} active net app(s)",
        ]

        baseline = FullSystemBaseline(
            discovered_at=utc_now_iso(),
            os_platform=passport.os_platform,
            hostname=snap.hostname,
            cpu_model=passport.cpu_model,
            cpu_cores=f"{passport.cpu_physical_cores}C/{passport.cpu_logical_cores}T",
            ram_total_gb=round(passport.ram_total_gb, 2),
            gpu_name=gpu.name if gpu.available else passport.gpu_summary,
            gpu_available=gpu.available,
            storage_summary=passport.storage_summary,
            smart_disks=[{"device": d.device, "status": d.status, "summary": d.summary} for d in smart_disks],
            health_score=playbook.health_score,
            reclaimable_bytes=playbook.total_reclaimable_bytes,
            findings_count=len(report.findings),
            installed_apps_count=len(apps),
            autoruns_count=len(autoruns),
            ghost_drivers_count=len(ghosts),
            irq_sources_count=len(lat_rep.irq_sources),
            dpc_p95_us=lat_rep.p95_us,
            dpc_grade=lat_rep.grade,
            network_apps_count=len(net_apps),
            hosts_status=hosts_res.status,
            device_checks_count=len(dev_checks),
            dev_vault_count=len(dev_vault),
            recent_os_changes_count=len(recent_changes),
            integrity_status=shield_status,
            active_window=f"{focus.window_title} ({focus.process_name})",
            preflight_status=pf_msg,
            subsystems_checked=checked,
            summary_lines=summary_lines,
        )
        self.save_baseline(baseline)

        self.journal.log_event(
            component="FullSystemDiscovery",
            action="run_full_discovery",
            how=f"Checked {len(checked)} local subsystems on {platform.system()} ({snap.hostname})",
            why="User granted permission for comprehensive local baseline discovery",
            outcome="ok",
            details=f"Score={playbook.health_score}/100, apps={len(apps)}, autoruns={len(autoruns)}, ghosts={len(ghosts)}",
        )
        return FullDiscoveryOutput(
            baseline=baseline,
            snapshot=snap,
            report=report,
            playbook=playbook,
            latency_report=lat_rep,
            autoruns=autoruns,
            ghost_drivers=ghosts,
        )
