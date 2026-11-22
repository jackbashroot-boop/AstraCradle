"""Configuration management, permission consent gates, situation profiles, and atomic persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from .models import AutomationRule, utc_now_iso


DEFAULT_PROTECTED_PROCESSES = [
    "systemd",
    "init",
    "launchd",
    "kernel_task",
    "WindowServer",
    "loginwindow",
    "csrss.exe",
    "lsass.exe",
    "services.exe",
    "smss.exe",
    "wininit.exe",
    "winlogon.exe",
    "explorer.exe",
    "dwm.exe",
    "svchost.exe",
    "dbus-daemon",
    "Xorg",
    "Wayland",
    "astracradle",
    "python",
    "python3",
]


SITUATION_PROFILES: dict[str, dict[str, Any]] = {
    "Balanced Daily": {
        "description": "Optimal equilibrium between snappy responsiveness, quiet thermals, and conservative safety.",
        "dry_run_default": False,
        "auto_trim_caches": True,
        "cache_threshold_mb": 512,
        "ram_pressure_threshold": 85.0,
        "cpu_alert_threshold": 90.0,
        "disk_floor_percent": 10.0,
        "power_preference": "balanced",
        "pause_bg_during_fullscreen": False,
    },
    "Gaming / Low-Latency": {
        "description": "Maximizes frame pacing, trims stale shader/temp clutter, and silences idle AI & background daemons.",
        "dry_run_default": False,
        "auto_trim_caches": True,
        "cache_threshold_mb": 256,
        "ram_pressure_threshold": 75.0,
        "cpu_alert_threshold": 88.0,
        "disk_floor_percent": 12.0,
        "power_preference": "performance",
        "pause_bg_during_fullscreen": True,
    },
    "3D Render & Benchmark": {
        "description": "Unlocks sustained GPU/CPU throughput, frees standby RAM, and pauses non-essential background tasks.",
        "dry_run_default": False,
        "auto_trim_caches": True,
        "cache_threshold_mb": 512,
        "ram_pressure_threshold": 80.0,
        "cpu_alert_threshold": 96.0,
        "disk_floor_percent": 12.0,
        "power_preference": "performance",
        "pause_bg_during_fullscreen": False,
    },
    "Data & Code Compilation": {
        "description": "Protects IDE/build/dataset scratch caches while freeing RAM and lowering noisy background contention.",
        "dry_run_default": False,
        "auto_trim_caches": True,
        "cache_threshold_mb": 1536,
        "ram_pressure_threshold": 90.0,
        "cpu_alert_threshold": 96.0,
        "disk_floor_percent": 15.0,
        "power_preference": "performance",
        "pause_bg_during_fullscreen": False,
    },
    "Battery Endurance": {
        "description": "Reduces background wakeups, switches power governor toward efficiency, and caps polling overhead.",
        "dry_run_default": False,
        "auto_trim_caches": False,
        "cache_threshold_mb": 1024,
        "ram_pressure_threshold": 88.0,
        "cpu_alert_threshold": 80.0,
        "disk_floor_percent": 10.0,
        "power_preference": "efficiency",
        "pause_bg_during_fullscreen": True,
    },
    "Minimal Safe Mode": {
        "description": "Strictly read-only diagnostics and dry-run playbooks; zero automatic mutations allowed.",
        "dry_run_default": True,
        "auto_trim_caches": False,
        "cache_threshold_mb": 2048,
        "ram_pressure_threshold": 92.0,
        "cpu_alert_threshold": 95.0,
        "disk_floor_percent": 15.0,
        "power_preference": "balanced",
        "pause_bg_during_fullscreen": True,
    },
}


def default_automation_rules() -> list[dict[str, Any]]:
    return [
        asdict(
            AutomationRule(
                rule_id="rule_temp_trim",
                name="Auto-Quarantine Stale Temp Caches",
                description="Soft-moves expired temporary files older than retention window into Quarantine.",
                enabled=True,
                trigger_type="threshold",
                threshold_value=512.0,
                action_id="clean_temp_caches",
                require_quiet_check=True,
            )
        ),
        asdict(
            AutomationRule(
                rule_id="rule_log_vacuum",
                name="Rotate & Trim Oversized App Logs",
                description="Safely archives user-level log files exceeding safe limits.",
                enabled=True,
                trigger_type="threshold",
                threshold_value=256.0,
                action_id="vacuum_user_logs",
                require_quiet_check=True,
            )
        ),
        asdict(
            AutomationRule(
                rule_id="rule_idle_ai_prompt",
                name="Prompt to Silence Idle AI Chatbots & Helpers",
                description="Detects dormant local LLM/chatbot background processes and offers 1-click automation.",
                enabled=True,
                trigger_type="threshold",
                threshold_value=1.0,
                action_id="scan_idle_ai_helpers",
                require_quiet_check=True,
            )
        ),
        asdict(
            AutomationRule(
                rule_id="rule_self_heal",
                name="Continuous Self-Integrity & Forensic Audit",
                description="Verifies AstraCradle state, checksums, forensic journal, and caps silently.",
                enabled=True,
                trigger_type="interval",
                threshold_value=60.0,
                action_id="run_self_heal",
                require_quiet_check=False,
            )
        ),
        asdict(
            AutomationRule(
                rule_id="rule_quarantine_gc",
                name="Quarantine Vault Expiration Sweep",
                description="Purges expired quarantine items older than retention days or above vault size cap.",
                enabled=True,
                trigger_type="interval",
                threshold_value=24.0,
                action_id="prune_quarantine",
                require_quiet_check=True,
            )
        ),
    ]


@dataclass
class AppConfig:
    theme: str = "Obsidian Horology"
    ui_scale: str = "100%"
    language: str = "en"
    simple_mode: bool = True
    active_profile: str = "Balanced Daily"
    os_adapter_override: str = "auto"
    dry_run_default: bool = False
    auto_self_heal: bool = True
    telemetry_interval_sec: float = 2.0
    low_power_ui_mode: bool = False
    show_tray_icon: bool = True
    minimize_to_tray: bool = False
    start_minimized: bool = False
    launch_at_login: bool = False
    ram_guard_enabled: bool = False
    ram_guard_threshold: float = 85.0
    astrashield_enabled: bool = False
    astrashield_dns_port: int = 5353
    astrashield_upstream_dns: str = "1.1.1.1"
    astrashield_youtube_safe_prune: bool = True
    astrashield_categories: list[str] = field(
        default_factory=lambda: ["ads", "youtube_ads", "trackers", "telemetry", "malware"]
    )
    badusb_guard_enabled: bool = True
    usb_strict_gate: bool = True
    usb_notify_new_devices: bool = True
    usb_trusted_ids: list[str] = field(default_factory=list)
    usb_blocked_ids: list[str] = field(default_factory=list)
    background_schedule_enabled: bool = False
    schedule_interval_minutes: int = 180
    quarantine_enabled: bool = True
    quarantine_retention_days: int = 14
    quarantine_max_mb: int = 2048
    disk_safety_floor_percent: float = 8.0
    log_max_mb: int = 32
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    pause_during_fullscreen: bool = False
    require_confirm_moderate: bool = True
    elevated_actions_mode: str = "playbook_only"
    # Explicit Local Diagnostic Permission Gates (user-consented)
    consent_prompted: bool = False
    first_run_scan_completed: bool = False
    consent_telemetry: bool = True
    consent_process_scan: bool = True
    consent_installed_apps: bool = True
    consent_bloat_scan: bool = True
    consent_privacy_scan: bool = False
    consent_auto_remediate: bool = False
    consent_crash_diagnostics: bool = True
    app_auto_detect_enabled: bool = False
    auto_detect_rules: dict[str, str] = field(
        default_factory=lambda: {
            "blender": "script_3d_render",
            "unreal": "script_3d_render",
            "steam": "script_gaming_max",
            "cargo": "script_compile_data",
            "cmake": "script_compile_data",
        }
    )
    # Seamless Auto-Updater (checks GitHub repo first, then falls back to local)
    auto_update_mode: str = "silent_auto"  # silent_auto | notify | manual
    update_channel: str = "stable"  # stable | beta
    update_repo_url: str = "https://github.com/jackbashroot-boop/AstraCradle"
    update_manifest_url: str = "local://updates/manifest.json"
    active_preset_id: str = "preset_intel_hybrid_gaming"
    hardware_custom_tiers: dict[str, str] = field(default_factory=dict)
    probalance_enabled: bool = False
    probalance_cpu_threshold: float = 35.0
    probalance_boost_foreground: bool = True
    thermal_spc_enabled: bool = True
    telemetry_resolution: str = "60s"
    excluded_processes: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED_PROCESSES))
    custom_protected_paths: list[str] = field(default_factory=list)
    ignored_recommendations: list[str] = field(default_factory=list)
    automation_rules: list[dict[str, Any]] = field(default_factory=default_automation_rules)
    last_updated: str = field(default_factory=utc_now_iso)

    def apply_profile(self, profile_name: str) -> None:
        if profile_name not in SITUATION_PROFILES:
            return
        prof = SITUATION_PROFILES[profile_name]
        self.active_profile = profile_name
        self.dry_run_default = bool(prof["dry_run_default"])
        self.disk_safety_floor_percent = float(prof["disk_floor_percent"])
        self.pause_during_fullscreen = bool(prof["pause_bg_during_fullscreen"])
        for rule in self.automation_rules:
            if rule.get("rule_id") == "rule_temp_trim":
                rule["enabled"] = bool(prof["auto_trim_caches"]) and not self.dry_run_default
                rule["threshold_value"] = float(prof["cache_threshold_mb"])
        self.last_updated = utc_now_iso()


class ConfigManager:
    """Manages ~/.astracradle workspace layout, atomic config writes, and shadow backups."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        env_home = os.environ.get("ASTRACRADLE_HOME")
        if base_dir is not None:
            self.base_dir = Path(base_dir).expanduser().resolve()
        elif env_home:
            self.base_dir = Path(env_home).expanduser().resolve()
        else:
            self.base_dir = Path.home() / ".astracradle"

        self.state_dir = self.base_dir / "state"
        self.quarantine_dir = self.base_dir / "quarantine"
        self.snapshots_dir = self.base_dir / "snapshots"
        self.logs_dir = self.base_dir / "logs"
        self.playbooks_dir = self.base_dir / "playbooks"
        self.plugins_dir = self.base_dir / "plugins"
        self.crash_dir = self.base_dir / "crash_reports"
        self.updates_dir = self.base_dir / "updates"

        self.config_path = self.state_dir / "config.json"
        self.config_bak_path = self.state_dir / "config.json.bak"
        self.checksum_path = self.state_dir / "checksums.json"
        self.rollback_path = self.state_dir / "rollback_ledger.json"
        self.rollback_bak_path = self.state_dir / "rollback_ledger.json.bak"
        self.self_heal_log_path = self.state_dir / "self_heal_ledger.json"
        self.forensic_log_path = self.logs_dir / "forensic_journal.jsonl"
        self.heartbeat_path = self.state_dir / "heartbeat.json"
        self.scripts_path = self.state_dir / "scripts.json"
        self.crash_history_path = self.state_dir / "crash_history.json"

        self.ensure_directories()

    def ensure_directories(self) -> None:
        for d in (
            self.base_dir,
            self.state_dir,
            self.quarantine_dir,
            self.snapshots_dir,
            self.logs_dir,
            self.playbooks_dir,
            self.plugins_dir,
            self.crash_dir,
            self.updates_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _read_checksums(self) -> dict[str, str]:
        if not self.checksum_path.exists():
            return {}
        try:
            data = json.loads(self.checksum_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
        return {}

    def _write_checksum(self, key: str, digest: str) -> None:
        checksums = self._read_checksums()
        checksums[key] = digest
        tmp = self.checksum_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(checksums, indent=2), encoding="utf-8")
        os.replace(tmp, self.checksum_path)

    def _sanitize_config(self, raw: dict[str, Any]) -> AppConfig:
        defaults = AppConfig()
        valid_fields = {f.name for f in AppConfig.__dataclass_fields__.values()}
        filtered = {k: v for k, v in raw.items() if k in valid_fields}

        def _safe_int(val: Any, default: int, lo: int, hi: int) -> int:
            try:
                if isinstance(val, bool):
                    return default
                n = int(float(val))
                return max(lo, min(hi, n))
            except Exception:
                return default

        def _safe_float(val: Any, default: float, lo: float, hi: float) -> float:
            try:
                if isinstance(val, bool):
                    return default
                f = float(val)
                if not math.isfinite(f):
                    return default
                return max(lo, min(hi, f))
            except Exception:
                return default

        def _safe_str_list(val: Any, default: list[str]) -> list[str]:
            if not isinstance(val, list):
                return list(default)
            return [str(x) for x in val if isinstance(x, (str, int, float)) and str(x).strip()]

        filtered["quarantine_retention_days"] = _safe_int(
            filtered.get("quarantine_retention_days"), defaults.quarantine_retention_days, 1, 365
        )
        filtered["quarantine_max_mb"] = _safe_int(
            filtered.get("quarantine_max_mb"), defaults.quarantine_max_mb, 32, 102400
        )
        filtered["log_max_mb"] = _safe_int(filtered.get("log_max_mb"), defaults.log_max_mb, 1, 1024)
        filtered["schedule_interval_minutes"] = _safe_int(
            filtered.get("schedule_interval_minutes"), defaults.schedule_interval_minutes, 15, 1440
        )
        filtered["telemetry_interval_sec"] = _safe_float(
            filtered.get("telemetry_interval_sec"), defaults.telemetry_interval_sec, 0.5, 60.0
        )
        filtered["disk_safety_floor_percent"] = _safe_float(
            filtered.get("disk_safety_floor_percent"), defaults.disk_safety_floor_percent, 1.0, 60.0
        )
        filtered["probalance_cpu_threshold"] = _safe_float(
            filtered.get("probalance_cpu_threshold"), defaults.probalance_cpu_threshold, 10.0, 95.0
        )
        filtered["ram_guard_threshold"] = _safe_float(
            filtered.get("ram_guard_threshold"), defaults.ram_guard_threshold, 50.0, 98.0
        )
        filtered["astrashield_dns_port"] = _safe_int(
            filtered.get("astrashield_dns_port"), defaults.astrashield_dns_port, 1024, 65535
        )

        for bool_k in (
            "dry_run_default",
            "auto_self_heal",
            "low_power_ui_mode",
            "show_tray_icon",
            "minimize_to_tray",
            "start_minimized",
            "launch_at_login",
            "ram_guard_enabled",
            "astrashield_enabled",
            "astrashield_youtube_safe_prune",
            "badusb_guard_enabled",
            "usb_strict_gate",
            "usb_notify_new_devices",
            "background_schedule_enabled",
            "quarantine_enabled",
            "quiet_hours_enabled",
            "pause_during_fullscreen",
            "require_confirm_moderate",
            "consent_prompted",
            "first_run_scan_completed",
            "consent_telemetry",
            "consent_process_scan",
            "consent_installed_apps",
            "consent_bloat_scan",
            "consent_privacy_scan",
            "consent_auto_remediate",
            "consent_crash_diagnostics",
            "app_auto_detect_enabled",
            "probalance_enabled",
            "probalance_boost_foreground",
            "thermal_spc_enabled",
        ):
            if bool_k in filtered and not isinstance(filtered[bool_k], bool):
                filtered[bool_k] = getattr(defaults, bool_k)

        raw_ad = filtered.get("auto_detect_rules")
        if isinstance(raw_ad, dict):
            filtered["auto_detect_rules"] = {
                str(k): str(v) for k, v in raw_ad.items() if str(k).strip() and str(v).strip()
            }
        else:
            filtered["auto_detect_rules"] = dict(defaults.auto_detect_rules)

        filtered["excluded_processes"] = _safe_str_list(
            filtered.get("excluded_processes"), defaults.excluded_processes
        )
        filtered["custom_protected_paths"] = _safe_str_list(
            filtered.get("custom_protected_paths"), defaults.custom_protected_paths
        )
        filtered["ignored_recommendations"] = _safe_str_list(
            filtered.get("ignored_recommendations"), defaults.ignored_recommendations
        )
        filtered["astrashield_categories"] = _safe_str_list(
            filtered.get("astrashield_categories"), defaults.astrashield_categories
        )
        filtered["usb_trusted_ids"] = _safe_str_list(
            filtered.get("usb_trusted_ids"), defaults.usb_trusted_ids
        )
        filtered["usb_blocked_ids"] = _safe_str_list(
            filtered.get("usb_blocked_ids"), defaults.usb_blocked_ids
        )

        raw_rules = filtered.get("automation_rules")
        if isinstance(raw_rules, list):
            clean_rules = [r for r in raw_rules if isinstance(r, dict) and r.get("rule_id")]
            filtered["automation_rules"] = clean_rules if clean_rules else default_automation_rules()
        else:
            filtered["automation_rules"] = default_automation_rules()

        for str_k in (
            "theme",
            "ui_scale",
            "language",
            "active_profile",
            "os_adapter_override",
            "quiet_hours_start",
            "quiet_hours_end",
            "elevated_actions_mode",
            "auto_update_mode",
            "update_channel",
            "update_repo_url",
            "update_manifest_url",
            "astrashield_upstream_dns",
            "active_preset_id",
            "telemetry_resolution",
            "last_updated",
        ):
            if str_k in filtered and not isinstance(filtered[str_k], str):
                filtered[str_k] = getattr(defaults, str_k)
        if filtered.get("telemetry_resolution") not in ("60s", "5m", "30m", "1hr", "30d"):
            filtered["telemetry_resolution"] = defaults.telemetry_resolution

        return AppConfig(**filtered)

    def load(self) -> AppConfig:
        self.ensure_directories()
        if not self.config_path.exists():
            cfg = AppConfig()
            self.save(cfg)
            return cfg
        try:
            raw_bytes = self.config_path.read_bytes()
            data = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Config root is not a JSON object")
            return self._sanitize_config(data)
        except Exception:
            if self.config_bak_path.exists():
                try:
                    data = json.loads(self.config_bak_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        cfg = self._sanitize_config(data)
                        self.save(cfg)
                        return cfg
                except Exception:
                    pass
            cfg = AppConfig()
            self.save(cfg)
            return cfg

    def save(self, cfg: AppConfig) -> None:
        self.ensure_directories()
        cfg.last_updated = utc_now_iso()
        payload = json.dumps(asdict(cfg), indent=2).encode("utf-8")
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, self.config_path)
        tmp_bak = self.config_bak_path.with_suffix(".tmp")
        tmp_bak.write_bytes(payload)
        os.replace(tmp_bak, self.config_bak_path)
        self._write_checksum("config.json", self.sha256_bytes(payload))
