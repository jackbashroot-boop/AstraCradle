"""Typed domain models for AstraCradle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PartitionInfo:
    device: str
    mountpoint: str
    fstype: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float


@dataclass
class ProcessInfo:
    pid: int
    name: str
    username: str
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    status: str
    cmdline: str = ""
    protected: bool = False
    category: str = "standard"  # standard | ai_chatbot | idle_helper | system
    recommendation: str = ""


@dataclass
class StartupItem:
    item_id: str
    name: str
    location: str
    command: str
    scope: str  # "user" | "system"
    enabled: bool
    impact: str  # "low" | "medium" | "high"
    os_family: str


@dataclass
class CacheTarget:
    target_id: str
    name: str
    category: str
    path: str
    size_bytes: int
    file_count: int
    risk: str  # "zero" | "low" | "moderate"
    description: str
    selected_by_default: bool = True
    exists: bool = True


@dataclass
class ProcessRecommendation:
    rec_id: str
    category: str  # ai_chatbot | unused_app | idle_process | startup_bloat
    name: str
    pid: int | None
    memory_mb: float
    cpu_percent: float
    confidence: str  # high | medium
    reason: str
    suggested_action: str  # terminate | disable_startup | uninstall_review | ignore
    target_ref: str = ""
    safe_to_automate: bool = True


@dataclass
class OptimizationScript:
    script_id: str
    name: str
    category: str  # gaming | 3d_render | compile_data | ai_hibernate | custom
    description: str
    enabled: bool = False
    built_in: bool = True
    os_family: str = "all"
    steps_summary: list[str] = field(default_factory=list)
    apply_actions: list[dict[str, Any]] = field(default_factory=list)
    revert_actions: list[dict[str, Any]] = field(default_factory=list)
    last_toggled_at: str | None = None


@dataclass
class ForensicEntry:
    entry_id: str
    timestamp: str
    component: str
    action: str
    how: str
    why: str
    outcome: str  # ok | warning | error | skipped | rolled_back
    details: str = ""
    prev_hash: str = ""
    entry_hash: str = ""


@dataclass
class SelfAuditFinding:
    audit_id: str
    timestamp: str
    severity: str  # ok | notice | flaw | error
    title: str
    explanation: str
    remediation: str


@dataclass
class CrashPostMortem:
    incident_id: str
    detected_at: str
    crash_type: str  # unclean_app_exit | python_exception | os_crash_or_power_loss | simulated_crash
    probable_cause: str
    evidence: list[str]
    fix_steps: list[str]
    last_known_actions: list[str]
    resolved: bool = False
    confidence_percent: int = 85
    evidence_scores: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PluginModuleInfo:
    module_id: str
    name: str
    version: str
    kind: str  # diagnostic | script | os_adapter | safeguard
    description: str
    enabled: bool = True
    built_in: bool = True


@dataclass
class UpdateCheckResult:
    current_version: str
    latest_version: str
    update_available: bool
    channel: str
    release_notes: str
    package_hash: str = ""
    status_message: str = ""


@dataclass
class SystemSnapshot:
    timestamp: str
    os_family: str
    os_release: str
    hostname: str
    architecture: str
    uptime_seconds: int
    is_elevated: bool
    cpu_percent: float
    cpu_per_core: list[float]
    cpu_count_logical: int
    cpu_freq_mhz: float | None
    load_avg: tuple[float, float, float] | None
    mem_total_bytes: int
    mem_used_bytes: int
    mem_available_bytes: int
    mem_percent: float
    swap_total_bytes: int
    swap_used_bytes: int
    swap_percent: float
    partitions: list[PartitionInfo]
    net_bytes_sent: int
    net_bytes_recv: int
    dns_latency_ms: float | None
    battery_percent: float | None
    battery_plugged: bool | None
    temperature_c: float | None
    process_count: int
    top_processes: list[ProcessInfo]


@dataclass
class DiagnosticFinding:
    finding_id: str
    subsystem: str  # storage | startup | memory | power | network | integrity | privacy | ai_bloat | forensic
    title: str
    severity: str  # info | low | medium | high | critical
    summary: str
    evidence: str
    impact_score: int
    reclaimable_bytes: int = 0
    action_id: str | None = None
    action_params: dict[str, Any] = field(default_factory=dict)
    safety_tier: str = "safe_auto"  # safe_auto | guarded | manual_review
    risk_level: str = "zero"  # zero | low | moderate
    manual_steps: list[str] = field(default_factory=list)
    manual_commands: list[str] = field(default_factory=list)
    can_auto_fix: bool = True
    confidence_percent: int = 85
    evidence_signals: list[str] = field(default_factory=list)


@dataclass
class PlaybookStep:
    step_number: int
    finding_id: str
    title: str
    subsystem: str
    severity: str
    safety_tier: str
    risk_level: str
    summary: str
    evidence: str
    manual_steps: list[str]
    manual_commands: list[str]
    action_id: str | None
    action_params: dict[str, Any] = field(default_factory=dict)
    can_auto_fix: bool = True
    status: str = "pending"
    result_note: str = ""
    confidence_percent: int = 85
    evidence_signals: list[str] = field(default_factory=list)


@dataclass
class Playbook:
    playbook_id: str
    created_at: str
    os_family: str
    profile_name: str
    health_score: int
    summary_headline: str
    executive_summary: str
    total_reclaimable_bytes: int
    steps: list[PlaybookStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RollbackEntry:
    entry_id: str
    timestamp: str
    action_id: str
    title: str
    dry_run: bool
    status: str  # applied | rolled_back | failed
    summary: str
    quarantine_paths: list[dict[str, str]] = field(default_factory=list)
    revert_commands: list[str] = field(default_factory=list)
    reverted_at: str | None = None
    bytes_affected: int = 0


@dataclass
class SelfHealEvent:
    event_id: str
    timestamp: str
    check_name: str
    status: str  # ok | repaired | warning
    details: str
    repaired_automatically: bool = False


@dataclass
class AutomationRule:
    rule_id: str
    name: str
    description: str
    enabled: bool
    trigger_type: str
    threshold_value: float
    action_id: str
    require_quiet_check: bool = True
    last_triggered: str | None = None
