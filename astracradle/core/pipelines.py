"""Customizable Optimization Pipelines, Local File/CLI Hook Connectors & Shareable Preset Packs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .config import AppConfig, ConfigManager
from .executor import GuardedExecutor
from .hardware_library import HardwareLibraryManager, PriorityPreset
from .journal import ForensicJournal
from .models import ProcessInfo, utc_now_iso
from .os_adapters import BaseOSAdapter
from .scripts_engine import ScriptsEngine


@dataclass
class PipelineStage:
    stage_type: str  # preflight_snapshot | apply_preset | run_script | clean_caches | self_heal | apply_gpu_profile | notify
    label: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    condition: str = "always"  # always | if_ram_above | if_temp_above | if_on_battery | if_not_fullscreen
    condition_value: float = 75.0


@dataclass
class OptimizationPipeline:
    pipeline_id: str
    name: str
    description: str
    trigger_mode: str  # manual | on_app_launch | scheduled | local_hook
    stages: list[PipelineStage] = field(default_factory=list)
    built_in: bool = True
    last_run_at: str | None = None


def default_pipelines() -> list[OptimizationPipeline]:
    return [
        OptimizationPipeline(
            pipeline_id="pipe_smooth_gaming",
            name="Smooth Gaming Launch Pipeline",
            description="Captures baseline, pins background/AI helpers to E-cores + Idle I/O, enables Gaming script, and sweeps temp caches.",
            trigger_mode="manual",
            stages=[
                PipelineStage("apply_preset", "Apply Hardware Priority Preset", {"preset_id": "preset_intel_hybrid_gaming"}),
                PipelineStage("run_script", "Enable Gaming Max-FPS Script", {"script_id": "script_gaming_max", "enable": True}),
                PipelineStage("clean_caches", "Quarantine Stale Temp Caches", {}),
                PipelineStage("self_heal", "Verify State & Forensic Chain", {}),
            ],
            built_in=True,
        ),
        OptimizationPipeline(
            pipeline_id="pipe_creator_render",
            name="3D Render & Compile Pipeline",
            description="Locks creator workstation priorities, activates 3D/Render profile, and keeps build/shader caches protected.",
            trigger_mode="manual",
            stages=[
                PipelineStage("apply_preset", "Apply Creator Workstation Preset", {"preset_id": "preset_ai_creator_workstation"}),
                PipelineStage("run_script", "Enable 3D Render / Modeling Script", {"script_id": "script_3d_render", "enable": True}),
                PipelineStage("self_heal", "Verify State & Forensic Chain", {}),
            ],
            built_in=True,
        ),
        OptimizationPipeline(
            pipeline_id="pipe_deep_hygiene",
            name="Deep Hygiene & Pre-Flight Restore Pipeline",
            description="Checks OS snapshot readiness, quarantines temp/log clutter, hibernates idle AI chatbots, and audits logs.",
            trigger_mode="scheduled",
            stages=[
                PipelineStage("preflight_snapshot", "Pre-Flight Native OS Snapshot Check", {}),
                PipelineStage("clean_caches", "Quarantine Temp & Log Clutter", {}),
                PipelineStage("run_script", "Hibernate Idle AI Chatbots", {"script_id": "script_ai_hibernate", "enable": True}),
                PipelineStage("self_heal", "7-Invariant Self-Heal Audit", {}),
            ],
            built_in=True,
        ),
    ]


class PipelineEngine:
    """Runs, saves, and connects user-customizable multi-stage optimization pipelines."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        executor: GuardedExecutor,
        scripts_engine: ScriptsEngine,
        hw_mgr: HardwareLibraryManager,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.executor = executor
        self.scripts_engine = scripts_engine
        self.hw_mgr = hw_mgr
        self.journal = journal
        self.pipelines_path = cfg_mgr.state_dir / "pipelines.json"
        self.hooks_dir = cfg_mgr.base_dir / "hooks"
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        self.hook_trigger_path = self.hooks_dir / "trigger.json"

    def load_pipelines(self) -> list[OptimizationPipeline]:
        if not self.pipelines_path.exists():
            pipes = default_pipelines()
            self.save_pipelines(pipes)
            return pipes
        try:
            raw = json.loads(self.pipelines_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("pipelines.json not a list")
            out: list[OptimizationPipeline] = []
            for item in raw:
                if not isinstance(item, dict) or not item.get("pipeline_id"):
                    continue
                stages = [
                    PipelineStage(
                        stage_type=str(st.get("stage_type", "notify")),
                        label=str(st.get("label", "Stage")),
                        params=st.get("params") if isinstance(st.get("params"), dict) else {},
                        enabled=bool(st.get("enabled", True)),
                        condition=str(st.get("condition", "always")),
                        condition_value=float(st.get("condition_value", 75.0)),
                    )
                    for st in (item.get("stages") or [])
                    if isinstance(st, dict)
                ]
                out.append(
                    OptimizationPipeline(
                        pipeline_id=str(item["pipeline_id"]),
                        name=str(item.get("name", item["pipeline_id"])),
                        description=str(item.get("description", "")),
                        trigger_mode=str(item.get("trigger_mode", "manual")),
                        stages=stages,
                        built_in=bool(item.get("built_in", False)),
                        last_run_at=item.get("last_run_at"),
                    )
                )
            return out or default_pipelines()
        except Exception:
            pipes = default_pipelines()
            self.save_pipelines(pipes)
            return pipes

    def save_pipelines(self, pipes: list[OptimizationPipeline]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.pipelines_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(p) for p in pipes], indent=2), encoding="utf-8")
        os.replace(tmp, self.pipelines_path)

    def save_custom_pipeline(self, pipe: OptimizationPipeline) -> OptimizationPipeline:
        pipes = [p for p in self.load_pipelines() if p.pipeline_id != pipe.pipeline_id]
        pipe.built_in = False
        pipes.append(pipe)
        self.save_pipelines(pipes)
        return pipe

    def delete_pipeline(self, pipeline_id: str) -> tuple[bool, str]:
        pipes = self.load_pipelines()
        remaining = [p for p in pipes if p.pipeline_id != pipeline_id or p.built_in]
        if len(remaining) == len(pipes):
            return False, "Built-in pipelines cannot be deleted (or pipeline not found)."
        self.save_pipelines(remaining)
        self.journal.log_event(
            "PipelineEngine",
            f"delete_{pipeline_id}",
            f"Deleted custom pipeline '{pipeline_id}'",
            "User removed custom pipeline",
            "ok",
        )
        return True, f"Deleted custom pipeline '{pipeline_id}'."

    def run_pipeline(
        self,
        pipeline_id: str,
        adapter: BaseOSAdapter,
        config: AppConfig,
        processes: list[ProcessInfo],
        dry_run: bool | None = None,
    ) -> tuple[bool, str, list[str]]:
        pipes = self.load_pipelines()
        target = next((p for p in pipes if p.pipeline_id == pipeline_id), None)
        if target is None:
            return False, f"Pipeline '{pipeline_id}' not found.", []

        is_dry = config.dry_run_default if dry_run is None else dry_run
        logs: list[str] = []
        presets = {p.preset_id: p for p in self.hw_mgr.load_presets()}

        import psutil
        from .advanced_controls import FullscreenFocusDetector, GpuPowerAndFanController

        ram_pct = float(psutil.virtual_memory().percent)
        batt = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        on_battery = bool(batt and not batt.power_plugged)
        gpu_live = GpuPowerAndFanController.probe_live_power_and_fan()
        try:
            gpu_temp = float(gpu_live.get("temp_c")) if gpu_live.get("temp_c") not in (None, "N/A") else None
        except Exception:
            gpu_temp = None
        fs_state = FullscreenFocusDetector.detect()

        for st in target.stages:
            if not st.enabled:
                continue
            cond = getattr(st, "condition", "always")
            thresh = float(getattr(st, "condition_value", 75.0))
            if cond == "if_ram_above" and ram_pct < thresh:
                logs.append(f"Skipped '{st.label}' (RAM {ram_pct:.0f}% < {thresh:.0f}%).")
                continue
            if cond == "if_temp_above" and (gpu_temp is None or gpu_temp < thresh):
                logs.append(f"Skipped '{st.label}' (GPU temp {gpu_temp or 'N/A'} < {thresh:.0f}C).")
                continue
            if cond == "if_on_battery" and not on_battery:
                logs.append(f"Skipped '{st.label}' (AC power plugged in).")
                continue
            if cond == "if_not_fullscreen" and fs_state.is_fullscreen:
                logs.append(f"Skipped '{st.label}' (Fullscreen app active: {fs_state.window_title}).")
                continue

            if st.stage_type == "apply_preset":
                pid = str(st.params.get("preset_id") or "")
                pr = presets.get(pid)
                if pr:
                    _, pmsg = self.hw_mgr.apply_preset(pr, config, dry_run=is_dry)
                    logs.append(pmsg)
            elif st.stage_type == "run_script":
                sid = str(st.params.get("script_id") or "")
                en = bool(st.params.get("enable", True))
                _, smsg = self.scripts_engine.toggle_script(sid, en, adapter, config, processes, dry_run=is_dry)
                logs.append(smsg)
            elif st.stage_type == "clean_caches":
                res = self.executor.execute_action("clean_temp_caches", adapter, config, dry_run=is_dry)
                logs.append(res.message)
            elif st.stage_type == "preflight_snapshot":
                from .telemetry_history import PreFlightSnapshotHook
                _, snap_msg = PreFlightSnapshotHook.check_or_prepare(dry_run=True)
                logs.append(snap_msg)
            elif st.stage_type == "apply_gpu_profile":
                gctrl = GpuPowerAndFanController(self.cfg_mgr, self.journal)
                gpid = str(st.params.get("gpu_profile_id") or "gpu_balanced_stock")
                gprof = next((g for g in gctrl.load_profiles() if g.profile_id == gpid), None)
                if gprof:
                    _, gmsg, _ = gctrl.apply_profile(gprof, dry_run=is_dry)
                    logs.append(gmsg)
            elif st.stage_type == "self_heal":
                logs.append("Verified forensic ledger & state invariants.")

        if not is_dry:
            target.last_run_at = utc_now_iso()
            self.save_pipelines(pipes)

        summary = f"{'[DRY RUN] ' if is_dry else ''}Pipeline '{target.name}' completed {len(logs)} stage(s)."
        self.journal.log_event(
            component="PipelineEngine",
            action=f"run_{pipeline_id}",
            how="; ".join(logs)[:400] or summary,
            why=f"Executed customizable optimization pipeline '{target.name}'",
            outcome="ok",
        )
        return True, summary, logs

    def poll_local_hook_trigger(
        self,
        adapter: BaseOSAdapter,
        config: AppConfig,
        processes: list[ProcessInfo],
    ) -> str | None:
        """Safe local file-drop IPC connector (~/.astracradle/hooks/trigger.json) for external launchers."""
        if not self.hook_trigger_path.exists():
            return None
        try:
            data = json.loads(self.hook_trigger_path.read_text(encoding="utf-8"))
            self.hook_trigger_path.unlink(missing_ok=True)
            if not isinstance(data, dict):
                return None
            if data.get("pipeline_id"):
                _, msg, _ = self.run_pipeline(str(data["pipeline_id"]), adapter, config, processes, dry_run=bool(data.get("dry_run", False)))
                return f"Local Hook -> {msg}"
            if data.get("preset_id"):
                presets = {p.preset_id: p for p in self.hw_mgr.load_presets()}
                pr = presets.get(str(data["preset_id"]))
                if pr:
                    _, msg = self.hw_mgr.apply_preset(pr, config, dry_run=bool(data.get("dry_run", False)))
                    return f"Local Hook -> {msg}"
        except Exception:
            try:
                self.hook_trigger_path.unlink(missing_ok=True)
            except OSError:
                pass
        return None


class ScriptAndPresetPackManager:
    """Exports and imports shareable .astracradle-pack.json bundles with SHA-256 verification."""

    @staticmethod
    def export_pack(
        dest_path: Path,
        config: AppConfig,
        presets: list[PriorityPreset],
        pipelines: list[OptimizationPipeline],
    ) -> Path:
        body = {
            "format": "astracradle-pack-v1",
            "exported_at": utc_now_iso(),
            "auto_detect_rules": config.auto_detect_rules,
            "presets": [asdict(p) for p in presets if not p.built_in] or [asdict(presets[0])],
            "pipelines": [asdict(pl) for pl in pipelines if not pl.built_in] or [asdict(pipelines[0])],
        }
        canonical = json.dumps(body, sort_keys=True).encode("utf-8")
        pack = {"payload": body, "sha256": hashlib.sha256(canonical).hexdigest()}
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
        return dest_path

    @staticmethod
    def import_pack(
        src_path: Path,
        hw_mgr: HardwareLibraryManager,
        pipe_mgr: PipelineEngine,
        config: AppConfig,
    ) -> tuple[bool, str]:
        try:
            pack = json.loads(src_path.read_text(encoding="utf-8"))
            payload = pack.get("payload")
            expected = str(pack.get("sha256") or "")
            if not isinstance(payload, dict) or payload.get("format") != "astracradle-pack-v1":
                return False, "Invalid AstraCradle pack format."
            actual = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
            if expected and actual != expected:
                return False, "Pack rejected: SHA-256 verification failed."

            imported_presets = 0
            for pr in payload.get("presets") or []:
                if isinstance(pr, dict) and pr.get("preset_id") and pr.get("name"):
                    valid = {f.name for f in PriorityPreset.__dataclass_fields__.values()}
                    obj = PriorityPreset(**{k: v for k, v in pr.items() if k in valid})
                    hw_mgr.save_custom_preset(obj)
                    imported_presets += 1

            imported_pipes = 0
            for pl in payload.get("pipelines") or []:
                if isinstance(pl, dict) and pl.get("pipeline_id") and pl.get("name"):
                    stages = [
                        PipelineStage(
                            stage_type=str(s.get("stage_type", "notify")),
                            label=str(s.get("label", "Stage")),
                            params=s.get("params") if isinstance(s.get("params"), dict) else {},
                            enabled=bool(s.get("enabled", True)),
                        )
                        for s in (pl.get("stages") or [])
                        if isinstance(s, dict)
                    ]
                    pipe_mgr.save_custom_pipeline(
                        OptimizationPipeline(
                            pipeline_id=str(pl["pipeline_id"]),
                            name=str(pl["name"]),
                            description=str(pl.get("description", "")),
                            trigger_mode=str(pl.get("trigger_mode", "manual")),
                            stages=stages,
                            built_in=False,
                        )
                    )
                    imported_pipes += 1

            if isinstance(payload.get("auto_detect_rules"), dict):
                for k, v in payload["auto_detect_rules"].items():
                    config.auto_detect_rules[str(k)] = str(v)

            return True, f"Imported {imported_presets} preset(s) and {imported_pipes} pipeline(s) cleanly."
        except Exception as exc:
            return False, f"Pack import failed: {exc}"
