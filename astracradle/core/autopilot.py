"""Automation rules evaluator with Quiet Hours, consent checks, and forensic logging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import AppConfig, ConfigManager
from .executor import ExecutionResult, GuardedExecutor
from .journal import ForensicJournal
from .models import SystemSnapshot, utc_now_iso
from .os_adapters import BaseOSAdapter
from .self_heal import SelfHealEngine


@dataclass
class OptimizationProofScorecard:
    timestamp: str
    action_label: str
    ram_freed_mb: float
    cpu_delta_pct: float
    health_delta: int
    summary: str


class AutopilotEngine:
    """Evaluates user-configured automation rules without violating consent or safety gates."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        executor: GuardedExecutor,
        self_heal: SelfHealEngine,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.executor = executor
        self.self_heal = self_heal
        self.journal = journal

    @staticmethod
    def is_quiet_hours(config: AppConfig) -> bool:
        if not config.quiet_hours_enabled:
            return False
        try:
            now_hm = datetime.now().strftime("%H:%M")
            start = config.quiet_hours_start
            end = config.quiet_hours_end
            if start <= end:
                return start <= now_hm <= end
            return now_hm >= start or now_hm <= end
        except Exception:
            return False

    def evaluate_once(
        self,
        snapshot: SystemSnapshot,
        adapter: BaseOSAdapter,
        config: AppConfig,
    ) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        quiet_now = self.is_quiet_hours(config)

        for rule in config.automation_rules:
            if not rule.get("enabled", False):
                continue
            if rule.get("require_quiet_check", True) and quiet_now:
                continue

            action_id = str(rule.get("action_id", ""))
            if action_id == "run_self_heal":
                if config.auto_self_heal:
                    if getattr(config, "auto_heal_features_enabled", True):
                        events, _ = self.self_heal.run_full_self_heal(adapter, config)
                    else:
                        events, _ = self.self_heal.run_self_check(adapter, config)
                    repaired = sum(1 for e in events if e.repaired_automatically)
                    rule["last_triggered"] = utc_now_iso()
                    results.append(
                        ExecutionResult(
                            ok=True,
                            dry_run=False,
                            title=rule.get("name", "Self-Heal Watchdog"),
                            message=f"Verified {len(events)} invariants ({repaired} auto-repaired).",
                        )
                    )
                continue

            if action_id == "scan_idle_ai_helpers":
                ai_cnt = sum(1 for p in snapshot.top_processes if p.category == "ai_chatbot")
                rule["last_triggered"] = utc_now_iso()
                results.append(
                    ExecutionResult(
                        ok=True,
                        dry_run=True,
                        title=rule.get("name", "AI Chatbot Scan"),
                        message=f"Identified {ai_cnt} active/idle AI chatbot process(es).",
                    )
                )
                continue

            if action_id == "prune_quarantine":
                res = self.executor.execute_action(
                    action_id, adapter, config, why="Scheduled Quarantine Vault pruning rule"
                )
                rule["last_triggered"] = utc_now_iso()
                results.append(res)
            elif action_id in ("clean_temp_caches", "vacuum_user_logs"):
                if config.consent_auto_remediate or config.dry_run_default:
                    res = self.executor.execute_action(
                        action_id, adapter, config, why="Consented Autopilot rule execution"
                    )
                    rule["last_triggered"] = utc_now_iso()
                    results.append(res)

        self.cfg_mgr.save(config)
        return results

    @staticmethod
    def capture_proof_baseline(snapshot: SystemSnapshot, health_score: int) -> dict[str, float]:
        return {
            "ram_used_bytes": float(snapshot.mem_used_bytes),
            "cpu_percent": float(snapshot.cpu_percent),
            "health_score": float(health_score),
        }

    def compare_proof_scorecard(
        self,
        action_label: str,
        baseline: dict[str, float],
        post_snapshot: SystemSnapshot,
        post_health_score: int,
    ) -> OptimizationProofScorecard:
        ram_freed_mb = round((baseline.get("ram_used_bytes", 0.0) - float(post_snapshot.mem_used_bytes)) / (1024.0 * 1024.0), 1)
        cpu_delta = round(float(post_snapshot.cpu_percent) - baseline.get("cpu_percent", 0.0), 1)
        health_delta = int(post_health_score - round(baseline.get("health_score", float(post_health_score))))
        ram_txt = f"Freed {ram_freed_mb:.1f} MB RAM" if ram_freed_mb > 0 else f"RAM {ram_freed_mb:+.1f} MB"
        cpu_txt = f"CPU {cpu_delta:+.1f}%"
        hl_txt = f"Health {health_delta:+d} pts"
        summary = f"{action_label}: {ram_txt} · {cpu_txt} · {hl_txt}"
        self.journal.log_event(
            "ProofScorecard",
            "before_after_proof",
            summary,
            "Verified measurable before/after optimization impact",
            "ok",
        )
        return OptimizationProofScorecard(
            timestamp=utc_now_iso(),
            action_label=action_label,
            ram_freed_mb=ram_freed_mb,
            cpu_delta_pct=cpu_delta,
            health_delta=health_delta,
            summary=summary,
        )

    def evaluate_smart_triggers(
        self,
        snapshot: SystemSnapshot,
        config: AppConfig,
        *,
        gpu_temp_c: float | None = None,
        wifi_congestion_pct: float | None = None,
        battery_pct: float | None = None,
        on_battery: bool = False,
    ) -> list[dict[str, str]]:
        """Rule-based If-X-Then-Y trigger evaluator (Thermal, Wi-Fi Congestion, Low Battery)."""
        if not getattr(config, "auto_trigger_rules_enabled", True) or self.is_quiet_hours(config):
            return []
        fired: list[dict[str, str]] = []
        th_limit = float(getattr(config, "thermal_trigger_temp_c", 82.0))
        cur_temp = gpu_temp_c if gpu_temp_c is not None else snapshot.temperature_c
        if cur_temp is not None and float(cur_temp) >= th_limit:
            fired.append(
                {
                    "rule": "thermal_guard",
                    "action": "restrain_background",
                    "message": f"Thermal trigger ({cur_temp:.1f}°C >= {th_limit:.0f}°C): throttling background hogs.",
                }
            )
        wf_limit = float(getattr(config, "wifi_congestion_trigger_pct", 75.0))
        if wifi_congestion_pct is not None and wifi_congestion_pct >= wf_limit:
            fired.append(
                {
                    "rule": "wifi_congestion",
                    "action": "optimize_wifi",
                    "message": f"Wi-Fi congestion ({wifi_congestion_pct:.0f}% >= {wf_limit:.0f}%): clean channel switch ready.",
                }
            )
        bat_limit = float(getattr(config, "battery_endurance_trigger_pct", 25.0))
        if on_battery and battery_pct is not None and battery_pct <= bat_limit and config.active_profile != "Battery Endurance":
            config.apply_profile("Battery Endurance")
            config.low_power_ui_mode = True
            self.cfg_mgr.save(config)
            fired.append(
                {
                    "rule": "battery_endurance",
                    "action": "switch_battery_endurance",
                    "message": f"Battery at {battery_pct:.0f}% (<= {bat_limit:.0f}%): switched to Battery Endurance profile.",
                }
            )
        return fired

    def add_custom_rule(
        self,
        config: AppConfig,
        *,
        name: str,
        condition_type: str,
        condition_value: str,
        action_type: str,
        action_value: str,
    ) -> dict[str, Any]:
        """Adds a user-defined If-X-Then-Y custom rule and persists it."""
        import uuid

        rule = {
            "rule_id": f"crule_{uuid.uuid4().hex[:8]}",
            "name": name.strip() or f"If {condition_type}={condition_value}",
            "enabled": True,
            "condition_type": condition_type,  # process_running | cpu_above | on_battery_and_temp
            "condition_value": str(condition_value).strip(),
            "action_type": action_type,  # switch_profile | toast_alert | low_power_ui
            "action_value": str(action_value).strip(),
        }
        rules = list(getattr(config, "custom_automation_rules", []) or [])
        rules.append(rule)
        config.custom_automation_rules = rules
        self.cfg_mgr.save(config)
        self.journal.log_event("Autopilot", "add_custom_rule", f"Added custom rule: {rule['name']}", "User custom rule builder", "ok")
        return rule

    def delete_custom_rule(self, config: AppConfig, rule_id: str) -> bool:
        rules = list(getattr(config, "custom_automation_rules", []) or [])
        new_rules = [r for r in rules if r.get("rule_id") != rule_id]
        if len(new_rules) == len(rules):
            return False
        config.custom_automation_rules = new_rules
        self.cfg_mgr.save(config)
        return True

    def toggle_custom_rule(self, config: AppConfig, rule_id: str) -> bool:
        for r in getattr(config, "custom_automation_rules", []) or []:
            if r.get("rule_id") == rule_id:
                r["enabled"] = not bool(r.get("enabled", True))
                self.cfg_mgr.save(config)
                return True
        return False

    def evaluate_custom_rules(
        self,
        snapshot: SystemSnapshot,
        config: AppConfig,
        *,
        running_process_names: set[str] | None = None,
        on_battery: bool = False,
    ) -> list[dict[str, str]]:
        """Evaluates user-defined custom rules against live system state."""
        rules = getattr(config, "custom_automation_rules", []) or []
        if not rules or self.is_quiet_hours(config):
            return []
        if running_process_names is None:
            running_process_names = {
                p.name.lower() for p in (getattr(snapshot, "top_processes", None) or []) if getattr(p, "name", "")
            }
        fired: list[dict[str, str]] = []
        changed_cfg = False
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            ctype = str(rule.get("condition_type") or "")
            cval = str(rule.get("condition_value") or "").strip()
            atype = str(rule.get("action_type") or "")
            aval = str(rule.get("action_value") or "").strip()
            matched = False
            if ctype == "process_running" and cval:
                target = cval.lower()
                matched = any(target in pname for pname in running_process_names)
            elif ctype == "cpu_above":
                try:
                    matched = float(snapshot.cpu_percent) >= float(cval)
                except Exception:
                    matched = False
            elif ctype == "on_battery_and_temp":
                try:
                    t_lim = float(cval or "75")
                    matched = bool(on_battery and snapshot.temperature_c is not None and snapshot.temperature_c >= t_lim)
                except Exception:
                    matched = False
            if not matched:
                continue
            if atype == "switch_profile" and aval and config.active_profile != aval:
                config.apply_profile(aval)
                changed_cfg = True
            elif atype == "low_power_ui" and not config.low_power_ui_mode:
                config.low_power_ui_mode = True
                changed_cfg = True
            msg = f"Custom rule '{rule.get('name', ctype)}' triggered -> {atype}: {aval or 'notified'}"
            fired.append({"rule_id": str(rule.get("rule_id", "")), "action": atype, "value": aval, "message": msg})
        if changed_cfg:
            self.cfg_mgr.save(config)
        return fired
