"""Consent-gated Installed App Usage, SHA-256 Duplicate File & Disk Bloat Analyzer."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import re
import subprocess
import time

from .config import AppConfig
from .os_adapters import BaseOSAdapter


@dataclass
class InstalledAppInfo:
    name: str
    version: str
    estimated_mb: float
    last_used_hint: str
    source: str
    unused_candidate: bool = False


@dataclass
class DuplicateGroup:
    sha256: str
    file_size_bytes: int
    keep_path: str
    duplicate_paths: list[str]
    reclaimable_bytes: int


@dataclass
class FolderBloatItem:
    path: str
    size_bytes: int
    file_count: int


class BloatAnalyzer:
    """Strictly local analyzer for unused installed programs, SHA-256 duplicates, and folder bloat."""

    @staticmethod
    def _dir_size_limited(path: Path, max_files: int = 3000) -> tuple[int, int]:
        total = 0
        count = 0
        try:
            for root, _, files in os.walk(path, followlinks=False):
                if Path(root).is_symlink():
                    continue
                for fn in files:
                    fp = Path(root) / fn
                    if fp.is_symlink():
                        continue
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        pass
                    count += 1
                    if count >= max_files:
                        return total, count
        except OSError:
            pass
        return total, count

    def scan_installed_apps(self, config: AppConfig, limit: int = 40) -> list[InstalledAppInfo]:
        if not config.consent_installed_apps:
            return []

        sys_name = platform.system()
        apps: list[InstalledAppInfo] = []

        if sys_name == "Windows":
            apps.extend(self._scan_windows_registry_apps(limit))
        elif sys_name == "Darwin":
            apps.extend(self._scan_macos_applications(limit))
        else:
            apps.extend(self._scan_linux_applications(limit))

        apps.sort(key=lambda a: (not a.unused_candidate, -a.estimated_mb, a.name.lower()))
        return apps[:limit]

    def _scan_windows_registry_apps(self, limit: int) -> list[InstalledAppInfo]:
        out: list[InstalledAppInfo] = []
        try:
            import winreg  # type: ignore

            roots = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            seen: set[str] = set()
            for hive, subkey in roots:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        num = winreg.QueryInfoKey(key)[0]
                        for idx in range(min(num, 250)):
                            try:
                                name_k = winreg.EnumKey(key, idx)
                                with winreg.OpenKey(key, name_k) as sub:
                                    disp = str(winreg.QueryValueEx(sub, "DisplayName")[0]).strip()
                                    if not disp or disp.lower() in seen:
                                        continue
                                    seen.add(disp.lower())
                                    try:
                                        ver = str(winreg.QueryValueEx(sub, "DisplayVersion")[0])
                                    except OSError:
                                        ver = "—"
                                    try:
                                        kb = int(winreg.QueryValueEx(sub, "EstimatedSize")[0])
                                        mb = round(kb / 1024.0, 1)
                                    except OSError:
                                        mb = 0.0
                                    try:
                                        inst = str(winreg.QueryValueEx(sub, "InstallDate")[0])
                                    except OSError:
                                        inst = "Unknown"
                                    out.append(
                                        InstalledAppInfo(
                                            name=disp,
                                            version=ver,
                                            estimated_mb=mb,
                                            last_used_hint=f"Installed: {inst}",
                                            source="Windows Uninstall Registry",
                                            unused_candidate=mb >= 250.0,
                                        )
                                    )
                            except OSError:
                                continue
                except OSError:
                    continue
        except Exception:
            pass
        return out[:limit]

    def _scan_macos_applications(self, limit: int) -> list[InstalledAppInfo]:
        out: list[InstalledAppInfo] = []
        now = time.time()
        apps_dir = Path("/Applications")
        if not apps_dir.exists():
            return out
        try:
            for bundle in sorted(apps_dir.glob("*.app"))[:limit]:
                try:
                    st = bundle.stat()
                    age_days = max(0, int((now - st.st_mtime) / 86400))
                    sz, _ = self._dir_size_limited(bundle, max_files=800)
                    mb = round(sz / (1024 * 1024), 1)
                    out.append(
                        InstalledAppInfo(
                            name=bundle.stem,
                            version="bundle",
                            estimated_mb=mb,
                            last_used_hint=f"Modified {age_days}d ago",
                            source="/Applications",
                            unused_candidate=age_days >= 60 and mb >= 100.0,
                        )
                    )
                except OSError:
                    continue
        except OSError:
            pass
        return out

    def _scan_linux_applications(self, limit: int) -> list[InstalledAppInfo]:
        out: list[InstalledAppInfo] = []
        # Try dpkg-query on Debian/Ubuntu for real footprint + package version
        try:
            proc = subprocess.run(
                ["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Installed-Size}\n"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if proc.returncode == 0 and proc.stdout:
                rows: list[tuple[float, str, str]] = []
                for line in proc.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) == 3:
                        pkg, ver, kb_s = parts
                        try:
                            mb = round(int(kb_s) / 1024.0, 1)
                        except ValueError:
                            mb = 0.0
                        if mb >= 40.0 and not pkg.startswith(("lib", "linux-")):
                            rows.append((mb, pkg, ver))
                rows.sort(reverse=True)
                for mb, pkg, ver in rows[:limit]:
                    out.append(
                        InstalledAppInfo(
                            name=pkg,
                            version=ver[:24],
                            estimated_mb=mb,
                            last_used_hint="Package footprint review",
                            source="dpkg-query",
                            unused_candidate=mb >= 200.0,
                        )
                    )
                if out:
                    return out
        except Exception:
            pass

        # Fallback: inspect user & system .desktop launchers
        seen: set[str] = set()
        for desktop_dir in (Path.home() / ".local/share/applications", Path("/usr/share/applications")):
            if not desktop_dir.exists():
                continue
            for df in sorted(desktop_dir.glob("*.desktop"))[:80]:
                try:
                    txt = df.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r"^Name=(.+)$", txt, re.MULTILINE)
                    app_name = m.group(1).strip() if m else df.stem
                    if app_name.lower() in seen:
                        continue
                    seen.add(app_name.lower())
                    age_days = max(0, int((time.time() - df.stat().st_mtime) / 86400))
                    out.append(
                        InstalledAppInfo(
                            name=app_name,
                            version="desktop",
                            estimated_mb=0.0,
                            last_used_hint=f"Launcher updated {age_days}d ago",
                            source=str(desktop_dir),
                            unused_candidate=age_days >= 90,
                        )
                    )
                except OSError:
                    continue
        return out[:limit]

    @staticmethod
    def _file_hash(path: Path, max_bytes: int | None = None) -> str:
        h = hashlib.sha256()
        read_total = 0
        with path.open("rb") as fh:
            while True:
                to_read = 65536 if max_bytes is None else min(65536, max_bytes - read_total)
                if to_read <= 0:
                    break
                chunk = fh.read(to_read)
                if not chunk:
                    break
                h.update(chunk)
                read_total += len(chunk)
        return h.hexdigest()

    def find_duplicates(
        self,
        roots: list[Path | str],
        adapter: BaseOSAdapter,
        min_bytes: int = 1024,
        max_files: int = 15000,
        allow_protected_scan: bool = False,
    ) -> list[DuplicateGroup]:
        """Two-phase SHA-256 duplicate finder (size -> 4KB header -> full SHA-256)."""
        by_size: dict[int, list[Path]] = defaultdict(list)
        scanned = 0

        for raw_root in roots:
            root = Path(raw_root).expanduser()
            if not root.exists() or root.is_symlink():
                continue
            if not allow_protected_scan and adapter.is_path_protected(root):
                continue
            for dirpath, _, filenames in os.walk(root, followlinks=False):
                dp = Path(dirpath)
                if dp.is_symlink():
                    continue
                for fn in filenames:
                    fp = dp / fn
                    if fp.is_symlink():
                        continue
                    try:
                        sz = fp.stat().st_size
                    except OSError:
                        continue
                    if sz >= min_bytes:
                        by_size[sz].append(fp)
                    scanned += 1
                    if scanned >= max_files:
                        break
                if scanned >= max_files:
                    break

        # Phase 1: 4KB prefix hash for size collisions
        by_prefix: dict[tuple[int, str], list[Path]] = defaultdict(list)
        for sz, paths in by_size.items():
            if len(paths) < 2:
                continue
            for p in paths:
                try:
                    pre = self._file_hash(p, max_bytes=4096)
                    by_prefix[(sz, pre)].append(p)
                except OSError:
                    continue

        # Phase 2: full SHA-256 for prefix collisions
        by_full: dict[tuple[int, str], list[Path]] = defaultdict(list)
        for (sz, _), paths in by_prefix.items():
            if len(paths) < 2:
                continue
            for p in paths:
                try:
                    full = self._file_hash(p, max_bytes=None)
                    by_full[(sz, full)].append(p)
                except OSError:
                    continue

        groups: list[DuplicateGroup] = []
        for (sz, digest), paths in by_full.items():
            if len(paths) < 2:
                continue
            # Keep oldest/shortest path as canonical original
            ordered = sorted(paths, key=lambda p: (len(str(p)), str(p)))
            keep = ordered[0]
            dups = [str(p) for p in ordered[1:]]
            groups.append(
                DuplicateGroup(
                    sha256=digest,
                    file_size_bytes=sz,
                    keep_path=str(keep),
                    duplicate_paths=dups,
                    reclaimable_bytes=sz * len(dups),
                )
            )
        groups.sort(key=lambda g: -g.reclaimable_bytes)
        return groups

    def analyze_folder_bloat(self, root: Path | str | None = None, top_n: int = 8) -> list[FolderBloatItem]:
        base = Path(root).expanduser() if root else Path.home()
        if not base.exists() or base.is_symlink():
            return []
        items: list[FolderBloatItem] = []
        try:
            for entry in base.iterdir():
                if entry.is_symlink() or not entry.is_dir():
                    continue
                sz, cnt = self._dir_size_limited(entry, max_files=2500)
                if sz > 0:
                    items.append(FolderBloatItem(path=str(entry), size_bytes=sz, file_count=cnt))
        except OSError:
            pass
        items.sort(key=lambda i: -i.size_bytes)
        return items[:top_n]
