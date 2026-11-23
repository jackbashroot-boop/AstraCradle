"""Automation rules evaluator with Quiet Hours, consent checks, and forensic logging."""

from __future__ import annotations

from datetime import datetime

from .config import AppConfig, ConfigManager
from .executor import ExecutionResult, GuardedExecutor
from .journal import ForensicJournal
from .models import SystemSnapshot, utc_now_iso
from .os_adapters import BaseOSAdapter
from .self_heal import SelfHealEngine


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
