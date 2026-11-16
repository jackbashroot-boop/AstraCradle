"""Real-time cross-platform system telemetry collector using psutil."""

from __future__ import annotations

import math
import os
import platform
import socket
import threading
import time
from typing import Iterable

import psutil

from .models import PartitionInfo, ProcessInfo, SystemSnapshot, utc_now_iso
from .os_adapters import BaseOSAdapter
from .process_advisor import ProcessAdvisor


def format_bytes(num_bytes: int | float) -> str:
    try:
        f = float(num_bytes)
    except Exception:
        return "0 B"
    if not math.isfinite(f) or f <= 0:
        return "0 B"
    val = f
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0 or unit == "TB":
            if unit in ("B", "KB"):
                return f"{val:.0f} {unit}"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} TB"


def format_uptime(seconds: int) -> str:
    days, rem = divmod(max(0, int(seconds)), 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


class TelemetryCollector:
    """Collects non-blocking real OS telemetry across Windows, macOS, and Linux."""

    def __init__(self, excluded_processes: Iterable[str] = ()) -> None:
        self.excluded_lower = {p.lower() for p in excluded_processes}
        try:
            psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass

    def update_exclusions(self, excluded_processes: Iterable[str]) -> None:
        self.excluded_lower = {p.lower() for p in excluded_processes}

    def measure_dns_latency_ms(self, host: str = "example.com", timeout_sec: float = 1.5) -> float | None:
        result: list[float | None] = [None]

        def _probe() -> None:
            start = time.perf_counter()
            try:
                socket.getaddrinfo(host, 80, proto=socket.IPPROTO_TCP)
                result[0] = round((time.perf_counter() - start) * 1000.0, 1)
            except Exception:
                result[0] = None

        t = threading.Thread(target=_probe, daemon=True)
        t.start()
        t.join(timeout=timeout_sec)
        return result[0]

    def collect(
        self,
        adapter: BaseOSAdapter,
        include_dns: bool = False,
        process_limit: int = 30,
        consent_telemetry: bool = True,
        consent_processes: bool = True,
    ) -> SystemSnapshot:
        if not consent_telemetry:
            return SystemSnapshot(
                timestamp=utc_now_iso(),
                os_family=platform.system(),
                os_release=platform.release(),
                hostname=socket.gethostname(),
                architecture=platform.machine(),
                uptime_seconds=0,
                is_elevated=adapter.is_elevated(),
                cpu_percent=0.0,
                cpu_per_core=[0.0],
                cpu_count_logical=psutil.cpu_count(logical=True) or 1,
                cpu_freq_mhz=None,
                load_avg=None,
                mem_total_bytes=0,
                mem_used_bytes=0,
                mem_available_bytes=0,
                mem_percent=0.0,
                swap_total_bytes=0,
                swap_used_bytes=0,
                swap_percent=0.0,
                partitions=[],
                net_bytes_sent=0,
                net_bytes_recv=0,
                dns_latency_ms=None,
                battery_percent=None,
                battery_plugged=None,
                temperature_c=None,
                process_count=0,
                top_processes=[],
            )

        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True) or [0.0]
        cpu_total = round(sum(cpu_per_core) / max(1, len(cpu_per_core)), 1)
        freq = None
        try:
            cf = psutil.cpu_freq()
            if cf:
                freq = round(cf.current, 0)
        except Exception:
            pass

        load_avg = None
        try:
            if hasattr(os, "getloadavg"):
                la = os.getloadavg()
                load_avg = (round(la[0], 2), round(la[1], 2), round(la[2], 2))
        except Exception:
            pass

        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()

        partitions: list[PartitionInfo] = []
        seen_mounts: set[str] = set()
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint in seen_mounts:
                continue
            if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay") and part.mountpoint != "/":
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                seen_mounts.add(part.mountpoint)
                partitions.append(
                    PartitionInfo(
                        device=part.device,
                        mountpoint=part.mountpoint,
                        fstype=part.fstype or "unknown",
                        total_bytes=usage.total,
                        used_bytes=usage.used,
                        free_bytes=usage.free,
                        percent=round(usage.percent, 1),
                    )
                )
            except OSError:
                continue

        net = psutil.net_io_counters()
        dns_ms = self.measure_dns_latency_ms() if include_dns else None

        bat_pct = None
        bat_plugged = None
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                bat_pct = round(bat.percent, 1)
                bat_plugged = bool(bat.power_plugged)
        except Exception:
            pass

        temp_c = None
        try:
            temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
            if temps:
                for _, entries in temps.items():
                    if entries:
                        temp_c = round(entries[0].current, 1)
                        break
        except Exception:
            pass

        procs: list[ProcessInfo] = []
        total_procs = 0
        own_pid = os.getpid()
        if consent_processes:
            for proc in psutil.process_iter(
                ["pid", "name", "username", "cpu_percent", "memory_info", "memory_percent", "status", "cmdline"]
            ):
                total_procs += 1
                try:
                    info = proc.info
                    pid = int(info["pid"])
                    name = str(info.get("name") or f"pid-{pid}")
                    mem_info = info.get("memory_info")
                    mem_mb = round((mem_info.rss / (1024 * 1024)) if mem_info else 0.0, 1)
                    cpu_p = round(float(info.get("cpu_percent") or 0.0), 1)
                    mem_p = round(float(info.get("memory_percent") or 0.0), 1)
                    cmd_list = info.get("cmdline") or []
                    cmdline = " ".join(cmd_list[:8]) if isinstance(cmd_list, list) else str(cmd_list)
                    is_prot = (
                        pid <= 4
                        or pid == own_pid
                        or name.lower() in self.excluded_lower
                        or any(ex in name.lower() for ex in self.excluded_lower if len(ex) > 3)
                    )
                    pobj = ProcessInfo(
                        pid=pid,
                        name=name,
                        username=str(info.get("username") or "-"),
                        cpu_percent=cpu_p,
                        memory_mb=mem_mb,
                        memory_percent=mem_p,
                        status=str(info.get("status") or "running"),
                        cmdline=cmdline[:160],
                        protected=is_prot,
                    )
                    procs.append(ProcessAdvisor.classify_process(pobj))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

        procs.sort(
            key=lambda p: (
                0 if p.category == "ai_chatbot" else 1,
                -(p.memory_mb + p.cpu_percent * 8.0),
            )
        )

        return SystemSnapshot(
            timestamp=utc_now_iso(),
            os_family=platform.system(),
            os_release=platform.release(),
            hostname=socket.gethostname(),
            architecture=platform.machine(),
            uptime_seconds=max(0, int(time.time() - psutil.boot_time())),
            is_elevated=adapter.is_elevated(),
            cpu_percent=cpu_total,
            cpu_per_core=[round(c, 1) for c in cpu_per_core],
            cpu_count_logical=psutil.cpu_count(logical=True) or 1,
            cpu_freq_mhz=freq,
            load_avg=load_avg,
            mem_total_bytes=vm.total,
            mem_used_bytes=vm.used,
            mem_available_bytes=vm.available,
            mem_percent=round(vm.percent, 1),
            swap_total_bytes=sm.total,
            swap_used_bytes=sm.used,
            swap_percent=round(sm.percent, 1),
            partitions=partitions,
            net_bytes_sent=net.bytes_sent if net else 0,
            net_bytes_recv=net.bytes_recv if net else 0,
            dns_latency_ms=dns_ms,
            battery_percent=bat_pct,
            battery_plugged=bat_plugged,
            temperature_c=temp_c,
            process_count=total_procs,
            top_processes=procs[:process_limit],
        )
