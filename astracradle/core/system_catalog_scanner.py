"""Multi-Drive File & Program Catalog, Passive Scanner, Known-Issues DB & Quiet Auto-Healer."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import stat as _stat
import subprocess
import threading
import time
from typing import Any
import uuid

import psutil

from .config import AppConfig
from .journal import ForensicJournal
from .models import RollbackEntry, utc_now_iso
from .os_adapters import BaseOSAdapter
from .rollback import RollbackManager


@dataclass(frozen=True)
class KnownIssueRule:
    issue_id: str
    title: str
    category: str  # filesystem | program | config | security | conflict | performance
    severity: str  # low | medium | high
    safety_tier: str  # safe_auto | confirm_yes_no
    confidence: float
    explanation: str
    fix_action: str
    platforms: tuple[str, ...] = ("Linux", "Darwin", "Windows")


KNOWN_ISSUES_DB: tuple[KnownIssueRule, ...] = (
    KnownIssueRule(
        issue_id="dangling_symlink",
        title="Broken / Dangling Symlink",
        category="filesystem",
        severity="low",
        safety_tier="safe_auto",
        confidence=0.98,
        explanation="Symbolic link points to a target that no longer exists, causing file-manager or launcher stalls.",
        fix_action="quarantine_or_unlink_symlink",
    ),
    KnownIssueRule(
        issue_id="corrupt_json_cache",
        title="Malformed JSON User Cache / State File",
        category="config",
        severity="medium",
        safety_tier="safe_auto",
        confidence=0.95,
        explanation="Truncated or invalid JSON in user cache causes applications or plugins to crash or fail to launch.",
        fix_action="quarantine_file",
    ),
    KnownIssueRule(
        issue_id="stale_lock_file",
        title="Orphaned Stale Lock File",
        category="filesystem",
        severity="medium",
        safety_tier="safe_auto",
        confidence=0.92,
        explanation="Leftover .lock / SingletonLock / db.lck file from an unclean exit blocks the app from starting.",
        fix_action="quarantine_file",
    ),
    KnownIssueRule(
        issue_id="zero_byte_desktop_entry",
        title="Corrupted / Empty App Launcher (.desktop)",
        category="config",
        severity="low",
        safety_tier="safe_auto",
        confidence=0.96,
        explanation="0-byte or missing-Exec .desktop launcher clutters app menus and triggers desktop warnings.",
        fix_action="quarantine_file",
        platforms=("Linux",),
    ),
    KnownIssueRule(
        issue_id="coredump_or_crash_bloat",
        title="Leftover Crash Dump / Core File",
        category="performance",
        severity="low",
        safety_tier="safe_auto",
        confidence=0.94,
        explanation="Large leftover .core / .dmp crash dump consumes multi-GB disk space silently.",
        fix_action="quarantine_file",
    ),
    KnownIssueRule(
        issue_id="incomplete_download_artifact",
        title="Stale Incomplete Temp Download (>7d)",
        category="filesystem",
        severity="low",
        safety_tier="safe_auto",
        confidence=0.90,
        explanation="Abandoned .part / .crdownload / .tmp file older than 7 days wastes drive capacity.",
        fix_action="quarantine_file",
    ),
    KnownIssueRule(
        issue_id="user_script_not_executable",
        title="User Bin/Fish Script Missing +x Permission",
        category="security",
        severity="low",
        safety_tier="safe_auto",
        confidence=0.93,
        explanation="Script in ~/.local/bin has a valid shebang (#!) but lacks executable permission bit.",
        fix_action="chmod_user_exec",
        platforms=("Linux", "Darwin"),
    ),
    KnownIssueRule(
        issue_id="world_writable_user_script",
        title="World-Writable User Script/Hook (777 Risk)",
        category="security",
        severity="high",
        safety_tier="safe_auto",
        confidence=0.97,
        explanation="Script in user bin/config is world-writable (o+w), allowing tampering by other local accounts.",
        fix_action="strip_world_writable",
        platforms=("Linux", "Darwin"),
    ),
    KnownIssueRule(
        issue_id="fish_syntax_error",
        title="Fish Shell Config Syntax Issue",
        category="config",
        severity="medium",
        safety_tier="confirm_yes_no",
        confidence=0.91,
        explanation="Invalid syntax in ~/.config/fish/conf.d/*.fish slows or breaks terminal startup.",
        fix_action="advise_fish_fix",
        platforms=("Linux", "Darwin"),
    ),
    KnownIssueRule(
        issue_id="arch_tlp_ppd_conflict",
        title="TLP vs power-profiles-daemon Conflict",
        category="conflict",
        severity="medium",
        safety_tier="confirm_yes_no",
        confidence=0.94,
        explanation="Both TLP and power-profiles-daemon are installed; running both causes erratic CPU governor & battery behavior.",
        fix_action="advise_disable_conflict",
        platforms=("Linux",),
    ),
    KnownIssueRule(
        issue_id="gpu_shader_cache_bloat",
        title="Oversized Browser / Electron GPUCache",
        category="performance",
        severity="low",
        safety_tier="safe_auto",
        confidence=0.89,
        explanation="Stale GPUCache/ShaderCache directories over 512 MB can trigger stuttering and graphical glitches.",
        fix_action="quarantine_file",
    ),
)

_ISSUE_BY_ID: dict[str, KnownIssueRule] = {r.issue_id: r for r in KNOWN_ISSUES_DB}


@dataclass
class CatalogIssueFinding:
    finding_id: str
    issue_id: str
    title: str
    target_path: str
    category: str
    severity: str
    safety_tier: str
    confidence: float
    explanation: str
    fix_action: str
    status: str = "open"  # open | auto_fixed | fixed | skipped
    detected_at: str = field(default_factory=utc_now_iso)


@dataclass
class CatalogScanSummary:
    drives_scanned: list[str]
    files_cataloged: int
    files_checked_this_pass: int
    programs_cataloged: int
    newly_installed_programs: list[str]
    issues_found: list[CatalogIssueFinding]
    quiet_fixes_applied: int
    elapsed_sec: float
    mode: str  # full | passive | programs_only


class SystemCatalogAndHealer:
    """SQLite-backed multi-drive file/program catalog, passive scanner, and known-bug auto-healer."""

    _VIRTUAL_FS = frozenset(
        {"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "squashfs", "overlay", "autofs", "fuse.portal"}
    )
    _SKIP_DIRS = frozenset(
        {".git", "node_modules", "__pycache__", ".venv", "venv", "proc", "sys", "dev", "run", ".Trash", "Trash", ".astracradle"}
    )

    def __init__(
        self,
        cfg_mgr_or_base: Any,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
    ) -> None:
        self.base_dir = Path(cfg_mgr_or_base.base_dir) if hasattr(cfg_mgr_or_base, "base_dir") else Path(cfg_mgr_or_base)
        self.db_path = self.base_dir / "state" / "system_catalog.db"
        self.rollback_mgr = rollback_mgr
        self.journal = journal
        self._lock = threading.RLock()
        self._passive_cursor: int = 0
        self._has_fish = bool(shutil.which("fish"))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA cache_size=-4000;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            try:
                with self._connect() as conn:
                    self._create_tables(conn)
            except sqlite3.DatabaseError:
                # Self-heal corrupted SQLite DB cleanly
                bak = self.db_path.with_suffix(".corrupt.bak")
                if self.db_path.exists():
                    try:
                        shutil.move(str(self.db_path), str(bak))
                    except Exception:
                        self.db_path.unlink(missing_ok=True)
                with self._connect() as conn:
                    self._create_tables(conn)

    @staticmethod
    def _create_tables(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS programs (
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                version TEXT DEFAULT '',
                install_path TEXT DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                is_new INTEGER DEFAULT 0,
                PRIMARY KEY (name, source)
            );
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                drive_root TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                issue_id TEXT DEFAULT '',
                last_checked TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fixes_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                target_path TEXT NOT NULL,
                issue_id TEXT NOT NULL,
                action_taken TEXT NOT NULL,
                outcome TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_files_drive ON files(drive_root);
            CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
            """
        )

    @classmethod
    def list_all_drives(cls, include_roots: list[Path] | None = None) -> list[Path]:
        """Returns real mounted storage drives across Linux, macOS, and Windows."""
        if include_roots:
            return [Path(p) for p in include_roots if Path(p).exists()]
        roots: list[Path] = []
        seen: set[str] = set()
        try:
            for part in psutil.disk_partitions(all=False):
                fstype = (part.fstype or "").lower()
                if fstype in cls._VIRTUAL_FS:
                    continue
                mp = Path(part.mountpoint)
                if str(mp) in ("/boot", "/boot/efi"):
                    continue
                if mp.exists() and str(mp) not in seen:
                    seen.add(str(mp))
                    roots.append(mp)
        except Exception:
            pass
        if not roots:
            roots.append(Path.home())
        return roots

    def discover_installed_programs(self) -> tuple[list[dict[str, str]], list[str]]:
        """Catalogs installed apps/packages and returns (programs, newly_installed_names)."""
        now = utc_now_iso()
        found: dict[tuple[str, str], dict[str, str]] = {}
        sys_name = platform.system()

        if sys_name == "Linux":
            # 1. Arch / CachyOS pacman
            if shutil.which("pacman"):
                try:
                    proc = subprocess.run(["pacman", "-Q"], capture_output=True, text=True, timeout=5, check=False)
                    for ln in (proc.stdout or "").splitlines():
                        parts = ln.strip().split(None, 1)
                        if parts:
                            found[(parts[0], "pacman")] = {"name": parts[0], "source": "pacman", "version": parts[1] if len(parts) > 1 else "", "install_path": "/usr/bin"}
                except Exception:
                    pass
            # 2. Debian/Ubuntu dpkg fallback
            elif shutil.which("dpkg-query"):
                try:
                    proc = subprocess.run(
                        ["dpkg-query", "-W", "-f=${Package}\t${Version}\n"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    for ln in (proc.stdout or "").splitlines():
                        parts = ln.split("\t")
                        if parts[0]:
                            found[(parts[0], "dpkg")] = {"name": parts[0], "source": "dpkg", "version": parts[1] if len(parts) > 1 else "", "install_path": "/usr/bin"}
                except Exception:
                    pass
            # 3. Flatpak & Desktop apps
            if shutil.which("flatpak"):
                try:
                    proc = subprocess.run(["flatpak", "list", "--app", "--columns=application,version"], capture_output=True, text=True, timeout=4, check=False)
                    for ln in (proc.stdout or "").splitlines():
                        parts = ln.split("\t")
                        if parts[0]:
                            found[(parts[0].strip(), "flatpak")] = {"name": parts[0].strip(), "source": "flatpak", "version": parts[1].strip() if len(parts) > 1 else "", "install_path": "flatpak"}
                except Exception:
                    pass
            for app_dir in (Path("/usr/share/applications"), Path.home() / ".local" / "share" / "applications"):
                if app_dir.exists():
                    try:
                        for f in app_dir.glob("*.desktop"):
                            found[(f.stem, "desktop")] = {"name": f.stem, "source": "desktop", "version": "", "install_path": str(f)}
                    except OSError:
                        pass
        elif sys_name == "Darwin":
            for app_dir in (Path("/Applications"), Path.home() / "Applications"):
                if app_dir.exists():
                    try:
                        for f in app_dir.glob("*.app"):
                            found[(f.stem, "macos_app")] = {"name": f.stem, "source": "macos_app", "version": "", "install_path": str(f)}
                    except OSError:
                        pass
        elif sys_name == "Windows":
            for pdir in (Path(os.environ.get("ProgramFiles", "C:\\Program Files")), Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"):
                if pdir.exists():
                    try:
                        for f in pdir.iterdir():
                            if f.is_dir():
                                found[(f.name, "windows_dir")] = {"name": f.name, "source": "windows_dir", "version": "", "install_path": str(f)}
                    except OSError:
                        pass

        # User local bin binaries/scripts across all Unix systems
        local_bin = Path.home() / ".local" / "bin"
        if local_bin.exists():
            try:
                for f in local_bin.iterdir():
                    if f.is_file():
                        found[(f.name, "local_bin")] = {"name": f.name, "source": "local_bin", "version": "", "install_path": str(f)}
            except OSError:
                pass

        newly_installed: list[str] = []
        with self._lock:
            with self._connect() as conn:
                existing = {(r[0], r[1]) for r in conn.execute("SELECT name, source FROM programs").fetchall()}
                had_baseline = len(existing) > 0
                rows = []
                for key, meta in found.items():
                    is_new = 1 if (had_baseline and key not in existing) else 0
                    if is_new:
                        newly_installed.append(f"{meta['name']} ({meta['source']})")
                    rows.append((meta["name"], meta["source"], meta["version"], meta["install_path"], now, now, is_new))
                conn.executemany(
                    """
                    INSERT INTO programs (name, source, version, install_path, first_seen, last_seen, is_new)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name, source) DO UPDATE SET
                        version=excluded.version,
                        install_path=excluded.install_path,
                        last_seen=excluded.last_seen,
                        is_new=CASE WHEN excluded.is_new=1 THEN 1 ELSE programs.is_new END
                    """,
                    rows,
                )

        if newly_installed:
            self.journal.log_event(
                "SystemCatalog",
                "new_programs_detected",
                f"Detected {len(newly_installed)} newly installed program(s): {', '.join(newly_installed[:6])}",
                "Automatically catalog newly installed apps and inspect for known conflicts/bugs",
                "ok",
            )
        return list(found.values()), newly_installed

    @staticmethod
    def _classify_file(p: Path) -> str:
        suf = p.suffix.lower()
        if suf in (".json", ".toml", ".yaml", ".yml", ".ini", ".conf", ".desktop", ".fish"):
            return "config"
        if suf in (".log", ".dmp", ".core"):
            return "diagnostic"
        if suf in (".part", ".crdownload", ".tmp", ".lock"):
            return "temp_or_lock"
        if suf in (".sh", ".py", ".fish", ".exe", ".AppImage"):
            return "executable"
        return "regular"

    def _inspect_single_path(
        self,
        p: Path,
        now_ts: float,
        pre_stat: os.stat_result | None = None,
        is_sym: bool | None = None,
    ) -> str | None:
        """Evaluates one file/symlink against KNOWN_ISSUES_DB; returns issue_id or None."""
        try:
            sym = p.is_symlink() if is_sym is None else is_sym
            if sym:
                if not p.exists():
                    return "dangling_symlink"
                return None
            st = p.stat() if pre_stat is None else pre_stat
        except OSError:
            return None

        name_low = p.name.lower()
        suf = p.suffix.lower()

        # 1. Empty / invalid .desktop entry
        if suf == ".desktop":
            if st.st_size == 0:
                return "zero_byte_desktop_entry"
            if st.st_size < 8192:
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                    if "[Desktop Entry]" in txt and "Exec=" not in txt and "Type=Link" not in txt:
                        return "zero_byte_desktop_entry"
                except OSError:
                    pass

        # 2. Malformed JSON in user config/cache
        if suf == ".json" and 0 < st.st_size <= 262144 and (".cache" in p.parts or ".config" in p.parts or "state" in p.parts):
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return "corrupt_json_cache"

        # 3. Orphaned lockfiles older than 1 hour (never touch active running process locks blindly)
        if (name_low.endswith(".lock") or name_low == "singletonlock") and (now_ts - st.st_mtime) > 3600:
            if ".cache" in p.parts or "tmp" in str(p).lower():
                return "stale_lock_file"

        # 4. Leftover crash dumps / core files
        if suf in (".dmp", ".core") or name_low.startswith("core."):
            if st.st_size > 1024:
                return "coredump_or_crash_bloat"

        # 5. Incomplete downloads older than 7 days
        if suf in (".part", ".crdownload") and (now_ts - st.st_mtime) > 7 * 86400:
            return "incomplete_download_artifact"

        # 6. User scripts in ~/.local/bin permissions
        if ".local" in p.parts and "bin" in p.parts and _stat.S_ISREG(st.st_mode):
            mode = st.st_mode
            if mode & 0o002:
                return "world_writable_user_script"
            if suf in (".sh", ".fish", ".py") and not (mode & 0o100):
                try:
                    with open(p, "rb") as fh:
                        if fh.read(2) == b"#!":
                            return "user_script_not_executable"
                except OSError:
                    pass

        # 7. Fish config syntax check
        if suf == ".fish" and "fish" in p.parts and self._has_fish:
            try:
                pr = subprocess.run(["fish", "-n", str(p)], capture_output=True, text=True, timeout=2, check=False)
                if pr.returncode != 0:
                    return "fish_syntax_error"
            except Exception:
                pass

        return None

    def _check_program_issues(self, programs: list[dict[str, str]]) -> list[CatalogIssueFinding]:
        names = {p["name"].lower() for p in programs}
        findings: list[CatalogIssueFinding] = []
        if "tlp" in names and "power-profiles-daemon" in names:
            r = _ISSUE_BY_ID["arch_tlp_ppd_conflict"]
            findings.append(
                CatalogIssueFinding(
                    finding_id="prog_tlp_ppd",
                    issue_id=r.issue_id,
                    title=r.title,
                    target_path="tlp + power-profiles-daemon",
                    category=r.category,
                    severity=r.severity,
                    safety_tier=r.safety_tier,
                    confidence=r.confidence,
                    explanation=r.explanation,
                    fix_action=r.fix_action,
                )
            )
        return findings

    def apply_issue_fix(
        self,
        finding: CatalogIssueFinding,
        adapter: BaseOSAdapter,
        config: AppConfig,
        dry_run: bool = False,
    ) -> tuple[bool, str]:
        """Applies a reversible, protected fix for a known catalog issue."""
        p = Path(finding.target_path)
        action = finding.fix_action
        if dry_run:
            return True, f"DRY-RUN: Would apply {action} to {finding.target_path}"

        if action == "quarantine_or_unlink_symlink":
            if not p.is_symlink():
                return False, "Target is no longer a symlink."
            if adapter.is_path_protected(p):
                return False, f"Protected path blocked: {p}"
            try:
                p.unlink()
                self._record_fix(finding.target_path, finding.issue_id, "unlinked_dangling_symlink", "ok")
                finding.status = "fixed"
                return True, f"Removed dangling symlink {p.name}"
            except OSError as exc:
                return False, f"Could not remove symlink: {exc}"

        if action == "quarantine_file":
            if not p.exists():
                return False, "File no longer exists."
            if adapter.is_path_protected(p):
                return False, f"Protected path blocked: {p}"
            if config.quarantine_enabled:
                entry_id = f"cat_{uuid.uuid4().hex[:10]}"
                ok_q, mapping, sz = self.rollback_mgr.quarantine_file(entry_id, p)
                if ok_q and mapping:
                    self.rollback_mgr.record(
                        RollbackEntry(
                            entry_id=entry_id,
                            timestamp=utc_now_iso(),
                            action_id=f"catalog_{finding.issue_id}",
                            title=f"Catalog Auto-Fix: {finding.title}",
                            dry_run=False,
                            status="applied",
                            summary=f"{finding.explanation} ({p.name}, {sz} B)",
                            quarantine_paths=[mapping],
                        )
                    )
                    self._record_fix(finding.target_path, finding.issue_id, "quarantined", "ok")
                    finding.status = "fixed"
                    return True, f"Safely quarantined {p.name} (reversible in Vault)"
                return False, f"Could not quarantine {p}"
            return False, "Quarantine disabled; skipped destructive delete."

        if action == "chmod_user_exec":
            try:
                os.chmod(p, p.stat().st_mode | 0o700)
                self._record_fix(finding.target_path, finding.issue_id, "chmod_u+rwx", "ok")
                finding.status = "fixed"
                return True, f"Restored +x executable permission on {p.name}"
            except OSError as exc:
                return False, f"chmod failed: {exc}"

        if action == "strip_world_writable":
            try:
                os.chmod(p, p.stat().st_mode & ~0o022)
                self._record_fix(finding.target_path, finding.issue_id, "chmod_go-w", "ok")
                finding.status = "fixed"
                return True, f"Hardened permissions (removed g/o write) on {p.name}"
            except OSError as exc:
                return False, f"chmod failed: {exc}"

        return False, f"Guided manual/confirm action required: {finding.explanation}"

    def _record_fix(self, target_path: str, issue_id: str, action_taken: str, outcome: str) -> None:
        now = utc_now_iso()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO fixes_history (timestamp, target_path, issue_id, action_taken, outcome) VALUES (?, ?, ?, ?, ?)",
                    (now, target_path, issue_id, action_taken, outcome),
                )
                conn.execute(
                    "UPDATE files SET status='fixed', issue_id='' WHERE path=?",
                    (target_path,),
                )
        self.journal.log_event(
            "SystemCatalog",
            "quiet_auto_fix",
            f"[{issue_id}] {action_taken} -> {target_path}",
            "Quietly fix cataloged file/program bug safely",
            outcome,
        )

    def _walk_roots_fast(self, roots: list[Path], max_files: int) -> list[tuple[Path, str, bool, os.stat_result | None]]:
        """Fast single-pass os.scandir traversal avoiding symlink loops and virtual system trees."""
        collected: list[tuple[Path, str, bool, os.stat_result | None]] = []
        skip_dirs = self._SKIP_DIRS
        stack: list[tuple[str, int]] = [(str(r), 0) for r in reversed(roots) if r.exists()]
        while stack and len(collected) < max_files:
            cur_dir, depth = stack.pop()
            if depth > 7:
                continue
            try:
                with os.scandir(cur_dir) as it:
                    for entry in it:
                        if len(collected) >= max_files:
                            break
                        name = entry.name
                        if name in skip_dirs:
                            continue
                        ep_str = entry.path
                        if entry.is_symlink():
                            collected.append((Path(ep_str), ep_str, True, None))
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if ep_str in ("/proc", "/sys", "/dev", "/run", "/snap"):
                                continue
                            stack.append((ep_str, depth + 1))
                        elif entry.is_file(follow_symlinks=False):
                            try:
                                st = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            collected.append((Path(ep_str), ep_str, False, st))
            except OSError:
                continue
        return collected

    def run_full_scan(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        scan_roots: list[Path] | None = None,
        max_files: int = 8000,
        quiet_autofix: bool = True,
        dry_run: bool = False,
    ) -> CatalogScanSummary:
        """Scans all mounted drives + installed programs, updates SQLite catalog, and quietly fixes safe bugs."""
        t0 = time.monotonic()
        roots = self.list_all_drives(include_roots=scan_roots)
        effective_roots: list[Path] = []
        for r in roots:
            if str(r) == "/" and scan_roots is None:
                for sub in (Path.home(), Path("/opt"), Path("/usr/local/bin")):
                    if sub.exists():
                        effective_roots.append(sub)
            else:
                effective_roots.append(r)

        root_strs = [str(r) for r in effective_roots]
        default_root = root_strs[0] if root_strs else "/"
        programs, newly_installed = self.discover_installed_programs()
        files = self._walk_roots_fast(effective_roots, max_files=max_files)
        now_iso = utc_now_iso()
        now_ts = time.time()

        # Fetch existing mtimes so unchanged files don't need expensive re-parsing
        with self._lock:
            with self._connect() as conn:
                cached = {
                    r[0]: (r[1], r[2], r[3])
                    for r in conn.execute("SELECT path, size_bytes, mtime_ns, issue_id FROM files").fetchall()
                }

        db_rows: list[tuple[str, str, int, int, str, str, str, str]] = []
        findings: list[CatalogIssueFinding] = self._check_program_issues(programs)
        quiet_fixes = 0

        for fp, sp, is_sym, st in files:
            if is_sym or st is None:
                sz, mns = 0, 0
            else:
                sz, mns = int(st.st_size), int(st.st_mtime_ns)

            prev = cached.get(sp)
            if prev and prev[0] == sz and prev[1] == mns and not prev[2]:
                issue_id = None
            else:
                issue_id = self._inspect_single_path(fp, now_ts, pre_stat=st, is_sym=is_sym)

            drive_root = next((rs for rs in root_strs if sp.startswith(rs)), default_root)
            cat = self._classify_file(fp)
            status = "issue" if issue_id else "ok"

            if issue_id and issue_id in _ISSUE_BY_ID:
                rule = _ISSUE_BY_ID[issue_id]
                finding = CatalogIssueFinding(
                    finding_id=f"cat_{hashlib.sha1(sp.encode('utf-8', 'ignore')).hexdigest()[:10]}",
                    issue_id=rule.issue_id,
                    title=rule.title,
                    target_path=sp,
                    category=rule.category,
                    severity=rule.severity,
                    safety_tier=rule.safety_tier,
                    confidence=rule.confidence,
                    explanation=rule.explanation,
                    fix_action=rule.fix_action,
                )
                if quiet_autofix and rule.safety_tier == "safe_auto" and not dry_run:
                    ok_fix, _ = self.apply_issue_fix(finding, adapter, config, dry_run=False)
                    if ok_fix:
                        finding.status = "auto_fixed"
                        status = "fixed"
                        issue_id = ""
                        quiet_fixes += 1
                findings.append(finding)

            db_rows.append((sp, drive_root, sz, mns, cat, status, issue_id or "", now_iso))

        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO files (path, drive_root, size_bytes, mtime_ns, category, status, issue_id, last_checked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        size_bytes=excluded.size_bytes,
                        mtime_ns=excluded.mtime_ns,
                        category=excluded.category,
                        status=excluded.status,
                        issue_id=excluded.issue_id,
                        last_checked=excluded.last_checked
                    """,
                    db_rows,
                )
                total_files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
                total_progs = int(conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0])

        elapsed = round(time.monotonic() - t0, 3)
        self.journal.log_event(
            "SystemCatalog",
            "full_scan",
            f"Scanned {len(effective_roots)} drive root(s), {len(files)} files ({total_files} cataloged), {total_progs} programs, {quiet_fixes} quiet fix(es) in {elapsed}s",
            "Maintain complete file & program intelligence catalog across all drives",
            "ok",
        )
        return CatalogScanSummary(
            drives_scanned=[str(r) for r in effective_roots],
            files_cataloged=total_files,
            files_checked_this_pass=len(files),
            programs_cataloged=total_progs,
            newly_installed_programs=newly_installed,
            issues_found=findings,
            quiet_fixes_applied=quiet_fixes,
            elapsed_sec=elapsed,
            mode="full",
        )

    def run_passive_batch(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        batch_size: int = 120,
        quiet_autofix: bool = True,
        check_new_programs: bool = True,
        dry_run: bool = False,
    ) -> CatalogScanSummary:
        """Passively re-checks cataloged files in tiny low-CPU batches and detects new apps."""
        t0 = time.monotonic()
        newly_installed: list[str] = []
        programs: list[dict[str, str]] = []
        if check_new_programs:
            programs, newly_installed = self.discover_installed_programs()

        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT path FROM files ORDER BY last_checked ASC LIMIT ?",
                    (max(10, int(batch_size)),),
                ).fetchall()
                total_files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
                total_progs = int(conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0])

        now_iso = utc_now_iso()
        now_ts = time.time()
        findings: list[CatalogIssueFinding] = self._check_program_issues(programs) if programs else []
        quiet_fixes = 0
        updates: list[tuple[str, str, str, str]] = []
        missing_paths: list[tuple[str,]] = []

        for (sp,) in rows:
            fp = Path(sp)
            if not fp.exists() and not fp.is_symlink():
                missing_paths.append((sp,))
                continue
            issue_id = self._inspect_single_path(fp, now_ts)
            status = "issue" if issue_id else "ok"
            if issue_id and issue_id in _ISSUE_BY_ID:
                rule = _ISSUE_BY_ID[issue_id]
                finding = CatalogIssueFinding(
                    finding_id=f"cat_{hashlib.sha1(sp.encode('utf-8', 'ignore')).hexdigest()[:10]}",
                    issue_id=rule.issue_id,
                    title=rule.title,
                    target_path=sp,
                    category=rule.category,
                    severity=rule.severity,
                    safety_tier=rule.safety_tier,
                    confidence=rule.confidence,
                    explanation=rule.explanation,
                    fix_action=rule.fix_action,
                )
                if quiet_autofix and rule.safety_tier == "safe_auto" and not dry_run:
                    ok_fix, _ = self.apply_issue_fix(finding, adapter, config, dry_run=False)
                    if ok_fix:
                        finding.status = "auto_fixed"
                        status = "fixed"
                        issue_id = ""
                        quiet_fixes += 1
                findings.append(finding)
            updates.append((status, issue_id or "", now_iso, sp))

        with self._lock:
            with self._connect() as conn:
                if missing_paths:
                    conn.executemany("DELETE FROM files WHERE path=?", missing_paths)
                if updates:
                    conn.executemany("UPDATE files SET status=?, issue_id=?, last_checked=? WHERE path=?", updates)

        return CatalogScanSummary(
            drives_scanned=[],
            files_cataloged=max(0, total_files - len(missing_paths)),
            files_checked_this_pass=len(rows),
            programs_cataloged=total_progs,
            newly_installed_programs=newly_installed,
            issues_found=findings,
            quiet_fixes_applied=quiet_fixes,
            elapsed_sec=round(time.monotonic() - t0, 3),
            mode="passive",
        )

    def get_catalog_stats(self) -> dict[str, Any]:
        with self._lock:
            try:
                with self._connect() as conn:
                    files_cnt = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
                    open_cnt = int(conn.execute("SELECT COUNT(*) FROM files WHERE status='issue'").fetchone()[0])
                    progs_cnt = int(conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0])
                    new_progs = [r[0] for r in conn.execute("SELECT name FROM programs WHERE is_new=1 LIMIT 20").fetchall()]
                    fixes_cnt = int(conn.execute("SELECT COUNT(*) FROM fixes_history").fetchone()[0])
                    drives = [r[0] for r in conn.execute("SELECT DISTINCT drive_root FROM files").fetchall()]
                    return {
                        "files_cataloged": files_cnt,
                        "open_file_issues": open_cnt,
                        "programs_cataloged": progs_cnt,
                        "new_programs": new_progs,
                        "total_fixes_applied": fixes_cnt,
                        "drives": drives,
                        "known_rules_count": len(KNOWN_ISSUES_DB),
                    }
            except Exception:
                return {
                    "files_cataloged": 0,
                    "open_file_issues": 0,
                    "programs_cataloged": 0,
                    "new_programs": [],
                    "total_fixes_applied": 0,
                    "drives": [],
                    "known_rules_count": len(KNOWN_ISSUES_DB),
                }
