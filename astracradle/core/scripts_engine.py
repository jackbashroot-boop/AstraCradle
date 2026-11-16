"""Toggleable Optimization Scripts Engine (Gaming, 3D Render, Compile/Data, AI Hibernate + Custom)."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import uuid

from .config import AppConfig, ConfigManager
from .executor import GuardedExecutor
from .journal import ForensicJournal
from .models import OptimizationScript, ProcessInfo, utc_now_iso
from .os_adapters import BaseOSAdapter
from .process_advisor import AI_CHATBOT_SIGNATURES


def default_scripts() -> list[OptimizationScript]:
    return [
        OptimizationScript(
            script_id="script_gaming_max",
            name="Gaming Max-FPS & Low-Latency",
            category="gaming",
            description=(
                "Optimizes frame pacing, trims temp/shader clutter, pauses idle AI chatbots & background updaters, "
                "and shifts power bias toward maximum responsiveness."
            ),
            enabled=False,
            built_in=True,
            os_family="all",
            steps_summary=[
                "Snapshot current profile & active state for 1-click revert",
                "Trim stale temp/crash caches safely into Quarantine",
                "Lower priority on idle non-essential background helpers & pause AI chatbots",
                "Apply High-Performance / Game-Mode power & timer bias",
            ],
            apply_actions=[
                {"type": "set_profile", "profile": "Gaming / Low-Latency"},
                {"type": "exec_action", "action_id": "clean_temp_caches"},
                {"type": "hibernate_ai_chatbots", "mode": "renice"},
                {
                    "type": "os_power_hint",
                    "Windows": "powercfg /setactive SCHEME_MIN",
                    "Linux": "powerprofilesctl set performance",
                    "Darwin": "sudo pmset -a powernap 0",
                },
            ],
            revert_actions=[
                {"type": "set_profile", "profile": "Balanced Daily"},
                {
                    "type": "os_power_hint",
                    "Windows": "powercfg /setactive SCHEME_BALANCED",
                    "Linux": "powerprofilesctl set balanced",
                    "Darwin": "sudo pmset -a powernap 1",
                },
            ],
        ),
        OptimizationScript(
            script_id="script_3d_render",
            name="3D Benchmarking, Modeling & Rendering",
            category="3d_render",
            description=(
                "Prepares system for Blender, Unreal Engine, Maya, CAD, or 3D benchmarks by reclaiming scratch clutter, "
                "silencing background RAM hogs, and unlocking sustained compute throughput."
            ),
            enabled=False,
            built_in=True,
            os_family="all",
            steps_summary=[
                "Switch active equilibrium profile to '3D Render & Benchmark'",
                "Quarantine stale temp/log artifacts to maximize scratch headroom",
                "Lower background priority on idle helpers & AI chat servers",
                "Recommend sustained high-performance thermal/GPU power scheme",
            ],
            apply_actions=[
                {"type": "set_profile", "profile": "3D Render & Benchmark"},
                {"type": "exec_action", "action_id": "clean_temp_caches"},
                {"type": "hibernate_ai_chatbots", "mode": "renice"},
                {
                    "type": "os_power_hint",
                    "Windows": "powercfg /setactive SCHEME_MIN",
                    "Linux": "powerprofilesctl set performance",
                    "Darwin": "sudo pmset -a disablesleep 1",
                },
            ],
            revert_actions=[
                {"type": "set_profile", "profile": "Balanced Daily"},
                {
                    "type": "os_power_hint",
                    "Windows": "powercfg /setactive SCHEME_BALANCED",
                    "Linux": "powerprofilesctl set balanced",
                    "Darwin": "sudo pmset -a disablesleep 0",
                },
            ],
        ),
        OptimizationScript(
            script_id="script_compile_data",
            name="Massive Data & Code Compilation Boost",
            category="compile_data",
            description=(
                "Tunes system for heavy C++/Rust/Python builds, Docker pipelines, or large data processing while "
                "strictly protecting compiler/IDE build caches from premature deletion."
            ),
            enabled=False,
            built_in=True,
            os_family="all",
            steps_summary=[
                "Activate 'Data & Code Compilation' profile (raises cache safety threshold to 1.5 GB)",
                "Rotate oversized logs without touching IDE/build artifacts",
                "Lower priority on idle desktop helpers & chatbots to free CPU threads",
            ],
            apply_actions=[
                {"type": "set_profile", "profile": "Data & Code Compilation"},
                {"type": "exec_action", "action_id": "vacuum_user_logs"},
                {"type": "hibernate_ai_chatbots", "mode": "renice"},
            ],
            revert_actions=[
                {"type": "set_profile", "profile": "Balanced Daily"},
            ],
        ),
        OptimizationScript(
            script_id="script_ai_hibernate",
            name="Silence Idle AI Chatbots & Assistants",
            category="ai_hibernate",
            description=(
                "Locates unused local AI chatbot/LLM background processes (Ollama, LM Studio, GPT4All, KoboldCpp, "
                "Copilot/ChatGPT helpers) and gracefully stops or throttles them."
            ),
            enabled=False,
            built_in=True,
            os_family="all",
            steps_summary=[
                "Scan active processes for known local AI chatbot/LLM runtimes",
                "Verify each target is not on your Protected Process Allow-List",
                "Gracefully terminate idle AI daemons (with full forensic logging)",
            ],
            apply_actions=[
                {"type": "hibernate_ai_chatbots", "mode": "terminate_idle"},
            ],
            revert_actions=[],
        ),
    ]


class ScriptsEngine:
    """Manages toggleable, reversible optimization scripts and user-created custom scripts."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        executor: GuardedExecutor,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.executor = executor
        self.journal = journal

    DANGEROUS_PATTERNS = (
        "rm -rf /",
        "rm -fr /",
        "rm -rf /*",
        "mkfs",
        "dd if=",
        "format c:",
        "del /f /s /q",
        "rd /s /q",
        ":(){",
        "shutdown",
        "reboot",
        "poweroff",
    )

    @classmethod
    def is_command_safe(cls, cmd: str) -> tuple[bool, str]:
        low = " ".join(cmd.strip().lower().split())
        if not low:
            return False, "Empty command"
        for pat in cls.DANGEROUS_PATTERNS:
            if pat in low:
                return False, f"Blocked by Guardrail: destructive pattern '{pat}' refused."
        if any(tok in low for tok in ("&&", "||", ";", "`", "$(")):
            return False, "Blocked by Guardrail: chained shell operators refused in custom scripts."
        return True, ""

    def load_scripts(self) -> list[OptimizationScript]:
        path = self.cfg_mgr.scripts_path
        if not path.exists():
            scripts = default_scripts()
            self.save_scripts(scripts)
            return scripts
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("scripts.json is not a list")
            valid = {f.name for f in OptimizationScript.__dataclass_fields__.values()}
            req = {"script_id", "name", "category", "description"}
            out: list[OptimizationScript] = []
            for item in raw:
                if isinstance(item, dict) and req.issubset(item.keys()):
                    try:
                        out.append(OptimizationScript(**{k: v for k, v in item.items() if k in valid}))
                    except Exception:
                        continue
            if not out:
                raise ValueError("No valid scripts in scripts.json")
            return out
        except Exception:
            scripts = default_scripts()
            self.save_scripts(scripts)
            return scripts

    def save_scripts(self, scripts: list[OptimizationScript]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.cfg_mgr.scripts_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(s) for s in scripts], indent=2), encoding="utf-8")
        os.replace(tmp, self.cfg_mgr.scripts_path)

    def add_custom_script(
        self,
        name: str,
        category: str,
        description: str,
        shell_command: str = "",
        profile_name: str = "",
    ) -> OptimizationScript:
        scripts = self.load_scripts()
        steps = []
        apply_acts = []
        if profile_name:
            steps.append(f"Switch profile to '{profile_name}'")
            apply_acts.append({"type": "set_profile", "profile": profile_name})
        if shell_command.strip():
            steps.append(f"Run safe command: {shell_command.strip()}")
            apply_acts.append({"type": "custom_shell", "command": shell_command.strip()})
        if not steps:
            steps.append("Custom user toggle script")

        new_s = OptimizationScript(
            script_id=f"custom_{uuid.uuid4().hex[:8]}",
            name=name.strip() or "Custom Script",
            category=category or "custom",
            description=description.strip() or "User-defined toggleable optimization script.",
            enabled=False,
            built_in=False,
            os_family="all",
            steps_summary=steps,
            apply_actions=apply_acts,
            revert_actions=[{"type": "set_profile", "profile": "Balanced Daily"}],
        )
        scripts.append(new_s)
        self.save_scripts(scripts)
        self.journal.log_event(
            component="ScriptsEngine",
            action="add_custom_script",
            how=f"Registered custom script '{new_s.name}' ({new_s.script_id})",
            why="User added a reusable toggleable optimization script",
            outcome="ok",
        )
        return new_s

    def remove_script(self, script_id: str) -> tuple[bool, str]:
        scripts = self.load_scripts()
        remaining = [s for s in scripts if s.script_id != script_id]
        if len(remaining) == len(scripts):
            return False, f"Script '{script_id}' not found."
        self.save_scripts(remaining)
        self.journal.log_event(
            component="ScriptsEngine",
            action="remove_script",
            how=f"Removed script '{script_id}' from toggle registry",
            why="User requested removal of optimization script",
            outcome="ok",
        )
        return True, f"Removed script '{script_id}'."

    def toggle_script(
        self,
        script_id: str,
        enable: bool,
        adapter: BaseOSAdapter,
        config: AppConfig,
        processes: list[ProcessInfo],
        dry_run: bool | None = None,
    ) -> tuple[bool, str]:
        scripts = self.load_scripts()
        target: OptimizationScript | None = None
        for s in scripts:
            if s.script_id == script_id:
                target = s
                break
        if target is None:
            return False, f"Script '{script_id}' not found."

        is_dry = config.dry_run_default if dry_run is None else dry_run
        actions = target.apply_actions if enable else target.revert_actions
        notes: list[str] = []

        for act in actions:
            atype = act.get("type")
            if atype == "set_profile":
                prof = str(act.get("profile", "Balanced Daily"))
                if not is_dry:
                    config.apply_profile(prof)
                    self.cfg_mgr.save(config)
                notes.append(f"Profile -> {prof}")
            elif atype == "exec_action":
                aid = str(act.get("action_id", ""))
                res = self.executor.execute_action(aid, adapter, config, dry_run=is_dry)
                notes.append(res.message)
            elif atype == "hibernate_ai_chatbots":
                mode = str(act.get("mode", "renice"))
                affected = 0
                for p in processes:
                    if p.protected:
                        continue
                    hay = f"{p.name} {p.cmdline}".lower()
                    if any(sig in hay for sig, _ in AI_CHATBOT_SIGNATURES):
                        if is_dry:
                            affected += 1
                        elif mode == "terminate_idle" and p.cpu_percent < 3.0:
                            ok, _ = self.executor.safe_terminate_process(p.pid, config)
                            if ok:
                                affected += 1
                        else:
                            ok, _ = self.executor.safe_renice_process(p.pid, config)
                            if ok:
                                affected += 1
                notes.append(f"AI chatbot runtimes handled: {affected}")
            elif atype == "os_power_hint":
                sys_cmd = act.get(platform.system())
                if sys_cmd:
                    notes.append(f"OS power command ready: {sys_cmd}")
            elif atype == "custom_shell":
                cmd = str(act.get("command", "")).strip()
                if cmd:
                    safe_ok, reason = self.is_command_safe(cmd)
                    if not safe_ok:
                        self.journal.log_event(
                            component="ScriptsEngine",
                            action=f"blocked_{script_id}",
                            how=f"Refused unsafe custom command: {cmd[:120]}",
                            why=reason,
                            outcome="skipped",
                        )
                        return False, reason
                    if not is_dry:
                        try:
                            argv = shlex.split(cmd, posix=(os.name != "nt"))
                            proc = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=10)
                            notes.append(f"Command exit={proc.returncode}")
                        except Exception as exc:
                            notes.append(f"Command error: {exc}")
                    else:
                        notes.append(f"[DRY RUN] Would run: {cmd}")

        if not is_dry:
            snap_file = self.cfg_mgr.snapshots_dir / f"tweak_{script_id}.json"
            if enable:
                try:
                    snap_payload = adapter.capture_tweak_snapshot(target.name)
                    snap_file.write_text(json.dumps(snap_payload, indent=2), encoding="utf-8")
                    notes.append(f"Saved pre-tweak OS snapshot ({snap_payload.get('mode', 'balanced')})")
                except Exception:
                    pass
            elif snap_file.exists():
                try:
                    snap_payload = json.loads(snap_file.read_text(encoding="utf-8"))
                    _, rmsg = adapter.restore_tweak_snapshot(snap_payload)
                    snap_file.unlink(missing_ok=True)
                    notes.append(rmsg)
                except Exception:
                    pass
            target.enabled = enable
            target.last_toggled_at = utc_now_iso()
            self.save_scripts(scripts)

        summary = "; ".join(notes) if notes else ("Enabled" if enable else "Reverted")
        self.journal.log_event(
            component="ScriptsEngine",
            action=f"{'enable' if enable else 'revert'}_{script_id}",
            how=f"{'Simulated' if is_dry else 'Executed'} script '{target.name}': {summary}",
            why=f"User toggled optimization script {'ON' if enable else 'OFF'}",
            outcome="ok",
            details=summary,
        )
        return True, f"{'[DRY RUN] ' if is_dry else ''}{target.name}: {summary}"
