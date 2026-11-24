"""Quarantine Vault and Rollback Ledger for 100% reversible system actions."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import uuid

from .config import ConfigManager
from .models import RollbackEntry, utc_now_iso


class RollbackManager:
    """Stores reversible action history and manages the Quarantine Vault."""

    def __init__(self, cfg_mgr: ConfigManager) -> None:
        self.cfg_mgr = cfg_mgr

    @staticmethod
    def _parse_entries(raw: object) -> list[RollbackEntry]:
        if not isinstance(raw, list):
            raise ValueError("Rollback ledger is not a list")
        valid_fields = {f.name for f in RollbackEntry.__dataclass_fields__.values()}
        req = {"entry_id", "timestamp", "action_id", "title", "dry_run", "status", "summary"}
        out: list[RollbackEntry] = []
        for item in raw:
            if isinstance(item, dict) and req.issubset(item.keys()):
                try:
                    out.append(RollbackEntry(**{k: v for k, v in item.items() if k in valid_fields}))
                except Exception:
                    continue
        return out

    def load_entries(self) -> list[RollbackEntry]:
        path = self.cfg_mgr.rollback_path
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return self._parse_entries(raw)
        except Exception:
            if self.cfg_mgr.rollback_bak_path.exists():
                try:
                    raw = json.loads(self.cfg_mgr.rollback_bak_path.read_text(encoding="utf-8"))
                    entries = self._parse_entries(raw)
                    self.save_entries(entries)
                    return entries
                except Exception:
                    pass
            return []

    def save_entries(self, entries: list[RollbackEntry]) -> None:
        self.cfg_mgr.ensure_directories()
        payload = json.dumps([asdict(e) for e in entries], indent=2).encode("utf-8")
        tmp = self.cfg_mgr.rollback_path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, self.cfg_mgr.rollback_path)
        tmp_bak = self.cfg_mgr.rollback_bak_path.with_suffix(".tmp")
        tmp_bak.write_bytes(payload)
        os.replace(tmp_bak, self.cfg_mgr.rollback_bak_path)
        self.cfg_mgr._write_checksum("rollback_ledger.json", self.cfg_mgr.sha256_bytes(payload))

    def record(self, entry: RollbackEntry) -> RollbackEntry:
        entries = self.load_entries()
        entries.insert(0, entry)
        self.save_entries(entries[:200])
        return entry

    def quarantine_file(self, entry_id: str, src: Path) -> tuple[bool, dict[str, str] | None, int]:
        try:
            if not src.exists() or src.is_symlink() or not src.is_file():
                return False, None, 0
            size = src.stat().st_size
            q_root = self.cfg_mgr.quarantine_dir / entry_id
            q_root.mkdir(parents=True, exist_ok=True)
            rel_name = f"{uuid.uuid4().hex[:8]}_{src.name}"
            dest = q_root / rel_name
            shutil.move(str(src), str(dest))
            return True, {"src": str(src), "quarantine": str(dest)}, size
        except Exception:
            return False, None, 0

    def rollback_entry(self, entry_id: str) -> tuple[bool, str]:
        entries = self.load_entries()
        target: RollbackEntry | None = None
        for e in entries:
            if e.entry_id == entry_id:
                target = e
                break
        if target is None:
            return False, f"Rollback entry '{entry_id}' not found."
        if target.dry_run:
            return False, "Dry-run simulation entries made no disk modifications."
        if target.status == "rolled_back":
            return False, "This action has already been rolled back."

        restored = 0
        failed = 0
        for mapping in target.quarantine_paths:
            src = Path(mapping["src"])
            q_path = Path(mapping["quarantine"])
            if not q_path.exists() and not q_path.is_symlink():
                failed += 1
                continue
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(q_path), str(src))
                restored += 1
            except Exception:
                failed += 1

        q_folder = self.cfg_mgr.quarantine_dir / entry_id
        if q_folder.exists():
            try:
                if not any(q_folder.iterdir()):
                    q_folder.rmdir()
            except OSError:
                pass

        if restored > 0 or not target.quarantine_paths:
            target.status = "rolled_back"
            target.reverted_at = utc_now_iso()
            self.save_entries(entries)
            note = f"Restored {restored} file(s)" + (f" ({failed} missing/locked)" if failed else "")
            return True, note
        return False, f"Could not restore files from quarantine ({failed} missing or locked)."

    def quarantine_stats(self) -> tuple[int, int]:
        total_bytes = 0
        total_files = 0
        if not self.cfg_mgr.quarantine_dir.exists():
            return 0, 0
        for p in self.cfg_mgr.quarantine_dir.rglob("*"):
            if p.is_file():
                try:
                    total_bytes += p.stat().st_size
                    total_files += 1
                except OSError:
                    pass
        return total_bytes, total_files

    def prune_quarantine(self, retention_days: int = 14, max_mb: int = 2048) -> tuple[int, int]:
        if not self.cfg_mgr.quarantine_dir.exists():
            return 0, 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        removed_files = 0
        freed_bytes = 0

        files: list[tuple[float, int, Path]] = []
        for p in self.cfg_mgr.quarantine_dir.rglob("*"):
            if p.is_file():
                try:
                    st = p.stat()
                    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        size = st.st_size
                        p.unlink(missing_ok=True)
                        removed_files += 1
                        freed_bytes += size
                    else:
                        files.append((st.st_mtime, st.st_size, p))
                except OSError:
                    continue

        max_bytes = max(32, max_mb) * 1024 * 1024
        current_bytes = sum(item[1] for item in files)
        if current_bytes > max_bytes:
            files.sort(key=lambda x: x[0])
            for _, size, path in files:
                if current_bytes <= max_bytes:
                    break
                try:
                    path.unlink(missing_ok=True)
                    current_bytes -= size
                    freed_bytes += size
                    removed_files += 1
                except OSError:
                    continue

        for d in list(self.cfg_mgr.quarantine_dir.iterdir()):
            if d.is_dir():
                try:
                    if not any(d.iterdir()):
                        d.rmdir()
                except OSError:
                    pass
        return removed_files, freed_bytes

    def shred_quarantine_entry(self, entry_id: str, confirm_irreversible: bool = False) -> tuple[bool, str]:
        """Optional zero-pass overwrite before unlink for confirmed sensitive quarantine items."""
        if not confirm_irreversible:
            return False, "Confirmation required before zero-pass secure shredding."
        entries = self.load_entries()
        target = next((e for e in entries if e.entry_id == entry_id), None)
        if target is None:
            return False, f"Quarantine entry '{entry_id}' not found."
        if target.dry_run:
            return False, "Dry-run entries hold no quarantined files."
        shredded = 0
        for mapping in target.quarantine_paths:
            q_path = Path(mapping.get("quarantine", ""))
            if not q_path.exists() or not q_path.is_file():
                continue
            try:
                sz = q_path.stat().st_size
                if sz > 0:
                    with q_path.open("r+b") as fh:
                        remaining = min(sz, 16 * 1024 * 1024)
                        chunk = b"\x00" * 65536
                        while remaining > 0:
                            w = min(remaining, len(chunk))
                            fh.write(chunk[:w])
                            remaining -= w
                        fh.flush()
                        os.fsync(fh.fileno())
                q_path.unlink(missing_ok=True)
                shredded += 1
            except OSError:
                pass
        q_folder = self.cfg_mgr.quarantine_dir / entry_id
        if q_folder.exists():
            try:
                if not any(q_folder.iterdir()):
                    q_folder.rmdir()
            except OSError:
                pass
        target.status = "shredded"
        target.reverted_at = utc_now_iso()
        self.save_entries(entries)
        return True, f"Zero-pass shredded {shredded} quarantined file(s) permanently."
