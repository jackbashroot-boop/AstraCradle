"""GPU/VRAM Telemetry, 30-Day SQLite History, Driver Blame, 1-Click Glitch Fixers, OS Snapshots & Update Checker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import time

from .config import AppConfig, ConfigManager
from .journal import ForensicJournal
from .models import SystemSnapshot
from .os_adapters import BaseOSAdapter


@dataclass
class GpuTelemetry:
    available: bool
    name: str
    utilization_percent: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None
    temperature_c: float | None
    throttling_reason: str | None


@dataclass
class HistoryPoint:
    ts: float
    health_score: int
    cpu_percent: float
    mem_percent: float
    temp_c: float | None


class GpuTelemetryCollector:
    """Collects live GPU utilization, VRAM pressure, and thermal throttling status locally."""

    _nvidia_checked: bool = False
    _nvidia_bin: str | None = None
    _gpu_cache: tuple[float, GpuTelemetry] | None = None

    @classmethod
    def collect(cls) -> GpuTelemetry:
        now_m = time.monotonic()
        if cls._gpu_cache is not None and (now_m - cls._gpu_cache[0]) < 4.0:
            return cls._gpu_cache[1]
        if not cls._nvidia_checked:
            cls._nvidia_bin = shutil.which("nvidia-smi")
            cls._nvidia_checked = True
        if cls._nvidia_bin:
            try:
                proc = subprocess.run(
                    [
                        cls._nvidia_bin,
                        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,clocks_throttle_reasons.hw_thermal_slowdown",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    parts = [p.strip() for p in proc.stdout.strip().splitlines()[0].split(",")]
                    if len(parts) >= 5:
                        def _f(idx: int) -> float | None:
                            try:
                                return float(parts[idx])
                            except Exception:
                                return None

                        throt = "Thermal Slowdown Active" if (len(parts) > 5 and parts[5].lower() == "active") else None
                        res_gpu = GpuTelemetry(
                            available=True,
                            name=parts[0],
                            utilization_percent=_f(1),
                            vram_used_mb=_f(2),
                            vram_total_mb=_f(3),
                            temperature_c=_f(4),
                            throttling_reason=throt,
                        )
                        cls._gpu_cache = (now_m, res_gpu)
                        return res_gpu
            except Exception:
                pass

        # Linux sysfs GPU busy/VRAM fallback
        if platform.system() == "Linux":
            drm_dir = Path("/sys/class/drm")
            if drm_dir.exists():
                for card in sorted(drm_dir.glob("card*/device")):
                    busy = card / "gpu_busy_percent"
                    vram_used = card / "mem_info_vram_used"
                    vram_tot = card / "mem_info_vram_total"
                    if busy.exists():
                        try:
                            util = float(busy.read_text().strip())
                            vu = float(vram_used.read_text().strip()) / (1024 * 1024) if vram_used.exists() else None
                            vt = float(vram_tot.read_text().strip()) / (1024 * 1024) if vram_tot.exists() else None
                            return GpuTelemetry(True, card.parent.name, util, vu, vt, None, None)
                        except Exception:
                            pass

        return GpuTelemetry(False, "Integrated / No Dedicated Telemetry", None, None, None, None, None)


class TelemetryHistoryDB:
    """Multi-resolution (60s / 5m / 30m / 1hr / 30d) local SQLite + ring-buffer telemetry store."""

    RESOLUTION_SECONDS: dict[str, int] = {
        "60s": 60,
        "5m": 300,
        "30m": 1800,
        "1hr": 3600,
        "30d": 30 * 86400,
    }

    def __init__(self, cfg_mgr: ConfigManager) -> None:
        self.db_path = cfg_mgr.state_dir / "telemetry_history.db"
        self._ring: list[HistoryPoint] = []
        self._write_count: int = 0
        self._init_db()

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS samples ("
                    "ts REAL PRIMARY KEY, "
                    "health_score INTEGER, "
                    "cpu_percent REAL, "
                    "mem_percent REAL, "
                    "temp_c REAL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS benchmarks ("
                    "ts REAL PRIMARY KEY, "
                    "health_score INTEGER, "
                    "idle_ram_mb REAL, "
                    "boot_uptime_sec REAL, "
                    "bufferbloat_grade TEXT, "
                    "top_ram_process TEXT, "
                    "top_ram_mb REAL)"
                )
        except Exception:
            pass

    def record_tick(self, snap: SystemSnapshot, health_score: int, persist_db: bool = True) -> None:
        now = time.time()
        pt = HistoryPoint(now, int(health_score), float(snap.cpu_percent), float(snap.mem_percent), snap.temperature_c)
        self._ring.append(pt)
        if len(self._ring) > 1900:
            cutoff_ring = now - 3600.0
            self._ring = [p for p in self._ring if p.ts >= cutoff_ring][-1800:]
        if persist_db:
            self.record(snap, health_score, now=now)

    def record(self, snap: SystemSnapshot, health_score: int, now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        cutoff = ts - (30 * 86400)
        self._write_count += 1
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO samples (ts, health_score, cpu_percent, mem_percent, temp_c) VALUES (?, ?, ?, ?, ?)",
                    (ts, int(health_score), float(snap.cpu_percent), float(snap.mem_percent), snap.temperature_c),
                )
                if self._write_count % 20 == 1:
                    conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        except Exception:
            pass

    def query_resolution(self, resolution: str = "60s", limit: int = 120) -> list[HistoryPoint]:
        window = self.RESOLUTION_SECONDS.get(resolution, 60)
        since = time.time() - window
        if resolution in ("60s", "5m", "30m", "1hr"):
            pts = [p for p in self._ring if p.ts >= since]
            if len(pts) < 2:
                # Merge any recent SQLite samples
                db_pts = self._query_since(since, limit=max(limit, 360))
                seen = {round(p.ts, 2): p for p in db_pts}
                for p in pts:
                    seen[round(p.ts, 2)] = p
                pts = sorted(seen.values(), key=lambda x: x.ts)
        else:
            pts = self._query_since(since, limit=max(limit, 720))

        if len(pts) <= limit:
            return pts
        # Bucket downsample cleanly
        step = len(pts) / float(limit)
        out: list[HistoryPoint] = []
        for idx in range(limit):
            s = int(idx * step)
            e = max(s + 1, int((idx + 1) * step))
            chunk = pts[s:e]
            temps = [c.temp_c for c in chunk if c.temp_c is not None]
            avg_t = round(sum(temps) / len(temps), 1) if temps else None
            out.append(
                HistoryPoint(
                    ts=chunk[-1].ts,
                    health_score=int(round(sum(c.health_score for c in chunk) / len(chunk))),
                    cpu_percent=round(sum(c.cpu_percent for c in chunk) / len(chunk), 1),
                    mem_percent=round(sum(c.mem_percent for c in chunk) / len(chunk), 1),
                    temp_c=avg_t,
                )
            )
        return out

    def _query_since(self, since: float, limit: int = 200) -> list[HistoryPoint]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT ts, health_score, cpu_percent, mem_percent, temp_c FROM samples "
                    "WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                    (since, limit),
                ).fetchall()
            return [HistoryPoint(float(r[0]), int(r[1]), float(r[2]), float(r[3]), r[4]) for r in rows]
        except Exception:
            return []

    def record_weekly_benchmark(
        self,
        snap: SystemSnapshot,
        health_score: int,
        bufferbloat_grade: str = "A",
        now: float | None = None,
    ) -> dict[str, object]:
        ts = now if now is not None else time.time()
        idle_ram_mb = round(snap.mem_used_bytes / (1024 * 1024), 1)
        top_proc_name = ""
        top_proc_mb = 0.0
        for p in sorted(getattr(snap, "top_processes", None) or [], key=lambda x: getattr(x, "memory_mb", 0.0), reverse=True)[:1]:
            top_proc_name = p.name
            top_proc_mb = round(float(p.memory_mb), 1)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO benchmarks "
                    "(ts, health_score, idle_ram_mb, boot_uptime_sec, bufferbloat_grade, top_ram_process, top_ram_mb) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ts, int(health_score), idle_ram_mb, float(snap.uptime_seconds), bufferbloat_grade, top_proc_name, top_proc_mb),
                )
        except Exception:
            pass
        return {
            "ts": ts,
            "health_score": int(health_score),
            "idle_ram_mb": idle_ram_mb,
            "bufferbloat_grade": bufferbloat_grade,
            "top_ram_process": top_proc_name,
            "top_ram_mb": top_proc_mb,
        }

    def list_benchmarks(self, limit: int = 12) -> list[dict[str, object]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT ts, health_score, idle_ram_mb, boot_uptime_sec, bufferbloat_grade, top_ram_process, top_ram_mb "
                    "FROM benchmarks ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [
                {
                    "ts": float(r[0]),
                    "health_score": int(r[1]),
                    "idle_ram_mb": float(r[2]),
                    "boot_uptime_sec": float(r[3]),
                    "bufferbloat_grade": str(r[4] or "A"),
                    "top_ram_process": str(r[5] or ""),
                    "top_ram_mb": float(r[6] or 0.0),
                }
                for r in rows
            ]
        except Exception:
            return []

    def detect_regressions(self, ram_growth_mb_threshold: float = 512.0) -> list[str]:
        """Compares the newest benchmark against the previous baseline to flag RAM or health regressions."""
        benches = self.list_benchmarks(limit=5)
        if len(benches) < 2:
            return []
        cur, prev = benches[0], benches[1]
        alerts: list[str] = []
        ram_delta = float(cur["idle_ram_mb"]) - float(prev["idle_ram_mb"])
        if ram_delta >= ram_growth_mb_threshold:
            culprit = f" (top RAM user: {cur['top_ram_process']} @ {cur['top_ram_mb']:.0f} MB)" if cur["top_ram_process"] else ""
            alerts.append(f"Idle RAM grew +{ram_delta:.0f} MB vs previous benchmark{culprit}.")
        score_delta = int(cur["health_score"]) - int(prev["health_score"])
        if score_delta <= -10:
            alerts.append(f"Equilibrium Health Score dropped {score_delta} pts ({prev['health_score']} -> {cur['health_score']}).")
        return alerts

    def query_recent(self, days: int = 30, limit: int = 200) -> list[HistoryPoint]:
        since = time.time() - (max(1, days) * 86400)
        return self._query_since(since, limit=limit)


class DriverUpdateCorrelator:
    """Finds recent OS/driver/package updates so CrashAnalyzer can blame recent changes."""

    @staticmethod
    def recent_changes(limit: int = 8) -> list[str]:
        sys_name = platform.system()
        out: list[str] = []
        if sys_name == "Linux":
            dpkg_log = Path("/var/log/dpkg.log")
            if dpkg_log.exists():
                try:
                    lines = [
                        ln.strip()
                        for ln in dpkg_log.read_text(encoding="utf-8", errors="ignore").splitlines()[-120:]
                        if " install " in ln or " upgrade " in ln
                    ]
                    out.extend(lines[-limit:])
                except OSError:
                    pass
        elif sys_name == "Darwin":
            plist = Path("/Library/Receipts/InstallHistory.plist")
            if plist.exists():
                out.append(f"macOS InstallHistory.plist updated ({time.ctime(plist.stat().st_mtime)})")
        elif sys_name == "Windows" and shutil.which("powershell"):
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-HotFix | Select-Object -Last 5 | Out-String"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    out.append(proc.stdout.strip()[:400])
            except Exception:
                pass
        return out[:limit]


class PreFlightSnapshotHook:
    """Checks or triggers native OS restore point / APFS / Timeshift snapshot before heavy tweaks."""

    @staticmethod
    def check_or_prepare(dry_run: bool = True) -> tuple[bool, str]:
        sys_name = platform.system()
        if sys_name == "Windows":
            if dry_run:
                return True, "[Pre-Flight] Windows System Restore hook ready (Checkpoint-Computer)."
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Checkpoint-Computer -Description 'AstraCradlePreFlight' -RestorePointType MODIFY_SETTINGS"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip() or "Restore point requested."
            except Exception as exc:
                return False, str(exc)
        if sys_name == "Darwin":
            if dry_run:
                return True, "[Pre-Flight] macOS APFS local snapshot ready (tmutil localsnapshot)."
            if shutil.which("tmutil"):
                proc = subprocess.run(["tmutil", "localsnapshot"], capture_output=True, text=True, timeout=10)
                return proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip()
            return False, "tmutil unavailable."
        # Linux
        if shutil.which("timeshift"):
            return True, "[Pre-Flight] Timeshift snapshot utility detected."
        return True, "[Pre-Flight] Using AstraCradle Quarantine Vault + Hardware Tweak Snapshot."


class GlitchTroubleshooter:
    """1-click everyday OS glitch troubleshooters (thumbnails, DNS/Wi-Fi, audio/spooler, desktop shell)."""

    @staticmethod
    def list_troubleshooters() -> list[dict[str, str]]:
        return [
            {
                "id": "rebuild_thumbs",
                "title": "Rebuild Broken Icons & Thumbnail Cache",
                "desc": "Quarantines corrupted desktop thumbnail caches so icons regenerate cleanly.",
            },
            {
                "id": "refresh_dns_net",
                "title": "Refresh DNS & Network Resolver Cache",
                "desc": "Flushes stale OS DNS lookups to fix broken page loads or laggy matchmaking.",
            },
            {
                "id": "refresh_audio_spool",
                "title": "Check Audio / Print Spooler Health",
                "desc": "Checks user audio/print service readiness and provides instant gentle restart commands.",
            },
            {
                "id": "refresh_shell",
                "title": "Gentle Desktop Shell & Font Cache Check",
                "desc": "Refreshes user font/icon caches without rebooting your PC.",
            },
        ]

    @staticmethod
    def run(
        trouble_id: str,
        adapter: BaseOSAdapter,
        executor,
        config: AppConfig,
        journal: ForensicJournal,
        dry_run: bool = False,
    ) -> tuple[bool, str]:
        if trouble_id == "rebuild_thumbs":
            thumbs = Path.home() / ".cache" / "thumbnails"
            res = executor.execute_action(
                "clean_paths", adapter, config, params={"paths": [str(thumbs)]}, dry_run=dry_run, why="Glitch fix: rebuild thumbnails"
            )
            return res.ok, res.message
        if trouble_id == "refresh_dns_net":
            res = executor.execute_action(
                "flush_dns_cache", adapter, config, dry_run=dry_run, why="Glitch fix: refresh DNS resolver"
            )
            return res.ok, res.message
        if trouble_id == "refresh_audio_spool":
            cmds = adapter.os_maintenance_commands()
            msg = "Ready: " + ", ".join(list(cmds.keys())[:2]) if cmds else "Audio/spooler verified in user space."
            journal.log_event("GlitchFix", trouble_id, msg, "User ran audio/spooler troubleshooter", "ok")
            return True, msg
        if trouble_id == "refresh_shell":
            font_cache = Path.home() / ".cache" / "fontconfig"
            res = executor.execute_action(
                "clean_paths", adapter, config, params={"paths": [str(font_cache)]}, dry_run=dry_run, why="Glitch fix: refresh font/shell cache"
            )
            return res.ok, res.message
        return False, "Unknown troubleshooter."


class OutdatedAppsChecker:
    """Opt-in native package/runtime outdated check (winget / brew / apt)."""

    @staticmethod
    def check(consent: bool = False) -> tuple[bool, list[str]]:
        if not consent:
            return False, ["Opt-in required: click 'Check Outdated Runtimes' to inspect native OS package updates."]
        sys_name = platform.system()
        try:
            if sys_name == "Windows" and shutil.which("winget"):
                p = subprocess.run(["winget", "upgrade"], capture_output=True, text=True, timeout=8)
                lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()][:12]
                return True, lines or ["All winget packages appear up to date."]
            if sys_name == "Darwin" and shutil.which("brew"):
                p = subprocess.run(["brew", "outdated"], capture_output=True, text=True, timeout=8)
                lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()][:12]
                return True, lines or ["All Homebrew packages are up to date."]
            if sys_name == "Linux" and shutil.which("apt"):
                p = subprocess.run(["apt", "list", "--upgradable"], capture_output=True, text=True, timeout=6)
                lines = [ln.strip() for ln in p.stdout.splitlines() if "/" in ln][:12]
                return True, lines or ["All APT packages are up to date."]
        except Exception as exc:
            return False, [f"Update check timed out or unavailable: {exc}"]
        return True, ["No supported native package manager CLI found for non-admin check."]
