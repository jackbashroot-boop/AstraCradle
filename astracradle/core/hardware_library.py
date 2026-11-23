"""Hardware Component Library, PC Spec Passport & Savable Priority Presets."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any

import psutil

from .config import AppConfig, ConfigManager
from .journal import ForensicJournal
from .models import utc_now_iso


@dataclass
class HardwarePassport:
    os_platform: str
    hostname: str
    cpu_model: str
    cpu_physical_cores: int
    cpu_logical_cores: int
    ram_total_gb: float
    gpu_summary: str
    storage_summary: str
    detected_cpu_profile_id: str
    detected_gpu_profile_id: str
    detected_storage_id: str
    detected_ram_tier_id: str

    def to_clipboard_summary(self) -> str:
        return (
            f"AstraCradle PC Spec Passport\n"
            f"OS: {self.os_platform} ({self.hostname})\n"
            f"CPU: {self.cpu_model} ({self.cpu_physical_cores}C/{self.cpu_logical_cores}T)\n"
            f"RAM: {self.ram_total_gb:.1f} GB\n"
            f"GPU: {self.gpu_summary}\n"
            f"Storage: {self.storage_summary}"
        )


@dataclass
class PriorityPreset:
    preset_id: str
    name: str
    description: str
    cpu_profile_id: str
    gpu_profile_id: str
    storage_id: str
    ram_tier_id: str
    foreground_nice: int = -5  # -10..0 (best-effort without root if negative falls back gracefully)
    background_nice: int = 10  # 0..19
    background_io_class: str = "idle"  # idle | best_effort | normal
    pin_background_to_efficiency_cores: bool = True
    efficiency_core_fraction: float = 0.5  # upper fraction of logical CPUs
    hibernate_idle_ai: bool = True
    built_in: bool = True
    updated_at: str = field(default_factory=utc_now_iso)


def default_priority_presets() -> list[PriorityPreset]:
    return [
        PriorityPreset(
            preset_id="preset_intel_hybrid_gaming",
            name="Intel Hybrid P-Core Gaming",
            description="Pins noisy background helpers & idle AI onto upper E-cores with Idle I/O priority while freeing P-cores.",
            cpu_profile_id="intel_arrow_lake_200s",
            gpu_profile_id="nvidia_rtx_5080_4080_4070",
            storage_id="nvme_gen4_gen5",
            ram_tier_id="ram_32gb",
            foreground_nice=-4,
            background_nice=12,
            background_io_class="idle",
            pin_background_to_efficiency_cores=True,
            efficiency_core_fraction=0.5,
            hibernate_idle_ai=True,
            built_in=True,
        ),
        PriorityPreset(
            preset_id="preset_amd_dual_ccd_x3d",
            name="AMD Dual-CCD X3D (CCD0 Focus)",
            description="Offloads background helpers to CCD1 (upper half of logical CPUs) so games keep CCD0 3D V-Cache clean.",
            cpu_profile_id="amd_ryzen_dual_ccd_x3d",
            gpu_profile_id="amd_rx_9070_7900",
            storage_id="nvme_gen4_gen5",
            ram_tier_id="ram_32gb",
            foreground_nice=-4,
            background_nice=12,
            background_io_class="idle",
            pin_background_to_efficiency_cores=True,
            efficiency_core_fraction=0.5,
            hibernate_idle_ai=True,
            built_in=True,
        ),
        PriorityPreset(
            preset_id="preset_ai_creator_workstation",
            name="AI & 3D Creator Workstation",
            description="Balances heavy multi-core throughput, keeps local AI models alive when RAM >=32GB, and sets best-effort I/O.",
            cpu_profile_id="amd_ryzen_single_x3d_zen5",
            gpu_profile_id="nvidia_rtx_5090_4090",
            storage_id="nvme_gen4_gen5",
            ram_tier_id="ram_64gb_plus",
            foreground_nice=0,
            background_nice=6,
            background_io_class="best_effort",
            pin_background_to_efficiency_cores=False,
            efficiency_core_fraction=0.4,
            hibernate_idle_ai=False,
            built_in=True,
        ),
        PriorityPreset(
            preset_id="preset_laptop_quiet",
            name="Laptop Battery & Tight RAM Saver",
            description="Aggressively quiets background updaters & idle AI models for cool, quiet endurance on 8–16GB systems.",
            cpu_profile_id="mobile_laptop_efficiency",
            gpu_profile_id="integrated_igpu",
            storage_id="sata_ssd",
            ram_tier_id="ram_16gb",
            foreground_nice=0,
            background_nice=15,
            background_io_class="idle",
            pin_background_to_efficiency_cores=True,
            efficiency_core_fraction=0.5,
            hibernate_idle_ai=True,
            built_in=True,
        ),
    ]


class HardwareLibraryManager:
    """Loads component specs, detects local hardware passport, and manages savable Priority Presets."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal, data_dir: Path | None = None) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.data_dir = data_dir or (Path(__file__).resolve().parents[2] / "data")
        self.library_path = self.data_dir / "component_library.json"
        self.presets_path = cfg_mgr.state_dir / "presets.json"

    def load_component_library(self) -> dict[str, Any]:
        if self.library_path.exists():
            try:
                raw = json.loads(self.library_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception:
                pass
        return {"version": "fallback", "cpus": [], "gpus": [], "storage": [], "ram_tiers": []}

    def detect_passport(self) -> HardwarePassport:
        sys_name = platform.system()
        cpu_name = platform.processor() or platform.machine()
        if sys_name == "Linux" and Path("/proc/cpuinfo").exists():
            try:
                for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.lower().startswith("model name"):
                        cpu_name = line.split(":", 1)[1].strip()
                        break
            except OSError:
                pass
        elif sys_name == "Darwin":
            try:
                p = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=2)
                if p.returncode == 0 and p.stdout.strip():
                    cpu_name = p.stdout.strip()
            except Exception:
                pass

        phys = psutil.cpu_count(logical=False) or 1
        logi = psutil.cpu_count(logical=True) or phys
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)

        gpu_txt = "Integrated / Standard Display Adapter"
        try:
            nv = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if nv.returncode == 0 and nv.stdout.strip():
                gpu_txt = nv.stdout.strip().splitlines()[0]
        except Exception:
            pass

        parts = psutil.disk_partitions(all=False)
        stor_txt = f"{parts[0].device} ({parts[0].fstype})" if parts else "Local Disk"

        # Match component library IDs heuristically
        low_cpu = cpu_name.lower()
        if "core ultra" in low_cpu or "arrow" in low_cpu:
            cpu_id = "intel_arrow_lake_200s"
        elif "intel" in low_cpu:
            cpu_id = "intel_raptor_alder_hybrid"
        elif "x3d" in low_cpu and ("7950" in low_cpu or "7900" in low_cpu or "9950" in low_cpu or "9900" in low_cpu):
            cpu_id = "amd_ryzen_dual_ccd_x3d"
        elif "ryzen" in low_cpu or "amd" in low_cpu:
            cpu_id = "amd_ryzen_single_x3d_zen5"
        elif sys_name == "Darwin" and ("arm" in platform.machine().lower() or "apple" in low_cpu):
            cpu_id = "apple_silicon_m_series"
        else:
            cpu_id = "mobile_laptop_efficiency"

        low_gpu = gpu_txt.lower()
        if "5090" in low_gpu or "4090" in low_gpu:
            gpu_id = "nvidia_rtx_5090_4090"
        elif "rtx" in low_gpu or "nvidia" in low_gpu:
            gpu_id = "nvidia_rtx_5080_4080_4070"
        elif "radeon" in low_gpu or "rx " in low_gpu:
            gpu_id = "amd_rx_9070_7900"
        elif "arc" in low_gpu:
            gpu_id = "intel_arc_midrange"
        else:
            gpu_id = "integrated_igpu"

        stor_id = "nvme_gen4_gen5" if ("nvme" in stor_txt.lower() or sys_name == "Darwin") else "sata_ssd"
        if ram_gb >= 48:
            ram_id = "ram_64gb_plus"
        elif ram_gb >= 24:
            ram_id = "ram_32gb"
        elif ram_gb >= 12:
            ram_id = "ram_16gb"
        else:
            ram_id = "ram_8gb"

        return HardwarePassport(
            os_platform=platform.platform(),
            hostname=platform.node(),
            cpu_model=cpu_name,
            cpu_physical_cores=phys,
            cpu_logical_cores=logi,
            ram_total_gb=ram_gb,
            gpu_summary=gpu_txt,
            storage_summary=stor_txt,
            detected_cpu_profile_id=cpu_id,
            detected_gpu_profile_id=gpu_id,
            detected_storage_id=stor_id,
            detected_ram_tier_id=ram_id,
        )

    def load_presets(self) -> list[PriorityPreset]:
        if not self.presets_path.exists():
            presets = default_priority_presets()
            self.save_presets(presets)
            return presets
        try:
            raw = json.loads(self.presets_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("presets.json is not a list")
            valid = {f.name for f in PriorityPreset.__dataclass_fields__.values()}
            req = {"preset_id", "name", "cpu_profile_id"}
            out = []
            for item in raw:
                if isinstance(item, dict) and req.issubset(item.keys()):
                    try:
                        out.append(PriorityPreset(**{k: v for k, v in item.items() if k in valid}))
                    except Exception:
                        continue
            return out or default_priority_presets()
        except Exception:
            presets = default_priority_presets()
            self.save_presets(presets)
            return presets

    def save_presets(self, presets: list[PriorityPreset]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.presets_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(p) for p in presets], indent=2), encoding="utf-8")
        os.replace(tmp, self.presets_path)

    def save_custom_preset(self, preset: PriorityPreset) -> PriorityPreset:
        presets = [p for p in self.load_presets() if p.preset_id != preset.preset_id]
        preset.built_in = False
        preset.updated_at = utc_now_iso()
        presets.append(preset)
        self.save_presets(presets)
        self.journal.log_event(
            "HardwarePresets",
            "save_preset",
            f"Saved custom component & priority preset '{preset.name}'",
            "User saved customized CPU/GPU/Storage/I/O preset",
            "ok",
        )
        return preset

    def delete_preset(self, preset_id: str) -> tuple[bool, str]:
        presets = self.load_presets()
        remaining = [p for p in presets if p.preset_id != preset_id or p.built_in]
        if len(remaining) == len(presets):
            return False, "Built-in presets cannot be deleted (or preset not found)."
        self.save_presets(remaining)
        return True, f"Deleted custom preset '{preset_id}'."

    def apply_preset(
        self,
        preset: PriorityPreset,
        config: AppConfig,
        dry_run: bool = False,
    ) -> tuple[int, str]:
        """Applies CPU affinity + I/O priority + nice rules to non-protected background processes."""
        logi_cores = psutil.cpu_count(logical=True) or 1
        eff_count = max(1, int(round(logi_cores * max(0.1, min(0.9, preset.efficiency_core_fraction)))))
        eff_cpus = list(range(max(0, logi_cores - eff_count), logi_cores))
        protected = {x.lower() for x in config.excluded_processes}
        own_pid = os.getpid()
        tuned = 0

        for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
            try:
                pid = int(proc.info["pid"])
                name = str(proc.info.get("name") or "")
                if pid <= 4 or pid == own_pid or name.lower() in protected:
                    continue
                cpu_p = float(proc.info.get("cpu_percent") or 0.0)
                if cpu_p >= 20.0:
                    continue  # Never throttle an actively busy foreground worker
                if not dry_run:
                    if preset.pin_background_to_efficiency_cores and hasattr(proc, "cpu_affinity") and logi_cores > 1:
                        try:
                            proc.cpu_affinity(eff_cpus)
                        except Exception:
                            pass
                    if hasattr(proc, "ionice"):
                        try:
                            if preset.background_io_class == "idle" and hasattr(psutil, "IOPRIO_CLASS_IDLE"):
                                proc.ionice(psutil.IOPRIO_CLASS_IDLE)
                            elif preset.background_io_class == "best_effort" and hasattr(psutil, "IOPRIO_CLASS_BE"):
                                proc.ionice(psutil.IOPRIO_CLASS_BE, 6)
                        except Exception:
                            pass
                    try:
                        if os.name != "nt":
                            proc.nice(max(0, min(19, int(preset.background_nice))))
                    except Exception:
                        pass
                tuned += 1
                if tuned >= 25:
                    break
            except Exception:
                continue

        msg = (
            f"{'[DRY RUN] ' if dry_run else ''}Applied preset '{preset.name}' to {tuned} background process(es) "
            f"(I/O={preset.background_io_class}, E-core mask={eff_cpus if preset.pin_background_to_efficiency_cores else 'all'})."
        )
        self.journal.log_event(
            "HardwarePresets",
            "apply_preset",
            msg,
            f"Align CPU affinity, I/O class, and nice values with hardware preset '{preset.name}'",
            "ok",
        )
        return tuned, msg
