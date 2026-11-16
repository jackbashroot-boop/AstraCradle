"""Guarded action executor with dry-run simulation, allow-lists, quarantine, and forensic logging."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import uuid

import psutil

from .config import AppConfig, ConfigManager
from .journal import ForensicJournal
from .models import RollbackEntry, utc_now_iso
from .os_adapters import BaseOSAdapter
from .rollback import RollbackManager
from .telemetry import format_bytes


@dataclass
class ExecutionResult:
    ok: bool
    dry_run: bool
    title: str
    message: str
    bytes_affected: int = 0
    files_affected: int = 0
    entry_id: str | None = None
    preview_items: list[dict[str, str]] = field(default_factory=list)


class GuardedExecutor:
    """Executes safe, reversible optimizations while refusing unsafe mutations & logging everything."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.rollback_mgr = rollback_mgr
        self.journal = journal

    def _collect_safe_files(
        self,
        root: Path,
        adapter: BaseOSAdapter,
        min_age_seconds: int = 60,
        max_files: int = 3000,
    ) -> list[Path]:
        if not root.exists() or adapter.is_path_protected(root):
            return []
        now = time.time()
        candidates: list[Path] = []
        uid = os.getuid() if hasattr(os, "getuid") else None

        if root.is_file():
            return [root]

        for dirpath, _, filenames in os.walk(root, followlinks=False):
            dp = Path(dirpath)
            if adapter.is_path_protected(dp):
                continue
            for fname in filenames:
                fpath = dp / fname
                if fpath.is_symlink() or adapter.is_path_protected(fpath):
                    continue
                try:
                    st = fpath.stat()
                    if str(root) == "/tmp" and uid is not None and st.st_uid != uid:
                        continue
                    if self.cfg_mgr.base_dir in fpath.parents:
                        continue
                    if (now - st.st_mtime) < min_age_seconds:
                        continue
                    candidates.append(fpath)
                    if len(candidates) >= max_files:
                        return candidates
                except OSError:
                    continue
        return candidates

    def execute_action(
        self,
        action_id: str,
        adapter: BaseOSAdapter,
        config: AppConfig,
        params: dict[str, Any] | None = None,
        dry_run: bool | None = None,
        why: str = "User or Playbook optimization request",
    ) -> ExecutionResult:
        params = params or {}
        is_dry = config.dry_run_default if dry_run is None else dry_run

        if action_id == "clean_temp_caches":
            res = self._clean_caches(adapter, config, params, is_dry)
        elif action_id == "clean_paths":
            paths = [Path(p) for p in params.get("paths", [])]
            res = self._clean_path_list("Custom Cache Sweep", paths, adapter, config, is_dry)
        elif action_id == "clean_duplicates":
            res = self._clean_duplicate_files(params.get("paths", []), adapter, config, is_dry)
        elif action_id == "vacuum_user_logs":
            res = self._vacuum_logs(adapter, config, is_dry)
        elif action_id == "flush_dns_cache":
            res = self._flush_dns(adapter, is_dry)
        elif action_id == "prune_quarantine":
            res = self._prune_quarantine(config, is_dry)
        elif action_id == "reclaim_memory_guidance":
            res = ExecutionResult(
                ok=True,
                dry_run=True,
                title="Memory Guidance",
                message="Memory pressure relief is guided in Process & AI Hunter to protect active work.",
            )
        else:
            res = ExecutionResult(
                ok=False,
                dry_run=is_dry,
                title=action_id,
                message=f"Action '{action_id}' requires manual execution via Playbook commands.",
            )

        self.journal.log_event(
            component="GuardedExecutor",
            action=action_id,
            how=f"{'Dry-run simulation' if res.dry_run else 'Guarded execution'}: {res.message}",
            why=why,
            outcome="ok" if res.ok else "warning",
            details=f"files={res.files_affected}, bytes={res.bytes_affected}, entry_id={res.entry_id}",
        )
        return res

    def _clean_caches(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        params: dict[str, Any],
        is_dry: bool,
    ) -> ExecutionResult:
        targets = adapter.discover_cache_targets()
        selected_ids = set(params.get("target_ids") or [t.target_id for t in targets if t.selected_by_default])
        paths = [Path(t.path) for t in targets if t.target_id in selected_ids and t.exists]
        return self._clean_path_list("Safe Cache & Temp Sweep", paths, adapter, config, is_dry)

    def _clean_path_list(
        self,
        title: str,
        paths: list[Path],
        adapter: BaseOSAdapter,
        config: AppConfig,
        is_dry: bool,
    ) -> ExecutionResult:
        entry_id = f"act-{uuid.uuid4().hex[:8]}"
        files_to_clean: list[Path] = []
        for p in paths:
            if adapter.is_path_protected(p):
                continue
            files_to_clean.extend(self._collect_safe_files(p, adapter, min_age_seconds=300))

        total_bytes = 0
        for f in files_to_clean:
            try:
                total_bytes += f.stat().st_size
            except OSError:
                pass

        if is_dry:
            preview_items: list[dict[str, str]] = []
            for f in files_to_clean[:60]:
                try:
                    sz = f.stat().st_size
                except OSError:
                    sz = 0
                preview_items.append({"path": str(f), "size": format_bytes(sz)})
            msg = f"[DRY RUN] Would quarantine {len(files_to_clean)} stale file(s) ({format_bytes(total_bytes)})."
            self.rollback_mgr.record(
                RollbackEntry(
                    entry_id=entry_id,
                    timestamp=utc_now_iso(),
                    action_id="clean_temp_caches",
                    title=title,
                    dry_run=True,
                    status="applied",
                    summary=msg,
                    bytes_affected=total_bytes,
                )
            )
            return ExecutionResult(
                ok=True,
                dry_run=True,
                title=title,
                message=msg,
                bytes_affected=total_bytes,
                files_affected=len(files_to_clean),
                entry_id=entry_id,
                preview_items=preview_items,
            )

        q_mappings: list[dict[str, str]] = []
        moved_bytes = 0
        moved_files = 0
        for f in files_to_clean:
            if config.quarantine_enabled:
                ok, mapping, sz = self.rollback_mgr.quarantine_file(entry_id, f)
                if ok and mapping:
                    q_mappings.append(mapping)
                    moved_bytes += sz
                    moved_files += 1
            else:
                try:
                    sz = f.stat().st_size
                    f.unlink(missing_ok=True)
                    moved_bytes += sz
                    moved_files += 1
                except OSError:
                    pass

        msg = (
            f"Quarantined {moved_files} stale file(s) ({format_bytes(moved_bytes)}). Reversible in Rollback Vault."
            if config.quarantine_enabled
            else f"Deleted {moved_files} stale file(s) ({format_bytes(moved_bytes)})."
        )
        self.rollback_mgr.record(
            RollbackEntry(
                entry_id=entry_id,
                timestamp=utc_now_iso(),
                action_id="clean_temp_caches",
                title=title,
                dry_run=False,
                status="applied",
                summary=msg,
                quarantine_paths=q_mappings,
                bytes_affected=moved_bytes,
            )
        )
        return ExecutionResult(
            ok=True,
            dry_run=False,
            title=title,
            message=msg,
            bytes_affected=moved_bytes,
            files_affected=moved_files,
            entry_id=entry_id,
        )

    def _vacuum_logs(self, adapter: BaseOSAdapter, config: AppConfig, is_dry: bool) -> ExecutionResult:
        targets = [t for t in adapter.discover_cache_targets() if t.category in ("Logs", "Crash Dumps") and t.exists]
        paths = [Path(t.path) for t in targets]
        if not paths:
            return ExecutionResult(
                ok=True,
                dry_run=is_dry,
                title="Log & Crash Hygiene",
                message="User log & crash directories are already clean (0 bytes to rotate).",
            )
        return self._clean_path_list("User Log & Crash Hygiene", paths, adapter, config, is_dry)

    def _flush_dns(self, adapter: BaseOSAdapter, is_dry: bool) -> ExecutionResult:
        cmd = adapter.dns_flush_command()
        if not cmd:
            return ExecutionResult(
                ok=False,
                dry_run=is_dry,
                title="Flush DNS Cache",
                message="No supported local DNS flush utility detected without elevation; use Playbook commands.",
            )
        if is_dry:
            return ExecutionResult(
                ok=True,
                dry_run=True,
                title="Flush DNS Cache",
                message=f"[DRY RUN] Would execute: {' '.join(cmd)}",
            )
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                entry_id = f"act-{uuid.uuid4().hex[:8]}"
                self.rollback_mgr.record(
                    RollbackEntry(
                        entry_id=entry_id,
                        timestamp=utc_now_iso(),
                        action_id="flush_dns_cache",
                        title="Flush DNS Cache",
                        dry_run=False,
                        status="applied",
                        summary=f"Executed {' '.join(cmd)} cleanly.",
                    )
                )
                return ExecutionResult(
                    ok=True,
                    dry_run=False,
                    title="Flush DNS Cache",
                    message="OS DNS resolver cache flushed cleanly.",
                    entry_id=entry_id,
                )
            return ExecutionResult(
                ok=False,
                dry_run=False,
                title="Flush DNS Cache",
                message=f"DNS flush requires elevated privileges ({proc.stderr.strip() or 'exit ' + str(proc.returncode)}).",
            )
        except Exception as exc:
            return ExecutionResult(ok=False, dry_run=False, title="Flush DNS Cache", message=str(exc))

    def _clean_duplicate_files(
        self,
        raw_paths: list[str],
        adapter: BaseOSAdapter,
        config: AppConfig,
        is_dry: bool,
    ) -> ExecutionResult:
        files: list[Path] = []
        blocked = 0
        for p_str in raw_paths:
            p = Path(p_str)
            if adapter.is_path_protected(p):
                blocked += 1
                continue
            if p.exists() and p.is_file() and not p.is_symlink():
                files.append(p)
        if not files:
            msg = (
                f"Blocked {blocked} protected path(s); no safe duplicate files selected."
                if blocked
                else "No eligible duplicate files to quarantine."
            )
            return ExecutionResult(ok=True, dry_run=is_dry, title="Quarantine Duplicate Files", message=msg)

        if is_dry:
            total_b = sum(f.stat().st_size for f in files if f.exists())
            return ExecutionResult(
                ok=True,
                dry_run=True,
                title="Quarantine Duplicate Files",
                message=f"[DRY RUN] Would quarantine {len(files)} duplicate file(s) ({format_bytes(total_b)}).",
                bytes_affected=total_b,
                files_affected=len(files),
            )

        entry_id = f"act-{uuid.uuid4().hex[:8]}"
        moved: list[dict[str, str]] = []
        total_bytes = 0
        for fp in files:
            ok, rec, sz = self.rollback_mgr.quarantine_file(entry_id, fp)
            if ok and rec:
                moved.append(rec)
                total_bytes += sz
        if moved:
            self.rollback_mgr.record(
                RollbackEntry(
                    entry_id=entry_id,
                    timestamp=utc_now_iso(),
                    action_id="clean_duplicates",
                    title="Quarantine Duplicate Files",
                    dry_run=False,
                    status="applied",
                    summary=f"Quarantined {len(moved)} duplicate file(s) ({format_bytes(total_bytes)}).",
                    quarantine_paths=moved,
                    bytes_affected=total_bytes,
                )
            )
        return ExecutionResult(
            ok=True,
            dry_run=False,
            title="Quarantine Duplicate Files",
            message=f"Quarantined {len(moved)} duplicate file(s), reclaiming {format_bytes(total_bytes)}.",
            bytes_affected=total_bytes,
            files_affected=len(moved),
            entry_id=entry_id if moved else None,
        )

    def _prune_quarantine(self, config: AppConfig, is_dry: bool) -> ExecutionResult:
        q_bytes, q_files = self.rollback_mgr.quarantine_stats()
        if is_dry:
            return ExecutionResult(
                ok=True,
                dry_run=True,
                title="Prune Quarantine Vault",
                message=f"[DRY RUN] Quarantine holds {q_files} file(s) ({format_bytes(q_bytes)}).",
            )
        removed, freed = self.rollback_mgr.prune_quarantine(
            config.quarantine_retention_days, config.quarantine_max_mb
        )
        return ExecutionResult(
            ok=True,
            dry_run=False,
            title="Prune Quarantine Vault",
            message=f"Swept {removed} expired quarantine file(s), freeing {format_bytes(freed)}.",
            bytes_affected=freed,
            files_affected=removed,
        )

    def safe_renice_process(self, pid: int, config: AppConfig, nice_delta: int = 5) -> tuple[bool, str]:
        own_pid = os.getpid()
        if pid <= 4 or pid == own_pid:
            msg = "Blocked by Guardrail: cannot alter priority of kernel/init or AstraCradle itself."
            self.journal.log_event("GuardedExecutor", "safe_renice_process", f"Rejected PID {pid}", msg, "skipped")
            return False, msg
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if name.lower() in {x.lower() for x in config.excluded_processes}:
                msg = f"Blocked by Guardrail: '{name}' is on Protected Process Allow-List."
                self.journal.log_event("GuardedExecutor", "safe_renice_process", f"Rejected {name} ({pid})", msg, "skipped")
                return False, msg
            cur = proc.nice()
            if os.name == "nt":
                proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # type: ignore[attr-defined]
            else:
                proc.nice(min(19, int(cur) + nice_delta))
            msg = f"Lowered background priority for '{name}' (PID {pid})."
            self.journal.log_event(
                "GuardedExecutor",
                "safe_renice_process",
                f"Adjusted nice priority for {name} (PID {pid})",
                "Reduce background CPU contention without terminating app",
                "ok",
            )
            return True, msg
        except psutil.AccessDenied:
            return False, f"Permission denied lowering priority for PID {pid}."
        except psutil.NoSuchProcess:
            return False, f"Process {pid} already exited."
        except Exception as exc:
            return False, str(exc)

    def safe_terminate_process(self, pid: int, config: AppConfig, why: str = "User requested turn-off") -> tuple[bool, str]:
        own_pid = os.getpid()
        if pid <= 4 or pid == own_pid:
            msg = "Blocked by Guardrail: cannot terminate kernel/init or AstraCradle itself."
            self.journal.log_event("GuardedExecutor", "safe_terminate_process", f"Rejected PID {pid}", msg, "skipped")
            return False, msg
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if name.lower() in {x.lower() for x in config.excluded_processes}:
                msg = f"Blocked by Guardrail: '{name}' is protected from termination."
                self.journal.log_event("GuardedExecutor", "safe_terminate_process", f"Rejected {name}", msg, "skipped")
                return False, msg
            proc.terminate()
            msg = f"Sent graceful terminate signal (SIGTERM) to '{name}' (PID {pid})."
            self.journal.log_event("GuardedExecutor", "safe_terminate_process", msg, why, "ok")
            return True, msg
        except psutil.AccessDenied:
            return False, f"Permission denied terminating PID {pid}."
        except psutil.NoSuchProcess:
            return False, f"Process {pid} already exited."
        except Exception as exc:
            return False, str(exc)
