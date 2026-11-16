"""Performance Guardian: RAM Leak Watchdog, Before/After Benchmark, App Auto-Detector, Net/DNS & USB/Power Advisor."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import tempfile
import time

import psutil

from .config import AppConfig, ConfigManager
from .models import ProcessInfo, utc_now_iso


@dataclass
class MemoryLeakSuspect:
    pid: int
    name: str
    start_mb: float
    current_mb: float
    growth_mb: float
    samples: int


@dataclass
class BenchmarkResult:
    timestamp: str
    label: str
    cpu_mops_per_sec: float
    mem_copy_mb_per_sec: float
    disk_write_mb_per_sec: float
    disk_read_mb_per_sec: float
    dns_ms: float | None
    composite_score: int


@dataclass
class NetworkAppActivity:
    pid: int
    name: str
    connections: int
    remote_endpoints: list[str]


@dataclass
class HostsAuditResult:
    hosts_path: str
    entry_count: int
    suspicious_entries: list[str]
    status: str  # clean | review


@dataclass
class DeviceGlitchCheck:
    check_id: str
    title: str
    status: str  # ok | notice
    details: str
    remediation: str


class MemoryLeakWatchdog:
    """Tracks per-process RSS over time to catch creeping memory leaks before swap thrashing."""

    def __init__(self, max_history: int = 24) -> None:
        self.max_history = max_history
        self._series: dict[tuple[int, str], list[float]] = defaultdict(list)

    def record_snapshot(self, processes: list[ProcessInfo]) -> None:
        alive_keys = set()
        for p in processes:
            key = (p.pid, p.name)
            alive_keys.add(key)
            arr = self._series[key]
            arr.append(float(p.memory_mb))
            if len(arr) > self.max_history:
                self._series[key] = arr[-self.max_history :]
        # Prune exited PIDs
        for k in list(self._series.keys()):
            if k not in alive_keys:
                del self._series[k]

    def detect_leaks(self, min_samples: int = 4, min_growth_mb: float = 48.0) -> list[MemoryLeakSuspect]:
        suspects: list[MemoryLeakSuspect] = []
        for (pid, name), vals in self._series.items():
            if len(vals) < min_samples:
                continue
            growth = vals[-1] - vals[0]
            # Monotonic or mostly rising trend check
            rises = sum(1 for i in range(1, len(vals)) if vals[i] >= vals[i - 1] - 0.5)
            if growth >= min_growth_mb and rises >= len(vals) - 2:
                suspects.append(
                    MemoryLeakSuspect(
                        pid=pid,
                        name=name,
                        start_mb=round(vals[0], 1),
                        current_mb=round(vals[-1], 1),
                        growth_mb=round(growth, 1),
                        samples=len(vals),
                    )
                )
        suspects.sort(key=lambda s: -s.growth_mb)
        return suspects


class MicroBenchmark:
    """Fast, strictly local 1-second Before/After CPU/RAM/Disk sanity benchmark."""

    def __init__(self, cfg_mgr: ConfigManager) -> None:
        self.cfg_mgr = cfg_mgr
        self.history_path = cfg_mgr.snapshots_dir / "benchmarks.json"

    def run(self, label: str = "Baseline") -> BenchmarkResult:
        # 1. CPU integer/float throughput
        t0 = time.perf_counter()
        acc = 0
        for i in range(1, 250_000):
            acc = (acc + (i * 31) ^ (i >> 2)) & 0xFFFFFFFF
        cpu_dt = max(1e-4, time.perf_counter() - t0)
        cpu_mops = round((0.25 / cpu_dt), 2)

        # 2. Memory buffer copy throughput (8 MB x 6 passes = 48 MB)
        src = bytearray(b"A" * (8 * 1024 * 1024))
        t1 = time.perf_counter()
        for _ in range(6):
            _dup = bytes(src)
        mem_dt = max(1e-4, time.perf_counter() - t1)
        mem_mb_s = round(48.0 / mem_dt, 1)

        # 3. Sequential temp disk write + read (4 MB)
        payload = os.urandom(4 * 1024 * 1024)
        with tempfile.NamedTemporaryFile(dir=self.cfg_mgr.snapshots_dir, delete=False) as tf:
            tmp_p = Path(tf.name)
        try:
            t2 = time.perf_counter()
            tmp_p.write_bytes(payload)
            w_dt = max(1e-4, time.perf_counter() - t2)
            t3 = time.perf_counter()
            _ = tmp_p.read_bytes()
            r_dt = max(1e-4, time.perf_counter() - t3)
        finally:
            tmp_p.unlink(missing_ok=True)
        w_mb_s = round(4.0 / w_dt, 1)
        r_mb_s = round(4.0 / r_dt, 1)

        # Score normalized nicely for visual comparison
        score = max(10, min(100, int(min(40, cpu_mops * 8) + min(30, mem_mb_s / 150) + min(30, (w_mb_s + r_mb_s) / 40))))
        res = BenchmarkResult(
            timestamp=utc_now_iso(),
            label=label,
            cpu_mops_per_sec=cpu_mops,
            mem_copy_mb_per_sec=mem_mb_s,
            disk_write_mb_per_sec=w_mb_s,
            disk_read_mb_per_sec=r_mb_s,
            dns_ms=None,
            composite_score=score,
        )
        hist = self.load_history()
        hist.insert(0, res)
        self.history_path.write_text(json.dumps([asdict(x) for x in hist[:10]], indent=2), encoding="utf-8")
        return res

    def load_history(self) -> list[BenchmarkResult]:
        if not self.history_path.exists():
            return []
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            valid = {f.name for f in BenchmarkResult.__dataclass_fields__.values()}
            return [BenchmarkResult(**{k: v for k, v in r.items() if k in valid}) for r in raw if isinstance(r, dict)]
        except Exception:
            return []


class AppAutoDetector:
    """Watches running apps for configured games/3D/compile workloads and suggests/toggles scripts."""

    def __init__(self) -> None:
        self.active_auto_script: str | None = None
        self.triggered_by_app: str | None = None

    def evaluate(
        self,
        processes: list[ProcessInfo],
        config: AppConfig,
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (action: 'enable'|'revert'|None, script_id, matched_app_name)."""
        if not config.app_auto_detect_enabled or not config.auto_detect_rules:
            return None, None, None

        proc_names = [f"{p.name} {p.cmdline}".lower() for p in processes]
        for kw, script_id in config.auto_detect_rules.items():
            kl = kw.strip().lower()
            if kl and any(kl in pn for pn in proc_names):
                if self.active_auto_script != script_id:
                    self.active_auto_script = script_id
                    self.triggered_by_app = kw
                    return "enable", script_id, kw
                return None, None, None

        # None of the watched apps are running anymore -> revert if we auto-enabled one
        if self.active_auto_script is not None:
            sid = self.active_auto_script
            app_nm = self.triggered_by_app
            self.active_auto_script = None
            self.triggered_by_app = None
            return "revert", sid, app_nm
        return None, None, None


class NetworkAndDnsAdvisor:
    """Per-app socket activity snapshot, hosts integrity audit, and DNS latency check."""

    @staticmethod
    def sample_network_apps(limit: int = 10) -> list[NetworkAppActivity]:
        counts: dict[tuple[int, str], set[str]] = defaultdict(set)
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.pid and conn.raddr:
                    try:
                        name = psutil.Process(conn.pid).name()
                    except Exception:
                        name = f"pid-{conn.pid}"
                    ep = f"{conn.raddr.ip}:{conn.raddr.port}"
                    counts[(conn.pid, name)].add(ep)
        except Exception:
            pass
        rows = [
            NetworkAppActivity(pid=pid, name=name, connections=len(eps), remote_endpoints=sorted(eps)[:4])
            for (pid, name), eps in counts.items()
        ]
        rows.sort(key=lambda r: -r.connections)
        return rows[:limit]

    @staticmethod
    def audit_hosts_file() -> HostsAuditResult:
        if platform.system() == "Windows":
            hp = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "drivers" / "etc" / "hosts"
        else:
            hp = Path("/etc/hosts")
        if not hp.exists():
            return HostsAuditResult(str(hp), 0, [], "clean")
        suspicious: list[str] = []
        entries = 0
        try:
            for line in hp.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                entries += 1
                parts = s.split()
                if len(parts) >= 2:
                    ip, host = parts[0], parts[1].lower()
                    if ip not in ("127.0.0.1", "::1", "0.0.0.0", "255.255.255.255", "fe00::0", "ff00::0", "ff02::1", "ff02::2"):
                        if any(k in host for k in ("microsoft", "apple", "google", "github", "windowsupdate", "steam")):
                            suspicious.append(s)
        except OSError:
            pass
        return HostsAuditResult(str(hp), entries, suspicious, "review" if suspicious else "clean")

    @staticmethod
    def compare_dns_resolvers(hosts: tuple[str, ...] = ("example.com",), timeout_sec: float = 1.0) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for h in hosts:
            t0 = time.perf_counter()
            try:
                socket.setdefaulttimeout(timeout_sec)
                socket.getaddrinfo(h, 80)
                out[h] = round((time.perf_counter() - t0) * 1000.0, 1)
            except Exception:
                out[h] = None
        return out


class DeviceGlitchAdvisor:
    """Checks common USB selective-suspend, audio & GPU power-sleep settings."""

    @staticmethod
    def inspect() -> list[DeviceGlitchCheck]:
        sys_name = platform.system()
        checks: list[DeviceGlitchCheck] = []
        if sys_name == "Linux":
            usb_auto = Path("/sys/module/usbcore/parameters/autosuspend")
            if usb_auto.exists():
                try:
                    val = usb_auto.read_text(encoding="utf-8").strip()
                    if val not in ("-1", "0"):
                        checks.append(
                            DeviceGlitchCheck(
                                "linux_usb_suspend",
                                "USB Autosuspend Active",
                                "notice",
                                f"usbcore.autosuspend={val}s (may cause mouse/audio DAC micro-stutters).",
                                "Temporarily disable if peripherals stutter: echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend",
                            )
                        )
                    else:
                        checks.append(
                            DeviceGlitchCheck(
                                "linux_usb_suspend",
                                "USB Autosuspend Safe",
                                "ok",
                                "USB autosuspend is disabled (-1/0).",
                                "No action needed.",
                            )
                        )
                except OSError:
                    pass
        elif sys_name == "Windows":
            checks.append(
                DeviceGlitchCheck(
                    "win_usb_suspend",
                    "Windows USB Selective Suspend",
                    "notice",
                    "Selective suspend can sleep gaming mice or USB audio interfaces under load.",
                    "In Power Options -> USB settings -> USB selective suspend: set to Disabled for desktop/gaming.",
                )
            )
        else:
            checks.append(
                DeviceGlitchCheck(
                    "mac_powernap",
                    "macOS Power Nap & Audio Drift",
                    "ok",
                    "Use 'pmset -g' to verify wake-on-network and Power Nap during studio recording.",
                    "sudo pmset -a powernap 0   # Disable Power Nap during latency-sensitive audio/3D work",
                )
            )
        if not checks:
            checks.append(
                DeviceGlitchCheck(
                    "device_power_ok",
                    "Device Power & Peripheral Hygiene",
                    "ok",
                    "No USB/audio power-sleep anomalies detected in user-space sensors.",
                    "No action needed.",
                )
            )
        return checks
