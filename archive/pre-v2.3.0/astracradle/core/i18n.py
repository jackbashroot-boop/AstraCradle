"""Localization (i18n) and searchable in-app Help Knowledge Base for AstraCradle."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
}


@dataclass(frozen=True)
class HelpTopic:
    topic_id: str
    title: str
    category: str
    keywords: tuple[str, ...]
    summary: str
    details: str
    nav_target: str | None = None


HELP_TOPICS: tuple[HelpTopic, ...] = (
    HelpTopic(
        topic_id="equilibrium_score",
        title="Equilibrium Health Score (0–100)",
        category="Command Deck",
        keywords=("score", "gauge", "dyson", "health", "rating", "90", "red", "cyan"),
        summary="Live 0–100 score showing how healthy, cool, and clutter-free your PC currently is.",
        details=(
            "AstraCradle calculates your Equilibrium Score locally from CPU load, RAM pressure, "
            "free disk headroom, DNS responsiveness, dormant AI/background processes, and reclaimable "
            "cache size. 85–100 is Optimal; 65–84 means light cleanup helps; below 65 highlights "
            "actionable bottlenecks in your Playbook."
        ),
        nav_target="dashboard",
    ),
    HelpTopic(
        topic_id="situation_profiles",
        title="Situation Profiles (Gaming, 3D Render, Compile, Battery, Safe Mode)",
        category="Command Deck",
        keywords=("profile", "gaming", "render", "compile", "battery", "safe mode", "balanced"),
        summary="Instant presets that tune safety floors, cache thresholds, and power behavior.",
        details=(
            "• Balanced Daily: everyday speed + quiet thermals.\n"
            "• Gaming / Low-Latency: trims shader/temp clutter and quiets idle AI helpers.\n"
            "• 3D Render & Benchmark: prioritizes sustained CPU/GPU headroom.\n"
            "• Data & Code Compilation: protects build caches while lowering background noise.\n"
            "• Battery Endurance: reduces wakeups and avoids aggressive cleanup.\n"
            "• Minimal Safe Mode: strictly read-only Dry-Run; zero changes allowed."
        ),
        nav_target="dashboard",
    ),
    HelpTopic(
        topic_id="quarantine_rollback",
        title="Quarantine Vault & 1-Click Rollback",
        category="Safety & Recovery",
        keywords=("quarantine", "undo", "rollback", "restore", "delete", "safe", "files", "backup"),
        summary="Cleaned files are moved to Quarantine first—never permanently shredded—so Undo restores them.",
        details=(
            "Whenever AstraCradle cleans caches, logs, or duplicate copies, it moves items into "
            "~/.astracradle/quarantine with a recorded SHA-256 ledger entry. Click 'Rollback Last' "
            "or open Safeguards -> Rollback Ledger to move items straight back to their original paths. "
            "Personal folders (Documents, Desktop, Photos, SSH keys) are strictly protected."
        ),
        nav_target="safeguards",
    ),
    HelpTopic(
        topic_id="dry_run_mode",
        title="Dry-Run Simulation Mode",
        category="Safety & Recovery",
        keywords=("dry run", "dry-run", "preview", "simulate", "test", "safe"),
        summary="Preview exact files, bytes, and steps AstraCradle would touch without changing a single byte.",
        details=(
            "Turn on Dry-Run in the top bar or choose 'Minimal Safe Mode' to test any Playbook step "
            "or Toggleable Script safely. Every simulated action is still recorded in the Forensic Journal "
            "marked as dry_run so you can review exact impact beforehand."
        ),
        nav_target="playbook",
    ),
    HelpTopic(
        topic_id="ai_hunter",
        title="AI Chatbot & Idle Process Hunter",
        category="Modules & Bloat",
        keywords=("ai", "chatbot", "ollama", "lmstudio", "gpt4all", "ram", "idle", "process", "hunter"),
        summary="Detects dormant local LLMs and idle helpers holding gigabytes of RAM at near-0% CPU.",
        details=(
            "With your explicit permission, AstraCradle scans running processes locally for idle AI "
            "chatbot runtimes (Ollama, LM Studio, Jan, KoboldCpp, GPT4All, Text-Gen WebUI) and dormant "
            "helpers. You can terminate them once or click 'Run & Automate Always' to create a rule. "
            "Core OS daemons (systemd, explorer.exe, WindowServer, etc.) are never touchable."
        ),
        nav_target="modules",
    ),
    HelpTopic(
        topic_id="duplicates_bloat",
        title="Installed App Usage, Duplicate Finder & Folder Bloat",
        category="Modules & Bloat",
        keywords=("duplicate", "sha256", "bloat", "unused", "programs", "apps", "disk", "large", "folders"),
        summary="Finds unused installed programs, exact SHA-256 duplicate files, and oversized folders.",
        details=(
            "Inside Modules -> Duplicates & Bloat:\n"
            "• Unused Apps: lists installed applications with footprint and last-used hints for review.\n"
            "• SHA-256 Duplicate Finder: groups files by size, pre-hashes 4 KB headers, then verifies "
            "exact SHA-256 matches—keeping the original copy and moving extras to Quarantine.\n"
            "• Folder Bloat: highlights the heaviest subdirectories so you know where disk space went."
        ),
        nav_target="modules",
    ),
    HelpTopic(
        topic_id="toggleable_scripts",
        title="Toggleable Scripts & Hardware Power Snapshots",
        category="Toggleable Scripts",
        keywords=("scripts", "toggle", "gaming", "render", "power", "governor", "snapshot", "custom"),
        summary="Flip workload optimization scripts ON before a heavy task and OFF afterwards to restore defaults.",
        details=(
            "Built-in scripts (Gaming, 3D Render, Compile/Data, Idle AI Hibernate) capture an OS hardware "
            "state snapshot (Windows powercfg plan, macOS pmset flags, Linux CPU governor/swappiness) "
            "before toggling ON, and restore your original configuration when toggled OFF. Custom scripts "
            "pass a strict destructive-command guardrail blocking dangerous shell patterns."
        ),
        nav_target="scripts",
    ),
    HelpTopic(
        topic_id="forensic_journal",
        title="Forensic Journal & Hash-Chained Self-Audit",
        category="Safety & Recovery",
        keywords=("log", "forensic", "sha256", "hash", "audit", "tamper", "what", "how", "why"),
        summary="Every action logs WHAT happened, HOW it ran, and WHY—linked by cryptographic SHA-256 hashes.",
        details=(
            "Each entry in ~/.astracradle/logs/forensic_journal.jsonl stores prev_hash -> entry_hash. "
            "AstraCradle self-reads and audits this chain at startup and during Self-Heal checks to detect "
            "any missing/corrupted lines, recurring errors, or failed rollbacks."
        ),
        nav_target="safeguards",
    ),
    HelpTopic(
        topic_id="crash_post_mortem",
        title="Crash & OS Failure Post-Mortem (Event ID 41, OOM, Panic)",
        category="Safety & Recovery",
        keywords=("crash", "bsod", "event 41", "oom", "panic", "power loss", "reboot", "freeze"),
        summary="Detects sudden app crashes or OS power-loss reboots and explains what happened & how to fix it.",
        details=(
            "AstraCradle's Heartbeat Sentinel compares PID, clean_shutdown flag, and OS boot_time. "
            "If your PC crashed or lost power, AstraCradle correlates the last forensic actions with native "
            "OS crash clues (Windows Event ID 41 Kernel-Power / BugCheck 1001, macOS DiagnosticReports, "
            "Linux journalctl OOM-killer / pstore) and shows step-by-step recovery actions."
        ),
        nav_target="safeguards",
    ),
    HelpTopic(
        topic_id="scheduler_tray",
        title="Background Scheduler, System Tray & Threshold Alerts",
        category="Autopilot",
        keywords=("schedule", "tray", "background", "alert", "toast", "quiet hours", "automate", "timer"),
        summary="Run scheduled health checks automatically and receive threshold warnings without keeping the window open.",
        details=(
            "Under Autopilot or Settings you can generate/enable a native background schedule (Windows Task "
            "Scheduler, macOS launchd user agent, or Linux systemd --user timer), toggle Minimize to Tray, "
            "and configure Quiet Hours so background maintenance never interrupts late-night work or games."
        ),
        nav_target="autopilot",
    ),
    HelpTopic(
        topic_id="updates_plugins",
        title="Drop-In Plugins & Ed25519 Signed Updates",
        category="Settings",
        keywords=("plugin", "update", "ed25519", "signature", "verify", "module", "patch"),
        summary="Expand AstraCradle with drop-in JSON/Python plugins and cryptographically verified updates.",
        details=(
            "Drop plugin manifests (.json) or safe Python plugin packs (.py with PLUGIN_META) into "
            "~/.astracradle/plugins/. Local or staged updates verify both SHA-256 package hashes and "
            "pure-Python RFC 8032 Ed25519 signatures before applying."
        ),
        nav_target="settings",
    ),
    HelpTopic(
        topic_id="accessibility_shortcuts",
        title="Keyboard Shortcuts, High Contrast, Scaling & Languages",
        category="Accessibility",
        keywords=("shortcut", "keyboard", "f1", "ctrl", "high contrast", "scale", "font", "language", "spanish", "french"),
        summary="Navigate rapidly with hotkeys, switch High Contrast mode, scale text, or change UI language.",
        details=(
            "Keyboard shortcuts:\n"
            "• F1 or Ctrl+F — Open Help & Search Menu\n"
            "• Ctrl+R — Run Live System Scan\n"
            "• Ctrl+1..0 — Jump between Command Deck, Playbook, Hunter, Scripts, Hardware & Pipelines, System Modules, Forensic & Vault, Settings, ProBalance & Latency, Autoruns & Baselines\n"
            "• Settings -> Theme ('High Contrast'), UI Scale (90%–130%), and Language (EN / ES / FR)."
        ),
        nav_target="settings",
    ),
    HelpTopic(
        topic_id="hardware_presets",
        title="PC Component Library, Spec Passport & Priority Presets",
        category="Hardware & Pipelines",
        keywords=("cpu", "gpu", "ram", "nvme", "x3d", "ccd0", "p-core", "e-core", "affinity", "ionice", "passport", "preset"),
        summary="Match your exact CPU/GPU/Storage/RAM and save custom CPU-affinity + I/O priority presets.",
        details=(
            "Under Hardware & Pipelines -> Component Library & Presets:\n"
            "• Know Your PC Passport: auto-detects CPU, GPU, RAM, and storage with 1-click Copy Spec Passport.\n"
            "• Hybrid Intel (P/E-Core) & Dual-CCD AMD X3D (CCD0/CCD1) rules: pins noisy background apps to efficiency cores and Idle I/O.\n"
            "• Save custom Priority Presets or delete custom ones anytime."
        ),
        nav_target="hardware",
    ),
    HelpTopic(
        topic_id="pipelines_connectors",
        title="Visual Optimization Pipelines, Conditions & Local Hooks",
        category="Hardware & Pipelines",
        keywords=("pipeline", "workflow", "stage", "condition", "hook", "trigger", "pack", "sha256", "share"),
        summary="Chain multi-step optimizations with conditional branches, local triggers, and shareable .astracradle-pack.json files.",
        details=(
            "Under Hardware & Pipelines -> Visual Pipelines & Packs:\n"
            "• Click 'Edit Stages' to reorder stages (Up/Down) and pick conditions (if_ram_above, if_temp_above, if_on_battery, if_not_fullscreen).\n"
            "• Local Hook Connector: drop JSON into ~/.astracradle/hooks/trigger.json or run `astracradle --run-pipeline <id>`.\n"
            "• Export/Import SHA-256 verified .astracradle-pack.json preset & pipeline bundles."
        ),
        nav_target="hardware",
    ),
    HelpTopic(
        topic_id="gpu_power_fan",
        title="GPU Power-Limit & Thermal Fan-Curve Profiles",
        category="Hardware & Pipelines",
        keywords=("gpu", "vram", "thermal", "throttling", "watt", "power limit", "fan", "curve", "undervolt"),
        summary="Track live GPU/VRAM load and thermal throttling, and save quiet/max-cooling power & fan profiles.",
        details=(
            "Command Deck displays live GPU utilization, VRAM usage, and thermal throttling status, "
            "plus a 30-day local SQLite history graph. Under Hardware & Pipelines -> GPU Power & Fan Curves, "
            "create and apply Power % / Fan % thermal profiles safely."
        ),
        nav_target="hardware",
    ),
    HelpTopic(
        topic_id="network_firewall",
        title="Per-App Network Firewall & Bandwidth Shaper",
        category="Modules & Bloat",
        keywords=("firewall", "network", "throttle", "bandwidth", "block", "kbps", "netsh", "nft", "pfctl"),
        summary="Create reversible per-app bandwidth throttle, block, or allow rules with OS-native commands.",
        details=(
            "Under System Modules -> Processes & Firewall, specify an app name and action (throttle KB/s, block, or allow). "
            "AstraCradle stores rules safely and generates exact OS-native netsh/pfctl/nft/tc commands."
        ),
        nav_target="modules",
    ),
    HelpTopic(
        topic_id="fullscreen_focus_shredder",
        title="Fullscreen Focus Guard, 1-Click Glitch Fixes, Toast Undo & Shredder",
        category="Safety & Recovery",
        keywords=("fullscreen", "focus", "glitch", "thumbnail", "dns", "spooler", "shred", "zero-pass", "toast", "outdated"),
        summary="Pause background tweaks in fullscreen games, fix everyday glitches, undo via Toast, or shred confirmed items.",
        details=(
            "• Fullscreen Focus Guard: detects fullscreen games/presentations (Win32/X11/Wayland/Quartz) to pause background noise.\n"
            "• Everyday PC Glitch Fixes: 1-click thumbnail/font cache rebuild, DNS flush, audio/spooler check, and opt-in outdated runtime scan.\n"
            "• Toast Undo & Zero-Pass Shredder: click Undo right on the notification banner, or optionally zero-pass shred confirmed quarantine items."
        ),
        nav_target="modules",
    ),
    HelpTopic(
        topic_id="interrupt_dpc_guard",
        title="Interrupt Affinity & DPC Latency Guard",
        category="ProBalance & Latency",
        keywords=("dpc", "latency", "jitter", "irq", "interrupt", "affinity", "usb", "audio", "stutter"),
        summary="Measures timer/DPC wakeup jitter and moves noisy USB/NIC/audio IRQs off primary game cores.",
        details=(
            "Under ProBalance & Latency -> Interrupt & DPC Guard:\n"
            "• Run a portable Timer/DPC Jitter probe (p50/p95/p99/max microseconds).\n"
            "• Inspect active hardware IRQs and apply safe Interrupt Affinity Policies moving USB/NIC/audio interrupts off Core 0."
        ),
        nav_target="probalance",
    ),
    HelpTopic(
        topic_id="probalance_app_profiles",
        title="ProBalance Foreground Boost & Auto-Reverted App Profiles",
        category="ProBalance & Latency",
        keywords=("probalance", "foreground", "boost", "nice", "cpu hog", "auto-revert", "blender", "steam"),
        summary="Restrains background CPU hogs dynamically and auto-reverts app power/GPU profiles when apps exit.",
        details=(
            "Under ProBalance & Latency:\n"
            "• ProBalance Engine: temporarily lowers priority of non-protected background CPU hogs and restores them automatically.\n"
            "• Auto-Revert App Rules: escalates your Situation Profile/Preset on app launch and reverts cleanly on exit."
        ),
        nav_target="probalance",
    ),
    HelpTopic(
        topic_id="thermal_spc_baselines",
        title="Workload-Aware Thermal Baselines & Welford SPC",
        category="Autoruns & Baselines",
        keywords=("thermal", "spc", "welford", "sigma", "baseline", "drift", "anomaly", "idle", "gaming"),
        summary="Learns online mean and standard deviation per workload tier (idle/light/heavy/gaming) to flag real thermal drift.",
        details=(
            "Under Autoruns & Baselines -> Thermal SPC Baselines, AstraCradle maintains online Welford statistics "
            "separately for idle, light, heavy, and gaming workloads and alerts on 3-sigma control-limit drift."
        ),
        nav_target="autoruns",
    ),
    HelpTopic(
        topic_id="fan_curve_editor",
        title="Interactive Draft-vs-Applied Fan Curve Editor",
        category="Hardware & Pipelines",
        keywords=("fan curve", "draft", "applied", "canvas", "drag", "temperature", "cooling"),
        summary="Drag temperature-to-fan% nodes on a live heat-gradient canvas comparing Draft vs Applied curves.",
        details=(
            "Under Hardware & Pipelines -> GPU Power & Fan Curves, drag any control point to sculpt a Draft curve "
            "against the dashed Applied curve, then click 'Apply Draft Curve' or 'Reset Draft'."
        ),
        nav_target="hardware",
    ),
    HelpTopic(
        topic_id="autoruns_ghost_drivers",
        title="Autoruns Startup/Task Audit & Ghost Driver Cleanup",
        category="Autoruns & Baselines",
        keywords=("autoruns", "scheduled task", "cron", "systemd", "ghost driver", "disconnected", "phantom"),
        summary="Plain-English audit of startup items, scheduled tasks, services, and disconnected ghost drivers.",
        details=(
            "Under Autoruns & Baselines:\n"
            "• Autoruns & Services: inspect and safely toggle user startup entries and scheduled tasks.\n"
            "• Ghost Driver Cleanup: detect disconnected PnP/idle modules or orphaned driver artifacts with rollback."
        ),
        nav_target="autoruns",
    ),
    HelpTopic(
        topic_id="multires_evidence_scoring",
        title="Multi-Resolution History (60s/5m/30m/1hr/30d) & Confidence Scores",
        category="Command Deck",
        keywords=("60s", "5m", "30m", "1hr", "30d", "resolution", "confidence", "evidence", "score"),
        summary="Switch telemetry chart windows from 60s to 30d and inspect quantitative evidence & confidence scores.",
        details=(
            "Command Deck lets you switch between 60s, 5m, 30m, 1hr, and 30d telemetry charts. "
            "Playbook findings and Crash Post-Mortem reports show weighted Evidence & Confidence % scores."
        ),
        nav_target="dashboard",
    ),
    HelpTopic(
        topic_id="clean_uninstall",
        title="Safe & Clean Uninstallation",
        category="Setup & Uninstall",
        keywords=("uninstall", "remove", "delete", "clean", "data", "profiles", "restore"),
        summary="Reverts active scripts, restores quarantined files if desired, and cleanly removes AstraCradle.",
        details=(
            "Click 'Uninstall...' in the top bar (or run Uninstall_AstraCradle.bat / .command / .sh). "
            "AstraCradle explicitly asks whether to delete all profiles, logs, and saved data or keep them, "
            "reverts active optimization tweaks, removes scheduled tasks/shortcuts, and leaves zero stray damage."
        ),
        nav_target=None,
    ),
    HelpTopic(
        topic_id="smart_sunburst_vault",
        title="SMART Disk Health, Sunburst Visualizer & AI/Dev Model Vault",
        category="Modules & Bloat",
        keywords=("smart", "nvme", "wear", "temperature", "sunburst", "treemap", "ollama", "huggingface", "node_modules", "empty"),
        summary="Inspect drive wear/temp, visualize disk usage as animated Dyson arcs, and spot giant AI/dev caches.",
        details=(
            "Inside Modules -> Duplicates & Bloat:\n"
            "• Dyson Disk Sunburst: animated interactive ring showing your heaviest directories.\n"
            "• SMART / NVMe Health: displays drive temperature, wear level, and unsafe shutdown counters.\n"
            "• AI & Dev Vault: surfaces large Ollama/HuggingFace/LM Studio model caches and rebuildable node_modules/target folders.\n"
            "• Empty/Dead Sweeper: safely finds empty directories, dangling symlinks, and broken shortcuts."
        ),
        nav_target="modules",
    ),
    HelpTopic(
        topic_id="benchmark_autodetect",
        title="Before/After Micro-Benchmark, App Auto-Detector & Leak Watchdog",
        category="Command Deck",
        keywords=("benchmark", "speed", "auto detect", "blender", "game", "memory leak", "watchdog", "ram"),
        summary="Measure real speedups, auto-toggle workload scripts on app launch, and catch RAM leaks early.",
        details=(
            "• Micro-Benchmark: runs a 1-second local CPU, memory-copy, and temp disk test so you can compare Before vs After scores.\n"
            "• App Auto-Detector: watches for configured games/3D/compile apps (e.g. Blender, Steam, Cargo) and flips the matching script ON automatically.\n"
            "• RAM Leak Watchdog: tracks per-process RSS growth across ticks and warns if an app climbs steadily."
        ),
        nav_target="dashboard",
    ),
    HelpTopic(
        topic_id="privacy_shield_bundle",
        title="Privacy Sweeper, Integrity Shield, Delayed Startup & Recovery Bundle",
        category="Safety & Recovery",
        keywords=("privacy", "browser", "cookies", "delay", "startup", "integrity", "hosts", "ssh", "bundle", "digest", "process"),
        summary="Sweep non-credential traces, delay heavy startup apps (+60s), guard hosts/SSH, or export recovery bundles.",
        details=(
            "• Privacy Trace Sweeper: cleans browser/thumbnail caches while strictly preserving passwords, bookmarks & cookies.\n"
            "• Startup Delay (+60s): lets heavy apps start 60s after login so your desktop boots fast.\n"
            "• Integrity Shield: hashes critical files (hosts, ~/.ssh/authorized_keys) and alerts on unexpected edits.\n"
            "• Recovery Bundle (.zip) & Weekly Digest (.html): portable offline diagnostics & visual weekly summaries.\n"
            "• Plain-English Process Translator: click any process row to see what it does and whether it's safe."
        ),
        nav_target="safeguards",
    ),
)


class Translator:
    """Loads localized UI strings with automatic English fallback."""

    def __init__(self, lang: str = "en", locales_dir: Path | None = None) -> None:
        self.locales_dir = locales_dir or (Path(__file__).resolve().parents[2] / "locales")
        self.lang = lang if lang in SUPPORTED_LANGUAGES else "en"
        self._en = self._load_file("en")
        self._active = self._load_file(self.lang) if self.lang != "en" else self._en

    def _load_file(self, code: str) -> dict[str, str]:
        p = self.locales_dir / f"{code}.json"
        if not p.exists():
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass
        return {}

    def set_language(self, lang: str) -> None:
        self.lang = lang if lang in SUPPORTED_LANGUAGES else "en"
        self._active = self._load_file(self.lang) if self.lang != "en" else self._en

    def t(self, key: str, default: str | None = None) -> str:
        if key in self._active:
            return self._active[key]
        if key in self._en:
            return self._en[key]
        return default if default is not None else key


def search_help_topics(query: str) -> list[HelpTopic]:
    q = " ".join(query.strip().lower().split())
    if not q:
        return list(HELP_TOPICS)
    terms = q.split(" ")
    scored: list[tuple[int, HelpTopic]] = []
    for topic in HELP_TOPICS:
        hay_title = topic.title.lower()
        hay_kw = " ".join(topic.keywords).lower()
        hay_body = f"{topic.category} {topic.summary} {topic.details}".lower()
        score = 0
        matched = True
        for term in terms:
            if term in hay_title:
                score += 6
            elif term in hay_kw:
                score += 4
            elif term in hay_body:
                score += 1
            else:
                matched = False
                break
        if matched and score > 0:
            scored.append((score, topic))
    scored.sort(key=lambda pair: (-pair[0], pair[1].title))
    return [t for _, t in scored]
