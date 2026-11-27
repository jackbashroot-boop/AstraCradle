from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from .models import utc_now_iso

_SKIP_VIRTUAL_ROOTS = ("/proc", "/sys", "/dev", "/run", "/snap", "/var/lib/docker/overlay2")
_EXEC_EXTS = {
    ".sh", ".bash", ".zsh", ".py", ".pl", ".rb", ".js", ".ts",
    ".appimage", ".deb", ".rpm", ".run", ".bin", ".exe", ".msi",
    ".bat", ".cmd", ".ps1", ".desktop", ".service", ".timer", ".so",
}
_SUSPICIOUS_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(?:curl|wget)\s+[^\n|]{4,180}\|\s*(?:sh|bash|zsh|python)", re.I), "high_risk", "Pipes remote download directly into a shell/interpreter"),
    (re.compile(r"/dev/tcp/\d+\.\d+\.\d+\.\d+/\d+", re.I), "high_risk", "Contains raw /dev/tcp reverse-shell endpoint"),
    (re.compile(r"\b(?:nc|ncat|netcat)\b[^\n]{0,80}\s-e\s+/(?:bin|usr)", re.I), "high_risk", "Contains netcat remote shell execution flag (-e)"),
    (re.compile(r"base64\s+(?:-d|--decode)[^\n|]{0,80}\|\s*(?:sh|bash|python)", re.I), "high_risk", "Decodes obfuscated base64 payload directly into shell"),
    (re.compile(r"\b(?:xmrig|stratum\+tcp|minerd|cryptonight)\b", re.I), "high_risk", "Matches cryptocurrency miner signature"),
    (re.compile(r"LD_PRELOAD\s*=", re.I), "warning", "Sets LD_PRELOAD library injection variable"),
    (re.compile(r"rm\s+-rf\s+/(?:\s|$|\*)", re.I), "high_risk", "Contains destructive root filesystem wipe command"),
    (re.compile(r"chmod\s+(?:\+s|47\d\d|777)\s+", re.I), "warning", "Alters SUID or world-writable permissions"),
]


def _file_sha256(path: Path, max_bytes: int = 16 * 1024 * 1024) -> str:
    try:
        st = path.stat()
        if st.st_size > max_bytes:
            return ""
        h = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


class PcFileGuardian:
    """SQLite-backed full PC file catalog, passive idle scanner, incoming verifier, and pre-activation alert engine."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_catalog (
                    path TEXT PRIMARY KEY,
                    mount_point TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    mtime REAL NOT NULL DEFAULT 0.0,
                    sha256 TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'file',
                    is_executable INTEGER NOT NULL DEFAULT 0,
                    risk_level TEXT NOT NULL DEFAULT 'safe',
                    risk_reason TEXT NOT NULL DEFAULT '',
                    scan_source TEXT NOT NULL DEFAULT 'idle_catalog',
                    last_scanned_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fg_risk ON file_catalog(risk_level)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fg_cat ON file_catalog(category)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_inventory (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_cursor (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    roots_json TEXT NOT NULL DEFAULT '[]',
                    pending_dirs_json TEXT NOT NULL DEFAULT '[]',
                    files_cataloged INTEGER NOT NULL DEFAULT 0,
                    bytes_cataloged INTEGER NOT NULL DEFAULT 0,
                    drives_json TEXT NOT NULL DEFAULT '[]',
                    last_batch_at TEXT NOT NULL DEFAULT '',
                    completed_full_pass INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO scan_cursor
                (id, enabled, roots_json, pending_dirs_json, files_cataloged, bytes_cataloged, drives_json, last_batch_at, completed_full_pass, last_error)
                VALUES (1, 0, '[]', '[]', 0, 0, '[]', '', 0, '')
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guardian_alerts (
                    alert_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    acknowledged INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    @staticmethod
    def discover_scan_roots(*, include_system_readable: bool = True) -> tuple[list[str], list[dict[str, Any]]]:
        """Discovers real mounted drives and user/system roots without looping virtual filesystems."""
        drives: list[dict[str, Any]] = []
        roots: list[str] = []
        seen_roots: set[str] = set()

        try:
            parts = psutil.disk_partitions(all=False)
        except Exception:
            parts = []

        home = Path.home().resolve()
        if home.exists():
            roots.append(str(home))
            seen_roots.add(str(home))

        for p in parts:
            mp = str(getattr(p, "mountpoint", "") or "").strip()
            fstype = str(getattr(p, "fstype", "") or "").lower()
            if not mp:
                continue
            if any(mp == vr or mp.startswith(vr + "/") for vr in _SKIP_VIRTUAL_ROOTS):
                continue
            if fstype in ("tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "squashfs", "overlay"):
                continue
            try:
                du = psutil.disk_usage(mp)
                drives.append(
                    {
                        "device": str(getattr(p, "device", "")),
                        "mountpoint": mp,
                        "fstype": fstype,
                        "total_gb": round(du.total / (1024**3), 2),
                        "free_gb": round(du.free / (1024**3), 2),
                    }
                )
            except Exception:
                drives.append({"device": str(getattr(p, "device", "")), "mountpoint": mp, "fstype": fstype})

            # Add external/secondary drives directly, plus readable system folders on '/'
            if mp not in ("/", "C:\\"):
                if mp not in seen_roots and os.path.isdir(mp):
                    roots.append(mp)
                    seen_roots.add(mp)

        if include_system_readable:
            if platform.system() == "Windows":
                for d in drives:
                    mp = d.get("mountpoint", "")
                    if mp and mp not in seen_roots and os.path.isdir(mp):
                        roots.append(mp)
                        seen_roots.add(mp)
            else:
                for sys_sub in ("/etc", "/usr/local", "/opt", "/var/log"):
                    if os.path.isdir(sys_sub) and sys_sub not in seen_roots:
                        roots.append(sys_sub)
                        seen_roots.add(sys_sub)
                for media_parent in ("/mnt", "/media", f"/run/media/{os.environ.get('USER', '')}"):
                    if os.path.isdir(media_parent):
                        try:
                            for child in Path(media_parent).iterdir():
                                cs = str(child)
                                if child.is_dir() and cs not in seen_roots:
                                    roots.append(cs)
                                    seen_roots.add(cs)
                        except Exception:
                            pass

        return roots, drives

    def capture_system_inventory(self) -> dict[str, Any]:
        """Captures full relevant PC info (OS, CPU, RAM, drives, listening ports, startup units, packages) into DB."""
        roots, drives = self.discover_scan_roots()
        vm = psutil.virtual_memory()
        listening_ports: list[dict[str, Any]] = []
        try:
            for c in psutil.net_connections(kind="inet"):
                if getattr(c, "status", "") == "LISTEN" and c.laddr:
                    listening_ports.append({"ip": c.laddr.ip, "port": int(c.laddr.port), "pid": c.pid})
        except Exception:
            pass

        pkg_info = {"manager": "unknown", "count": 0}
        for mgr, cmd in (
            ("pacman", ["pacman", "-Qq"]),
            ("dpkg", ["dpkg-query", "-W", "-f=${Package}\n"]),
            ("rpm", ["rpm", "-qa"]),
        ):
            if shutil.which(mgr) or (mgr == "dpkg" and shutil.which("dpkg-query")):
                try:
                    out = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
                    if out.returncode == 0:
                        lines = [ln for ln in (out.stdout or "").splitlines() if ln.strip()]
                        pkg_info = {"manager": mgr, "count": len(lines), "sample": lines[:25]}
                        break
                except Exception:
                    pass

        inv = {
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "hostname": platform.node(),
            "cpu_cores_logical": psutil.cpu_count(logical=True) or 1,
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_available_gb": round(vm.available / (1024**3), 2),
            "drives": drives,
            "scan_roots": roots,
            "listening_ports": listening_ports[:60],
            "packages": pkg_info,
            "clamav_installed": bool(shutil.which("clamscan")),
            "captured_at": utc_now_iso(),
        }
        now_iso = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_inventory (key, value_json, updated_at) VALUES (?, ?, ?)",
                ("pc_profile", json.dumps(inv), now_iso),
            )
        return inv

    def verify_file_safety(
        self,
        path: Path,
        *,
        deep_hash: bool = False,
        run_clamav: bool = False,
    ) -> dict[str, Any]:
        """Inspects a file statically (magic/permissions/patterns/conflicts/hash + optional clamscan) without executing it."""
        p = Path(path)
        ext = p.suffix.lower()
        try:
            st = p.lstat()
        except Exception as exc:
            return {
                "path": str(p),
                "size_bytes": 0,
                "mtime": 0.0,
                "sha256": "",
                "category": "unreadable",
                "is_executable": False,
                "risk_level": "warning",
                "risk_reason": f"Unreadable file metadata: {exc}",
            }

        is_exec = bool(
            (st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            or ext in _EXEC_EXTS
        )
        suid = bool(st.st_mode & (stat.S_ISUID | stat.S_ISGID))
        world_writable = bool(st.st_mode & stat.S_IWOTH)

        risk_level = "safe"
        reasons: list[str] = []
        category = "executable" if is_exec else ("startup_unit" if ext in (".desktop", ".service", ".timer") else "file")

        if ext in (".desktop", ".service", ".timer"):
            category = "startup_unit"

        if suid and str(p).startswith(str(Path.home())):
            risk_level = "high_risk"
            reasons.append("SUID/SGID bit set inside user home directory")
        if world_writable and is_exec:
            risk_level = "warning" if risk_level == "safe" else risk_level
            reasons.append("World-writable executable file")

        sha = _file_sha256(p) if (deep_hash or is_exec) else ""

        # Read up to 64KB of script/config text for static payload & conflict inspection
        head_bytes = b""
        try:
            if st.st_size > 0 and not stat.S_ISFIFO(st.st_mode) and not stat.S_ISSOCK(st.st_mode):
                with p.open("rb") as fh:
                    head_bytes = fh.read(65536)
        except Exception:
            head_bytes = b""

        if head_bytes:
            if head_bytes.startswith(b"\x7fELF") and category == "file":
                category = "elf_binary"
                is_exec = True
                if not sha:
                    sha = _file_sha256(p)
            elif head_bytes.startswith(b"MZ") and category == "file":
                category = "pe_binary"
                is_exec = True
                if not sha:
                    sha = _file_sha256(p)
            elif head_bytes.startswith(b"#!") and category == "file":
                category = "script"
                is_exec = True
                if not sha:
                    sha = _file_sha256(p)

            # Check text patterns when likely script/unit/text
            if b"\x00" not in head_bytes[:1024]:
                txt = head_bytes.decode("utf-8", errors="ignore")
                for rx, sev, desc in _SUSPICIOUS_PATTERNS:
                    if rx.search(txt):
                        if sev == "high_risk" or risk_level == "safe":
                            risk_level = sev
                        reasons.append(desc)
                # Startup / autostart target verification
                if ext in (".desktop", ".service"):
                    m_exec = re.search(r"^(?:Exec|ExecStart)\s*=\s*(.+)$", txt, re.M)
                    if m_exec:
                        cmd_val = m_exec.group(1).strip()
                        if any(tmp_dir in cmd_val for tmp_dir in ("/tmp/", "/dev/shm/", "/var/tmp/")):
                            risk_level = "high_risk"
                            reasons.append(f"Startup entry launches from temporary folder: {cmd_val[:90]}")
                        elif "/Downloads/" in cmd_val:
                            if risk_level == "safe":
                                risk_level = "warning"
                            reasons.append(f"Startup entry executes directly from Downloads: {cmd_val[:90]}")

        if run_clamav and is_exec and shutil.which("clamscan"):
            try:
                cres = subprocess.run(["clamscan", "--no-summary", str(p)], capture_output=True, text=True, timeout=15)
                if cres.returncode == 1:
                    risk_level = "high_risk"
                    reasons.append(f"ClamAV flagged file: {(cres.stdout or '').strip()[:140]}")
            except Exception:
                pass

        return {
            "path": str(p),
            "size_bytes": int(st.st_size),
            "mtime": float(st.st_mtime),
            "sha256": sha,
            "category": category,
            "is_executable": is_exec,
            "risk_level": risk_level,
            "risk_reason": "; ".join(reasons),
        }

    def start_full_catalog(self, roots: list[str] | None = None) -> dict[str, Any]:
        """Initializes or resumes a full passive drive & file catalog crawl."""
        disc_roots, drives = self.discover_scan_roots()
        chosen_roots = [str(Path(r).expanduser()) for r in (roots or disc_roots) if os.path.isdir(str(Path(r).expanduser()))]
        if not chosen_roots:
            chosen_roots = [str(Path.home())]
        self.capture_system_inventory()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE scan_cursor
                SET enabled = 1,
                    roots_json = ?,
                    pending_dirs_json = ?,
                    drives_json = ?,
                    completed_full_pass = 0,
                    last_error = ''
                WHERE id = 1
                """,
                (json.dumps(chosen_roots), json.dumps(chosen_roots), json.dumps(drives)),
            )
        return {
            "ok": True,
            "enabled": True,
            "roots": chosen_roots,
            "drives": drives,
        }

    @staticmethod
    def _mount_for_path(path_str: str, drives: list[dict[str, Any]]) -> str:
        best = ""
        for d in drives:
            mp = str(d.get("mountpoint") or "")
            if mp and (path_str == mp or path_str.startswith(mp.rstrip("/") + "/") or mp == "/"):
                if len(mp) > len(best):
                    best = mp
        return best or "/"

    def run_idle_catalog_batch(
        self,
        *,
        cpu_percent: float = 0.0,
        is_gaming: bool = False,
        max_files: int = 250,
        max_seconds: float = 0.75,
        force: bool = False,
    ) -> dict[str, Any]:
        """Passively catalogs pending directories ONLY when the PC is idle (unless force=True)."""
        if not force and (is_gaming or float(cpu_percent) >= 25.0):
            return {"ran": False, "reason": "pc_busy"}

        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT * FROM scan_cursor WHERE id = 1").fetchone()
            if cur is None or (not force and not int(cur["enabled"])):
                return {"ran": False, "reason": "disabled"}
            if int(cur["completed_full_pass"]) and not force:
                return {"ran": False, "reason": "full_pass_complete", "files_cataloged": int(cur["files_cataloged"])}
            pending: list[str] = json.loads(cur["pending_dirs_json"] or "[]")
            roots: list[str] = json.loads(cur["roots_json"] or "[]")
            drives: list[dict[str, Any]] = json.loads(cur["drives_json"] or "[]")
            if force and not pending and not int(cur["completed_full_pass"]):
                if not roots:
                    roots, drives = self.discover_scan_roots()
                pending = list(roots)

        if not pending:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE scan_cursor SET completed_full_pass = 1, last_batch_at = ? WHERE id = 1",
                    (utc_now_iso(),),
                )
            return {"ran": True, "completed_full_pass": True, "processed": 0}

        t0 = time.monotonic()
        rows_to_upsert: list[tuple[Any, ...]] = []
        new_alerts: list[tuple[str, str, str, str, str]] = []
        processed = 0
        bytes_added = 0
        now_iso = utc_now_iso()

        while pending and processed < max_files and (time.monotonic() - t0) < max_seconds:
            cur_dir = pending.pop(0)
            if any(cur_dir == vr or cur_dir.startswith(vr + "/") for vr in _SKIP_VIRTUAL_ROOTS):
                continue
            try:
                with os.scandir(cur_dir) as it:
                    for entry in it:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            p_sub = entry.path
                            if not any(p_sub == vr or p_sub.startswith(vr + "/") for vr in _SKIP_VIRTUAL_ROOTS):
                                pending.append(p_sub)
                            continue
                        if entry.is_file(follow_symlinks=False):
                            info = self.verify_file_safety(Path(entry.path), deep_hash=False)
                            mp = self._mount_for_path(entry.path, drives)
                            rows_to_upsert.append(
                                (
                                    info["path"],
                                    mp,
                                    info["size_bytes"],
                                    info["mtime"],
                                    info["sha256"],
                                    info["category"],
                                    1 if info["is_executable"] else 0,
                                    info["risk_level"],
                                    info["risk_reason"],
                                    "idle_catalog",
                                    now_iso,
                                )
                            )
                            processed += 1
                            bytes_added += int(info["size_bytes"])
                            if info["risk_level"] in ("warning", "high_risk"):
                                new_alerts.append(
                                    (
                                        info["risk_level"],
                                        f"Catalog Flagged {info['category'].upper()}",
                                        info["path"],
                                        info["risk_reason"],
                                    )
                                )
                            if processed >= max_files or (time.monotonic() - t0) >= max_seconds:
                                break
            except PermissionError:
                continue
            except Exception:
                continue

        completed = len(pending) == 0
        with self._lock, self._connect() as conn:
            if rows_to_upsert:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO file_catalog
                    (path, mount_point, size_bytes, mtime, sha256, category, is_executable, risk_level, risk_reason, scan_source, last_scanned_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_upsert,
                )
            for sev, title, tpath, reason in new_alerts[:20]:
                existing = conn.execute(
                    "SELECT alert_id FROM guardian_alerts WHERE target_path = ? AND acknowledged = 0",
                    (tpath,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO guardian_alerts (alert_id, created_at, severity, title, target_path, reason, notified, acknowledged) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                        (f"fga-{uuid.uuid4().hex[:8]}", now_iso, sev, title, tpath, reason),
                    )
            total_cnt = conn.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM file_catalog").fetchone()
            conn.execute(
                """
                UPDATE scan_cursor
                SET pending_dirs_json = ?,
                    files_cataloged = ?,
                    bytes_cataloged = ?,
                    last_batch_at = ?,
                    completed_full_pass = ?
                WHERE id = 1
                """,
                (
                    json.dumps(pending),
                    int(total_cnt[0] if total_cnt else processed),
                    int(total_cnt[1] if total_cnt else bytes_added),
                    now_iso,
                    1 if completed else 0,
                ),
            )

        return {
            "ran": True,
            "processed": processed,
            "pending_dirs": len(pending),
            "completed_full_pass": completed,
            "alerts_raised": len(new_alerts),
        }

    def scan_incoming_and_prelaunch(self, extra_dirs: list[Path] | None = None) -> dict[str, Any]:
        """Scans incoming folders (Downloads/Desktop/tmp) & pre-activation startup locations BEFORE items are turned on."""
        watch_dirs: list[Path] = []
        home = Path.home()
        for rel in ("Downloads", "Desktop", ".config/autostart", ".config/systemd/user"):
            p = home / rel
            if p.is_dir():
                watch_dirs.append(p)
        for ed in (extra_dirs or []):
            ep = Path(ed).expanduser()
            if ep.is_dir() and ep not in watch_dirs:
                watch_dirs.append(ep)

        checked = 0
        new_files = 0
        flagged: list[dict[str, Any]] = []
        now_iso = utc_now_iso()

        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT drives_json FROM scan_cursor WHERE id = 1").fetchone()
            drives = json.loads(cur["drives_json"] or "[]") if cur else []

            for wd in watch_dirs:
                is_startup_zone = wd.name in ("autostart", "user")
                try:
                    entries = list(wd.iterdir())[:250]
                except Exception:
                    continue
                for fpath in entries:
                    if not fpath.is_file() or fpath.is_symlink():
                        continue
                    checked += 1
                    try:
                        st = fpath.stat()
                    except Exception:
                        continue
                    prev = conn.execute(
                        "SELECT mtime, size_bytes, risk_level FROM file_catalog WHERE path = ?",
                        (str(fpath),),
                    ).fetchone()
                    if prev is not None and abs(float(prev["mtime"]) - float(st.st_mtime)) < 0.01 and int(prev["size_bytes"]) == int(st.st_size):
                        continue

                    new_files += 1
                    info = self.verify_file_safety(fpath, deep_hash=True, run_clamav=True)
                    mp = self._mount_for_path(str(fpath), drives)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO file_catalog
                        (path, mount_point, size_bytes, mtime, sha256, category, is_executable, risk_level, risk_reason, scan_source, last_scanned_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            info["path"],
                            mp,
                            info["size_bytes"],
                            info["mtime"],
                            info["sha256"],
                            info["category"],
                            1 if info["is_executable"] else 0,
                            info["risk_level"],
                            info["risk_reason"],
                            "incoming_watch",
                            now_iso,
                        ),
                    )
                    # Pre-activation alert for risky incoming files OR newly dropped startup items/executables
                    should_alert = info["risk_level"] in ("warning", "high_risk") or (
                        is_startup_zone and fpath.suffix.lower() in (".desktop", ".service", ".timer")
                    )
                    if should_alert:
                        sev = info["risk_level"] if info["risk_level"] != "safe" else "warning"
                        title = (
                            "Pre-Activation Startup Warning"
                            if is_startup_zone
                            else f"Incoming File Alert ({sev.upper()})"
                        )
                        reason = info["risk_reason"] or f"New {info['category']} staged at {fpath.name} — verify before enabling/running."
                        existing = conn.execute(
                            "SELECT alert_id FROM guardian_alerts WHERE target_path = ? AND acknowledged = 0",
                            (info["path"],),
                        ).fetchone()
                        if existing is None:
                            conn.execute(
                                "INSERT INTO guardian_alerts (alert_id, created_at, severity, title, target_path, reason, notified, acknowledged) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                                (f"fga-{uuid.uuid4().hex[:8]}", now_iso, sev, title, info["path"], reason),
                            )
                        flagged.append({"path": info["path"], "severity": sev, "title": title, "reason": reason})

        return {
            "ok": True,
            "watched_dirs": [str(w) for w in watch_dirs],
            "checked": checked,
            "new_or_updated": new_files,
            "flagged": flagged,
        }

    def pop_unnotified_alerts(self, limit: int = 5) -> list[dict[str, Any]]:
        """Returns newly raised alerts not yet surfaced to the UI and marks them notified."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT alert_id, created_at, severity, title, target_path, reason FROM guardian_alerts WHERE notified = 0 AND acknowledged = 0 ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            out = [dict(r) for r in rows]
            if out:
                ids = [(r["alert_id"],) for r in out]
                conn.executemany("UPDATE guardian_alerts SET notified = 1 WHERE alert_id = ?", ids)
            return out

    def acknowledge_alerts(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE guardian_alerts SET acknowledged = 1 WHERE acknowledged = 0")
            return int(cur.rowcount or 0)

    def search_catalog(self, query: str = "", *, risk_filter: str = "", limit: int = 40) -> list[dict[str, Any]]:
        q = f"%{(query or '').strip().lower()}%"
        rf = (risk_filter or "").strip().lower()
        with self._lock, self._connect() as conn:
            if rf:
                rows = conn.execute(
                    "SELECT * FROM file_catalog WHERE risk_level = ? AND (LOWER(path) LIKE ? OR LOWER(category) LIKE ? OR LOWER(risk_reason) LIKE ?) ORDER BY last_scanned_at DESC LIMIT ?",
                    (rf, q, q, q, max(1, min(200, int(limit)))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM file_catalog WHERE LOWER(path) LIKE ? OR LOWER(category) LIKE ? OR LOWER(risk_reason) LIKE ? ORDER BY last_scanned_at DESC LIMIT ?",
                    (q, q, q, max(1, min(200, int(limit)))),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_status(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT * FROM scan_cursor WHERE id = 1").fetchone()
            inv_row = conn.execute("SELECT value_json FROM system_inventory WHERE key = 'pc_profile'").fetchone()
            risk_counts = {
                r["risk_level"]: int(r["cnt"])
                for r in conn.execute("SELECT risk_level, COUNT(*) AS cnt FROM file_catalog GROUP BY risk_level").fetchall()
            }
            open_alerts = [
                dict(r)
                for r in conn.execute(
                    "SELECT alert_id, created_at, severity, title, target_path, reason FROM guardian_alerts WHERE acknowledged = 0 ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
            ]
        pending = json.loads(cur["pending_dirs_json"] or "[]") if cur else []
        drives = json.loads(cur["drives_json"] or "[]") if cur else []
        inv = json.loads(inv_row["value_json"]) if inv_row else {}
        return {
            "ok": True,
            "catalog_enabled": bool(cur and int(cur["enabled"])),
            "completed_full_pass": bool(cur and int(cur["completed_full_pass"])),
            "files_cataloged": int(cur["files_cataloged"]) if cur else 0,
            "bytes_cataloged": int(cur["bytes_cataloged"]) if cur else 0,
            "pending_dirs_count": len(pending),
            "last_batch_at": str(cur["last_batch_at"]) if cur else "",
            "drives": drives,
            "risk_counts": risk_counts,
            "open_alerts": open_alerts,
            "system_inventory": inv,
        }
