"""Deep 8-subsystem OS diagnostics engine (including AI Chatbot & Idle Bloat analysis)."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig, SITUATION_PROFILES
from .models import (
    CacheTarget,
    DiagnosticFinding,
    ProcessRecommendation,
    StartupItem,
    SystemSnapshot,
)
from .os_adapters import BaseOSAdapter
from .process_advisor import ProcessAdvisor
from .telemetry import format_bytes, format_uptime


@dataclass
class DiagnosticReport:
    snapshot: SystemSnapshot
    health_score: int
    cache_targets: list[CacheTarget]
    startup_items: list[StartupItem]
    recommendations: list[ProcessRecommendation]
    findings: list[DiagnosticFinding]
    power_info: dict[str, str]


class DiagnosticsEngine:
    """Evaluates 8 core OS subsystems and emits safe, actionable findings."""

    def __init__(self) -> None:
        self.process_advisor = ProcessAdvisor()

    def run_scan(
        self,
        snapshot: SystemSnapshot,
        adapter: BaseOSAdapter,
        config: AppConfig,
    ) -> DiagnosticReport:
        findings: list[DiagnosticFinding] = []
        cache_targets = adapter.discover_cache_targets()
        startup_items = adapter.discover_startup_items()
        power_info = adapter.power_profile_info()
        recommendations = self.process_advisor.analyze(
            snapshot.top_processes, startup_items, config
        )
        prof = SITUATION_PROFILES.get(config.active_profile, SITUATION_PROFILES["Balanced Daily"])

        # 1. Storage & Cache Hygiene
        reclaimable_safe = sum(
            t.size_bytes for t in cache_targets if t.exists and t.risk in ("zero", "low")
        )
        if reclaimable_safe > 15 * 1024 * 1024:
            sev = "medium" if reclaimable_safe > 512 * 1024 * 1024 else "low"
            penalty = 12 if sev == "medium" else 6
            target_paths = [
                t.path for t in cache_targets if t.exists and t.risk in ("zero", "low") and t.size_bytes > 0
            ]
            findings.append(
                DiagnosticFinding(
                    finding_id="storage_cache_bloat",
                    subsystem="storage",
                    title=f"Reclaimable Cache & Temp Footprint ({format_bytes(reclaimable_safe)})",
                    severity=sev,
                    summary=(
                        f"Detected {format_bytes(reclaimable_safe)} in safe user caches and temporary files."
                    ),
                    evidence=", ".join(target_paths[:3]) or "User temp & cache directories",
                    impact_score=penalty,
                    reclaimable_bytes=reclaimable_safe,
                    action_id="clean_temp_caches",
                    action_params={"target_ids": [t.target_id for t in cache_targets if t.selected_by_default]},
                    safety_tier="guarded",
                    risk_level="low",
                    manual_steps=[
                        "Inspect ~/.cache, thumbnails, or OS temp folder sizes.",
                        "Quarantine stale temporary artifacts rather than deleting blindly.",
                    ],
                    manual_commands=[f"# Review size of {p}" for p in target_paths[:3]]
                    + adapter.trim_or_disk_health_manual()[:1],
                    can_auto_fix=True,
                )
            )

        # Disk headroom floor
        for part in snapshot.partitions:
            free_pct = 100.0 - part.percent
            if free_pct < config.disk_safety_floor_percent:
                findings.append(
                    DiagnosticFinding(
                        finding_id=f"disk_pressure_{part.mountpoint}",
                        subsystem="storage",
                        title=f"Low Free Disk Headroom on {part.mountpoint} ({free_pct:.1f}% free)",
                        severity="high" if free_pct < 5.0 else "medium",
                        summary=(
                            f"Volume {part.mountpoint} has only {format_bytes(part.free_bytes)} free."
                        ),
                        evidence=f"{part.device} ({part.fstype}): {part.percent}% used",
                        impact_score=18 if free_pct < 5.0 else 10,
                        action_id="prune_quarantine",
                        safety_tier="manual_review",
                        risk_level="moderate",
                        manual_steps=[
                            "Purge expired Quarantine items or large unused Downloads.",
                            "Run OS SSD TRIM / storage sense diagnostics.",
                        ],
                        manual_commands=adapter.trim_or_disk_health_manual(),
                        can_auto_fix=True,
                    )
                )

        # 2. AI Chatbots & Idle Helper Bloat
        ai_recs = [r for r in recommendations if r.category == "ai_chatbot"]
        if ai_recs:
            total_ai_mb = sum(r.memory_mb for r in ai_recs)
            findings.append(
                DiagnosticFinding(
                    finding_id="ai_chatbot_idle_bloat",
                    subsystem="ai_bloat",
                    title=f"{len(ai_recs)} Idle/Background AI Chatbot Process(es) Detected ({total_ai_mb:.0f} MB)",
                    severity="medium" if total_ai_mb > 300 else "low",
                    summary=(
                        "Detected unused or background local AI chatbot/LLM runtimes consuming RAM/startup slots."
                    ),
                    evidence=", ".join(r.name for r in ai_recs[:4]),
                    impact_score=8,
                    safety_tier="guarded",
                    risk_level="low",
                    manual_steps=[
                        "Open 'AI & Process Hunter' in AstraCradle to inspect each chatbot process.",
                        "Turn off unused chatbots with 1 click or enable the 'Silence Idle AI Chatbots' toggle script.",
                    ],
                    manual_commands=[],
                    can_auto_fix=False,
                )
            )

        # 3. Memory & Swap Equilibrium
        ram_thresh = float(prof.get("ram_pressure_threshold", 85.0))
        if snapshot.mem_percent >= ram_thresh:
            top_mem = ", ".join(f"{p.name} ({p.memory_mb:.0f} MB)" for p in snapshot.top_processes[:3])
            findings.append(
                DiagnosticFinding(
                    finding_id="memory_pressure_high",
                    subsystem="memory",
                    title=f"Elevated RAM Pressure ({snapshot.mem_percent:.1f}% Used)",
                    severity="high" if snapshot.mem_percent > 92.0 else "medium",
                    summary="Available physical memory is tight, which can trigger swap thrashing.",
                    evidence=f"Available: {format_bytes(snapshot.mem_available_bytes)} | Top: {top_mem}",
                    impact_score=16 if snapshot.mem_percent > 92.0 else 10,
                    action_id="reclaim_memory_guidance",
                    safety_tier="manual_review",
                    risk_level="low",
                    manual_steps=[
                        "Open 'AI & Process Hunter' or 'System Modules -> Processes'.",
                        "Turn off idle AI chatbots or unused background apps first.",
                    ],
                    manual_commands=[f"# Top memory consumers: {top_mem}"],
                    can_auto_fix=False,
                )
            )

        # 4. Startup Crowd
        enabled_startups = [s for s in startup_items if s.enabled]
        if len(enabled_startups) >= 6:
            findings.append(
                DiagnosticFinding(
                    finding_id="startup_crowd",
                    subsystem="startup",
                    title=f"{len(enabled_startups)} Active Startup / Login Entries Detected",
                    severity="medium" if len(enabled_startups) > 10 else "low",
                    summary="Multiple login apps launch automatically at boot, lengthening startup time.",
                    evidence=", ".join(s.name for s in enabled_startups[:5]),
                    impact_score=7,
                    safety_tier="manual_review",
                    risk_level="low",
                    manual_steps=[
                        "Open System Modules -> Startup & Services inside AstraCradle.",
                        "Disable non-essential updaters, chat clients, or AI helpers at login.",
                    ],
                    manual_commands=[s.command for s in enabled_startups[:3] if s.command],
                    can_auto_fix=False,
                )
            )

        # 5. CPU & Thermal
        cpu_thresh = float(prof.get("cpu_alert_threshold", 90.0))
        if snapshot.cpu_percent >= cpu_thresh:
            top_cpu = ", ".join(f"{p.name} ({p.cpu_percent:.1f}%)" for p in snapshot.top_processes[:3])
            findings.append(
                DiagnosticFinding(
                    finding_id="cpu_saturation",
                    subsystem="power",
                    title=f"Sustained High CPU Load ({snapshot.cpu_percent:.1f}%)",
                    severity="high",
                    summary="CPU headroom is saturated; foreground responsiveness may suffer.",
                    evidence=f"Top CPU: {top_cpu}",
                    impact_score=14,
                    safety_tier="manual_review",
                    risk_level="low",
                    manual_steps=["Renice background tasks in System Modules -> Processes."],
                    manual_commands=[],
                    can_auto_fix=False,
                )
            )

        if snapshot.temperature_c is not None and snapshot.temperature_c >= 85.0:
            findings.append(
                DiagnosticFinding(
                    finding_id="thermal_warning",
                    subsystem="power",
                    title=f"Elevated Thermal Reading ({snapshot.temperature_c:.1f}°C)",
                    severity="high" if snapshot.temperature_c >= 92.0 else "medium",
                    summary="High thermal output can cause clock throttling or sudden thermal shutdown.",
                    evidence=f"Sensor: {snapshot.temperature_c:.1f}°C | Governor: {power_info.get('governor', 'unknown')}",
                    impact_score=12,
                    safety_tier="manual_review",
                    risk_level="zero",
                    manual_steps=[
                        "Check airflow/fans and turn off aggressive overclock/benchmark scripts.",
                    ],
                    manual_commands=[],
                    can_auto_fix=False,
                )
            )

        # 6. Network & DNS
        if snapshot.dns_latency_ms is not None and snapshot.dns_latency_ms > 150.0:
            findings.append(
                DiagnosticFinding(
                    finding_id="dns_latency_slow",
                    subsystem="network",
                    title=f"Sluggish DNS Resolution ({snapshot.dns_latency_ms:.0f} ms)",
                    severity="low",
                    summary="Slow DNS lookups delay initial web and API handshakes.",
                    evidence=f"Lookup probe took {snapshot.dns_latency_ms:.0f} ms",
                    impact_score=5,
                    action_id="flush_dns_cache",
                    safety_tier="safe_auto",
                    risk_level="zero",
                    manual_steps=["Flush the local OS DNS resolver cache."],
                    manual_commands=adapter.dns_flush_manual(),
                    can_auto_fix=bool(adapter.dns_flush_command()),
                )
            )

        # 7. OS Integrity & Baseline Hygiene
        if snapshot.uptime_seconds > 14 * 86400:
            findings.append(
                DiagnosticFinding(
                    finding_id="long_uptime_maintenance",
                    subsystem="integrity",
                    title=f"Extended OS Uptime ({format_uptime(snapshot.uptime_seconds)})",
                    severity="info",
                    summary="Long uptimes accumulate pending security patches and stale handles.",
                    evidence=f"Uptime: {format_uptime(snapshot.uptime_seconds)}",
                    impact_score=3,
                    safety_tier="manual_review",
                    risk_level="zero",
                    manual_steps=["Run OS verification commands below and schedule a clean reboot."],
                    manual_commands=adapter.integrity_check_manual(),
                    can_auto_fix=False,
                )
            )

        findings.append(
            DiagnosticFinding(
                finding_id="os_maintenance_baseline",
                subsystem="integrity",
                title=f"{adapter.os_family} Integrity, Logs & SSD Hygiene Check",
                severity="info",
                summary=(
                    f"Standard reversible maintenance verification tailored for {adapter.os_family} "
                    f"(profile: {config.active_profile})."
                ),
                evidence=f"Elevation: {'Admin/Root' if snapshot.is_elevated else 'Standard User (Safe Sandbox)'}",
                impact_score=0,
                action_id="vacuum_user_logs",
                safety_tier="safe_auto",
                risk_level="zero",
                manual_steps=[
                    "Run AstraCradle's safe user-log rotation & forensic baseline.",
                    "Optionally run the native OS verification commands below in an elevated shell.",
                ],
                manual_commands=adapter.integrity_check_manual(),
                can_auto_fix=True,
            )
        )

        # 8. Zombie / Defunct process hygiene
        zombie_count = sum(1 for p in snapshot.top_processes if p.status == "zombie")
        if zombie_count > 0:
            findings.append(
                DiagnosticFinding(
                    finding_id="zombie_processes",
                    subsystem="privacy",
                    title=f"{zombie_count} Defunct (Zombie) Process Entry Detected",
                    severity="low",
                    summary="Zombie entries indicate a parent process hasn't reaped child exits.",
                    evidence=f"Detected {zombie_count} zombie process(es).",
                    impact_score=3,
                    safety_tier="manual_review",
                    risk_level="zero",
                    manual_steps=["Restart the parent application cleanly."],
                    manual_commands=[],
                    can_auto_fix=False,
                )
            )

        penalty_sum = sum(f.impact_score for f in findings)
        health_score = max(12, min(100, 100 - penalty_sum))

        # Populate quantitative evidence & confidence scores (0-100%) on every finding
        for f in findings:
            signals: list[str] = []
            conf = 68
            if f.evidence:
                signals.append(f"Direct sensor/path proof: {f.evidence[:110]}")
                conf += 14
            if f.reclaimable_bytes > 0:
                signals.append(f"Measured reclaimable footprint: {format_bytes(f.reclaimable_bytes)}")
                conf += 10
            if f.severity in ("high", "critical"):
                signals.append(f"Threshold breach severity={f.severity} (impact -{f.impact_score})")
                conf += 7
            elif f.impact_score > 0:
                signals.append(f"Equilibrium impact weight: -{f.impact_score} pts")
                conf += 4
            if f.can_auto_fix:
                signals.append("Verified safe/guarded action path available")
                conf += 3
            f.confidence_percent = max(35, min(99, conf))
            f.evidence_signals = signals or [f"Baseline check ({f.subsystem})"]

        return DiagnosticReport(
            snapshot=snapshot,
            health_score=health_score,
            cache_targets=cache_targets,
            startup_items=startup_items,
            recommendations=recommendations,
            findings=findings,
            power_info=power_info,
        )
