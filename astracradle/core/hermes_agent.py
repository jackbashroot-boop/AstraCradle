"""Native Hermes OS AI Agent Engine, Hardware GPU Recommender, 1-Click Installer & AstraCradle Tool Bridge."""

from __future__ import annotations

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
import urllib.request

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

        self.hermes_home = cfg_mgr.base_dir / "hermes"
        self.env_path = self.hermes_home / ".env"
        self.config_yaml_path = self.hermes_home / "config.yaml"
        self.memory_path = self.hermes_home / "MEMORY.md"
        self.user_md_path = self.hermes_home / "USER.md"
        self.soul_md_path = self.hermes_home / "SOUL.md"
        self.skills_dir = self.hermes_home / "skills"
        self.sessions_db_path = self.hermes_home / "sessions.db"
        self.cron_path = self.hermes_home / "cron_jobs.json"
        self.learned_games_path = self.hermes_home / "learned_games.json"

        self._gateway_proc: subprocess.Popen | None = None
        self._ollama_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

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

        bridge_skill_dir = self.skills_dir / "astracradle-os-bridge"
        bridge_skill_dir.mkdir(parents=True, exist_ok=True)
        bridge_skill = bridge_skill_dir / "SKILL.md"
        if not bridge_skill.exists():
            bridge_skill.write_text(
                "---\n"
                "name: astracradle-os-bridge\n"
                "description: Use AstraCradle native OS tools to diagnose hardware/thermals, reclaim safe caches, check Wi-Fi channels, and explain running processes.\n"
                "---\n\n"
                "# AstraCradle OS Equilibrium Workflow\n"
                "1. Call `astra_system_status` first to get live CPU, RAM, disk, and GPU metrics.\n"
                "2. Call `astra_deep_scan` when the user asks why their computer is slow, hot, or cluttered.\n"
                "3. Only call `astra_fix_safe` with `dry_run=false` when the user asks to apply safe cleanup; otherwise preview with `dry_run=true`.\n"
                "4. Call `astra_process_lookup` to explain unfamiliar PIDs or processes in plain English.\n",
                encoding="utf-8",
            )

        self._init_sessions_db()
        if config is not None:
            self.write_config_yaml(config)
        return self.hermes_home

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
    ) -> dict[str, Any] | None:
        """Detects active games or heavy fullscreen GPU apps and updates learned_games.json with VRAM & GPU usage."""
        gpu_obj = gpu if gpu is not None else GpuTelemetryCollector.collect()
        gpu_util = _parse_float_safe(getattr(gpu_obj, "utilization_percent", None)) or 0.0
        vram_used = _parse_float_safe(getattr(gpu_obj, "vram_used_mb", None)) or 0.0
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
                "last_seen": utc_now_iso(),
            }
            data[d_key] = rec
            self._save_learned_games(data)
            return rec

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
        return [
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
                    "name": "file_read",
                    "description": "Read a local non-protected text file (max 6000 chars).",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_write",
                    "description": "Write text to a user-writable file outside protected OS directories.",
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
        ]

    def _effective_permission_level(self, config: AppConfig) -> str:
        lvl = str(getattr(config, "hermes_permission_level", "safe_user") or "safe_user")
        if lvl not in HERMES_PERMISSION_LEVELS:
            lvl = "safe_user"
        # Backward compat: if legacy hermes_allow_shell=True on safe_user, upgrade to standard_user
        if lvl == "safe_user" and bool(getattr(config, "hermes_allow_shell", False)):
            return "standard_user"
        return lvl

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
                    return {"ok": ok_f, "dry_run": dry, "summary": summary, "notes": notes[:8]}
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

            if name == "file_read":
                p = Path(str(args.get("path", ""))).expanduser()
                if not p.exists() or not p.is_file():
                    return {"ok": False, "error": f"File not found: {p}"}
                if p.name == ".env":
                    return {"ok": False, "error": "Refused reading .env credentials directly."}
                return {"ok": True, "path": str(p), "content": p.read_text(encoding="utf-8", errors="ignore")[:6000]}

            if name == "file_write":
                if perm == "read_only":
                    return {"ok": False, "blocked": True, "error": "Blocked by Read-Only mode (Level 1)."}
                p = Path(str(args.get("path", ""))).expanduser().resolve()
                if perm == "safe_user":
                    try:
                        p.relative_to(self.cfg_mgr.base_dir.resolve())
                    except ValueError:
                        return {
                            "ok": False,
                            "blocked": True,
                            "error": "Level 2 (Safe Tune-Ups Only) restricts file writes to ~/.astracradle. Choose Level 3 or 4 for user files.",
                        }
                if adapter.is_path_protected(p):
                    return {"ok": False, "error": f"Blocked write to protected OS path: {p}"}
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(args.get("content", "")), encoding="utf-8")
                self.journal.log_event("HermesOS", "file_write", f"Wrote {p}", "Hermes tool execution", "ok")
                return {"ok": True, "path": str(p)}

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

            return {"ok": False, "error": f"Unknown tool: {name}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _build_system_prompt(self, config: AppConfig) -> str:
        self.ensure_hermes_home(config)
        soul = self.soul_md_path.read_text(encoding="utf-8", errors="ignore")[:1800]
        user_txt = self.user_md_path.read_text(encoding="utf-8", errors="ignore")[:1500]
        mem = self.memory_path.read_text(encoding="utf-8", errors="ignore")[:2500]
        skills = self.list_skills()
        skill_summary = "\n".join(f"- {s['slug']}: {s['content'][:220].replace(chr(10), ' ')}" for s in skills[:8])
        return (
            f"{soul}\n\n{user_txt}\n\n{mem}\n\n"
            f"# Available Learned Skills\n{skill_summary or '(none yet)'}\n\n"
            "When solving multi-step requests, call the AstraCradle/Hermes tools accurately and save durable preferences with `memory_save`."
        )

    def _call_llm(
        self,
        spec: dict[str, Any],
        api_key: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Executes one LLM chat-completion turn via OpenAI-compatible or Anthropic REST API."""
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
        if spec["provider"] == "ollama":
            gpu_plan = spec.get("gpu_plan") or {}
            delay_s = float(gpu_plan.get("throttle_delay_sec", 0.0) or 0.0)
            if delay_s > 0.0:
                time.sleep(min(2.0, delay_s))
            opts: dict[str, Any] = {
                "num_ctx": int(gpu_plan.get("num_ctx") or spec.get("context_tokens", 32768)),
            }
            if gpu_plan.get("num_batch"):
                opts["num_batch"] = int(gpu_plan["num_batch"])
            if gpu_plan.get("num_gpu_layers") is not None:
                opts["num_gpu"] = int(gpu_plan["num_gpu_layers"])
            payload["options"] = opts
            if gpu_plan.get("keep_alive"):
                payload["keep_alive"] = str(gpu_plan["keep_alive"])

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = str(msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []
        return content, tool_calls

    def run_prompt(
        self,
        prompt: str,
        config: AppConfig,
        *,
        session_id: str = "default",
        allow_subagent: bool = True,
        max_turns: int = 6,
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

        self._append_session_message(session_id, "user", clean_prompt, title_hint=clean_prompt)

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
            for _turn in range(max_turns):
                content, tool_calls = caller(spec, api_key, messages, tools)
                if not tool_calls:
                    final_reply = content or "(No text returned)"
                    break

                messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    tname = str(fn.get("name") or "")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except Exception:
                        parsed_args = {}
                    result = self.execute_tool(tname, parsed_args, config, allow_subagent=allow_subagent)
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

            # Hermes Closed Learning Loop: auto-crystallize reusable skill after 3+ tool calls
            if len(tool_events) >= 3 and not skill_created:
                slug = re.sub(r"[^a-z0-9]+", "-", clean_prompt.lower())[:32].strip("-") or "auto-workflow"
                steps_txt = "\n".join(f"{idx+1}. Call `{ev['tool']}` ({json.dumps(ev['args'])})" for idx, ev in enumerate(tool_events))
                sp = self.save_skill(
                    f"auto-{slug}",
                    f"Auto-learned Hermes workflow for: {clean_prompt[:90]}",
                    f"# Auto-Learned Procedure\n{steps_txt}\n",
                )
                skill_created = str(sp)

            self._append_session_message(
                session_id,
                "assistant",
                final_reply,
                tool_trace=json.dumps(tool_events),
            )
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
