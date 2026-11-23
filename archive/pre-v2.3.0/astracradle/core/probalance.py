"""ProBalance Foreground Boost & Auto-Reverted App Profile Subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import platform
from typing import Any

import psutil

from .config import AppConfig, ConfigManager, DEFAULT_PROTECTED_PROCESSES, SITUATION_PROFILES
from .journal import ForensicJournal
from .models import ProcessInfo, utc_now_iso


@dataclass
class AppProfileRule:
    rule_id: str
    app_pattern: str
    target_power_profile: str = "Gaming / Low-Latency"
    target_preset_id: str = ""
    target_gpu_profile_id: str = ""
    boost_foreground: bool = True
    enabled: bool = True
    built_in: bool = False
    notes: str = ""


@dataclass
class RestraintRecord:
    pid: int
    name: str
    original_nice: int
    applied_nice: int
    cpu_percent: float
    reason: str
    applied_at: str = field(default_factory=utc_now_iso)


@dataclass
class ActiveEscalationState:
    rule_id: str
    matched_app: str
    prior_power_profile: str
    prior_preset_id: str
    started_at: str = field(default_factory=utc_now_iso)


def default_app_profile_rules() -> list[AppProfileRule]:
    return [
        AppProfileRule(
            rule_id="app_rule_blender",
            app_pattern="blender",
            target_power_profile="3D Render & Benchmark",
            target_preset_id="preset_workstation_render",
            target_gpu_profile_id="gpu_max_airflow",
            boost_foreground=True,
            enabled=True,
            built_in=True,
            notes="Escalates power & GPU cooling while Blender is open, then auto-reverts on exit.",
        ),
        AppProfileRule(
            rule_id="app_rule_steam_game",
            app_pattern="steam",
            target_power_profile="Gaming / Low-Latency",
            target_preset_id="preset_intel_hybrid_gaming",
            target_gpu_profile_id="gpu_balanced_stock",
            boost_foreground=True,
            enabled=True,
            built_in=True,
            notes="Switches to low-latency gaming profile while game runtime is active, then reverts.",
        ),
        AppProfileRule(
            rule_id="app_rule_cargo_build",
            app_pattern="cargo",
            target_power_profile="Data & Code Compilation",
            target_preset_id="preset_workstation_render",
            target_gpu_profile_id="",
            boost_foreground=False,
            enabled=True,
            built_in=True,
            notes="Maximizes compilation throughput while preserving IDE caches, then auto-reverts.",
        ),
    ]


class ProBalanceEngine:
    """Temporarily restrains background CPU hogs, boosts foreground apps, and auto-reverts app profiles."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.rules_path = cfg_mgr.state_dir / "app_profile_rules.json"
        self.state_path = cfg_mgr.state_dir / "probalance_state.json"
        self.restrained: dict[int, RestraintRecord] = {}
        self.active_escalation: ActiveEscalationState | None = None
        self.recent_events: list[str] = []
        self._load_state()

    def load_rules(self) -> list[AppProfileRule]:
        if not self.rules_path.exists():
            rules = default_app_profile_rules()
            self.save_rules(rules)
            return rules
        try:
            raw = json.loads(self.rules_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("not a list")
            valid = {f.name for f in AppProfileRule.__dataclass_fields__.values()}
            out = [
                AppProfileRule(**{k: v for k, v in r.items() if k in valid})
                for r in raw
                if isinstance(r, dict) and r.get("rule_id") and r.get("app_pattern")
            ]
            return out or default_app_profile_rules()
        except Exception:
            rules = default_app_profile_rules()
            self.save_rules(rules)
            return rules

    def save_rules(self, rules: list[AppProfileRule]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.rules_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in rules], indent=2), encoding="utf-8")
        os.replace(tmp, self.rules_path)

    def save_custom_rule(
        self,
        app_pattern: str,
        target_power_profile: str,
        target_preset_id: str = "",
        target_gpu_profile_id: str = "",
        boost_foreground: bool = True,
        notes: str = "",
    ) -> AppProfileRule:
        pat = " ".join(app_pattern.strip().lower().split())[:48] or "custom_app"
        prof = target_power_profile if target_power_profile in SITUATION_PROFILES else "Gaming / Low-Latency"
        rid = f"app_rule_{pat.replace(' ', '_')[:22]}"
        rule = AppProfileRule(
            rule_id=rid,
            app_pattern=pat,
            target_power_profile=prof,
            target_preset_id=target_preset_id.strip(),
            target_gpu_profile_id=target_gpu_profile_id.strip(),
            boost_foreground=bool(boost_foreground),
            enabled=True,
            built_in=False,
            notes=notes.strip() or f"Auto-escalate to '{prof}' when '{pat}' runs and revert on exit.",
        )
        rules = [r for r in self.load_rules() if r.rule_id != rid]
        rules.append(rule)
        self.save_rules(rules)
        self.journal.log_event(
            "ProBalanceEngine",
            f"save_app_rule_{rid}",
            f"Saved auto-reverted app rule for '{pat}' -> {prof}",
            rule.notes,
            "ok",
        )
        return rule

    def delete_rule(self, rule_id: str) -> tuple[bool, str]:
        rules = self.load_rules()
        rem = [r for r in rules if r.rule_id != rule_id or r.built_in]
        if len(rem) == len(rules):
            return False, "Built-in app profile rules cannot be deleted (or rule not found)."
        self.save_rules(rem)
        self.journal.log_event(
            "ProBalanceEngine",
            f"delete_app_rule_{rule_id}",
            f"Deleted custom app profile rule '{rule_id}'",
            "User removed custom app profile rule",
            "ok",
        )
        return True, f"Deleted custom app rule '{rule_id}'."

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            esc = raw.get("active_escalation")
            if isinstance(esc, dict) and esc.get("rule_id") and esc.get("prior_power_profile"):
                valid_e = {f.name for f in ActiveEscalationState.__dataclass_fields__.values()}
                self.active_escalation = ActiveEscalationState(**{k: v for k, v in esc.items() if k in valid_e})
            ev = raw.get("recent_events")
            if isinstance(ev, list):
                self.recent_events = [str(x) for x in ev[:20]]
        except Exception:
            self.active_escalation = None

    def _save_state(self) -> None:
        self.cfg_mgr.ensure_directories()
        payload = {
            "restrained": [asdict(r) for r in self.restrained.values()],
            "active_escalation": asdict(self.active_escalation) if self.active_escalation else None,
            "recent_events": self.recent_events[:20],
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _is_protected(self, proc: ProcessInfo, config: AppConfig) -> bool:
        if proc.protected or proc.pid <= 4 or proc.pid == os.getpid():
            return True
        low = proc.name.lower()
        protected_set = {x.lower() for x in (config.excluded_processes + DEFAULT_PROTECTED_PROCESSES)}
        return any(p in low for p in protected_set if p)

    @staticmethod
    def _set_nice_safe(pid: int, new_nice: int, dry_run: bool) -> tuple[bool, int]:
        try:
            p = psutil.Process(pid)
            orig = int(p.nice())
            if dry_run:
                return True, orig
            if platform.system() == "Windows":
                target = getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", orig) if new_nice > 0 else orig
                p.nice(target)
            else:
                p.nice(max(-20, min(19, int(new_nice))))
            return True, orig
        except Exception:
            return False, 0

    def evaluate_tick(
        self,
        processes: list[ProcessInfo],
        active_window_proc: str,
        config: AppConfig,
        dry_run: bool = True,
    ) -> list[str]:
        """Evaluates ProBalance restraint/restore and Auto-Reverted App Profile escalations."""
        notes: list[str] = []
        active_low = (active_window_proc or "").strip().lower()
        thresh = max(10.0, min(95.0, float(getattr(config, "probalance_cpu_threshold", 35.0))))
        enabled = bool(getattr(config, "probalance_enabled", False))

        # 1. Dynamic ProBalance CPU hog restraint & automatic release
        live_pids = {p.pid: p for p in processes}
        for pid in list(self.restrained.keys()):
            rec = self.restrained[pid]
            proc = live_pids.get(pid)
            should_release = (
                not enabled
                or proc is None
                or proc.cpu_percent < (thresh * 0.65)
                or (active_low and active_low in proc.name.lower())
            )
            if should_release:
                if proc is not None and not dry_run:
                    self._set_nice_safe(pid, rec.original_nice, dry_run=False)
                del self.restrained[pid]
                msg = f"Released ProBalance restraint on {rec.name} (PID {pid}); restored priority {rec.original_nice}."
                notes.append(msg)
                self.recent_events.insert(0, f"[{utc_now_iso()}] {msg}")

        if enabled:
            for proc in processes:
                if self._is_protected(proc, config):
                    continue
                if active_low and active_low in proc.name.lower():
                    continue
                if proc.cpu_percent >= thresh and proc.pid not in self.restrained:
                    target_nice = 10
                    ok, orig_nice = self._set_nice_safe(proc.pid, target_nice, dry_run=dry_run)
                    if ok:
                        self.restrained[proc.pid] = RestraintRecord(
                            pid=proc.pid,
                            name=proc.name,
                            original_nice=orig_nice,
                            applied_nice=target_nice,
                            cpu_percent=proc.cpu_percent,
                            reason=f"Background CPU hog ({proc.cpu_percent:.1f}% >= {thresh:.0f}%)",
                        )
                        msg = (
                            f"{'[DRY RUN] ' if dry_run else ''}ProBalance restrained {proc.name} (PID {proc.pid}, "
                            f"CPU {proc.cpu_percent:.1f}% -> nice {target_nice})."
                        )
                        notes.append(msg)
                        self.recent_events.insert(0, f"[{utc_now_iso()}] {msg}")
                        self.journal.log_event("ProBalanceEngine", "restrain_hog", msg, "Preserve foreground responsiveness", "ok")

        # 2. Auto-Reverted App Profiles (Escalate on launch, cleanly revert on exit)
        rules = [r for r in self.load_rules() if r.enabled]
        proc_blobs = [f"{p.name} {p.cmdline}".lower() for p in processes]
        matched_rule: AppProfileRule | None = None
        for r in rules:
            pat = r.app_pattern.strip().lower()
            if pat and any(pat in blob for blob in proc_blobs):
                matched_rule = r
                break

        if matched_rule is not None:
            if self.active_escalation is None or self.active_escalation.rule_id != matched_rule.rule_id:
                prior_prof = self.active_escalation.prior_power_profile if self.active_escalation else config.active_profile
                prior_preset = self.active_escalation.prior_preset_id if self.active_escalation else config.active_preset_id
                self.active_escalation = ActiveEscalationState(
                    rule_id=matched_rule.rule_id,
                    matched_app=matched_rule.app_pattern,
                    prior_power_profile=prior_prof,
                    prior_preset_id=prior_preset,
                )
                if not dry_run:
                    config.apply_profile(matched_rule.target_power_profile)
                    if matched_rule.target_preset_id:
                        config.active_preset_id = matched_rule.target_preset_id
                    self.cfg_mgr.save(config)
                msg = (
                    f"{'[DRY RUN] ' if dry_run else ''}Auto-Escalated to '{matched_rule.target_power_profile}' "
                    f"for '{matched_rule.app_pattern}' (will revert to '{prior_prof}' on exit)."
                )
                notes.append(msg)
                self.recent_events.insert(0, f"[{utc_now_iso()}] {msg}")
                self.journal.log_event("ProBalanceEngine", "app_escalate", msg, matched_rule.notes, "ok")
        elif self.active_escalation is not None:
            prev = self.active_escalation
            self.active_escalation = None
            if not dry_run:
                config.apply_profile(prev.prior_power_profile)
                if prev.prior_preset_id:
                    config.active_preset_id = prev.prior_preset_id
                self.cfg_mgr.save(config)
            msg = (
                f"{'[DRY RUN] ' if dry_run else ''}App '{prev.matched_app}' exited -> Auto-Reverted back to "
                f"'{prev.prior_power_profile}'."
            )
            notes.append(msg)
            self.recent_events.insert(0, f"[{utc_now_iso()}] {msg}")
            self.journal.log_event("ProBalanceEngine", "app_auto_revert", msg, "Watched app closed", "ok")

        self.recent_events = self.recent_events[:20]
        self._save_state()
        return notes

    def revert_all(self, config: AppConfig, dry_run: bool = False) -> tuple[bool, str]:
        released = 0
        for pid, rec in list(self.restrained.items()):
            if not dry_run:
                self._set_nice_safe(pid, rec.original_nice, dry_run=False)
            del self.restrained[pid]
            released += 1
        reverted_app = False
        if self.active_escalation is not None:
            prev = self.active_escalation
            self.active_escalation = None
            if not dry_run:
                config.apply_profile(prev.prior_power_profile)
                if prev.prior_preset_id:
                    config.active_preset_id = prev.prior_preset_id
                self.cfg_mgr.save(config)
            reverted_app = True
        self._save_state()
        msg = f"Reverted ProBalance ({released} process restraint(s) cleared, app escalation reverted={reverted_app})."
        self.recent_events.insert(0, f"[{utc_now_iso()}] {msg}")
        self.journal.log_event("ProBalanceEngine", "revert_all", msg, "User/system requested clean ProBalance reset", "ok")
        return True, msg
