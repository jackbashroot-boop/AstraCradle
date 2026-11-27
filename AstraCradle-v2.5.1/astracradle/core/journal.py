"""Comprehensive hash-chained Forensic Audit Journal & Self-Reading Log Verifier."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
import uuid

from .config import ConfigManager
from .models import ForensicEntry, SelfAuditFinding, utc_now_iso


class ForensicJournal:
    """Logs EVERYTHING AstraCradle does, HOW it did it, and WHY, then audits itself."""

    def __init__(self, cfg_mgr: ConfigManager) -> None:
        self.cfg_mgr = cfg_mgr

    @staticmethod
    def _compute_hash(entry_dict: dict) -> str:
        payload = json.dumps(
            {
                "entry_id": entry_dict.get("entry_id", ""),
                "timestamp": entry_dict.get("timestamp", ""),
                "component": entry_dict.get("component", ""),
                "action": entry_dict.get("action", ""),
                "how": entry_dict.get("how", ""),
                "why": entry_dict.get("why", ""),
                "outcome": entry_dict.get("outcome", ""),
                "details": entry_dict.get("details", ""),
                "prev_hash": entry_dict.get("prev_hash", ""),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:24]

    def _last_hash(self) -> str:
        entries = self.read_entries(limit=1, newest_first=True)
        return entries[0].entry_hash if entries else "GENESIS"

    def log_event(
        self,
        component: str,
        action: str,
        how: str,
        why: str,
        outcome: str = "ok",
        details: str = "",
    ) -> ForensicEntry:
        self.cfg_mgr.ensure_directories()
        prev_hash = self._last_hash()
        entry = ForensicEntry(
            entry_id=f"fj-{uuid.uuid4().hex[:8]}",
            timestamp=utc_now_iso(),
            component=component,
            action=action,
            how=how,
            why=why,
            outcome=outcome,
            details=details,
            prev_hash=prev_hash,
            entry_hash="",
        )
        d = asdict(entry)
        entry.entry_hash = self._compute_hash(d)
        d["entry_hash"] = entry.entry_hash

        with self.cfg_mgr.forensic_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(d) + "\n")
        return entry

    def read_entries(self, limit: int = 200, newest_first: bool = True) -> list[ForensicEntry]:
        path = self.cfg_mgr.forensic_log_path
        if not path.exists():
            return []
        valid = {f.name for f in ForensicEntry.__dataclass_fields__.values()}
        entries: list[ForensicEntry] = []
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    if isinstance(raw, dict) and {"entry_id", "timestamp", "component", "action", "how", "why", "outcome"}.issubset(raw.keys()):
                        entries.append(ForensicEntry(**{k: v for k, v in raw.items() if k in valid}))
                except Exception:
                    continue
        except OSError:
            return []
        if newest_first:
            entries.reverse()
        return entries[:limit]

    def verify_and_audit(self) -> list[SelfAuditFinding]:
        """Reads AstraCradle's own forensic journal, validates integrity, and flags errors/flaws."""
        path = self.cfg_mgr.forensic_log_path
        findings: list[SelfAuditFinding] = []
        if not path.exists():
            return [
                SelfAuditFinding(
                    audit_id="aud-init",
                    timestamp=utc_now_iso(),
                    severity="ok",
                    title="Forensic Journal Initialized",
                    explanation="No prior log entries exist yet; hash-chained logging is active.",
                    remediation="No action needed.",
                )
            ]

        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
        malformed = 0
        chain_breaks = 0
        prev = "GENESIS"
        outcomes = Counter()
        failed_actions = Counter()

        for ln in lines:
            try:
                raw = json.loads(ln)
                if not isinstance(raw, dict) or "entry_hash" not in raw or "prev_hash" not in raw:
                    malformed += 1
                    continue
                expected_hash = self._compute_hash(raw)
                if raw.get("prev_hash") != prev or raw.get("entry_hash") != expected_hash:
                    chain_breaks += 1
                prev = str(raw.get("entry_hash") or prev)
                outcome = str(raw.get("outcome") or "ok")
                outcomes[outcome] += 1
                if outcome in ("error", "warning"):
                    failed_actions[str(raw.get("action") or "unknown")] += 1
            except Exception:
                malformed += 1

        if malformed > 0:
            findings.append(
                SelfAuditFinding(
                    audit_id=f"aud-{uuid.uuid4().hex[:6]}",
                    timestamp=utc_now_iso(),
                    severity="flaw",
                    title=f"{malformed} Malformed Forensic Line(s) Detected",
                    explanation="One or more lines in forensic_journal.jsonl failed JSON parsing (possible abrupt crash mid-write).",
                    remediation="Run Self-Heal check or inspect Crash Post-Mortem Analyzer for pre-crash context.",
                )
            )
        if chain_breaks > 0:
            findings.append(
                SelfAuditFinding(
                    audit_id=f"aud-{uuid.uuid4().hex[:6]}",
                    timestamp=utc_now_iso(),
                    severity="notice",
                    title=f"{chain_breaks} Hash-Chain Discontinuity Detected",
                    explanation="Journal chain had a break (can occur after external truncation or abrupt power loss).",
                    remediation="Inspect recent entries in Forensic Journal & Safeguards.",
                )
            )

        for act, cnt in failed_actions.items():
            if cnt >= 2:
                findings.append(
                    SelfAuditFinding(
                        audit_id=f"aud-{uuid.uuid4().hex[:6]}",
                        timestamp=utc_now_iso(),
                        severity="error",
                        title=f"Recurring Error Pattern in '{act}' ({cnt}x)",
                        explanation=f"The action '{act}' logged warnings or errors {cnt} times in recent runs.",
                        remediation="Check elevation permissions or switch this action to Manual Playbook mode.",
                    )
                )

        if not findings:
            findings.append(
                SelfAuditFinding(
                    audit_id=f"aud-{uuid.uuid4().hex[:6]}",
                    timestamp=utc_now_iso(),
                    severity="ok",
                    title=f"Forensic Audit Clean ({len(lines)} Chained Entries Verified)",
                    explanation=(
                        f"All {len(lines)} forensic log entries passed SHA-256 chain verification with zero "
                        f"malformed records ({outcomes.get('ok', 0)} OK, {outcomes.get('warning', 0)} warnings)."
                    ),
                    remediation="All subsystems operating within verified invariants.",
                )
            )

        return findings
