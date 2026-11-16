"""Interrupt Affinity & DPC/Timer Latency Guard Subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform
import statistics
import time
from typing import Any

from .config import ConfigManager
from .journal import ForensicJournal
from .models import utc_now_iso


@dataclass
class InterruptAffinityRule:
    rule_id: str
    device_label: str
    irq_or_bus: str  # usb | nic | audio | gpu | nvme | custom
    target_core_mask: str  # e.g. "2-3" or "E-Cores / CCD1"
    reason: str
    enabled: bool = True
    built_in: bool = False
    command_preview: str = ""
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class IrqSourceInfo:
    irq: str
    device: str
    total_count: int
    bus_hint: str


@dataclass
class LatencyReport:
    timestamp: str
    samples: int
    p50_us: float
    p95_us: float
    p99_us: float
    max_us: float
    grade: str  # Clean | Fair | Elevated Jitter
    irq_sources: list[IrqSourceInfo] = field(default_factory=list)
    notes: str = ""


def default_interrupt_rules() -> list[InterruptAffinityRule]:
    return [
        InterruptAffinityRule(
            rule_id="irq_usb_off_core0",
            device_label="USB xHCI Mouse / DAC Controller",
            irq_or_bus="usb",
            target_core_mask="2-3",
            reason="Moves high-polling-rate mouse & USB audio interrupts off primary game render Core 0.",
            enabled=True,
            built_in=True,
            command_preview=InterruptAndDpcGuard.build_os_affinity_command("usb", "2-3"),
        ),
        InterruptAffinityRule(
            rule_id="irq_nic_rss_spread",
            device_label="Network Adapter (NIC RX/TX Queues)",
            irq_or_bus="nic",
            target_core_mask="2-5",
            reason="Spreads network packet interrupts away from Core 0 to reduce frame-time spikes.",
            enabled=True,
            built_in=True,
            command_preview=InterruptAndDpcGuard.build_os_affinity_command("nic", "2-5"),
        ),
        InterruptAffinityRule(
            rule_id="irq_audio_dedicated",
            device_label="HD Audio / Studio DAC Interrupts",
            irq_or_bus="audio",
            target_core_mask="1",
            reason="Prevents audio buffer underruns & crackling under heavy GPU/NVMe load.",
            enabled=False,
            built_in=True,
            command_preview=InterruptAndDpcGuard.build_os_affinity_command("audio", "1"),
        ),
    ]


class InterruptAndDpcGuard:
    """Measures timer/DPC jitter, inspects IRQ distributions, and manages safe interrupt affinity rules."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.rules_path = cfg_mgr.state_dir / "interrupt_rules.json"

    @staticmethod
    def build_os_affinity_command(bus: str, core_mask: str) -> str:
        safe_bus = "".join(c for c in bus.lower() if c.isalnum() or c in "-_")[:24] or "usb"
        safe_mask = "".join(c for c in core_mask if c.isalnum() or c in ",-")[:24] or "2-3"
        sys_name = platform.system()
        if sys_name == "Windows":
            return (
                f'powershell -NoProfile -Command "# Set InterruptAffinityPolicy for {safe_bus} -> cores {safe_mask}; '
                f'Get-PnpDevice -Class {safe_bus.upper()} | Select-Object FriendlyName,InstanceId"'
            )
        if sys_name == "Darwin":
            return f"# macOS manages IRQ affinity in XNU; inspect IOInterrupts via `ioreg -l | grep -i {safe_bus}`"
        return f"# Linux IRQ affinity for {safe_bus}: echo {safe_mask} | sudo tee /proc/irq/<IRQ>/smp_affinity_list"

    def load_rules(self) -> list[InterruptAffinityRule]:
        if not self.rules_path.exists():
            rules = default_interrupt_rules()
            self.save_rules(rules)
            return rules
        try:
            raw = json.loads(self.rules_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("not a list")
            valid = {f.name for f in InterruptAffinityRule.__dataclass_fields__.values()}
            out = [
                InterruptAffinityRule(**{k: v for k, v in r.items() if k in valid})
                for r in raw
                if isinstance(r, dict) and r.get("rule_id") and r.get("device_label")
            ]
            return out or default_interrupt_rules()
        except Exception:
            rules = default_interrupt_rules()
            self.save_rules(rules)
            return rules

    def save_rules(self, rules: list[InterruptAffinityRule]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.rules_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in rules], indent=2), encoding="utf-8")
        os.replace(tmp, self.rules_path)

    def save_custom_rule(
        self,
        device_label: str,
        irq_or_bus: str,
        target_core_mask: str,
        reason: str = "",
    ) -> InterruptAffinityRule:
        clean_label = " ".join(device_label.strip().split())[:64] or "Custom Device IRQ"
        clean_bus = "".join(c for c in irq_or_bus.strip().lower() if c.isalnum() or c in "-_")[:24] or "custom"
        clean_mask = "".join(c for c in target_core_mask.strip() if c.isalnum() or c in ",-")[:24] or "2-3"
        rid = f"irq_custom_{clean_label.lower().replace(' ', '_')[:20]}"
        rule = InterruptAffinityRule(
            rule_id=rid,
            device_label=clean_label,
            irq_or_bus=clean_bus,
            target_core_mask=clean_mask,
            reason=reason.strip() or f"User-configured interrupt affinity for {clean_label}.",
            enabled=True,
            built_in=False,
            command_preview=self.build_os_affinity_command(clean_bus, clean_mask),
        )
        rules = [r for r in self.load_rules() if r.rule_id != rid]
        rules.append(rule)
        self.save_rules(rules)
        self.journal.log_event(
            "InterruptDpcGuard",
            f"save_rule_{rid}",
            f"Saved interrupt affinity policy '{clean_label}' -> cores {clean_mask}",
            rule.reason,
            "ok",
        )
        return rule

    def delete_rule(self, rule_id: str) -> tuple[bool, str]:
        rules = self.load_rules()
        rem = [r for r in rules if r.rule_id != rule_id or r.built_in]
        if len(rem) == len(rules):
            return False, "Built-in interrupt affinity rules cannot be deleted (or rule not found)."
        self.save_rules(rem)
        self.journal.log_event(
            "InterruptDpcGuard",
            f"delete_rule_{rule_id}",
            f"Deleted custom interrupt rule '{rule_id}'",
            "User removed custom interrupt affinity policy",
            "ok",
        )
        return True, f"Deleted custom interrupt rule '{rule_id}'."

    def apply_rule(self, rule: InterruptAffinityRule, dry_run: bool = True) -> tuple[bool, str, str]:
        cmd = self.build_os_affinity_command(rule.irq_or_bus, rule.target_core_mask)
        rules = self.load_rules()
        for r in rules:
            if r.rule_id == rule.rule_id:
                r.enabled = True
                r.command_preview = cmd
                r.updated_at = utc_now_iso()
        if not dry_run:
            self.save_rules(rules)
        msg = (
            f"{'[DRY RUN] ' if dry_run else ''}Prepared Interrupt Affinity Policy for {rule.device_label} "
            f"(target cores {rule.target_core_mask})."
        )
        self.journal.log_event("InterruptDpcGuard", f"apply_{rule.rule_id}", msg, rule.reason, "ok")
        return True, msg, cmd

    @staticmethod
    def list_irq_sources(limit: int = 12) -> list[IrqSourceInfo]:
        sources: list[IrqSourceInfo] = []
        proc_irq = Path("/proc/interrupts")
        if platform.system() == "Linux" and proc_irq.exists():
            try:
                lines = proc_irq.read_text(encoding="utf-8", errors="ignore").splitlines()
                if lines:
                    header_cols = len(lines[0].split())
                    for ln in lines[1:]:
                        parts = ln.split()
                        if len(parts) < header_cols + 2:
                            continue
                        irq_id = parts[0].rstrip(":")
                        counts = []
                        for tok in parts[1 : 1 + header_cols]:
                            if tok.isdigit():
                                counts.append(int(tok))
                        total = sum(counts)
                        dev = " ".join(parts[1 + len(counts) :])[:64]
                        low = dev.lower()
                        bus = (
                            "usb"
                            if ("xhci" in low or "usb" in low)
                            else ("nic" if ("eth" in low or "iwl" in low or "enp" in low or "nvme" not in low and "net" in low) else ("nvme" if "nvme" in low else ("audio" if ("hda" in low or "snd" in low or "audio" in low) else "system")))
                        )
                        if total > 0 or irq_id.isdigit():
                            sources.append(IrqSourceInfo(irq_id, dev or "IRQ", total, bus))
            except OSError:
                pass
        if not sources:
            sources = [
                IrqSourceInfo("USB", "xHCI Host Controller (User-space probe)", 0, "usb"),
                IrqSourceInfo("NIC", "Primary Network Adapter Queue", 0, "nic"),
                IrqSourceInfo("AUD", "Audio Endpoint / HD Audio Controller", 0, "audio"),
            ]
        sources.sort(key=lambda s: -s.total_count)
        return sources[:limit]

    @classmethod
    def measure_dpc_latency(cls, samples: int = 24, sleep_us: int = 500) -> LatencyReport:
        """Measures user-space timer/scheduler wakeup jitter as a portable DPC/IRQ health proxy."""
        n = max(6, min(120, int(samples)))
        target_ns = max(100, int(sleep_us)) * 1_000
        jitters_us: list[float] = []
        for _ in range(n):
            t0 = time.perf_counter_ns()
            time.sleep(target_ns / 1_000_000_000.0)
            elapsed_ns = time.perf_counter_ns() - t0
            jit_us = max(0.0, (elapsed_ns - target_ns) / 1_000.0)
            jitters_us.append(round(jit_us, 1))

        jitters_us.sort()
        p50 = round(statistics.median(jitters_us), 1)
        p95 = round(jitters_us[min(len(jitters_us) - 1, int(0.95 * len(jitters_us)))], 1)
        p99 = round(jitters_us[min(len(jitters_us) - 1, int(0.99 * len(jitters_us)))], 1)
        mx = round(jitters_us[-1], 1)
        if p95 <= 450.0:
            grade = "Clean"
            note = "Timer wakeup jitter is crisp (<450 us p95); audio and frame pacing look steady."
        elif p95 <= 1500.0:
            grade = "Fair"
            note = "Moderate scheduler jitter detected; consider pinning USB/NIC IRQs off Core 0."
        else:
            grade = "Elevated Jitter"
            note = "High wakeup/DPC latency spikes detected; review busy background processes or USB/NIC IRQs."

        return LatencyReport(
            timestamp=utc_now_iso(),
            samples=n,
            p50_us=p50,
            p95_us=p95,
            p99_us=p99,
            max_us=mx,
            grade=grade,
            irq_sources=cls.list_irq_sources(limit=10),
            notes=note,
        )
