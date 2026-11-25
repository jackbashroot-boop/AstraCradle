"""Cross-platform single-instance lock & graceful replacement manager."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Iterable

import psutil

from .config import ConfigManager


_CLI_ONLY_FLAGS: frozenset[str] = frozenset(
    {
        "-h",
        "--help",
        "-V",
        "--version",
        "--self-check",
        "--diagnose",
        "--scan",
        "--apply",
        "--rollback",
        "--status",
        "--smart-scan",
        "--wifi-optimize",
        "--fix-safe",
        "--bloat-scan",
        "--dev-scan",
        "--sweeper-scan",
        "--privacy-scan",
        "--leaks",
        "--conflicts",
        "--export-crash-log",
        "--comprehensive-setup",
        "--list-modules",
        "--modules",
        "--setup-wizard",
        "--export-ai-bundle",
        "--install-shortcuts",
        "--mesh-status",
        "--mesh-plan",
        "--setup-reboot",
        "--apply-boot-toggles",
        "--widget-status",
        "--bufferbloat-test",
        "--export-profile",
        "--import-profile",
        "--tailscale-status",
        "--commands-list",
        "--failover-status",
        "--failover-tick",
        "--failover-watch",
        "--deps-status",
        "--install-deps",
        "--help-topic",
        "--lookup-pid",
        "--proximity-connect",
        "--beam-host",
        "--beam-connect",
        "--benchmark-report",
        "--check-updates",
        "--set-update-repo",
        "--install-update",
        "--uninstall",
        "--apply-preset",
        "--run-pipeline",
        "--quit",
    }
)


class SingleInstanceManager:
    """Enforces at most one interactive AstraCradle GUI process at a time."""

    def __init__(self, cfg_mgr: ConfigManager) -> None:
        self.cfg_mgr = cfg_mgr
        self.lock_path: Path = cfg_mgr.state_dir / "instance.lock"

    @staticmethod
    def is_gui_cmdline(cmdline: Iterable[str] | None) -> bool:
        """Returns True when `cmdline` looks like an interactive AstraCradle GUI launch."""
        if not cmdline:
            return False
        parts = [str(p) for p in cmdline if p]
        if not parts:
            return False
        joined = " ".join(parts).lower()
        if "pytest" in joined or "unittest" in joined:
            return False
        # Exclude one-shot CLI invocations
        for tok in parts[1:]:
            flag = tok.split("=", 1)[0].strip()
            if flag in _CLI_ONLY_FLAGS:
                return False

        # Match `astracradle-gui`, `AstraCradle`, `python -m astracradle`, or `astracradle` console launcher
        exe_name = Path(parts[0]).name.lower()
        if exe_name in ("astracradle-gui", "astracradle-gui.exe", "astracradle", "astracradle.exe"):
            return True
        if any(Path(p).name.lower() in ("astracradle-gui", "astracradle-gui.exe", "astracradle", "astracradle.exe") for p in parts[:3]):
            return True
        for idx, tok in enumerate(parts):
            if tok == "-m" and idx + 1 < len(parts) and parts[idx + 1] in ("astracradle", "astracradle.__main__"):
                return True
            if tok.endswith("astracradle/__main__.py") or tok.endswith("astracradle\\__main__.py"):
                return True
        return False

    def read_lock_pid(self) -> int | None:
        if not self.lock_path.exists():
            return None
        try:
            raw = json.loads(self.lock_path.read_text(encoding="utf-8"))
            pid = int(raw.get("pid", 0))
            return pid if pid > 0 else None
        except Exception:
            return None

    def find_existing_pids(self, include_process_scan: bool = True) -> list[int]:
        """Returns active PIDs of prior AstraCradle GUI instances (never self or parent)."""
        me = os.getpid()
        parent = os.getppid()
        pids: set[int] = set()

        lock_pid = self.read_lock_pid()
        if lock_pid and lock_pid not in (me, parent) and psutil.pid_exists(lock_pid):
            try:
                proc = psutil.Process(lock_pid)
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    cmd = proc.cmdline()
                    if "pytest" not in " ".join(cmd).lower():
                        pids.add(lock_pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        if include_process_scan:
            for proc in psutil.process_iter(["pid", "cmdline", "status"]):
                try:
                    pid = int(proc.info.get("pid") or 0)
                    if pid <= 0 or pid in (me, parent):
                        continue
                    if proc.info.get("status") == psutil.STATUS_ZOMBIE:
                        continue
                    if self.is_gui_cmdline(proc.info.get("cmdline")):
                        pids.add(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

        return sorted(pids)

    def _mark_replacement_clean_shutdown(self) -> None:
        """Marks heartbeat clean_shutdown=True so replacing an instance does not trigger a crash post-mortem."""
        hb_path = self.cfg_mgr.state_dir / "heartbeat.json"
        if not hb_path.exists():
            return
        try:
            data = json.loads(hb_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["clean_shutdown"] = True
                data["ts"] = time.time()
                data["replaced_by_pid"] = os.getpid()
                hb_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def terminate_existing_instances(
        self,
        timeout_sec: float = 3.0,
        include_process_scan: bool = True,
    ) -> list[int]:
        """Gracefully terminates any existing GUI instances (forcing kill if unresponsive)."""
        target_pids = self.find_existing_pids(include_process_scan=include_process_scan)
        if not target_pids:
            return []

        self._mark_replacement_clean_shutdown()
        procs: list[psutil.Process] = []
        for pid in target_pids:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        terminated_pids = [p.pid for p in procs]
        if procs:
            _gone, alive = psutil.wait_procs(procs, timeout=max(0.2, float(timeout_sec)))
            if alive:
                for p in alive:
                    try:
                        p.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
                psutil.wait_procs(alive, timeout=0.6)

        # Clean stale lock if its owner is now gone
        lock_pid = self.read_lock_pid()
        if lock_pid and lock_pid in terminated_pids and not psutil.pid_exists(lock_pid):
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass

        return terminated_pids

    def acquire(self) -> Path:
        """Writes the instance lock file atomically for the current PID."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_path = self.lock_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self.lock_path)
        return self.lock_path

    def release(self) -> None:
        """Removes instance.lock only if it is currently owned by this PID."""
        try:
            lock_pid = self.read_lock_pid()
            if lock_pid == os.getpid():
                self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass
