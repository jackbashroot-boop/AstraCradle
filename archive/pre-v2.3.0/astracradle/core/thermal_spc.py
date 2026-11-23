"""Workload-Aware Thermal & Voltage Baselines with Welford Online SPC Anomaly Detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import os
from typing import Any

from .config import ConfigManager
from .journal import ForensicJournal
from .models import utc_now_iso


WORKLOAD_TIERS = ("idle", "light", "heavy", "gaming")


@dataclass
class WorkloadBaseline:
    tier: str
    count: int = 0
    mean_temp_c: float = 0.0
    m2_temp: float = 0.0
    mean_cpu_pct: float = 0.0
    m2_cpu: float = 0.0
    mean_power_w: float = 0.0
    m2_power: float = 0.0
    power_count: int = 0
    last_updated: str = field(default_factory=utc_now_iso)

    @property
    def std_temp_c(self) -> float:
        if self.count < 2:
            return 0.0
        return round(math.sqrt(max(0.0, self.m2_temp / (self.count - 1))), 2)

    @property
    def ucl_temp_c(self) -> float:
        """Upper Control Limit (mean + 3*sigma, minimum +6C cushion)."""
        if self.count < 3:
            return 85.0
        return round(self.mean_temp_c + max(6.0, 3.0 * self.std_temp_c), 1)

    @property
    def lcl_temp_c(self) -> float:
        if self.count < 3:
            return 20.0
        return round(max(10.0, self.mean_temp_c - max(6.0, 3.0 * self.std_temp_c)), 1)

    @property
    def std_power_w(self) -> float:
        if self.power_count < 2:
            return 0.0
        return round(math.sqrt(max(0.0, self.m2_power / (self.power_count - 1))), 2)


@dataclass
class ThermalSpcAnomaly:
    tier: str
    metric: str  # temperature_c | power_w
    observed: float
    mean: float
    std: float
    z_score: float
    ucl: float
    severity: str  # warning | critical
    summary: str
    detected_at: str = field(default_factory=utc_now_iso)


class ThermalSpcMonitor:
    """Maintains per-workload Welford online statistics & flags 2.5-sigma/3-sigma SPC drift."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.path = cfg_mgr.state_dir / "thermal_spc_baselines.json"
        self.anomalies: list[ThermalSpcAnomaly] = []

    @staticmethod
    def classify_workload(cpu_percent: float, gpu_percent: float | None = None, is_fullscreen: bool = False) -> str:
        try:
            cpu = float(cpu_percent)
            if not math.isfinite(cpu):
                cpu = 0.0
        except Exception:
            cpu = 0.0
        gpu = 0.0
        if gpu_percent is not None:
            try:
                g = float(gpu_percent)
                if math.isfinite(g):
                    gpu = g
            except Exception:
                pass
        if is_fullscreen or gpu >= 60.0 or cpu >= 75.0:
            return "gaming"
        if cpu >= 40.0 or gpu >= 30.0:
            return "heavy"
        if cpu >= 14.0:
            return "light"
        return "idle"

    def load_baselines(self) -> dict[str, WorkloadBaseline]:
        base = {t: WorkloadBaseline(tier=t) for t in WORKLOAD_TIERS}
        if not self.path.exists():
            return base
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return base
            valid = {f.name for f in WorkloadBaseline.__dataclass_fields__.values()}
            for t in WORKLOAD_TIERS:
                item = raw.get(t)
                if isinstance(item, dict):
                    cleaned = {k: v for k, v in item.items() if k in valid}
                    wb = WorkloadBaseline(**cleaned)
                    wb.tier = t
                    wb.count = max(0, int(wb.count)) if isinstance(wb.count, (int, float)) and math.isfinite(wb.count) else 0
                    for fk in ("mean_temp_c", "m2_temp", "mean_cpu_pct", "m2_cpu", "mean_power_w", "m2_power"):
                        v = getattr(wb, fk, 0.0)
                        if not isinstance(v, (int, float)) or not math.isfinite(v):
                            setattr(wb, fk, 0.0)
                    wb.m2_temp = max(0.0, float(wb.m2_temp))
                    wb.m2_cpu = max(0.0, float(wb.m2_cpu))
                    wb.m2_power = max(0.0, float(wb.m2_power))
                    base[t] = wb
            return base
        except Exception:
            return base

    def save_baselines(self, baselines: dict[str, WorkloadBaseline]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: asdict(v) for k, v in baselines.items()}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def reset_baselines(self, tier: str | None = None) -> None:
        baselines = self.load_baselines()
        if tier and tier in WORKLOAD_TIERS:
            baselines[tier] = WorkloadBaseline(tier=tier)
        else:
            baselines = {t: WorkloadBaseline(tier=t) for t in WORKLOAD_TIERS}
        self.anomalies.clear()
        self.save_baselines(baselines)
        self.journal.log_event(
            "ThermalSpcMonitor",
            "reset_baselines",
            f"Reset Welford SPC baselines ({tier or 'all tiers'})",
            "User recalibrated thermal & power baselines",
            "ok",
        )

    def update_sample(
        self,
        tier: str,
        temp_c: float | None,
        cpu_percent: float,
        power_w: float | None = None,
    ) -> list[ThermalSpcAnomaly]:
        """Updates Welford accumulator for `tier` and checks for SPC control-limit anomalies."""
        t_key = tier if tier in WORKLOAD_TIERS else "light"
        baselines = self.load_baselines()
        wb = baselines[t_key]
        found: list[ThermalSpcAnomaly] = []

        # Sanitize CPU
        try:
            cpu_v = float(cpu_percent)
            if not math.isfinite(cpu_v):
                cpu_v = 0.0
        except Exception:
            cpu_v = 0.0
        cpu_v = max(0.0, min(100.0, cpu_v))

        # Check temperature SPC anomaly BEFORE polluting baseline if extreme 3-sigma outlier
        if temp_c is not None:
            try:
                tv = float(temp_c)
            except Exception:
                tv = float("nan")
            if math.isfinite(tv) and -20.0 <= tv <= 140.0:
                if wb.count >= 5:
                    std = wb.std_temp_c
                    eff_std = max(1.5, std)
                    z = (tv - wb.mean_temp_c) / eff_std
                    ucl = wb.ucl_temp_c
                    if tv > ucl or z >= 2.8:
                        sev = "critical" if (z >= 3.5 or tv >= 92.0) else "warning"
                        anom = ThermalSpcAnomaly(
                            tier=t_key,
                            metric="temperature_c",
                            observed=round(tv, 1),
                            mean=round(wb.mean_temp_c, 1),
                            std=std,
                            z_score=round(z, 2),
                            ucl=ucl,
                            severity=sev,
                            summary=(
                                f"Thermal SPC drift in '{t_key}': {tv:.1f}C exceeds baseline {wb.mean_temp_c:.1f}C "
                                f"(UCL {ucl:.1f}C, z={z:.1f}s)."
                            ),
                        )
                        found.append(anom)
                        self.anomalies.insert(0, anom)
                        self.anomalies = self.anomalies[:25]
                        self.journal.log_event(
                            "ThermalSpcMonitor",
                            f"spc_drift_{t_key}",
                            anom.summary,
                            "Detected statistical thermal deviation vs learned workload baseline",
                            "warning",
                        )

                # Welford online update for temp & cpu
                wb.count += 1
                d1 = tv - wb.mean_temp_c
                wb.mean_temp_c += d1 / wb.count
                d2 = tv - wb.mean_temp_c
                wb.m2_temp = max(0.0, wb.m2_temp + d1 * d2)

                dc1 = cpu_v - wb.mean_cpu_pct
                wb.mean_cpu_pct += dc1 / wb.count
                dc2 = cpu_v - wb.mean_cpu_pct
                wb.m2_cpu = max(0.0, wb.m2_cpu + dc1 * dc2)
                wb.last_updated = utc_now_iso()

        # Optional GPU/Board Power SPC update
        if power_w is not None:
            try:
                pw = float(power_w)
            except Exception:
                pw = float("nan")
            if math.isfinite(pw) and 0.0 <= pw <= 1200.0:
                if wb.power_count >= 5:
                    p_std = max(5.0, wb.std_power_w)
                    pz = (pw - wb.mean_power_w) / p_std
                    p_ucl = round(wb.mean_power_w + max(18.0, 3.0 * p_std), 1)
                    if pw > p_ucl or pz >= 3.0:
                        anom = ThermalSpcAnomaly(
                            tier=t_key,
                            metric="power_w",
                            observed=round(pw, 1),
                            mean=round(wb.mean_power_w, 1),
                            std=wb.std_power_w,
                            z_score=round(pz, 2),
                            ucl=p_ucl,
                            severity="warning",
                            summary=(
                                f"Power SPC spike in '{t_key}': {pw:.1f}W vs baseline {wb.mean_power_w:.1f}W "
                                f"(UCL {p_ucl:.1f}W, z={pz:.1f}s)."
                            ),
                        )
                        found.append(anom)
                        self.anomalies.insert(0, anom)
                        self.anomalies = self.anomalies[:25]
                wb.power_count += 1
                dp1 = pw - wb.mean_power_w
                wb.mean_power_w += dp1 / wb.power_count
                dp2 = pw - wb.mean_power_w
                wb.m2_power = max(0.0, wb.m2_power + dp1 * dp2)
                wb.last_updated = utc_now_iso()

        self.save_baselines(baselines)
        return found
