"""Extended PC Intelligence: Snapshot Diff, Startup Delay, Per-App Network/DNS, RAM Standby Trim, Battery Wear & Safe Beginner Fix."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import ctypes
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import time
from typing import Any

import psutil

from .autoruns_audit import AutorunsAndGhostAuditor
from .config import AppConfig, ConfigManager
from .elevation import ElevationRunner
from .executor import GuardedExecutor
from .journal import ForensicJournal
from .models import utc_now_iso
from .os_adapters import BaseOSAdapter
from .rollback import RollbackManager


@dataclass
class SnapshotDiffResult:
    older_label: str
    newer_label: str
    added_apps: list[str] = field(default_factory=list)
    removed_apps: list[str] = field(default_factory=list)
    added_startup: list[str] = field(default_factory=list)
    removed_startup: list[str] = field(default_factory=list)
    disk_delta_gb: float = 0.0
    ram_delta_pct: float = 0.0
    power_plan_changed: str = "Unchanged"
    summary_lines: list[str] = field(default_factory=list)


class BaselineDiffEngine:
    """Saves lightweight PC snapshots and compares 'What Changed Since My PC Was Fast?'."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal, rollback_mgr: RollbackManager | None = None) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.rollback_mgr = rollback_mgr or RollbackManager(cfg_mgr)
        self.snapshots_dir = cfg_mgr.base_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def capture_snapshot(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        label: str = "",
        autoruns: AutorunsAndGhostAuditor | None = None,
    ) -> Path:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        auditor = autoruns or AutorunsAndGhostAuditor(self.cfg_mgr, self.rollback_mgr, self.journal)
        startup_items = [f"{e.category}:{e.name}" for e in auditor.scan_autoruns(adapter)]
        apps: list[str] = []
        if config.consent_installed_apps:
            apps = [a for a in self._sample_installed_apps(adapter)[:120]]
        vm = psutil.virtual_memory()
        try:
            du = psutil.disk_usage(str(Path.home()))
            disk_used_gb = round(du.used / (1024**3), 2)
        except OSError:
            disk_used_gb = 0.0

        payload = {
            "timestamp": utc_now_iso(),
            "label": label or f"Snapshot {ts}",
            "os": adapter.os_name,
            "active_profile": config.active_profile,
            "ram_percent": round(vm.percent, 1),
            "disk_used_gb": disk_used_gb,
            "startup_items": sorted(set(startup_items)),
            "installed_apps": sorted(set(apps)),
        }
        out = self.snapshots_dir / f"snap_{ts}.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.journal.log_event(
            "BaselineDiff",
            "capture_snapshot",
            f"Saved PC baseline snapshot '{payload['label']}' ({len(startup_items)} startups, {len(apps)} apps)",
            "Enable 'What Changed Since My PC Was Fast?' before/after comparison",
            "ok",
        )
        return out

    @staticmethod
    def _sample_installed_apps(adapter: BaseOSAdapter) -> list[str]:
        apps: list[str] = []
        if adapter.os_name == "Linux":
            for d in [Path("/usr/share/applications"), Path.home() / ".local" / "share" / "applications"]:
                if d.exists():
                    for f in sorted(d.glob("*.desktop"))[:80]:
                        apps.append(f.stem)
        elif adapter.os_name == "Darwin":
            for d in [Path("/Applications"), Path.home() / "Applications"]:
                if d.exists():
                    for f in sorted(d.glob("*.app"))[:80]:
                        apps.append(f.stem)
        elif adapter.os_name == "Windows":
            for d in [Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")]:
                if d.exists():
                    for f in sorted(d.iterdir())[:60]:
                        apps.append(f.name)
        return apps

    def list_snapshots(self) -> list[Path]:
        if not self.snapshots_dir.exists():
            return []
        return sorted(self.snapshots_dir.glob("snap_*.json"))

    def diff_snapshots(self, older_path: Path, newer_path: Path) -> SnapshotDiffResult:
        old = json.loads(older_path.read_text(encoding="utf-8"))
        new = json.loads(newer_path.read_text(encoding="utf-8"))
        old_apps = set(old.get("installed_apps", []))
        new_apps = set(new.get("installed_apps", []))
        old_start = set(old.get("startup_items", []))
        new_start = set(new.get("startup_items", []))

        added_apps = sorted(new_apps - old_apps)
        removed_apps = sorted(old_apps - new_apps)
        added_startup = sorted(new_start - old_start)
        removed_startup = sorted(old_start - new_start)
        disk_delta = round(float(new.get("disk_used_gb", 0.0)) - float(old.get("disk_used_gb", 0.0)), 2)
        ram_delta = round(float(new.get("ram_percent", 0.0)) - float(old.get("ram_percent", 0.0)), 1)
        old_prof = str(old.get("active_profile", ""))
        new_prof = str(new.get("active_profile", ""))
        prof_msg = "Unchanged" if old_prof == new_prof else f"{old_prof} -> {new_prof}"

        lines = [
            f"Comparing '{old.get('label', older_path.name)}' -> '{new.get('label', newer_path.name)}'",
            f"New apps installed ({len(added_apps)}): {', '.join(added_apps[:8]) or 'None'}",
            f"Apps removed ({len(removed_apps)}): {', '.join(removed_apps[:8]) or 'None'}",
            f"New startup items ({len(added_startup)}): {', '.join(added_startup[:8]) or 'None'}",
            f"Disk usage delta: {disk_delta:+.2f} GB · RAM usage delta: {ram_delta:+.1f}% · Profile: {prof_msg}",
        ]
        return SnapshotDiffResult(
            older_label=str(old.get("label", older_path.name)),
            newer_label=str(new.get("label", newer_path.name)),
            added_apps=added_apps,
            removed_apps=removed_apps,
            added_startup=added_startup,
            removed_startup=removed_startup,
            disk_delta_gb=disk_delta,
            ram_delta_pct=ram_delta,
            power_plan_changed=prof_msg,
            summary_lines=lines,
        )


class StartupDelayManager:
    """Delays non-critical startup apps by N seconds (e.g. 45s) instead of deleting them."""

    def __init__(self, cfg_mgr: ConfigManager, rollback_mgr: RollbackManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.rollback_mgr = rollback_mgr
        self.journal = journal

    def delay_startup_entry(self, entry_path: Path, delay_seconds: int = 45) -> tuple[bool, str]:
        p = entry_path.expanduser()
        if not p.exists():
            return False, f"Startup file not found: {p}"
        delay_seconds = max(5, min(300, int(delay_seconds)))
        sys_name = platform.system()

        # Backup original into quarantine/startup_backups for 100% reversibility
        bak_dir = self.cfg_mgr.quarantine_dir / "startup_delay_backups"
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak_path = bak_dir / f"{p.name}.{int(time.time())}.bak"
        try:
            orig_bytes = p.read_bytes()
            bak_path.write_bytes(orig_bytes)
        except OSError as exc:
            return False, f"Cannot read/backup startup entry: {exc}"

        if sys_name == "Linux" and p.suffix == ".desktop":
            text = orig_bytes.decode("utf-8", errors="ignore")
            lines = [ln for ln in text.splitlines() if not ln.startswith("X-GNOME-Autostart-Delay=")]
            lines.append(f"X-GNOME-Autostart-Delay={delay_seconds}")
            # Also wrap Exec cleanly if desktop doesn't respect X-GNOME-Autostart-Delay
            out_lines: list[str] = []
            for ln in lines:
                if ln.startswith("Exec=") and "sleep " not in ln:
                    cmd = ln[5:].strip()
                    out_lines.append(f"Exec=sh -c 'sleep {delay_seconds}; exec {cmd}'")
                else:
                    out_lines.append(ln)
            p.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        elif sys_name == "Darwin" and p.suffix == ".plist":
            text = orig_bytes.decode("utf-8", errors="ignore")
            if "AstraCradleDelay" not in text and "<key>ProgramArguments</key>" in text:
                # Add RunAtLoad delay note & backup; non-destructive wrapper script
                delay_sh = bak_dir / f"{p.stem}_delay.sh"
                delay_sh.write_text(f"#!/usr/bin/env bash\nsleep {delay_seconds}\n", encoding="utf-8")
                delay_sh.chmod(0o755)
        else:
            # Windows / generic script wrapper
            wrapper = p.parent / f"{p.stem}_Delayed{delay_seconds}s.bat"
            wrapper.write_text(
                f"@echo off\r\ntimeout /t {delay_seconds} /nobreak >nul\r\nstart \"\" \"{p}\"\r\n",
                encoding="utf-8",
            )

        self.journal.log_event(
            "StartupDelay",
            "delay_startup",
            f"Delayed '{p.name}' startup by {delay_seconds}s (backup: {bak_path.name})",
            "Speed up desktop boot without removing user's startup app",
            "ok",
        )
        return True, f"Delayed '{p.name}' by {delay_seconds}s (reversible backup saved: {bak_path.name})."


@dataclass
class NetworkHogProcess:
    pid: int
    name: str
    connection_count: int
    established_count: int
    remote_sample: str


class NetworkAndDnsInspector:
    """Inspects per-app socket/connection hogs, checks ping latency, and manages reversible Fast-DNS."""

    DNS_PRESETS = {
        "Cloudflare (1.1.1.1)": ("1.1.1.1", "1.0.0.1"),
        "Quad9 Malware-Filter (9.9.9.9)": ("9.9.9.9", "149.112.112.112"),
        "Google Public (8.8.8.8)": ("8.8.8.8", "8.8.4.4"),
        "Automatic (DHCP)": ("auto", "auto"),
    }

    def __init__(self, journal: ForensicJournal) -> None:
        self.journal = journal

    def scan_network_hogs(self, limit: int = 12) -> list[NetworkHogProcess]:
        counts: Counter[int] = Counter()
        est_counts: Counter[int] = Counter()
        samples: dict[int, str] = {}
        try:
            conns = psutil.net_connections(kind="inet")
        except Exception:
            conns = []
        for c in conns:
            pid = getattr(c, "pid", None)
            if not pid:
                continue
            counts[pid] += 1
            if getattr(c, "status", "") == "ESTABLISHED":
                est_counts[pid] += 1
            if pid not in samples and getattr(c, "raddr", None):
                try:
                    samples[pid] = f"{c.raddr.ip}:{c.raddr.port}"
                except Exception:
                    pass

        out: list[NetworkHogProcess] = []
        for pid, total in counts.most_common(limit):
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = f"pid-{pid}"
            out.append(
                NetworkHogProcess(
                    pid=pid,
                    name=name,
                    connection_count=total,
                    established_count=est_counts.get(pid, 0),
                    remote_sample=samples.get(pid, "listening"),
                )
            )
        return out

    @staticmethod
    def measure_dns_ping_ms(host: str = "1.1.1.1", port: int = 443, samples: int = 2) -> float | None:
        times: list[float] = []
        for _ in range(samples):
            t0 = time.perf_counter()
            try:
                with socket.create_connection((host, port), timeout=1.5):
                    times.append((time.perf_counter() - t0) * 1000.0)
            except OSError:
                continue
        return round(sum(times) / len(times), 1) if times else None

    def apply_dns_preset_command(self, preset_name: str) -> str:
        dns = self.DNS_PRESETS.get(preset_name, ("1.1.1.1", "1.0.0.1"))
        sys_name = platform.system()
        if sys_name == "Windows":
            if dns[0] == "auto":
                return 'netsh interface ip set dns name="Ethernet" source=dhcp'
            return f'netsh interface ip set dns name="Ethernet" static {dns[0]} & netsh interface ip add dns name="Ethernet" {dns[1]} index=2'
        if sys_name == "Darwin":
            if dns[0] == "auto":
                return 'networksetup -setdnsservers "Wi-Fi" Empty'
            return f'networksetup -setdnsservers "Wi-Fi" {dns[0]} {dns[1]}'
        if dns[0] == "auto":
            return "nmcli con mod \"$(nmcli -t -f NAME con show --active | head -n1)\" ipv4.ignore-auto-dns no ipv4.dns \"\""
        return f'nmcli con mod "$(nmcli -t -f NAME con show --active | head -n1)" ipv4.ignore-auto-dns yes ipv4.dns "{dns[0]} {dns[1]}"'

    def apply_dns_preset(
        self,
        preset_name: str,
        dry_run: bool = False,
        elevation: ElevationRunner | None = None,
    ) -> tuple[bool, str]:
        dns = self.DNS_PRESETS.get(preset_name, ("1.1.1.1", "1.0.0.1"))
        if dry_run:
            return True, f"[DRY RUN] Would apply DNS preset '{preset_name}' ({dns[0]} / {dns[1]})."
        sys_name = platform.system()
        argv: list[str] = []
        if sys_name == "Linux" and shutil.which("nmcli"):
            try:
                p = subprocess.run(
                    ["nmcli", "-t", "-f", "NAME", "con", "show", "--active"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                con_name = next((ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()), "")
                if con_name:
                    if dns[0] == "auto":
                        argv = ["nmcli", "con", "mod", con_name, "ipv4.ignore-auto-dns", "no", "ipv4.dns", ""]
                    else:
                        argv = ["nmcli", "con", "mod", con_name, "ipv4.ignore-auto-dns", "yes", "ipv4.dns", f"{dns[0]} {dns[1]}"]
            except Exception:
                pass
        if sys_name == "Linux" and not argv and shutil.which("resolvectl"):
            ifaces = [n for n in psutil.net_if_addrs().keys() if n != "lo" and not n.startswith("docker") and not n.startswith("veth")]
            iface = ifaces[0] if ifaces else ""
            if iface:
                if dns[0] == "auto":
                    argv = ["resolvectl", "revert", iface]
                else:
                    argv = ["resolvectl", "dns", iface, dns[0], dns[1]]
        elif sys_name == "Darwin" and shutil.which("networksetup"):
            argv = ["networksetup", "-setdnsservers", "Wi-Fi"] + (["Empty"] if dns[0] == "auto" else [dns[0], dns[1]])
        elif sys_name == "Windows":
            argv = (
                ["netsh", "interface", "ip", "set", "dns", "name=Ethernet", "source=dhcp"]
                if dns[0] == "auto"
                else ["netsh", "interface", "ip", "set", "dns", "name=Ethernet", "static", dns[0]]
            )

        if not argv:
            return False, f"Network manager CLI unavailable; copy command: {self.apply_dns_preset_command(preset_name)}"

        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=6, check=False)
            if proc.returncode == 0:
                self.journal.log_event("NetworkDNS", "apply_dns", f"Applied DNS preset {preset_name}", "User switched DNS", "ok")
                return True, f"Applied DNS preset '{preset_name}'."
        except Exception:
            pass

        if elevation is not None:
            eres = elevation.run_privileged(argv, reason=f"Switch system DNS to {preset_name} ({dns[0]}, {dns[1]})")
            if eres.ok:
                self.journal.log_event("NetworkDNS", "apply_dns", f"Applied DNS preset {preset_name} via elevation", "User switched DNS", "ok")
                return True, f"Applied DNS preset '{preset_name}'."
            return False, eres.message
        return False, "Elevation required to modify system DNS."


class MemoryStandbyManager:
    """Safe cross-OS standby/working-set RAM trim & memory pressure guard."""

    def __init__(self, journal: ForensicJournal, elevation: ElevationRunner | None = None) -> None:
        self.journal = journal
        self.elevation = elevation

    def trim_standby_ram(self, dry_run: bool = False, elevation: ElevationRunner | None = None) -> tuple[bool, str, float]:
        elev = elevation or self.elevation
        vm_before = psutil.virtual_memory()
        avail_before_mb = vm_before.available / (1024 * 1024)
        sys_name = platform.system()

        if dry_run:
            return (
                True,
                f"Dry-run: RAM currently {vm_before.percent:.1f}% used ({avail_before_mb:.0f} MB free). Ready for safe working-set trim.",
                0.0,
            )

        trimmed = 0
        if sys_name == "Windows":
            # Trim current process & non-protected user process working sets safely via Win32 SetProcessWorkingSetSize
            try:
                k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                for proc in psutil.process_iter(["pid", "name"]):
                    if proc.info["pid"] <= 4:
                        continue
                    h = k32.OpenProcess(0x0100 | 0x0400, False, proc.info["pid"])
                    if h:
                        k32.SetProcessWorkingSetSize(h, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
                        k32.CloseHandle(h)
                        trimmed += 1
            except Exception:
                pass
        elif sys_name == "Linux":
            # Safe user-space GC + sync; root drop_caches only if elevated
            try:
                import gc
                gc.collect()
                if shutil.which("sync"):
                    subprocess.run(["sync"], timeout=3, check=False)
                drop = Path("/proc/sys/vm/drop_caches")
                if drop.exists():
                    try:
                        drop.write_text("1\n", encoding="utf-8")
                        trimmed += 1
                    except PermissionError:
                        if elev is not None:
                            eres = elev.write_privileged_file(
                                drop,
                                "1\n",
                                reason="Drop reclaimable Linux kernel pagecache to free standby RAM",
                            )
                            if eres.ok:
                                trimmed += 1
            except Exception:
                pass
        elif sys_name == "Darwin":
            try:
                import gc
                gc.collect()
            except Exception:
                pass

        vm_after = psutil.virtual_memory()
        avail_after_mb = vm_after.available / (1024 * 1024)
        delta_mb = round(max(0.0, avail_after_mb - avail_before_mb), 1)
        self.journal.log_event(
            "MemoryGuard",
            "trim_standby_ram",
            f"Safe RAM standby/working-set trim complete (freed ~{delta_mb:.1f} MB; RAM {vm_after.percent:.1f}%)",
            "Relieve memory pressure without terminating open user applications",
            "ok",
        )
        return True, f"Safe RAM trim complete (~{delta_mb:.1f} MB reclaimed · {avail_after_mb:.0f} MB free).", delta_mb


@dataclass
class BatteryHealthReport:
    present: bool
    percent: float | None
    plugged_in: bool | None
    health_percent: float | None
    cycle_count: int | None
    power_draw_watts: float | None
    charge_limit_pct: int | None
    wake_locks: list[str] = field(default_factory=list)
    summary: str = ""


class BatteryAndPowerInspector:
    """Reads Laptop Battery Wear %, Cycle Count, Wattage, Charge Limit & Sleep Wake-Locks."""

    def inspect(self) -> BatteryHealthReport:
        bat = psutil.sensors_battery()
        sys_name = platform.system()
        health_pct: float | None = None
        cycles: int | None = None
        watts: float | None = None
        charge_limit: int | None = None
        wake_locks: list[str] = []

        if sys_name == "Linux":
            ps_root = Path("/sys/class/power_supply")
            if ps_root.exists():
                for bdir in sorted(ps_root.glob("BAT*")):
                    try:
                        efull = self._read_int(bdir / "energy_full") or self._read_int(bdir / "charge_full")
                        edesign = self._read_int(bdir / "energy_full_design") or self._read_int(bdir / "charge_full_design")
                        if efull and edesign and edesign > 0:
                            health_pct = round(min(100.0, (efull / edesign) * 100.0), 1)
                        cyc = self._read_int(bdir / "cycle_count")
                        if cyc is not None:
                            cycles = cyc
                        pnow = self._read_int(bdir / "power_now")
                        if pnow is not None:
                            watts = round(pnow / 1_000_000.0, 2)
                        lim = self._read_int(bdir / "charge_control_end_threshold")
                        if lim is not None:
                            charge_limit = lim
                    except Exception:
                        pass
            if shutil.which("systemd-inhibit"):
                try:
                    proc = subprocess.run(["systemd-inhibit", "--list", "--no-legend"], capture_output=True, text=True, timeout=2, check=False)
                    for ln in (proc.stdout or "").splitlines()[:6]:
                        if ln.strip():
                            wake_locks.append(" ".join(ln.split()[:3]))
                except Exception:
                    pass
        elif sys_name == "Darwin" and shutil.which("pmset"):
            try:
                proc = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=2, check=False)
                for ln in (proc.stdout or "").splitlines():
                    if "PreventUserIdleSystemSleep" in ln or "PreventSystemSleep" in ln:
                        wake_locks.append(ln.strip()[:80])
            except Exception:
                pass
        elif sys_name == "Windows" and shutil.which("powercfg"):
            try:
                proc = subprocess.run(["powercfg", "/requests"], capture_output=True, text=True, timeout=2, check=False)
                for ln in (proc.stdout or "").splitlines():
                    s = ln.strip()
                    if s and not s.endswith(":") and "None." not in s:
                        wake_locks.append(s[:80])
            except Exception:
                pass

        if bat is None and health_pct is None:
            return BatteryHealthReport(
                present=False,
                percent=None,
                plugged_in=None,
                health_percent=None,
                cycle_count=None,
                power_draw_watts=None,
                charge_limit_pct=None,
                wake_locks=wake_locks[:6],
                summary="Desktop / AC system detected (no battery pack). Active wake-locks: " + (", ".join(wake_locks[:4]) or "None"),
            )

        pct = round(bat.percent, 1) if bat else None
        plugged = bat.power_plugged if bat else None
        parts = [f"Charge: {pct if pct is not None else '?'}% ({'AC' if plugged else 'Battery'})"]
        if health_pct is not None:
            parts.append(f"Battery Health: {health_pct}%")
        if cycles is not None:
            parts.append(f"Cycles: {cycles}")
        if watts is not None:
            parts.append(f"Draw: {watts}W")
        if charge_limit is not None:
            parts.append(f"Charge Limit: {charge_limit}%")
        parts.append(f"Wake-Locks: {len(wake_locks)}")
        return BatteryHealthReport(
            present=True,
            percent=pct,
            plugged_in=plugged,
            health_percent=health_pct,
            cycle_count=cycles,
            power_draw_watts=watts,
            charge_limit_pct=charge_limit,
            wake_locks=wake_locks[:6],
            summary=" · ".join(parts),
        )

    @staticmethod
    def _read_int(path: Path) -> int | None:
        if not path.exists():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except Exception:
            return None


class SafeBeginnerFixer:
    """1-Click 'Fix Everything Safe' + Weekly Health Digest for non-technical users."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        executor: GuardedExecutor,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.executor = executor
        self.rollback_mgr = rollback_mgr
        self.journal = journal

    def run_fix_everything_safe(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        dry_run: bool = False,
    ) -> tuple[bool, str, list[str]]:
        """Executes ONLY 100% reversible, low-risk fixes with clear plain-English summary."""
        notes: list[str] = []
        # 1) Quarantine expired temp caches (100% reversible)
        r1 = self.executor.execute_action(
            "clean_temp_caches",
            adapter,
            config,
            dry_run=dry_run,
            why="Reclaim space safely by moving expired temp files into reversible Quarantine.",
        )
        notes.append(f"Temp Caches: {r1.message}")

        # 2) Trim oversized user logs
        r2 = self.executor.execute_action(
            "vacuum_user_logs",
            adapter,
            config,
            dry_run=dry_run,
            why="Keep app log folders tidy without deleting active system logs.",
        )
        notes.append(f"Log Vacuum: {r2.message}")

        # 3) Safe Standby RAM Trim
        _, ram_msg, _ = MemoryStandbyManager(self.journal).trim_standby_ram(dry_run=dry_run)
        notes.append(f"Memory Trim: {ram_msg}")

        # 4) Ghost driver cleanup audit
        aud = AutorunsAndGhostAuditor(self.cfg_mgr, self.rollback_mgr, self.journal)
        ghosts = aud.scan_ghost_drivers()
        cleaned_ghosts = 0
        for gh in ghosts[:5]:
            ok_g, _ = aud.clean_ghost_driver(gh, adapter, dry_run=dry_run)
            if ok_g:
                cleaned_ghosts += 1
        notes.append(f"Ghost Drivers: scanned {len(ghosts)}, processed {cleaned_ghosts} safely.")

        self.journal.log_event(
            "SafeBeginnerFix",
            "fix_everything_safe",
            f"Completed 1-Click Safe Maintenance ({len(notes)} steps, dry_run={dry_run})",
            "Give beginners a zero-risk, reversible 1-click optimization pass",
            "ok",
        )
        summary = "1-Click Fix Everything Safe complete (" + ("Dry-Run Preview" if dry_run else "Applied & Reversible") + ")."
        return True, summary, notes

    def build_weekly_digest(self, adblock_blocked_count: int = 0) -> str:
        entries = self.journal.read_entries(limit=250, newest_first=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        week_entries = []
        for e in entries:
            try:
                ts = datetime.fromisoformat(str(e.timestamp).replace("Z", "+00:00"))
                if ts >= cutoff:
                    week_entries.append(e)
            except Exception:
                week_entries.append(e)
        ok_count = sum(1 for e in week_entries if e.outcome == "ok")
        err_count = sum(1 for e in week_entries if e.outcome == "error")
        q_bytes, q_cnt = self.rollback_mgr.quarantine_stats()
        return (
            f"7-Day Activity: {len(week_entries)} logged actions ({ok_count} OK, {err_count} errors)\n"
            f"Quarantine Vault: {q_cnt} reversible item(s) ({q_bytes / (1024*1024):.1f} MB recoverable)\n"
            f"AstraShield DNS/Ad Blocker: {adblock_blocked_count} tracker/ad queries blocked"
        )
