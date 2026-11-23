"""Generates plain-English diagnosis summaries and dual-mode (DIY + Auto) playbooks."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from .config import AppConfig
from .diagnostics import DiagnosticReport
from .models import Playbook, PlaybookStep, utc_now_iso
from .telemetry import format_bytes


class PlaybookBuilder:
    """Compiles a DiagnosticReport into a step-by-step Playbook."""

    def build(self, report: DiagnosticReport, config: AppConfig) -> Playbook:
        snap = report.snapshot
        total_reclaimable = sum(f.reclaimable_bytes for f in report.findings)

        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        sorted_findings = sorted(
            report.findings, key=lambda f: (order.get(f.severity, 9), -f.impact_score)
        )

        steps: list[PlaybookStep] = []
        for idx, f in enumerate(sorted_findings, start=1):
            steps.append(
                PlaybookStep(
                    step_number=idx,
                    finding_id=f.finding_id,
                    title=f.title,
                    subsystem=f.subsystem,
                    severity=f.severity,
                    safety_tier=f.safety_tier,
                    risk_level=f.risk_level,
                    summary=f.summary,
                    evidence=f.evidence,
                    manual_steps=list(f.manual_steps),
                    manual_commands=list(f.manual_commands),
                    action_id=f.action_id,
                    action_params=dict(f.action_params),
                    can_auto_fix=f.can_auto_fix,
                    status="pending" if f.can_auto_fix else "manual_only",
                    confidence_percent=getattr(f, "confidence_percent", 85),
                    evidence_signals=list(getattr(f, "evidence_signals", [])),
                )
            )

        score = report.health_score
        if score >= 90:
            headline = "Dyson Core Optimal — Minor Hygiene Available"
        elif score >= 75:
            headline = "System Healthy — Targeted Tuning Recommended"
        elif score >= 55:
            headline = "Moderate Load & Clutter — Optimization Advised"
        else:
            headline = "High Pressure Detected — Prioritized Playbook Ready"

        actionable_count = sum(1 for s in steps if s.can_auto_fix)
        manual_count = len(steps) - actionable_count
        exec_summary = (
            f"Host '{snap.hostname}' ({snap.os_family} {snap.os_release}, {snap.architecture}) scored "
            f"{score}/100 under profile '{config.active_profile}'. "
            f"CPU is at {snap.cpu_percent:.1f}%, RAM at {snap.mem_percent:.1f}%, with "
            f"{format_bytes(total_reclaimable)} safely reclaimable and {len(report.recommendations)} "
            f"AI/process optimization suggestion(s). Playbook has {len(steps)} step(s): "
            f"{actionable_count} auto-executable with Quarantine & Rollback, {manual_count} DIY guided."
        )

        return Playbook(
            playbook_id=f"pb-{uuid.uuid4().hex[:8]}",
            created_at=utc_now_iso(),
            os_family=snap.os_family,
            profile_name=config.active_profile,
            health_score=score,
            summary_headline=headline,
            executive_summary=exec_summary,
            total_reclaimable_bytes=total_reclaimable,
            steps=steps,
        )

    @staticmethod
    def to_markdown(playbook: Playbook) -> str:
        lines = [
            f"# AstraCradle Optimization Playbook ({playbook.playbook_id})",
            "",
            f"- **Generated:** {playbook.created_at}",
            f"- **Target OS:** {playbook.os_family}",
            f"- **Active Profile:** {playbook.profile_name}",
            f"- **Equilibrium Health Score:** {playbook.health_score}/100",
            f"- **Reclaimable Footprint:** {format_bytes(playbook.total_reclaimable_bytes)}",
            "",
            f"## {playbook.summary_headline}",
            "",
            playbook.executive_summary,
            "",
            "---",
            "",
        ]
        for step in playbook.steps:
            mode = "Auto-Fix Supported (AstraCradle Guarded)" if step.can_auto_fix else "Manual DIY Guidance"
            lines.extend(
                [
                    f"### Step {step.step_number}: {step.title}",
                    f"- **Subsystem:** `{step.subsystem}` | **Severity:** `{step.severity}` | "
                    f"**Safety Tier:** `{step.safety_tier}` | **Risk:** `{step.risk_level}`",
                    f"- **Mode:** {mode} | **Status:** `{step.status}`",
                    f"- **Diagnosis:** {step.summary}",
                    f"- **Evidence:** `{step.evidence}`",
                    "",
                    "**DIY Instructions:**",
                ]
            )
            for ms in step.manual_steps:
                lines.append(f"1. {ms}")
            if step.manual_commands:
                lines.append("")
                lines.append("**Commands:**")
                lines.append("```bash")
                lines.extend(step.manual_commands)
                lines.append("```")
            lines.append("")
        return "\n".join(lines)

    def export(self, playbook: Playbook, out_dir: Path, fmt: str = "md") -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path = out_dir / f"{playbook.playbook_id}.json"
            path.write_text(json.dumps(playbook.to_dict(), indent=2), encoding="utf-8")
            return path
        path = out_dir / f"{playbook.playbook_id}.md"
        path.write_text(self.to_markdown(playbook), encoding="utf-8")
        return path
