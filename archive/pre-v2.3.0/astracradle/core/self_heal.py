"""Silent 7-Invariant Self-Checking & Self-Healing Safeguard Watchdog."""

from __future__ import annotations

from dataclasses import asdict
import json
import time
import uuid

import psutil

from .config import AppConfig, ConfigManager
from .journal import ForensicJournal
from .models import SelfAuditFinding, SelfHealEvent, utc_now_iso
from .os_adapters import BaseOSAdapter
from .rollback import RollbackManager
from .telemetry import format_bytes


class SelfHealEngine:
    """Continuously verifies & auto-repairs AstraCradle's own state, logs, and invariants."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.rollback_mgr = rollback_mgr
        self.journal = journal

    def load_ledger(self) -> list[SelfHealEvent]:
        path = self.cfg_mgr.self_heal_log_path
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            valid = {f.name for f in SelfHealEvent.__dataclass_fields__.values()}
            req = {"event_id", "timestamp", "check_name", "status", "details"}
            out: list[SelfHealEvent] = []
            for item in raw:
                if isinstance(item, dict) and req.issubset(item.keys()):
                    try:
                        out.append(SelfHealEvent(**{k: v for k, v in item.items() if k in valid}))
                    except Exception:
                        continue
            return out
        except Exception:
            return []

    def _save_ledger(self, events: list[SelfHealEvent]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.cfg_mgr.self_heal_log_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(e) for e in events[:120]], indent=2), encoding="utf-8")
        tmp.replace(self.cfg_mgr.self_heal_log_path)

    def _record(self, check_name: str, status: str, details: str, repaired: bool = False) -> SelfHealEvent:
        ev = SelfHealEvent(
            event_id=f"sh-{uuid.uuid4().hex[:8]}",
            timestamp=utc_now_iso(),
            check_name=check_name,
            status=status,
            details=details,
            repaired_automatically=repaired,
        )
        ledger = self.load_ledger()
        ledger.insert(0, ev)
        self._save_ledger(ledger)
        return ev

    def run_self_check(
        self, adapter: BaseOSAdapter, config: AppConfig
    ) -> tuple[list[SelfHealEvent], list[SelfAuditFinding]]:
        """Runs all 7 silent self-check invariants including forensic log self-audit."""
        results: list[SelfHealEvent] = []

        # 1. Workspace directory integrity
        missing_dirs = [
            d
            for d in (
                self.cfg_mgr.base_dir,
                self.cfg_mgr.state_dir,
                self.cfg_mgr.quarantine_dir,
                self.cfg_mgr.snapshots_dir,
                self.cfg_mgr.logs_dir,
                self.cfg_mgr.playbooks_dir,
                self.cfg_mgr.plugins_dir,
                self.cfg_mgr.crash_dir,
                self.cfg_mgr.updates_dir,
            )
            if not d.exists()
        ]
        if missing_dirs:
            self.cfg_mgr.ensure_directories()
            results.append(
                self._record(
                    "Workspace Tree Integrity",
                    "repaired",
                    f"Recreated {len(missing_dirs)} missing state/vault/plugin folder(s) silently.",
                    repaired=True,
                )
            )
        else:
            results.append(
                self._record(
                    "Workspace Tree Integrity",
                    "ok",
                    "All 9 AstraCradle workspace, vault, plugin, and crash directories verified.",
                )
            )

        # 2. Config JSON + SHA-256 integrity
        checksums = self.cfg_mgr._read_checksums()
        cfg_ok = False
        cfg_repaired = False
        cfg_note = ""
        if self.cfg_mgr.config_path.exists():
            try:
                raw_bytes = self.cfg_mgr.config_path.read_bytes()
                parsed = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ValueError("Invalid config root")
                expected = checksums.get("config.json")
                actual = self.cfg_mgr.sha256_bytes(raw_bytes)
                if expected and expected != actual:
                    cfg_repaired = True
                    self.cfg_mgr.save(self.cfg_mgr.load())
                    cfg_note = "Detected config checksum drift; validated schema and re-sealed shadow backup."
                else:
                    cfg_ok = True
                    cfg_note = "Config schema and SHA-256 integrity verified."
            except Exception as exc:
                repaired_cfg = self.cfg_mgr.load()
                self.cfg_mgr.save(repaired_cfg)
                cfg_repaired = True
                cfg_note = f"Corrupt config.json ({exc}); automatically restored from shadow backup."
        else:
            self.cfg_mgr.save(config)
            cfg_repaired = True
            cfg_note = "Missing config.json regenerated safely from active defaults."

        results.append(
            self._record(
                "Configuration & Schema Guard",
                "repaired" if cfg_repaired else ("ok" if cfg_ok else "warning"),
                cfg_note,
                repaired=cfg_repaired,
            )
        )

        # 3. Rollback ledger integrity
        rb_repaired = False
        rb_note = "Rollback ledger verified cleanly."
        if self.cfg_mgr.rollback_path.exists():
            try:
                raw_rb = self.cfg_mgr.rollback_path.read_bytes()
                parsed_rb = json.loads(raw_rb.decode("utf-8"))
                if not isinstance(parsed_rb, list):
                    raise ValueError("Rollback ledger root is not a JSON list")
            except Exception:
                recovered = self.rollback_mgr.load_entries()
                self.rollback_mgr.save_entries(recovered)
                rb_repaired = True
                rb_note = "Corrupted rollback_ledger.json repaired automatically from shadow backup."
        else:
            self.rollback_mgr.save_entries([])
            rb_note = "Initialized empty rollback ledger."
        results.append(
            self._record(
                "Rollback Ledger Integrity",
                "repaired" if rb_repaired else "ok",
                rb_note,
                repaired=rb_repaired,
            )
        )

        # 4. Orphaned .tmp sweep
        orphans = list(self.cfg_mgr.state_dir.glob("*.tmp"))
        stale_orphans = [p for p in orphans if (time.time() - p.stat().st_mtime) > 10]
        if stale_orphans:
            for p in stale_orphans:
                p.unlink(missing_ok=True)
            results.append(
                self._record(
                    "Orphaned Temp Artifact Sweep",
                    "repaired",
                    f"Cleaned {len(stale_orphans)} stale atomic write temp file(s).",
                    repaired=True,
                )
            )
        else:
            results.append(
                self._record(
                    "Orphaned Temp Artifact Sweep",
                    "ok",
                    "Zero orphaned state temp artifacts detected.",
                )
            )

        # 5. Quarantine & Self-Log Headroom Cap
        q_bytes, q_files = self.rollback_mgr.quarantine_stats()
        max_q_bytes = config.quarantine_max_mb * 1024 * 1024
        if q_bytes > max_q_bytes:
            removed, freed = self.rollback_mgr.prune_quarantine(
                config.quarantine_retention_days, config.quarantine_max_mb
            )
            results.append(
                self._record(
                    "Quarantine & Log Cap Guard",
                    "repaired",
                    f"Quarantine exceeded {config.quarantine_max_mb} MB; auto-pruned {removed} item(s) ({format_bytes(freed)}).",
                    repaired=True,
                )
            )
        else:
            results.append(
                self._record(
                    "Quarantine & Log Cap Guard",
                    "ok",
                    f"Quarantine vault healthy ({q_files} files, {format_bytes(q_bytes)} / {config.quarantine_max_mb} MB cap).",
                )
            )

        # 6. Forensic Journal Self-Audit
        audit_findings = self.journal.verify_and_audit()
        flaws = [a for a in audit_findings if a.severity in ("flaw", "error")]
        results.append(
            self._record(
                "Forensic Journal Self-Audit",
                "warning" if flaws else "ok",
                audit_findings[0].title if audit_findings else "Forensic log verified.",
            )
        )

        # 7. OS Adapter & Self-Footprint Sanity
        try:
            rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
            results.append(
                self._record(
                    "Adapter & Self-Footprint Check",
                    "ok",
                    f"OS adapter '{adapter.os_family}' active; AstraCradle RSS footprint {rss_mb:.1f} MB.",
                )
            )
        except Exception as exc:
            results.append(
                self._record(
                    "Adapter & Self-Footprint Check",
                    "warning",
                    f"Telemetry fallback active ({exc}).",
                )
            )

        repaired_cnt = sum(1 for r in results if r.repaired_automatically)
        self.journal.log_event(
            component="SelfHealEngine",
            action="run_self_check",
            how=f"Verified 7 internal invariants ({repaired_cnt} auto-repaired)",
            why="Continuous silent self-protection and corruption prevention",
            outcome="ok" if repaired_cnt == 0 else "warning",
        )
        return results, audit_findings

    def simulate_fault_and_heal(
        self, adapter: BaseOSAdapter, config: AppConfig
    ) -> tuple[list[SelfHealEvent], list[SelfAuditFinding]]:
        self.cfg_mgr.save(config)
        self.cfg_mgr.config_path.write_text("{CORRUPTED_BY_SIMULATION:::", encoding="utf-8")
        self.journal.log_event(
            component="SelfHealEngine",
            action="simulate_fault",
            how="Intentionally injected invalid JSON into config.json",
            why="User triggered Self-Heal verification test",
            outcome="warning",
        )
        return self.run_self_check(adapter, config)
