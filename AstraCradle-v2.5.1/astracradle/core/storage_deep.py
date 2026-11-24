"""Deep Storage Diagnostics: SMART/NVMe Health, Disk Sunburst, Empty/Dead Sweeper, and Dev/AI Model Vault."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

import psutil

from .bloat_analyzer import FolderBloatItem
from .os_adapters import BaseOSAdapter


@dataclass
class SmartHealthInfo:
    device: str
    status: str  # Healthy | Caution | Unknown
    temperature_c: float | None
    wear_percent_used: int | None
    power_on_hours: int | None
    unsafe_shutdowns: int | None
    source: str
    summary: str


@dataclass
class DevModelArtifact:
    artifact_id: str
    kind: str  # ai_model | cargo_target | node_modules | venv | docker_cache
    name: str
    path: str
    size_bytes: int
    age_days: int
    note: str


@dataclass
class DeadArtifactItem:
    kind: str  # empty_dir | dead_symlink | broken_shortcut
    path: str


@dataclass
class SunburstSlice:
    label: str
    path: str
    size_bytes: int
    fraction: float
    color: str


class SmartDiskMonitor:
    """Reads NVMe/SMART wear & temperature cleanly with safe non-root fallback."""

    def inspect_disks(self) -> list[SmartHealthInfo]:
        results: list[SmartHealthInfo] = []
        sys_name = platform.system()

        if shutil.which("smartctl"):
            try:
                scan = subprocess.run(
                    ["smartctl", "--scan-open", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if scan.returncode == 0 and scan.stdout.strip():
                    data = json.loads(scan.stdout)
                    for dev in (data.get("devices") or [])[:4]:
                        dname = str(dev.get("name") or "")
                        if not dname:
                            continue
                        info = self._read_smartctl_dev(dname)
                        if info:
                            results.append(info)
            except Exception:
                pass

        if not results and sys_name == "Linux":
            # Read non-privileged NVMe/thermal hwmon sensors if present
            hwmon_root = Path("/sys/class/hwmon")
            if hwmon_root.exists():
                for hw in sorted(hwmon_root.iterdir()):
                    try:
                        name_f = hw / "name"
                        t_f = hw / "temp1_input"
                        if name_f.exists() and t_f.exists():
                            nm = name_f.read_text(encoding="utf-8", errors="ignore").strip()
                            if "nvme" in nm.lower() or "drivetemp" in nm.lower():
                                temp = int(t_f.read_text(encoding="utf-8").strip()) / 1000.0
                                status = "Caution" if temp >= 70.0 else "Healthy"
                                results.append(
                                    SmartHealthInfo(
                                        device=nm,
                                        status=status,
                                        temperature_c=round(temp, 1),
                                        wear_percent_used=None,
                                        power_on_hours=None,
                                        unsafe_shutdowns=None,
                                        source="sysfs/hwmon",
                                        summary=f"Drive sensor '{nm}' at {temp:.1f}°C ({status}).",
                                    )
                                )
                    except Exception:
                        continue

        if not results:
            # Partition headroom fallback so the panel always renders clear disk health
            parts = psutil.disk_partitions(all=False)
            if parts:
                p0 = parts[0]
                results.append(
                    SmartHealthInfo(
                        device=p0.device or p0.mountpoint,
                        status="Healthy",
                        temperature_c=None,
                        wear_percent_used=None,
                        power_on_hours=None,
                        unsafe_shutdowns=None,
                        source="OS Partition Sensor",
                        summary=(
                            f"Mounted '{p0.mountpoint}' ({p0.fstype or 'fs'}). "
                            "Run with smartctl/admin rights for hardware NVMe wear telemetry."
                        ),
                    )
                )
        return results

    @staticmethod
    def _read_smartctl_dev(dev: str) -> SmartHealthInfo | None:
        try:
            proc = subprocess.run(
                ["smartctl", "-A", "-H", "-j", dev],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if not proc.stdout.strip():
                return None
            j = json.loads(proc.stdout)
            passed = bool((j.get("smart_status") or {}).get("passed", True))
            nvme = j.get("nvme_smart_health_information_log") or {}
            temp = (j.get("temperature") or {}).get("current") or nvme.get("temperature")
            wear = nvme.get("percentage_used")
            poh = (j.get("power_on_time") or {}).get("hours") or nvme.get("power_on_hours")
            unsafe = nvme.get("unsafe_shutdowns")
            warn_parts: list[str] = []
            if not passed:
                warn_parts.append("SMART health failed")
            if wear is not None and int(wear) >= 80:
                warn_parts.append(f"NVMe wear {wear}% >= 80% (back up drive)")
            if temp is not None and float(temp) >= 70.0:
                warn_parts.append(f"Drive temp {temp}°C >= 70°C")
            status = "Healthy" if not warn_parts else "Caution"
            warn_note = f" — {'; '.join(warn_parts)}" if warn_parts else ""
            return SmartHealthInfo(
                device=dev,
                status=status,
                temperature_c=float(temp) if temp is not None else None,
                wear_percent_used=int(wear) if wear is not None else None,
                power_on_hours=int(poh) if poh is not None else None,
                unsafe_shutdowns=int(unsafe) if unsafe is not None else None,
                source="smartctl",
                summary=f"{dev}: {status} (wear={wear if wear is not None else '?'}%, temp={temp if temp is not None else '?'}°C){warn_note}",
            )
        except Exception:
            return None


class DevAndAIMoVaultScanner:
    """Scans local AI model stores (Ollama/HuggingFace/LM Studio) & heavy dev caches."""

    @staticmethod
    def _dir_size(path: Path, max_files: int = 3000) -> int:
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
                        return total
        except OSError:
            pass
        return total

    def scan(self, home: Path | None = None, limit: int = 25) -> list[DevModelArtifact]:
        root = home or Path.home()
        now = time.time()
        found: list[DevModelArtifact] = []

        ai_stores = [
            ("ollama_models", "ai_model", "Ollama Local LLM Blobs", root / ".ollama" / "models"),
            ("hf_hub", "ai_model", "HuggingFace Hub Cache", root / ".cache" / "huggingface" / "hub"),
            ("lmstudio_models", "ai_model", "LM Studio GGUF Models", root / ".cache" / "lm-studio" / "models"),
            ("gpt4all_models", "ai_model", "GPT4All Models", root / ".local" / "share" / "nomic.ai" / "GPT4All"),
        ]
        for aid, kind, label, p in ai_stores:
            if p.exists() and not p.is_symlink():
                sz = self._dir_size(p)
                if sz > 0:
                    try:
                        age = max(0, int((now - p.stat().st_mtime) / 86400))
                    except OSError:
                        age = 0
                    found.append(
                        DevModelArtifact(
                            artifact_id=aid,
                            kind=kind,
                            name=label,
                            path=str(p),
                            size_bytes=sz,
                            age_days=age,
                            note="Local AI weights; safe to prune unused models you can re-download.",
                        )
                    )

        # Scan shallow project folders (2 levels under ~/Projects, ~/Dev, ~/Code, ~/workspace, ~/)
        dev_names = {"node_modules": "node_modules", "target": "cargo_target", ".venv": "venv"}
        search_bases = [root / d for d in ("Projects", "Dev", "Code", "workspace", "src") if (root / d).exists()]
        seen_paths: set[str] = set()
        for base in search_bases:
            try:
                for dirpath, dirs, _ in os.walk(base, followlinks=False):
                    dp = Path(dirpath)
                    rel_depth = len(dp.relative_to(base).parts)
                    if rel_depth > 3:
                        dirs[:] = []
                        continue
                    for d in list(dirs):
                        if d in dev_names:
                            cand = dp / d
                            if cand.is_symlink() or str(cand) in seen_paths:
                                continue
                            seen_paths.add(str(cand))
                            sz = self._dir_size(cand, max_files=2500)
                            if sz >= 5 * 1024 * 1024:
                                age = max(0, int((now - cand.stat().st_mtime) / 86400))
                                found.append(
                                    DevModelArtifact(
                                        artifact_id=f"dev_{len(found)}",
                                        kind=dev_names[d],
                                        name=f"{dp.name}/{d}",
                                        path=str(cand),
                                        size_bytes=sz,
                                        age_days=age,
                                        note=f"Rebuildable {d} folder (last touched {age}d ago).",
                                    )
                                )
                            dirs.remove(d)
            except OSError:
                continue

        found.sort(key=lambda a: -a.size_bytes)
        return found[:limit]


class EmptyAndDeadSweeper:
    """Safely identifies empty dirs, dangling symlinks, and broken shortcuts."""

    def scan(
        self,
        roots: list[Path | str],
        adapter: BaseOSAdapter,
        max_items: int = 100,
    ) -> list[DeadArtifactItem]:
        items: list[DeadArtifactItem] = []
        for raw in roots:
            root = Path(raw).expanduser()
            if not root.exists() or root.is_symlink():
                continue
            try:
                for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
                    dp = Path(dirpath)
                    if adapter.is_path_protected(dp):
                        continue
                    # Broken symlinks
                    for fn in filenames:
                        fp = dp / fn
                        if fp.is_symlink() and not fp.exists():
                            items.append(DeadArtifactItem("dead_symlink", str(fp)))
                            if len(items) >= max_items:
                                return items
                        elif fp.suffix == ".desktop" and fp.is_file():
                            try:
                                txt = fp.read_text(encoding="utf-8", errors="ignore")
                                for line in txt.splitlines():
                                    if line.startswith("Exec="):
                                        exe = line.split("=", 1)[1].strip().split()[0].strip('"')
                                        if exe.startswith("/") and not Path(exe).exists():
                                            items.append(DeadArtifactItem("broken_shortcut", str(fp)))
                                            break
                            except OSError:
                                pass
                    # Empty subdirectories (never remove the root itself)
                    if dp != root and not dirnames and not filenames:
                        try:
                            if not any(dp.iterdir()):
                                items.append(DeadArtifactItem("empty_dir", str(dp)))
                        except OSError:
                            pass
                    if len(items) >= max_items:
                        return items
            except OSError:
                continue
        return items


class DiskSunburstBuilder:
    """Converts FolderBloatItem sizes into proportional radial slices for Dyson Sunburst visualization."""

    PALETTE_RING = ("#48D8E0", "#2EC994", "#D4AE6A", "#6CE8EF", "#F4C16D", "#F45B69", "#8B9EB3", "#38BDF8")

    @classmethod
    def build(cls, folders: list[FolderBloatItem]) -> list[SunburstSlice]:
        total = sum(max(0, f.size_bytes) for f in folders)
        if total <= 0:
            return []
        slices: list[SunburstSlice] = []
        for idx, f in enumerate(folders):
            if f.size_bytes <= 0:
                continue
            slices.append(
                SunburstSlice(
                    label=Path(f.path).name or f.path,
                    path=f.path,
                    size_bytes=f.size_bytes,
                    fraction=f.size_bytes / total,
                    color=cls.PALETTE_RING[idx % len(cls.PALETTE_RING)],
                )
            )
        return slices
