"""Safeguard Plus: Privacy Sweeper, Delayed Startup, Integrity Shield, Recovery Bundle, Process Translator & Weekly Digest."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import time
import zipfile

from .config import AppConfig, ConfigManager
from .journal import ForensicJournal
from .models import ProcessInfo, StartupItem, utc_now_iso
from .os_adapters import BaseOSAdapter
from .telemetry import format_bytes


@dataclass
class PrivacyTraceTarget:
    trace_id: str
    name: str
    path: str
    size_bytes: int
    protected_data_excluded: str


@dataclass
class IntegrityShieldFinding:
    path: str
    status: str  # baseline_created | ok | modified | missing
    sha256: str


@dataclass
class ProcessExplanation:
    name: str
    role: str
    essential: bool
    plain_english: str
    safe_advice: str


KNOWN_PROCESS_DICTIONARY: dict[str, tuple[str, bool, str, str]] = {
    "systemd": ("Core Linux System Manager", True, "Starts and supervises all Linux system services and hardware events.", "Never stop."),
    "init": ("Kernel Root Process (PID 1)", True, "First user-space process launched by the OS kernel.", "Never stop."),
    "kworker": ("Linux Kernel Worker Thread", True, "Handles low-level disk, USB, and driver work on behalf of the kernel.", "Normal kernel activity; cannot be terminated."),
    "gvfsd": ("GNOME Virtual Filesystem Daemon", False, "Handles desktop file-manager mounts (USB drives, network shares).", "Safe to ignore unless using excessive RAM."),
    "launchd": ("macOS System & App Service Manager", True, "Core macOS init daemon managing apps and background agents.", "Never stop."),
    "windowserver": ("macOS Display Compositor", True, "Draws every window, animation, and cursor on macOS.", "Never stop."),
    "kernel_task": ("macOS Kernel & Thermal Throttler", True, "Coordinates kernel memory and prevents CPU overheating.", "Never stop. High CPU indicates thermal load—check airflow."),
    "explorer.exe": ("Windows Desktop & File Shell", True, "Provides your Windows Start Menu, taskbar, and file explorer.", "Do not terminate (or restart gently only if UI freezes)."),
    "dwm.exe": ("Windows Desktop Window Manager", True, "Hardware-accelerated screen compositor for Windows.", "Never stop."),
    "svchost.exe": ("Windows Shared Service Host", True, "Hosts core Windows background services (audio, networking, updates).", "Leave running; use Services panel if a specific service loops."),
    "csrss.exe": ("Windows Client/Server Runtime", True, "Essential Windows console and subsystem process.", "Never stop."),
    "ollama": ("Local AI Model Runner (Ollama)", False, "Serves local LLMs in RAM/VRAM until stopped or unloaded.", "Safe to hibernate/stop when not actively chatting with local AI."),
    "ollama_llama_server": ("Ollama LLM Inference Worker", False, "Holds active neural network weights in memory.", "Safe to terminate when idle to reclaim gigabytes of RAM."),
    "lmstudio": ("LM Studio Local AI App", False, "Desktop runner for local GGUF language models.", "Safe to close when idle."),
    "python": ("Python Runtime", False, "Executes local Python tools, scripts, or AstraCradle itself.", "Check command line before stopping."),
    "python3": ("Python 3 Runtime", False, "Executes local Python tools, scripts, or AstraCradle itself.", "Check command line before stopping."),
    "chrome": ("Google Chrome Browser", False, "Web browser renderer/extension worker.", "Close unused tabs or restart if memory leaks occur."),
    "firefox": ("Mozilla Firefox Browser", False, "Web browser content process.", "Close heavy tabs if memory pressure rises."),
    "code": ("Visual Studio Code IDE", False, "Code editor and language-server host.", "Safe while coding; close when gaming/rendering."),
}


class ProcessTranslator:
    """Translates cryptic OS/background process names into plain-English guidance."""

    @staticmethod
    def explain(proc_name: str, is_protected: bool = False, is_ai: bool = False) -> ProcessExplanation:
        low = proc_name.strip().lower()
        for key, (role, ess, desc, advice) in KNOWN_PROCESS_DICTIONARY.items():
            if low == key or low.startswith(key):
                return ProcessExplanation(proc_name, role, ess or is_protected, desc, advice)
        if is_ai:
            return ProcessExplanation(
                proc_name,
                "Local AI / LLM Runtime",
                False,
                "Runs local AI/chatbot models in memory. Often stays loaded holding gigabytes of RAM even when idle.",
                "Safe to terminate or auto-hibernate when you are done using local AI.",
            )
        if is_protected:
            return ProcessExplanation(
                proc_name,
                "Protected OS / Core Process",
                True,
                "Marked on AstraCradle's Protected Process Allow-List to guarantee system stability.",
                "Protected: AstraCradle will never terminate or alter this process.",
            )
        return ProcessExplanation(
            proc_name,
            "User Application / Background Helper",
            False,
            "A standard user-space program or helper running on your computer.",
            "Lower priority (renice) if noisy, or close cleanly if no longer needed.",
        )


class PrivacyTraceSweeper:
    """Consent-gated privacy cache finder preserving bookmarks, passwords, and login cookies."""

    EXCLUDED_CREDENTIALS = ("login data", "cookies", "places.sqlite", "key4.db", "logins.json", "bookmarks")

    def scan(self, config: AppConfig, home: Path | None = None) -> list[PrivacyTraceTarget]:
        if not config.consent_privacy_scan:
            return []
        root = home or Path.home()
        candidates = [
            ("priv_thumbnails", "Desktop Thumbnail Cache", root / ".cache" / "thumbnails", "Zero passwords/cookies touched"),
            ("priv_chrome_cache", "Chromium Disk Cache (Cache only)", root / ".cache" / "google-chrome" / "Default" / "Cache", "Excludes Cookies & Login Data"),
            ("priv_chromium_cache", "Chromium OSS Cache", root / ".cache" / "chromium" / "Default" / "Cache", "Excludes Cookies & Login Data"),
            ("priv_mozilla_cache", "Firefox Cache2", root / ".cache" / "mozilla" / "firefox", "Excludes bookmarks, history & logins"),
            ("priv_win_recent", "Recent Shortcuts Cache", root / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Recent", "Only shortcut MRUs"),
        ]
        found: list[PrivacyTraceTarget] = []
        for tid, name, p, note in candidates:
            if p.exists() and not p.is_symlink():
                sz = self._safe_size(p)
                if sz > 0:
                    found.append(PrivacyTraceTarget(tid, name, str(p), sz, note))
        return found

    @classmethod
    def _safe_size(cls, path: Path, limit: int = 3000) -> int:
        total = 0
        cnt = 0
        try:
            for root, _, files in os.walk(path, followlinks=False):
                for fn in files:
                    if fn.lower() in cls.EXCLUDED_CREDENTIALS:
                        continue
                    fp = Path(root) / fn
                    if fp.is_symlink():
                        continue
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        pass
                    cnt += 1
                    if cnt >= limit:
                        return total
        except OSError:
            pass
        return total


class StartupDelayManager:
    """Applies a safe login boot-delay wrapper (+60s) to heavy non-essential startup entries."""

    @staticmethod
    def build_delayed_command(original_cmd: str, delay_seconds: int = 60, os_family: str | None = None) -> str:
        secs = max(10, min(300, int(delay_seconds)))
        sys_name = os_family or platform.system()
        if sys_name == "Windows":
            return f'cmd /c "timeout /t {secs} /nobreak >nul && start \"\" {original_cmd}"'
        return f'/bin/sh -c "sleep {secs} && exec {original_cmd}"'

    @staticmethod
    def apply_delay_to_item(item: StartupItem, delay_seconds: int = 60) -> tuple[bool, str]:
        if item.scope != "user":
            return False, "System-wide startup items require manual admin review."
        p = Path(item.location)
        if not p.exists():
            return False, f"Startup descriptor '{p}' not found."
        if item.os_family == "Linux" and p.suffix == ".desktop":
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
                new_lines = []
                updated = False
                for ln in lines:
                    if ln.startswith("Exec=") and "sleep " not in ln:
                        orig = ln.split("=", 1)[1].strip()
                        new_lines.append(f"Exec={StartupDelayManager.build_delayed_command(orig, delay_seconds, 'Linux')}")
                        updated = True
                    else:
                        new_lines.append(ln)
                if updated:
                    p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    return True, f"Delayed '{item.name}' by {delay_seconds}s on login."
                return True, f"'{item.name}' already has a login delay configured."
            except OSError as exc:
                return False, str(exc)
        return True, f"Prepared +{delay_seconds}s boot-delay command for '{item.name}'."


class ConfigIntegrityShield:
    """Monitors critical user/system persistence paths against unexpected silent changes."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.baseline_path = cfg_mgr.state_dir / "integrity_shield.json"

    @staticmethod
    def default_watch_paths() -> list[Path]:
        paths = [
            Path.home() / ".ssh" / "authorized_keys",
            Path("/etc/hosts") if platform.system() != "Windows" else Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "drivers" / "etc" / "hosts",
        ]
        return paths

    @staticmethod
    def _hash_target(p: Path) -> str:
        if not p.exists() or not p.is_file():
            return "MISSING"
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            return "UNREADABLE"

    def check_or_init(self, watch_paths: list[Path] | None = None) -> list[IntegrityShieldFinding]:
        targets = watch_paths or self.default_watch_paths()
        baseline: dict[str, str] = {}
        if self.baseline_path.exists():
            try:
                raw = json.loads(self.baseline_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    baseline = {str(k): str(v) for k, v in raw.items()}
            except Exception:
                baseline = {}

        findings: list[IntegrityShieldFinding] = []
        changed_baseline = False
        for p in targets:
            key = str(p)
            cur = self._hash_target(p)
            if key not in baseline:
                baseline[key] = cur
                changed_baseline = True
                findings.append(IntegrityShieldFinding(key, "baseline_created", cur))
            elif baseline[key] == cur:
                findings.append(IntegrityShieldFinding(key, "ok", cur))
            else:
                findings.append(IntegrityShieldFinding(key, "missing" if cur == "MISSING" else "modified", cur))
                self.journal.log_event(
                    component="IntegrityShield",
                    action="persistence_path_changed",
                    how=f"SHA-256 changed on monitored file '{key}' ({baseline[key][:10]} -> {cur[:10]})",
                    why="Alert user to unexpected changes in hosts or SSH/persistence files",
                    outcome="warning",
                )
        if changed_baseline:
            self.baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        return findings

    def accept_new_baseline(self, watch_paths: list[Path] | None = None) -> None:
        targets = watch_paths or self.default_watch_paths()
        data = {str(p): self._hash_target(p) for p in targets}
        self.baseline_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class RecoveryBundleExporter:
    """Exports a single portable diagnostic/forensic .zip bundle for support or post-mortem review."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal

    def export_zip(self, dest_path: Path | None = None) -> Path:
        self.cfg_mgr.ensure_directories()
        out = dest_path or (self.cfg_mgr.playbooks_dir / f"astracradle_recovery_{int(time.time())}.zip")
        out.parent.mkdir(parents=True, exist_ok=True)
        files = [
            self.cfg_mgr.config_path,
            self.cfg_mgr.rollback_path,
            self.cfg_mgr.self_heal_log_path,
            self.cfg_mgr.crash_history_path,
            self.cfg_mgr.forensic_log_path,
            self.cfg_mgr.heartbeat_path,
            self.cfg_mgr.scripts_path,
        ]
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                if f.exists():
                    zf.write(f, arcname=f"state/{f.name}")
            if self.cfg_mgr.snapshots_dir.exists():
                for sf in sorted(self.cfg_mgr.snapshots_dir.glob("*.json")):
                    zf.write(sf, arcname=f"snapshots/{sf.name}")
            meta = {
                "exported_at": utc_now_iso(),
                "os": platform.platform(),
                "python": platform.python_version(),
            }
            zf.writestr("bundle_info.json", json.dumps(meta, indent=2))
        self.journal.log_event(
            component="RecoveryBundle",
            action="export_zip",
            how=f"Exported portable forensic & state recovery zip -> {out}",
            why="Allow offline diagnostics or support sharing with zero personal data",
            outcome="ok",
        )
        return out


class WeeklyDigestGenerator:
    """Generates a self-contained offline HTML health digest with inline SVG visualization."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal

    def generate(
        self,
        health_score: int,
        reclaimable_bytes: int,
        crashes_count: int,
        entries_count: int,
        profile_name: str,
    ) -> Path:
        self.cfg_mgr.ensure_directories()
        out = self.cfg_mgr.playbooks_dir / f"weekly_digest_{int(time.time())}.html"
        pct = max(0, min(100, int(health_score)))
        bar_w = int(pct * 3.2)
        doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>AstraCradle Weekly Health Digest</title>
<style>
body{{font-family:system-ui,Segoe UI,sans-serif;background:#0e1217;color:#f1f5f9;margin:0;padding:32px}}
.card{{max-width:760px;margin:0 auto;background:#151b23;border:1px solid #26313f;border-radius:14px;padding:24px}}
h1{{color:#48d8e0;margin-top:0}} .meta{{color:#9fb0c3;font-size:14px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin:20px 0}}
.tile{{background:#1d2530;border-radius:10px;padding:14px;border:1px solid #364659}}
.val{{font-size:24px;font-weight:700;color:#2ec994}}
</style></head><body><div class="card">
<h1>AstraCradle — Local Weekly Health Digest</h1>
<p class="meta">Generated {html.escape(utc_now_iso())} · Profile: {html.escape(profile_name)} · 100% Offline Local Report</p>
<svg width="360" height="44" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="10" width="320" height="18" rx="9" fill="#10151c" stroke="#364659"/>
  <rect x="0" y="10" width="{bar_w}" height="18" rx="9" fill="#48d8e0"/>
  <text x="330" y="24" fill="#f1f5f9" font-size="14" font-weight="bold">{pct}%</text>
</svg>
<div class="grid">
  <div class="tile"><div>Equilibrium Score</div><div class="val">{pct}/100</div></div>
  <div class="tile"><div>Reclaimable Caches</div><div class="val">{html.escape(format_bytes(reclaimable_bytes))}</div></div>
  <div class="tile"><div>Forensic Entries Audited</div><div class="val">{entries_count}</div></div>
  <div class="tile"><div>Crash Post-Mortems</div><div class="val">{crashes_count}</div></div>
</div>
<p class="meta">All AstraCradle diagnostics, quarantine moves, and logs remain strictly local on this computer.</p>
</div></body></html>"""
        out.write_text(doc, encoding="utf-8")
        self.journal.log_event(
            component="WeeklyDigest",
            action="generate_digest",
            how=f"Generated offline HTML health digest -> {out}",
            why="Provide beginner-friendly local summary report",
            outcome="ok",
        )
        return out
