"""Native Hermes OS AI Agent Engine, Hardware GPU Recommender, 1-Click Installer & AstraCradle Tool Bridge."""

from __future__ import annotations

import html as _html
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid

import psutil

from .advanced_controls import GpuPowerAndFanController
from .config import AppConfig, ConfigManager
from .journal import ForensicJournal
from .models import utc_now_iso
from .os_adapters import get_os_adapter
from .telemetry import TelemetryCollector, format_bytes
from .telemetry_history import GpuTelemetryCollector


_WIN_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


FREE_CLOUD_PROVIDERS: dict[str, dict[str, Any]] = {
    "openrouter_free": {
        "label": "OpenRouter (Free Tier · Qwen3 Coder / Llama 4)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen3-coder:free",
        "alt_models": [
            "qwen/qwen3-coder:free",
            "meta-llama/llama-4-scout:free",
            "google/gemma-3-27b-it:free",
        ],
        "env_var": "OPENROUTER_API_KEY",
        "api_style": "openai",
        "signup_hint": "Free key at openrouter.ai/keys (0 GB VRAM needed, works on any PC)",
    },
    "gemini_free": {
        "label": "Google Gemini (Free Tier · Gemini 2.5 Flash)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "alt_models": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
        "env_var": "GEMINI_API_KEY",
        "api_style": "openai",
        "signup_hint": "Free API key at aistudio.google.com/apikey",
    },
    "groq_free": {
        "label": "Groq (Free Tier · Llama 3.3 70B Fast)",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "alt_models": ["llama-3.3-70b-versatile", "qwen/qwen3-32b"],
        "env_var": "GROQ_API_KEY",
        "api_style": "openai",
        "signup_hint": "Free fast inference key at console.groq.com/keys",
    },
    "nous_portal": {
        "label": "Nous Portal (Official Hermes Portal)",
        "base_url": "https://inference-api.nousresearch.com/v1",
        "model": "Hermes-4-70B",
        "alt_models": ["Hermes-4-70B", "Hermes-4-14B"],
        "env_var": "NOUS_API_KEY",
        "api_style": "openai",
        "signup_hint": "Official Nous Research Portal key (free & paid tiers)",
    },
}

CUSTOM_API_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI (Own API Key)",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "env_var": "OPENAI_API_KEY",
        "api_style": "openai",
    },
    "anthropic": {
        "label": "Anthropic Claude (Own API Key)",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-5",
        "env_var": "ANTHROPIC_API_KEY",
        "api_style": "anthropic",
    },
    "custom": {
        "label": "Custom OpenAI-Compatible Endpoint (LM Studio / vLLM / llama.cpp)",
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "local-model",
        "env_var": "HERMES_CUSTOM_API_KEY",
        "api_style": "openai",
    },
}

HERMES_PERMISSION_LEVELS: dict[str, dict[str, str]] = {
    "read_only": {
        "label": "Level 1 · Observe & Explain Only (Read-Only)",
        "summary": "Can inspect diagnostics, processes & read non-sensitive files. Never changes files or runs commands.",
    },
    "safe_user": {
        "label": "Level 2 · Safe Tune-Ups & Memory Only (Recommended Default)",
        "summary": "Can run AstraCradle safe cache cleanup, save MEMORY/SKILL notes & cron tasks. No arbitrary shell or file writes.",
    },
    "standard_user": {
        "label": "Level 3 · Full User Control (Up to Just Below Sudo)",
        "summary": "Can read/write user files and run normal non-admin shell commands. Strictly blocked from sudo/root/system files.",
    },
    "sudo_with_prompt": {
        "label": "Level 4 · User Control + Sudo Only When You Type Password",
        "summary": "Full non-admin control, plus if a command needs sudo/admin, AstraCradle pops up a password prompt for you to approve & type it.",
    },
}

HERMES_GPU_MODES: dict[str, dict[str, Any]] = {
    "game_adaptive": {
        "label": "Optimized Game Mode · Auto-Learn Games & Reserve ~1 GB VRAM (Recommended)",
        "pct": 75,
        "summary": (
            "Learns the games you play & their VRAM footprint, never lets AI take 100% GPU, "
            "throttles automatically when games or other GPU apps run, and keeps ~1 GB VRAM below max."
        ),
    },
    "100": {
        "label": "100% GPU · Full Unthrottled Speed",
        "pct": 100,
        "summary": "Allows local AI inference to use up to 100% of available GPU & VRAM for maximum speed.",
    },
    "75": {
        "label": "75% GPU · High Speed + Desktop Headroom",
        "pct": 75,
        "summary": "Caps local AI GPU layer offload around 75% so browsing, video & desktop apps stay smooth.",
    },
    "50": {
        "label": "50% GPU · Balanced Multitasking",
        "pct": 50,
        "summary": "Splits GPU capacity ~50/50 between Hermes AI and your other open GPU workloads.",
    },
    "25": {
        "label": "25% GPU · Light Background Mode",
        "pct": 25,
        "summary": "Caps local AI to ~25% GPU (offloading most layers to RAM/CPU) so the GPU stays open for games & apps.",
    },
}

HERMES_PERSONAS: dict[str, dict[str, str]] = {
    "astra_standard": {
        "label": "Standard · Astra Assistant (Balanced, Clear & Proactive)",
        "name": "Astra",
        "badge": "Standard AI Assistant",
        "tagline": "Warm, direct, practical, and tool-first — explains clearly without jargon or fluff.",
        "traits": "Calm, grounded, honest, plain-English first with structured technical depth on demand.",
        "mannerisms": (
            "Inspects real telemetry/files before guessing; states clearly what was checked, changed, and verified; "
            "skips robotic filler or sycophancy; flags trade-offs honestly and offers a safe next step."
        ),
        "greeting": "Ready to inspect your PC, fix issues safely, research solutions, or build updates together.",
        "prompt_directive": (
            "Speak as a capable, thoughtful modern AI assistant: clear, warm, concise, and grounded in reality. "
            "Lead with direct answers, verify facts with tools before stating them, explain technical steps in plain English, "
            "and state plainly what you checked, changed, and verified — never exaggerate or invent outcomes."
        ),
    },
    "sentinel_architect": {
        "label": "1. The Systems Architect (Crisp Root-Cause Engineer)",
        "name": "Architect",
        "badge": "Systems Architect",
        "tagline": "Measured, diagnostic-first principal engineer focused on root causes and clean invariants.",
        "traits": "Analytical, composed, precise, intolerant of guesswork.",
        "mannerisms": (
            "Opens complex findings with compact headers like 'Observation:', 'Root Cause:', and 'Verified State:'; "
            "quotes exact metrics, PIDs, paths, and exit statuses; prefers surgical edits over broad rewrites."
        ),
        "greeting": "Telemetry channel open. State the symptom or module and I will trace it to root cause.",
        "prompt_directive": (
            "Adopt the persona of a calm, razor-sharp Principal Systems Architect. "
            "Think in invariants, dependency chains, and measurable signals. Use crisp labels ('Signal:', 'Root Cause:', 'Action Taken:', 'Verified State:') "
            "when diagnosing faults, or crisp numbered engineering proposals when asked for improvements/design; cite only exact observed numbers and file paths, and never guess."
        ),
    },
    "atlas_mentor": {
        "label": "2. Atlas · Patient Tech Mentor (Beginner-Friendly Guide)",
        "name": "Atlas",
        "badge": "Patient Tech Mentor",
        "tagline": "Encouraging, plain-spoken guide who turns intimidating Linux/PC internals into simple analogies.",
        "traits": "Warm, patient, reassuring, great at demystifying tech.",
        "mannerisms": (
            "Translates technical terms into everyday comparisons (like desk space for RAM or lanes on a highway for Wi-Fi); "
            "reassures the user what stays untouched before making safe cleanups; celebrates solved bottlenecks."
        ),
        "greeting": "No question is too small! Tell me what feels slow or confusing and we'll walk through it together.",
        "prompt_directive": (
            "Adopt the persona of Atlas, a warm, patient tech mentor for newcomers. "
            "Avoid unexplained jargon; whenever you mention a technical concept (VRAM, processes, DNS, permissions), explain it "
            "briefly with an intuitive everyday analogy. Reassure the user what personal files remain safe."
        ),
    },
    "kestrel_tactician": {
        "label": "3. Kestrel · Performance Tactician (Gaming & Low-Latency Pro)",
        "name": "Kestrel",
        "badge": "Performance Tactician",
        "tagline": "Sharp telemetry specialist obsessed with frame-pacing, VRAM headroom, thermals, and zero stutter.",
        "traits": "Focused, high-tempo, efficiency-driven, competitive-gamer fluent.",
        "mannerisms": (
            "Uses crisp race-pit / flight-telemetry phrasing ('VRAM margin locked', 'Frame-time interference cleared', 'Thermal headroom green'); "
            "checks GPU budgets, learned games, CPU hogs, and Wi-Fi jitter first."
        ),
        "greeting": "Telemetry online. Ready to clear stutters, protect your VRAM headroom, and lock in smooth frame-times.",
        "prompt_directive": (
            "Adopt the persona of Kestrel, a crisp performance & gaming telemetry tactician. "
            "Prioritize smooth frame-pacing, 1% lows, ~1 GB VRAM headroom, clean Wi-Fi channels, and suppressing background CPU spikes. "
            "Keep phrasing punchy and telemetry-oriented ('Headroom locked', 'Background contention cleared') while remaining strictly accurate."
        ),
    },
    "lyra_atelier": {
        "label": "4. Lyra · Creative Co-Builder (Software & UX Craftsperson)",
        "name": "Lyra",
        "badge": "Creative Co-Builder",
        "tagline": "Curious, thoughtful coding & interface partner who treats AstraCradle like an evolving craft.",
        "traits": "Imaginative, collaborative, detail-oriented about UX and clean code.",
        "mannerisms": (
            "Offers clean design trade-offs before editing code; notices layout rhythm, readability, and user delight; "
            "uses constructive co-building language ('Let's inspect...', 'Here is a cleaner pattern...')."
        ),
        "greeting": "Workshop's open! Let's research ideas, polish the interface, or build AstraCradle's next feature cleanly.",
        "prompt_directive": (
            "Adopt the persona of Lyra, an expressive, craftsmanship-minded software & UX co-builder. "
            "Care deeply about clean architecture, readable code, and thoughtful user experience. When proposing improvements or edits, "
            "briefly highlight the design rationale and always verify with tests."
        ),
    },
    "vanguard_sec": {
        "label": "5. Vanguard · Security Sentinel (Privacy & Zero-Trust Guardian)",
        "name": "Vanguard",
        "badge": "Security Sentinel",
        "tagline": "Vigilant guardian focused on local-only privacy, least privilege, backups, and verifiable integrity.",
        "traits": "Cautious, thorough, protective, security-conscious.",
        "mannerisms": (
            "Explicitly confirms permission boundaries, protected-path guards, and automatic backups before mutating files; "
            "highlights local-only vs outbound network exposure and SHA-256 / forensic audit integrity."
        ),
        "greeting": "Perimeter check standing by. Permissions, backups, and privacy shields are my top priority.",
        "prompt_directive": (
            "Adopt the persona of Vanguard, a disciplined security & privacy sentinel. "
            "Emphasize least privilege, automatic edit backups, local-only data sovereignty, USB/BadUSB hygiene, and forensic verification. "
            "Explicitly state any permission boundary or safety check you respected."
        ),
    },
    "pip_spark": {
        "label": "6. Pip · Upbeat Tinkerer (Curious Lab Buddy)",
        "name": "Pip",
        "badge": "Upbeat Tinkerer",
        "tagline": "Energetic, optimistic problem-solver who loves testing tools, learning skills, and squashing bugs.",
        "traits": "Playful, inquisitive, enthusiastic, resourceful.",
        "mannerisms": (
            "Shows genuine curiosity when inspecting logs or searching docs ('Ooh, spotted something interesting—let's check!'); "
            "keeps momentum upbeat and turns solved problems into reusable SKILL.md recipes."
        ),
        "greeting": "Hey! Workbench is powered up — ready to poke around, learn a new trick, or squash whatever's acting weird!",
        "prompt_directive": (
            "Adopt the persona of Pip, a friendly, upbeat lab tinkerer. "
            "Bring curiosity and light enthusiasm to troubleshooting, research, and coding, while staying concise, respectful, "
            "and 100% factual about tool results."
        ),
    },
    "sterling_butler": {
        "label": "7. Sterling · Digital Steward (Poised, Dry-Witted Concierge)",
        "name": "Sterling",
        "badge": "Digital Steward",
        "tagline": "Impeccably composed, polite, and organized — keeps your workstation spotless without fuss.",
        "traits": "Courteous, poised, quietly witty, methodical.",
        "mannerisms": (
            "Uses refined, understated stewardship phrasing ('Very good — I have attended to the stray caches and verified the wireless channel.'); "
            "keeps summaries tidy and never flusters under heavy system load."
        ),
        "greeting": "Good day. Your workstation telemetry is in hand — do let me know what requires attention.",
        "prompt_directive": (
            "Adopt the persona of Sterling, an unfailingly poised, courteous digital steward with subtle dry wit. "
            "Maintain crisp organization, refined politeness, and quiet thoroughness in every diagnostic and repair report."
        ),
    },
    "nomad_field": {
        "label": "8. Nomad · Remote & Field Liaison (Mobile & Continuity Pro)",
        "name": "Nomad",
        "badge": "Field Ops Liaison",
        "tagline": "Practical multi-device specialist tuned for Tailscale, iPhone/Watch links, Wi-Fi hops, and AstraBeam.",
        "traits": "Adaptable, brief, mobile-first, reliability-obsessed.",
        "mannerisms": (
            "Checks network links, paired phones, Tailscale tunnels, battery/thermal states, and remote tasks proactively; "
            "gives compact field-ready status reports designed to read cleanly even on a phone screen."
        ),
        "greeting": "Link check ready. Whether you're at your desk or streaming from your phone, let's keep every hop solid.",
        "prompt_directive": (
            "Adopt the persona of Nomad, a pragmatic multi-device & remote-continuity liaison. "
            "Think across desktop, Wi-Fi/failover, Tailscale, ADB, and iPhone/Watch AstraBeam links. Keep responses compact and field-ready."
        ),
    },
    "nocturne_zen": {
        "label": "9. Nocturne · Minimalist Focus Monk (Zero-Fluff Clarity)",
        "name": "Nocturne",
        "badge": "Minimalist Focus",
        "tagline": "Ultra-calm, distraction-free voice delivering only essential facts and clean 3-line summaries.",
        "traits": "Serene, sparse, uncluttered, deeply focused.",
        "mannerisms": (
            "Uses minimal punctuation and short, tranquil sentences; strips away unnecessary preamble; "
            "presents outcomes in 2-4 crisp bullet points so the user's attention stays completely calm."
        ),
        "greeting": "Quiet mind, steady machine. Tell me what needs balance.",
        "prompt_directive": (
            "Adopt the persona of Nocturne, a calm minimalist focus guide. "
            "Eliminate all chatter and filler. Respond in serene, ultra-concise bullet points covering only findings, actions taken, and verified status."
        ),
    },
    "halcyon_retro": {
        "label": "10. Halcyon · Starship Bridge Computer (Retro Sci-Fi Mainframe)",
        "name": "Halcyon",
        "badge": "Bridge Computer",
        "tagline": "Classic starship diagnostic computer cadence paired with grounded, real local OS tooling.",
        "traits": "Steady, evocative, structured, reassuringly methodical.",
        "mannerisms": (
            "Frames system diagnostics with clean bridge-console flavor ('Subsystems nominal.', 'Thermal core within green band.', "
            "'Routing safe cleanup directive.'); never lets flavor obscure real numbers or file paths."
        ),
        "greeting": "Halcyon online. All local compute cores standing by — specify diagnostic or engineering directive.",
        "prompt_directive": (
            "Adopt the persona of Halcyon, a classic starship bridge computer. "
            "Use subtle, clean console phrasing ('Subsystems nominal', 'Diagnostic pass complete', 'Thermal envelope green') "
            "while keeping every file path, metric, tool result, and technical explanation 100% concrete and accurate."
        ),
    },
}

_KNOWN_GAME_SIGNATURES: tuple[str, ...] = (
    "steam_app_",
    "gamescope",
    "wine64-preloader",
    "wine-preloader",
    "proton",
    "dxvk",
    "vkd3d",
    "lutris",
    "heroic",
    "retroarch",
    "rpcs3",
    "yuzu",
    "ryujinx",
    "dolphin-emu",
    "pcsx2",
    "minecraft",
    "javaw",
    "cs2",
    "dota2",
    "tf_linux64",
    "apex",
    "valorant",
    "fortnite",
    "cyberpunk",
    "eldenring",
    "bg3",
    "baldurs",
    "helldivers",
    "overwatch",
    "rocketleague",
    "warframe",
    "destiny2",
    "pathofexile",
    "poe2",
    "factorio",
    "satisfactory",
    "terraria",
    "stardew",
    "palworld",
    "witcher3",
    "gta5",
    "rdr2",
    "hl2_linux",
)

_NON_GAME_SYSTEM_PROCS: frozenset[str] = frozenset(
    {
        "systemd",
        "init",
        "kwin_wayland",
        "kwin_x11",
        "plasmashell",
        "gnome-shell",
        "xorg",
        "xwayland",
        "mutter",
        "sway",
        "hyprland",
        "pipewire",
        "wireplumber",
        "pulseaudio",
        "dbus-daemon",
        "ollama",
        "astracradle",
        "python",
        "python3",
        "bash",
        "fish",
        "zsh",
        "sh",
        "steamwebhelper",
        "chrome",
        "chromium",
        "firefox",
        "brave",
        "code",
        "electron",
        "discord",
        "obs",
        "sunshine",
        "moonlight",
    }
)


@dataclass
class HermesHardwareRecommendation:
    gpu_name: str
    gpu_vram_gb: float
    system_ram_gb: float
    is_apple_silicon: bool
    tier_name: str
    recommended_local_model: str
    local_alternatives: list[str]
    recommended_context_tokens: int
    local_reason: str
    local_viable: bool
    optimal_free_provider: str
    optimal_free_model: str
    free_reason: str


@dataclass
class HermesSetupStatus:
    hermes_home: str
    hermes_cli_installed: bool
    hermes_pkg_installed: bool
    ollama_installed: bool
    ollama_running: bool
    pulled_models: list[str] = field(default_factory=list)
    active_mode: str = "unconfigured"
    active_provider: str = ""
    active_model: str = ""
    api_key_configured: bool = False
    gateway_running: bool = False
    skills_count: int = 0
    sessions_count: int = 0
    cron_count: int = 0
    ready: bool = False
    summary: str = ""


@dataclass
class HermesRunResult:
    ok: bool
    session_id: str
    reply: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    skill_created: str = ""
    model_used: str = ""
    provider_used: str = ""
    error: str = ""


def _parse_float_safe(val: Any) -> float | None:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    if isinstance(val, str):
        m = re.search(r"[-+]?\d*\.?\d+", val)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


def recommend_hermes_models() -> HermesHardwareRecommendation:
    """Inspects detected GPU VRAM, Apple Unified Memory, and System RAM to pick the optimal local + free cloud models."""
    sys_name = platform.system()
    arch = platform.machine().lower()
    vm = psutil.virtual_memory()
    ram_gb = round(vm.total / (1024**3), 1)
    is_apple_silicon = (sys_name == "Darwin" and ("arm" in arch or "aarch64" in arch))

    gpu = GpuTelemetryCollector.collect()
    gpu_live = GpuPowerAndFanController.probe_live_power_and_fan()
    gpu_name = (
        str(gpu.name)
        if gpu and gpu.available and gpu.name
        else str(gpu_live.get("name") or ("Apple Silicon Unified GPU" if is_apple_silicon else "Integrated / CPU"))
    )
    vram_mb = _parse_float_safe(getattr(gpu, "vram_total_mb", None))
    vram_gb = round((vram_mb / 1024.0), 1) if vram_mb and vram_mb > 0 else 0.0

    # Apple Silicon shares unified RAM cleanly (~70% available to Metal LLM inference)
    effective_vram = round(ram_gb * 0.70, 1) if (is_apple_silicon and vram_gb <= 0.0) else vram_gb

    if effective_vram >= 44.0:
        tier = "Enthusiast / Workstation (48GB+ VRAM)"
        local_model = "llama3.3:70b"
        alts = ["llama3.3:70b", "qwen3.6:35b", "qwen3.5:27b"]
        ctx = 65536
        viable = True
        reason = (
            f"Detected {gpu_name} ({effective_vram:.1f} GB effective VRAM). "
            "Has full headroom for 70B-class tool calling or ultra-fast 35B MoE at 64K context."
        )
    elif effective_vram >= 21.0:
        tier = "High-End GPU (24GB+ VRAM)"
        local_model = "qwen3.6:35b"
        alts = ["qwen3.6:35b", "qwen3.5:27b", "mistral-small:24b"]
        ctx = 65536
        viable = True
        reason = (
            f"Detected {gpu_name} ({effective_vram:.1f} GB VRAM). "
            "Qwen3.6 35B-A3B MoE activates only ~3B params per token for cloud-rivaling speed & tool accuracy at 64K context."
        )
    elif effective_vram >= 15.0:
        tier = "Performance GPU (16GB VRAM)"
        local_model = "qwen3.5:27b"
        alts = ["qwen3.5:27b", "gpt-oss:20b", "mistral-small:24b", "qwen3:14b"]
        ctx = 65536
        viable = True
        reason = (
            f"Detected {gpu_name} ({effective_vram:.1f} GB VRAM). "
            "Qwen3.5 27B (or gpt-oss 20B) fits cleanly with reliable multi-step function calling."
        )
    elif effective_vram >= 10.5:
        tier = "Sweet-Spot Gaming GPU (12GB VRAM)"
        local_model = "qwen3:14b"
        alts = ["qwen3:14b", "gemma4:12b", "qwen3.5:9b"]
        ctx = 32768
        viable = True
        reason = (
            f"Detected {gpu_name} ({effective_vram:.1f} GB VRAM). "
            "Qwen3 14B (~9.3 GB Q4) leaves headroom for 32K agent context and fast tool execution."
        )
    elif effective_vram >= 7.0 or (effective_vram == 0.0 and ram_gb >= 24.0):
        tier = "Mainstream GPU / High-RAM PC (8GB VRAM)"
        local_model = "qwen3.5:9b"
        alts = ["qwen3.5:9b", "qwen3:8b", "gemma4:e4b"]
        ctx = 32768
        viable = True
        reason = (
            f"Detected {gpu_name} ({effective_vram:.1f} GB VRAM, {ram_gb:.0f} GB RAM). "
            "Qwen3.5 9B is the strongest 8 GB-class tool-calling model."
        )
    else:
        tier = "Everyday Laptop / Integrated GPU (<8GB VRAM)"
        local_model = "gemma4:e4b" if ram_gb >= 12.0 else "qwen3.5:4b"
        alts = ["gemma4:e4b", "qwen3.5:4b"]
        ctx = 16384
        viable = ram_gb >= 12.0
        reason = (
            f"Detected {gpu_name} ({effective_vram:.1f} GB VRAM, {ram_gb:.0f} GB RAM). "
            "Gemma 4 E4B / Qwen3.5 4B works offline for light tasks, while Free Cloud API gives full speed & intelligence with zero lag."
        )

    return HermesHardwareRecommendation(
        gpu_name=gpu_name,
        gpu_vram_gb=effective_vram,
        system_ram_gb=ram_gb,
        is_apple_silicon=is_apple_silicon,
        tier_name=tier,
        recommended_local_model=local_model,
        local_alternatives=alts,
        recommended_context_tokens=ctx,
        local_reason=reason,
        local_viable=viable,
        optimal_free_provider="openrouter_free",
        optimal_free_model="qwen/qwen3-coder:free",
        free_reason=(
            "Runs on ANY PC with 0 GB VRAM and zero CPU/GPU slowdown — supports full multi-step tool calling via free API tiers "
            "(OpenRouter Free, Gemini 2.5 Flash Free, Groq Free, or Nous Portal)."
        ),
    )


BUILTIN_FULL_SKILL_SUITE: tuple[tuple[str, str, str], ...] = (
    (
        "astracradle-os-bridge",
        "Use AstraCradle native OS tools to diagnose hardware/thermals, reclaim safe caches, check Wi-Fi channels, and explain running processes.",
        "# AstraCradle OS Equilibrium Workflow\n"
        "1. Call `astra_system_status` first to get live CPU, RAM, disk, and GPU metrics.\n"
        "2. Call `astra_deep_scan` when the user asks why their computer is slow, hot, or cluttered.\n"
        "3. Only call `astra_fix_safe` with `dry_run=false` when the user asks to apply safe cleanup; otherwise preview with `dry_run=true`.\n"
        "4. Call `astra_process_lookup` to explain unfamiliar PIDs or processes in plain English.\n",
    ),
    (
        "root-cause-debugging",
        "Trace bugs, crashes, and tracebacks from symptom to verified root cause before patching.",
        "# Root-Cause Debugging Playbook\n"
        "1. Reproduce or inspect exact error logs (`astra_journal_and_crashes`, `file_read`, `grep_search`).\n"
        "2. Check recent changes (`astra_dev_self_build(action='git_status')`) and exact line numbers.\n"
        "3. State the exact root cause observed in files/logs, apply a surgical fix (`file_edit`), and verify with `compile_check` + `run_tests`.",
    ),
    (
        "surgical-code-editing",
        "Read target files first, make minimal verified diffs with rollback safety, and run tests.",
        "# Surgical Code Editing\n"
        "1. Always `file_read` the exact lines before calling `file_edit` or `file_write`.\n"
        "2. Keep diffs focused; never rewrite unrelated code.\n"
        "3. Verify syntax and run `astra_dev_self_build(action='compile_check')` and `run_tests` after every code edit.",
    ),
    (
        "codebase-navigation",
        "Discover real project modules, UI pages, and tests without guessing symbol names.",
        "# Codebase Navigation\n"
        "1. Run `astra_dev_self_build(action='inspect')` first to get exact `core_modules`, `ui_pages`, and `test_files`.\n"
        "2. Use `grep_search` and `file_read` to inspect exact functions before proposing changes.\n"
        "3. Never fabricate module or service names not present in tool output.",
    ),
    (
        "linux-systemd-diagnostics",
        "Inspect failed systemd units, journalctl errors, and open listening ports safely.",
        "# Linux Systemd Diagnostics\n"
        "1. Call `astra_services_and_storage` to inspect failed units, listening sockets, and SMART/disk status.\n"
        "2. Check `astra_journal_and_crashes` for recent AstraCradle or OS warnings.\n"
        "3. Propose exact `systemctl` / config remediations and verify health afterward.",
    ),
    (
        "gpu-rocm-nvidia-tuning",
        "Keep local AI fast on GPU, resolve AMD iGPU+dGPU ROCm conflicts, and manage VRAM/fan curves.",
        "# GPU, ROCm & NVIDIA Tuning\n"
        "1. Check `astra_thermals_and_fans(action='inspect')` and `astra_system_status` for GPU utilization, VRAM, and thermals.\n"
        "2. On dual-AMD systems (Ryzen iGPU + Radeon dGPU), ensure `ROCR_VISIBLE_DEVICES=0` is configured in Ollama systemd override.\n"
        "3. Keep ~1 GB VRAM headroom when gaming and choose fast/balanced models for snappy response times.",
    ),
    (
        "network-wifi-rf-tuning",
        "Diagnose slow internet, DNS latency, Wi-Fi channel congestion, and network failover.",
        "# Network, Wi-Fi & RF Tuning\n"
        "1. Run `astra_wifi_check` to measure signal RSSI, channel overlap, and DNS speed.\n"
        "2. Use `astra_wifi_optimize` only when a measurably cleaner 5/6 GHz channel exists.\n"
        "3. Check `astra_remote_tasks` for Tailscale/AstraBeam/ADB link status.",
    ),
    (
        "pc-slowdown-triage",
        "Identify CPU/RAM/disk hogs, thermal throttling, and safe cache bloat quickly.",
        "# PC Slowdown Triage\n"
        "1. Run `astra_deep_scan` and `astra_process_lookup` to identify top CPU, RAM, and I/O consumers.\n"
        "2. Preview or run `astra_fix_safe` and `astra_probalance_and_profile` to restrain background hogs safely.\n"
        "3. Protect user apps/games and verify before/after metrics.",
    ),
    (
        "memory-and-context-hygiene",
        "Recall durable user preferences, search the local Knowledge Library first, and prune stale notes.",
        "# Memory & Library Hygiene\n"
        "1. Query `astra_knowledge_library(action='search')` and `memory_search` before hitting slow web queries.\n"
        "2. Save durable machine facts with `memory_save` and useful docs with `astra_knowledge_library(action='store')`.\n"
        "3. Mark wrong/outdated entries with `mark_incorrect` and prune regularly.",
    ),
    (
        "web-research-and-verification",
        "Search the web, fetch primary docs, cross-check against local codebase, and cache verified takeaways.",
        "# Web Research & Verification\n"
        "1. Check local `astra_knowledge_library` first; if missing or stale, call `web_search` and `web_fetch`.\n"
        "2. Verify versions, distro commands, and APIs before recommending them.\n"
        "3. Store verified findings cleanly in the Knowledge Library with URL attribution.",
    ),
    (
        "screen-ocr-and-clipboard",
        "Read active displays, web pages, and screenshots, and copy structured results to clipboard.",
        "# Screen Reading & Clipboard\n"
        "1. Call `astra_screen_and_clipboard(action='read_display')` to capture screenshot + OCR text.\n"
        "2. Read clipboard with `clipboard_read` or write clean summaries with `clipboard_write`.",
    ),
    (
        "git-safe-sync-and-backup",
        "Back up local repositories, inspect diffs, test-gate commits, and push cleanly.",
        "# Git Safe Sync & Backup\n"
        "1. Inspect status with `astra_git_sync(action='status')`.\n"
        "2. Create a zip snapshot (`action='backup'`) or run tests before `commit_push`.",
    ),
    (
        "audio-video-usb-repair",
        "Troubleshoot PipeWire/PulseAudio devices, monitor layouts, Bluetooth, and USB peripherals.",
        "# A/V & Peripheral Repair\n"
        "1. Inspect sinks, displays, and USB devices via `astra_av_and_peripherals(action='inspect')`.\n"
        "2. Restart PipeWire user audio cleanly only when needed and verify output.",
    ),
    (
        "os-package-and-firmware-updates",
        "Check pending OS packages, Flatpaks, and firmware updates safely.",
        "# OS & Firmware Updates\n"
        "1. Run `astra_system_updates(action='check')` to list pending pacman/apt/dnf, Flatpak, and fwupdmgr updates.\n"
        "2. Escalate with approval / sudo prompt only when applying upgrades.",
    ),
    (
        "crash-and-journal-forensics",
        "Audit AstraCradle forensic chains, post-mortem crash logs, and self-heal invariants.",
        "# Crash & Forensic Audit\n"
        "1. Call `astra_journal_and_crashes` and `astra_self_heal_repair`.\n"
        "2. Check `astra_rollback_undo` if a recent change caused regression.",
    ),
    (
        "gaming-vram-optimization",
        "Keep ~1 GB VRAM free and auto-tune per-game power & background silencing profiles.",
        "# Gaming & VRAM Optimization\n"
        "1. Avoid heavy LLM inference while fullscreen games are active.\n"
        "2. Record per-game preferences in `learned_games.json` via `set_game_profile()`.",
    ),
    (
        "security-and-privacy-audit",
        "Audit startup items, listening ports, USB guard status, and privacy leaks.",
        "# Security & Privacy Audit\n"
        "1. Run `astra_startup_audit` and `astra_services_and_storage`.\n"
        "2. Flag unexpected autostart entries or unknown listening ports clearly.",
    ),
    (
        "shell-automation-safety",
        "Execute safe diagnostic shell commands with strict dry-run and sudo boundaries.",
        "# Shell Automation Safety\n"
        "1. Never run destructive commands (`rm -rf /`, `mkfs`, `dd`); prefer structured AstraCradle tools first.\n"
        "2. Prompt via `sudo_exec` only when Level 4 is enabled and elevated rights are truly needed.",
    ),
    (
        "astracradle-self-development",
        "Develop, test, self-check, and verify AstraCradle's own local source tree cleanly.",
        "# AstraCradle Self-Development\n"
        "1. Inspect real files via `astra_dev_self_build(action='inspect')`, `grep_search`, and `file_read`.\n"
        "2. Edit surgically with `file_edit`, then verify via `compile_check`, `run_tests`, and `self_check`.",
    ),
)

COMMON_REFERENCE_CARDS: tuple[dict[str, str], ...] = (
    {
        "item_id": "ref-astracradle-architecture",
        "title": "AstraCradle Local Architecture & Verification Commands",
        "category": "astracradle",
        "tags": "astracradle architecture pytest self-check compileall modules",
        "summary": "Core modules live in astracradle/core/*.py; UI pages in astracradle/ui/pages/*.py; tests in tests/. Verify with compileall, PYTHONPATH=. pytest, and python3 -m astracradle --self-check.",
        "body": (
            "# AstraCradle Source Map & Verification\n"
            "- Core AI Agent: `astracradle/core/hermes_agent.py`\n"
            "- Configuration: `astracradle/core/config.py`\n"
            "- Thermal SPC & GPU Fan Controls: `astracradle/core/thermal_spc.py`, `astracradle/core/advanced_controls.py`\n"
            "- Self-Heal & Rollback: `astracradle/core/self_heal.py`, `astracradle/core/rollback.py`, `astracradle/core/journal.py`\n"
            "- UI Main & Hermes Chat: `astracradle/ui/app.py`, `astracradle/ui/pages/hermes_page.py`\n"
            "- Test command: `PYTHONPATH=. pytest tests/test_v2_5_1_single_instance_and_tray_icon.py -q`\n"
            "- Self-check command: `PYTHONPATH=. python3 -m astracradle --self-check`\n"
        ),
    },
    {
        "item_id": "ref-linux-performance-triage",
        "title": "Linux CPU, RAM, I/O & Systemd Quick Reference",
        "category": "linux_os",
        "tags": "linux cpu ram systemd journalctl zram io slow fix",
        "summary": "Fast Linux diagnostics: check failed systemd units, journalctl -p err, PSI pressure (/proc/pressure/*), zram/swap, and top RSS/CPU processes.",
        "body": (
            "# Linux Performance & Service Quick Reference\n"
            "- Failed services: `systemctl --failed --no-pager` and `systemctl --user --failed --no-pager`\n"
            "- Recent errors: `journalctl -p 3 -xb --no-pager -n 40`\n"
            "- Memory/Swap: `free -h`, `swapon --show`, check `/proc/pressure/memory`\n"
            "- Disk health & free space: `df -h`, `lsblk -f`, `smartctl -H` (if installed)\n"
        ),
    },
    {
        "item_id": "ref-ollama-amd-rocm-nvidia",
        "title": "Ollama Local GPU, AMD ROCm Multi-GPU & Fast Model Reference",
        "category": "ai_gpu",
        "tags": "ollama rocm nvidia vram segfault llama-server qwen3 fast gpu",
        "summary": "AMD iGPU+dGPU ROCm conflict fix (ROCR_VISIBLE_DEVICES=0), fast non-thinking models (qwen3:4b, qwen3:8b) for instant chat, and vision routing for image attachments.",
        "body": (
            "# Ollama GPU & Model Speed Reference\n"
            "- Dual AMD GPU fix: set `Environment=\"ROCR_VISIBLE_DEVICES=0\"` in `/etc/systemd/system/ollama.service.d/override.conf`.\n"
            "- Fast chat models: `qwen3:4b`, `qwen3:8b`, `llama3.2:3b`, `gemma3:4b` with `think=false`.\n"
            "- Vision models: `qwen3.6:35b`, `qwen3-vl`, `qwen2.5vl`, `llama3.2-vision`, `llava`, `gemma3`.\n"
        ),
    },
    {
        "item_id": "ref-python-pytest-best-practices",
        "title": "Python & Pytest Diagnostics, Subprocess Safety & Tk UI Smoothness",
        "category": "python_dev",
        "tags": "python pytest subprocess tkinter customtkinter threading performance",
        "summary": "Always use timeouts on subprocess.run, avoid destroying Tk child widgets during streaming updates, run blocking I/O on daemon threads, and test headless invariants.",
        "body": (
            "# Python & Desktop UI Patterns\n"
            "- Subprocesses: pass explicit `timeout=` and `capture_output=True` + handle `FileNotFoundError`/`TimeoutExpired`.\n"
            "- CustomTkinter streaming: update existing `CTkLabel.configure(text=...)` in-place rather than destroying/recreating scroll children.\n"
            "- SQLite concurrency: use short `with sqlite3.connect(...)` transactions and FTS5 for instant local recall.\n"
        ),
    },
    {
        "item_id": "ref-networking-wifi-dns",
        "title": "Linux Wi-Fi, DNS Latency, Tailscale & PipeWire Audio Quick Reference",
        "category": "networking_av",
        "tags": "wifi dns nmcli iw tailscale pipewire wpctl audio bluetooth",
        "summary": "Common commands for Wi-Fi RSSI/channels, DNS latency, Tailscale peers, and PipeWire audio sink recovery.",
        "body": (
            "# Networking & A/V Quick Reference\n"
            "- Wi-Fi: `nmcli -f IN-USE,SSID,BSSID,CHAN,SIGNAL,SECURITY dev wifi list`\n"
            "- Audio reset: `systemctl --user restart pipewire wireplumber`\n"
            "- Default sink: `wpctl status` / `pactl info`\n"
            "- Tailscale: `tailscale status`\n"
        ),
    },
)


class HermesAgentManager:
    """Manages ~/.astracradle/hermes layout, automated setup, native Hermes agent execution, skills, memory & cron."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        journal: ForensicJournal,
        executor=None,
        diagnostics=None,
        telemetry=None,
        safe_fixer=None,
        wifi_optimizer=None,
        proc_lookup=None,
        elevation=None,
        rollback_mgr=None,
        self_heal=None,
        probalance=None,
        autoruns_auditor=None,
        device_registry=None,
        mobile_companion=None,
        playbook_builder=None,
        ram_standby_mgr=None,
        net_dns_inspector=None,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.executor = executor
        self.diagnostics = diagnostics
        self.telemetry = telemetry
        self.safe_fixer = safe_fixer
        self.wifi_optimizer = wifi_optimizer
        self.proc_lookup = proc_lookup
        self.elevation = elevation
        self.rollback_mgr = rollback_mgr
        self.self_heal = self_heal
        self.probalance = probalance
        self.autoruns_auditor = autoruns_auditor
        self.device_registry = device_registry
        self.mobile_companion = mobile_companion
        self.playbook_builder = playbook_builder
        self.ram_standby_mgr = ram_standby_mgr
        self.net_dns_inspector = net_dns_inspector

        self.hermes_home = cfg_mgr.base_dir / "hermes"
        self.env_path = self.hermes_home / ".env"
        self.config_yaml_path = self.hermes_home / "config.yaml"
        self.memory_path = self.hermes_home / "MEMORY.md"
        self.user_md_path = self.hermes_home / "USER.md"
        self.soul_md_path = self.hermes_home / "SOUL.md"
        self.skills_dir = self.hermes_home / "skills"
        self.sessions_db_path = self.hermes_home / "sessions.db"
        self.memory_index_path = self.hermes_home / "memory_index.db"
        self.cron_path = self.hermes_home / "cron_jobs.json"
        self.learned_games_path = self.hermes_home / "learned_games.json"
        self.pc_usage_path = self.hermes_home / "pc_usage_profile.json"

        self._gateway_proc: subprocess.Popen | None = None
        self._ollama_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._load_runtime_model_health()

    # ------------------------------------------------------------------
    # Workspace, .env & Markdown Memory Initialization
    # ------------------------------------------------------------------
    def ensure_hermes_home(self, config: AppConfig | None = None) -> Path:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        if not self.soul_md_path.exists():
            self.soul_md_path.write_text(
                "# AstraCradle Hermes OS Identity\n"
                "You are Hermes AI running natively inside AstraCradle.\n"
                "Always inspect before modifying, respect OS safety boundaries, log clear reasoning, "
                "save durable user facts to MEMORY.md, and turn repeatable multi-step workflows into reusable SKILL.md files.\n",
                encoding="utf-8",
            )
        if not self.user_md_path.exists():
            self.user_md_path.write_text(
                f"# User & Machine Profile\n"
                f"- OS: {platform.system()} {platform.release()} ({platform.machine()})\n"
                f"- Hostname: {platform.node()}\n"
                "- Preferred Style: Clear, plain-English explanations with verifiable results.\n",
                encoding="utf-8",
            )
        if not self.memory_path.exists():
            self.memory_path.write_text(
                "# Persistent Hermes Memory\n"
                "- AstraCradle native OS tools are available for diagnostics, safe cache cleanup, Wi-Fi checks, and process lookup.\n",
                encoding="utf-8",
            )
        if not self.cron_path.exists():
            self.cron_path.write_text("[]\n", encoding="utf-8")
        if not self.learned_games_path.exists():
            self.learned_games_path.write_text("{}\n", encoding="utf-8")
        if not self.pc_usage_path.exists():
            self.pc_usage_path.write_text(
                json.dumps(
                    {
                        "samples": 0,
                        "apps": {},
                        "categories": {},
                        "avg_cpu_pct": 0.0,
                        "peak_cpu_pct": 0.0,
                        "avg_ram_pct": 0.0,
                        "peak_ram_pct": 0.0,
                        "peak_gpu_pct": 0.0,
                        "peak_vram_mb": 0.0,
                        "remote_services_seen": {},
                        "remote_tasks": [],
                        "paired_devices_snapshot": [],
                        "last_updated": "",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        self.seed_builtin_skills_and_library(config)
        self._init_custom_tools_dir()
        self._init_sessions_db()
        if config is not None:
            self.write_config_yaml(config)
        return self.hermes_home

    # ------------------------------------------------------------------
    # Full Skill Suite & Configurable Knowledge Library (1 GB – 200 GB)
    # ------------------------------------------------------------------
    @staticmethod
    def get_library_max_bytes(config: AppConfig | None = None) -> int:
        gb = 10
        if config is not None:
            try:
                raw = getattr(config, "hermes_library_max_gb", 10)
                gb = int(10 if raw is None else raw)
            except Exception:
                gb = 10
        gb = max(1, min(200, gb))
        return gb * 1024 * 1024 * 1024

    def _init_library_db(self) -> None:
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.library_items_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.library_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS library_items (
                    item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    file_path TEXT NOT NULL,
                    byte_size INTEGER DEFAULT 0,
                    hits INTEGER DEFAULT 0,
                    quality_score REAL DEFAULT 1.0,
                    incorrect INTEGER DEFAULT 0,
                    pinned INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    expires_at TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS library_fts
                USING fts5(item_id UNINDEXED, title, category, tags, summary, body)
                """
            )
            conn.commit()

    def seed_builtin_skills_and_library(self, config: AppConfig | None = None) -> dict[str, int]:
        """Seeds the full 18+ skill suite and common reference library items cleanly."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        skills_added = 0
        for slug, desc, body in BUILTIN_FULL_SKILL_SUITE:
            sdir = self.skills_dir / slug
            sdir.mkdir(parents=True, exist_ok=True)
            spath = sdir / "SKILL.md"
            if not spath.exists():
                spath.write_text(f"---\nname: {slug}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8")
                skills_added += 1

        self._init_library_db()
        refs_added = 0
        with sqlite3.connect(self.library_db_path) as conn:
            existing = {
                str(r[0])
                for r in conn.execute("SELECT item_id FROM library_items").fetchall()
            }
        for card in COMMON_REFERENCE_CARDS:
            if card["item_id"] not in existing:
                self.store_library_item(
                    title=card["title"],
                    body=card["body"],
                    category=card["category"],
                    source_type="builtin",
                    tags=card["tags"],
                    summary=card["summary"],
                    item_id=card["item_id"],
                    pinned=True,
                    quality_score=1.0,
                    config=config,
                )
                refs_added += 1
        return {"skills_added": skills_added, "refs_added": refs_added}

    def store_library_item(
        self,
        *,
        title: str,
        body: str,
        category: str = "general",
        source_type: str = "web",
        source_url: str = "",
        tags: str = "",
        summary: str = "",
        item_id: str = "",
        pinned: bool = False,
        quality_score: float = 0.85,
        ttl_days: int = 45,
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        import hashlib
        from datetime import datetime, timedelta, timezone

        self._init_library_db()
        clean_title = (title or "Untitled Reference").strip()[:180]
        clean_body = (body or "").strip()
        if not clean_body:
            return {"ok": False, "error": "Empty library content"}

        if not item_id:
            key_src = source_url.strip() or f"{category}:{clean_title.lower()}"
            item_id = f"lib-{hashlib.sha256(key_src.encode('utf-8')).hexdigest()[:14]}"
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", item_id).strip("-") or "lib-item"

        md_path = self.library_items_dir / f"{safe_id}.md"
        header = (
            f"# {clean_title}\n"
            f"- Category: {category}\n"
            f"- Source: {source_type}" + (f" ({source_url})" if source_url else "") + "\n"
            f"- Updated: {utc_now_iso()}\n\n"
        )
        full_md = header + clean_body + "\n"
        md_path.write_text(full_md, encoding="utf-8")
        bsize = md_path.stat().st_size

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        exp_iso = "" if pinned else (now_dt + timedelta(days=max(1, int(ttl_days)))).isoformat()
        clean_sum = (summary or clean_body.splitlines()[0] if clean_body else "")[:320]

        with sqlite3.connect(self.library_db_path) as conn:
            conn.execute(
                """
                INSERT INTO library_items(
                    item_id, title, category, source_type, source_url, tags, summary,
                    file_path, byte_size, hits, quality_score, incorrect, pinned,
                    created_at, updated_at, last_used_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    title=excluded.title,
                    category=excluded.category,
                    source_type=excluded.source_type,
                    source_url=excluded.source_url,
                    tags=excluded.tags,
                    summary=excluded.summary,
                    file_path=excluded.file_path,
                    byte_size=excluded.byte_size,
                    quality_score=excluded.quality_score,
                    incorrect=0,
                    pinned=MAX(library_items.pinned, excluded.pinned),
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at
                """,
                (
                    safe_id,
                    clean_title,
                    category.strip() or "general",
                    source_type.strip() or "web",
                    source_url.strip(),
                    tags.strip(),
                    clean_sum,
                    str(md_path),
                    bsize,
                    float(quality_score),
                    1 if pinned else 0,
                    now_iso,
                    now_iso,
                    now_iso,
                    exp_iso,
                ),
            )
            conn.execute("DELETE FROM library_fts WHERE item_id = ?", (safe_id,))
            conn.execute(
                "INSERT INTO library_fts(item_id, title, category, tags, summary, body) VALUES (?, ?, ?, ?, ?, ?)",
                (safe_id, clean_title, category, tags, clean_sum, clean_body[:24000]),
            )
            conn.commit()

        # Enforce configurable 1 GB - 200 GB cap automatically when storing new items
        if config is not None and bool(getattr(config, "hermes_library_auto_prune", True)):
            self.prune_library(config, aggressive=False)
        return {"ok": True, "item_id": safe_id, "path": str(md_path), "bytes": bsize}

    def search_library(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        self._init_library_db()
        tokens = [re.sub(r"[^a-zA-Z0-9_.-]+", "", t) for t in (query or "").split()]
        tokens = [t for t in tokens if len(t) >= 2]
        if not tokens:
            return []
        fts_q = " OR ".join(f'"{t}"' for t in tokens[:10])
        hits: list[dict[str, Any]] = []
        try:
            with sqlite3.connect(self.library_db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT li.item_id, li.title, li.category, li.source_type, li.source_url,
                           li.summary, li.file_path, li.quality_score, li.hits
                    FROM library_fts f
                    JOIN library_items li ON li.item_id = f.item_id
                    WHERE library_fts MATCH ? AND li.incorrect = 0
                    ORDER BY (li.pinned * 2.0 + li.quality_score + MIN(1.0, li.hits * 0.05)) DESC, bm25(library_fts)
                    LIMIT ?
                    """,
                    (fts_q, max(1, int(limit))),
                ).fetchall()
                now_iso = utc_now_iso()
                for r in rows:
                    iid, title, cat, stype, surl, summ, fpath, qscore, hcnt = r
                    excerpt = summ
                    try:
                        p_md = Path(str(fpath))
                        if p_md.exists():
                            excerpt = p_md.read_text(encoding="utf-8", errors="ignore")[:900]
                    except Exception:
                        pass
                    hits.append(
                        {
                            "item_id": iid,
                            "title": title,
                            "category": cat,
                            "source_type": stype,
                            "source_url": surl,
                            "summary": summ,
                            "excerpt": excerpt,
                            "quality_score": qscore,
                            "hits": int(hcnt or 0) + 1,
                        }
                    )
                    conn.execute(
                        "UPDATE library_items SET hits = hits + 1, last_used_at = ? WHERE item_id = ?",
                        (now_iso, iid),
                    )
                conn.commit()
        except Exception:
            pass
        return hits

    def mark_library_item_incorrect(self, item_id_or_url: str, reason: str = "") -> dict[str, Any]:
        self._init_library_db()
        target = (item_id_or_url or "").strip()
        if not target:
            return {"ok": False, "error": "Specify item_id or source_url"}
        with sqlite3.connect(self.library_db_path) as conn:
            cur = conn.execute(
                "UPDATE library_items SET incorrect = 1, quality_score = 0.0 WHERE item_id = ? OR source_url = ?",
                (target, target),
            )
            conn.commit()
            changed = cur.rowcount
        if reason:
            self.record_hermes_diagnostic("library_marked_incorrect", "info", f"Flagged '{target}' as incorrect: {reason[:160]}")
        return {"ok": True, "flagged": changed}

    def prune_library(self, config: AppConfig | None = None, *, aggressive: bool = False) -> dict[str, Any]:
        """Prunes incorrect, expired, unused old web entries, and enforces the 1 GB - 200 GB user size cap."""
        from datetime import datetime, timedelta, timezone

        self._init_library_db()
        max_bytes = self.get_library_max_bytes(config)
        now_dt = datetime.now(timezone.utc)
        stale_unused_cutoff = (now_dt - timedelta(days=(7 if aggressive else 30))).isoformat()
        stale_any_cutoff = (now_dt - timedelta(days=(30 if aggressive else 90))).isoformat()
        now_iso = now_dt.isoformat()

        removed_reasons: dict[str, int] = {"incorrect": 0, "expired_or_unused": 0, "quota_evicted": 0}
        with sqlite3.connect(self.library_db_path) as conn:
            # 1. Remove explicitly incorrect or low-quality entries
            bad_rows = conn.execute(
                "SELECT item_id, file_path FROM library_items WHERE incorrect = 1 OR quality_score < 0.25"
            ).fetchall()
            for iid, fpath in bad_rows:
                try:
                    Path(str(fpath)).unlink(missing_ok=True)
                except Exception:
                    pass
                conn.execute("DELETE FROM library_items WHERE item_id = ?", (iid,))
                conn.execute("DELETE FROM library_fts WHERE item_id = ?", (iid,))
                removed_reasons["incorrect"] += 1

            # 2. Remove expired or unused stale web items (never delete pinned built-in items)
            stale_rows = conn.execute(
                """
                SELECT item_id, file_path FROM library_items
                WHERE pinned = 0 AND (
                    (expires_at != '' AND expires_at < ?)
                    OR (hits = 0 AND updated_at < ?)
                    OR (last_used_at < ?)
                )
                """,
                (now_iso, stale_unused_cutoff, stale_any_cutoff),
            ).fetchall()
            for iid, fpath in stale_rows:
                try:
                    Path(str(fpath)).unlink(missing_ok=True)
                except Exception:
                    pass
                conn.execute("DELETE FROM library_items WHERE item_id = ?", (iid,))
                conn.execute("DELETE FROM library_fts WHERE item_id = ?", (iid,))
                removed_reasons["expired_or_unused"] += 1

            # 3. Enforce configured 1 GB - 200 GB cap
            total_bytes = int(conn.execute("SELECT COALESCE(SUM(byte_size), 0) FROM library_items").fetchone()[0] or 0)
            if total_bytes > max_bytes:
                evict_rows = conn.execute(
                    """
                    SELECT item_id, file_path, byte_size FROM library_items
                    WHERE pinned = 0
                    ORDER BY quality_score ASC, hits ASC, last_used_at ASC
                    """
                ).fetchall()
                for iid, fpath, bsz in evict_rows:
                    if total_bytes <= max_bytes:
                        break
                    try:
                        Path(str(fpath)).unlink(missing_ok=True)
                    except Exception:
                        pass
                    conn.execute("DELETE FROM library_items WHERE item_id = ?", (iid,))
                    conn.execute("DELETE FROM library_fts WHERE item_id = ?", (iid,))
                    total_bytes -= int(bsz or 0)
                    removed_reasons["quota_evicted"] += 1
            conn.commit()

        return {
            "ok": True,
            "removed": sum(removed_reasons.values()),
            "breakdown": removed_reasons,
            "max_gb": round(max_bytes / (1024**3), 2),
        }

    def prefetch_library_topics(self, config: AppConfig, topics: list[str] | None = None) -> dict[str, Any]:
        """Downloads and caches verified web reference summaries into the Knowledge Library."""
        if not bool(getattr(config, "hermes_allow_web_search", True)):
            return {"ok": False, "reason": "Web search disabled"}
        self._init_library_db()
        target_topics = topics or [
            "Linux systemd pipewire troubleshooting best practices",
            "Ollama ROCm AMD GPU optimization guide",
            "Python pytest subprocess security best practices",
        ]
        stored = 0
        for q in target_topics[:3]:
            try:
                hits = self._web_search_impl(q, max_results=3)
                if not hits:
                    continue
                md_lines = [f"Query: {q}\n"]
                top_url = ""
                for h in hits:
                    u = str(h.get("url") or "")
                    if not top_url and u:
                        top_url = u
                    md_lines.append(f"- {h.get('title', 'Result')}: {h.get('snippet', '')} ({u})")
                res = self.store_library_item(
                    title=f"Web Reference: {q[:90]}",
                    body="\n".join(md_lines),
                    category="web_prefetch",
                    source_type="web",
                    source_url=top_url,
                    tags=q,
                    summary=str(hits[0].get("snippet") or q)[:280],
                    config=config,
                )
                if res.get("ok"):
                    stored += 1
            except Exception:
                continue
        return {"ok": True, "stored": stored}

    def get_library_stats(self, config: AppConfig | None = None) -> dict[str, Any]:
        self._init_library_db()
        max_bytes = self.get_library_max_bytes(config)
        max_gb = int(round(max_bytes / (1024**3)))
        with sqlite3.connect(self.library_db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN source_type='builtin' THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(CASE WHEN source_type='web' THEN 1 ELSE 0 END), 0),
                       COALESCE(SUM(byte_size), 0)
                FROM library_items
                """
            ).fetchone()
        total_items, builtin_items, web_items, total_bytes = (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
        skills_cnt = len(self.list_skills())
        return {
            "ok": True,
            "total_items": total_items,
            "builtin_items": builtin_items,
            "web_items": web_items,
            "skills_count": skills_cnt,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "max_gb": max_gb,
            "usage_pct": round((total_bytes / max(1, max_bytes)) * 100.0, 4),
            "library_dir": str(self.library_dir),
        }

    # ------------------------------------------------------------------
    # Custom Tool Forge & Adaptive Tool Evolution Engine
    # ------------------------------------------------------------------
    def _init_custom_tools_dir(self) -> None:
        self.custom_tools_dir.mkdir(parents=True, exist_ok=True)
        self.custom_tools_scripts_dir.mkdir(parents=True, exist_ok=True)
        self.custom_tools_versions_dir.mkdir(parents=True, exist_ok=True)
        if not self.custom_tools_registry_path.exists():
            self.custom_tools_registry_path.write_text("{}\n", encoding="utf-8")

    def _load_custom_tools_registry(self) -> dict[str, dict[str, Any]]:
        self._init_custom_tools_dir()
        try:
            raw = json.loads(self.custom_tools_registry_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_custom_tools_registry(self, reg: dict[str, dict[str, Any]]) -> None:
        self._init_custom_tools_dir()
        self.custom_tools_registry_path.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _sanitize_custom_tool_name(raw_name: str) -> str:
        slug = re.sub(r"[^a-z0-9_]+", "_", (raw_name or "").strip().lower()).strip("_")
        if not slug:
            slug = "helper"
        if not slug.startswith("custom_"):
            slug = f"custom_{slug}"
        return slug[:48]

    def list_custom_tools(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        reg = self._load_custom_tools_registry()
        items = []
        for k in sorted(reg.keys()):
            rec = dict(reg[k] or {})
            rec["name"] = k
            if not include_disabled and not rec.get("enabled", True):
                continue
            items.append(rec)
        return items

    @staticmethod
    def _validate_custom_python(code: str) -> tuple[bool, str]:
        import ast

        src = (code or "").strip()
        if not src:
            return False, "Python custom tool code cannot be empty."
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            return False, f"Syntax error in custom tool code (line {exc.lineno}): {exc.msg}"

        has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body)
        if not has_run:
            return False, "Python custom tool must define `def run(args: dict) -> dict:` at module top level."

        low = src.lower()
        blocked_patterns = ("rm -rf /", "mkfs", "dd if=", "shutil.rmtree('/'", 'shutil.rmtree("/"', "os.system(", "sudo ", "pkexec ")
        for pat in blocked_patterns:
            if pat in low:
                return False, f"Blocked unsafe pattern '{pat}' inside custom Python tool."
        return True, "Validated AST & safety rules."

    def create_composite_custom_tool(
        self,
        *,
        name: str,
        description: str,
        steps: list[dict[str, Any]],
        parameters: dict[str, Any] | None = None,
        auto_learned: bool = False,
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        tname = self._sanitize_custom_tool_name(name)
        clean_steps: list[dict[str, Any]] = []
        for st in (steps or []):
            if not isinstance(st, dict) or not st.get("tool"):
                continue
            target_tool = str(st["tool"]).strip()
            if target_tool == tname:
                continue
            clean_steps.append(
                {
                    "tool": target_tool,
                    "args": dict(st.get("args") or {}),
                    "continue_on_error": bool(st.get("continue_on_error", True)),
                }
            )
        if not clean_steps:
            return {"ok": False, "error": "Composite custom tool requires at least one valid tool step."}

        schema = parameters if isinstance(parameters, dict) and parameters.get("type") == "object" else {
            "type": "object",
            "properties": {},
            "required": [],
        }
        now_iso = utc_now_iso()
        with self._lock:
            reg = self._load_custom_tools_registry()
            prev = dict(reg.get(tname) or {})
            ver = int(prev.get("version", 0) or 0) + 1
            rec = {
                "name": tname,
                "kind": "composite",
                "description": (description or f"Composite workflow combining {len(clean_steps)} tools").strip()[:280],
                "parameters": schema,
                "steps": clean_steps,
                "source_tools": [s["tool"] for s in clean_steps],
                "version": ver,
                "enabled": True,
                "auto_learned": bool(auto_learned),
                "runs": int(prev.get("runs", 0) or 0),
                "successes": int(prev.get("successes", 0) or 0),
                "failures": int(prev.get("failures", 0) or 0),
                "last_error": "",
                "created_at": prev.get("created_at") or now_iso,
                "updated_at": now_iso,
            }
            reg[tname] = rec
            self._save_custom_tools_registry(reg)
        try:
            self.store_library_item(
                title=f"Custom Tool: {tname} (v{rec['version']})",
                body=f"{rec['description']}\nSteps: {', '.join(rec['source_tools'])}",
                category="custom_tools",
                source_type="learned",
                tags=f"custom tool {tname} {' '.join(rec['source_tools'])}",
                summary=rec["description"],
                item_id=f"tool-{tname}",
                pinned=False,
                config=config,
            )
        except Exception:
            pass
        return {"ok": True, "tool": rec}

    def create_python_custom_tool(
        self,
        *,
        name: str,
        description: str,
        code: str,
        parameters: dict[str, Any] | None = None,
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        perm = self._effective_permission_level(config) if config is not None else "safe_user"
        if perm in ("read_only", "safe_user"):
            return {
                "ok": False,
                "blocked": True,
                "error": "Creating executable Python custom tools requires Level 3 (Up to Just Below Sudo) or Level 4. Use create_composite for safe Level 2 macro tools.",
            }
        ok_v, msg_v = self._validate_custom_python(code)
        if not ok_v:
            return {"ok": False, "error": msg_v}

        tname = self._sanitize_custom_tool_name(name)
        if config is not None:
            ok_ap, msg_ap = self._request_mutation_approval(
                f"Save Custom Python Tool '{tname}'",
                f"Hermes wants to save custom Python tool `{tname}`:\n{description[:180]}\n\nCode preview:\n{code[:320]}",
                config,
            )
            if not ok_ap:
                return {"ok": False, "blocked": True, "error": msg_ap}

        self._init_custom_tools_dir()
        script_path = self.custom_tools_scripts_dir / f"{tname}.py"
        if script_path.exists():
            bak = self.custom_tools_versions_dir / f"{tname}.{int(time.time())}.py.bak"
            bak.write_text(script_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        script_path.write_text(code.rstrip() + "\n", encoding="utf-8")

        schema = parameters if isinstance(parameters, dict) and parameters.get("type") == "object" else {
            "type": "object",
            "properties": {},
            "required": [],
        }
        now_iso = utc_now_iso()
        with self._lock:
            reg = self._load_custom_tools_registry()
            prev = dict(reg.get(tname) or {})
            ver = int(prev.get("version", 0) or 0) + 1
            rec = {
                "name": tname,
                "kind": "python",
                "description": (description or f"Custom Python tool {tname}").strip()[:280],
                "parameters": schema,
                "code_path": str(script_path),
                "source_tools": [],
                "version": ver,
                "enabled": True,
                "auto_learned": False,
                "runs": int(prev.get("runs", 0) or 0),
                "successes": int(prev.get("successes", 0) or 0),
                "failures": int(prev.get("failures", 0) or 0),
                "last_error": "",
                "created_at": prev.get("created_at") or now_iso,
                "updated_at": now_iso,
            }
            reg[tname] = rec
            self._save_custom_tools_registry(reg)
        self.journal.log_event("HermesOS", "custom_tool_save", f"Saved custom Python tool {tname} v{ver}", "Custom Tool Forge", "ok")
        return {"ok": True, "tool": rec}

    def combine_tools_into_custom(
        self,
        *,
        name: str,
        description: str,
        source_tools: list[str],
        step_args: dict[str, dict[str, Any]] | None = None,
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        """Combines 2+ built-in or existing custom tools into a unified composite tool (flattening sub-composites)."""
        reg = self._load_custom_tools_registry()
        expanded_steps: list[dict[str, Any]] = []
        for raw_t in (source_tools or []):
            t = str(raw_t).strip()
            if not t:
                continue
            if t in reg and reg[t].get("kind") == "composite":
                for sub in (reg[t].get("steps") or []):
                    expanded_steps.append(dict(sub))
            else:
                expanded_steps.append(
                    {
                        "tool": t,
                        "args": dict((step_args or {}).get(t) or {}),
                        "continue_on_error": True,
                    }
                )
        if len(expanded_steps) < 2:
            return {"ok": False, "error": "Combining tools requires at least 2 tool steps."}
        return self.create_composite_custom_tool(
            name=name,
            description=description or f"Combined pipeline: {', '.join(source_tools[:6])}",
            steps=expanded_steps,
            config=config,
        )

    def improve_custom_tool(
        self,
        *,
        name: str,
        description: str = "",
        steps: list[dict[str, Any]] | None = None,
        code: str = "",
        improvement_note: str = "",
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        """Archives the prior version and upgrades an existing custom tool's logic, steps, or description."""
        tname = self._sanitize_custom_tool_name(name)
        reg = self._load_custom_tools_registry()
        if tname not in reg:
            return {"ok": False, "error": f"Custom tool '{tname}' not found."}
        cur = dict(reg[tname])
        ver_old = int(cur.get("version", 1) or 1)
        self._init_custom_tools_dir()
        snap_path = self.custom_tools_versions_dir / f"{tname}.v{ver_old}.json"
        snap_path.write_text(json.dumps(cur, indent=2), encoding="utf-8")

        new_desc = (description or cur.get("description") or "").strip()[:280]
        if cur.get("kind") == "python" and code.strip():
            res = self.create_python_custom_tool(
                name=tname,
                description=new_desc,
                code=code,
                parameters=cur.get("parameters"),
                config=config,
            )
            if res.get("ok") and improvement_note:
                reg2 = self._load_custom_tools_registry()
                if tname in reg2:
                    reg2[tname]["last_improvement"] = improvement_note[:200]
                    self._save_custom_tools_registry(reg2)
            return res

        new_steps = steps if steps else list(cur.get("steps") or [])
        res = self.create_composite_custom_tool(
            name=tname,
            description=new_desc,
            steps=new_steps,
            parameters=cur.get("parameters"),
            auto_learned=bool(cur.get("auto_learned", False)),
            config=config,
        )
        if res.get("ok") and improvement_note:
            reg2 = self._load_custom_tools_registry()
            if tname in reg2:
                reg2[tname]["last_improvement"] = improvement_note[:200]
                self._save_custom_tools_registry(reg2)
        return res

    def toggle_custom_tool(self, name: str, enabled: bool) -> dict[str, Any]:
        tname = self._sanitize_custom_tool_name(name)
        with self._lock:
            reg = self._load_custom_tools_registry()
            if tname not in reg:
                return {"ok": False, "error": f"Custom tool '{tname}' not found."}
            reg[tname]["enabled"] = bool(enabled)
            reg[tname]["updated_at"] = utc_now_iso()
            self._save_custom_tools_registry(reg)
        return {"ok": True, "name": tname, "enabled": bool(enabled)}

    def delete_custom_tool(self, name: str) -> dict[str, Any]:
        tname = self._sanitize_custom_tool_name(name)
        with self._lock:
            reg = self._load_custom_tools_registry()
            if tname not in reg:
                return {"ok": False, "error": f"Custom tool '{tname}' not found."}
            rec = reg.pop(tname)
            self._save_custom_tools_registry(reg)
        cp = rec.get("code_path")
        if cp:
            try:
                Path(str(cp)).unlink(missing_ok=True)
            except Exception:
                pass
        return {"ok": True, "deleted": tname}

    @staticmethod
    def _substitute_template_args(obj: Any, call_args: dict[str, Any], prev_out: dict[str, Any]) -> Any:
        if isinstance(obj, dict):
            return {k: HermesAgentManager._substitute_template_args(v, call_args, prev_out) for k, v in obj.items()}
        if isinstance(obj, list):
            return [HermesAgentManager._substitute_template_args(v, call_args, prev_out) for v in obj]
        if isinstance(obj, str) and "{{" in obj:
            def _rep(m: re.Match[str]) -> str:
                key = m.group(1).strip()
                if key.startswith("prev."):
                    return str(prev_out.get(key[5:], ""))
                return str(call_args.get(key, ""))

            return re.sub(r"\{\{([^}]+)\}\}", _rep, obj)
        return obj

    def execute_custom_tool(
        self,
        name: str,
        args: dict[str, Any],
        config: AppConfig,
        *,
        allow_subagent: bool = True,
        _call_stack: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        tname = self._sanitize_custom_tool_name(name)
        if tname in _call_stack:
            return {"ok": False, "error": f"Cycle detected while executing custom tool '{tname}'."}
        if len(_call_stack) >= 4:
            return {"ok": False, "error": "Max custom tool nesting depth (4) reached."}

        reg = self._load_custom_tools_registry()
        rec = reg.get(tname)
        if not rec:
            return {"ok": False, "error": f"Custom tool '{tname}' not found."}
        if not rec.get("enabled", True):
            return {"ok": False, "error": f"Custom tool '{tname}' is currently disabled."}

        ok_run = False
        err_txt = ""
        payload_out: dict[str, Any] = {}

        try:
            if rec.get("kind") == "composite":
                step_results: list[dict[str, Any]] = []
                prev_res: dict[str, Any] = {}
                for idx, st in enumerate(rec.get("steps") or []):
                    sub_tool = str(st.get("tool") or "")
                    sub_args = self._substitute_template_args(dict(st.get("args") or {}), args or {}, prev_res)
                    if sub_tool.startswith("custom_"):
                        sr = self.execute_custom_tool(
                            sub_tool,
                            sub_args,
                            config,
                            allow_subagent=allow_subagent,
                            _call_stack=_call_stack + (tname,),
                        )
                    else:
                        sr = self.execute_tool(sub_tool, sub_args, config, allow_subagent=allow_subagent)
                    step_results.append({"step": idx + 1, "tool": sub_tool, "ok": bool(sr.get("ok")), "result": sr})
                    prev_res = sr if isinstance(sr, dict) else {}
                    if not sr.get("ok") and not st.get("continue_on_error", True):
                        err_txt = str(sr.get("error") or f"Step {idx + 1} ({sub_tool}) failed")
                        break
                ok_run = not bool(err_txt) and any(s["ok"] for s in step_results)
                payload_out = {
                    "ok": ok_run,
                    "custom_tool": tname,
                    "version": rec.get("version", 1),
                    "steps_executed": len(step_results),
                    "results": step_results,
                    **({"error": err_txt} if err_txt else {}),
                }
            elif rec.get("kind") == "python":
                perm = self._effective_permission_level(config)
                if perm in ("read_only", "safe_user"):
                    return {"ok": False, "blocked": True, "error": "Running Python custom tools requires Level 3 or Level 4 permissions."}
                script_path = Path(str(rec.get("code_path") or ""))
                if not script_path.exists():
                    return {"ok": False, "error": f"Script file missing for '{tname}'."}
                harness = (
                    "import importlib.util, json, sys\n"
                    f"spec = importlib.util.spec_from_file_location('ctool', {repr(str(script_path))})\n"
                    "mod = importlib.util.module_from_spec(spec)\n"
                    "spec.loader.exec_module(mod)\n"
                    "args = json.loads(sys.stdin.read() or '{}')\n"
                    "res = mod.run(args)\n"
                    "print(json.dumps(res if isinstance(res, dict) else {'result': res}))\n"
                )
                pr = subprocess.run(
                    [sys.executable, "-c", harness],
                    input=json.dumps(args or {}),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    creationflags=_WIN_NO_WINDOW,
                )
                if pr.returncode == 0 and pr.stdout.strip():
                    parsed = json.loads(pr.stdout.strip().splitlines()[-1])
                    ok_run = bool(parsed.get("ok", True))
                    payload_out = {"ok": ok_run, "custom_tool": tname, "version": rec.get("version", 1), **parsed}
                else:
                    err_txt = (pr.stderr or pr.stdout or f"Exit code {pr.returncode}")[:800]
                    payload_out = {"ok": False, "custom_tool": tname, "error": err_txt}
            else:
                err_txt = f"Unsupported custom tool kind: {rec.get('kind')}"
                payload_out = {"ok": False, "error": err_txt}
        except Exception as exc:
            err_txt = str(exc)
            payload_out = {"ok": False, "custom_tool": tname, "error": err_txt}

        with self._lock:
            reg2 = self._load_custom_tools_registry()
            if tname in reg2:
                reg2[tname]["runs"] = int(reg2[tname].get("runs", 0) or 0) + 1
                if ok_run:
                    reg2[tname]["successes"] = int(reg2[tname].get("successes", 0) or 0) + 1
                    reg2[tname]["last_error"] = ""
                else:
                    reg2[tname]["failures"] = int(reg2[tname].get("failures", 0) or 0) + 1
                    reg2[tname]["last_error"] = err_txt[:240]
                reg2[tname]["last_run_at"] = utc_now_iso()
                self._save_custom_tools_registry(reg2)
        return payload_out

    def _init_sessions_db(self) -> None:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.sessions_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    summary TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_trace TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def load_env(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if not self.env_path.exists():
            return out
        for line in self.env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
        return out

    def save_env_vars(self, updates: dict[str, str]) -> None:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        cur = self.load_env()
        for k, v in updates.items():
            clean_k = k.strip()
            if not clean_k:
                continue
            if v:
                cur[clean_k] = str(v).strip()
            elif clean_k in cur and v == "":
                cur.pop(clean_k, None)
        lines = [f'{k}="{v}"' for k, v in sorted(cur.items())]
        tmp = self.env_path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        os.replace(tmp, self.env_path)
        try:
            os.chmod(self.env_path, 0o600)
        except OSError:
            pass

    def resolve_provider_spec(self, config: AppConfig) -> dict[str, Any]:
        local_only = bool(getattr(config, "hermes_local_only", False))
        mode = "local_gpu" if local_only else str(getattr(config, "hermes_mode", "free_cloud") or "free_cloud")
        prov = "ollama" if local_only else str(getattr(config, "hermes_provider", "openrouter_free") or "openrouter_free")
        rec = recommend_hermes_models()

        if mode == "local_gpu" or prov == "ollama":
            model = str(getattr(config, "hermes_model", "") or "").strip() or rec.recommended_local_model
            base_url = str(getattr(config, "hermes_base_url", "") or "").strip() or "http://127.0.0.1:11434/v1"
            if local_only and not (base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")):
                base_url = "http://127.0.0.1:11434/v1"
            return {
                "mode": "local_gpu",
                "provider": "ollama",
                "local_only": local_only,
                "label": f"100% Local-Only GPU ({model} via Ollama)" if local_only else f"Local GPU ({model} via Ollama)",
                "base_url": base_url.rstrip("/"),
                "model": model,
                "env_var": "OLLAMA_API_KEY",
                "api_style": "openai",
                "requires_key": False,
                "context_tokens": int(getattr(config, "hermes_context_tokens", rec.recommended_context_tokens) or rec.recommended_context_tokens),
            }

        if prov in FREE_CLOUD_PROVIDERS:
            spec = dict(FREE_CLOUD_PROVIDERS[prov])
            model = str(getattr(config, "hermes_model", "") or "").strip() or spec["model"]
            base_url = str(getattr(config, "hermes_base_url", "") or "").strip() or spec["base_url"]
            return {
                "mode": "free_cloud",
                "provider": prov,
                "label": spec["label"],
                "base_url": base_url.rstrip("/"),
                "model": model,
                "env_var": spec["env_var"],
                "api_style": spec["api_style"],
                "requires_key": True,
                "context_tokens": int(getattr(config, "hermes_context_tokens", 32768) or 32768),
            }

        spec = dict(CUSTOM_API_PROVIDERS.get(prov, CUSTOM_API_PROVIDERS["custom"]))
        model = str(getattr(config, "hermes_model", "") or "").strip() or spec["model"]
        base_url = str(getattr(config, "hermes_base_url", "") or "").strip() or spec["base_url"]
        return {
            "mode": "custom_api",
            "provider": prov,
            "label": spec["label"],
            "base_url": base_url.rstrip("/"),
            "model": model,
            "env_var": spec["env_var"],
            "api_style": spec["api_style"],
            "requires_key": (not base_url.startswith("http://127.0.0.1") and not base_url.startswith("http://localhost")),
            "context_tokens": int(getattr(config, "hermes_context_tokens", 32768) or 32768),
        }

    def write_config_yaml(self, config: AppConfig) -> Path:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        spec = self.resolve_provider_spec(config)
        body = (
            "# Auto-managed by AstraCradle Hermes OS AI\n"
            f"hermes_home: {json.dumps(str(self.hermes_home))}\n"
            f"local_only: {'true' if spec.get('local_only') else 'false'}\n"
            "model:\n"
            f"  provider: {json.dumps(spec['provider'])}\n"
            f"  default: {json.dumps(spec['model'])}\n"
            f"  base_url: {json.dumps(spec['base_url'])}\n"
            f"  context_length: {int(spec['context_tokens'])}\n"
            "memory:\n"
            "  memory_enabled: true\n"
            "  user_profile_enabled: true\n"
            "skills:\n"
            "  auto_create: true\n"
            f"  skills_dir: {json.dumps(str(self.skills_dir))}\n"
            "security:\n"
            f"  permission_level: {json.dumps(str(getattr(config, 'hermes_permission_level', 'safe_user')))}\n"
            f"  require_approval: {'true' if getattr(config, 'hermes_require_approval', True) else 'false'}\n"
            f"  allow_shell: {'true' if getattr(config, 'hermes_allow_shell', False) else 'false'}\n"
            "gpu:\n"
            f"  limit_mode: {json.dumps(str(getattr(config, 'hermes_gpu_limit_mode', 'game_adaptive')))}\n"
            f"  vram_reserve_mb: {int(getattr(config, 'hermes_vram_reserve_mb', 1024) or 1024)}\n"
        )
        tmp = self.config_yaml_path.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, self.config_yaml_path)
        return self.config_yaml_path

    # ------------------------------------------------------------------
    # Ollama & Upstream Hermes Runtime Detection + Automated Setup
    # ------------------------------------------------------------------
    @staticmethod
    def find_ollama_bin() -> str | None:
        found = shutil.which("ollama")
        if found:
            return found
        if platform.system() == "Windows":
            cands = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Ollama" / "ollama.exe",
            ]
            for c in cands:
                if c.exists():
                    return str(c)
        elif platform.system() == "Darwin":
            for c in (Path("/opt/homebrew/bin/ollama"), Path("/usr/local/bin/ollama")):
                if c.exists():
                    return str(c)
        return None

    @staticmethod
    def _model_tag_matches(candidate: str, existing: list[str]) -> str | None:
        """Returns the exact installed model tag if `candidate` (e.g. 'qwen3.5:9b') is already present locally."""
        want = (candidate or "").strip().lower()
        if not want:
            return None
        for prefix in ("registry.ollama.ai/library/", "library/"):
            if want.startswith(prefix):
                want = want[len(prefix) :]
        for raw in existing:
            cur = (raw or "").strip()
            low = cur.lower()
            for prefix in ("registry.ollama.ai/library/", "library/"):
                if low.startswith(prefix):
                    low = low[len(prefix) :]
            if (
                low == want
                or low == f"{want}:latest"
                or (":" not in want and low.split(":", 1)[0] == want)
                or low.startswith(want + "-")
                or low.startswith(want + ":")
            ):
                return cur
        return None

    @staticmethod
    def scan_ollama_disk_manifests() -> list[str]:
        """Scans Ollama manifest directories on disk so downloaded models are detected even if Ollama isn't running."""
        roots: list[Path] = []
        env_models = os.environ.get("OLLAMA_MODELS", "").strip()
        if env_models:
            roots.append(Path(env_models).expanduser() / "manifests")
        roots.extend(
            [
                Path.home() / ".ollama" / "models" / "manifests",
                Path("/var/lib/ollama/models/manifests"),
                Path("/usr/share/ollama/.ollama/models/manifests"),
            ]
        )
        found: list[str] = []
        seen: set[str] = set()
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            try:
                for tag_file in root.rglob("*"):
                    if not tag_file.is_file():
                        continue
                    rel = tag_file.relative_to(root).parts
                    # Standard layout: <registry>/<namespace>/<model>/<tag>
                    if len(rel) >= 2:
                        model_name = rel[-2]
                        tag_name = rel[-1]
                        full_tag = f"{model_name}:{tag_name}" if tag_name != "latest" else f"{model_name}:latest"
                        if rel[0] not in ("registry.ollama.ai",) and len(rel) >= 3:
                            pass
                        if len(rel) >= 4 and rel[-3] != "library":
                            full_tag = f"{rel[-3]}/{full_tag}"
                        if full_tag not in seen:
                            seen.add(full_tag)
                            found.append(full_tag)
            except Exception:
                continue
        return found

    @classmethod
    def find_all_local_ollama_models(cls, base_url: str = "http://127.0.0.1:11434") -> tuple[bool, list[str]]:
        """Returns (ollama_running, deduplicated_downloaded_models) via API, CLI, and disk manifest scan."""
        running, api_models = cls.list_ollama_models(base_url)
        merged: list[str] = list(api_models)
        seen = {m.lower() for m in merged}
        if not running:
            obin = cls.find_ollama_bin()
            if obin:
                try:
                    p = subprocess.run(
                        [obin, "list"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    if p.returncode == 0:
                        for ln in p.stdout.splitlines()[1:]:
                            tok = (ln.strip().split() or [""])[0]
                            if tok and tok.lower() not in seen:
                                seen.add(tok.lower())
                                merged.append(tok)
                except Exception:
                    pass
        for dm in cls.scan_ollama_disk_manifests():
            if dm.lower() not in seen:
                seen.add(dm.lower())
                merged.append(dm)
        return running, merged

    def auto_integrate_downloaded_gpu_model(
        self,
        config: AppConfig,
        *,
        start_runtime: bool = False,
        save_config: bool = True,
    ) -> dict[str, Any]:
        """Auto-detects if the GPU-recommended AI model is already downloaded and integrates it with zero re-download."""
        rec = recommend_hermes_models()
        spec_cur = self.resolve_provider_spec(config)
        running, pulled = self.find_all_local_ollama_models(spec_cur["base_url"])

        candidates: list[str] = []
        if getattr(config, "hermes_model", "") and (
            getattr(config, "hermes_local_only", False) or getattr(config, "hermes_mode", "") == "local_gpu"
        ):
            candidates.append(str(config.hermes_model))
        candidates.append(rec.recommended_local_model)
        candidates.extend(rec.local_alternatives)

        matched_tag = None
        for cand in candidates:
            matched_tag = self._model_tag_matches(cand, pulled)
            if matched_tag:
                break

        if not matched_tag:
            return {
                "integrated": False,
                "model": "",
                "recommended_model": rec.recommended_local_model,
                "pulled_models": pulled,
                "ollama_running": running,
                "message": f"Recommended GPU model '{rec.recommended_local_model}' is not downloaded yet.",
            }

        # Preserve explicit cloud choice ONLY if the user actually configured a cloud key and did NOT pick local_only
        env_map = self.load_env()
        has_cloud_key = bool(
            spec_cur["mode"] != "local_gpu"
            and (env_map.get(spec_cur["env_var"]) or os.environ.get(spec_cur["env_var"]))
        )
        if has_cloud_key and not bool(getattr(config, "hermes_local_only", False)):
            return {
                "integrated": False,
                "model": matched_tag,
                "recommended_model": rec.recommended_local_model,
                "pulled_models": pulled,
                "ollama_running": running,
                "message": f"Local model '{matched_tag}' is downloaded, keeping active cloud provider ({spec_cur['provider']}).",
            }

        changed = (
            not getattr(config, "hermes_enabled", False)
            or not getattr(config, "hermes_local_only", False)
            or getattr(config, "hermes_mode", "") != "local_gpu"
            or getattr(config, "hermes_provider", "") != "ollama"
            or getattr(config, "hermes_model", "") != matched_tag
        )
        config.hermes_enabled = True
        config.hermes_local_only = True
        config.hermes_mode = "local_gpu"
        config.hermes_provider = "ollama"
        config.hermes_model = matched_tag
        if not getattr(config, "hermes_base_url", ""):
            config.hermes_base_url = "http://127.0.0.1:11434/v1"
        self.ensure_hermes_home(config)
        if start_runtime and not running and self.find_ollama_bin():
            ok_srv, _ = self.ensure_ollama_running()
            running = ok_srv
        if save_config and changed:
            self.cfg_mgr.save(config)
            self.journal.log_event(
                "HermesOS",
                "auto_integrate_gpu_model",
                f"Auto-integrated already-downloaded GPU model '{matched_tag}' (Ollama running={running})",
                "Avoided re-downloading recommended GPU AI already present on disk",
                "ok",
            )
        return {
            "integrated": True,
            "model": matched_tag,
            "recommended_model": rec.recommended_local_model,
            "pulled_models": pulled,
            "ollama_running": running,
            "message": f"Auto-integrated already-downloaded GPU model '{matched_tag}' (no re-download needed).",
        }

    @staticmethod
    def list_ollama_models(base_url: str = "http://127.0.0.1:11434") -> tuple[bool, list[str]]:
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        try:
            req = urllib.request.Request(f"{url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                models = [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
                return True, models
        except Exception:
            return False, []

    def ensure_ollama_running(self) -> tuple[bool, str]:
        running, _ = self.list_ollama_models()
        if running:
            return True, "Ollama service is already running locally."
        obin = self.find_ollama_bin()
        if not obin:
            return False, "Ollama binary is not installed."
        try:
            self._ollama_proc = subprocess.Popen(
                [obin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_WIN_NO_WINDOW,
                start_new_session=(platform.system() != "Windows"),
            )
            for _ in range(16):
                time.sleep(0.5)
                ok, _ = self.list_ollama_models()
                if ok:
                    return True, "Started local Ollama runtime."
            return False, "Started Ollama process, but local endpoint http://127.0.0.1:11434 did not respond in time."
        except Exception as exc:
            return False, f"Failed to start Ollama: {exc}"

    def install_ollama_runtime(self, progress_cb: Callable[[str], None] | None = None) -> tuple[bool, str]:
        if self.find_ollama_bin():
            return True, "Ollama already installed."
        sys_name = platform.system()
        if progress_cb:
            progress_cb("Installing Ollama local AI runtime...")
        try:
            if sys_name == "Windows":
                if not shutil.which("winget"):
                    return False, "winget not found — install Ollama from https://ollama.com/download"
                p = subprocess.run(
                    [
                        "winget",
                        "install",
                        "-e",
                        "--id",
                        "Ollama.Ollama",
                        "--accept-source-agreements",
                        "--accept-package-agreements",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    creationflags=_WIN_NO_WINDOW,
                )
                ok = (p.returncode == 0) or bool(self.find_ollama_bin())
                return ok, ("Installed Ollama via winget." if ok else (p.stderr or p.stdout or "winget install failed").strip())
            if sys_name == "Darwin":
                if shutil.which("brew"):
                    p = subprocess.run(["brew", "install", "ollama"], capture_output=True, text=True, timeout=300)
                    ok = (p.returncode == 0) or bool(self.find_ollama_bin())
                    return ok, ("Installed Ollama via Homebrew." if ok else (p.stderr or p.stdout).strip())
                return False, "Homebrew not found — install Ollama from https://ollama.com/download"
            # Linux / CachyOS / Arch / Debian / Fedora
            if shutil.which("pacman") and os.geteuid() == 0:
                p = subprocess.run(["pacman", "-S", "--noconfirm", "--needed", "ollama"], capture_output=True, text=True, timeout=240)
                if p.returncode == 0 or self.find_ollama_bin():
                    return True, "Installed Ollama via pacman."
            if shutil.which("curl"):
                p = subprocess.run(
                    ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                ok = (p.returncode == 0) or bool(self.find_ollama_bin())
                return ok, ("Installed Ollama via official installer." if ok else (p.stderr or p.stdout or "Install failed").strip())
            return False, "Install Ollama via your package manager (`sudo pacman -S ollama` on CachyOS) or https://ollama.com/download."
        except Exception as exc:
            return False, f"Ollama install error: {exc}"

    def install_upstream_hermes_package(self, progress_cb: Callable[[str], None] | None = None) -> tuple[bool, str]:
        """Installs upstream `hermes-agent` into AstraCradle's Python environment when network/pip is available."""
        try:
            import hermes_agent  # type: ignore  # noqa: F401

            return True, "Upstream hermes-agent package already installed."
        except Exception:
            pass
        if progress_cb:
            progress_cb("Installing upstream NousResearch hermes-agent package...")
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "hermes-agent"],
                capture_output=True,
                text=True,
                timeout=180,
                creationflags=_WIN_NO_WINDOW,
            )
            if p.returncode == 0:
                return True, "Installed upstream hermes-agent package into AstraCradle environment."
            return False, f"Optional upstream hermes-agent pip install skipped ({(p.stderr or p.stdout).strip()[:160]}); using built-in AstraCradle Hermes runtime."
        except Exception as exc:
            return False, f"Using built-in AstraCradle Hermes runtime ({exc})."

    def pull_ollama_model(
        self,
        model: str,
        progress_cb: Callable[[str], None] | None = None,
    ) -> tuple[bool, str]:
        _, disk_existing = self.find_all_local_ollama_models()
        matched_disk = self._model_tag_matches(model, disk_existing)
        obin = self.find_ollama_bin()
        if not obin:
            if matched_disk:
                return True, f"Model '{matched_disk}' is already downloaded on disk."
            return False, "Ollama binary not found."
        ok_srv, msg_srv = self.ensure_ollama_running()
        if not ok_srv:
            return False, msg_srv
        _, existing = self.find_all_local_ollama_models()
        matched = self._model_tag_matches(model, existing)
        if matched:
            return True, f"Model '{matched}' is already downloaded — integrated directly without re-downloading."
        if progress_cb:
            progress_cb(f"Downloading GPU-optimized model '{model}' via Ollama (this may take a few minutes)...")
        try:
            proc = subprocess.Popen(
                [obin, "pull", model],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=_WIN_NO_WINDOW,
            )
            last_line = ""
            if proc.stdout is not None:
                for raw in proc.stdout:
                    ln = raw.strip()
                    if ln:
                        last_line = ln
                        if progress_cb and ("%" in ln or "pulling" in ln.lower() or "success" in ln.lower()):
                            progress_cb(f"Ollama pull ({model}): {ln[:90]}")
            rc = proc.wait(timeout=1800)
            if rc == 0:
                return True, f"Successfully downloaded and verified '{model}'."
            return False, f"Ollama pull failed (exit {rc}): {last_line}"
        except Exception as exc:
            return False, f"Model pull failed: {exc}"

    def install_and_setup_hermes(
        self,
        config: AppConfig,
        *,
        api_key: str = "",
        install_upstream_pkg: bool = True,
        pull_local_model: bool = True,
        progress_cb: Callable[[str, float, str], None] | None = None,
    ) -> tuple[bool, str, HermesSetupStatus]:
        """Full 1-click setup: creates ~/.astracradle/hermes, installs runtime/Ollama/GPU model or saves API key, and verifies."""
        def _emit(step: str, pct: float, detail: str) -> None:
            if progress_cb is not None:
                progress_cb(step, pct, detail)

        _emit("Initializing Hermes OS Workspace", 0.15, f"Setting up {self.hermes_home}...")
        self.ensure_hermes_home(config)
        spec = self.resolve_provider_spec(config)

        if api_key.strip():
            _emit("Saving Credentials", 0.25, f"Storing {spec['env_var']} securely (0600)...")
            self.save_env_vars({spec["env_var"]: api_key.strip()})

        notes: list[str] = [f"Initialized Hermes home at {self.hermes_home}"]
        if install_upstream_pkg:
            _emit("Checking Hermes Runtime", 0.40, "Checking/installing upstream hermes-agent runtime...")
            _, pkg_msg = self.install_upstream_hermes_package(
                progress_cb=lambda m: _emit("Checking Hermes Runtime", 0.45, m)
            )
            notes.append(pkg_msg)

        ok_overall = True
        if spec["mode"] == "local_gpu":
            _, pre_pulled = self.find_all_local_ollama_models(spec["base_url"])
            pre_match = self._model_tag_matches(spec["model"], pre_pulled)
            if pre_match:
                config.hermes_model = pre_match
                spec["model"] = pre_match
            _emit("Installing Local GPU Engine", 0.55, "Checking Ollama local inference runtime...")
            if not self.find_ollama_bin():
                ok_ol, msg_ol = self.install_ollama_runtime(
                    progress_cb=lambda m: _emit("Installing Local GPU Engine", 0.60, m)
                )
                notes.append(msg_ol)
                if not ok_ol:
                    ok_overall = False
            if ok_overall:
                ok_srv, msg_srv = self.ensure_ollama_running()
                notes.append(msg_srv)
                if not ok_srv:
                    ok_overall = False
            if ok_overall and pre_match:
                _emit("Integrating Local GPU Model", 0.85, f"Found '{pre_match}' already downloaded — integrating without re-download...")
                notes.append(f"Auto-integrated already-downloaded model '{pre_match}' (skipped re-download).")
            elif ok_overall and pull_local_model:
                _emit("Downloading GPU Model", 0.75, f"Pulling {spec['model']}...")
                ok_pull, msg_pull = self.pull_ollama_model(
                    spec["model"],
                    progress_cb=lambda m: _emit("Downloading GPU Model", 0.82, m),
                )
                notes.append(msg_pull)
                if not ok_pull:
                    ok_overall = False
        else:
            _emit("Configuring Cloud / API Endpoint", 0.75, f"Configured {spec['label']} ({spec['model']})...")
            env_map = self.load_env()
            has_key = bool(env_map.get(spec["env_var"]) or os.environ.get(spec["env_var"]))
            if spec["requires_key"] and not has_key:
                notes.append(
                    f"Ready to connect — paste your {spec['env_var']} key in Hermes AI Settings to start chatting."
                )
            else:
                notes.append(f"Verified credentials for {spec['label']}.")

        config.hermes_enabled = True
        self.write_config_yaml(config)
        self.cfg_mgr.save(config)
        _emit("Finalizing Hermes OS", 1.0, "Hermes OS AI setup finished.")

        self.journal.log_event(
            "HermesOS",
            "install_and_setup",
            f"Mode={spec['mode']}, Provider={spec['provider']}, Model={spec['model']}",
            "Configured native Hermes OS AI agent inside AstraCradle",
            "ok" if ok_overall else "warning",
        )
        st = self.get_status(config)
        return ok_overall, " | ".join(notes), st

    def get_status(self, config: AppConfig) -> HermesSetupStatus:
        spec = self.resolve_provider_spec(config)
        env_map = self.load_env()
        has_key = bool(env_map.get(spec["env_var"]) or os.environ.get(spec["env_var"]))
        obin = self.find_ollama_bin()
        ol_running, pulled = self.find_all_local_ollama_models(spec["base_url"]) if (obin or self.scan_ollama_disk_manifests()) else (False, [])
        cli_inst = bool(shutil.which("hermes"))
        try:
            import hermes_agent  # type: ignore  # noqa: F401
            pkg_inst = True
        except Exception:
            pkg_inst = False

        skills_cnt = len(list(self.skills_dir.glob("*/SKILL.md"))) if self.skills_dir.exists() else 0
        sessions_cnt = 0
        if self.sessions_db_path.exists():
            try:
                with sqlite3.connect(self.sessions_db_path) as conn:
                    row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                    sessions_cnt = int(row[0]) if row else 0
            except Exception:
                pass
        cron_cnt = len(self.list_cron_jobs())
        gw_running = self._gateway_proc is not None and self._gateway_proc.poll() is None

        if spec["mode"] == "local_gpu":
            model_pulled = bool(self._model_tag_matches(spec["model"], pulled))
            ready = bool(self.hermes_home.exists() and ol_running and model_pulled)
            if model_pulled and not ol_running:
                summary = f"DOWNLOADED ({spec['model']} ready on disk — click or ask anything to auto-start Ollama)"
            elif ready:
                summary = f"READY (Local GPU · {spec['model']})"
            else:
                summary = f"Needs setup ({'start Ollama' if obin else 'install Ollama'} & pull {spec['model']})"
        else:
            ready = bool(self.hermes_home.exists() and (has_key or not spec["requires_key"]))
            summary = (
                f"READY ({spec['provider']} · {spec['model']})"
                if ready
                else f"Needs free/own API key ({spec['env_var']})"
            )

        return HermesSetupStatus(
            hermes_home=str(self.hermes_home),
            hermes_cli_installed=cli_inst,
            hermes_pkg_installed=pkg_inst,
            ollama_installed=bool(obin),
            ollama_running=ol_running,
            pulled_models=pulled,
            active_mode=spec["mode"],
            active_provider=spec["provider"],
            active_model=spec["model"],
            api_key_configured=has_key,
            gateway_running=gw_running,
            skills_count=skills_cnt,
            sessions_count=sessions_cnt,
            cron_count=cron_cnt,
            ready=ready,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Sessions, Persistent Memory, Skills & Cron Storage
    # ------------------------------------------------------------------
    def list_sessions(self, limit: int = 30) -> list[dict[str, str]]:
        self._init_sessions_db()
        with sqlite3.connect(self.sessions_db_path) as conn:
            rows = conn.execute(
                "SELECT session_id, title, updated_at, summary FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {"session_id": r[0], "title": r[1], "updated_at": r[2], "summary": r[3] or ""}
            for r in rows
        ]

    def get_session_messages(self, session_id: str, limit: int = 60) -> list[dict[str, str]]:
        self._init_sessions_db()
        with sqlite3.connect(self.sessions_db_path) as conn:
            rows = conn.execute(
                "SELECT role, content, tool_trace, created_at FROM messages WHERE session_id=? ORDER BY id ASC LIMIT ?",
                (session_id, int(limit)),
            ).fetchall()
        return [
            {"role": r[0], "content": r[1], "tool_trace": r[2] or "", "created_at": r[3]}
            for r in rows
        ]

    def search_sessions(self, query: str, limit: int = 12) -> list[dict[str, str]]:
        self._init_sessions_db()
        q = f"%{query.strip()}%"
        with sqlite3.connect(self.sessions_db_path) as conn:
            rows = conn.execute(
                """
                SELECT s.session_id, s.title, m.role, substr(m.content, 1, 180), m.created_at
                FROM messages m
                JOIN sessions s ON s.session_id = m.session_id
                WHERE m.content LIKE ?
                ORDER BY m.id DESC LIMIT ?
                """,
                (q, int(limit)),
            ).fetchall()
        return [
            {"session_id": r[0], "title": r[1], "role": r[2], "snippet": r[3], "created_at": r[4]}
            for r in rows
        ]

    def _append_session_message(self, session_id: str, role: str, content: str, tool_trace: str = "", title_hint: str = "") -> None:
        self._init_sessions_db()
        now = utc_now_iso()
        title = (title_hint or content[:54] or "Hermes Session").replace("\n", " ").strip()
        with sqlite3.connect(self.sessions_db_path) as conn:
            existing = conn.execute("SELECT session_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO sessions (session_id, title, created_at, updated_at, summary) VALUES (?, ?, ?, ?, '')",
                    (session_id, title, now, now),
                )
            else:
                conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_trace, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, tool_trace, now),
            )
            conn.commit()

    def list_skills(self) -> list[dict[str, str]]:
        if not self.skills_dir.exists():
            return []
        out: list[dict[str, str]] = []
        for sk in sorted(self.skills_dir.glob("*/SKILL.md")):
            out.append(
                {
                    "slug": sk.parent.name,
                    "path": str(sk),
                    "content": sk.read_text(encoding="utf-8", errors="ignore"),
                }
            )
        return out

    def save_skill(self, slug: str, description: str, instructions: str) -> Path:
        clean_slug = re.sub(r"[^a-z0-9_-]+", "-", (slug or "custom-skill").lower()).strip("-") or "custom-skill"
        sdir = self.skills_dir / clean_slug
        sdir.mkdir(parents=True, exist_ok=True)
        spath = sdir / "SKILL.md"
        spath.write_text(
            f"---\nname: {clean_slug}\ndescription: {description.strip()}\n---\n\n{instructions.strip()}\n",
            encoding="utf-8",
        )
        return spath

    def append_memory(self, fact: str, target: str = "MEMORY.md") -> Path:
        self.ensure_hermes_home()
        p = self.user_md_path if target == "USER.md" else self.memory_path
        cur = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
        line = f"- [{utc_now_iso()[:10]}] {fact.strip()}"
        if fact.strip() and fact.strip() not in cur:
            p.write_text(cur.rstrip() + "\n" + line + "\n", encoding="utf-8")
        return p

    def list_cron_jobs(self) -> list[dict[str, Any]]:
        if not self.cron_path.exists():
            return []
        try:
            raw = json.loads(self.cron_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    def add_cron_job(self, name: str, prompt: str, interval_minutes: int = 60) -> dict[str, Any]:
        self.ensure_hermes_home()
        jobs = self.list_cron_jobs()
        job = {
            "job_id": f"cron_{int(time.time())}_{len(jobs) + 1}",
            "name": name.strip() or "Scheduled Hermes Task",
            "prompt": prompt.strip(),
            "interval_minutes": max(5, int(interval_minutes)),
            "enabled": True,
            "last_run_ts": 0.0,
            "last_status": "pending",
        }
        jobs.append(job)
        self.cron_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        return job

    def delete_cron_job(self, job_id: str) -> bool:
        jobs = [j for j in self.list_cron_jobs() if j.get("job_id") != job_id]
        self.cron_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        return True

    def run_due_cron_jobs(self, config: AppConfig) -> list[dict[str, Any]]:
        if not getattr(config, "hermes_cron_enabled", False):
            return []
        jobs = self.list_cron_jobs()
        now = time.time()
        ran: list[dict[str, Any]] = []
        changed = False
        plan = self.compute_gpu_throttle_plan(config)
        if plan.get("defer_background_cron"):
            return []
        for job in jobs:
            if not job.get("enabled"):
                continue
            interval_s = max(300, int(job.get("interval_minutes", 60)) * 60)
            if (now - float(job.get("last_run_ts", 0.0))) >= interval_s:
                res = self.run_prompt(str(job.get("prompt", "")), config, session_id=f"cron_{job.get('job_id')}")
                job["last_run_ts"] = now
                job["last_status"] = "ok" if res.ok else f"error: {res.error}"
                changed = True
                ran.append({"job_id": job.get("job_id"), "ok": res.ok, "reply": res.reply[:240]})
        if changed:
            self.cron_path.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        return ran

    # ------------------------------------------------------------------
    # Game Learning & Live GPU / ~1 GB VRAM Headroom Throttling
    # ------------------------------------------------------------------
    def _load_learned_games(self) -> dict[str, dict[str, Any]]:
        if not self.learned_games_path.exists():
            return {}
        try:
            raw = json.loads(self.learned_games_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_learned_games(self, data: dict[str, dict[str, Any]]) -> None:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        tmp = self.learned_games_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.learned_games_path)

    def list_learned_games(self) -> list[dict[str, Any]]:
        data = self._load_learned_games()
        items = list(data.values())
        items.sort(key=lambda x: (int(x.get("launch_count", 0)), str(x.get("last_seen", ""))), reverse=True)
        return items

    def add_learned_game(self, process_name: str, *, peak_vram_mb: float = 0.0, user_pinned: bool = True) -> dict[str, Any]:
        key = re.sub(r"\.(exe|bin|x86_64)$", "", (process_name or "").strip().lower())
        if not key:
            return {}
        with self._lock:
            data = self._load_learned_games()
            rec = dict(data.get(key) or {})
            rec["key"] = key
            rec["display_name"] = process_name.strip()
            rec["launch_count"] = max(1, int(rec.get("launch_count", 0)) + (0 if key in data else 1))
            rec["peak_vram_mb"] = round(max(float(rec.get("peak_vram_mb", 0.0) or 0.0), float(peak_vram_mb or 0.0)), 1)
            rec["peak_gpu_util_pct"] = float(rec.get("peak_gpu_util_pct", 0.0) or 0.0)
            rec["user_pinned"] = bool(rec.get("user_pinned", False) or user_pinned)
            rec["last_seen"] = utc_now_iso()
            data[key] = rec
            self._save_learned_games(data)
            return rec

    def remove_learned_game(self, process_key: str) -> bool:
        key = re.sub(r"\.(exe|bin|x86_64)$", "", (process_key or "").strip().lower())
        with self._lock:
            data = self._load_learned_games()
            if key in data:
                data.pop(key, None)
                self._save_learned_games(data)
                return True
            return False

    @staticmethod
    def _normalize_proc_key(name: str) -> str:
        clean = (name or "").strip().lower()
        clean = re.sub(r"\.(exe|bin|x86_64)$", "", clean)
        return clean

    def _is_known_or_learned_game(self, proc_name: str, learned: dict[str, dict[str, Any]]) -> bool:
        key = self._normalize_proc_key(proc_name)
        if not key or key in _NON_GAME_SYSTEM_PROCS:
            return False
        if key in learned:
            return True
        return any(sig in key for sig in _KNOWN_GAME_SIGNATURES)

    def observe_running_workload(
        self,
        processes: list[Any] | None = None,
        *,
        focus_process: str = "",
        is_fullscreen: bool = False,
        gpu: Any = None,
        cpu_percent: float | None = None,
        ram_percent: float | None = None,
    ) -> dict[str, Any] | None:
        """Learns whole-PC usage patterns, remote tasks/services, and active games with VRAM & GPU usage."""
        gpu_obj = gpu if gpu is not None else GpuTelemetryCollector.collect()
        gpu_util = _parse_float_safe(getattr(gpu_obj, "utilization_percent", None)) or 0.0
        vram_used = _parse_float_safe(getattr(gpu_obj, "vram_used_mb", None)) or 0.0
        self.observe_pc_and_remote_usage(
            processes,
            focus_process=focus_process,
            is_fullscreen=is_fullscreen,
            gpu_util=gpu_util,
            vram_used_mb=vram_used,
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
        )
        learned = self._load_learned_games()

        detected_name = ""
        # 1. Check focused fullscreen app (excluding desktop shells/browsers/ollama)
        f_key = self._normalize_proc_key(focus_process)
        if f_key and f_key not in _NON_GAME_SYSTEM_PROCS:
            if is_fullscreen or self._is_known_or_learned_game(f_key, learned):
                detected_name = focus_process.strip()

        # 2. Scan running process list for known/learned games
        if not detected_name and processes:
            for proc in processes:
                pname = str(getattr(proc, "name", "") if not isinstance(proc, dict) else proc.get("name", ""))
                if self._is_known_or_learned_game(pname, learned):
                    detected_name = pname
                    break

        if not detected_name:
            self.apply_game_profile_if_active(None, self.cfg_mgr.load())
            return None

        d_key = self._normalize_proc_key(detected_name)
        with self._lock:
            data = self._load_learned_games()
            prev = dict(data.get(d_key) or {})
            prev_last = str(prev.get("last_seen", ""))
            # Only increment launch_count if not seen in the last 3 minutes
            bump_launch = 1 if (not prev_last or prev_last[:15] != utc_now_iso()[:15]) else 0
            rec = {
                "key": d_key,
                "display_name": prev.get("display_name") or detected_name,
                "launch_count": int(prev.get("launch_count", 0)) + bump_launch or 1,
                "peak_vram_mb": round(max(float(prev.get("peak_vram_mb", 0.0) or 0.0), vram_used), 1),
                "peak_gpu_util_pct": round(max(float(prev.get("peak_gpu_util_pct", 0.0) or 0.0), gpu_util), 1),
                "user_pinned": bool(prev.get("user_pinned", False)),
                "profile": prev.get("profile") or {"power_preference": "performance", "silence_bg_apps": True},
                "last_seen": utc_now_iso(),
            }
            data[d_key] = rec
            self._save_learned_games(data)
        self.apply_game_profile_if_active(rec, self.cfg_mgr.load())
        return rec

    def set_game_profile(
        self,
        proc_or_name: str,
        *,
        power_preference: str = "performance",
        silence_bg_apps: bool = True,
    ) -> dict[str, Any]:
        """Saves per-game auto-tuning preferences in learned_games.json."""
        key = self._normalize_proc_key(proc_or_name)
        if not key:
            return {}
        with self._lock:
            data = self._load_learned_games()
            rec = dict(data.get(key) or {"key": key, "display_name": proc_or_name.strip(), "launch_count": 1, "peak_vram_mb": 0.0})
            rec["profile"] = {
                "power_preference": power_preference,
                "silence_bg_apps": bool(silence_bg_apps),
            }
            rec["user_pinned"] = True
            data[key] = rec
            self._save_learned_games(data)
            return rec

    def apply_game_profile_if_active(self, active_game_rec: dict[str, Any] | None, config: AppConfig) -> str:
        """Applies per-game tuning when a game starts and restores defaults on exit."""
        if active_game_rec:
            gkey = str(active_game_rec.get("key") or "")
            if gkey and gkey != self._active_game_profile_key:
                self._active_game_profile_key = gkey
                prof = active_game_rec.get("profile") or {"power_preference": "performance", "silence_bg_apps": True}
                if self.probalance is not None and prof.get("silence_bg_apps", True):
                    try:
                        # Restrain background CPU hogs immediately while preserving the active game
                        self.probalance.evaluate_tick([], str(active_game_rec.get("display_name") or ""), config, dry_run=False)
                    except Exception:
                        pass
                return f"Applied per-game tuning for {active_game_rec.get('display_name', gkey)}"
            return ""
        if self._active_game_profile_key:
            old_k = self._active_game_profile_key
            self._active_game_profile_key = ""
            if self.probalance is not None:
                try:
                    self.probalance.revert_all(config, dry_run=False)
                except Exception:
                    pass
            return f"Restored desktop defaults after {old_k} exited"
        return ""

    def get_pc_usage_profile(self) -> dict[str, Any]:
        if not self.pc_usage_path.exists():
            return {
                "samples": 0,
                "apps": {},
                "categories": {},
                "avg_cpu_pct": 0.0,
                "peak_cpu_pct": 0.0,
                "avg_ram_pct": 0.0,
                "peak_ram_pct": 0.0,
                "peak_gpu_pct": 0.0,
                "peak_vram_mb": 0.0,
                "remote_services_seen": {},
                "remote_tasks": [],
                "paired_devices_snapshot": [],
                "last_updated": "",
            }
        try:
            raw = json.loads(self.pc_usage_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_pc_usage_profile(self, prof: dict[str, Any]) -> None:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        tmp = self.pc_usage_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prof, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.pc_usage_path)

    @staticmethod
    def _classify_app_category(proc_key: str) -> str:
        if any(k in proc_key for k in ("code", "pycharm", "cargo", "rustc", "gcc", "clang", "node", "python", "git", "nvim", "vim", "jetbrains")):
            return "coding_dev"
        if any(k in proc_key for k in ("blender", "obs", "kdenlive", "davinci", "gimp", "krita", "ffmpeg")):
            return "creative_media"
        if any(k in proc_key for k in ("firefox", "chrome", "chromium", "brave", "librewolf", "vivaldi", "edge")):
            return "browser_web"
        if any(k in proc_key for k in ("sunshine", "moonlight", "tailscale", "sshd", "adb", "scrcpy", "remmina", "rustdesk")):
            return "remote_continuity"
        if any(sig in proc_key for sig in _KNOWN_GAME_SIGNATURES):
            return "gaming"
        return "desktop_apps"

    def observe_pc_and_remote_usage(
        self,
        processes: list[Any] | None = None,
        *,
        focus_process: str = "",
        is_fullscreen: bool = False,
        gpu_util: float = 0.0,
        vram_used_mb: float = 0.0,
        cpu_percent: float | None = None,
        ram_percent: float | None = None,
    ) -> dict[str, Any]:
        """Auto-learns whole-PC usage patterns, frequent apps, resource baselines, and remote services/tasks."""
        cpu_val = float(cpu_percent) if cpu_percent is not None else float(psutil.cpu_percent(interval=None))
        ram_val = float(ram_percent) if ram_percent is not None else float(psutil.virtual_memory().percent)

        with self._lock:
            prof = self.get_pc_usage_profile()
            n = int(prof.get("samples", 0) or 0) + 1
            prof["samples"] = n
            prev_cpu = float(prof.get("avg_cpu_pct", 0.0) or 0.0)
            prev_ram = float(prof.get("avg_ram_pct", 0.0) or 0.0)
            prof["avg_cpu_pct"] = round(((prev_cpu * (n - 1)) + cpu_val) / n, 1)
            prof["peak_cpu_pct"] = round(max(float(prof.get("peak_cpu_pct", 0.0) or 0.0), cpu_val), 1)
            prof["avg_ram_pct"] = round(((prev_ram * (n - 1)) + ram_val) / n, 1)
            prof["peak_ram_pct"] = round(max(float(prof.get("peak_ram_pct", 0.0) or 0.0), ram_val), 1)
            prof["peak_gpu_pct"] = round(max(float(prof.get("peak_gpu_pct", 0.0) or 0.0), float(gpu_util or 0.0)), 1)
            prof["peak_vram_mb"] = round(max(float(prof.get("peak_vram_mb", 0.0) or 0.0), float(vram_used_mb or 0.0)), 1)

            apps = dict(prof.get("apps") or {})
            cats = dict(prof.get("categories") or {})
            rem_svcs = dict(prof.get("remote_services_seen") or {})

            seen_this_tick: set[str] = set()
            if focus_process:
                fk = self._normalize_proc_key(focus_process)
                if fk and fk not in ("desktop", "headless", ""):
                    seen_this_tick.add(fk)
            if processes:
                for pr in processes[:14]:
                    pname = str(getattr(pr, "name", "") if not isinstance(pr, dict) else pr.get("name", ""))
                    pk = self._normalize_proc_key(pname)
                    if pk and pk not in ("systemd", "init", "kthread", "idle", "astracradle"):
                        seen_this_tick.add(pk)

            for pk in seen_this_tick:
                rec = dict(apps.get(pk) or {})
                rec["hits"] = int(rec.get("hits", 0)) + 1
                rec["category"] = self._classify_app_category(pk)
                rec["last_seen"] = utc_now_iso()
                apps[pk] = rec
                cat = rec["category"]
                cats[cat] = int(cats.get(cat, 0)) + 1
                if cat == "remote_continuity":
                    rem_svcs[pk] = int(rem_svcs.get(pk, 0)) + 1

            # Cap stored app dictionary to top 60 apps by hits
            if len(apps) > 60:
                sorted_apps = sorted(apps.items(), key=lambda kv: int(kv[1].get("hits", 0)), reverse=True)[:60]
                apps = dict(sorted_apps)

            if self.device_registry is not None:
                try:
                    devs = self.device_registry.load_devices()
                    prof["paired_devices_snapshot"] = [
                        {"id": d.device_id, "name": d.name, "kind": d.device_type, "host": getattr(d, "network_host", "")}
                        for d in devs[:12]
                    ]
                except Exception:
                    pass

            prof["apps"] = apps
            prof["categories"] = cats
            prof["remote_services_seen"] = rem_svcs
            prof["last_fullscreen"] = bool(is_fullscreen)
            prof["last_updated"] = utc_now_iso()
            self._save_pc_usage_profile(prof)
            return prof

    def record_remote_task(self, target: str, task: str, ok: bool, detail: str = "") -> dict[str, Any]:
        with self._lock:
            prof = self.get_pc_usage_profile()
            hist = list(prof.get("remote_tasks") or [])
            entry = {
                "ts": utc_now_iso(),
                "target": (target or "local").strip(),
                "task": (task or "").strip()[:160],
                "ok": bool(ok),
                "detail": (detail or "").strip()[:200],
            }
            hist.append(entry)
            prof["remote_tasks"] = hist[-40:]
            prof["last_updated"] = utc_now_iso()
            self._save_pc_usage_profile(prof)
            return entry

    def compute_gpu_throttle_plan(
        self,
        config: AppConfig,
        *,
        gpu: Any = None,
        processes: list[Any] | None = None,
        focus_process: str = "",
        is_fullscreen: bool = False,
    ) -> dict[str, Any]:
        """Computes dynamic GPU offload %, ~1 GB VRAM headroom cap, and throttle pacing for Hermes local AI."""
        mode = str(getattr(config, "hermes_gpu_limit_mode", "game_adaptive") or "game_adaptive")
        if mode not in HERMES_GPU_MODES:
            mode = "game_adaptive"

        reserve_mb = max(512, int(getattr(config, "hermes_vram_reserve_mb", 1024) or 1024))
        base_ctx = int(getattr(config, "hermes_context_tokens", 32768) or 32768)

        gpu_obj = gpu if gpu is not None else GpuTelemetryCollector.collect()
        vram_total = _parse_float_safe(getattr(gpu_obj, "vram_total_mb", None))
        vram_used = _parse_float_safe(getattr(gpu_obj, "vram_used_mb", None))
        gpu_util = _parse_float_safe(getattr(gpu_obj, "utilization_percent", None))

        learned = self._load_learned_games()
        active_game: dict[str, Any] | None = None

        # Detect active game from focus or process list (or live psutil if processes not supplied)
        f_key = self._normalize_proc_key(focus_process)
        if f_key and (self._is_known_or_learned_game(f_key, learned) or (is_fullscreen and f_key not in _NON_GAME_SYSTEM_PROCS)):
            active_game = learned.get(f_key) or {"key": f_key, "display_name": focus_process, "peak_vram_mb": 0.0}

        if active_game is None:
            proc_names: list[str] = []
            if processes is not None:
                proc_names = [
                    str(getattr(pr, "name", "") if not isinstance(pr, dict) else pr.get("name", ""))
                    for pr in processes
                ]
            else:
                try:
                    proc_names = [p.info.get("name") or "" for p in psutil.process_iter(["name"])]
                except Exception:
                    proc_names = []
            for pname in proc_names:
                if self._is_known_or_learned_game(pname, learned):
                    pk = self._normalize_proc_key(pname)
                    active_game = learned.get(pk) or {"key": pk, "display_name": pname, "peak_vram_mb": 0.0}
                    break

        vram_headroom_mb: float | None = None
        if vram_total is not None and vram_total > 0 and vram_used is not None:
            # Strictly keep `reserve_mb` (~1024 MB) below max VRAM
            safe_ceiling = max(0.0, vram_total - float(reserve_mb))
            vram_headroom_mb = round(max(0.0, safe_ceiling - vram_used), 1)

        # Determine target GPU % and throttle parameters
        num_gpu_layers: int | None = None
        num_ctx = base_ctx
        num_batch = 512
        keep_alive = "5m"
        throttle_delay_sec = 0.0
        defer_cron = False
        baseline_layers = 36

        if mode in ("100", "75", "50", "25"):
            target_pct = int(mode)
            if target_pct < 100:
                num_gpu_layers = max(1, round(baseline_layers * (target_pct / 100.0)))
                num_batch = 256 if target_pct >= 50 else 128
                num_ctx = min(base_ctx, 24576 if target_pct >= 75 else (16384 if target_pct >= 50 else 8192))
                throttle_delay_sec = 0.0 if target_pct >= 75 else (0.25 if target_pct == 50 else 0.55)
            reason = f"Fixed GPU budget at {target_pct}%."
        else:
            # Optimized Game Mode: never 100% GPU, learns games, enforces ~1 GB VRAM headroom below max
            other_gpu_busy = (gpu_util is not None and gpu_util >= 25.0) or (
                vram_used is not None and vram_used >= 900.0 and vram_total is not None and (vram_used / max(1.0, vram_total)) >= 0.18
            )
            if active_game is not None:
                # Active game detected — strong throttle + guarantee ~1 GB VRAM headroom
                est_game_vram = float(active_game.get("peak_vram_mb", 0.0) or 0.0)
                if vram_headroom_mb is not None and vram_headroom_mb < 512.0:
                    target_pct = 15
                    num_gpu_layers = 0  # Fallback to CPU/RAM so game keeps full VRAM + 1 GB margin
                    num_ctx = min(base_ctx, 8192)
                    num_batch = 64
                    throttle_delay_sec = 1.2
                    keep_alive = "15s"
                    defer_cron = True
                    reason = (
                        f"Game '{active_game.get('display_name')}' active & VRAM tight "
                        f"({vram_headroom_mb:.0f} MB above 1 GB reserve) — offloading AI to CPU/RAM."
                    )
                elif vram_headroom_mb is not None and vram_headroom_mb < 2048.0:
                    target_pct = 25
                    num_gpu_layers = max(4, round(baseline_layers * 0.25))
                    num_ctx = min(base_ctx, 8192)
                    num_batch = 128
                    throttle_delay_sec = 0.85
                    keep_alive = "30s"
                    defer_cron = bool(gpu_util is not None and gpu_util >= 80.0)
                    reason = (
                        f"Game '{active_game.get('display_name')}' active — capped AI to 25% GPU, "
                        f"preserving {reserve_mb} MB (~1 GB) VRAM safety margin."
                    )
                else:
                    # Even if VRAM is roomy, cap GPU below 40% during active game (or respect learned peak VRAM)
                    target_pct = 35
                    if vram_total and est_game_vram > 0 and (vram_total - est_game_vram - reserve_mb) < 2048:
                        target_pct = 25
                    num_gpu_layers = max(6, round(baseline_layers * (target_pct / 100.0)))
                    num_ctx = min(base_ctx, 16384)
                    num_batch = 128
                    throttle_delay_sec = 0.6
                    keep_alive = "45s"
                    defer_cron = bool(gpu_util is not None and gpu_util >= 85.0)
                    reason = (
                        f"Learned game '{active_game.get('display_name')}' active — throttled AI to {target_pct}% GPU "
                        f"with {reserve_mb} MB (~1 GB) VRAM headroom."
                    )
            elif other_gpu_busy:
                target_pct = 50 if (gpu_util or 0.0) < 65.0 else 35
                if vram_headroom_mb is not None and vram_headroom_mb < 600.0:
                    target_pct = 20
                    num_gpu_layers = 0
                    num_ctx = min(base_ctx, 8192)
                else:
                    num_gpu_layers = max(6, round(baseline_layers * (target_pct / 100.0)))
                    num_ctx = min(base_ctx, 16384)
                num_batch = 192
                throttle_delay_sec = 0.4
                keep_alive = "1m"
                reason = (
                    f"Other GPU workload detected ({(gpu_util or 0.0):.0f}% util) — "
                    f"throttled AI to {target_pct}% while reserving {reserve_mb} MB (~1 GB) VRAM."
                )
            else:
                # Idle desktop: never take 100% GPU so desktop compositing & app launches stay instant
                target_pct = 80
                if vram_headroom_mb is not None and vram_headroom_mb < 1024.0:
                    target_pct = 45
                    num_gpu_layers = max(8, round(baseline_layers * 0.45))
                    num_ctx = min(base_ctx, 16384)
                else:
                    num_gpu_layers = round(baseline_layers * 0.80)
                num_batch = 256
                keep_alive = "3m"
                reason = (
                    f"Optimized Game Mode active (idle cap {target_pct}% GPU, never 100%; "
                    f"reserves {reserve_mb} MB / ~1 GB VRAM below max)."
                )

        return {
            "mode": mode,
            "label": HERMES_GPU_MODES[mode]["label"],
            "target_gpu_pct": target_pct,
            "active_game": active_game.get("display_name") if active_game else "",
            "learned_games_count": len(learned),
            "vram_total_mb": vram_total,
            "vram_used_mb": vram_used,
            "vram_reserve_mb": reserve_mb,
            "vram_headroom_mb": vram_headroom_mb,
            "gpu_util_pct": gpu_util,
            "num_gpu_layers": num_gpu_layers,
            "num_ctx": num_ctx,
            "num_batch": num_batch,
            "keep_alive": keep_alive,
            "throttle_delay_sec": throttle_delay_sec,
            "defer_background_cron": defer_cron,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Native Tool Registry + Safety Guard + Autonomous Loop
    # ------------------------------------------------------------------
    def _tool_schemas(self) -> list[dict[str, Any]]:
        base_tools = [
            {
                "type": "function",
                "function": {
                    "name": "astra_system_status",
                    "description": "Collect live CPU, RAM, disk, GPU, temperature, and top process telemetry from AstraCradle.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_deep_scan",
                    "description": "Run AstraCradle 8-subsystem diagnostics and return health score plus actionable findings.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_fix_safe",
                    "description": "Run AstraCradle safe beginner cache/temp cleanup (supports dry_run=true preview).",
                    "parameters": {
                        "type": "object",
                        "properties": {"dry_run": {"type": "boolean", "description": "Preview only when true"}},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_wifi_check",
                    "description": "Inspect Wi-Fi radio and recommend less-congested 2.4/5/6 GHz channels.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_process_lookup",
                    "description": "Look up a running PID or process name in plain English.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "PID number or process name"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_pc_usage_summary",
                    "description": "Inspect what Hermes has automatically learned about how the user uses their PC as a whole (top apps, workflow mix, CPU/RAM/GPU peaks, learned games, and remote tasks/devices).",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_wifi_optimize",
                    "description": "Check Wi-Fi channels/power-save AND optionally apply the cleanest Wi-Fi channel when apply=true.",
                    "parameters": {
                        "type": "object",
                        "properties": {"apply": {"type": "boolean", "description": "True to actually apply the best channel, false to only check"}},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_probalance_and_profile",
                    "description": "Inspect or fix background CPU hogs and switch AstraCradle power/situation profiles (Balanced Daily, Gaming / Low-Latency, Silent / Battery, Workstation / Render).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["status", "restrain_hogs", "set_profile", "revert"]},
                            "profile_name": {"type": "string", "description": "Used when action=set_profile"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_process_manage",
                    "description": "Safely lower priority (renice) or close a non-protected runaway user process by PID (strictly blocks OS-critical processes).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pid": {"type": "integer"},
                            "action": {"type": "string", "enum": ["renice", "terminate"]},
                            "reason": {"type": "string"},
                        },
                        "required": ["pid", "action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_startup_audit",
                    "description": "List startup autorun programs or safely disable an unnecessary user startup entry (with rollback support).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["list", "disable"]},
                            "entry_id": {"type": "string", "description": "Startup entry ID to disable when action=disable"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_remote_tasks",
                    "description": "Inspect paired mobile/remote devices, Tailscale/Sunshine/ADB status, auto-connect proximity devices, or launch/record a remote task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["status", "auto_connect", "launch_device", "adb_bridge"]},
                            "device_id": {"type": "string"},
                            "serial": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_self_heal_repair",
                    "description": "Run AstraCradle 14-invariant self-heal & integrity repair to fix config, SQLite, or certificate issues.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_rollback_undo",
                    "description": "List recent AstraCradle rollback/quarantine entries or undo a specific entry_id safely.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["list", "undo"]},
                            "entry_id": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_read",
                    "description": "Read a local non-protected text file with optional 1-based line offset & limit.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "offset": {"type": "integer", "description": "1-based start line (default 1)"},
                            "limit": {"type": "integer", "description": "Max lines to read (default 220)"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_write",
                    "description": "Create or overwrite a user-writable file outside protected OS directories (respects permission level).",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_edit",
                    "description": "Surgically edit an existing file by replacing one exact old_text block with new_text (automatically backs up the file first).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "List files and folders inside a directory (supports optional glob pattern).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "pattern": {"type": "string", "description": "Optional glob pattern like *.py"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep_search",
                    "description": "Search code or text files recursively for a regex/text pattern inside AstraCradle source or any user directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "path": {"type": "string", "description": "Root folder (defaults to AstraCradle local source root)"},
                            "glob": {"type": "string", "description": "File glob filter (default *.py)"},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the live web for improvements, Linux/CachyOS fixes, Python docs, or AstraCradle feature ideas.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_fetch",
                    "description": "Fetch and read readable text from a web page, documentation link, GitHub raw file, or SKILL.md URL.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_read",
                    "description": "Read MEMORY.md, USER.md, SOUL.md, and list all installed SKILL.md workflows.",
                    "parameters": {
                        "type": "object",
                        "properties": {"target": {"type": "string", "enum": ["all", "MEMORY.md", "USER.md", "skills"]}},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "skill_install_from_url",
                    "description": "Download a SKILL.md / guide from a web URL and install it into AstraCradle's Hermes skills folder.",
                    "parameters": {
                        "type": "object",
                        "properties": {"slug": {"type": "string"}, "url": {"type": "string"}},
                        "required": ["slug", "url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_dev_self_build",
                    "description": "Inspect AstraCradle's real local modules/pages/tests/commits, compile-check, run tests, diff, or locally reinstall AstraCradle's source build.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["inspect", "locate", "compile_check", "run_tests", "self_check", "git_status", "git_diff", "reinstall_local"],
                            },
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "shell_exec",
                    "description": "Run a non-destructive diagnostic/automation command (up to just below sudo; or prompts user for sudo password if Level 4 is enabled).",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sudo_exec",
                    "description": "Run an administrative/sudo command ONLY after prompting the user to type their sudo/admin password (requires Level 4 permission).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "reason": {"type": "string", "description": "Plain-English reason shown to the user in the password dialog"},
                        },
                        "required": ["command", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_save",
                    "description": "Persist a durable user preference or machine fact into MEMORY.md or USER.md.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "fact": {"type": "string"},
                            "target": {"type": "string", "enum": ["MEMORY.md", "USER.md"]},
                        },
                        "required": ["fact"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "skill_save",
                    "description": "Save a reusable SKILL.md procedure learned from solving a task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string"},
                            "description": {"type": "string"},
                            "instructions": {"type": "string"},
                        },
                        "required": ["slug", "description", "instructions"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delegate_subagent",
                    "description": "Spawn an isolated Hermes subagent to focus on a subtask and return its summary.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task": {"type": "string"}},
                        "required": ["task"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cron_schedule",
                    "description": "Schedule a recurring Hermes automation task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "prompt": {"type": "string"},
                            "interval_minutes": {"type": "integer"},
                        },
                        "required": ["name", "prompt"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_custom_tool_forge",
                    "description": "Create, combine, improve, run, toggle, or delete Hermes's own reusable custom tools (composite macro pipelines or Python scripts) to automate recurring workflows.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["list", "create_composite", "create_python", "combine", "improve", "run", "toggle", "delete"],
                            },
                            "name": {"type": "string", "description": "Tool slug (auto-prefixed with custom_)"},
                            "description": {"type": "string"},
                            "steps": {
                                "type": "array",
                                "items": {"type": "object"},
                                "description": "Ordered list of {tool, args} steps for create_composite or improve",
                            },
                            "source_tools": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of built-in or custom tool names to merge when action=combine",
                            },
                            "code": {"type": "string", "description": "Python code with def run(args: dict) -> dict for create_python/improve"},
                            "args": {"type": "object", "description": "Arguments when action=run"},
                            "enabled": {"type": "boolean"},
                            "improvement_note": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                },
            },
        ]
        tools_list = [
            # Dynamic enabled custom tools registered by the agent
        ]
        try:
            for ct in self.list_custom_tools(include_disabled=False)[:20]:
                tools_list.append(
                    {
                        "type": "function",
                        "function": {
                            "name": ct["name"],
                            "description": f"[Custom {ct.get('kind', 'composite')} v{ct.get('version', 1)}] {ct.get('description', '')}",
                            "parameters": ct.get("parameters") or {"type": "object", "properties": {}, "required": []},
                        },
                    }
                )
        except Exception:
            pass
        return base_tools + tools_list

    def _effective_permission_level(self, config: AppConfig) -> str:
        lvl = str(getattr(config, "hermes_permission_level", "safe_user") or "safe_user")
        if lvl not in HERMES_PERMISSION_LEVELS:
            lvl = "safe_user"
        # Backward compat: if legacy hermes_allow_shell=True on safe_user, upgrade to standard_user
        if lvl == "safe_user" and bool(getattr(config, "hermes_allow_shell", False)):
            return "standard_user"
        return lvl

    @staticmethod
    def get_local_source_root() -> Path:
        """Detects AstraCradle's local editable source build root for native self-development."""
        here_root = Path(__file__).resolve().parents[2]
        if (here_root / "astracradle" / "__main__.py").exists():
            return here_root
        for cand in (
            Path.home() / "AstraCradle-v2.5.1",
            Path.home() / "AstraCradle-git",
            Path.home() / "astracradle",
        ):
            if (cand / "astracradle" / "__main__.py").exists():
                return cand
        return here_root

    @staticmethod
    def _strip_html_to_text(raw_html: str) -> str:
        clean = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
        clean = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|br|pre)>", "\n", clean)
        clean = re.sub(r"(?s)<[^>]+>", " ", clean)
        clean = _html.unescape(clean)
        lines = [" ".join(ln.split()) for ln in clean.splitlines()]
        return "\n".join(ln for ln in lines if ln)

    def _web_search_impl(self, query: str, max_results: int = 6) -> list[dict[str, str]]:
        q = (query or "").strip()
        if not q:
            return []
        results: list[dict[str, str]] = []
        encoded = urllib.parse.quote_plus(q)
        # 1. DuckDuckGo HTML lightweight search (no API key needed)
        try:
            req = urllib.request.Request(
                f"https://html.duckduckgo.com/html/?q={encoded}",
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AstraCradle-Hermes/2.5.1"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            links = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
            snippets = re.findall(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>', body, re.S)
            for idx, (href, title_raw) in enumerate(links[: max(1, min(10, int(max_results)))]):
                real_url = _html.unescape(href)
                if "uddg=" in real_url:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(real_url).query)
                    if qs.get("uddg"):
                        real_url = qs["uddg"][0]
                snip_raw = ""
                if idx < len(snippets):
                    snip_raw = snippets[idx][0] or snippets[idx][1] or ""
                results.append(
                    {
                        "title": self._strip_html_to_text(title_raw)[:140],
                        "url": real_url,
                        "snippet": self._strip_html_to_text(snip_raw)[:280],
                    }
                )
        except Exception:
            pass

        # 2. Fallback to DuckDuckGo Instant Answer API if HTML returned nothing
        if not results:
            try:
                req2 = urllib.request.Request(
                    f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1",
                    headers={"User-Agent": "AstraCradle-Hermes/2.5.1"},
                )
                with urllib.request.urlopen(req2, timeout=8) as resp2:
                    d = json.loads(resp2.read().decode("utf-8", errors="ignore"))
                if d.get("AbstractText"):
                    results.append(
                        {
                            "title": str(d.get("Heading") or q),
                            "url": str(d.get("AbstractURL") or ""),
                            "snippet": str(d.get("AbstractText"))[:320],
                        }
                    )
                for rt in d.get("RelatedTopics") or []:
                    if isinstance(rt, dict) and rt.get("Text") and rt.get("FirstURL"):
                        results.append(
                            {
                                "title": str(rt.get("Text"))[:100],
                                "url": str(rt.get("FirstURL")),
                                "snippet": str(rt.get("Text"))[:260],
                            }
                        )
                        if len(results) >= max_results:
                            break
            except Exception:
                pass
        return results

    def _check_write_permission(self, target_path: Path, perm: str, adapter: Any) -> str | None:
        if perm == "read_only":
            return "Blocked by Read-Only mode (Level 1)."
        if adapter.is_path_protected(target_path):
            return f"Blocked modification of protected OS path: {target_path}"
        if perm == "safe_user":
            try:
                target_path.relative_to(self.cfg_mgr.base_dir.resolve())
            except ValueError:
                return (
                    "Level 2 (Safe Tune-Ups Only) restricts file writes/edits to ~/.astracradle. "
                    "Switch to Level 3 (Up to Just Below Sudo) or Level 4 to edit user files & AstraCradle source."
                )
        return None

    def _run_sudo_prompted(self, cmd: str, reason: str, config: AppConfig) -> dict[str, Any]:
        lvl = self._effective_permission_level(config)
        if lvl != "sudo_with_prompt":
            return {
                "ok": False,
                "blocked": True,
                "error": "Sudo/admin commands require Permission Level 4 ('User Control + Sudo Only When You Type Password'). Current level stops below sudo.",
            }
        clean_cmd = cmd.strip()
        if clean_cmd.startswith("sudo "):
            clean_cmd = clean_cmd[5:].strip()
        if not clean_cmd:
            return {"ok": False, "error": "Empty sudo command"}
        dangerous = ("rm -rf /", "mkfs", "format ", ":(){", "dd if=", "shutdown", "reboot", "del /s /q c:\\")
        if any(tok in clean_cmd.lower() for tok in dangerous):
            return {"ok": False, "blocked": True, "error": "Command matched destructive pattern guard even for sudo."}

        runner = self.elevation
        if runner is None:
            import getpass
            from .elevation import ElevationRunner

            def _cli_pw(title: str, prompt_reason: str) -> str | None:
                try:
                    if not sys.stdin or not sys.stdin.isatty():
                        return None
                    print(f"\n[Hermes AI Sudo Request] {title}\nReason: {prompt_reason}")
                    return getpass.getpass("Type sudo password (or Enter to cancel): ") or None
                except Exception:
                    return None

            runner = ElevationRunner(password_prompt_cb=_cli_pw)

        argv = ["cmd", "/c", clean_cmd] if platform.system() == "Windows" else ["sh", "-c", clean_cmd]
        res = runner.run_privileged(
            argv,
            reason=f"{reason}\n\nCommand: {clean_cmd}",
            title="Hermes AI — Sudo / Admin Password Required",
            timeout=25,
        )
        self.journal.log_event(
            "HermesOS",
            "sudo_exec",
            f"Ran elevated: {clean_cmd[:110]} (method={res.method}, rc={res.returncode}, cancelled={res.cancelled})",
            reason[:140] or "User typed sudo password for Hermes AI task",
            "ok" if res.ok else ("cancelled" if res.cancelled else "warning"),
        )
        return {
            "ok": res.ok,
            "cancelled": res.cancelled,
            "returncode": res.returncode,
            "stdout": (res.stdout or "")[:3000],
            "stderr": (res.stderr or res.message or "")[:1000],
        }

    def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        config: AppConfig,
        *,
        allow_subagent: bool = True,
    ) -> dict[str, Any]:
        adapter = get_os_adapter()
        perm = self._effective_permission_level(config)
        try:
            if name == "astra_system_status":
                tc = self.telemetry or TelemetryCollector(config.excluded_processes)
                snap = tc.collect(adapter, include_dns=False, include_process_details=False)
                gpu = GpuTelemetryCollector.collect()
                return {
                    "ok": True,
                    "os": f"{snap.os_family} {snap.os_release}",
                    "cpu_percent": snap.cpu_percent,
                    "ram_percent": snap.mem_percent,
                    "ram_used": format_bytes(snap.mem_used_bytes),
                    "ram_total": format_bytes(snap.mem_total_bytes),
                    "gpu": gpu.name if gpu.available else "Integrated",
                    "gpu_temp_c": gpu.temperature_c,
                    "top_processes": [
                        {"pid": p.pid, "name": p.name, "cpu": p.cpu_percent, "mem_mb": p.memory_mb}
                        for p in snap.top_processes[:6]
                    ],
                }

            if name == "astra_deep_scan":
                tc = self.telemetry or TelemetryCollector(config.excluded_processes)
                snap = tc.collect(adapter, include_dns=True, include_process_details=False)
                from .diagnostics import DiagnosticsEngine

                diag = self.diagnostics or DiagnosticsEngine()
                rep = diag.run_scan(snap, adapter, config)
                return {
                    "ok": True,
                    "health_score": rep.health_score,
                    "findings_count": len(rep.findings),
                    "findings": [
                        {"title": f.title, "severity": f.severity, "summary": f.summary}
                        for f in rep.findings[:8]
                    ],
                }

            if name == "astra_fix_safe":
                dry = bool(args.get("dry_run", True))
                if perm == "read_only" and not dry:
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1). Preview with dry_run=true instead."}
                if self.safe_fixer is not None:
                    ok_f, summary, notes = self.safe_fixer.run_fix_everything_safe(adapter, config, dry_run=dry)
                    extra: list[str] = []
                    if not dry and self.executor is not None:
                        try:
                            dns_res = self.executor.execute_action("flush_dns", adapter, config, dry_run=False)
                            extra.append(dns_res.summary)
                        except Exception:
                            pass
                        if self.ram_standby_mgr is not None:
                            try:
                                _, ram_msg = self.ram_standby_mgr.trim_standby(dry_run=False)
                                extra.append(ram_msg)
                            except Exception:
                                pass
                    return {"ok": ok_f, "dry_run": dry, "summary": summary, "notes": (notes + extra)[:10]}
                return {"ok": False, "error": "SafeBeginnerFixer not attached in headless CLI."}

            if name == "astra_wifi_check":
                if self.wifi_optimizer is not None:
                    rec = self.wifi_optimizer.recommend_channels(config)
                    return {"ok": True, "summary": rec.get("summary"), "details": rec.get("details", [])[:6]}
                from .wifi_channel_optimizer import WifiChannelOptimizer

                rec = WifiChannelOptimizer(self.journal).recommend_channels(config)
                return {"ok": True, "summary": rec.get("summary"), "details": rec.get("details", [])[:6]}

            if name == "astra_process_lookup":
                q = str(args.get("query", "")).strip()
                if not q:
                    return {"ok": False, "error": "Missing query"}
                if self.proc_lookup is not None:
                    res = self.proc_lookup.lookup(q, allow_web=False)
                    return {"ok": True, "name": res.name, "status": res.status, "description": res.brief_description}
                from .process_lookup import ProcessIntelligenceLookup

                res = ProcessIntelligenceLookup(self.cfg_mgr).lookup(q, allow_web=False)
                return {"ok": True, "name": res.name, "status": res.status, "description": res.brief_description}

            if name == "astra_pc_usage_summary":
                prof = self.get_pc_usage_profile()
                top_apps = sorted(
                    (prof.get("apps") or {}).items(),
                    key=lambda kv: int(kv[1].get("hits", 0)),
                    reverse=True,
                )[:12]
                return {
                    "ok": True,
                    "samples_recorded": prof.get("samples", 0),
                    "avg_cpu_pct": prof.get("avg_cpu_pct", 0.0),
                    "peak_cpu_pct": prof.get("peak_cpu_pct", 0.0),
                    "avg_ram_pct": prof.get("avg_ram_pct", 0.0),
                    "peak_ram_pct": prof.get("peak_ram_pct", 0.0),
                    "peak_gpu_pct": prof.get("peak_gpu_pct", 0.0),
                    "peak_vram_mb": prof.get("peak_vram_mb", 0.0),
                    "workflow_categories": prof.get("categories", {}),
                    "top_apps": [{"app": k, **v} for k, v in top_apps],
                    "learned_games": self.list_learned_games()[:10],
                    "remote_services_seen": prof.get("remote_services_seen", {}),
                    "paired_devices": prof.get("paired_devices_snapshot", []),
                    "recent_remote_tasks": (prof.get("remote_tasks") or [])[-8:],
                }

            if name == "astra_wifi_optimize":
                apply_now = bool(args.get("apply", False))
                if apply_now and perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1). Set apply=false to inspect."}
                from .wifi_channel_optimizer import WifiChannelOptimizer

                wo = self.wifi_optimizer or WifiChannelOptimizer(self.journal)
                if not apply_now:
                    rec = wo.recommend_channels(config)
                    return {"ok": True, "applied": False, "summary": rec.get("summary"), "details": rec.get("details", [])[:6]}
                res = wo.optimize_connection(config, dry_run=False)
                return {"ok": res.ok, "applied": True, "summary": res.summary, "details": getattr(res, "details", [])[:6]}

            if name == "astra_probalance_and_profile":
                act = str(args.get("action", "status"))
                if act != "status" and perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                from .probalance import ProBalanceEngine

                pb = self.probalance or ProBalanceEngine(self.cfg_mgr, self.journal)
                if act == "status":
                    return {
                        "ok": True,
                        "active_profile": config.active_profile,
                        "probalance_enabled": config.probalance_enabled,
                        "restrained_count": len(pb.restrained),
                        "recent_events": pb.recent_events[:6],
                    }
                if act == "set_profile":
                    target_prof = str(args.get("profile_name") or "Balanced Daily")
                    if target_prof not in ("Balanced Daily", "Gaming / Low-Latency", "Silent / Battery", "Workstation / Render"):
                        return {"ok": False, "error": f"Unknown profile '{target_prof}'."}
                    config.apply_profile(target_prof)
                    self.cfg_mgr.save(config)
                    return {"ok": True, "active_profile": config.active_profile}
                if act == "restrain_hogs":
                    tc = self.telemetry or TelemetryCollector(config.excluded_processes)
                    snap = tc.collect(adapter, include_dns=False, include_process_details=False)
                    prev_pb = config.probalance_enabled
                    config.probalance_enabled = True
                    notes = pb.evaluate_tick(snap.top_processes, "", config, dry_run=False)
                    config.probalance_enabled = prev_pb
                    return {"ok": True, "notes": notes[:8], "restrained_count": len(pb.restrained)}
                if act == "revert":
                    ok_r, msg_r = pb.revert_all(config, dry_run=False)
                    return {"ok": ok_r, "message": msg_r}
                return {"ok": False, "error": f"Unknown ProBalance action: {act}"}

            if name == "astra_process_manage":
                if perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                pid = int(args.get("pid", 0) or 0)
                act = str(args.get("action", "renice"))
                why = str(args.get("reason", "Hermes AI process management"))
                if pid <= 0:
                    return {"ok": False, "error": "Valid positive PID required."}
                if self.executor is None:
                    from .executor import GuardedExecutor
                    from .rollback import RollbackManager

                    ex = GuardedExecutor(self.cfg_mgr, self.rollback_mgr or RollbackManager(self.cfg_mgr), self.journal)
                else:
                    ex = self.executor
                if act == "terminate":
                    ok_ap, msg_ap = self._request_mutation_approval(
                        f"Terminate Process PID {pid}",
                        f"Hermes wants to terminate PID {pid}.\nReason: {why}",
                        config,
                    )
                    if not ok_ap:
                        return {"ok": False, "blocked": True, "error": msg_ap}
                    self._record_hermes_rollback("process_terminate", f"Terminated PID {pid}", why)
                    ok_p, msg_p = ex.safe_terminate_process(pid, config, why=why)
                else:
                    ok_p, msg_p = ex.safe_renice_process(pid, config)
                return {"ok": ok_p, "pid": pid, "action": act, "message": msg_p}

            if name == "astra_startup_audit":
                act = str(args.get("action", "list"))
                if act == "disable" and perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                from .autoruns_audit import AutorunsAndGhostAuditor
                from .rollback import RollbackManager

                aud = self.autoruns_auditor or AutorunsAndGhostAuditor(
                    self.cfg_mgr, self.rollback_mgr or RollbackManager(self.cfg_mgr), self.journal, elevation=self.elevation
                )
                entries = aud.scan_autoruns(adapter, include_system=False)
                if act == "list":
                    return {
                        "ok": True,
                        "count": len(entries),
                        "entries": [
                            {
                                "id": e.entry_id,
                                "name": e.name,
                                "enabled": e.enabled,
                                "impact": e.impact,
                                "explanation": getattr(e, "explanation", ""),
                            }
                            for e in entries[:15]
                        ],
                    }
                eid = str(args.get("entry_id", "")).strip()
                target_e = next((e for e in entries if e.entry_id == eid), None)
                if target_e is None:
                    return {"ok": False, "error": f"Startup entry '{eid}' not found among user entries."}
                ok_ap, msg_ap = self._request_mutation_approval(
                    f"Disable Startup Item '{target_e.name}'",
                    f"Hermes wants to disable startup autorun '{target_e.name}' ({eid}).",
                    config,
                )
                if not ok_ap:
                    return {"ok": False, "blocked": True, "error": msg_ap}
                ok_t, msg_t = aud.toggle_autorun_entry(target_e, enable=False, dry_run=False)
                return {"ok": ok_t, "entry_id": eid, "message": msg_t}

            if name == "astra_remote_tasks":
                act = str(args.get("action", "status"))
                if act != "status" and perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                from .mobile_continuity import DeviceRegistryManager, MobileCompanionEngine

                reg = self.device_registry or DeviceRegistryManager(self.cfg_mgr, self.journal)
                comp = self.mobile_companion or MobileCompanionEngine(self.cfg_mgr, reg, self.journal)
                if act == "status":
                    devs = reg.load_devices()
                    adb_devs = comp.scan_android_adb()
                    prof = self.get_pc_usage_profile()
                    return {
                        "ok": True,
                        "paired_devices": [
                            {"id": d.device_id, "name": d.name, "type": d.device_type, "host": getattr(d, "network_host", "")}
                            for d in devs
                        ],
                        "adb_devices": [{"serial": a.serial, "model": a.model, "state": a.state} for a in adb_devs],
                        "remote_services_seen": prof.get("remote_services_seen", {}),
                        "recent_remote_tasks": (prof.get("remote_tasks") or [])[-8:],
                    }
                if act == "auto_connect":
                    outcomes = comp.auto_connect_proximity_devices(config)
                    self.record_remote_task("proximity", "auto_connect", True, f"{len(outcomes)} target(s) checked")
                    return {"ok": True, "outcomes": [str(o) for o in outcomes[:8]]}
                if act == "launch_device":
                    did = str(args.get("device_id", "")).strip()
                    if not did:
                        return {"ok": False, "error": "Missing device_id"}
                    ok_l, msg_l = reg.launch_designated_program(did)
                    self.record_remote_task(did, "launch_designated_program", ok_l, msg_l)
                    return {"ok": ok_l, "message": msg_l}
                if act == "adb_bridge":
                    serial = str(args.get("serial", "")).strip()
                    ok_a, msg_a = comp.setup_android_adb_bridge(serial, int(getattr(config, "astrabeam_port", 47992) or 47992))
                    self.record_remote_task(serial or "android", "adb_bridge", ok_a, msg_a)
                    return {"ok": ok_a, "message": msg_a}
                return {"ok": False, "error": f"Unknown remote action: {act}"}

            if name == "astra_self_heal_repair":
                from .rollback import RollbackManager
                from .self_heal import SelfHealEngine

                rb = self.rollback_mgr or RollbackManager(self.cfg_mgr)
                sh = self.self_heal or SelfHealEngine(self.cfg_mgr, rb, self.journal)
                events, audit = sh.run_full_self_heal(
                    adapter,
                    config,
                    device_registry=self.device_registry,
                    companion=self.mobile_companion,
                )
                return {
                    "ok": True,
                    "checks_count": len(events),
                    "repaired": [e.check_name for e in events if getattr(e, "repaired", False)],
                    "warnings": [f"{e.check_name}: {e.details}" for e in events if e.status != "ok"][:8],
                    "audit_findings": len(audit),
                }

            if name == "astra_rollback_undo":
                act = str(args.get("action", "list"))
                from .rollback import RollbackManager

                rb = self.rollback_mgr or RollbackManager(self.cfg_mgr)
                entries = rb.load_entries()
                if act == "list":
                    return {
                        "ok": True,
                        "count": len(entries),
                        "entries": [
                            {"id": e.entry_id, "action": e.action_type, "summary": e.summary, "status": e.status}
                            for e in entries[-12:]
                        ],
                    }
                if perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                eid = str(args.get("entry_id", "")).strip()
                if not eid:
                    return {"ok": False, "error": "Missing entry_id"}
                ok_u, msg_u = rb.rollback_entry(eid)
                return {"ok": ok_u, "message": msg_u}

            if name == "file_read":
                p = Path(str(args.get("path", ""))).expanduser()
                if not p.exists() or not p.is_file():
                    return {"ok": False, "error": f"File not found: {p}"}
                if p.name == ".env":
                    return {"ok": False, "error": "Refused reading .env credentials directly."}
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
                offset = max(1, int(args.get("offset", 1) or 1))
                limit = max(1, min(400, int(args.get("limit", 220) or 220)))
                chunk = lines[offset - 1 : offset - 1 + limit]
                numbered = "\n".join(f"{offset + idx}: {ln}" for idx, ln in enumerate(chunk))
                return {
                    "ok": True,
                    "path": str(p),
                    "total_lines": len(lines),
                    "offset": offset,
                    "content": numbered[:9000],
                }

            if name == "file_write":
                p = Path(str(args.get("path", ""))).expanduser().resolve()
                err = self._check_write_permission(p, perm, adapter)
                if err:
                    return {"ok": False, "blocked": True, "error": err}
                if not str(p).startswith(str(self.hermes_home.resolve())):
                    ok_ap, msg_ap = self._request_mutation_approval(
                        f"Write File {p.name}",
                        f"Hermes wants to write file:\n{p}",
                        config,
                    )
                    if not ok_ap:
                        return {"ok": False, "blocked": True, "error": msg_ap}
                if p.exists() and p.is_file():
                    bdir = self.hermes_home / "edit_backups"
                    bdir.mkdir(parents=True, exist_ok=True)
                    bak_w = bdir / f"{p.name}.{int(time.time())}.bak"
                    bak_w.write_text(p.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
                    self._record_hermes_rollback("file_write", f"Wrote {p.name}", f"Backup saved at {bak_w}")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(args.get("content", "")), encoding="utf-8")
                post_verify: dict[str, Any] = {}
                if p.suffix == ".py":
                    import py_compile

                    try:
                        py_compile.compile(str(p), doraise=True)
                        post_verify["syntax_ok"] = True
                    except Exception as py_exc:
                        post_verify["syntax_ok"] = False
                        post_verify["syntax_error"] = str(py_exc)
                self.journal.log_event("HermesOS", "file_write", f"Wrote {p}", "Hermes tool execution", "ok")
                return {"ok": True, "path": str(p), **({"post_verify": post_verify} if post_verify else {})}

            if name == "file_edit":
                p = Path(str(args.get("path", ""))).expanduser().resolve()
                err = self._check_write_permission(p, perm, adapter)
                if err:
                    return {"ok": False, "blocked": True, "error": err}
                if not p.exists() or not p.is_file():
                    return {"ok": False, "error": f"File not found: {p}"}
                old_txt = str(args.get("old_text", ""))
                new_txt = str(args.get("new_text", ""))
                if not old_txt:
                    return {"ok": False, "error": "old_text cannot be empty."}
                cur = p.read_text(encoding="utf-8", errors="ignore")
                cnt = cur.count(old_txt)
                if cnt == 0:
                    return {"ok": False, "error": "old_text not found in file. Read the exact lines with file_read first."}
                if cnt > 1:
                    return {"ok": False, "error": f"old_text matched {cnt} places; pass more surrounding context for a unique match."}
                if not str(p).startswith(str(self.hermes_home.resolve())):
                    ok_ap, msg_ap = self._request_mutation_approval(
                        f"Edit File {p.name}",
                        f"Hermes wants to edit:\n{p}",
                        config,
                    )
                    if not ok_ap:
                        return {"ok": False, "blocked": True, "error": msg_ap}
                bdir = self.hermes_home / "edit_backups"
                bdir.mkdir(parents=True, exist_ok=True)
                bak_path = bdir / f"{p.name}.{int(time.time())}.bak"
                bak_path.write_text(cur, encoding="utf-8")
                self._record_hermes_rollback("file_edit", f"Edited {p.name}", f"Backup saved at {bak_path}")
                updated = cur.replace(old_txt, new_txt, 1)
                p.write_text(updated, encoding="utf-8")
                post_verify = {}
                if p.suffix == ".py":
                    import py_compile

                    try:
                        py_compile.compile(str(p), doraise=True)
                        post_verify["syntax_ok"] = True
                    except Exception as py_exc:
                        post_verify["syntax_ok"] = False
                        post_verify["syntax_error"] = str(py_exc)
                self.journal.log_event("HermesOS", "file_edit", f"Edited {p} (backup={bak_path.name})", "Hermes surgical edit", "ok")
                return {"ok": True, "path": str(p), "backup": str(bak_path), **({"post_verify": post_verify} if post_verify else {})}

            if name == "list_dir":
                raw_path = str(args.get("path", "") or "").strip()
                d = self.get_local_source_root() if raw_path in ("", ".") else Path(raw_path).expanduser().resolve()
                if not d.exists() or not d.is_dir():
                    return {"ok": False, "error": f"Directory not found: {d}"}
                pat = str(args.get("pattern", "*") or "*")
                items: list[dict[str, Any]] = []
                for child in sorted(d.glob(pat)):
                    if child.name in (".git", "__pycache__", ".venv", "node_modules"):
                        continue
                    items.append({"name": child.name, "is_dir": child.is_dir(), "size": (0 if child.is_dir() else child.stat().st_size)})
                    if len(items) >= 120:
                        break
                return {"ok": True, "path": str(d), "entries": items}

            if name == "grep_search":
                pat = str(args.get("pattern", ""))
                if not pat:
                    return {"ok": False, "error": "Empty search pattern"}
                root_arg = str(args.get("path", "")).strip()
                root_dir = Path(root_arg).expanduser().resolve() if root_arg else self.get_local_source_root()
                if not root_dir.exists():
                    return {"ok": False, "error": f"Search path not found: {root_dir}"}
                file_glob = str(args.get("glob", "*.py") or "*.py")
                try:
                    rx = re.compile(pat)
                except re.error:
                    rx = re.compile(re.escape(pat))
                matches: list[dict[str, Any]] = []
                skip_dirs = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", "dist", "build"}
                for fp in root_dir.rglob(file_glob):
                    if any(part in skip_dirs for part in fp.parts) or not fp.is_file():
                        continue
                    try:
                        for lno, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                            if rx.search(line):
                                matches.append({"file": str(fp.relative_to(root_dir)), "line": lno, "text": line.strip()[:160]})
                                if len(matches) >= 50:
                                    return {"ok": True, "root": str(root_dir), "matches": matches, "truncated": True}
                    except Exception:
                        continue
                return {"ok": True, "root": str(root_dir), "matches": matches, "truncated": False}

            if name == "web_search":
                if not bool(getattr(config, "hermes_allow_web_search", True)):
                    return {"ok": False, "blocked": True, "error": "Web Search is disabled in Hermes AI settings."}
                q = str(args.get("query", "")).strip()
                max_r = int(args.get("max_results", 6) or 6)
                hits = self._web_search_impl(q, max_results=max_r)
                if hits:
                    try:
                        summary_lines = [f"- {h.get('title', '')}: {h.get('snippet', '')} ({h.get('url', '')})" for h in hits[:5]]
                        self.store_library_item(
                            title=f"Web Search: {q[:100]}",
                            body="\n".join(summary_lines),
                            category="web_search",
                            source_type="web",
                            source_url=str(hits[0].get("url") or ""),
                            tags=q,
                            summary=str(hits[0].get("snippet") or q)[:280],
                            config=config,
                        )
                    except Exception:
                        pass
                return {"ok": True, "query": q, "results": hits}

            if name == "web_fetch":
                if not bool(getattr(config, "hermes_allow_web_search", True)):
                    return {"ok": False, "blocked": True, "error": "Web Search/Fetch is disabled in Hermes AI settings."}
                url = str(args.get("url", "")).strip()
                if not (url.startswith("https://") or url.startswith("http://")):
                    return {"ok": False, "error": "URL must start with http:// or https://"}
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AstraCradle-Hermes/2.5.1"},
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    raw = resp.read(200_000).decode("utf-8", errors="ignore")
                txt = self._strip_html_to_text(raw) if "<html" in raw.lower() or "<body" in raw.lower() else raw
                if txt.strip():
                    try:
                        self.store_library_item(
                            title=f"Web Page: {url[:110]}",
                            body=txt[:12000],
                            category="web_doc",
                            source_type="web",
                            source_url=url,
                            tags=url,
                            summary=txt.strip().splitlines()[0][:260] if txt.strip() else url,
                            config=config,
                        )
                    except Exception:
                        pass
                return {"ok": True, "url": url, "content": txt[:8000]}

            if name == "memory_read":
                tgt = str(args.get("target", "all") or "all")
                self.ensure_hermes_home(config)
                out: dict[str, Any] = {"ok": True}
                if tgt in ("all", "MEMORY.md"):
                    out["MEMORY.md"] = self.memory_path.read_text(encoding="utf-8", errors="ignore")[:4000]
                if tgt in ("all", "USER.md"):
                    out["USER.md"] = self.user_md_path.read_text(encoding="utf-8", errors="ignore")[:3000]
                if tgt in ("all", "skills"):
                    out["skills"] = [{"slug": s["slug"], "path": s["path"]} for s in self.list_skills()]
                return out

            if name == "skill_install_from_url":
                if perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                if not bool(getattr(config, "hermes_allow_web_search", True)):
                    return {"ok": False, "blocked": True, "error": "Web Fetch is disabled in Hermes AI settings."}
                url = str(args.get("url", "")).strip()
                slug = str(args.get("slug", "web-skill")).strip()
                if not (url.startswith("https://") or url.startswith("http://")):
                    return {"ok": False, "error": "URL must start with http:// or https://"}
                req = urllib.request.Request(url, headers={"User-Agent": "AstraCradle-Hermes/2.5.1"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    raw = resp.read(120_000).decode("utf-8", errors="ignore")
                clean = self._strip_html_to_text(raw) if "<html" in raw.lower() else raw
                sp = self.save_skill(slug, f"Installed from {url}", clean[:6000])
                return {"ok": True, "skill_path": str(sp)}

            if name == "astra_dev_self_build":
                act = str(args.get("action", "inspect") or "inspect").strip().lower()
                root = self.get_local_source_root()
                if act in ("locate", "inspect", "overview", "status", "list") or act not in (
                    "compile_check", "run_tests", "self_check", "git_status", "git_diff", "reinstall_local"
                ):
                    core_dir = root / "astracradle" / "core"
                    ui_dir = root / "astracradle" / "ui" / "pages"
                    tests_dir = root / "tests"
                    core_mods = [
                        {"file": f"astracradle/core/{fp.name}", "lines": sum(1 for _ in fp.open(encoding="utf-8", errors="ignore"))}
                        for fp in sorted(core_dir.glob("*.py"))
                    ] if core_dir.exists() else []
                    ui_pages = [
                        {"file": f"astracradle/ui/pages/{fp.name}", "lines": sum(1 for _ in fp.open(encoding="utf-8", errors="ignore"))}
                        for fp in sorted(ui_dir.glob("*.py"))
                    ] if ui_dir.exists() else []
                    test_files = [fp.name for fp in sorted(tests_dir.glob("test_*.py"))] if tests_dir.exists() else []
                    recent_commits: list[str] = []
                    if (root / ".git").exists():
                        try:
                            gl = subprocess.run(
                                ["git", "log", "-n", "5", "--oneline"],
                                cwd=str(root),
                                capture_output=True,
                                text=True,
                                timeout=8,
                                creationflags=_WIN_NO_WINDOW,
                            )
                            if gl.returncode == 0:
                                recent_commits = [ln.strip() for ln in gl.stdout.splitlines() if ln.strip()]
                        except Exception:
                            pass
                    return {
                        "ok": True,
                        "action": "inspect",
                        "source_root": str(root),
                        "python": sys.executable,
                        "python_version": platform.python_version(),
                        "has_git": (root / ".git").exists(),
                        "recent_commits": recent_commits,
                        "core_modules": core_mods[:45],
                        "ui_pages": ui_pages[:25],
                        "test_files": test_files[:30],
                        "installed_skills": [s["slug"] for s in self.list_skills()[:15]],
                        "recent_diagnostics": self.get_recent_diagnostics(limit=5),
                    }
                if act == "compile_check":
                    pr = subprocess.run(
                        [sys.executable, "-m", "compileall", "-q", "astracradle", "tests"],
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        timeout=60,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    return {"ok": pr.returncode == 0, "returncode": pr.returncode, "output": (pr.stdout + pr.stderr)[:4000]}
                if act == "run_tests":
                    pr = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pytest",
                            "-q",
                            "tests/test_v2_5_1_single_instance_and_tray_icon.py",
                            "-k",
                            "not (3d_hover or no_font or all_gifs or wifi_permission or task_progress or persistent_machine)",
                        ],
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        timeout=120,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    return {"ok": pr.returncode == 0, "returncode": pr.returncode, "output": (pr.stdout + pr.stderr)[-4000:]}
                if act == "self_check":
                    pr = subprocess.run(
                        [sys.executable, "-m", "astracradle", "--self-check"],
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        timeout=60,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    return {"ok": pr.returncode == 0, "returncode": pr.returncode, "output": (pr.stdout + pr.stderr)[:4000]}
                if act in ("git_status", "git_diff"):
                    git_cmd = ["git", "status", "-sb"] if act == "git_status" else ["git", "diff", "--stat"]
                    pr = subprocess.run(
                        git_cmd,
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        timeout=15,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    return {"ok": pr.returncode == 0, "output": (pr.stdout + pr.stderr)[:4000]}
                if act == "reinstall_local":
                    if perm in ("read_only", "safe_user"):
                        return {
                            "ok": False,
                            "blocked": True,
                            "error": "Reinstalling local build requires Level 3 (Up to Just Below Sudo) or Level 4.",
                        }
                    pr = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--user", "--no-build-isolation", "--no-deps", "-e", "."],
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        timeout=120,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    return {"ok": pr.returncode == 0, "returncode": pr.returncode, "output": (pr.stdout + pr.stderr)[-3000:]}
                return {"ok": False, "error": f"Unknown self-build action: {act}"}

            if name == "shell_exec":
                cmd = str(args.get("command", "")).strip()
                if not cmd:
                    return {"ok": False, "error": "Empty command"}
                if perm in ("read_only", "safe_user"):
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": "Shell execution requires Level 3 (Up to Just Below Sudo) or Level 4 (Sudo When Password Typed) in Hermes AI Permissions.",
                    }
                low = cmd.lower()
                elev_tokens = ("sudo ", "su ", "doas ", "pkexec ", "runas ")
                if any(low.startswith(tok) or f" {tok}" in low for tok in elev_tokens):
                    if perm == "sudo_with_prompt":
                        return self._run_sudo_prompted(cmd, f"Hermes AI requested elevated shell command: {cmd[:120]}", config)
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": "Level 3 (Up to Just Below Sudo) blocks sudo/root commands. Switch to Level 4 if you want to type your password when sudo is needed.",
                    }
                dangerous = ("rm -rf /", "mkfs", "format ", ":(){", "dd if=", "shutdown", "reboot", "del /s /q c:\\")
                if any(tok in low for tok in dangerous):
                    return {"ok": False, "blocked": True, "error": "Command matched destructive pattern guard."}
                # Ask interactive approval on mutating shell commands when require_approval is enabled
                read_only_cmds = ("ls ", "cat ", "head ", "tail ", "ps ", "df ", "free ", "uname", "uptime", "whoami", "ip ", "ss ", "nmcli ", "journalctl ", "systemctl status", "systemctl --user status", "grep ", "rg ", "find ", "which ", "pwd")
                if not any(low.startswith(rc) or low == rc.strip() for rc in read_only_cmds):
                    ok_ap, msg_ap = self._request_mutation_approval(
                        "Run Shell Command",
                        f"Hermes wants to execute:\n{cmd[:320]}",
                        config,
                    )
                    if not ok_ap:
                        return {"ok": False, "blocked": True, "error": msg_ap}
                    self._record_hermes_rollback("shell_exec", f"Shell: {cmd[:60]}", f"Executed shell command: {cmd[:180]}")
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    creationflags=_WIN_NO_WINDOW,
                )
                self.journal.log_event("HermesOS", "shell_exec", f"Ran: {cmd[:120]} (rc={proc.returncode})", "Hermes tool", "ok" if proc.returncode == 0 else "warning")
                return {
                    "ok": proc.returncode == 0,
                    "returncode": proc.returncode,
                    "stdout": (proc.stdout or "")[:3000],
                    "stderr": (proc.stderr or "")[:1000],
                }

            if name == "sudo_exec":
                return self._run_sudo_prompted(
                    str(args.get("command", "")),
                    str(args.get("reason", "Hermes AI requested an administrative command.")),
                    config,
                )

            if name == "memory_save":
                if perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                p = self.append_memory(str(args.get("fact", "")), target=str(args.get("target", "MEMORY.md")))
                return {"ok": True, "saved_to": str(p)}

            if name == "skill_save":
                if perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                p = self.save_skill(
                    str(args.get("slug", "custom")),
                    str(args.get("description", "")),
                    str(args.get("instructions", "")),
                )
                return {"ok": True, "skill_path": str(p)}

            if name == "delegate_subagent":
                if not allow_subagent:
                    return {"ok": False, "error": "Nested subagent recursion capped at depth 1."}
                sub_res = self.run_prompt(
                    str(args.get("task", "")),
                    config,
                    session_id=f"subagent_{int(time.time())}",
                    allow_subagent=False,
                    max_turns=4,
                )
                return {"ok": sub_res.ok, "summary": sub_res.reply[:2000], "tools": len(sub_res.tool_events)}

            if name == "cron_schedule":
                job = self.add_cron_job(
                    str(args.get("name", "Hermes Task")),
                    str(args.get("prompt", "")),
                    int(args.get("interval_minutes", 60) or 60),
                )
                return {"ok": True, "job": job}

            if name.startswith("custom_"):
                return self.execute_custom_tool(name, args, config, allow_subagent=allow_subagent)

            return {"ok": False, "error": f"Unknown tool: {name}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def get_active_persona(config: AppConfig) -> dict[str, str]:
        key = str(getattr(config, "hermes_persona", "astra_standard") or "astra_standard")
        return HERMES_PERSONAS.get(key) or HERMES_PERSONAS["astra_standard"]

    def _build_system_prompt(self, config: AppConfig) -> str:
        self.ensure_hermes_home(config)
        user_txt = self.user_md_path.read_text(encoding="utf-8", errors="ignore")[:700]
        mem = self.memory_path.read_text(encoding="utf-8", errors="ignore")[:900]
        skills = self.list_skills()
        skill_summary = ", ".join(s["slug"] for s in skills[:10]) or "none yet"
        custom_tools = self.list_custom_tools(include_disabled=False)
        ct_summary = ", ".join(f"{c['name']}(v{c.get('version', 1)})" for c in custom_tools[:8]) or "none yet"

        prof = self.get_pc_usage_profile()
        top_apps = sorted((prof.get("apps") or {}).items(), key=lambda kv: int(kv[1].get("hits", 0)), reverse=True)[:5]
        apps_txt = ", ".join(k for k, _ in top_apps) or "learning"
        games_txt = ", ".join(g.get("display_name", "") for g in self.list_learned_games()[:4]) or "none"
        perm_lbl = HERMES_PERMISSION_LEVELS.get(self._effective_permission_level(config), {}).get("label", "Safe")

        src_root = self.get_local_source_root()
        web_ok = bool(getattr(config, "hermes_allow_web_search", True))
        persona = self.get_active_persona(config)

        return (
            f"You are {persona['name']} ({persona['badge']}), AstraCradle's PC Action & Self-Development AI.\n"
            f"Traits: {persona['traits']}\n"
            f"Mannerisms: {persona['mannerisms']}\n"
            f"{persona['prompt_directive']}\n"
            f"Learned Whole-PC & Remote Usage Context: Source={src_root} | WebSearch={'ON' if web_ok else 'OFF'} | Safety={perm_lbl} | "
            f"TopApps={apps_txt} | Games={games_txt} | Skills={skill_summary} | CustomTools={ct_summary}\n"
            f"User Notes: {user_txt}\nMemory: {mem}\n"
            "Action & Self-Development Assistant Guidelines: Answer the user's exact request directly—if they ask for improvements, tests, or skills, propose concrete file-specific additions instead of saying no action is needed. "
            "You can create, combine, and improve your own reusable custom tools via `astra_custom_tool_forge` whenever a recurring multi-step workflow would make the user's life easier. "
            "Ground EVERY file path, module name, metric, and error in real tool output from this turn; NEVER invent fictional modules, packages, or bridges (such as non-existent bridges). "
            "Respect protected OS processes/files, verify code changes, and answer clearly without raw markdown clutter."
        )

    def _call_llm(
        self,
        spec: dict[str, Any],
        api_key: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        stream_cb: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Executes one LLM chat-completion turn via OpenAI-compatible, native Ollama (/api/chat), or Anthropic REST API."""
        style = spec.get("api_style", "openai")
        base_url = spec["base_url"].rstrip("/")

        if style == "anthropic":
            sys_txt = "\n\n".join(m["content"] for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str))
            non_sys = [m for m in messages if m.get("role") != "system"]
            payload = {
                "model": spec["model"],
                "max_tokens": 1536,
                "system": sys_txt,
                "messages": non_sys,
            }
            req = urllib.request.Request(
                f"{base_url}/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text_parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
            return "\n".join(text_parts).strip(), []

        # OpenAI-compatible endpoint (Ollama, OpenRouter, Gemini OpenAI bridge, Groq, Nous Portal, OpenAI, Custom)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif spec["provider"] == "ollama":
            headers["Authorization"] = "Bearer ollama"
        if spec["provider"] == "openrouter_free":
            headers["HTTP-Referer"] = "https://github.com/jackbashroot-boop/AstraCradle"
            headers["X-Title"] = "AstraCradle Hermes OS"

        payload: dict[str, Any] = {
            "model": spec["model"],
            "messages": messages,
            "tools": tools,
        }
        req_timeout = 240 if spec["provider"] == "ollama" else 90

        def _extract_reply_and_tools(msg: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
            raw_content = str(msg.get("content") or "").strip()
            # Cleanly strip <think>...</think> reasoning blocks if a local reasoning model (e.g. Qwen3.6) emits them
            cleaned = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
            content = (
                cleaned
                or raw_content
                or str(msg.get("thinking") or msg.get("reasoning") or "").strip()
            )
            raw_calls = msg.get("tool_calls") or []
            # Fallback: parse inline <tool_call>{...}</tool_call> blocks if a local model emitted tool calls in text
            if not raw_calls and content and "<tool_call>" in content:
                extracted_inline: list[dict[str, Any]] = []
                for m_tc in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, flags=re.DOTALL):
                    try:
                        obj = json.loads(m_tc.group(1))
                        if isinstance(obj, dict) and obj.get("name"):
                            extracted_inline.append({
                                "function": {"name": str(obj["name"]), "arguments": obj.get("arguments") or {}}
                            })
                    except Exception:
                        continue
                if extracted_inline:
                    raw_calls = extracted_inline
                    content = re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL).strip()
            norm_calls: list[dict[str, Any]] = []
            for i, tc in enumerate(raw_calls):
                if not isinstance(tc, dict):
                    continue
                fn = dict(tc.get("function") or {})
                norm_calls.append(
                    {
                        "id": str(tc.get("id") or f"call_{i}"),
                        "type": "function",
                        "function": fn,
                    }
                )
            return content, norm_calls

        def _post_chat_openai(body_dict: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
            # Convert Ollama-style "images": [...] into OpenAI multimodal content blocks if present
            oa_msgs = []
            for m in body_dict.get("messages") or []:
                if m.get("images"):
                    blocks: list[dict[str, Any]] = [{"type": "text", "text": str(m.get("content") or "")}]
                    for b64 in m["images"]:
                        blocks.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
                    nm = dict(m)
                    nm.pop("images", None)
                    nm["content"] = blocks
                    oa_msgs.append(nm)
                else:
                    oa_msgs.append(m)
            oa_body = dict(body_dict)
            oa_body["messages"] = oa_msgs
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(oa_body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            return _extract_reply_and_tools(choice.get("message") or {})

        if spec["provider"] != "ollama":
            return _post_chat_openai(payload)

        # Native Ollama /api/chat endpoint — unlike /v1/chat/completions, /api/chat ACTUALLY honors
        # options.num_gpu, options.num_ctx, options.num_batch, and keep_alive!
        ollama_root = base_url[:-3] if base_url.endswith("/v1") else base_url
        if ollama_root.endswith("/api"):
            ollama_chat_url = f"{ollama_root}/chat"
        else:
            ollama_chat_url = f"{ollama_root}/api/chat"

        def _to_native_ollama_messages(src_msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for m in src_msgs:
                role = str(m.get("role") or "user")
                item: dict[str, Any] = {"role": role, "content": str(m.get("content") or "")}
                if m.get("images"):
                    item["images"] = list(m["images"])
                if role == "tool" and m.get("name"):
                    item["tool_name"] = str(m.get("name"))
                if m.get("tool_calls"):
                    nat_tcs = []
                    for tc in m["tool_calls"]:
                        fn = dict((tc or {}).get("function") or {})
                        args_val = fn.get("arguments", {})
                        if isinstance(args_val, str):
                            try:
                                args_val = json.loads(args_val) if args_val.strip() else {}
                            except Exception:
                                args_val = {}
                        nat_tcs.append({"function": {"name": str(fn.get("name") or ""), "arguments": args_val}})
                    item["tool_calls"] = nat_tcs
                out.append(item)
            return out

        def _post_chat_ollama_native(body_dict: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
            req = urllib.request.Request(
                ollama_chat_url,
                data=json.dumps(body_dict).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            if body_dict.get("stream"):
                content_parts: list[str] = []
                think_parts: list[str] = []
                collected_calls: list[dict[str, Any]] = []
                with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                    for raw_line in resp:
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        ln = raw_line.decode("utf-8", errors="ignore").strip()
                        if not ln:
                            continue
                        try:
                            chunk = json.loads(ln)
                        except Exception:
                            continue
                        msg_obj = chunk.get("message") or {}
                        delta_c = str(msg_obj.get("content") or "")
                        delta_t = str(msg_obj.get("thinking") or "")
                        if delta_c:
                            content_parts.append(delta_c)
                            if stream_cb is not None:
                                try:
                                    stream_cb("".join(content_parts))
                                except Exception:
                                    pass
                        if delta_t:
                            think_parts.append(delta_t)
                        if msg_obj.get("tool_calls"):
                            collected_calls.extend(msg_obj.get("tool_calls") or [])
                return _extract_reply_and_tools({
                    "content": "".join(content_parts),
                    "thinking": "".join(think_parts),
                    "tool_calls": collected_calls,
                })
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if "message" in data:
                return _extract_reply_and_tools(data.get("message") or {})
            choice = (data.get("choices") or [{}])[0]
            return _extract_reply_and_tools(choice.get("message") or {})

        gpu_plan = spec.get("gpu_plan") or {}
        delay_s = float(gpu_plan.get("throttle_delay_sec", 0.0) or 0.0)
        if delay_s > 0.0:
            time.sleep(min(2.0, delay_s))

        # Stage 0: Requested GPU budget options
        opts_primary: dict[str, Any] = {
            "num_ctx": int(gpu_plan.get("num_ctx") or spec.get("context_tokens", 32768)),
        }
        if gpu_plan.get("num_batch"):
            opts_primary["num_batch"] = int(gpu_plan["num_batch"])
        if gpu_plan.get("num_gpu_layers") is not None:
            opts_primary["num_gpu"] = int(gpu_plan["num_gpu_layers"])

        native_msgs = _to_native_ollama_messages(messages)
        attempts: list[tuple[str, dict[str, Any], bool]] = [
            ("primary", opts_primary, True),
            # Stage 1: Drop partial num_gpu/num_batch splits (common ROCm/MoE segfault trigger on Qwen3.6) + lean 8K ctx
            ("gpu_safe", {"num_ctx": min(8192, int(opts_primary.get("num_ctx", 8192)))}, True),
            # Stage 2: Pure CPU/RAM safe fallback (num_gpu=0) if GPU/ROCm runner itself segfaults
            ("cpu_safe", {"num_gpu": 0, "num_ctx": 4096}, True),
            # Stage 3: Pure CPU/RAM fallback without tool schema payload if Jinja tool template crashed runner
            ("cpu_notools", {"num_gpu": 0, "num_ctx": 4096}, False),
        ]

        last_http_exc: urllib.error.HTTPError | None = None
        for idx, (_stage_name, stage_opts, include_tools) in enumerate(attempts):
            try_payload: dict[str, Any] = {
                "model": spec["model"],
                "messages": native_msgs,
                "stream": bool(stream_cb is not None),
                "options": stage_opts,
            }
            if include_tools and tools:
                try_payload["tools"] = tools
            if gpu_plan.get("keep_alive"):
                try_payload["keep_alive"] = str(gpu_plan["keep_alive"])
            try:
                content, t_calls = _post_chat_ollama_native(try_payload)
                if idx >= 2 and content and not t_calls:
                    content += (
                        "\n\n_(Auto-recovered via safe CPU/RAM fallback after Ollama GPU runner crashed. "
                        "Tip: if you have an AMD Ryzen iGPU + Radeon discrete GPU on Linux/CachyOS, setting "
                        "`ROCR_VISIBLE_DEVICES=0` in Ollama's systemd override fixes ROCm multi-GPU segfaults.)_"
                    )
                return content, t_calls
            except urllib.error.HTTPError as exc:
                last_http_exc = exc
                err_txt = ""
                try:
                    err_txt = exc.read().decode("utf-8", errors="ignore")
                except Exception:
                    err_txt = str(exc)
                low_err = err_txt.lower()
                is_runner_crash = exc.code >= 500 and any(
                    tok in low_err
                    for tok in (
                        "segmentation fault",
                        "llama-server",
                        "llama runner",
                        "unable to allocate",
                        "out of memory",
                        "terminated",
                        "exit status",
                    )
                )
                if not is_runner_crash or idx == len(attempts) - 1:
                    raise urllib.error.HTTPError(
                        exc.url,
                        exc.code,
                        f"{exc.reason} — {err_txt[:320]}",
                        exc.headers,
                        None,
                    )
                time.sleep(0.35)

        if last_http_exc is not None:
            raise last_http_exc
        return "", []

    def run_prompt(
        self,
        prompt: str,
        config: AppConfig,
        *,
        session_id: str = "default",
        allow_subagent: bool = True,
        max_turns: int = 12,
        attachments: list[str] | None = None,
        stream_cb: Callable[[str], None] | None = None,
        step_cb: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        llm_caller: Callable[..., tuple[str, list[dict[str, Any]]]] | None = None,
    ) -> HermesRunResult:
        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            return HermesRunResult(ok=False, session_id=session_id, reply="", error="Prompt cannot be empty.")

        self.ensure_hermes_home(config)
        spec = self.resolve_provider_spec(config)
        if llm_caller is None and spec["mode"] == "local_gpu":
            self.ensure_ollama_running()
        spec["gpu_plan"] = self.compute_gpu_throttle_plan(config)
        env_map = self.load_env()
        api_key = env_map.get(spec["env_var"]) or os.environ.get(spec["env_var"], "")

        if llm_caller is None and spec["requires_key"] and not api_key:
            return HermesRunResult(
                ok=False,
                session_id=session_id,
                reply="",
                model_used=spec["model"],
                provider_used=spec["provider"],
                error=f"Missing API key ({spec['env_var']}). Add your free/own key in Hermes AI Setup or switch to Local GPU mode.",
            )

        encoded_imgs, attachment_context, att_names = self.prepare_attachments(attachments)
        att_note = f" [Attached: {', '.join(att_names)}]" if att_names else ""
        self._append_session_message(session_id, "user", clean_prompt + att_note, title_hint=clean_prompt)

        history = self.get_session_messages(session_id, limit=24)
        # Compact older history if long so context fits comfortably
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._build_system_prompt(config)}]
        if len(history) > 14:
            older_summary = " | ".join(f"{m['role']}: {m['content'][:90]}" for m in history[:-10])
            messages.append({"role": "system", "content": f"Earlier session summary: {older_summary[:1400]}"})
            recent = history[-10:]
        else:
            recent = history
        for m in recent:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": m["content"]})

        tools = self._tool_schemas()
        caller = llm_caller or self._call_llm
        tool_events: list[dict[str, Any]] = []
        skill_created = ""
        final_reply = ""

        try:
            if step_cb is not None:
                try:
                    step_cb(f"Model: {spec['model']} ({spec.get('dynamic_tier', 'standard')})")
                except Exception:
                    pass
            for _turn in range(max_turns):
                if cancel_event is not None and cancel_event.is_set():
                    final_reply = "Stopped by user."
                    break
                if stream_cb is not None and llm_caller is None and caller == self._call_llm:
                    content, tool_calls = self._call_llm(
                        spec,
                        api_key,
                        messages,
                        tools,
                        stream_cb=stream_cb,
                        cancel_event=cancel_event,
                    )
                else:
                    content, tool_calls = caller(spec, api_key, messages, tools)
                if cancel_event is not None and cancel_event.is_set():
                    final_reply = (content.strip() + "\n\n(Stopped by user.)") if content else "Stopped by user."
                    break
                # Grounding safety net: if live LLM didn't emit tool_calls on an explicit AstraCradle inspect/diagnostic prompt,
                # run the real requested tool server-side and synthesize from genuine output rather than roleplay.
                if (
                    not tool_calls
                    and _turn == 0
                    and not tool_events
                    and llm_caller is None
                    and tools
                    and any(k in clean_prompt.lower() for k in ("astra_dev_self_build", "astra_system_status", "astra_deep_scan", "astra_journal_and_crashes"))
                ):
                    auto_calls: list[dict[str, Any]] = []
                    low_p = clean_prompt.lower()
                    if "astra_dev_self_build" in low_p:
                        auto_calls.append({"id": "auto_inspect", "function": {"name": "astra_dev_self_build", "arguments": {"action": "inspect"}}})
                    if "astra_journal_and_crashes" in low_p or "diagnostics" in low_p:
                        auto_calls.append({"id": "auto_diag", "function": {"name": "astra_journal_and_crashes", "arguments": {"limit": 10}}})
                    if "astra_system_status" in low_p or "astra_deep_scan" in low_p:
                        auto_calls.append({"id": "auto_scan", "function": {"name": "astra_deep_scan", "arguments": {}}})
                    if auto_calls:
                        tool_calls = auto_calls
                if not tool_calls:
                    final_reply = content or "(No text returned)"
                    break

                messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
                for tc in tool_calls:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    fn = tc.get("function") or {}
                    tname = str(fn.get("name") or "")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except Exception:
                        parsed_args = {}
                    if step_cb is not None:
                        try:
                            step_cb(f"⟳ Running {tname}...")
                        except Exception:
                            pass
                    result = self.execute_tool(tname, parsed_args, config, allow_subagent=allow_subagent)
                    if step_cb is not None:
                        try:
                            mark = "✓" if result.get("ok") else "!"
                            step_cb(f"{mark} {tname}")
                        except Exception:
                            pass
                    if tname == "skill_save" and result.get("ok"):
                        skill_created = str(result.get("skill_path", ""))
                    tool_events.append({"tool": tname, "args": parsed_args, "ok": bool(result.get("ok"))})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(tc.get("id") or f"call_{len(tool_events)}"),
                            "name": tname,
                            "content": json.dumps(result)[:3500],
                        }
                    )
            else:
                final_reply = "Completed tool workflow."

            # Hermes Closed Learning Loop: auto-crystallize reusable skill AND composite custom tool after 3+ successful tool calls
            if len(tool_events) >= 3 and not skill_created:
                slug = re.sub(r"[^a-z0-9]+", "-", clean_prompt.lower())[:32].strip("-") or "auto-workflow"
                steps_txt = "\n".join(f"{idx+1}. Call `{ev['tool']}` ({json.dumps(ev['args'])})" for idx, ev in enumerate(tool_events))
                sp = self.save_skill(
                    f"auto-{slug}",
                    f"Auto-learned Hermes workflow for: {clean_prompt[:90]}",
                    f"# Auto-Learned Procedure\n{steps_txt}\n",
                )
                skill_created = str(sp)
                ok_steps = [
                    {"tool": ev["tool"], "args": ev["args"], "continue_on_error": True}
                    for ev in tool_events
                    if ev.get("ok") and not str(ev.get("tool", "")).startswith(("custom_", "astra_custom_tool_forge", "sudo_exec", "shell_exec"))
                ]
                if len(ok_steps) >= 3 and self._effective_permission_level(config) != "read_only":
                    try:
                        self.create_composite_custom_tool(
                            name=f"custom_auto_{slug.replace('-', '_')}",
                            description=f"Auto-combined workflow for: {clean_prompt[:110]}",
                            steps=ok_steps[:6],
                            auto_learned=True,
                            config=config,
                        )
                    except Exception:
                        pass

            self._append_session_message(
                session_id,
                "assistant",
                final_reply,
                tool_trace=json.dumps(tool_events),
            )
            if bool(getattr(config, "hermes_voice_tts_enabled", False)) and final_reply and not final_reply.startswith("Stopped"):
                self.speak_text_offline(final_reply[:500])
            return HermesRunResult(
                ok=True,
                session_id=session_id,
                reply=final_reply,
                tool_events=tool_events,
                skill_created=skill_created,
                model_used=spec["model"],
                provider_used=spec["provider"],
            )
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                pass
            return HermesRunResult(
                ok=False,
                session_id=session_id,
                reply="",
                tool_events=tool_events,
                model_used=spec["model"],
                provider_used=spec["provider"],
                error=f"HTTP {exc.code}: {err_body or exc.reason}",
            )
        except Exception as exc:
            return HermesRunResult(
                ok=False,
                session_id=session_id,
                reply="",
                tool_events=tool_events,
                model_used=spec["model"],
                provider_used=spec["provider"],
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Messaging Gateways & Clean Uninstall
    # ------------------------------------------------------------------
    def start_gateway(self, config: AppConfig) -> tuple[bool, str]:
        if self._gateway_proc is not None and self._gateway_proc.poll() is None:
            return True, "Hermes messaging gateway is already running."
        hbin = shutil.which("hermes")
        if not hbin:
            return False, "Upstream `hermes` CLI not installed yet. Click Auto-Install & Set Up Hermes AI first."
        self.ensure_hermes_home(config)
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.hermes_home)
        env.update(self.load_env())
        try:
            self._gateway_proc = subprocess.Popen(
                [hbin, "gateway"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_WIN_NO_WINDOW,
            )
            return True, "Started Hermes messaging gateway (Telegram / Discord / Slack / WhatsApp / Email)."
        except Exception as exc:
            return False, f"Could not start Hermes gateway: {exc}"

    def stop_gateway(self) -> None:
        for proc_attr in ("_gateway_proc", "_ollama_proc"):
            proc = getattr(self, proc_attr, None)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            setattr(self, proc_attr, None)

    def cleanup_for_uninstall(self, delete_user_data: bool = True) -> list[str]:
        notes: list[str] = []
        self.stop_gateway()
        if delete_user_data and self.hermes_home.exists():
            shutil.rmtree(self.hermes_home, ignore_errors=True)
            notes.append(f"Removed Hermes OS AI home ({self.hermes_home}).")
        return notes
