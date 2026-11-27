"""Modular feature registry, install bundle presets, and runtime module gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AstraModuleSpec:
    module_id: str
    name: str
    category: str
    summary: str
    nav_keys: tuple[str, ...]
    footprint_label: str
    locked: bool = False
    default_enabled: bool = True
    background_flags: tuple[str, ...] = ()


ASTRA_MODULES: tuple[AstraModuleSpec, ...] = (
    AstraModuleSpec(
        module_id="core_diagnostics",
        name="Core Diagnostics, Playbook & Self-Heal",
        category="Core (Required)",
        summary="Command Deck, 8-subsystem Diagnostics & Playbook, Bluetooth & Peripherals, 1-Click Safe Fix, Forensic Vault & Settings.",
        nav_keys=("dashboard", "peripherals", "playbook", "safeguards", "settings"),
        footprint_label="Essential · ~20 MB RAM",
        locked=True,
        default_enabled=True,
    ),
    AstraModuleSpec(
        module_id="live_monitoring",
        name="Live Monitoring & Hardware Sensors",
        category="Observability",
        summary="Real-time CPU/GPU/RAM charts, detailed thermal/voltage sensors, USB peripheral list & tool conflict detector.",
        nav_keys=("monitoring",),
        footprint_label="Light · Low polling",
        default_enabled=True,
    ),
    AstraModuleSpec(
        module_id="ai_hunter",
        name="AI & Idle Process Hunter",
        category="Performance",
        summary="Detects dormant local LLM/chatbot runtimes (Ollama, LM Studio) & idle helpers holding RAM.",
        nav_keys=("hunter",),
        footprint_label="Zero idle overhead",
        default_enabled=True,
    ),
    AstraModuleSpec(
        module_id="privacy_shield",
        name="AstraShield Ad-Block, Smart TV & Cleanup",
        category="Security & Privacy",
        summary="Pi-hole hosts/DNS sinkhole, Smart TV LAN failsafe, YouTube Safe Ad-Prune, BadUSB Gate & storage cleanup.",
        nav_keys=("modules",),
        footprint_label="Optional DNS sinkhole",
        default_enabled=True,
        background_flags=("astrashield_auto_start", "badusb_guard_enabled"),
    ),
    AstraModuleSpec(
        module_id="wifi_failover_mesh",
        name="Wi-Fi Optimizer, Failover & RF Waves",
        category="Networking",
        summary="Cleanest 2.4/5/6 GHz Wi-Fi channel optimizer, Ethernet <-> Wi-Fi ping failover, Mesh Assistant & RF spectrum chart.",
        nav_keys=("wifi", "rf_waves"),
        footprint_label="Periodic Wi-Fi check",
        default_enabled=True,
        background_flags=("wifi_auto_optimize", "failover_enabled"),
    ),
    AstraModuleSpec(
        module_id="remote_beam",
        name="AstraBeam Remote, iPhone App & Moonlight",
        category="Remote & Mobile",
        summary="Tailscale TLS remote streaming, iPhone 16 Pro Max (/iphone) & Apple Watch (/watch) companion apps, Sunshine/Moonlight & Bluetooth proximity.",
        nav_keys=("remote_beam",),
        footprint_label="On-demand TLS server",
        default_enabled=True,
    ),
    AstraModuleSpec(
        module_id="system_scanner",
        name="System Scanner, Known-Issues DB & Vault",
        category="Catalog & Tools",
        summary="Multi-drive file/program SQLite catalog, low-CPU passive watcher, quiet auto-repair & Install Command Vault.",
        nav_keys=("scanner", "cmd_vault"),
        footprint_label="Background cataloger",
        default_enabled=True,
        background_flags=("catalog_passive_scan",),
    ),
    AstraModuleSpec(
        module_id="pro_tuning",
        name="ProBalance, Workload Scripts & Hardware Lab",
        category="Power User",
        summary="Process Lasso-style ProBalance, DPC latency guard, fan curves, toggleable workload scripts, autoruns & ghost drivers.",
        nav_keys=("scripts", "hardware", "probalance", "autoruns"),
        footprint_label="Power-user suite",
        default_enabled=True,
        background_flags=("probalance_enabled", "app_auto_detect_enabled"),
    ),
    AstraModuleSpec(
        module_id="hermes_ai",
        name="Hermes OS AI Agent (Local GPU & Free Cloud)",
        category="Autonomous AI",
        summary="Native Nous Research Hermes AI agent with persistent memory, self-learned SKILL.md workflows, cron tasks, messaging gateways, auto-picked GPU model (Ollama) & free cloud APIs.",
        nav_keys=("hermes",),
        footprint_label="Zero idle overhead until enabled",
        default_enabled=True,
        background_flags=("hermes_cron_enabled", "hermes_gateway_auto_start"),
    ),
)

MODULE_BY_ID: dict[str, AstraModuleSpec] = {m.module_id: m for m in ASTRA_MODULES}
ALL_MODULE_IDS: tuple[str, ...] = tuple(m.module_id for m in ASTRA_MODULES)
DEFAULT_ENABLED_MODULE_IDS: tuple[str, ...] = tuple(m.module_id for m in ASTRA_MODULES if m.default_enabled)

NAV_KEY_TO_MODULE_ID: dict[str, str] = {
    nk: m.module_id for m in ASTRA_MODULES for nk in m.nav_keys
}

INSTALL_BUNDLE_PRESETS: dict[str, dict[str, Any]] = {
    "Full Suite (Everything)": {
        "short_key": "full",
        "tagline": "Every AstraCradle feature enabled — diagnostics, shields, networking, iPhone/Moonlight remote & pro tuning.",
        "modules": list(ALL_MODULE_IDS),
        "simple_mode": False,
    },
    "Essential Optimizer": {
        "short_key": "essential",
        "tagline": "Lean daily PC maintenance: Diagnostics, Live Sensors, AI/Process Hunter, Privacy Cleanup & Self-Heal.",
        "modules": ["core_diagnostics", "live_monitoring", "ai_hunter", "privacy_shield"],
        "simple_mode": True,
    },
    "Streamer & iPhone Remote": {
        "short_key": "streamer",
        "tagline": "Tailored for iPhone 16 Pro Max / Moonlight / Tailscale remote control with Wi-Fi & failover protection.",
        "modules": ["core_diagnostics", "live_monitoring", "wifi_failover_mesh", "remote_beam"],
        "simple_mode": True,
    },
    "Privacy & Network Shield": {
        "short_key": "privacy",
        "tagline": "System & Smart TV ad-block sinkhole, BadUSB gate, Wi-Fi channel optimizer, failover & RF spectrum.",
        "modules": ["core_diagnostics", "privacy_shield", "wifi_failover_mesh"],
        "simple_mode": True,
    },
    "Minimal Core Only": {
        "short_key": "minimal",
        "tagline": "Ultra-lightweight foundation: Command Deck, Playbook, Self-Heal & Settings with zero extra watchers.",
        "modules": ["core_diagnostics"],
        "simple_mode": True,
    },
}

PRESET_ALIAS_MAP: dict[str, str] = {
    spec["short_key"]: name for name, spec in INSTALL_BUNDLE_PRESETS.items()
}


def normalize_enabled_modules(selected: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    """Validates module IDs, ensures core_diagnostics is always active, and preserves canonical order."""
    if selected is None:
        return list(DEFAULT_ENABLED_MODULE_IDS)
    raw = {str(x).strip() for x in selected if str(x).strip()}
    if "all" in raw:
        return list(ALL_MODULE_IDS)
    out: list[str] = ["core_diagnostics"]
    for mod in ASTRA_MODULES:
        if mod.module_id == "core_diagnostics":
            continue
        if mod.module_id in raw:
            out.append(mod.module_id)
    return out


def resolve_preset_or_modules(token: str) -> tuple[list[str], str]:
    """Resolves a CLI preset alias (e.g. 'streamer') or comma-separated module list."""
    clean = (token or "").strip()
    if not clean:
        return list(DEFAULT_ENABLED_MODULE_IDS), "Full Suite (Everything)"
    low = clean.lower()
    if low in PRESET_ALIAS_MAP:
        pname = PRESET_ALIAS_MAP[low]
        return list(INSTALL_BUNDLE_PRESETS[pname]["modules"]), pname
    for pname, spec in INSTALL_BUNDLE_PRESETS.items():
        if pname.lower() == low:
            return list(spec["modules"]), pname
    parts = [p.strip() for p in clean.split(",") if p.strip()]
    mods = normalize_enabled_modules(parts)
    for pname, spec in INSTALL_BUNDLE_PRESETS.items():
        if set(spec["modules"]) == set(mods):
            return mods, pname
    return mods, "Custom"


def allowed_nav_keys(enabled_modules: list[str] | tuple[str, ...] | None) -> set[str]:
    mods = set(normalize_enabled_modules(enabled_modules))
    keys: set[str] = set()
    for mod in ASTRA_MODULES:
        if mod.locked or mod.module_id in mods:
            keys.update(mod.nav_keys)
    return keys
