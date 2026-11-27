"""Native Hermes OS AI Agent Engine, Hardware GPU Recommender, 1-Click Installer & AstraCradle Tool Bridge."""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
import importlib.util
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
        "4. Call `astra_process_lookup` to explain unfamiliar PIDs or processes in plain English.\n"
        "5. Consult `astra_sop_templates` before multi-step troubleshooting so outputs follow proven decision guardrails and templates.\n",
    ),
    (
        "sop-standard-operating-book",
        "Use and evolve the Standard Operating Book & Composable Template Library for fast, consistent PC diagnostics, fixes, and reviews.",
        "# Standard Operating Book (SOP Template Library)\n"
        "- For recurring tasks (health triage, idle AI RAM checks, startup audit, disk hygiene, crash/journal review, Wi-Fi/audio fixes, code reviews), pull the matching template via `astra_sop_templates(action='match')`.\n"
        "- Follow every `decision_rules` guardrail (e.g. never kill `ollama` / `llama-server` serving Hermes without checking PIDs via `astra_process_lookup`).\n"
        "- Plug real tool data into the template's `output_template` placeholders rather than inventing structures or metrics.\n"
        "- Combine templates with `compose` or turn them into reusable one-click tools with `instantiate_tool`.\n",
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
    {
        "item_id": "ref-lan-tv-astrashield-sinkhole",
        "title": "AstraShield DNS Sinkhole & Smart TV LAN Discovery",
        "category": "networking",
        "tags": "sinkhole astrashield samsung tv smart tv lan network dns 5353 53 youtube dial",
        "summary": "AstraCradle's built-in sinkhole program is AstraShield (`AstraShieldManager` + `LanTvAdShield`). Use `astra_lan_and_sinkhole` to scan LAN TVs (including Samsung Tizen & YouTube state) and aim TVs at AstraShield DNS.",
        "body": (
            "# AstraShield DNS Sinkhole & Smart TV Discovery\n"
            "- Built-in sinkhole program: **AstraShield DNS Sinkhole** (`AstraShieldManager`), listening on `0.0.0.0:5353` (or `0.0.0.0:53` when elevated/forwarded).\n"
            "- Built-in LAN scanner: `LanTvAdShield` discovers Smart TVs (Samsung Tizen, LG webOS, Roku, Google TV/Chromecast, Sony, Fire TV) via ARP + SSDP DIAL + Avahi/mDNS and probes active YouTube state (`:8001/api/v2/`, `:8008/apps/YouTube`, `:8080/ws/app/YouTube`).\n"
            "- To aim a Samsung or Smart TV at the sinkhole: call `astra_lan_and_sinkhole(action='aim_tv_at_sinkhole', brand_filter='samsung')`. Set Primary DNS = PC LAN IP, Secondary (Failsafe) DNS = Router/Gateway IP. Samsung TVs require UDP port 53 (so use elevated port 53 redirect `53 -> 5353` if listening on 5353).\n"
        ),
    },
)


SOP_TEMPLATE_LIBRARY: tuple[dict[str, Any], ...] = (
    {
        "template_id": "sop_pc_health_triage",
        "title": "PC Health Triage & Actionable Recommendations",
        "category": "diagnostics",
        "keywords": ["scan", "health", "slow", "triage", "diagnose", "score", "findings", "recommendations", "cleanup"],
        "decision_rules": [
            "Run `astra_deep_scan` first to obtain real findings; never estimate or invent MB/count numbers.",
            "Distinguish reversible safe actions (`astra_fix_safe`) from manual process/startup decisions.",
            "NEVER recommend killing `ollama` or `llama-server` as 'harmless' if it is actively serving Hermes AI.",
        ],
        "tool_pipeline": [
            {"tool": "astra_deep_scan", "args": {}},
            {"tool": "astra_system_status", "args": {}},
        ],
        "output_template": (
            "Health Summary (Score: {{score}}/100)\n"
            "Verified Findings:\n- {{finding_1}}\n- {{finding_2}}\n"
            "Safe Next Actions (Ranked):\n1. {{safe_action_1}}\n2. {{manual_review_2}}"
        ),
    },
    {
        "template_id": "sop_idle_ai_ram_safety",
        "title": "Idle AI Chatbot & RAM Process Verification",
        "category": "safety",
        "keywords": ["idle", "ai", "chatbot", "ollama", "llama", "5.8", "ram", "process", "astra_process_lookup", "kill"],
        "decision_rules": [
            "ALWAYS run `astra_process_lookup` (query='ollama' or process name) before advising process termination.",
            "Check whether the process is the active Ollama / llama-server powering Hermes OS AI; if so, warn that stopping it unloads the active model.",
            "If system RAM usage is low (<75%) and the user wants local AI active, recommend keeping it running.",
        ],
        "tool_pipeline": [
            {"tool": "astra_process_lookup", "args": {"query": "ollama"}},
            {"tool": "astra_system_status", "args": {}},
        ],
        "output_template": (
            "AI Process Verification:\n"
            "- Active Processes: {{pid_and_name}} ({{rss_mb}} MB)\n"
            "- Hermes Dependency Check: {{is_hermes_runtime}}\n"
            "- Recommendation: {{keep_or_stop_reason}}"
        ),
    },
    {
        "template_id": "sop_startup_audit_trim",
        "title": "Startup & Login Entry Audit",
        "category": "performance",
        "keywords": ["startup", "login", "boot", "autorun", "autostart", "entries", "#3"],
        "decision_rules": [
            "Run `astra_startup_audit` before suggesting disabling any startup item.",
            "Preserve essential audio (`pipewire`, `wireplumber`), GPU, network (`tailscale`, `nm-applet`), and desktop portal entries.",
        ],
        "tool_pipeline": [
            {"tool": "astra_startup_audit", "args": {}},
        ],
        "output_template": (
            "Startup & Login Audit ({{total_entries}} entries):\n"
            "- Essential (Keep): {{essential_list}}\n"
            "- Optional / Safe to Review: {{optional_list}}"
        ),
    },
    {
        "template_id": "sop_disk_quarantine_hygiene",
        "title": "Cache, Quarantine Vault & Storage Hygiene",
        "category": "maintenance",
        "keywords": ["cache", "temp", "quarantine", "disk", "storage", "space", "2 gb", "hygiene", "clean"],
        "decision_rules": [
            "Verify disk and vault usage via `astra_services_and_storage` + `astra_deep_scan` before cleaning.",
            "Run `astra_fix_safe` with `dry_run=true` first unless user explicitly requested immediate cleanup.",
        ],
        "tool_pipeline": [
            {"tool": "astra_services_and_storage", "args": {}},
            {"tool": "astra_fix_safe", "args": {"dry_run": True}},
        ],
        "output_template": (
            "Storage & Quarantine Status:\n"
            "- Mounts: {{disk_summary}}\n"
            "- Reclaimable Safe Cache: {{reclaimable_mb}} MB (Dry-Run verified)\n"
            "- Recommendation: {{storage_action}}"
        ),
    },
    {
        "template_id": "sop_journal_crash_postmortem",
        "title": "Journal Errors, Crash Post-Mortems & UI Log Triage",
        "category": "diagnostics",
        "keywords": ["journal", "crash", "error", "exception", "post_mortem", "discontinuity", "ui log", "audit", "#4"],
        "decision_rules": [
            "Always call `astra_journal_and_crashes` to inspect audit findings, post-mortems, and `recent_ui_events`.",
            "Separate old historical warnings from active repeating failures.",
        ],
        "tool_pipeline": [
            {"tool": "astra_journal_and_crashes", "args": {"limit": 20}},
        ],
        "output_template": (
            "Journal & Crash Triage:\n"
            "- Audit Status: {{audit_summary}}\n"
            "- Recent Errors / Crashes: {{error_patterns}}\n"
            "- UI Events Noted: {{ui_events_note}}\n"
            "- Next Fix: {{remediation}}"
        ),
    },
    {
        "template_id": "sop_gpu_thermal_game_tuning",
        "title": "GPU Thermals, Fan Curve & Game-Mode Tuning",
        "category": "performance",
        "keywords": ["gpu", "vram", "thermal", "temp", "fan", "curve", "game", "rocm", "fps"],
        "decision_rules": [
            "Inspect `astra_thermals_and_fans` before proposing or applying any fan curve.",
            "Respect ~1 GB VRAM game-mode headroom so games never stutter.",
        ],
        "tool_pipeline": [
            {"tool": "astra_thermals_and_fans", "args": {"action": "inspect"}},
            {"tool": "astra_system_status", "args": {}},
        ],
        "output_template": (
            "GPU & Thermal Baseline:\n"
            "- GPU Temp / Load / VRAM: {{gpu_metrics}}\n"
            "- Active Fan Profile: {{applied_profile}}\n"
            "- Tuning Advice: {{gpu_recommendation}}"
        ),
    },
    {
        "template_id": "sop_wifi_latency_recovery",
        "title": "Wi-Fi Signal, Channel & DNS Latency Recovery",
        "category": "network",
        "keywords": ["wifi", "wi-fi", "network", "dns", "ping", "latency", "packet", "drop", "internet"],
        "decision_rules": [
            "Always inspect live radio/DNS telemetry via `astra_wifi_check` before calling `astra_wifi_optimize`.",
        ],
        "tool_pipeline": [
            {"tool": "astra_wifi_check", "args": {}},
        ],
        "output_template": (
            "Network & Wi-Fi Check:\n"
            "- Interface / Signal / Channel: {{wifi_signal}}\n"
            "- DNS & Latency: {{latency_ms}}\n"
            "- Action: {{wifi_next_step}}"
        ),
    },
    {
        "template_id": "sop_audio_usb_stutter_fix",
        "title": "PipeWire Audio & USB Autosuspend Stutter Fix",
        "category": "performance",
        "keywords": ["audio", "pipewire", "sound", "mic", "usb", "stutter", "mouse", "headset", "crackling"],
        "decision_rules": [
            "Inspect `astra_av_and_peripherals` first to list devices stuck on `power/control=auto`.",
        ],
        "tool_pipeline": [
            {"tool": "astra_av_and_peripherals", "args": {"action": "inspect"}},
        ],
        "output_template": (
            "Audio & USB Peripherals:\n"
            "- Audio Stack: {{audio_summary}}\n"
            "- USB Autosuspend Devices: {{usb_auto_list}}\n"
            "- Fix: {{av_action}}"
        ),
    },
    {
        "template_id": "sop_remote_astrabeam_pair",
        "title": "Remote Continuity, Tailscale & AstraBeam Setup",
        "category": "remote",
        "keywords": ["remote", "astrabeam", "tailscale", "phone", "iphone", "android", "watch", "sunshine", "pair"],
        "decision_rules": [
            "Verify active remote services and paired devices via `astra_remote_tasks` + `astra_services_and_storage`.",
        ],
        "tool_pipeline": [
            {"tool": "astra_remote_tasks", "args": {}},
            {"tool": "astra_services_and_storage", "args": {}},
        ],
        "output_template": (
            "Remote Continuity Status:\n"
            "- Tailscale / AstraBeam: {{remote_services}}\n"
            "- Paired Devices & Tasks: {{devices_and_tasks}}"
        ),
    },
    {
        "template_id": "sop_systemd_failed_units",
        "title": "Systemd Failed Units & Port Audit",
        "category": "diagnostics",
        "keywords": ["systemd", "service", "unit", "failed", "port", "listening", "daemon"],
        "decision_rules": [
            "Inspect failed units and open ports via `astra_services_and_storage` before restarting any service.",
        ],
        "tool_pipeline": [
            {"tool": "astra_services_and_storage", "args": {}},
        ],
        "output_template": (
            "Services & Ports Audit:\n"
            "- Failed Systemd Units: {{failed_units}}\n"
            "- Listening Ports: {{ports_summary}}"
        ),
    },
    {
        "template_id": "sop_code_change_review",
        "title": "AstraCradle Codebase & Git Change Review",
        "category": "development",
        "keywords": ["codebase", "astracradle", "git", "commit", "changes", "review", "diff", "refactor", "test"],
        "decision_rules": [
            "ALWAYS run `astra_dev_self_build(action='inspect')` and `astra_git_sync(action='status')` before commenting on AstraCradle modules or recent changes.",
            "Cite ONLY real file paths and symbols verified via tool output.",
        ],
        "tool_pipeline": [
            {"tool": "astra_dev_self_build", "args": {"action": "inspect"}},
            {"tool": "astra_git_sync", "args": {"action": "status"}},
        ],
        "output_template": (
            "AstraCradle Code & Git Review:\n"
            "- Repo / Branch Status: {{git_status}}\n"
            "- Inspected Modules: {{real_modules}}\n"
            "- Concrete Observations & Improvements: {{proposals}}"
        ),
    },
    {
        "template_id": "sop_self_heal_repair_cycle",
        "title": "14-Invariant Self-Heal & Integrity Recovery",
        "category": "safety",
        "keywords": ["self-heal", "invariant", "corrupt", "repair", "integrity", "backup", "restore"],
        "decision_rules": [
            "Run `astra_self_heal_repair` to verify all 14 AstraCradle workspace/config/TLS/journal invariants cleanly.",
        ],
        "tool_pipeline": [
            {"tool": "astra_self_heal_repair", "args": {}},
        ],
        "output_template": (
            "Self-Heal Verification:\n"
            "- Invariants Passed: {{passed_count}}/{{total_count}}\n"
            "- Warnings / Repairs: {{repair_notes}}"
        ),
    },
    {
        "template_id": "sop_custom_tool_synthesis",
        "title": "Forge, Combine & Improve Custom Tools",
        "category": "custom_tools",
        "keywords": ["custom tool", "forge", "combine", "workflow", "macro", "automate", "template", "playbook"],
        "decision_rules": [
            "List existing tools with `astra_custom_tool_forge(action='list')` before creating duplicates.",
            "Prefer `create_composite` or `combine` for safe multi-tool workflows; archive versions via `improve`.",
        ],
        "tool_pipeline": [
            {"tool": "astra_custom_tool_forge", "args": {"action": "list"}},
        ],
        "output_template": (
            "Custom Tool Forge Action:\n"
            "- Tool Name & Version: {{tool_name}} (v{{version}})\n"
            "- Combined Steps: {{steps_list}}\n"
            "- Verification Run: {{run_status}}"
        ),
    },
    {
        "template_id": "sop_software_update_guard",
        "title": "OS, Flatpak & Firmware Update Audit",
        "category": "maintenance",
        "keywords": ["update", "upgrade", "pacman", "apt", "dnf", "flatpak", "firmware", "fwupdmgr"],
        "decision_rules": [
            "Always run `astra_system_updates(action='check')` first; never run full system upgrade without explicit user request.",
        ],
        "tool_pipeline": [
            {"tool": "astra_system_updates", "args": {"action": "check"}},
        ],
        "output_template": (
            "Pending Updates Check:\n"
            "- OS / Flatpak / Firmware: {{pending_updates}}\n"
            "- Recommended Timing: {{update_advice}}"
        ),
    },
    {
        "template_id": "sop_security_integrity_audit",
        "title": "Security, BadUSB & Quarantine Audit",
        "category": "safety",
        "keywords": ["security", "badusb", "integrity", "malware", "threat", "firewall", "hardening"],
        "decision_rules": [
            "Combine `astra_journal_and_crashes`, `astra_services_and_storage`, and `astra_startup_audit` for evidence-backed security review.",
        ],
        "tool_pipeline": [
            {"tool": "astra_journal_and_crashes", "args": {"limit": 15}},
            {"tool": "astra_services_and_storage", "args": {}},
        ],
        "output_template": (
            "Security & Integrity Summary:\n"
            "- Audit Findings: {{audit_findings}}\n"
            "- Open Ports / Services: {{ports_and_services}}\n"
            "- Hardening Next Steps: {{hardening_steps}}"
        ),
    },
    {
        "template_id": "sop_memory_pressure_relief",
        "title": "High RAM / Swap Pressure & ProBalance Relief",
        "category": "performance",
        "keywords": ["ram", "memory", "swap", "oom", "probalance", "lag", "freeze", "stutter"],
        "decision_rules": [
            "Check live memory % and top RAM consumers via `astra_system_status` + `astra_process_lookup` before adjusting ProBalance.",
        ],
        "tool_pipeline": [
            {"tool": "astra_system_status", "args": {}},
            {"tool": "astra_probalance_and_profile", "args": {"action": "status"}},
        ],
        "output_template": (
            "Memory & ProBalance Status:\n"
            "- RAM / Swap Usage: {{ram_pct}}%\n"
            "- Top Memory Consumers: {{top_procs}}\n"
            "- Relief Action: {{relief_step}}"
        ),
    },
    {
        "template_id": "sop_backup_rollback_safety",
        "title": "Pre-Change Snapshot & Rollback Verification",
        "category": "safety",
        "keywords": ["rollback", "undo", "revert", "restore", "snapshot", "backup"],
        "decision_rules": [
            "Verify available rollback entries via `astra_rollback_undo` before reverting.",
        ],
        "tool_pipeline": [
            {"tool": "astra_rollback_undo", "args": {"action": "list"}},
        ],
        "output_template": (
            "Rollback Ledger Status:\n"
            "- Available Snapshots: {{rollback_list}}\n"
            "- Action Taken: {{rollback_outcome}}"
        ),
    },
    {
        "template_id": "sop_ui_event_log_eval",
        "title": "UI Toast & Event Log Evaluation",
        "category": "diagnostics",
        "keywords": ["ui_events", "toast", "popup", "ui log", "interface", "freeze", "glitch"],
        "decision_rules": [
            "Read `recent_ui_events` from `astra_journal_and_crashes` to inspect silent UI actions and error popups.",
        ],
        "tool_pipeline": [
            {"tool": "astra_journal_and_crashes", "args": {"limit": 15}},
        ],
        "output_template": (
            "UI Event Log Evaluation:\n"
            "- Log Path: {{ui_events_log_path}}\n"
            "- Notable Warnings / Errors: {{ui_errors}}\n"
            "- Routine Events: {{ui_routine_count}}"
        ),
    },
    {
        "template_id": "sop_ollama_rocm_recovery",
        "title": "Ollama ROCm / GPU Runner & Fast-Model Recovery",
        "category": "performance",
        "keywords": ["rocm", "llama-server", "500", "segfault", "ollama", "igpu", "model", "slow response"],
        "decision_rules": [
            "Search the Knowledge Library for `ROCR_VISIBLE_DEVICES` and inspect `get_recent_diagnostics` via `astra_journal_and_crashes`.",
        ],
        "tool_pipeline": [
            {"tool": "astra_knowledge_library", "args": {"action": "search", "query": "ROCR_VISIBLE_DEVICES ollama"}},
            {"tool": "astra_journal_and_crashes", "args": {"limit": 10}},
        ],
        "output_template": (
            "Ollama / GPU Runner Status:\n"
            "- Diagnostics: {{runner_diags}}\n"
            "- Recommended Fix: {{rocm_fix}}"
        ),
    },
    {
        "template_id": "sop_web_research_library",
        "title": "Verified Web Research & Knowledge Library Caching",
        "category": "custom_tools",
        "keywords": ["research", "web", "docs", "latest", "online", "verify", "library", "source"],
        "decision_rules": [
            "Search local FTS5 Knowledge Library first; if stale or missing, use `web_search` + `web_fetch` and cache verified findings cleanly.",
        ],
        "tool_pipeline": [
            {"tool": "astra_knowledge_library", "args": {"action": "search", "query": "{{query}}"}},
        ],
        "output_template": (
            "Verified Research Summary:\n"
            "- Sources Checked: {{sources}}\n"
            "- Key Verified Facts: {{facts}}"
        ),
    },
    {
        "template_id": "sop_git_rebase_conflict_recovery",
        "title": "Git Detached HEAD, Rebase & Unmerged Conflict Recovery",
        "category": "development",
        "keywords": ["detached head", "rebase", "unmerged", "conflict", "aa ", "uu ", "git status", "push rejected"],
        "decision_rules": [
            "NEVER tell the user to run `git add .` + `git commit` while `rebase_in_progress=True` or `unmerged_files` is non-empty — that creates an orphaned detached commit or commits conflict markers!",
            "Use `git rebase --abort` to return cleanly to `main`, OR resolve conflicts with `git checkout --theirs .`, verify `python3 -m compileall -q astracradle`, then `git add -A && git rebase --continue`.",
        ],
        "tool_pipeline": [
            {"tool": "astra_git_sync", "args": {"action": "status"}},
            {"tool": "astra_verify_work", "args": {"action": "audit_draft"}},
        ],
        "output_template": (
            "Git Recovery Status:\n"
            "- Branch / Rebase State: {{git_state}}\n"
            "- Unmerged Conflicts: {{unmerged_count}}\n"
            "- Safe Recovery Commands:\n{{safe_recovery_steps}}"
        ),
    },
    {
        "template_id": "sop_full_os_autofix",
        "title": "Full OS Auto-Fix (Standard OS Checks + Web Cross-Check + Stronger AI Escalation)",
        "category": "diagnostics",
        "keywords": ["full os autofix", "fix the pc fully", "full pc fix", "os common fixes", "escalate stronger ai", "autofix"],
        "decision_rules": [
            "Detect exact OS/distro, kernel, desktop, and custom hardware/scheduler features first.",
            "Run the standard OS checks & safe fixes (failed units, package/lock health, audio/network, stale temp/quarantine, idle AI RAM safety), then cross-check anomalies via web search and escalate to a stronger AI model if configured or copy an Arena escalation packet.",
        ],
        "tool_pipeline": [
            {"tool": "astra_full_os_autofix", "args": {"apply_safe_fixes": True, "web_crosscheck": True, "escalate_stronger_ai": True}},
        ],
        "output_template": (
            "Full OS Auto-Fix Report ({{os_label}}):\n"
            "- Standard Checks Run: {{checks_summary}}\n"
            "- Safe Fixes Applied: {{fixes_applied}}\n"
            "- Custom Features & Web Cross-Check: {{web_notes}}\n"
            "- Stronger AI Escalation: {{escalation_note}}"
        ),
    },
    {
        "template_id": "sop_lan_tv_sinkhole",
        "title": "LAN Smart TV Discovery & AstraShield DNS Sinkhole Routing",
        "category": "networking",
        "keywords": ["samsung", "tv", "smart tv", "youtube", "sinkhole", "astrashield", "lan", "scan my network", "aim", "dns"],
        "decision_rules": [
            "When the user asks to scan their LAN/network for devices or Smart TVs (or find which TV is running YouTube), call `astra_lan_and_sinkhole(action='scan_lan')` — NEVER rely only on `astra_remote_tasks` (which only lists paired AstraBeam/Tailscale devices).",
            "When the user mentions 'the sinkhole program', 'AstraShield', or 'aim the TV at the sinkhole', ALWAYS recognize AstraCradle's built-in AstraShield DNS Sinkhole and run `astra_lan_and_sinkhole(action='aim_tv_at_sinkhole')` or `sinkhole_status`.",
            "Remind the user clearly if AstraShield is listening on port 5353 vs port 53, because Samsung/Smart TVs require UDP port 53 (or NAT redirect 53 -> 5353) plus Router IP as Failsafe Secondary DNS.",
        ],
        "tool_pipeline": [
            {"tool": "astra_lan_and_sinkhole", "args": {"action": "scan_lan"}},
        ],
        "output_template": (
            "LAN & AstraShield Sinkhole Summary:\n"
            "- Discovered Smart TVs & Devices: {{devices}}\n"
            "- AstraShield DNS Sinkhole: {{sinkhole_status}}\n"
            "- TV DNS Configuration Steps: {{tv_steps}}"
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
        dep_lib=None,
        crash_analyzer=None,
        approval_prompt_cb=None,
        thermal_spc=None,
        gpu_pwr=None,
        catalog_scanner=None,
        adblock_shield=None,
        lan_tv_shield=None,
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
        self.dep_lib = dep_lib
        self.crash_analyzer = crash_analyzer
        self.approval_prompt_cb = approval_prompt_cb
        self.thermal_spc = thermal_spc
        self.gpu_pwr = gpu_pwr
        self.catalog_scanner = catalog_scanner
        self.adblock_shield = adblock_shield
        self.lan_tv_shield = lan_tv_shield
        self._fast_pull_started = False
        self._gpu_fallback_models: set[str] = set()
        self._last_autonomous_cycle_ts: float = 0.0
        self._autonomous_lock = threading.Lock()
        self._active_game_profile_key: str = ""

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
        self.subagents_path = self.hermes_home / "subagents.json"
        self.learned_games_path = self.hermes_home / "learned_games.json"
        self.pc_usage_path = self.hermes_home / "pc_usage_profile.json"
        self.diagnostics_log_path = self.hermes_home / "diagnostics.jsonl"
        self.autonomous_status_path = self.hermes_home / "autonomous_guardian.json"
        self.runtime_health_path = self.hermes_home / "runtime_model_health.json"
        self.startup_baseline_path = self.hermes_home / "startup_baseline.json"
        self.captures_dir = self.hermes_home / "captures"
        self.library_dir = self.hermes_home / "library"
        self.library_items_dir = self.library_dir / "items"
        self.library_db_path = self.library_dir / "library_index.db"
        self.library_topics_path = self.library_dir / "topics.json"
        self._last_library_prefetch_ts: float = 0.0
        self.custom_tools_dir = self.hermes_home / "custom_tools"
        self.custom_tools_registry_path = self.custom_tools_dir / "registry.json"
        self.custom_tools_scripts_dir = self.custom_tools_dir / "scripts"
        self.custom_tools_versions_dir = self.custom_tools_dir / "versions"
        self.sop_templates_path = self.hermes_home / "sop_templates.json"

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
        if not self.subagents_path.exists():
            self.subagents_path.write_text("[]\n", encoding="utf-8")
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
        for sop in SOP_TEMPLATE_LIBRARY:
            iid = f"sop-{sop['template_id']}"
            if iid not in existing:
                body_txt = (
                    f"# {sop['title']}\n"
                    f"Category: {sop['category']}\n\n"
                    "## Decision Rules\n" + "\n".join(f"- {r}" for r in sop["decision_rules"]) + "\n\n"
                    "## Tool Pipeline\n" + "\n".join(f"- `{s['tool']}` ({json.dumps(s.get('args') or {})})" for s in sop["tool_pipeline"]) + "\n\n"
                    f"## Output Template\n{sop['output_template']}\n"
                )
                self.store_library_item(
                    title=f"SOP: {sop['title']}",
                    body=body_txt,
                    category="sop_template",
                    source_type="builtin",
                    tags=" ".join(sop["keywords"]) + f" sop template {sop['category']}",
                    summary=f"SOP ({sop['category']}): " + "; ".join(sop["decision_rules"][:2]),
                    item_id=iid,
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

    _AUTHORITATIVE_DOC_DOMAINS: tuple[str, ...] = (
        "docs.python.org",
        "peps.python.org",
        "docs.pytest.org",
        "fastapi.tiangolo.com",
        "doc.rust-lang.org",
        "fishshell.com",
        "developer.mozilla.org",
        "typescriptlang.org",
        "wiki.archlinux.org",
        "docs.kernel.org",
        "ollama.com",
        "rocm.docs.amd.com",
        "git-scm.com",
        "realpython.com",
        "github.com",
        "pytorch.org",
    )

    @staticmethod
    def _extract_version_tags(text: str) -> str:
        found = re.findall(r"\b(?:python\s*3\.\d+|rust\s*1\.\d+|rocm\s*\d+(?:\.\d+)?|202[4-9])\b", (text or "").lower())
        norm = [re.sub(r"\s+", "", f) for f in found]
        seen: list[str] = []
        for t in norm:
            if t not in seen:
                seen.append(t)
        return " ".join(seen[:6])

    @staticmethod
    def _chunk_documentation_text(text: str, *, max_chunk_chars: int = 3600, max_chunks: int = 5) -> list[str]:
        """Splits fetched doc/code pages along paragraph/section boundaries without cutting mid-line or code block."""
        lines = [ln.rstrip() for ln in (text or "").splitlines()]
        chunks: list[str] = []
        buf: list[str] = []
        buf_len = 0
        for ln in lines:
            if buf_len + len(ln) + 1 > max_chunk_chars and buf:
                chunk = "\n".join(buf).strip()
                if chunk:
                    chunks.append(chunk)
                    if len(chunks) >= max_chunks:
                        break
                buf = [ln]
                buf_len = len(ln) + 1
            else:
                buf.append(ln)
                buf_len += len(ln) + 1
        if buf and len(chunks) < max_chunks:
            tail = "\n".join(buf).strip()
            if tail:
                chunks.append(tail)
        return chunks

    @staticmethod
    def _topic_slug(topic: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "_", (topic or "").strip().lower()).strip("_")
        return f"topic_{s[:40]}" if s else "topic_general"

    def _load_library_topics_raw(self) -> dict[str, dict[str, Any]]:
        self._init_library_db()
        if not self.library_topics_path.exists():
            defaults = {}
            now_iso = utc_now_iso()
            for cat, top in (
                ("programming", "Python 3.14 & pytest best practices"),
                ("linux", "Linux systemd & PipeWire troubleshooting"),
                ("ai_gpu", "Ollama ROCm AMD GPU optimization"),
            ):
                tid = self._topic_slug(top)
                defaults[tid] = {
                    "topic_id": tid,
                    "topic": top,
                    "category": cat,
                    "added_at": now_iso,
                    "last_harvested_at": "",
                    "pages_fetched": 0,
                }
            self.library_topics_path.write_text(json.dumps(defaults, indent=2) + "\n", encoding="utf-8")
            return defaults
        try:
            raw = json.loads(self.library_topics_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_library_topics_raw(self, data: dict[str, dict[str, Any]]) -> None:
        self._init_library_db()
        self.library_topics_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def list_library_topics(self) -> list[dict[str, Any]]:
        raw = self._load_library_topics_raw()
        self._init_library_db()
        counts: dict[str, tuple[int, int]] = {}
        with sqlite3.connect(self.library_db_path) as conn:
            for tid in raw.keys():
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(byte_size), 0) FROM library_items WHERE tags LIKE ?",
                    (f"%{tid}%",),
                ).fetchone()
                counts[tid] = (int(row[0] or 0), int(row[1] or 0))
        out: list[dict[str, Any]] = []
        for tid in sorted(raw.keys()):
            rec = dict(raw[tid] or {})
            cnt, bsz = counts.get(tid, (0, 0))
            rec["topic_id"] = tid
            rec["items_count"] = cnt
            rec["bytes_total"] = bsz
            rec["kb_total"] = round(bsz / 1024.0, 1)
            out.append(rec)
        return out

    def add_library_topic(
        self,
        topic: str,
        *,
        category: str = "programming",
        harvest_now: bool = False,
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        clean = " ".join((topic or "").split()).strip()
        if not clean:
            return {"ok": False, "error": "Topic cannot be empty."}
        tid = self._topic_slug(clean)
        with self._lock:
            data = self._load_library_topics_raw()
            prev = dict(data.get(tid) or {})
            rec = {
                "topic_id": tid,
                "topic": clean[:140],
                "category": (category or "programming").strip().lower()[:40],
                "added_at": prev.get("added_at") or utc_now_iso(),
                "last_harvested_at": prev.get("last_harvested_at") or "",
                "pages_fetched": int(prev.get("pages_fetched", 0) or 0),
            }
            data[tid] = rec
            self._save_library_topics_raw(data)
        harvest_res = None
        if harvest_now and config is not None:
            harvest_res = self.harvest_library_topic(tid, config)
        return {"ok": True, "topic": rec, **({"harvest": harvest_res} if harvest_res is not None else {})}

    def remove_library_topic(self, topic_or_id: str, *, remove_items: bool = True) -> dict[str, Any]:
        key_raw = (topic_or_id or "").strip()
        if not key_raw:
            return {"ok": False, "error": "Specify topic or topic_id to remove."}
        with self._lock:
            data = self._load_library_topics_raw()
            tid = key_raw if key_raw in data else self._topic_slug(key_raw)
            if tid not in data:
                # Try case-insensitive match by topic text
                match_id = next((k for k, v in data.items() if str(v.get("topic") or "").lower() == key_raw.lower()), "")
                tid = match_id
            if not tid or tid not in data:
                return {"ok": False, "error": f"Topic '{key_raw}' not found."}
            removed_rec = data.pop(tid)
            self._save_library_topics_raw(data)

        removed_items = 0
        if remove_items:
            self._init_library_db()
            with sqlite3.connect(self.library_db_path) as conn:
                rows = conn.execute(
                    "SELECT item_id, file_path FROM library_items WHERE pinned = 0 AND tags LIKE ?",
                    (f"%{tid}%",),
                ).fetchall()
                for iid, fpath in rows:
                    try:
                        Path(str(fpath)).unlink(missing_ok=True)
                    except Exception:
                        pass
                    conn.execute("DELETE FROM library_items WHERE item_id = ?", (iid,))
                    conn.execute("DELETE FROM library_fts WHERE item_id = ?", (iid,))
                    removed_items += 1
                conn.commit()
        return {"ok": True, "removed_topic": removed_rec.get("topic"), "topic_id": tid, "removed_items": removed_items}

    def list_library_items(self, *, query: str = "", category: str = "", limit: int = 80) -> list[dict[str, Any]]:
        self._init_library_db()
        ql = (query or "").strip().lower()
        cl = (category or "").strip().lower()
        with sqlite3.connect(self.library_db_path) as conn:
            rows = conn.execute(
                """
                SELECT item_id, title, category, source_type, source_url, tags, summary,
                       byte_size, hits, quality_score, pinned, incorrect, updated_at
                FROM library_items
                ORDER BY pinned DESC, hits DESC, updated_at DESC
                LIMIT ?
                """,
                (max(10, min(300, int(limit) * 3 if ql or cl else int(limit))),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            iid, title, cat, stype, surl, tags, summ, bsz, hits, qsc, pinned, inc, upd = r
            if cl and str(cat or "").lower() != cl:
                continue
            if ql and ql not in f"{title} {cat} {tags} {summ} {surl}".lower():
                continue
            out.append(
                {
                    "item_id": iid,
                    "title": title,
                    "category": cat,
                    "source_type": stype,
                    "source_url": surl,
                    "tags": tags,
                    "summary": summ,
                    "byte_size": int(bsz or 0),
                    "kb": round(int(bsz or 0) / 1024.0, 1),
                    "hits": int(hits or 0),
                    "quality_score": float(qsc or 0.0),
                    "pinned": bool(pinned),
                    "incorrect": bool(inc),
                    "updated_at": str(upd or "")[:19],
                }
            )
            if len(out) >= limit:
                break
        return out

    def delete_library_item(self, item_id: str) -> dict[str, Any]:
        self._init_library_db()
        iid = (item_id or "").strip()
        if not iid:
            return {"ok": False, "error": "Specify item_id to delete."}
        with sqlite3.connect(self.library_db_path) as conn:
            row = conn.execute("SELECT file_path, title FROM library_items WHERE item_id = ?", (iid,)).fetchone()
            if not row:
                return {"ok": False, "error": f"Library item '{iid}' not found."}
            fpath, title = row
            try:
                Path(str(fpath)).unlink(missing_ok=True)
            except Exception:
                pass
            conn.execute("DELETE FROM library_items WHERE item_id = ?", (iid,))
            conn.execute("DELETE FROM library_fts WHERE item_id = ?", (iid,))
            conn.commit()
        return {"ok": True, "deleted_id": iid, "title": title}

    def harvest_library_topic(
        self,
        topic_or_id: str,
        config: AppConfig,
        *,
        max_pages: int = 3,
        searcher: Callable[[str, int], list[dict[str, str]]] | None = None,
        fetcher: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        """Researches a topic online, prioritizes official docs, fetches full pages, chunks code/docs cleanly, and indexes into FTS5."""
        if searcher is None and not bool(getattr(config, "hermes_allow_web_search", True)):
            return {"ok": False, "reason": "Web search is disabled in Hermes settings."}

        data = self._load_library_topics_raw()
        tid = topic_or_id if topic_or_id in data else self._topic_slug(topic_or_id)
        if tid not in data:
            self.add_library_topic(topic_or_id, config=config)
            data = self._load_library_topics_raw()
            tid = self._topic_slug(topic_or_id)
        rec = dict(data[tid])
        query = str(rec.get("topic") or topic_or_id)
        category = str(rec.get("category") or "programming")

        search_fn = searcher or self._web_search_impl
        hits = search_fn(query, 6) or []
        if not hits:
            return {"ok": False, "topic": query, "stored_chunks": 0, "reason": "No web results returned."}

        # Rank official documentation domains first
        def _domain_rank(item: dict[str, str]) -> int:
            u = str(item.get("url") or "").lower()
            for idx_d, dom in enumerate(self._AUTHORITATIVE_DOC_DOMAINS):
                if dom in u:
                    return idx_d
            return 999

        ranked_hits = sorted(hits, key=_domain_rank)
        stored_chunks = 0
        pages_fetched = 0

        def _default_fetch(u: str) -> str:
            req = urllib.request.Request(
                u,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AstraCradle-Hermes/2.5.1"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read(240_000).decode("utf-8", errors="ignore")
            return self._strip_html_to_text(raw) if "<html" in raw.lower() or "<body" in raw.lower() else raw

        fetch_fn = fetcher or _default_fetch
        for h in ranked_hits[:max(1, min(5, int(max_pages)))]:
            url = str(h.get("url") or "").strip()
            title = str(h.get("title") or query).strip()
            snip = str(h.get("snippet") or "").strip()
            page_txt = ""
            if url.startswith(("http://", "https://")):
                try:
                    page_txt = (fetch_fn(url) or "").strip()
                except Exception:
                    page_txt = ""
            if len(page_txt) < 120:
                page_txt = f"{title}\n\n{snip}\nSource: {url}".strip()
            else:
                pages_fetched += 1

            ver_tags = self._extract_version_tags(f"{query} {title} {page_txt[:2000]}")
            chunks = self._chunk_documentation_text(page_txt, max_chunk_chars=3600, max_chunks=4)
            is_auth = _domain_rank(h) < 999
            qscore = 0.96 if is_auth else 0.85
            for c_idx, ch in enumerate(chunks):
                suffix = f" (Part {c_idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
                cid = f"{tid}-{abs(hash((url or title, c_idx))) % 10**10}"
                res = self.store_library_item(
                    title=f"{title[:130]}{suffix}",
                    body=ch,
                    category=category,
                    source_type="web",
                    source_url=url,
                    tags=f"{tid} {query} {ver_tags}".strip(),
                    summary=(snip or ch.splitlines()[0])[:280],
                    item_id=cid,
                    quality_score=qscore,
                    config=config,
                )
                if res.get("ok"):
                    stored_chunks += 1

        with self._lock:
            d2 = self._load_library_topics_raw()
            if tid in d2:
                d2[tid]["last_harvested_at"] = utc_now_iso()
                d2[tid]["pages_fetched"] = int(d2[tid].get("pages_fetched", 0) or 0) + pages_fetched
                self._save_library_topics_raw(d2)

        return {
            "ok": stored_chunks > 0,
            "topic_id": tid,
            "topic": query,
            "pages_fetched": pages_fetched,
            "stored_chunks": stored_chunks,
        }

    def prefetch_library_topics(self, config: AppConfig, topics: list[str] | None = None) -> dict[str, Any]:
        """Harvests all subscribed topics (or explicit topic list) with full-page doc chunking into the Knowledge Library."""
        if not bool(getattr(config, "hermes_allow_web_search", True)):
            return {"ok": False, "reason": "Web search disabled"}
        self._init_library_db()
        if topics:
            targets = [t for t in topics if str(t).strip()]
        else:
            targets = [t["topic_id"] for t in self.list_library_topics()]
        stored_total = 0
        pages_total = 0
        for tgt in targets[:8]:
            try:
                hr = self.harvest_library_topic(tgt, config, max_pages=2)
                stored_total += int(hr.get("stored_chunks", 0) or 0)
                pages_total += int(hr.get("pages_fetched", 0) or 0)
            except Exception:
                continue
        return {"ok": True, "stored": stored_total, "pages_fetched": pages_total, "topics_processed": len(targets[:8])}

    def get_library_stats(self, config: AppConfig | None = None) -> dict[str, Any]:
        self._init_library_db()
        max_bytes = self.get_library_max_bytes(config)
        max_gb = int(round(max_bytes / (1024**3)))
        cats: dict[str, dict[str, int]] = {}
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
            for crow in conn.execute(
                "SELECT category, COUNT(*), COALESCE(SUM(byte_size), 0) FROM library_items GROUP BY category"
            ).fetchall():
                cats[str(crow[0] or "general")] = {"count": int(crow[1] or 0), "bytes": int(crow[2] or 0)}
        total_items, builtin_items, web_items, total_bytes = (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
        rem_bytes = max(0, max_bytes - total_bytes)
        skills_cnt = len(self.list_skills())
        topics_cnt = len(self._load_library_topics_raw())
        return {
            "ok": True,
            "total_items": total_items,
            "builtin_items": builtin_items,
            "web_items": web_items,
            "skills_count": skills_cnt,
            "topics_count": topics_cnt,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "remaining_bytes": rem_bytes,
            "remaining_mb": round(rem_bytes / (1024 * 1024), 2),
            "remaining_gb": round(rem_bytes / (1024**3), 3),
            "max_bytes": max_bytes,
            "max_gb": max_gb,
            "usage_pct": round((total_bytes / max(1, max_bytes)) * 100.0, 4),
            "categories": cats,
            "library_dir": str(self.library_dir),
        }

    def build_internal_memory_manifest(self) -> dict[str, Any]:
        """Builds a compact map of all topics, categories, skills, SOPs, and custom tools Hermes already holds internally."""
        topics = [str(t.get("topic") or "") for t in self.list_library_topics() if t.get("topic")]
        skills = [s["slug"] for s in self.list_skills()]
        sops = [s["template_id"] for s in self.list_sop_templates()]
        custom_tools = [c["name"] for c in self.list_custom_tools(include_disabled=False)]
        self._init_library_db()
        cats: list[str] = []
        item_titles: list[str] = []
        try:
            with sqlite3.connect(self.library_db_path) as conn:
                cats = [str(r[0]) for r in conn.execute("SELECT DISTINCT category FROM library_items").fetchall() if r[0]]
                item_titles = [
                    str(r[0])
                    for r in conn.execute("SELECT title FROM library_items ORDER BY pinned DESC, hits DESC, updated_at DESC LIMIT 20").fetchall()
                    if r[0]
                ]
        except Exception:
            pass
        return {
            "topics": topics,
            "categories": cats,
            "sample_titles": item_titles,
            "skills_count": len(skills),
            "sops_count": len(sops),
            "custom_tools": custom_tools,
        }

    def deep_internal_recall(self, query: str, *, limit: int = 4) -> dict[str, Any]:
        """Searches ALL internal files (Knowledge Library FTS5, MEMORY.md, USER.md, Skills, Sessions, SOPs) BEFORE web search."""
        q = (query or "").strip()
        if not q:
            return {"ok": True, "has_strong_match": False, "library_hits": [], "memory_hits": [], "sop_hits": []}
        lib_hits = self.search_library(q, limit=max(1, limit))
        mem_hits = self.search_memory_fts(q, limit=max(1, limit))
        sop_hits = self.match_sop_templates(q, limit=2)
        q_tokens = [w for w in re.findall(r"[a-z0-9_]+", q.lower()) if len(w) >= 3]
        needed_overlap = min(2, max(1, len(q_tokens)))
        strong_lib = False
        for h in lib_hits:
            blob = f"{h.get('title', '')} {h.get('summary', '')} {h.get('tags', '')}".lower()
            overlap = sum(1 for tok in q_tokens if tok in blob)
            if overlap >= needed_overlap and (float(h.get("quality_score", 0.0) or 0.0) >= 0.8 or len(lib_hits) >= 2):
                strong_lib = True
                break
        strong_mem = False
        if len(mem_hits) >= 2:
            combined_mem = " ".join(f"{m.get('ref', '')} {m.get('snippet', '')}" for m in mem_hits).lower()
            if sum(1 for tok in q_tokens if tok in combined_mem) >= needed_overlap:
                strong_mem = True
        strong = strong_lib or strong_mem
        return {
            "ok": True,
            "query": q,
            "has_strong_match": strong,
            "library_hits": lib_hits,
            "memory_hits": mem_hits,
            "sop_hits": [{"template_id": s["template_id"], "title": s["title"]} for s in sop_hits],
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

    # ------------------------------------------------------------------
    # Standard Operating Book & Composable Template Library
    # ------------------------------------------------------------------
    def _load_learned_sop_templates(self) -> dict[str, dict[str, Any]]:
        if not self.sop_templates_path.exists():
            return {}
        try:
            raw = json.loads(self.sop_templates_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_learned_sop_templates(self, data: dict[str, dict[str, Any]]) -> None:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        self.sop_templates_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def list_sop_templates(self, category: str = "") -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for sop in SOP_TEMPLATE_LIBRARY:
            rec = dict(sop)
            rec["source"] = "builtin"
            by_id[rec["template_id"]] = rec
        for tid, rec in self._load_learned_sop_templates().items():
            if isinstance(rec, dict):
                merged = dict(rec)
                merged["template_id"] = tid
                merged.setdefault("source", "learned")
                by_id[tid] = merged
        items = list(by_id.values())
        if category.strip():
            cat_l = category.strip().lower()
            items = [i for i in items if str(i.get("category") or "").lower() == cat_l]
        return items

    def get_sop_template(self, template_id: str) -> dict[str, Any] | None:
        tid = (template_id or "").strip().lower()
        for item in self.list_sop_templates():
            if str(item.get("template_id") or "").lower() == tid:
                return item
        return None

    def match_sop_templates(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        """Instant keyword + FTS5 scoring across built-in and learned Standard Operating Book templates."""
        low = (query or "").strip().lower()
        if not low:
            return self.list_sop_templates()[:max(1, limit)]
        tokens = set(re.findall(r"[a-z0-9_#+-]+", low))
        scored: list[tuple[float, dict[str, Any]]] = []
        for sop in self.list_sop_templates():
            score = 0.0
            kws = [str(k).lower() for k in (sop.get("keywords") or [])]
            for kw in kws:
                if kw in low:
                    score += 3.0 if " " in kw or "_" in kw or kw.startswith("#") else 2.0
            title_words = set(re.findall(r"[a-z0-9_]+", str(sop.get("title") or "").lower()))
            score += 1.0 * len(tokens & title_words)
            if sop.get("source") == "learned":
                score += 0.3 * min(5, int(sop.get("successes", 0) or 0))
            if score > 0:
                scored.append((score, sop))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        return [s for _sc, s in scored[:max(1, limit)]]

    def compose_sop_templates(self, template_ids: list[str], title: str = "") -> dict[str, Any]:
        """Combines 2+ SOP templates into a single unified operating playbook + deduplicated tool pipeline."""
        chosen: list[dict[str, Any]] = []
        for raw_id in (template_ids or []):
            t = self.get_sop_template(str(raw_id))
            if t is not None:
                chosen.append(t)
        if not chosen:
            return {"ok": False, "error": "No matching SOP template IDs found."}

        seen_rules: set[str] = set()
        rules: list[str] = []
        seen_steps: set[str] = set()
        pipeline: list[dict[str, Any]] = []
        template_blocks: list[str] = []
        for c in chosen:
            for r in (c.get("decision_rules") or []):
                if r not in seen_rules:
                    seen_rules.add(r)
                    rules.append(r)
            for st in (c.get("tool_pipeline") or []):
                sig = f"{st.get('tool')}::{json.dumps(st.get('args') or {}, sort_keys=True)}"
                if sig not in seen_steps:
                    seen_steps.add(sig)
                    pipeline.append(dict(st))
            template_blocks.append(f"[{c['title']}]\n{c.get('output_template', '')}")

        comp_id = "sop_composed_" + "_".join(c["template_id"].replace("sop_", "")[:10] for c in chosen[:3])
        return {
            "ok": True,
            "composed": {
                "template_id": comp_id[:52],
                "title": title.strip() or (" + ".join(c["title"] for c in chosen[:3])),
                "source_templates": [c["template_id"] for c in chosen],
                "decision_rules": rules,
                "tool_pipeline": pipeline,
                "output_template": "\n\n".join(template_blocks),
            },
        }

    def instantiate_sop_as_custom_tool(
        self,
        template_ids: list[str],
        tool_name: str = "",
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        """Materializes one or more SOP templates into an immediately runnable `custom_sop_*` tool."""
        comp_res = self.compose_sop_templates(template_ids)
        if not comp_res.get("ok"):
            return comp_res
        comp = comp_res["composed"]
        tname = tool_name.strip() or comp["template_id"]
        steps = [
            {"tool": st["tool"], "args": dict(st.get("args") or {}), "continue_on_error": True}
            for st in comp["tool_pipeline"]
        ]
        return self.create_composite_custom_tool(
            name=tname,
            description=f"SOP Playbook Tool: {comp['title']}"[:240],
            steps=steps,
            auto_learned=False,
            config=config,
        )

    def save_learned_sop_template(
        self,
        *,
        template_id: str,
        title: str,
        category: str,
        keywords: list[str],
        decision_rules: list[str],
        tool_pipeline: list[dict[str, Any]],
        output_template: str,
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        slug = re.sub(r"[^a-z0-9_]+", "_", (template_id or title or "learned_sop").strip().lower()).strip("_")
        if not slug.startswith("sop_"):
            slug = f"sop_{slug}"
        slug = slug[:52]
        with self._lock:
            data = self._load_learned_sop_templates()
            prev = dict(data.get(slug) or {})
            rec = {
                "template_id": slug,
                "title": (title or slug).strip()[:140],
                "category": (category or "learned").strip().lower(),
                "keywords": [str(k).strip().lower() for k in (keywords or []) if str(k).strip()][:12],
                "decision_rules": [str(r).strip() for r in (decision_rules or []) if str(r).strip()][:8],
                "tool_pipeline": [
                    {"tool": str(s.get("tool")), "args": dict(s.get("args") or {})}
                    for s in (tool_pipeline or [])
                    if isinstance(s, dict) and s.get("tool")
                ][:8],
                "output_template": (output_template or "").strip()[:1400],
                "source": "learned",
                "uses": int(prev.get("uses", 0) or 0) + 1,
                "successes": int(prev.get("successes", 0) or 0) + 1,
                "updated_at": utc_now_iso(),
            }
            data[slug] = rec
            self._save_learned_sop_templates(data)
        try:
            self.store_library_item(
                title=f"Learned SOP: {rec['title']}",
                body=(
                    f"# {rec['title']}\n"
                    "## Decision Rules\n" + "\n".join(f"- {r}" for r in rec["decision_rules"]) + "\n\n"
                    f"## Output Template\n{rec['output_template']}\n"
                ),
                category="sop_template",
                source_type="learned",
                tags=" ".join(rec["keywords"]) + f" sop template {rec['category']}",
                summary="; ".join(rec["decision_rules"][:2]) or rec["title"],
                item_id=f"sop-{slug}",
                config=config,
            )
        except Exception:
            pass
        return {"ok": True, "template": rec}

    def verify_work(
        self,
        *,
        action: str = "audit_draft",
        query: str = "",
        draft_text: str = "",
        session_id: str = "default",
        config: AppConfig | None = None,
    ) -> dict[str, Any]:
        """Self-checks Hermes outputs: audits git/file/CLI claims, cross-checks claims via web search, or exports an Arena verification packet."""
        act = (action or "audit_draft").strip().lower()
        repo = self.get_local_source_root()

        if act == "verify_with_web":
            q = (query or draft_text or "").strip()
            if not q:
                return {"ok": False, "error": "Provide a `query` or claim to verify against web search and the local library."}
            lib_hits = self.search_library(q, limit=3)
            web_hits: list[dict[str, str]] = []
            web_err = ""
            if config is None or bool(getattr(config, "hermes_allow_web_search", True)):
                try:
                    web_hits = self.web_search(q, max_results=4)
                except Exception as exc:
                    web_err = str(exc)
            else:
                web_err = "Web search is disabled in Hermes settings."
            return {
                "ok": bool(lib_hits or web_hits),
                "query": q,
                "local_library_hits": lib_hits,
                "web_search_hits": web_hits,
                "web_note": web_err,
                "verification_guidance": (
                    "Compare your draft against web_search_hits & local_library_hits. "
                    "If a local library item contradicts verified documentation, flag it via `astra_knowledge_library(action='mark_incorrect')`."
                ),
            }

        if act == "export_for_arena":
            msgs = self.get_session_messages(session_id, limit=6)
            last_user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
            last_ai = next((m["content"] for m in reversed(msgs) if m.get("role") == "assistant"), draft_text)
            git_note = ""
            try:
                if (repo / ".git").exists():
                    pr = subprocess.run(["git", "status", "-sb"], cwd=str(repo), capture_output=True, text=True, timeout=6, creationflags=_WIN_NO_WINDOW)
                    git_note = (pr.stdout or "")[:800].strip()
            except Exception:
                pass
            ui_tail = ""
            try:
                lp = self.cfg_mgr.base_dir / "ui_events.log"
                if lp.exists():
                    ui_tail = "\n".join(lp.read_text(encoding="utf-8", errors="ignore").splitlines()[-15:])
            except Exception:
                pass
            packet = (
                "=== AstraCradle Hermes Verification Packet ===\n"
                f"Prompt: {last_user[:500]}\n\n"
                f"Hermes Reply:\n{last_ai[:2200]}\n\n"
                f"Local Git Status ({repo}):\n{git_note or 'n/a'}\n\n"
                f"Recent UI Events:\n{ui_tail or 'none'}\n"
                "Please double-check Hermes's reasoning, commands, and safety."
            )
            ok_c, msg_c = self.clipboard_write(packet)
            return {"ok": True, "copied_to_clipboard": ok_c, "clipboard_note": msg_c, "packet": packet}

        # Default: audit_draft
        warnings: list[str] = []
        txt_l = (draft_text or "").lower()
        if (repo / ".git").exists():
            rebase_active = (repo / ".git" / "REBASE_HEAD").exists() or (repo / ".git" / "rebase-merge").exists() or (repo / ".git" / "rebase-apply").exists()
            try:
                pr = subprocess.run(["git", "status", "-sb"], cwd=str(repo), capture_output=True, text=True, timeout=6, creationflags=_WIN_NO_WINDOW)
                st_out = pr.stdout or ""
                unmerged = [ln[3:].strip() for ln in st_out.splitlines() if len(ln) >= 3 and ln[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD")]
            except Exception:
                st_out, unmerged = "", []
            if (rebase_active or unmerged) and ("git commit" in txt_l or "git add ." in txt_l):
                warnings.append(
                    "DANGER: Repo has an active rebase or unmerged conflict files! Do NOT advise `git add .` / `git commit` on detached HEAD—advise `git rebase --abort` OR `git checkout --theirs .` + `compileall` + `git rebase --continue`."
                )

        if re.search(r"\bastra_[a-z_]+\s+--[a-z_]+", txt_l):
            warnings.append("Draft suggests fake bash CLI flags for an internal `astra_*` AI function tool.")

        for m_py in re.findall(r"\b(astracradle/[A-Za-z0-9_./-]+\.py)\b", draft_text or ""):
            if not (repo / m_py).exists():
                warnings.append(f"Cited file path does not exist locally: {m_py}")

        return {
            "ok": len(warnings) == 0,
            "verified": len(warnings) == 0,
            "warnings": warnings,
            "repo": str(repo),
        }

    # ------------------------------------------------------------------
    # Full OS Auto-Fix (Standard OS Checks + Web Cross-Check + Stronger AI Escalation)
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_os_profile() -> dict[str, str]:
        sys_name = platform.system()
        distro = sys_name
        distro_id = sys_name.lower()
        if sys_name == "Linux":
            os_rel = Path("/etc/os-release")
            if os_rel.exists():
                kv: dict[str, str] = {}
                for ln in os_rel.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "=" in ln and not ln.strip().startswith("#"):
                        k, v = ln.split("=", 1)
                        kv[k.strip()] = v.strip().strip('"')
                distro = kv.get("PRETTY_NAME") or kv.get("NAME") or "Linux"
                distro_id = (kv.get("ID") or "linux").lower()
        elif sys_name == "Windows":
            distro = f"Windows {platform.release()} ({platform.version()})"
        elif sys_name == "Darwin":
            distro = f"macOS {platform.mac_ver()[0] or platform.release()}"
        return {
            "system": sys_name,
            "distro": distro,
            "distro_id": distro_id,
            "kernel": platform.release(),
            "arch": platform.machine(),
            "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
            "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
        }

    def run_full_os_autofix(
        self,
        config: AppConfig,
        *,
        apply_safe_fixes: bool = True,
        web_crosscheck: bool = True,
        escalate_stronger_ai: bool = True,
        copy_escalation_packet: bool = True,
        searcher: Callable[[str, int], list[dict[str, str]]] | None = None,
        escalate_llm_caller: Callable[..., tuple[str, list[dict[str, Any]]]] | None = None,
    ) -> dict[str, Any]:
        """Runs a standard OS-specific diagnostic & safe fix suite, detects custom features,
        cross-checks anomalies via internal memory + live web search, and escalates to a stronger AI model."""
        self.ensure_hermes_home(config)
        os_prof = self._detect_os_profile()
        sys_name = os_prof["system"]
        perm = self._effective_permission_level(config)

        checks: list[dict[str, Any]] = []
        anomalies: list[str] = []
        custom_features: list[str] = []
        fixes_applied: list[str] = []

        def _add_check(cid: str, label: str, ok: bool, detail: str, *, anomaly: str = "") -> None:
            checks.append({"id": cid, "label": label, "ok": ok, "detail": detail[:280]})
            if not ok and anomaly:
                anomalies.append(anomaly)

        # 1. Standard OS-specific health checks
        if sys_name == "Linux":
            # a) Systemd failed units
            if shutil.which("systemctl"):
                try:
                    pr = subprocess.run(
                        ["systemctl", "--failed", "--no-legend", "--plain"],
                        capture_output=True, text=True, timeout=6, creationflags=_WIN_NO_WINDOW,
                    )
                    failed_lines = [ln.strip() for ln in (pr.stdout or "").splitlines() if ln.strip()]
                    if failed_lines:
                        _add_check("systemd_failed", "Systemd Failed Units", False, "; ".join(failed_lines[:5]),
                                   anomaly=f"{os_prof['distro']} systemd failed units: {', '.join(ln.split()[0] for ln in failed_lines[:3])}")
                    else:
                        _add_check("systemd_failed", "Systemd Failed Units", True, "0 failed system units")
                except Exception as exc:
                    _add_check("systemd_failed", "Systemd Failed Units", True, f"Skipped ({exc})")

            # b) PipeWire / Audio session health
            if shutil.which("systemctl"):
                try:
                    pw = subprocess.run(
                        ["systemctl", "--user", "is-active", "pipewire", "wireplumber"],
                        capture_output=True, text=True, timeout=5, creationflags=_WIN_NO_WINDOW,
                    )
                    states = [ln.strip() for ln in (pw.stdout or "").splitlines() if ln.strip()]
                    pw_ok = pw.returncode == 0 or ("active" in states)
                    _add_check("audio_pipewire", "PipeWire / WirePlumber Audio", pw_ok, ", ".join(states) or "inactive",
                               anomaly="" if pw_ok else f"{os_prof['distro']} PipeWire audio service inactive")
                except Exception:
                    pass

            # c) Package manager health & stale lockfiles (read-only audit)
            if shutil.which("pacman"):
                lock_p = Path("/var/lib/pacman/db.lck")
                if lock_p.exists():
                    _add_check("pkg_db", "Pacman Package DB", False, "db.lck lockfile present",
                               anomaly=f"{os_prof['distro']} pacman db.lck stale lockfile")
                else:
                    _add_check("pkg_db", "Pacman Package DB", True, "No pacman lockfile; DB unlocked")
            elif shutil.which("dpkg"):
                try:
                    dp = subprocess.run(["dpkg", "--audit"], capture_output=True, text=True, timeout=8, creationflags=_WIN_NO_WINDOW)
                    dp_out = (dp.stdout or "").strip()
                    _add_check("pkg_db", "DPKG Package Audit", not dp_out, dp_out or "Clean package state",
                               anomaly="" if not dp_out else f"{os_prof['distro']} dpkg audit issues")
                except Exception:
                    pass

            # d) Journal size & custom kernel/scheduler/GPU features
            if shutil.which("journalctl"):
                try:
                    jr = subprocess.run(["journalctl", "--disk-usage"], capture_output=True, text=True, timeout=6, creationflags=_WIN_NO_WINDOW)
                    jtxt = (jr.stdout or "").strip()
                    _add_check("journal_disk", "Systemd Journal Disk Usage", True, jtxt or "OK")
                except Exception:
                    pass

            # Detect custom CachyOS / Linux kernel schedulers (scx_*), ROCm/NVIDIA, Wayland, Wine/Proton
            try:
                ps_out = subprocess.run(["ps", "-eo", "comm"], capture_output=True, text=True, timeout=5).stdout or ""
                procs_low = ps_out.lower()
                for feat in ("scx_lavd", "scx_bpfland", "scx_rusty", "llama-server", "ollama", "pipewire", "gamescope"):
                    if feat in procs_low:
                        custom_features.append(feat)
                if os_prof["session_type"]:
                    custom_features.append(f"session:{os_prof['session_type']}")
                if "cachyos" in os_prof["distro_id"] or "cachyos" in os_prof["kernel"].lower():
                    custom_features.append("cachyos-kernel")
            except Exception:
                pass

        elif sys_name == "Windows":
            _add_check("win_platform", "Windows OS Health", True, os_prof["distro"])
            custom_features.append("windows-nt")
        else:
            _add_check("mac_platform", "macOS Health", True, os_prof["distro"])
            custom_features.append("darwin")

        # 2. Cross-platform diagnostics scan + safe cache/quarantine/RAM fixes
        if self.diagnostics is not None:
            try:
                snap = self.diagnostics.scan()
                items = list(getattr(snap, "items", []) or [])
                crit = [it for it in items if str(getattr(it, "severity", "")).lower() in ("warning", "critical", "high")]
                _add_check(
                    "astra_diag_scan",
                    "AstraCradle Diagnostics Scan",
                    len(crit) == 0,
                    f"{len(items)} item(s) scanned, {len(crit)} warning/critical",
                    anomaly=(f"{os_prof['distro']} diagnostics warning: {getattr(crit[0], 'title', '')}" if crit else ""),
                )
            except Exception as exc:
                _add_check("astra_diag_scan", "AstraCradle Diagnostics Scan", False, str(exc))

        if self.ram_standby_mgr is not None:
            try:
                ram_r = self.ram_standby_mgr.detect_pressure_or_idle_llms()
                llm_list = list(ram_r.get("idle_llms") or [])
                if llm_list:
                    custom_features.append("local-llm-runtime")
                    _add_check("ram_llm_guard", "RAM & Local LLM Headroom", True, f"Detected {len(llm_list)} active/idle LLM process(es); game-safe protections honored")
            except Exception:
                pass

        if apply_safe_fixes and perm != "read_only" and self.safe_fixer is not None:
            try:
                fix_rep = self.safe_fixer.run_safe_fixes(dry_run=False)
                for act in list(getattr(fix_rep, "actions", []) or []):
                    fixes_applied.append(f"{getattr(act, 'title', 'Safe Fix')}: {getattr(act, 'detail', 'applied')}")
                if not fixes_applied:
                    fixes_applied.append("Safe cache/quarantine sweep ran cleanly (no stale junk needed removal)")
            except Exception as exc:
                fixes_applied.append(f"Safe fixer error: {exc}")
        elif not apply_safe_fixes or perm == "read_only":
            fixes_applied.append("Dry-run/Read-Only: skipped mutating safe fixes")

        # 3. Internal-first + Web Search Cross-Check for anomalies & custom OS features
        web_findings: list[dict[str, Any]] = []
        internal_findings: list[dict[str, Any]] = []
        queries: list[str] = []
        for anom in anomalies[:2]:
            queries.append(anom)
        if not queries and custom_features:
            queries.append(f"{os_prof['distro']} {' '.join(custom_features[:3])} optimization troubleshooting")
        elif custom_features:
            queries.append(f"{os_prof['distro']} {custom_features[0]} best practices")

        for q in queries[:3]:
            recall = self.deep_internal_recall(q, limit=2)
            if recall.get("library_hits"):
                internal_findings.extend(
                    {"query": q, "title": h["title"], "source_url": h.get("source_url", ""), "summary": h.get("summary", "")[:220]}
                    for h in recall["library_hits"][:2]
                )
            if web_crosscheck and bool(getattr(config, "hermes_allow_web_search", True)):
                try:
                    search_fn = searcher or self._web_search_impl
                    whits = search_fn(q, 3) or []
                    for h in whits[:3]:
                        web_findings.append({
                            "query": q,
                            "title": str(h.get("title") or ""),
                            "url": str(h.get("url") or ""),
                            "snippet": str(h.get("snippet") or "")[:220],
                        })
                except Exception:
                    pass

        # 4. Stronger AI Escalation (in-app cloud/heavy model if available + Arena clipboard packet)
        escalation_summary = ""
        escalated_model = ""
        escalation_prompt = (
            f"AstraCradle Full OS Auto-Fix Escalation ({os_prof['distro']}, kernel {os_prof['kernel']}, arch {os_prof['arch']})\n"
            f"Desktop/Session: {os_prof['desktop']} / {os_prof['session_type']}\n"
            f"Custom Features Detected: {', '.join(custom_features) or 'standard'}\n"
            f"Standard Checks:\n" + "\n".join(f"- [{'OK' if c['ok'] else 'WARN'}] {c['label']}: {c['detail']}" for c in checks) + "\n"
            f"Safe Fixes Already Applied:\n" + "\n".join(f"- {fx}" for fx in fixes_applied) + "\n"
            f"Anomalies Needing Review: {', '.join(anomalies) or 'None critical'}\n"
            f"Web Cross-Check Notes:\n" + ("\n".join(f"- {w['title']} ({w['url']}): {w['snippet']}" for w in web_findings[:4]) or "- None") + "\n\n"
            "Please review these OS findings, custom features, and web references and give exact safe next commands or tweaks if anything else is needed."
        )

        if escalate_stronger_ai:
            spec = self.resolve_provider_spec(config)
            env_map = self.load_env()
            api_key = env_map.get(spec["env_var"]) or os.environ.get(spec["env_var"], "")
            target_spec = dict(spec)
            can_escalate_in_app = False
            if escalate_llm_caller is not None:
                can_escalate_in_app = True
            elif spec["requires_key"] and api_key:
                can_escalate_in_app = True
            elif spec["provider"] == "ollama":
                # Pick heaviest pulled local model (e.g. 35B/30B/14B) if Ollama is alive
                ok_o, pulled = self.find_all_local_ollama_models()
                if ok_o and pulled:
                    best_heavy = next(
                        (m for m in pulled if any(sz in m.lower() for sz in ("35b", "32b", "30b", "27b", "14b", "12b"))),
                        spec["model"],
                    )
                    target_spec["model"] = best_heavy
                    target_spec["think_mode"] = False
                    can_escalate_in_app = True

            if can_escalate_in_app:
                try:
                    caller = escalate_llm_caller or (lambda msgs, tls, sp, key: self._call_llm(msgs, tls, sp, key))
                    e_msgs = [
                        {"role": "system", "content": "You are a senior OS reliability engineer. Give concise, safe, OS-specific remediation steps grounded only in the telemetry below."},
                        {"role": "user", "content": escalation_prompt},
                    ]
                    rep_txt, _ = caller(e_msgs, [], target_spec, api_key)
                    escalation_summary = (rep_txt or "").strip()[:1800]
                    escalated_model = str(target_spec.get("model") or "")
                except Exception as exc:
                    escalation_summary = f"In-app escalation skipped ({exc})"

        copied_pkt = False
        if copy_escalation_packet:
            ok_cp, _ = self.clipboard_write(escalation_prompt)
            copied_pkt = ok_cp

        return {
            "ok": True,
            "os_profile": os_prof,
            "checks": checks,
            "anomalies": anomalies,
            "custom_features": custom_features,
            "fixes_applied": fixes_applied,
            "internal_findings": internal_findings,
            "web_findings": web_findings,
            "escalated_model": escalated_model,
            "escalation_summary": escalation_summary,
            "escalation_packet": escalation_prompt,
            "copied_to_clipboard": copied_pkt,
        }

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
            if importlib.util.find_spec("hermes_agent") is not None:
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
            pkg_inst = importlib.util.find_spec("hermes_agent") is not None
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

    def delete_session(self, session_id: str) -> dict[str, Any]:
        """Deletes a chat session and all of its stored messages from sessions.db, returning the next active session ID."""
        sid = (session_id or "").strip() or "default"
        self._init_sessions_db()
        with sqlite3.connect(self.sessions_db_path) as conn:
            cur_m = conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            deleted_msgs = int(cur_m.rowcount or 0)
            cur_s = conn.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
            deleted_sess = int(cur_s.rowcount or 0)
            conn.commit()
            nxt_row = conn.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC LIMIT 1").fetchone()
        next_sid = str(nxt_row[0]) if nxt_row else "default"
        if next_sid == sid:
            next_sid = "default"
        return {
            "ok": True,
            "deleted_session": sid,
            "deleted_messages": deleted_msgs,
            "deleted_record": bool(deleted_sess),
            "next_session_id": next_sid,
        }

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
    # Persistent Subagent Profiles & Task Delegation
    # ------------------------------------------------------------------
    def list_subagents(self) -> list[dict[str, Any]]:
        if not self.subagents_path.exists():
            return []
        try:
            raw = json.loads(self.subagents_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    def create_subagent(
        self,
        name: str,
        role_instructions: str,
        *,
        default_task: str = "",
        max_turns: int = 4,
    ) -> dict[str, Any]:
        self.ensure_hermes_home()
        items = self.list_subagents()
        slug = re.sub(r"[^a-z0-9_-]+", "_", (name or "worker").strip().lower()).strip("_") or "worker"
        rec = {
            "subagent_id": f"sub_{slug}_{int(time.time())}",
            "name": (name or "Worker Subagent").strip()[:80],
            "role_instructions": (role_instructions or "Focus concisely on the delegated subtask and return verified results.").strip()[:800],
            "default_task": (default_task or "").strip()[:500],
            "max_turns": max(1, min(8, int(max_turns or 4))),
            "runs": 0,
            "last_summary": "",
            "last_run_at": "",
        }
        items = [x for x in items if str(x.get("name", "")).lower() != rec["name"].lower()]
        items.append(rec)
        self.subagents_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        return {"ok": True, "subagent": rec}

    def delete_subagent(self, subagent_id_or_name: str) -> dict[str, Any]:
        key = (subagent_id_or_name or "").strip().lower()
        items = self.list_subagents()
        kept = [x for x in items if str(x.get("subagent_id", "")).lower() != key and str(x.get("name", "")).lower() != key]
        self.subagents_path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        return {"ok": len(kept) < len(items), "remaining": len(kept)}

    def run_subagent(
        self,
        task: str,
        config: AppConfig,
        *,
        subagent_id_or_name: str = "",
        role_instructions: str = "",
        max_turns: int = 4,
        llm_caller: Callable[..., tuple[str, list[dict[str, Any]]]] | None = None,
    ) -> dict[str, Any]:
        self.ensure_hermes_home(config)
        items = self.list_subagents()
        key = (subagent_id_or_name or "").strip().lower()
        matched = next((x for x in items if key and (str(x.get("subagent_id", "")).lower() == key or str(x.get("name", "")).lower() == key)), None)
        eff_role = role_instructions or (matched.get("role_instructions", "") if matched else "")
        eff_task = (task or (matched.get("default_task", "") if matched else "")).strip()
        if not eff_task:
            return {"ok": False, "error": "Subagent task cannot be empty."}
        turns = int((matched or {}).get("max_turns") or max_turns or 4)
        full_prompt = f"[Subagent Role: {eff_role}]\nTask: {eff_task}" if eff_role else eff_task
        res = self.run_prompt(
            full_prompt,
            config,
            session_id=f"subagent_{int(time.time())}",
            allow_subagent=False,
            max_turns=max(1, min(8, turns)),
            llm_caller=llm_caller,
        )
        if matched is not None:
            matched["runs"] = int(matched.get("runs", 0) or 0) + 1
            matched["last_summary"] = (res.reply or res.error)[:400]
            matched["last_run_at"] = utc_now_iso()
            self.subagents_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        return {
            "ok": res.ok,
            "subagent": (matched.get("name") if matched else "ad-hoc"),
            "summary": res.reply[:2000],
            "tools": len(res.tool_events),
            "error": res.error,
        }

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
                    "name": "astra_lan_and_sinkhole",
                    "description": "Scan local LAN devices & Smart TVs (Samsung Tizen, LG, Roku, Chromecast, active YouTube detection), inspect AstraShield DNS Sinkhole status, start LAN sinkhole, or aim a Smart TV at AstraShield DNS with failsafe fallback.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["scan_lan", "sinkhole_status", "start_lan_sinkhole", "aim_tv_at_sinkhole", "passthrough_on", "passthrough_off", "restore_tv"],
                            },
                            "tv_ip": {"type": "string", "description": "Optional specific TV IPv4 address"},
                            "brand_filter": {"type": "string", "description": "Optional brand/keyword filter such as 'samsung' or 'youtube'"},
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
                    "description": "Search the live web ONLY after checking internal Knowledge Library/files (`deep_recall`), or when fresh online updates are needed. Checks internal library first automatically unless force_web=true.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer"},
                            "force_web": {"type": "boolean", "description": "Set true only if internal knowledge was insufficient or live internet info is required."},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_inspect_media",
                    "description": "Inspect any local picture, video (extracts keyframe images + duration/codecs), audio clip (metadata + offline transcript), PDF, or Office document (.docx/.xlsx/.pptx) so Hermes can see and read media files directly.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the picture, video, audio, PDF, or Office file"},
                        },
                        "required": ["path"],
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
                    "name": "memory_search",
                    "description": "Full-text search across MEMORY.md, USER.md, installed SKILL.md workflows, and past chat transcripts.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search keywords or topic"},
                            "limit": {"type": "integer", "description": "Max hits to return (default 10)"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_journal_and_crashes",
                    "description": "Inspect AstraCradle Forensic Journal self-audit warnings, Crash Post-Mortem reports, and Hermes silent diagnostics for root-cause fixing.",
                    "parameters": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_services_and_storage",
                    "description": "Inspect failed systemd user/system services, open listening ports, and drive usage / SMART health.",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_screen_and_clipboard",
                    "description": "Capture the active display/web page (screenshot + OCR text) and read or copy/paste text via the system clipboard.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["read_display", "capture_screen", "clipboard_read", "clipboard_write"]},
                            "text": {"type": "string", "description": "Text to copy to the clipboard when action=clipboard_write"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_git_sync",
                    "description": "Inspect Git status, create pre-sync backup zip, pull updates, or test-gate + commit & push local AstraCradle repo changes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["status", "backup", "pull", "commit_push"]},
                            "message": {"type": "string", "description": "Commit message for commit_push"},
                            "repo_path": {"type": "string", "description": "Optional git repo path (defaults to local AstraCradle root)"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_system_updates",
                    "description": "Check pending OS, Flatpak, and firmware updates (pacman/apt/dnf/fwupdmgr) and optionally run snapshot-backed upgrade (Level 4).",
                    "parameters": {
                        "type": "object",
                        "properties": {"action": {"type": "string", "enum": ["check", "upgrade"]}},
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_av_and_peripherals",
                    "description": "Inspect PipeWire/PulseAudio, display refresh rates/VRR, and USB power autosuspend (with safe USB stutter fix).",
                    "parameters": {
                        "type": "object",
                        "properties": {"action": {"type": "string", "enum": ["inspect", "fix_usb_autosuspend"]}},
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_thermals_and_fans",
                    "description": "Inspect CPU/GPU/NVMe temperatures, Welford thermal SPC drift anomalies, and GPU fan curves (optionally apply a curve).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["inspect", "apply_curve"]},
                            "profile_id": {"type": "string", "description": "e.g. gpu_cool_quiet or gpu_performance_max"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_voice_io",
                    "description": "Check offline STT/TTS tools, speak a short message aloud, or transcribe push-to-talk audio offline.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["status", "speak", "listen"]},
                            "text": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_dependencies",
                    "description": "Check or automatically download/install any missing AstraCradle Python packages and companion OS dependencies.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["check", "install_missing"]},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_knowledge_library",
                    "description": "Search AstraCradle's local Skill & Knowledge Library (1-200 GB configurable cache), manage subscribed research topics (add/remove/list/harvest full docs), inspect stored items & remaining storage, or prune stale info.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "search", "deep_recall", "store", "prefetch", "mark_incorrect", "prune", "stats",
                                    "list_topics", "add_topic", "remove_topic", "harvest_topic",
                                    "list_items", "delete_item",
                                ],
                            },
                            "query": {"type": "string"},
                            "topic": {"type": "string"},
                            "topic_id": {"type": "string"},
                            "harvest_now": {"type": "boolean"},
                            "remove_items": {"type": "boolean"},
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                            "category": {"type": "string"},
                            "source_url": {"type": "string"},
                            "item_id": {"type": "string"},
                            "reason": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delegate_subagent",
                    "description": "Create, list, delete, or run an isolated Hermes subagent for a focused task and return its summary.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["run", "create", "list", "delete"]},
                            "task": {"type": "string", "description": "Subtask prompt to execute"},
                            "name": {"type": "string", "description": "Saved subagent profile name"},
                            "role_instructions": {"type": "string", "description": "Specialized system role for this subagent"},
                            "max_turns": {"type": "integer"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_full_os_autofix",
                    "description": "Run AstraCradle's standard OS-specific common fixes & health checks, cross-check anomalies/custom features via web search, and escalate to a stronger AI model / Arena packet.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "apply_safe_fixes": {"type": "boolean"},
                            "web_crosscheck": {"type": "boolean"},
                            "escalate_stronger_ai": {"type": "boolean"},
                            "copy_escalation_packet": {"type": "boolean"},
                        },
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
            {
                "type": "function",
                "function": {
                    "name": "astra_sop_templates",
                    "description": "Query, compose, save, or instantiate Standard Operating Book templates (`SOP_TEMPLATE_LIBRARY`) so Hermes plugs live data into proven workflows and guardrails.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["list", "match", "get", "compose", "instantiate_tool", "save"],
                            },
                            "query": {"type": "string"},
                            "category": {"type": "string"},
                            "template_id": {"type": "string"},
                            "template_ids": {"type": "array", "items": {"type": "string"}},
                            "title": {"type": "string"},
                            "tool_name": {"type": "string"},
                            "keywords": {"type": "array", "items": {"type": "string"}},
                            "decision_rules": {"type": "array", "items": {"type": "string"}},
                            "tool_pipeline": {"type": "array", "items": {"type": "object"}},
                            "output_template": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["action"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "astra_verify_work",
                    "description": "Self-check and double-check Hermes's own work: audit drafts/git state/code paths, cross-check technical claims via live web_search + Knowledge Library, or export an Arena second-opinion packet to clipboard.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["audit_draft", "verify_with_web", "export_for_arena"],
                            },
                            "query": {"type": "string", "description": "Technical claim or command to cross-check on the web"},
                            "draft_text": {"type": "string", "description": "Proposed reply or commands to self-audit"},
                            "session_id": {"type": "string"},
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

            if name == "astra_lan_and_sinkhole":
                act = str(args.get("action") or "scan_lan").strip().lower()
                from .adblock_shield import AstraShieldManager
                from .lan_tv_shield import LanTvAdShield

                shield = self.adblock_shield or AstraShieldManager(
                    self.cfg_mgr, self.rollback_mgr, self.journal, elevation=self.elevation
                )
                lan_tv = self.lan_tv_shield or LanTvAdShield(self.journal, state_dir=self.cfg_mgr.base_dir)
                lan_ip = shield.get_lan_ipv4()
                router_ip = lan_tv.get_default_gateway_ip()
                st = shield.status()

                if act == "sinkhole_status":
                    fs = lan_tv.load_failsafe_state()
                    return {
                        "ok": True,
                        "lan_ip": lan_ip,
                        "router_failsafe_ip": router_ip,
                        "hosts_active": st.hosts_active,
                        "dns_running": st.dns_server_running,
                        "dns_listen_addr": st.dns_listen_addr,
                        "blocked_domain_count": st.blocked_domain_count,
                        "total_queries": st.total_queries,
                        "blocked_queries": st.blocked_queries,
                        "top_blocked": st.top_blocked[:8],
                        "client_stats": shield.get_client_stats(),
                        "tv_failsafe_state": fs,
                        "port_53_note": (
                            "Listening on fallback port 5353 — Smart TVs (including Samsung) require UDP port 53, so use elevation/NAT redirect (53 -> 5353) or run AstraCradle with port 53 forwarding."
                            if ":5353" in str(st.dns_listen_addr)
                            else ""
                        ),
                    }

                if act == "start_lan_sinkhole":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                    ok_s, msg_s = shield.start_lan_dns_sinkhole(
                        port=53,
                        fallback_port=int(getattr(config, "astrashield_dns_port", 5353) or 5353),
                        upstream=str(getattr(config, "astrashield_doh_upstream", "1.1.1.1") or "1.1.1.1"),
                        allow_elevation=True,
                    )
                    st_after = shield.status()
                    return {
                        "ok": ok_s,
                        "message": msg_s,
                        "lan_ip": lan_ip,
                        "router_failsafe_ip": router_ip,
                        "dns_listen_addr": st_after.dns_listen_addr,
                        "blocked_domain_count": st_after.blocked_domain_count,
                    }

                if act in ("passthrough_on", "passthrough_off"):
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                    msg_pt = shield.set_passthrough_mode(act == "passthrough_on")
                    return {"ok": True, "passthrough_mode": shield.passthrough_mode, "message": msg_pt}

                if act == "restore_tv":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                    ok_r, msg_r = lan_tv.restore_tv_settings(
                        tv_ip=str(args.get("tv_ip") or "") or None,
                        reason="hermes_user_request",
                        shield=shield,
                    )
                    return {"ok": ok_r, "message": msg_r}

                # scan_lan or aim_tv_at_sinkhole
                devices = lan_tv.scan_lan_devices(shield=shield, ssdp_timeout=0.7, probe_tv_apps=True)
                brand_f = str(args.get("brand_filter") or "").strip().lower()
                tv_ip_arg = str(args.get("tv_ip") or "").strip()

                dev_dicts = [
                    {
                        "ip": d.ip,
                        "mac": d.mac,
                        "hostname": d.hostname,
                        "brand": d.brand,
                        "model_name": d.model_name,
                        "device_type": d.device_type,
                        "active_app": d.active_app,
                        "ssdp_info": d.ssdp_info,
                        "dns_queries": d.dns_queries,
                        "ads_blocked": d.ads_blocked,
                        "protection_status": d.protection_status,
                    }
                    for d in devices
                ]

                if act == "scan_lan":
                    smart_tvs = [x for x in dev_dicts if x["device_type"] in ("smart_tv", "streaming_box") or (brand_f and brand_f in f"{x['brand']} {x['model_name']} {x['ssdp_info']} {x['active_app']}".lower())]
                    yt_active = [x for x in dev_dicts if "youtube" in str(x.get("active_app") or "").lower()]
                    return {
                        "ok": True,
                        "lan_ip": lan_ip,
                        "sinkhole_running": st.dns_server_running,
                        "sinkhole_addr": st.dns_listen_addr,
                        "total_devices": len(dev_dicts),
                        "smart_tvs": smart_tvs,
                        "youtube_active_devices": yt_active,
                        "devices": dev_dicts[:35],
                    }

                if act == "aim_tv_at_sinkhole":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                    target_dev = None
                    if tv_ip_arg:
                        target_dev = next((d for d in devices if d.ip == tv_ip_arg), None)
                    if target_dev is None and brand_f:
                        target_dev = next(
                            (d for d in devices if brand_f in f"{d.brand} {d.model_name} {d.hostname} {d.ssdp_info} {d.active_app}".lower()),
                            None,
                        )
                    if target_dev is None:
                        target_dev = next((d for d in devices if d.device_type in ("smart_tv", "streaming_box")), None)

                    # Ensure LAN AstraShield DNS Sinkhole is running
                    sinkhole_msg = ""
                    if not (shield.dns_running and str(shield._listen_addr).startswith("0.0.0.0:")):
                        _, sinkhole_msg = shield.start_lan_dns_sinkhole(
                            port=53,
                            fallback_port=int(getattr(config, "astrashield_dns_port", 5353) or 5353),
                            upstream=str(getattr(config, "astrashield_doh_upstream", "1.1.1.1") or "1.1.1.1"),
                            allow_elevation=True,
                        )
                    # Arm persistent LAN TV watchdog + auto-revert on shutdown/stop
                    try:
                        config.astrashield_lan_tv_mode = True
                        config.astrashield_tv_auto_failsafe = True
                        self.cfg_mgr.save(config)
                    except Exception:
                        pass
                    st_now = shield.status()
                    listen_port = 53
                    if ":5353" in str(st_now.dns_listen_addr) and getattr(shield, "_nat_redirect_port", None) is None:
                        listen_port = 5353

                    if target_dev is not None:
                        ok_tv, msg_tv, plan = lan_tv.connect_and_protect_tv(target_dev, lan_ip=lan_ip, dns_port=listen_port)
                        return {
                            "ok": ok_tv,
                            "message": msg_tv,
                            "sinkhole_status": sinkhole_msg or f"Listening on {st_now.dns_listen_addr} ({st_now.blocked_domain_count} blocked domains)",
                            "target_tv": {
                                "ip": target_dev.ip,
                                "brand": target_dev.brand,
                                "model_name": target_dev.model_name,
                                "active_app": target_dev.active_app,
                            },
                            "dns_primary": lan_ip,
                            "dns_secondary_failsafe": router_ip,
                            "listening_port": listen_port,
                            "port_53_forward_needed": (listen_port != 53),
                            "samsung_tv_steps": [
                                f"Samsung TVs only have ONE Manual 'DNS Server' box + 'IPv6 Details' (no Secondary DNS box on the TV itself).",
                                f"BEST Auto-Revert option (reverts automatically even when your PC is OFF): Keep Samsung TV DNS on 'Get Automatically', turn TV IPv6 OFF (Network -> Expert Settings -> IPv6), and set Router ({router_ip}) LAN DHCP Primary DNS = {lan_ip} & Secondary DNS = 1.1.1.1.",
                                f"Direct TV Manual option (while PC stays ON): Set TV DNS Server = {lan_ip}, turn TV IPv6 OFF so IPv6 doesn't bypass AstraShield, and use AstraShield Pass-Through mode whenever you want normal unblocked DNS.",
                            ] + ([f"IMPORTANT: AstraShield is currently on UDP port {listen_port}; Samsung TVs query UDP port 53, so click 'Enable LAN TV Mode' / allow elevated UDP 53 -> {listen_port} redirect."] if listen_port != 53 else []),
                            "plan": plan,
                        }
                    return {
                        "ok": True,
                        "message": "No Smart TV auto-matched in ARP/SSDP right now, but AstraShield LAN Sinkhole instructions are ready.",
                        "sinkhole_status": sinkhole_msg or f"Listening on {st_now.dns_listen_addr}",
                        "dns_primary": lan_ip,
                        "dns_secondary_failsafe": router_ip,
                        "listening_port": listen_port,
                        "port_53_forward_needed": (listen_port != 53),
                        "discovered_devices": dev_dicts[:15],
                    }

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
                force_web = bool(args.get("force_web", False))
                max_r = int(args.get("max_results", 6) or 6)
                # Internal-First Deep Memory Check: inspect local Knowledge Library & memory BEFORE hitting external web
                if not force_web and q:
                    recall = self.deep_internal_recall(q, limit=4)
                    if recall.get("has_strong_match"):
                        mapped_results = [
                            {
                                "title": str(h.get("title") or ""),
                                "url": str(h.get("source_url") or h.get("file_path") or "internal://knowledge-library"),
                                "snippet": str(h.get("summary") or "")[:280],
                            }
                            for h in (recall.get("library_hits") or [])
                        ]
                        return {
                            "ok": True,
                            "source": "internal_first_cache",
                            "internal_found": True,
                            "query": q,
                            "results": mapped_results,
                            "internal_library_hits": recall.get("library_hits") or [],
                            "internal_memory_hits": recall.get("memory_hits") or [],
                            "note": "Answered from your internal Knowledge Library & memory files first (no external web request needed). Pass force_web=true only if live internet updates are required.",
                        }
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
                return {"ok": True, "source": "live_web", "internal_found": False, "query": q, "results": hits}

            if name == "astra_inspect_media":
                mpath = str(args.get("path") or "").strip()
                if not mpath:
                    return {"ok": False, "error": "Provide a file path to the picture, video, audio, PDF, or Office file."}
                return self.inspect_media_file(mpath)

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

            if name == "memory_search":
                q = str(args.get("query", "")).strip()
                lim = int(args.get("limit", 10) or 10)
                hits = self.search_memory_fts(q, limit=lim)
                return {"ok": True, "query": q, "count": len(hits), "hits": hits}

            if name == "astra_journal_and_crashes":
                lim = max(5, min(50, int(args.get("limit", 20) or 20)))
                audit_list = []
                try:
                    audit_list = [
                        {"severity": a.severity, "title": a.title, "explanation": a.explanation, "remediation": a.remediation}
                        for a in self.journal.self_audit(limit=300)
                    ]
                except Exception:
                    pass
                recent_entries = []
                try:
                    for item in self.journal.load_recent(limit=lim):
                        if item.get("outcome") in ("warning", "error") or len(recent_entries) < 8:
                            recent_entries.append({
                                "ts": str(item.get("timestamp", ""))[:19],
                                "action": item.get("action"),
                                "outcome": item.get("outcome"),
                                "what": str(item.get("what", ""))[:160],
                            })
                except Exception:
                    pass
                crashes = []
                try:
                    ca = self.crash_analyzer
                    if ca is None:
                        from .crash_analyzer import CrashPostMortemAnalyzer

                        ca = CrashPostMortemAnalyzer(self.cfg_mgr, self.journal)
                    crashes = [
                        {"id": c.crash_id, "ts": c.timestamp, "summary": c.summary, "likely_cause": c.likely_cause}
                        for c in ca.load_history()[:6]
                    ]
                except Exception:
                    pass
                ui_events_tail: list[str] = []
                ui_log_path = self.cfg_mgr.base_dir / "ui_events.log"
                try:
                    if ui_log_path.exists():
                        ui_events_tail = ui_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]
                except Exception:
                    pass
                return {
                    "ok": True,
                    "audit_findings": audit_list,
                    "recent_journal": recent_entries,
                    "crash_post_mortems": crashes,
                    "hermes_diagnostics": self.get_recent_diagnostics(limit=10),
                    "ui_events_log_path": str(ui_log_path),
                    "recent_ui_events": ui_events_tail,
                }

            if name == "astra_services_and_storage":
                failed_units: list[str] = []
                if shutil.which("systemctl"):
                    for cmd in (["systemctl", "--user", "--failed", "--no-pager", "--plain"], ["systemctl", "--failed", "--no-pager", "--plain"]):
                        try:
                            pr = subprocess.run(cmd, capture_output=True, text=True, timeout=4, creationflags=_WIN_NO_WINDOW)
                            for ln in (pr.stdout or "").splitlines():
                                if "failed" in ln.lower() and not ln.strip().startswith("0 "):
                                    failed_units.append(ln.strip()[:160])
                        except Exception:
                            pass
                disks: list[dict[str, Any]] = []
                try:
                    for part in psutil.disk_partitions(all=False):
                        if any(skip in (part.fstype or "").lower() for skip in ("squashfs", "tmpfs", "devtmpfs", "overlay")):
                            continue
                        try:
                            du = psutil.disk_usage(part.mountpoint)
                            disks.append({
                                "mount": part.mountpoint,
                                "fs": part.fstype,
                                "free_gb": round(du.free / (1024**3), 1),
                                "total_gb": round(du.total / (1024**3), 1),
                                "used_pct": du.percent,
                            })
                        except Exception:
                            continue
                except Exception:
                    pass
                ports_summary: list[str] = []
                if shutil.which("ss"):
                    try:
                        pr = subprocess.run(["ss", "-tuln"], capture_output=True, text=True, timeout=4, creationflags=_WIN_NO_WINDOW)
                        ports_summary = [ln.strip()[:120] for ln in (pr.stdout or "").splitlines()[1:18]]
                    except Exception:
                        pass
                return {
                    "ok": True,
                    "failed_systemd_units": failed_units[:15],
                    "disks": disks[:10],
                    "listening_ports": ports_summary,
                    "rocm_multi_gpu": self.detect_rocm_multi_gpu_conflict(),
                }

            if name == "astra_screen_and_clipboard":
                act = str(args.get("action") or "read_display").strip().lower()
                if act == "clipboard_read":
                    ok_c, txt_c = self.clipboard_read()
                    return {"ok": ok_c, "clipboard": txt_c[:8000]}
                if act == "clipboard_write":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Clipboard copy blocked in Read-Only mode."}
                    txt_w = str(args.get("text") or "")
                    ok_w, msg_w = self.clipboard_write(txt_w)
                    return {"ok": ok_w, "message": msg_w, "chars": len(txt_w)}
                cap = self.capture_display_and_ocr()
                return cap

            if name == "astra_git_sync":
                act = str(args.get("action") or "status").strip().lower()
                repo = Path(str(args.get("repo_path") or "")).expanduser().resolve() if args.get("repo_path") else self.get_local_source_root()
                if act == "backup":
                    bdir = self.cfg_mgr.base_dir / "backups"
                    bdir.mkdir(parents=True, exist_ok=True)
                    zpath = bdir / f"astracradle-backup-{int(time.time())}.zip"
                    shutil.make_archive(str(zpath.with_suffix("")), "zip", str(repo))
                    return {"ok": True, "backup_zip": str(zpath)}
                if not (repo / ".git").exists():
                    return {"ok": False, "error": f"Not a git repository: {repo}"}
                if act == "status":
                    pr = subprocess.run(["git", "status", "-sb"], cwd=str(repo), capture_output=True, text=True, timeout=10, creationflags=_WIN_NO_WINDOW)
                    rem = subprocess.run(["git", "remote", "-v"], cwd=str(repo), capture_output=True, text=True, timeout=10, creationflags=_WIN_NO_WINDOW)
                    st_txt = pr.stdout[:3000]
                    rebase_active = (repo / ".git" / "REBASE_HEAD").exists() or (repo / ".git" / "rebase-merge").exists() or (repo / ".git" / "rebase-apply").exists()
                    merge_active = (repo / ".git" / "MERGE_HEAD").exists()
                    unmerged = [ln[3:].strip() for ln in st_txt.splitlines() if len(ln) >= 3 and ln[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD")]
                    detached = "HEAD (no branch)" in st_txt or "HEAD detached" in st_txt
                    rec_msg = ""
                    if rebase_active or unmerged:
                        rec_msg = (
                            "WARNING: Git rebase/merge conflict is in progress (detached HEAD). "
                            "Do NOT run `git commit`! Recover safely via `git rebase --abort` (to return cleanly to main) "
                            "OR resolve conflicts (`git checkout --theirs .`, verify `python3 -m compileall -q astracradle`, then `git add -A && git rebase --continue`)."
                        )
                    elif detached:
                        rec_msg = "WARNING: Repository is on detached HEAD. Switch back with `git checkout main` before committing."
                    return {
                        "ok": pr.returncode == 0,
                        "repo": str(repo),
                        "status": st_txt,
                        "remotes": rem.stdout[:1000],
                        "detached_head": detached,
                        "rebase_in_progress": rebase_active,
                        "merge_in_progress": merge_active,
                        "unmerged_files": unmerged,
                        "recommended_recovery": rec_msg,
                    }
                if perm in ("read_only", "safe_user"):
                    return {"ok": False, "blocked": True, "error": "Git pull/push requires Level 3 or Level 4 permissions."}
                ok_ap, msg_ap = self._request_mutation_approval(
                    f"Git {act}",
                    f"Hermes wants to run git {act} in:\n{repo}",
                    config,
                )
                if not ok_ap:
                    return {"ok": False, "blocked": True, "error": msg_ap}
                if act == "pull":
                    pr = subprocess.run(["git", "pull", "--rebase"], cwd=str(repo), capture_output=True, text=True, timeout=60, creationflags=_WIN_NO_WINDOW)
                    return {"ok": pr.returncode == 0, "output": (pr.stdout + pr.stderr)[:3000]}
                if act == "commit_push":
                    cc = subprocess.run([sys.executable, "-m", "compileall", "-q", "astracradle"], cwd=str(repo), capture_output=True, text=True, timeout=45, creationflags=_WIN_NO_WINDOW)
                    if cc.returncode != 0:
                        return {"ok": False, "error": f"Blocked commit_push because compileall failed:\n{cc.stderr[:1200]}"}
                    msg = str(args.get("message") or f"AstraCradle Hermes verified update ({utc_now_iso()[:10]})")
                    subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, text=True, timeout=20, creationflags=_WIN_NO_WINDOW)
                    cm = subprocess.run(["git", "commit", "-m", msg], cwd=str(repo), capture_output=True, text=True, timeout=20, creationflags=_WIN_NO_WINDOW)
                    ps = subprocess.run(["git", "push"], cwd=str(repo), capture_output=True, text=True, timeout=60, creationflags=_WIN_NO_WINDOW)
                    return {"ok": ps.returncode == 0, "commit": (cm.stdout + cm.stderr)[:1000], "push": (ps.stdout + ps.stderr)[:2000]}
                return {"ok": False, "error": f"Unknown git action: {act}"}

            if name == "astra_system_updates":
                act = str(args.get("action") or "check").strip().lower()
                pending_os: list[str] = []
                pkg_mgr = "unknown"
                if shutil.which("checkupdates"):
                    pkg_mgr = "pacman"
                    pr = subprocess.run(["checkupdates"], capture_output=True, text=True, timeout=25, creationflags=_WIN_NO_WINDOW)
                    pending_os = [ln.strip() for ln in (pr.stdout or "").splitlines() if ln.strip()][:35]
                elif shutil.which("pacman"):
                    pkg_mgr = "pacman"
                    pr = subprocess.run(["pacman", "-Qu"], capture_output=True, text=True, timeout=15, creationflags=_WIN_NO_WINDOW)
                    pending_os = [ln.strip() for ln in (pr.stdout or "").splitlines() if ln.strip()][:35]
                elif shutil.which("apt"):
                    pkg_mgr = "apt"
                    pr = subprocess.run(["apt", "list", "--upgradable"], capture_output=True, text=True, timeout=20, creationflags=_WIN_NO_WINDOW)
                    pending_os = [ln.strip() for ln in (pr.stdout or "").splitlines()[1:] if ln.strip()][:35]
                elif shutil.which("dnf"):
                    pkg_mgr = "dnf"
                    pr = subprocess.run(["dnf", "check-update", "-q"], capture_output=True, text=True, timeout=25, creationflags=_WIN_NO_WINDOW)
                    pending_os = [ln.strip() for ln in (pr.stdout or "").splitlines() if ln.strip()][:35]
                flatpak_updates: list[str] = []
                if shutil.which("flatpak"):
                    fp = subprocess.run(["flatpak", "remote-ls", "--updates"], capture_output=True, text=True, timeout=15, creationflags=_WIN_NO_WINDOW)
                    flatpak_updates = [ln.strip() for ln in (fp.stdout or "").splitlines() if ln.strip()][:20]
                fw_updates = ""
                if shutil.which("fwupdmgr"):
                    fw = subprocess.run(["fwupdmgr", "get-updates"], capture_output=True, text=True, timeout=15, creationflags=_WIN_NO_WINDOW)
                    fw_updates = (fw.stdout or fw.stderr or "")[:800]
                if act == "check":
                    return {
                        "ok": True,
                        "package_manager": pkg_mgr,
                        "os_updates_count": len(pending_os),
                        "os_updates": pending_os[:20],
                        "flatpak_updates": flatpak_updates,
                        "firmware": fw_updates,
                        "snapshot_tool": ("timeshift" if shutil.which("timeshift") else ("snapper" if shutil.which("snapper") else "none")),
                    }
                if perm != "sudo_with_prompt":
                    return {"ok": False, "blocked": True, "error": "OS package upgrade requires Level 4 (Sudo When Password Typed)."}
                snap_prefix = "timeshift --create --comments 'Pre-Hermes-Update' && " if shutil.which("timeshift") else ""
                up_cmd = f"{snap_prefix}pacman -Syu --noconfirm" if pkg_mgr == "pacman" else (f"{snap_prefix}apt-get upgrade -y" if pkg_mgr == "apt" else f"{snap_prefix}dnf upgrade -y")
                return self._run_sudo_prompted(up_cmd, "Upgrade OS packages (with pre-upgrade snapshot if available)", config)

            if name == "astra_av_and_peripherals":
                act = str(args.get("action") or "inspect").strip().lower()
                if act == "fix_usb_autosuspend":
                    if perm != "sudo_with_prompt":
                        return {"ok": False, "blocked": True, "error": "Changing kernel USB autosuspend requires Level 4 (Sudo When Password Typed)."}
                    cmd = "for f in /sys/bus/usb/devices/*/power/control; do echo on > \"$f\" 2>/dev/null || true; done"
                    return self._run_sudo_prompted(cmd, "Disable USB autosuspend to prevent mouse/audio headset stutter", config)
                audio_txt = ""
                for cmd in (["wpctl", "status"], ["pactl", "info"]):
                    if shutil.which(cmd[0]):
                        pr = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=_WIN_NO_WINDOW)
                        audio_txt = (pr.stdout or "")[:1200]
                        if audio_txt:
                            break
                disp_txt = ""
                for cmd in (["kscreen-doctor", "-o"], ["wlr-randr"], ["xrandr", "--query"]):
                    if shutil.which(cmd[0]):
                        pr = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=_WIN_NO_WINDOW)
                        disp_txt = (pr.stdout or "")[:1200]
                        if disp_txt:
                            break
                usb_auto: list[str] = []
                try:
                    for ctl in sorted(Path("/sys/bus/usb/devices").glob("*/power/control"))[:20]:
                        val = ctl.read_text(encoding="utf-8", errors="ignore").strip()
                        if val == "auto":
                            usb_auto.append(ctl.parent.parent.name)
                except Exception:
                    pass
                return {"ok": True, "audio": audio_txt, "display": disp_txt, "usb_autosuspend_devices": usb_auto}

            if name == "astra_thermals_and_fans":
                act = str(args.get("action") or "inspect").strip().lower()
                gpu = GpuTelemetryCollector.collect()
                applied_id, pts = self.gpu_pwr.get_applied_curve() if self.gpu_pwr is not None else ("default", [])
                if act == "apply_curve" and self.gpu_pwr is not None:
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Fan curve tuning blocked in Read-Only mode."}
                    pid_req = str(args.get("profile_id") or "gpu_cool_quiet")
                    profs = self.gpu_pwr.load_profiles()
                    chosen_p = next((p for p in profs if p.profile_id == pid_req), profs[0] if profs else None)
                    if chosen_p is None:
                        return {"ok": False, "error": f"Profile '{pid_req}' not found."}
                    ok_c, msg_c, new_pts = self.gpu_pwr.apply_draft_curve(chosen_p.curve_points, profile_id=chosen_p.profile_id, dry_run=False)
                    return {"ok": ok_c, "message": msg_c, "curve": new_pts}
                baselines = {}
                anomalies = []
                if self.thermal_spc is not None:
                    baselines = {k: {"mean_temp_c": round(v.mean_temp_c, 1), "count": v.count} for k, v in self.thermal_spc.load_baselines().items()}
                    anomalies = [str(a) for a in (self.thermal_spc.anomalies or [])[-6:]]
                return {
                    "ok": True,
                    "gpu_name": getattr(gpu, "name", "Integrated"),
                    "gpu_temp_c": getattr(gpu, "temperature_c", None),
                    "applied_curve_id": applied_id,
                    "curve_points": pts,
                    "spc_baselines": baselines,
                    "spc_anomalies": anomalies,
                }

            if name == "astra_voice_io":
                act = str(args.get("action") or "status").strip().lower()
                if act == "speak":
                    ok_s, msg_s = self.speak_text_offline(str(args.get("text") or ""))
                    return {"ok": ok_s, "message": msg_s}
                if act == "listen":
                    ok_l, txt_l = self.listen_voice_offline()
                    return {"ok": ok_l, "transcript": txt_l}
                return {
                    "ok": True,
                    "tts_backends": [b for b in ("piper", "espeak-ng", "spd-say", "say") if shutil.which(b)],
                    "stt_backends": [b for b in ("whisper-cli", "whisper", "faster-whisper") if shutil.which(b)],
                }

            if name == "astra_dependencies":
                act = str(args.get("action") or "check").strip().lower()
                if act == "install_missing" and perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Installing dependencies is blocked in Read-Only mode."}
                return self.check_and_install_missing_dependencies(config, install=(act == "install_missing"))

            if name == "astra_knowledge_library":
                act = str(args.get("action") or "search").strip().lower()
                if act == "stats":
                    return self.get_library_stats(config)
                if act == "search":
                    q = str(args.get("query") or "").strip()
                    lim = int(args.get("limit", 5) or 5)
                    return {"ok": True, "query": q, "hits": self.search_library(q, limit=lim)}
                if act == "deep_recall":
                    q = str(args.get("query") or "").strip()
                    lim = int(args.get("limit", 4) or 4)
                    return self.deep_internal_recall(q, limit=lim)
                if act == "store":
                    return self.store_library_item(
                        title=str(args.get("title") or "Learned Item"),
                        body=str(args.get("body") or ""),
                        category=str(args.get("category") or "learned"),
                        source_type="learned",
                        source_url=str(args.get("source_url") or ""),
                        config=config,
                    )
                if act == "prefetch":
                    return self.prefetch_library_topics(config)
                if act == "list_topics":
                    tlist = self.list_library_topics()
                    return {"ok": True, "count": len(tlist), "topics": tlist}
                if act == "add_topic":
                    return self.add_library_topic(
                        str(args.get("topic") or args.get("query") or ""),
                        category=str(args.get("category") or "programming"),
                        harvest_now=bool(args.get("harvest_now", True)),
                        config=config,
                    )
                if act == "remove_topic":
                    return self.remove_library_topic(
                        str(args.get("topic_id") or args.get("topic") or args.get("query") or ""),
                        remove_items=bool(args.get("remove_items", True)),
                    )
                if act == "harvest_topic":
                    return self.harvest_library_topic(
                        str(args.get("topic_id") or args.get("topic") or args.get("query") or ""),
                        config,
                    )
                if act == "list_items":
                    ilist = self.list_library_items(
                        query=str(args.get("query") or ""),
                        category=str(args.get("category") or ""),
                        limit=int(args.get("limit", 40) or 40),
                    )
                    return {"ok": True, "count": len(ilist), "items": ilist}
                if act == "delete_item":
                    return self.delete_library_item(str(args.get("item_id") or ""))
                if act == "mark_incorrect":
                    tgt = str(args.get("item_id") or args.get("source_url") or "")
                    return self.mark_library_item_incorrect(tgt, reason=str(args.get("reason") or ""))
                if act == "prune":
                    return self.prune_library(config, aggressive=True)
                return {"ok": False, "error": f"Unknown library action: {act}"}

            if name == "astra_custom_tool_forge":
                act = str(args.get("action") or "list").strip().lower()
                if act == "list":
                    return {"ok": True, "tools": self.list_custom_tools(include_disabled=True)}
                if act == "create_composite":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Creating custom tools is blocked in Read-Only mode."}
                    return self.create_composite_custom_tool(
                        name=str(args.get("name") or ""),
                        description=str(args.get("description") or ""),
                        steps=list(args.get("steps") or []),
                        parameters=args.get("parameters") if isinstance(args.get("parameters"), dict) else None,
                        config=config,
                    )
                if act == "create_python":
                    return self.create_python_custom_tool(
                        name=str(args.get("name") or ""),
                        description=str(args.get("description") or ""),
                        code=str(args.get("code") or ""),
                        parameters=args.get("parameters") if isinstance(args.get("parameters"), dict) else None,
                        config=config,
                    )
                if act == "combine":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Combining tools is blocked in Read-Only mode."}
                    return self.combine_tools_into_custom(
                        name=str(args.get("name") or ""),
                        description=str(args.get("description") or ""),
                        source_tools=list(args.get("source_tools") or []),
                        config=config,
                    )
                if act == "improve":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Improving custom tools is blocked in Read-Only mode."}
                    return self.improve_custom_tool(
                        name=str(args.get("name") or ""),
                        description=str(args.get("description") or ""),
                        steps=args.get("steps") if isinstance(args.get("steps"), list) else None,
                        code=str(args.get("code") or ""),
                        improvement_note=str(args.get("improvement_note") or ""),
                        config=config,
                    )
                if act == "run":
                    return self.execute_custom_tool(
                        str(args.get("name") or ""),
                        dict(args.get("args") or {}),
                        config,
                        allow_subagent=allow_subagent,
                    )
                if act == "toggle":
                    return self.toggle_custom_tool(str(args.get("name") or ""), bool(args.get("enabled", True)))
                if act == "delete":
                    return self.delete_custom_tool(str(args.get("name") or ""))
                return {"ok": False, "error": f"Unknown custom tool forge action: {act}"}

            if name == "astra_sop_templates":
                act = str(args.get("action") or "match").strip().lower()
                if act == "list":
                    items = self.list_sop_templates(category=str(args.get("category") or ""))
                    return {
                        "ok": True,
                        "count": len(items),
                        "templates": [{"template_id": i["template_id"], "title": i["title"], "category": i["category"], "source": i.get("source", "builtin")} for i in items],
                    }
                if act == "match":
                    hits = self.match_sop_templates(str(args.get("query") or ""), limit=int(args.get("limit", 3) or 3))
                    return {"ok": True, "matches": hits}
                if act == "get":
                    t = self.get_sop_template(str(args.get("template_id") or ""))
                    return {"ok": t is not None, **({"template": t} if t else {"error": "Template not found"})}
                if act == "compose":
                    return self.compose_sop_templates(list(args.get("template_ids") or []), title=str(args.get("title") or ""))
                if act == "instantiate_tool":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Instantiating SOP custom tools is blocked in Read-Only mode."}
                    return self.instantiate_sop_as_custom_tool(
                        list(args.get("template_ids") or ([args.get("template_id")] if args.get("template_id") else [])),
                        tool_name=str(args.get("tool_name") or ""),
                        config=config,
                    )
                if act == "save":
                    if perm == "read_only":
                        return {"ok": False, "blocked": True, "error": "Saving learned SOP templates is blocked in Read-Only mode."}
                    return self.save_learned_sop_template(
                        template_id=str(args.get("template_id") or ""),
                        title=str(args.get("title") or "Learned Operating Template"),
                        category=str(args.get("category") or "learned"),
                        keywords=list(args.get("keywords") or []),
                        decision_rules=list(args.get("decision_rules") or []),
                        tool_pipeline=list(args.get("tool_pipeline") or []),
                        output_template=str(args.get("output_template") or ""),
                        config=config,
                    )
                return {"ok": False, "error": f"Unknown SOP template action: {act}"}

            if name == "astra_verify_work":
                return self.verify_work(
                    action=str(args.get("action") or "audit_draft"),
                    query=str(args.get("query") or ""),
                    draft_text=str(args.get("draft_text") or ""),
                    session_id=str(args.get("session_id") or "default"),
                    config=config,
                )

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

            if name == "astra_full_os_autofix":
                return self.run_full_os_autofix(
                    config,
                    apply_safe_fixes=bool(args.get("apply_safe_fixes", True)),
                    web_crosscheck=bool(args.get("web_crosscheck", True)),
                    escalate_stronger_ai=bool(args.get("escalate_stronger_ai", True)),
                    copy_escalation_packet=bool(args.get("copy_escalation_packet", True)),
                )

            if name == "delegate_subagent":
                act = str(args.get("action") or "run").strip().lower()
                if act == "list":
                    subs = self.list_subagents()
                    return {"ok": True, "count": len(subs), "subagents": subs}
                if act == "create":
                    return self.create_subagent(
                        str(args.get("name") or "Worker Subagent"),
                        str(args.get("role_instructions") or ""),
                        default_task=str(args.get("task") or ""),
                        max_turns=int(args.get("max_turns") or 4),
                    )
                if act == "delete":
                    return self.delete_subagent(str(args.get("name") or args.get("subagent_id") or ""))
                if not allow_subagent:
                    return {"ok": False, "error": "Nested subagent recursion capped at depth 1."}
                return self.run_subagent(
                    str(args.get("task", "")),
                    config,
                    subagent_id_or_name=str(args.get("name") or args.get("subagent_id") or ""),
                    role_instructions=str(args.get("role_instructions") or ""),
                    max_turns=int(args.get("max_turns") or 4),
                )

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

    # Dynamic Local Model Tiering (Fast 4B-9B -> Balanced 12B-14B -> Heavy 27B-35B+)
    _FAST_LOCAL_CANDIDATES: tuple[str, ...] = (
        "qwen3:8b",
        "qwen3.5:9b",
        "qwen2.5:7b",
        "qwen3:4b",
        "qwen3.5:4b",
        "llama3.1:8b",
        "gemma4:e4b",
        "gemma3:4b",
        "phi4-mini",
    )
    _BALANCED_LOCAL_CANDIDATES: tuple[str, ...] = (
        "qwen3:14b",
        "qwen2.5-coder:14b",
        "qwen2.5:14b",
        "gemma4:12b",
        "gemma3:12b",
    )

    @staticmethod
    def classify_prompt_complexity(prompt: str, history: list[dict[str, str]] | None = None) -> str:
        """Classifies prompt into 'fast', 'balanced', or 'heavy' for dynamic intelligent model scaling."""
        txt = (prompt or "").strip().lower()
        n_chars = len(txt)

        heavy_markers = (
            "code",
            "edit ",
            "file_edit",
            "file_write",
            "refactor",
            "implement",
            "build ",
            "next update",
            "self_build",
            "astracradle",
            "debug",
            "traceback",
            "stack trace",
            "root cause",
            "architecture",
            "research",
            "web search",
            "compare ",
            "deep ",
            "analyze ",
            "step by step",
            "comprehensive",
            "benchmark",
            "script",
            "python",
            "patch",
        )
        balanced_markers = (
            "diagnose",
            "scan",
            "fix ",
            "optimize",
            "wi-fi",
            "wifi",
            "startup",
            "probalance",
            "process",
            "gpu",
            "vram",
            "thermal",
            "remote",
            "tailscale",
            "astrabeam",
            "skill",
            "memory",
            "self-heal",
            "uninstall",
            "dependency",
            "dependencies",
        )
        if n_chars >= 420 or any(tok in txt for tok in heavy_markers):
            return "heavy"
        if n_chars >= 140 or any(tok in txt for tok in balanced_markers):
            return "balanced"
        # Also escalate slightly if the conversation already involves code or multi-turn technical depth
        if history and len(history) >= 6:
            recent_blob = " ".join(str(m.get("content") or "").lower()[:180] for m in history[-4:])
            if any(tok in recent_blob for tok in ("traceback", "def ", "class ", "file_edit", "astra_dev_self_build")):
                return "heavy"
        return "fast"

    _VISION_MODEL_HINTS = (
        "qwen3.6",
        "qwen3-vl",
        "qwen2.5vl",
        "qwen2-vl",
        "llama3.2-vision",
        "llava",
        "minicpm-v",
        "moondream",
        "gemma3",
        "pixtral",
        "bakllava",
        "vision",
        "-vl",
    )

    def _is_vision_model(self, model_name: str, base_url: str = "http://127.0.0.1:11434") -> bool:
        low = (model_name or "").lower()
        if any(h in low for h in self._VISION_MODEL_HINTS):
            return True
        if not low:
            return False
        try:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/show",
                data=json.dumps({"model": model_name}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                info = json.loads(resp.read().decode("utf-8", errors="ignore"))
            caps = [str(c).lower() for c in (info.get("capabilities") or [])]
            if "vision" in caps:
                return True
            mi = info.get("model_info") or {}
            if any("vision" in str(k).lower() or "mmproj" in str(k).lower() for k in mi.keys()):
                return True
        except Exception:
            pass
        return False

    def _find_vision_ollama_model(self, pulled: list[str], preferred: str = "", base_url: str = "http://127.0.0.1:11434") -> str | None:
        ordered = ([preferred] if preferred else []) + [m for m in pulled if m != preferred]
        for cand in ordered:
            if cand and self._is_vision_model(cand, base_url=base_url):
                return cand
        return None

    def select_dynamic_ollama_model(
        self,
        prompt: str,
        config: AppConfig,
        *,
        history: list[dict[str, str]] | None = None,
        installed_models: list[str] | None = None,
        has_images: bool = False,
    ) -> dict[str, Any]:
        """Dynamically picks the fastest intelligent local model matching prompt complexity, vision needs, and GPU headroom."""
        spec = self.resolve_provider_spec(config)
        heavy_model = spec["model"]
        tier = self.classify_prompt_complexity(prompt, history=history)
        if installed_models is None:
            ok, listed = self.find_all_local_ollama_models()
            pulled = listed if ok else self.scan_ollama_disk_manifests()
        else:
            pulled = list(installed_models)

        if has_images and spec["provider"] == "ollama":
            vis_model = self._find_vision_ollama_model(pulled, preferred=heavy_model, base_url=spec["base_url"])
            if vis_model:
                return {
                    "tier": "heavy",
                    "model": vis_model,
                    "think": False,
                    "reason": f"Vision attachment detected -> routed to multimodal model '{vis_model}'",
                    "fast_available": True,
                    "missing_fast_recommendation": "",
                    "vision_routed": True,
                }

        if not bool(getattr(config, "hermes_dynamic_model_routing", True)) or spec["provider"] != "ollama":
            return {
                "tier": tier,
                "model": heavy_model,
                "think": (tier == "heavy"),
                "reason": "Static model mode",
                "fast_available": True,
                "missing_fast_recommendation": "",
            }

        fast_pref = str(getattr(config, "hermes_fast_model", "") or "").strip()
        fast_candidates = ([fast_pref] if fast_pref else []) + list(self._FAST_LOCAL_CANDIDATES)
        fast_match = next((self._model_tag_matches(c, pulled) for c in fast_candidates if self._model_tag_matches(c, pulled)), None)
        balanced_match = next((self._model_tag_matches(c, pulled) for c in self._BALANCED_LOCAL_CANDIDATES if self._model_tag_matches(c, pulled)), None)

        if tier == "fast":
            chosen = fast_match or balanced_match or heavy_model
            think = False
            reason = f"Fast tier -> '{chosen}' (instant response, think=off)" if fast_match else f"Fast tier requested (no 4B-9B model pulled yet; using '{chosen}' with think=off)"
        elif tier == "balanced":
            chosen = balanced_match or fast_match or heavy_model
            think = False
            reason = f"Balanced OS tier -> '{chosen}'"
        else:
            chosen = heavy_model
            think = any(tok in (prompt or "").lower() for tok in ("debug", "traceback", "stack trace", "root cause", "architecture", "refactor", "step by step")) or len(prompt or "") >= 320
            reason = f"Deep engineering/research tier -> '{chosen}' (think={'on' if think else 'off'})"

        # If chosen model previously failed on GPU/tools (cpu_safe/cpu_notools) and a working GPU model exists,
        # prefer the working GPU model (with fast tool calling!) instead of running 35B on slow CPU/no-tools.
        if chosen in self._gpu_fallback_models and not has_images:
            alt_gpu = next((m for m in (balanced_match, fast_match) if m and m not in self._gpu_fallback_models), None)
            if alt_gpu:
                chosen = alt_gpu
                think = False
                reason = f"GPU-fast fallback -> '{chosen}' (keeps tool calling & GPU acceleration active)"

        return {
            "tier": tier,
            "model": chosen,
            "think": think,
            "reason": reason,
            "fast_available": bool(fast_match),
            "missing_fast_recommendation": ("" if fast_match else "qwen3:4b"),
        }

    def ensure_fast_companion_model_async(
        self,
        config: AppConfig,
        progress_cb: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Background-pulls a lightweight fast companion model (qwen3:8b) if user only has a giant 35B+ model."""
        if not bool(getattr(config, "hermes_dynamic_model_routing", True)):
            return {"started": False, "reason": "Dynamic routing disabled"}
        ok, pulled = self.find_all_local_ollama_models()
        if not ok and not pulled:
            return {"started": False, "reason": "Ollama not running"}
        for cand in self._FAST_LOCAL_CANDIDATES:
            if self._model_tag_matches(cand, pulled):
                return {"started": False, "already_present": True, "model": cand}
        if self._fast_pull_started:
            return {"started": False, "reason": "Pull already in progress"}
        self._fast_pull_started = True
        target = str(getattr(config, "hermes_fast_model", "") or "").strip() or "qwen3:4b"

        def _worker() -> None:
            try:
                self.pull_ollama_model(target, progress_cb=progress_cb)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()
        return {"started": True, "model": target}

    # ------------------------------------------------------------------
    # Silent Diagnostics, ROCm Fix & Autonomous Passive Whole-PC Guardian
    # ------------------------------------------------------------------
    def _load_runtime_model_health(self) -> None:
        if not self.runtime_health_path.exists():
            return
        try:
            raw = json.loads(self.runtime_health_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                fb = raw.get("gpu_fallback_models") or []
                if isinstance(fb, list):
                    self._gpu_fallback_models = {str(m) for m in fb if m}
        except Exception:
            pass

    def _save_gpu_fallback_model(self, model: str, stage: str = "cpu_safe") -> None:
        if not model:
            return
        self._gpu_fallback_models.add(model)
        try:
            self.hermes_home.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if self.runtime_health_path.exists():
                try:
                    data = json.loads(self.runtime_health_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    data = {}
            data["gpu_fallback_models"] = sorted(self._gpu_fallback_models)
            notes = dict(data.get("notes") or {})
            notes[model] = {"stage": stage, "updated_at": utc_now_iso()}
            data["notes"] = notes
            self.runtime_health_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _clear_runtime_model_health(self) -> None:
        self._gpu_fallback_models.clear()
        try:
            if self.runtime_health_path.exists():
                self.runtime_health_path.unlink()
        except Exception:
            pass

    def search_memory_fts(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        """Full-text FTS5 search across MEMORY.md, USER.md, skills, and sessions.db (with LIKE fallback)."""
        q = (query or "").strip()
        if not q:
            return []
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        docs: list[tuple[str, str, str]] = []
        for lbl, fp in (("MEMORY.md", self.memory_path), ("USER.md", self.user_md_path)):
            if fp.exists():
                for idx, chunk in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines()):
                    if chunk.strip():
                        docs.append((lbl, f"{lbl}:L{idx+1}", chunk.strip()))
        for sk in self.list_skills():
            docs.append((f"skill:{sk['slug']}", sk["path"], sk["content"][:2500]))
        if self.sessions_db_path.exists():
            try:
                with sqlite3.connect(self.sessions_db_path) as sconn:
                    for sid, role, content, ts in sconn.execute(
                        "SELECT session_id, role, content, created_at FROM messages ORDER BY id DESC LIMIT 400"
                    ).fetchall():
                        if content and str(content).strip():
                            docs.append((f"chat:{sid}", f"{role} ({str(ts)[:16]})", str(content)[:1200]))
            except Exception:
                pass

        try:
            with sqlite3.connect(self.memory_index_path) as conn:
                conn.execute("DROP TABLE IF EXISTS memory_fts")
                conn.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(source, ref, body)")
                conn.executemany("INSERT INTO memory_fts(source, ref, body) VALUES (?, ?, ?)", docs)
                tokens = [re.sub(r"[^a-zA-Z0-9_-]", "", t) for t in q.split() if len(t.strip()) >= 2]
                fts_q = " OR ".join(f'"{t}"*' for t in tokens[:8]) or f'"{q}"'
                rows = conn.execute(
                    "SELECT source, ref, snippet(memory_fts, 2, '[', ']', '...', 24), body FROM memory_fts WHERE memory_fts MATCH ? LIMIT ?",
                    (fts_q, max(1, min(30, int(limit) * 2))),
                ).fetchall()
                hits_out = [{"source": r[0], "ref": r[1], "snippet": r[2], "_body": r[3]} for r in rows]
                # Hybrid Local Vector Rerank when an Ollama embedding model is installed
                q_vec = self._compute_local_embedding(q)
                if q_vec and hits_out:
                    scored: list[tuple[float, dict[str, str]]] = []
                    for idx_h, item in enumerate(hits_out):
                        d_vec = self._compute_local_embedding(item.get("_body", "")[:600])
                        sim = self._cosine_sim(q_vec, d_vec) if d_vec else (1.0 / (1.0 + idx_h))
                        scored.append((sim, item))
                    scored.sort(key=lambda pair: pair[0], reverse=True)
                    hits_out = [pair[1] for pair in scored]
                return [{"source": h["source"], "ref": h["ref"], "snippet": h["snippet"]} for h in hits_out[: int(limit)]]
        except Exception:
            low_tokens = [t.lower() for t in q.split() if t.strip()]
            hits: list[dict[str, str]] = []
            for src, ref, body in docs:
                bl = body.lower()
                if any(t in bl for t in low_tokens):
                    hits.append({"source": src, "ref": ref, "snippet": body[:240]})
                    if len(hits) >= limit:
                        break
            return hits

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return (dot / (na * nb)) if (na > 0 and nb > 0) else 0.0

    def _compute_local_embedding(self, text: str) -> list[float]:
        """Computes a local vector embedding via Ollama /api/embed if an embedding model is installed."""
        clean = (text or "").strip()[:1000]
        if not clean:
            return []
        try:
            ok, pulled = self.find_all_local_ollama_models()
            if not ok:
                return []
            emb_model = next(
                (self._model_tag_matches(c, pulled) for c in ("nomic-embed-text", "mxbai-embed-large", "all-minilm") if self._model_tag_matches(c, pulled)),
                None,
            )
            if not emb_model:
                return []
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/embed",
                data=json.dumps({"model": emb_model, "input": clean}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            vecs = data.get("embeddings") or []
            if vecs and isinstance(vecs[0], list):
                return [float(v) for v in vecs[0]]
        except Exception:
            pass
        return []

    def clipboard_read(self) -> tuple[bool, str]:
        for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
            if shutil.which(cmd[0]):
                try:
                    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=3, creationflags=_WIN_NO_WINDOW)
                    if pr.returncode == 0:
                        return True, pr.stdout
                except Exception:
                    pass
        if sys.platform == "darwin" and shutil.which("pbpaste"):
            pr = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3)
            return pr.returncode == 0, pr.stdout
        return False, "No clipboard utility found (wl-paste/xclip/xsel)."

    def clipboard_write(self, text: str) -> tuple[bool, str]:
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]):
                try:
                    pr = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=3, creationflags=_WIN_NO_WINDOW)
                    if pr.returncode == 0:
                        return True, f"Copied {len(text)} chars to clipboard via {cmd[0]}."
                except Exception:
                    pass
        if sys.platform == "darwin" and shutil.which("pbcopy"):
            pr = subprocess.run(["pbcopy"], input=text, text=True, capture_output=True, timeout=3)
            return pr.returncode == 0, f"Copied {len(text)} chars to clipboard."
        return False, "No clipboard utility found (wl-copy/xclip/xsel)."

    def capture_display_and_ocr(self) -> dict[str, Any]:
        """Captures current screen/display and extracts readable text via OCR + active window title."""
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        out_img = self.captures_dir / "latest_screen.png"
        captured = False
        method = ""
        for cmd in (
            ["grim", str(out_img)],
            ["spectacle", "-b", "-n", "-o", str(out_img)],
            ["scrot", "-o", str(out_img)],
            ["import", "-window", "root", str(out_img)],
        ):
            if shutil.which(cmd[0]):
                try:
                    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=8, creationflags=_WIN_NO_WINDOW)
                    if pr.returncode == 0 and out_img.exists():
                        captured = True
                        method = cmd[0]
                        break
                except Exception:
                    pass
        if not captured:
            try:
                from PIL import ImageGrab

                im = ImageGrab.grab()
                im.save(out_img)
                captured = out_img.exists()
                method = "PIL.ImageGrab"
            except Exception:
                pass

        ocr_text = ""
        if captured and shutil.which("tesseract"):
            try:
                pr = subprocess.run(
                    ["tesseract", str(out_img), "stdout", "--psm", "6"],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    creationflags=_WIN_NO_WINDOW,
                )
                if pr.returncode == 0:
                    ocr_text = pr.stdout.strip()[:7500]
            except Exception:
                pass

        active_title = ""
        for cmd in (["xdotool", "getactivewindow", "getwindowname"], ["kdotool", "getactivewindow", "getwindowname"]):
            if shutil.which(cmd[0]):
                try:
                    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=3, creationflags=_WIN_NO_WINDOW)
                    if pr.returncode == 0 and pr.stdout.strip():
                        active_title = pr.stdout.strip()[:200]
                        break
                except Exception:
                    pass

        ok_clip, clip_txt = self.clipboard_read()
        return {
            "ok": bool(captured or ocr_text or ok_clip),
            "captured": captured,
            "capture_method": method,
            "screenshot_path": (str(out_img) if captured else ""),
            "active_window": active_title,
            "ocr_text": ocr_text,
            "clipboard_preview": (clip_txt[:1500] if ok_clip else ""),
            "note": ("" if ocr_text else ("Captured screenshot (attach it in chat for vision models, or install tesseract for direct OCR)." if captured else "Screen capture tool (grim/spectacle/scrot) not available in headless session.")),
        }

    def speak_text_offline(self, text: str) -> tuple[bool, str]:
        clean = (text or "").strip()[:1200]
        if not clean:
            return False, "Nothing to speak."
        for cmd in (["spd-say", clean], ["espeak-ng", clean], ["say", clean]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_WIN_NO_WINDOW)
                    return True, f"Speaking via {cmd[0]}."
                except Exception:
                    pass
        return False, "No offline TTS engine found (install espeak-ng, speech-dispatcher, or piper)."

    def listen_voice_offline(self, seconds: int = 6) -> tuple[bool, str]:
        """Records short microphone clip and transcribes offline if whisper/arecord are installed."""
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        wav = self.captures_dir / "voice_input.wav"
        rec_cmd = None
        if shutil.which("pw-record"):
            rec_cmd = ["pw-record", str(wav)]
        elif shutil.which("arecord"):
            rec_cmd = ["arecord", "-d", str(max(2, min(15, seconds))), "-f", "cd", "-q", str(wav)]
        if rec_cmd is None:
            return False, "No audio recorder found (install pipewire/pw-record or alsa-utils/arecord)."
        try:
            proc = subprocess.Popen(rec_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_WIN_NO_WINDOW)
            time.sleep(max(2, min(12, seconds)))
            proc.terminate()
            proc.wait(timeout=2)
        except Exception as exc:
            return False, f"Voice capture failed: {exc}"
        if shutil.which("whisper-cli"):
            pr = subprocess.run(["whisper-cli", "-nt", "-f", str(wav)], capture_output=True, text=True, timeout=25)
            return pr.returncode == 0, (pr.stdout or "").strip()
        if shutil.which("whisper"):
            pr = subprocess.run(["whisper", str(wav), "--output_format", "txt", "--output_dir", str(self.captures_dir)], capture_output=True, text=True, timeout=45)
            txt_p = wav.with_suffix(".txt")
            if txt_p.exists():
                return True, txt_p.read_text(encoding="utf-8", errors="ignore").strip()
        return False, "Audio recorded, but offline whisper/whisper-cli binary is not installed."

    @staticmethod
    def _select_tool_schemas(prompt: str, all_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Picks a lean, intent-matched subset of tools so local models respond faster and more accurately."""
        low = (prompt or "").strip().lower()
        selected_names: set[str] = {
            "astra_system_status",
            "memory_search",
            "astra_knowledge_library",
            "astra_custom_tool_forge",
            "astra_sop_templates",
            "astra_verify_work",
        }

        if any(w in low for w in ("scan", "fix", "autofix", "heal", "clean", "ram", "cpu", "gpu", "thermal", "temp", "fan", "probalance", "process", "kill", "startup", "autorun", "rollback", "undo", "dependency", "dependencies", "audio", "pipewire", "display", "usb", "stutter", "escalate")):
            selected_names.update({
                "astra_full_os_autofix", "astra_deep_scan", "astra_fix_safe", "astra_process_lookup", "astra_process_manage",
                "astra_probalance_and_profile", "astra_startup_audit", "astra_self_heal_repair",
                "astra_rollback_undo", "astra_dependencies", "astra_pc_usage_summary",
                "astra_thermals_and_fans", "astra_av_and_peripherals",
            })
        if any(w in low for w in ("wifi", "wi-fi", "network", "lan", "subnet", "dns", "internet", "ping", "remote", "phone", "iphone", "android", "watch", "tailscale", "adb", "astrabeam", "sunshine", "tv", "samsung", "lg", "roku", "chromecast", "sinkhole", "astrashield", "adblock", "youtube", "aim", "devices")):
            selected_names.update({"astra_wifi_check", "astra_wifi_optimize", "astra_lan_and_sinkhole", "astra_remote_tasks", "astra_services_and_storage"})
        if any(w in low for w in ("crash", "journal", "audit", "error", "warning", "fail", "service", "systemd", "port", "firewall", "disk", "smart", "ssd", "nvme", "storage", "diagnostic", "upgrade", "pacman", "apt", "firmware")):
            selected_names.update({"astra_journal_and_crashes", "astra_services_and_storage", "astra_deep_scan", "astra_self_heal_repair", "astra_system_updates"})
        if any(w in low for w in ("screen", "display", "page", "webpage", "web page", "clipboard", "copy", "paste", "ocr", "voice", "speak", "mic", "dictate", "picture", "image", "photo", "screenshot", "video", "clip", "mp4", "mkv", "mov", "webm", "audio", "mp3", "wav", "media", "pdf", "docx", "xlsx", "pptx")):
            selected_names.update({"astra_screen_and_clipboard", "astra_voice_io", "astra_inspect_media", "web_fetch"})
        if re.search(r"\b(?:astra|astracradle)\b", low) or any(w in low for w in ("code", "file", "edit", "write", "read", "grep", "search", "changes", "improve", "review", "feature", "build", "test", "update", "patch", "script", "python", "shell", "command", "terminal", "sudo", "git", "github", "push", "pull", "backup", "tool", "macro", "combine", "automate", "workflow", "template", "playbook", "sop")):
            selected_names.update({
                "file_read", "file_write", "file_edit", "list_dir", "grep_search",
                "astra_dev_self_build", "astra_git_sync", "astra_journal_and_crashes",
                "astra_pc_usage_summary", "shell_exec", "sudo_exec", "astra_rollback_undo",
            })
        if any(w in low for w in ("learned", "usage", "apps", "games", "summary", "summarize", "web", "online", "docs", "research", "url", "skill", "memory", "remember", "cron", "schedule", "subagent", "sub-agent")):
            selected_names.update({
                "astra_pc_usage_summary", "web_search", "web_fetch", "memory_read", "memory_save", "memory_search",
                "skill_save", "skill_install_from_url", "cron_schedule", "delegate_subagent",
            })

        # If prompt looked action-oriented without hitting a narrow group, provide the core OS + screen + memory trio
        if len(selected_names) <= 6:
            selected_names.update({
                "astra_deep_scan", "astra_pc_usage_summary", "astra_journal_and_crashes",
                "astra_services_and_storage", "astra_screen_and_clipboard", "astra_fix_safe",
                "memory_read", "web_search",
            })

        # Also include up to 8 enabled custom tools so the AI can call its own forged/combined tools directly
        custom_schemas = [t for t in all_tools if str((t.get("function") or {}).get("name") or "").startswith("custom_")][:8]
        filtered = [t for t in all_tools if (t.get("function") or {}).get("name") in selected_names]
        for ct in custom_schemas:
            if ct not in filtered:
                filtered.append(ct)
        return filtered or all_tools

    def _record_hermes_rollback(self, action_id: str, title: str, summary: str, revert_cmds: list[str] | None = None) -> str:
        if self.rollback_mgr is None:
            return ""
        try:
            from .models import RollbackEntry

            eid = f"hermes-{uuid.uuid4().hex[:8]}"
            self.rollback_mgr.record(
                RollbackEntry(
                    entry_id=eid,
                    timestamp=utc_now_iso(),
                    action_id=action_id,
                    title=title[:120],
                    dry_run=False,
                    status="applied",
                    summary=summary[:240],
                    revert_commands=list(revert_cmds or []),
                )
            )
            return eid
        except Exception:
            return ""

    def _request_mutation_approval(self, title: str, detail: str, config: AppConfig) -> tuple[bool, str]:
        if not bool(getattr(config, "hermes_require_approval", True)):
            return True, ""
        if self.approval_prompt_cb is None:
            return True, ""
        try:
            approved = bool(self.approval_prompt_cb(title, detail))
            if not approved:
                return False, f"User declined approval for: {title}"
            return True, ""
        except Exception:
            return True, ""

    _IMAGE_ATTACHMENT_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".ico", ".svg", ".heic")
    _VIDEO_ATTACHMENT_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv")
    _AUDIO_ATTACHMENT_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac")
    _OFFICE_ATTACHMENT_EXTS = (".docx", ".xlsx", ".pptx")

    @staticmethod
    def _image_file_to_png_b64(p: Path) -> str:
        import base64
        import io

        ext = p.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            return base64.b64encode(p.read_bytes()).decode("ascii")
        try:
            from PIL import Image

            with Image.open(p) as im:
                rgb = im.convert("RGB")
                buf = io.BytesIO()
                rgb.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            if shutil.which("ffmpeg"):
                try:
                    tmp_png = p.with_suffix(".converted.png")
                    pr = subprocess.run(
                        ["ffmpeg", "-y", "-i", str(p), "-vframes", "1", str(tmp_png)],
                        capture_output=True,
                        timeout=8,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    if pr.returncode == 0 and tmp_png.exists():
                        b64 = base64.b64encode(tmp_png.read_bytes()).decode("ascii")
                        tmp_png.unlink(missing_ok=True)
                        return b64
                except Exception:
                    pass
        return base64.b64encode(p.read_bytes()).decode("ascii")

    @staticmethod
    def _extract_office_text(p: Path) -> str:
        import xml.etree.ElementTree as ET
        import zipfile

        ext = p.suffix.lower()
        parts: list[str] = []
        def _xml_text(raw_bytes: bytes, *, only_t_tag: bool = True) -> str:
            try:
                root = ET.fromstring(raw_bytes)
                if only_t_tag:
                    return " ".join(
                        t.text for t in root.iter()
                        if (t.tag.endswith("}t") or t.tag == "t" or t.tag.endswith(":t")) and t.text
                    )
                return " ".join(t.text for t in root.iter() if t.text and t.text.strip())
            except Exception:
                raw_s = raw_bytes.decode("utf-8", errors="ignore")
                return " ".join(m.strip() for m in re.findall(r">([^<]+)<", raw_s) if m.strip())

        try:
            with zipfile.ZipFile(p, "r") as zf:
                names = zf.namelist()
                if ext == ".docx" and "word/document.xml" in names:
                    parts.append(_xml_text(zf.read("word/document.xml"), only_t_tag=True))
                elif ext == ".xlsx":
                    for target in sorted(n for n in names if n.startswith("xl/sharedStrings") or n.startswith("xl/worksheets/sheet"))[:6]:
                        txt = _xml_text(zf.read(target), only_t_tag=False)
                        if txt:
                            parts.append(f"[{Path(target).name}] {txt[:1500]}")
                elif ext == ".pptx":
                    for target in sorted(n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml"))[:10]:
                        txt = _xml_text(zf.read(target), only_t_tag=True)
                        if txt:
                            parts.append(f"[{Path(target).stem}] {txt[:1000]}")
        except Exception:
            return ""
        return "\n".join(parts).strip()[:6000]

    def _extract_video_frames_and_info(self, p: Path) -> tuple[list[str], list[str], str]:
        """Extracts up to 4 evenly spaced PNG keyframes + ffprobe metadata + optional OCR/audio transcript for videos."""
        import base64

        frames_b64: list[str] = []
        frame_paths: list[str] = []
        meta_lines: list[str] = []
        duration_sec = 0.0

        if shutil.which("ffprobe"):
            try:
                pr = subprocess.run(
                    [
                        "ffprobe", "-v", "quiet", "-print_format", "json",
                        "-show_format", "-show_streams", str(p),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    creationflags=_WIN_NO_WINDOW,
                )
                if pr.returncode == 0 and pr.stdout.strip():
                    info = json.loads(pr.stdout)
                    fmt = info.get("format") or {}
                    duration_sec = float(fmt.get("duration") or 0.0)
                    streams = info.get("streams") or []
                    vstreams = [s for s in streams if s.get("codec_type") == "video"]
                    astreams = [s for s in streams if s.get("codec_type") == "audio"]
                    if vstreams:
                        vs = vstreams[0]
                        meta_lines.append(
                            f"Video stream: {vs.get('codec_name', '?')} {vs.get('width', '?')}x{vs.get('height', '?')} @ {vs.get('r_frame_rate', '?')} fps, duration={duration_sec:.1f}s"
                        )
                    if astreams:
                        as0 = astreams[0]
                        meta_lines.append(f"Audio stream: {as0.get('codec_name', '?')} {as0.get('sample_rate', '?')} Hz")
            except Exception:
                pass

        if shutil.which("ffmpeg"):
            out_dir = self.captures_dir / "media_frames"
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", p.stem)[:32] or "video"
            ts_marks = (
                [max(0.0, duration_sec * frac) for frac in (0.1, 0.35, 0.65, 0.88)]
                if duration_sec >= 2.0
                else [0.0, 0.5]
            )
            for idx, tsec in enumerate(ts_marks[:4]):
                out_png = out_dir / f"{stem}_f{idx + 1}.png"
                try:
                    pr = subprocess.run(
                        [
                            "ffmpeg", "-y", "-ss", f"{tsec:.2f}", "-i", str(p),
                            "-vframes", "1", "-vf", "scale='min(1280,iw)':-1", str(out_png),
                        ],
                        capture_output=True,
                        timeout=10,
                        creationflags=_WIN_NO_WINDOW,
                    )
                    if pr.returncode == 0 and out_png.exists() and out_png.stat().st_size > 0:
                        frames_b64.append(base64.b64encode(out_png.read_bytes()).decode("ascii"))
                        frame_paths.append(str(out_png))
                except Exception:
                    continue

        if frame_paths and shutil.which("tesseract"):
            ocr_Snips: list[str] = []
            for fp in frame_paths[:2]:
                try:
                    o = subprocess.run(["tesseract", fp, "stdout", "--psm", "6"], capture_output=True, text=True, timeout=6, creationflags=_WIN_NO_WINDOW)
                    if o.returncode == 0 and o.stdout.strip():
                        ocr_Snips.append(o.stdout.strip()[:700])
                except Exception:
                    pass
            if ocr_Snips:
                meta_lines.append("OCR from video frames:\n" + "\n---\n".join(ocr_Snips))

        if not frames_b64:
            meta_lines.append("(ffmpeg not installed or video unreadable for frame extraction; install `ffmpeg` for visual video frame inspection.)")
        else:
            meta_lines.append(f"Extracted {len(frames_b64)} visual keyframe(s) for multimodal vision inspection.")

        return frames_b64, frame_paths, "\n".join(meta_lines)

    def _extract_audio_info(self, p: Path) -> str:
        lines: list[str] = []
        if shutil.which("ffprobe"):
            try:
                pr = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(p)],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    creationflags=_WIN_NO_WINDOW,
                )
                if pr.returncode == 0 and pr.stdout.strip():
                    info = json.loads(pr.stdout)
                    fmt = info.get("format") or {}
                    dur = float(fmt.get("duration") or 0.0)
                    lines.append(f"Audio: format={fmt.get('format_name', '?')}, duration={dur:.1f}s, bitrate={fmt.get('bit_rate', '?')}")
            except Exception:
                pass
        if shutil.which("whisper"):
            try:
                out_dir = self.captures_dir / "media_frames"
                out_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["whisper", str(p), "--model", "tiny", "--output_format", "txt", "--output_dir", str(out_dir)],
                    capture_output=True,
                    text=True,
                    timeout=35,
                    creationflags=_WIN_NO_WINDOW,
                )
                txt_p = out_dir / f"{p.stem}.txt"
                if txt_p.exists():
                    lines.append("Transcript:\n" + txt_p.read_text(encoding="utf-8", errors="ignore")[:3000])
            except Exception:
                pass
        return "\n".join(lines) or "Audio clip attached (install ffmpeg/whisper for duration & speech transcription)."

    def inspect_media_file(self, path_str: str) -> dict[str, Any]:
        """Inspects any local picture, video, audio, PDF, or Office file and extracts frames/metadata/text."""
        p = Path(str(path_str or "").strip()).expanduser().resolve()
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": f"Media file not found: {p}"}
        imgs, ctx_txt, names = self.prepare_attachments([str(p)])
        ext = p.suffix.lower()
        out_frames: list[str] = []
        if ext in self._IMAGE_ATTACHMENT_EXTS:
            out_frames = [str(p)]
        elif ext in self._VIDEO_ATTACHMENT_EXTS:
            frame_dir = self.captures_dir / "media_frames"
            stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", p.stem)[:32] or "video"
            out_frames = [str(x) for x in sorted(frame_dir.glob(f"{stem}_f*.png"))[:4]] if frame_dir.exists() else []
        return {
            "ok": True,
            "file": str(p),
            "name": p.name,
            "media_kind": (
                "image" if ext in self._IMAGE_ATTACHMENT_EXTS else
                "video" if ext in self._VIDEO_ATTACHMENT_EXTS else
                "audio" if ext in self._AUDIO_ATTACHMENT_EXTS else
                "office" if ext in self._OFFICE_ATTACHMENT_EXTS else
                "document"
            ),
            "vision_frames_count": len(imgs),
            "image_paths": out_frames,
            "summary": ctx_txt[:4000],
        }

    @staticmethod
    def _encode_image_attachments(attachments: list[str] | None) -> list[str]:
        encoded: list[str] = []
        for raw_p in (attachments or [])[:6]:
            try:
                p = Path(str(raw_p)).expanduser().resolve()
                if not p.exists() or not p.is_file():
                    continue
                if p.suffix.lower() not in HermesAgentManager._IMAGE_ATTACHMENT_EXTS:
                    continue
                if p.stat().st_size > 12 * 1024 * 1024:
                    continue
                encoded.append(HermesAgentManager._image_file_to_png_b64(p))
            except Exception:
                continue
        return encoded

    def prepare_attachments(self, attachments: list[str] | None) -> tuple[list[str], str, list[str]]:
        """Processes images (including SVG/TIFF/BMP/GIF), videos (keyframe images + metadata), audio, PDFs, Office docs, and code/log files."""
        import hashlib

        encoded_imgs: list[str] = []
        ctx_blocks: list[str] = []
        names: list[str] = []

        for raw_p in (attachments or [])[:8]:
            try:
                p = Path(str(raw_p)).expanduser().resolve()
                if not p.exists() or not p.is_file():
                    continue
                names.append(p.name)
                sz = p.stat().st_size
                ext = p.suffix.lower()

                if ext in self._IMAGE_ATTACHMENT_EXTS:
                    if sz <= 12 * 1024 * 1024:
                        encoded_imgs.append(self._image_file_to_png_b64(p))
                    ocr_txt = ""
                    if shutil.which("tesseract"):
                        try:
                            o = subprocess.run(
                                ["tesseract", str(p), "stdout", "--psm", "6"],
                                capture_output=True,
                                text=True,
                                timeout=8,
                                creationflags=_WIN_NO_WINDOW,
                            )
                            if o.returncode == 0 and o.stdout.strip():
                                ocr_txt = o.stdout.strip()[:2500]
                        except Exception:
                            pass
                    meta = f"[Attached image: {p.name} ({sz} bytes, path={p})]"
                    if ocr_txt:
                        meta += f"\nOCR extracted visible text:\n{ocr_txt}"
                    ctx_blocks.append(meta)
                    continue

                if ext in self._VIDEO_ATTACHMENT_EXTS:
                    v_frames, _v_paths, v_meta = self._extract_video_frames_and_info(p)
                    for vb in v_frames[:4]:
                        if len(encoded_imgs) < 6:
                            encoded_imgs.append(vb)
                    ctx_blocks.append(f"[Attached video: {p.name} ({sz} bytes, path={p})]\n{v_meta}")
                    continue

                if ext in self._AUDIO_ATTACHMENT_EXTS:
                    a_meta = self._extract_audio_info(p)
                    ctx_blocks.append(f"[Attached audio: {p.name} ({sz} bytes, path={p})]\n{a_meta}")
                    continue

                if ext in self._OFFICE_ATTACHMENT_EXTS:
                    o_txt = self._extract_office_text(p)
                    ctx_blocks.append(
                        f"[Attached Office doc: {p.name} ({sz} bytes, path={p})]\n"
                        + (o_txt or "(Could not extract XML text from Office archive.)")
                    )
                    continue

                if ext == ".pdf":
                    pdf_txt = ""
                    if shutil.which("pdftotext"):
                        try:
                            pr = subprocess.run(
                                ["pdftotext", str(p), "-"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            )
                            if pr.returncode == 0 and pr.stdout.strip():
                                pdf_txt = pr.stdout.strip()[:6000]
                        except Exception:
                            pass
                    if not pdf_txt:
                        try:
                            import pypdf  # type: ignore

                            reader = pypdf.PdfReader(str(p))
                            pdf_txt = "\n".join((pg.extract_text() or "") for pg in reader.pages[:12]).strip()[:6000]
                        except Exception:
                            pass
                    ctx_blocks.append(
                        f"[Attached PDF: {p.name} ({sz} bytes, path={p})]\n"
                        + (pdf_txt or "(Could not extract text; inspect file path with tools if needed.)")
                    )
                    continue

                # Read text/code/log/config/data files cleanly
                raw_bytes = p.read_bytes()[:64000]
                is_binary = b"\x00" in raw_bytes[:2048]
                if not is_binary:
                    txt = raw_bytes.decode("utf-8", errors="replace")
                    if len(txt) > 6000:
                        txt = txt[:5800] + "\n... [truncated; use file tools to read full file]"
                    ctx_blocks.append(f"[Attached file: {p.name} ({sz} bytes, path={p})]\n```\n{txt}\n```")
                else:
                    digest = hashlib.sha256(raw_bytes[:65536]).hexdigest()[:16]
                    ctx_blocks.append(
                        f"[Attached binary file: {p.name} ({sz} bytes, sha256_prefix={digest}, path={p})]"
                    )
            except Exception:
                continue

        return encoded_imgs, "\n\n".join(ctx_blocks), names

    def record_hermes_diagnostic(
        self,
        event: str,
        severity: str,
        detail: str,
        remediation: str = "",
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Logs Hermes runtime/GPU/OS diagnostics cleanly without polluting user chat bubbles."""
        try:
            self.hermes_home.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": utc_now_iso(),
                "event": event,
                "severity": severity,
                "detail": detail,
                "remediation": remediation,
                "metadata": metadata or {},
            }
            lines: list[str] = []
            if self.diagnostics_log_path.exists():
                lines = self.diagnostics_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-199:]
            lines.append(json.dumps(entry))
            self.diagnostics_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass
        try:
            self.journal.record(
                actor="hermes_agent",
                action=event,
                what=detail[:240],
                how="Hermes silent diagnostic & auto-recovery",
                why=remediation[:240] or "Keep AI & OS running smoothly",
                outcome=("warning" if severity in ("warning", "error") else "ok"),
                metadata=metadata or {},
            )
        except Exception:
            pass

    def get_recent_diagnostics(self, limit: int = 25) -> list[dict[str, Any]]:
        if not self.diagnostics_log_path.exists():
            return []
        out: list[dict[str, Any]] = []
        for ln in self.diagnostics_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
            try:
                d = json.loads(ln)
                if isinstance(d, dict):
                    out.append(d)
            except Exception:
                continue
        return list(reversed(out))

    @staticmethod
    def detect_rocm_multi_gpu_conflict() -> dict[str, Any]:
        """Detects Linux AMD multi-GPU (iGPU + discrete Radeon) where Ollama lacks ROCR_VISIBLE_DEVICES=0."""
        if sys.platform != "linux":
            return {"conflict": False, "reason": "Not Linux"}
        try:
            drm_dir = Path("/sys/class/drm")
            amd_cards: list[str] = []
            if drm_dir.exists():
                for card in sorted(drm_dir.glob("card[0-9]*")):
                    if "-" in card.name:
                        continue
                    vfile = card / "device" / "vendor"
                    if vfile.exists() and vfile.read_text(encoding="utf-8", errors="ignore").strip().lower() == "0x1002":
                        amd_cards.append(card.name)
            if len(amd_cards) < 2:
                return {"conflict": False, "amd_cards": amd_cards}
            dropin_dir = Path("/etc/systemd/system/ollama.service.d")
            has_rocr = False
            if dropin_dir.exists():
                for conf in dropin_dir.glob("*.conf"):
                    txt = conf.read_text(encoding="utf-8", errors="ignore")
                    if "ROCR_VISIBLE_DEVICES" in txt:
                        has_rocr = True
                        break
            return {
                "conflict": (not has_rocr),
                "amd_cards": amd_cards,
                "dropin_configured": has_rocr,
                "fix_cmd": (
                    "mkdir -p /etc/systemd/system/ollama.service.d && "
                    "printf '[Service]\\nEnvironment=\"ROCR_VISIBLE_DEVICES=0\"\\n' > /etc/systemd/system/ollama.service.d/rocm-gpu.conf && "
                    "systemctl daemon-reload && systemctl restart ollama"
                ),
            }
        except Exception as exc:
            return {"conflict": False, "error": str(exc)}

    def fix_ollama_rocm_systemd(self, config: AppConfig) -> dict[str, Any]:
        """Applies ROCR_VISIBLE_DEVICES=0 to Ollama's systemd unit (using Level 4 sudo prompt if enabled)."""
        info = self.detect_rocm_multi_gpu_conflict()
        if not info.get("conflict"):
            return {"ok": True, "already_configured": True, "message": "Ollama ROCm multi-GPU override is not needed or already configured."}
        perm = self._effective_permission_level(config)
        if perm != "sudo_with_prompt":
            return {
                "ok": False,
                "needs_sudo_level": True,
                "command": info["fix_cmd"],
                "message": "Switch Hermes Permissions to Level 4 (Sudo When Password Typed) or run the command once in Terminal.",
            }
        res = self._run_sudo_prompted(
            info["fix_cmd"],
            "Pin Ollama ROCm to discrete GPU 0 (ROCR_VISIBLE_DEVICES=0) to stop AMD multi-GPU crashes",
            config,
        )
        if res.get("ok"):
            self._clear_runtime_model_health()
            self.record_hermes_diagnostic(
                "rocm_systemd_fixed",
                "info",
                "Applied ROCR_VISIBLE_DEVICES=0 systemd override and restarted Ollama.",
            )
        return res

    def get_autonomous_guardian_status(self) -> dict[str, Any]:
        if not self.autonomous_status_path.exists():
            return {"last_run": "Pending first cycle", "actions": [], "health": "Ready", "rocm_conflict": False}
        try:
            data = json.loads(self.autonomous_status_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {"last_run": "Unknown", "actions": [], "health": "Ready"}

    def run_autonomous_passive_cycle(
        self,
        config: AppConfig,
        *,
        snapshot=None,
        gpu=None,
        is_gaming: bool = False,
        min_interval_sec: float = 90.0,
    ) -> dict[str, Any]:
        """Passively protects, heals, optimizes, and learns from the whole PC in background without disturbing the user."""
        if not bool(getattr(config, "hermes_autonomous_guardian", True)):
            return {"ran": False, "reason": "disabled"}
        now = time.monotonic()
        with self._autonomous_lock:
            if (now - self._last_autonomous_cycle_ts) < float(min_interval_sec):
                return {"ran": False, "reason": "throttled"}
            self._last_autonomous_cycle_ts = now

        actions_taken: list[str] = []
        warnings: list[str] = []
        perm = self._effective_permission_level(config)

        try:
            self.hermes_home.mkdir(parents=True, exist_ok=True)
            # 1. Thermal & RAM Passive Protection (respecting active games)
            ram_pct = float(getattr(snapshot, "mem_percent", 0.0) or 0.0)
            gpu_temp = float(getattr(gpu, "temperature_c", 0.0) or 0.0)
            if gpu_temp >= 85.0:
                warnings.append(f"High GPU temperature ({gpu_temp:.0f}C)")
                self.record_hermes_diagnostic(
                    "thermal_guard_alert",
                    "warning",
                    f"GPU temperature reached {gpu_temp:.0f}C during autonomous monitoring.",
                    "Ensure airflow/fan curve is active or lower GPU load.",
                )
            if not is_gaming and ram_pct >= 86.0 and self.ram_standby_mgr is not None and perm != "read_only":
                try:
                    trim_res = self.ram_standby_mgr.trim_standby_memory(dry_run=False)
                    if getattr(trim_res, "ok", False):
                        actions_taken.append(f"Trimmed RAM standby lists at {ram_pct:.0f}% RAM")
                except Exception:
                    pass

            # 2. Passive Self-Heal Invariant Sweep (auto-repair safe temp/schema items)
            adapter = get_os_adapter()
            if not is_gaming and self.self_heal is not None and perm != "read_only":
                try:
                    ev_list, _aud = self.self_heal.run_self_check(adapter, config)
                    repaired = [e.check_name for e in ev_list if getattr(e, "repaired", False)]
                    if repaired:
                        actions_taken.append(f"Self-Heal auto-repaired {len(repaired)} item(s): {', '.join(repaired[:2])}")
                except Exception:
                    pass

            # 2b. ProBalance Background CPU Hog Restraint (safe user-space renice when not gaming)
            if not is_gaming and self.probalance is not None and perm != "read_only" and getattr(config, "probalance_enabled", False):
                try:
                    procs = list(getattr(snapshot, "top_processes", []) or [])
                    if procs:
                        pb_notes = self.probalance.evaluate_tick(procs, "", config, dry_run=False)
                        if pb_notes:
                            actions_taken.append(pb_notes[0][:120])
                except Exception:
                    pass

            # 2c. Disk Safety Floor Guard (reversible temp/cache auto-trim via SafeBeginnerFixer)
            if not is_gaming and self.safe_fixer is not None and perm != "read_only":
                try:
                    du = psutil.disk_usage(str(self.cfg_mgr.base_dir))
                    free_pct = round((du.free / max(1, du.total)) * 100.0, 1)
                    floor_pct = float(getattr(config, "disk_safety_floor_percent", 12.0) or 12.0)
                    if free_pct < floor_pct:
                        ok_sf, msg_sf, _ = self.safe_fixer.run_fix_everything_safe(adapter, config, dry_run=False)
                        if ok_sf:
                            actions_taken.append(f"Low disk ({free_pct}% free) -> {msg_sf[:100]}")
                except Exception:
                    pass

            # 2d. Passive DNS Latency & Wi-Fi Congestion Guard
            if not is_gaming and self.net_dns_inspector is not None:
                try:
                    ping_ms = self.net_dns_inspector.measure_dns_ping_ms()
                    if ping_ms is not None and ping_ms >= 220.0:
                        warnings.append(f"Elevated DNS/WAN latency ({ping_ms:.0f} ms)")
                except Exception:
                    pass
            if (
                not is_gaming
                and self.wifi_optimizer is not None
                and getattr(config, "wifi_auto_optimize", True)
                and perm in ("standard_user", "sudo_with_prompt")
            ):
                try:
                    w_res = self.wifi_optimizer.scan_wifi_environment()
                    w_rec = self.wifi_optimizer.recommend_best_channel(w_res)
                    if w_rec and w_rec.get("should_switch") and w_rec.get("best_channel"):
                        ok_w, msg_w = self.wifi_optimizer.apply_channel_recommendation(
                            w_rec["best_channel"],
                            dry_run=False,
                        )
                        if ok_w:
                            actions_taken.append(f"Wi-Fi auto-optimized: {msg_w[:90]}")
                except Exception:
                    pass

            # 2e. Native Scheduled Cron Execution (paused automatically during gaming)
            if not is_gaming and getattr(config, "hermes_cron_enabled", False):
                try:
                    cron_ran = self.run_due_cron_jobs(config)
                    if cron_ran:
                        actions_taken.append(f"Executed {len(cron_ran)} scheduled cron task(s)")
                except Exception:
                    pass

            # 2f. Startup & Privacy Hygiene Watchdog (detect newly added user autoruns)
            if not is_gaming and self.autoruns_auditor is not None:
                try:
                    cur_entries = self.autoruns_auditor.scan_autoruns(adapter, include_system=False)
                    cur_ids = sorted(e.entry_id for e in cur_entries if e.enabled)
                    if self.startup_baseline_path.exists():
                        prev_ids = set(json.loads(self.startup_baseline_path.read_text(encoding="utf-8")) or [])
                        new_ids = [eid for eid in cur_ids if eid not in prev_ids]
                        if new_ids:
                            warnings.append(f"New startup item(s) detected: {', '.join(new_ids[:3])}")
                            self.record_hermes_diagnostic(
                                "new_startup_detected",
                                "warning",
                                f"New enabled startup autorun(s): {', '.join(new_ids[:5])}",
                                "Review in Hermes or Autoruns Audit to disable unwanted updaters.",
                            )
                    self.startup_baseline_path.write_text(json.dumps(cur_ids, indent=2), encoding="utf-8")
                except Exception:
                    pass

            # 2g. Anomaly & Crash Root-Cause Auto-Resolver
            if not is_gaming:
                try:
                    aud_items = self.journal.self_audit(limit=250)
                    err_titles = [a.title for a in aud_items if a.severity == "error"]
                    if err_titles:
                        self.record_hermes_diagnostic(
                            "recurring_anomaly_digest",
                            "warning",
                            "; ".join(err_titles[:4]),
                            "Ask Hermes 'inspect journal and crashes' for root-cause remediation.",
                        )
                except Exception:
                    pass

            # 3. AMD Multi-GPU ROCm Conflict Detection (silent diagnostic log for quick fix)
            rocm = self.detect_rocm_multi_gpu_conflict()
            rocm_conflict = bool(rocm.get("conflict"))
            if rocm_conflict:
                warnings.append("AMD multi-GPU ROCm conflict detected (ROCR_VISIBLE_DEVICES=0 recommended)")

            # 4. Dependency & Fast Companion Model Readiness
            if not is_gaming:
                try:
                    dep_res = self.check_and_install_missing_dependencies(
                        config,
                        install=bool(getattr(config, "consent_auto_install_deps", False)) and perm != "read_only",
                    )
                    if dep_res.get("installed_python"):
                        actions_taken.append(f"Installed missing Python pkg(s): {', '.join(dep_res['installed_python'])}")
                    if dep_res.get("missing_fast_model") and getattr(config, "hermes_dynamic_model_routing", True):
                        self.ensure_fast_companion_model_async(config)
                except Exception:
                    pass

            # 6. Skill & Knowledge Library Auto-Prune + Periodic Web Reference Prefetch
            try:
                if bool(getattr(config, "hermes_library_auto_prune", True)):
                    prune_res = self.prune_library(config, aggressive=False)
                    if prune_res.get("removed", 0) > 0:
                        actions_taken.append(f"Pruned {prune_res['removed']} stale/incorrect library item(s)")
                now_mono = time.monotonic()
                if (
                    not is_gaming
                    and bool(getattr(config, "hermes_allow_web_search", True))
                    and bool(getattr(config, "hermes_library_auto_prefetch", True))
                    and (now_mono - self._last_library_prefetch_ts) >= 3600.0
                ):
                    self._last_library_prefetch_ts = now_mono
                    pf = self.prefetch_library_topics(config)
                    if pf.get("stored", 0) > 0:
                        actions_taken.append(f"Refreshed {pf['stored']} Knowledge Library web reference(s)")
            except Exception:
                pass

            prev = self.get_autonomous_guardian_status()
            hist = list(prev.get("recent_actions") or [])
            for a in actions_taken:
                hist.insert(0, f"[{utc_now_iso()[:19].replace('T', ' ')}] {a}")
            status_obj = {
                "last_run": utc_now_iso(),
                "is_gaming": bool(is_gaming),
                "health": ("Needs Attention" if warnings else "Protected & Healthy"),
                "warnings": warnings,
                "actions_this_cycle": actions_taken,
                "recent_actions": hist[:25],
                "rocm_conflict": rocm_conflict,
                "permission_level": perm,
            }
            self.autonomous_status_path.write_text(json.dumps(status_obj, indent=2), encoding="utf-8")
            if actions_taken:
                self.record_hermes_diagnostic(
                    "autonomous_passive_cycle",
                    "info",
                    "; ".join(actions_taken),
                )
            return {"ran": True, **status_obj}
        except Exception as exc:
            return {"ran": False, "error": str(exc)}

    def check_and_install_missing_dependencies(self, config: AppConfig, *, install: bool = False) -> dict[str, Any]:
        """Checks missing Python packages & companion OS tools (and Ollama fast companion model) and optionally installs them."""
        import importlib.util

        py_reqs = [
            ("psutil", "psutil"),
            ("customtkinter", "customtkinter"),
            ("PIL", "pillow"),
            ("pystray", "pystray"),
            ("cryptography", "cryptography"),
        ]
        missing_py = [pip_name for mod_name, pip_name in py_reqs if importlib.util.find_spec(mod_name) is None]

        dep_lib = self.dep_lib
        if dep_lib is None:
            try:
                from .dependency_library import DependencyLibraryManager

                dep_lib = DependencyLibraryManager(self.cfg_mgr, self.journal)
            except Exception:
                dep_lib = None

        missing_sys: list[dict[str, str]] = []
        if dep_lib is not None:
            for st in dep_lib.inspect_all():
                if not st.installed:
                    missing_sys.append(
                        {
                            "dep_id": st.dep_id,
                            "name": st.name,
                            "required_for": st.required_for,
                            "recommended_cmd": st.recommended_cmd,
                        }
                    )

        fast_missing = ""
        if getattr(config, "hermes_mode", "") == "local_gpu" or getattr(config, "hermes_local_only", False):
            ok_o, pulled = self.find_all_local_ollama_models()
            if ok_o and not any(self._model_tag_matches(c, pulled) for c in self._FAST_LOCAL_CANDIDATES):
                fast_missing = "qwen3:8b"

        if not install:
            return {
                "ok": True,
                "missing_python": missing_py,
                "missing_companion": missing_sys,
                "missing_fast_model": fast_missing,
                "all_ready": (not missing_py and not missing_sys and not fast_missing),
            }

        installed_py: list[str] = []
        failed_py: list[str] = []
        if missing_py:
            pr = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", *missing_py],
                capture_output=True,
                text=True,
                timeout=180,
                creationflags=_WIN_NO_WINDOW,
            )
            if pr.returncode == 0:
                installed_py = list(missing_py)
            else:
                failed_py = list(missing_py)

        sys_outcomes: list[dict[str, Any]] = []
        if dep_lib is not None and missing_sys:
            outs = dep_lib.install_all_missing(permitted=True, dry_run=False)
            sys_outcomes = [{"dep_id": o.dep_id, "ok": o.ok, "message": o.message} for o in outs]

        fast_pull_res: dict[str, Any] = {}
        if fast_missing:
            fast_pull_res = self.ensure_fast_companion_model_async(config)

        return {
            "ok": not failed_py,
            "installed_python": installed_py,
            "failed_python": failed_py,
            "companion_outcomes": sys_outcomes,
            "fast_model_pull": fast_pull_res,
        }

    @staticmethod
    def _prompt_needs_tools(prompt: str) -> bool:
        """Returns True when the user is asking for live system/file/web/remote action rather than pure chat."""
        low = (prompt or "").strip().lower()
        if len(low) <= 28 and not any(w in low for w in ("scan", "fix", "check", "wifi", "wi-fi", "gpu", "ram", "cpu", "file", "search", "open", "kill", "run", "test", "install", "update", "remote", "tv", "dns", "lan", "sinkhole", "network")):
            return False
        action_hints = (
            "scan", "fix", "check", "diagnose", "optimize", "wifi", "wi-fi", "startup",
            "probalance", "process", "kill", "restart", "gpu", "vram", "ram", "cpu",
            "temp", "disk", "file", "edit", "write", "read", "code", "patch", "test",
            "self-check", "install", "dependency", "dependencies", "search", "web",
            "browse", "fetch", "skill", "memory", "remember", "cron", "remote",
            "device", "devices", "tailscale", "bluetooth", "astrabeam", "astracradle", "astra",
            "tv", "samsung", "lg", "roku", "chromecast", "sinkhole", "astrashield", "adblock", "ad-block", "lan", "subnet", "network", "youtube", "dns", "aim",
            "changes", "improve", "review", "feature", "learned", "usage", "apps",
            "games", "tool", "custom", "status", "audit", "summary", "summarize",
            "library", "topic", "research", "storage", "harvest", "autofix", "subagent", "sub-agent", "escalate",
            "picture", "image", "photo", "video", "audio", "media", "mp4", "mkv", "mp3", "wav", "docx", "xlsx", "pptx",
            "shell", "command", "terminal", "sudo", "update", "build", "error", "crash", "log",
        )
        return any(tok in low for tok in action_hints)

    def _build_compact_system_prompt(self, config: AppConfig) -> str:
        """Lightweight prompt for instant simple chat responses without 4KB prefill lag."""
        self.ensure_hermes_home(config)
        persona = self.get_active_persona(config)
        return (
            f"You are {persona['name']} ({persona['badge']}), AstraCradle's integrated PC AI assistant.\n"
            f"Traits: {persona['traits']}.\n"
            "Never invent fake percentages, endpoints, files, or metrics. Answer clearly, concisely, and helpfully in plain readable text without markdown code fences."
        )

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
        manifest = self.build_internal_memory_manifest()
        topics_txt = ", ".join(manifest.get("topics") or [])[:260] or "none yet"
        cats_txt = ", ".join(manifest.get("categories") or [])[:180] or "general"

        return (
            f"You are {persona['name']} ({persona['badge']}), AstraCradle's PC Action & Self-Development AI.\n"
            f"Traits: {persona['traits']}\n"
            f"Mannerisms: {persona['mannerisms']}\n"
            f"{persona['prompt_directive']}\n"
            f"Learned Whole-PC & Remote Usage Context: Source={src_root} | WebSearch={'ON' if web_ok else 'OFF'} | Safety={perm_lbl} | "
            f"TopApps={apps_txt} | Games={games_txt} | Skills={skill_summary} | CustomTools={ct_summary}\n"
            f"Internal Deep Memory Index: KnownTopics=[{topics_txt}] | Categories=[{cats_txt}]\n"
            f"User Notes: {user_txt}\nMemory: {mem}\n"
            "Action & Self-Development Assistant Guidelines: ALWAYS check your own internal Knowledge Library, MEMORY.md, Skills, and local files FIRST (`astra_knowledge_library(action='deep_recall')` / `memory_search`) before going to `web_search`, especially when a prompt relates to your KnownTopics. "
            "If the user references a picture, video, audio clip, PDF, or Office document, inspect it with `astra_inspect_media` (or attached multimodal frames). "
            "Answer the user's exact request directly—if they ask for improvements, tests, or skills, propose concrete file-specific additions instead of saying no action is needed. "
            "You can create, combine, and improve your own reusable custom tools via `astra_custom_tool_forge` whenever a recurring multi-step workflow would make the user's life easier. "
            "Ground EVERY file path, module name, metric, and error in real tool output from this turn; NEVER invent fictional modules, packages, endpoints, percentages, or bridges (such as non-existent bridges). "
            "CRITICAL: Tools like `astra_fix_safe`, `astra_process_manage`, `astra_startup_audit`, `astra_services_and_storage`, `astra_dev_self_build`, and `astra_knowledge_library` are internal AI function tools—NEVER tell the user to run `astra_*` commands or flags in bash/terminal. Offer to run them directly or describe the plain UI action. "
            "Respect protected OS processes/files, verify code changes, and answer clearly without raw markdown clutter or triple-backtick code fences unless showing real shell/Python code."
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
            if not cleaned and msg.get("thinking"):
                th = str(msg.get("thinking") or "").strip()
                # If a reasoning trace leaked without final content, extract the last drafted paragraph rather than dumping the checklist
                m_draft = re.search(
                    r"(?:Drafting the Response[^:]*:|Final Answer[^:]*:|Response:)\s*(.+)",
                    th,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                cleaned = m_draft.group(1).strip() if m_draft else th
            content = (
                cleaned
                or raw_content
                or str(msg.get("reasoning") or "").strip()
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

        # Stage 0: Requested GPU budget options (or lean fast budget for instant quick chat)
        is_fast_tier = spec.get("dynamic_tier") == "fast"
        base_ctx = int(gpu_plan.get("num_ctx") or spec.get("context_tokens", 32768))
        if is_fast_tier:
            base_ctx = min(2048, base_ctx)
        opts_primary: dict[str, Any] = {"num_ctx": base_ctx}
        if is_fast_tier:
            opts_primary["num_predict"] = 768
        if gpu_plan.get("num_batch"):
            opts_primary["num_batch"] = int(gpu_plan["num_batch"])
        if gpu_plan.get("num_gpu_layers") is not None:
            opts_primary["num_gpu"] = int(gpu_plan["num_gpu_layers"])

        native_msgs = _to_native_ollama_messages(messages)
        if spec["model"] in self._gpu_fallback_models:
            # Skip already-known crashing GPU stages for this model so replies don't stall waiting for a segfault
            attempts: list[tuple[str, dict[str, Any], bool]] = [
                ("cpu_safe", {"num_gpu": 0, "num_ctx": min(4096, base_ctx)}, True),
                ("cpu_notools", {"num_gpu": 0, "num_ctx": min(4096, base_ctx)}, False),
            ]
        else:
            attempts = [
                ("primary", opts_primary, True),
                # Stage 1: Drop partial num_gpu/num_batch splits (common ROCm/MoE segfault trigger) + lean 8K ctx
                ("gpu_safe", {"num_ctx": min(8192, base_ctx)}, True),
                # Stage 2: Pure CPU/RAM safe fallback (num_gpu=0) if GPU/ROCm runner itself segfaults
                ("cpu_safe", {"num_gpu": 0, "num_ctx": min(4096, base_ctx)}, True),
                # Stage 3: Pure CPU/RAM fallback without tool schema payload if Jinja tool template crashed runner
                ("cpu_notools", {"num_gpu": 0, "num_ctx": min(4096, base_ctx)}, False),
            ]

        last_http_exc: urllib.error.HTTPError | None = None
        for idx, (stage_name, stage_opts, include_tools) in enumerate(attempts):
            try_payload: dict[str, Any] = {
                "model": spec["model"],
                "messages": native_msgs,
                "stream": bool(stream_cb is not None),
                "options": stage_opts,
            }
            if "think_mode" in spec:
                try_payload["think"] = bool(spec["think_mode"])
            if include_tools and tools:
                try_payload["tools"] = tools
            if gpu_plan.get("keep_alive"):
                try_payload["keep_alive"] = str(gpu_plan["keep_alive"])
            try:
                content, t_calls = _post_chat_ollama_native(try_payload)
                if idx > 0 or stage_name.startswith("cpu"):
                    if stage_name.startswith("cpu"):
                        self._save_gpu_fallback_model(spec["model"], stage_name)
                    self.record_hermes_diagnostic(
                        "ollama_gpu_runner_fallback",
                        "warning",
                        f"Ollama model '{spec['model']}' recovered on '{stage_name}' stage.",
                        "If using AMD Ryzen iGPU + Radeon dGPU, apply ROCR_VISIBLE_DEVICES=0 in Ollama systemd override.",
                        metadata={"model": spec["model"], "stage": stage_name},
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
                # Older Ollama builds may reject top-level "think" with HTTP 400 — retry cleanly without it
                if exc.code == 400 and "think" in low_err and "think" in try_payload:
                    try_payload.pop("think", None)
                    try:
                        return _post_chat_ollama_native(try_payload)
                    except urllib.error.HTTPError as exc2:
                        exc = exc2
                        last_http_exc = exc2
                        try:
                            err_txt = exc2.read().decode("utf-8", errors="ignore")
                        except Exception:
                            err_txt = str(exc2)
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
        use_compact = False
        if spec["provider"] == "ollama":
            route = self.select_dynamic_ollama_model(
                clean_prompt,
                config,
                history=history,
                has_images=bool(encoded_imgs),
            )
            spec["model"] = route["model"]
            spec["think_mode"] = route["think"]
            spec["dynamic_tier"] = route["tier"]
            if llm_caller is None and route.get("missing_fast_recommendation"):
                self.ensure_fast_companion_model_async(config)
            if route["tier"] == "fast" and not self._prompt_needs_tools(clean_prompt) and not attachments:
                use_compact = True
        # Compact older history if long so context fits comfortably
        sys_prompt = self._build_compact_system_prompt(config) if use_compact else self._build_system_prompt(config)
        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
        if len(history) > 14:
            older_summary = " | ".join(f"{m['role']}: {m['content'][:90]}" for m in history[:-10])
            messages.append({"role": "system", "content": f"Earlier session summary: {older_summary[:1400]}"})
            recent = history[-10:]
        else:
            recent = history
        for m in recent:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": m["content"]})

        # Attach base64 images AND extracted file/OCR context on the final user message
        if encoded_imgs or attachment_context:
            for m in reversed(messages):
                if m.get("role") == "user":
                    if encoded_imgs:
                        m["images"] = encoded_imgs
                    if attachment_context:
                        m["content"] = f"{m['content']}\n\n{attachment_context}"
                    break

        # Deep Internal Memory Recall (check own library/memory/skills FIRST before external web searches)
        if not use_compact:
            try:
                recall = self.deep_internal_recall(clean_prompt, limit=3)
                blocks_int: list[str] = []
                for h in (recall.get("library_hits") or []):
                    blocks_int.append(f"- [Library:{h.get('category', '')}] {h['title']}: {h.get('summary', '')[:220]}")
                for m in (recall.get("memory_hits") or []):
                    blocks_int.append(f"- [Memory:{m.get('source', '')}] {m.get('ref', '')}: {m.get('snippet', '')[:180]}")
                if blocks_int:
                    messages.insert(
                        1,
                        {
                            "role": "system",
                            "content": (
                                "Deep Internal Memory Recall (Consult these internal files FIRST before calling web_search):\n"
                                + "\n".join(blocks_int)[:1100]
                            ),
                        },
                    )
            except Exception:
                pass
            try:
                sop_hits = self.match_sop_templates(clean_prompt, limit=2)
                if sop_hits:
                    sop_blocks = []
                    for sop in sop_hits:
                        rules_s = " | ".join(sop.get("decision_rules") or [])[:300]
                        tools_s = ", ".join(s.get("tool", "") for s in (sop.get("tool_pipeline") or []))
                        sop_blocks.append(
                            f"[{sop['template_id']}] {sop['title']}\n"
                            f"Decision Rules: {rules_s}\n"
                            f"Pipeline Tools: {tools_s}\n"
                            f"Fill-In Template:\n{sop.get('output_template', '')[:420]}"
                        )
                    messages.insert(
                        1,
                        {
                            "role": "system",
                            "content": (
                                "Matched Standard Operating Book Template(s) — Run the pipeline tools first, obey Decision Rules, "
                                "and plug real tool results into this template structure:\n\n" + "\n---\n".join(sop_blocks)[:1500]
                            ),
                        },
                    )
            except Exception:
                pass

        tools = [] if use_compact else self._select_tool_schemas(clean_prompt, self._tool_schemas())
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
                # Grounding safety net: if live LLM didn't emit tool_calls on an AstraCradle/system/usage prompt,
                # run the real inspection tool server-side and synthesize from genuine output rather than hallucinating.
                if (
                    not tool_calls
                    and _turn == 0
                    and not tool_events
                    and llm_caller is None
                    and tools
                ):
                    auto_calls: list[dict[str, Any]] = []
                    low_p = clean_prompt.lower()
                    if "astra_dev_self_build" in low_p or re.search(r"\b(?:astra|astracradle)\b", low_p) or any(k in low_p for k in ("codebase", "architecture")):
                        auto_calls.append({"id": "auto_inspect", "function": {"name": "astra_dev_self_build", "arguments": {"action": "inspect"}}})
                        auto_calls.append({"id": "auto_git", "function": {"name": "astra_git_sync", "arguments": {"action": "status"}}})
                    if "astra_process_lookup" in low_p or ("ollama" in low_p and "process" in low_p):
                        auto_calls.append({"id": "auto_proc", "function": {"name": "astra_process_lookup", "arguments": {"query": "ollama"}}})
                    if "astra_startup_audit" in low_p:
                        auto_calls.append({"id": "auto_startup", "function": {"name": "astra_startup_audit", "arguments": {}}})
                    if "astra_journal_and_crashes" in low_p or "diagnostics" in low_p or "ui log" in low_p:
                        auto_calls.append({"id": "auto_diag", "function": {"name": "astra_journal_and_crashes", "arguments": {"limit": 10}}})
                    if "astra_system_status" in low_p or "astra_deep_scan" in low_p:
                        auto_calls.append({"id": "auto_scan", "function": {"name": "astra_deep_scan", "arguments": {}}})
                    if any(k in low_p for k in ("learned", "pc usage", "top apps", "remote task")):
                        auto_calls.append({"id": "auto_usage", "function": {"name": "astra_pc_usage_summary", "arguments": {}}})
                    if auto_calls:
                        tool_calls = auto_calls
                        content = ""
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
                    # If astra_inspect_media extracted visual frames (image or video keyframes), inject them for vision models
                    if tname == "astra_inspect_media" and result.get("ok") and result.get("image_paths"):
                        media_imgs = self._encode_image_attachments(list(result.get("image_paths") or [])[:4])
                        if media_imgs:
                            messages.append(
                                {
                                    "role": "user",
                                    "content": f"[Visual frames from {result.get('name', 'media')} — inspect directly]",
                                    "images": media_imgs,
                                }
                            )
            else:
                final_reply = "Completed tool workflow."

            # Post-reply self-verification guardrail: catch dangerous mid-rebase git commit advice or fake CLI flags
            try:
                audit_res = self.verify_work(action="audit_draft", draft_text=final_reply, session_id=session_id, config=config)
                if not audit_res.get("verified") and audit_res.get("warnings"):
                    final_reply = (
                        final_reply.rstrip()
                        + "\n\nSelf-Verification Guardrail:\n- "
                        + "\n- ".join(audit_res["warnings"])
                    )
            except Exception:
                pass

            # Hermes Fast Closed Learning Loop: crystallize SOP template after 2+ successful tool calls,
            # and full SKILL.md + reusable composite custom tool after 3+ successful tool calls
            ok_steps_all = [
                {"tool": ev["tool"], "args": ev["args"], "continue_on_error": True}
                for ev in tool_events
                if ev.get("ok") and not str(ev.get("tool", "")).startswith(("custom_", "astra_custom_tool_forge", "astra_sop_templates", "sudo_exec", "shell_exec"))
            ]
            if len(ok_steps_all) >= 2 and self._effective_permission_level(config) != "read_only":
                slug_sop = re.sub(r"[^a-z0-9]+", "_", clean_prompt.lower())[:30].strip("_") or "workflow"
                try:
                    kw_list = [w for w in re.findall(r"[a-z0-9_]{3,}", clean_prompt.lower()) if w not in ("the", "and", "for", "with", "that", "this")][:6]
                    self.save_learned_sop_template(
                        template_id=f"sop_auto_{slug_sop}",
                        title=f"Auto-SOP: {clean_prompt[:70]}",
                        category="learned",
                        keywords=kw_list or ["auto", "workflow"],
                        decision_rules=["Follow verified tool outputs from this pipeline; confirm before destructive changes."],
                        tool_pipeline=ok_steps_all[:5],
                        output_template=f"Auto-Learned SOP ({clean_prompt[:60]}):\n" + "\n".join(f"- {s['tool']}: {{{{result_{i+1}}}}}" for i, s in enumerate(ok_steps_all[:5])),
                        config=config,
                    )
                except Exception:
                    pass

            if len(tool_events) >= 3 and not skill_created:
                slug = re.sub(r"[^a-z0-9]+", "-", clean_prompt.lower())[:32].strip("-") or "auto-workflow"
                steps_txt = "\n".join(f"{idx+1}. Call `{ev['tool']}` ({json.dumps(ev['args'])})" for idx, ev in enumerate(tool_events))
                sp = self.save_skill(
                    f"auto-{slug}",
                    f"Auto-learned Hermes workflow for: {clean_prompt[:90]}",
                    f"# Auto-Learned Procedure\n{steps_txt}\n",
                )
                skill_created = str(sp)
                if len(ok_steps_all) >= 3 and self._effective_permission_level(config) != "read_only":
                    try:
                        self.create_composite_custom_tool(
                            name=f"custom_auto_{slug.replace('-', '_')}",
                            description=f"Auto-combined workflow for: {clean_prompt[:110]}",
                            steps=ok_steps_all[:6],
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
