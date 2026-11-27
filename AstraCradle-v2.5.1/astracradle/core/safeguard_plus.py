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
from .models import StartupItem, utc_now_iso
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


class AIDiagnosticArchiveExporter:
    """Consolidates old logs/snapshots into tidy archives and builds a full AI-ready diagnostic report."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.archives_dir = cfg_mgr.base_dir / "archives"

    def consolidate_historical_data(self, keep_recent_snapshots: int = 10) -> tuple[int, Path | None]:
        """Archives older snapshots & rotated digests into a single compressed archive to avoid clutter."""
        self.cfg_mgr.ensure_directories()
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        to_archive: list[Path] = []
        if self.cfg_mgr.snapshots_dir.exists():
            snaps = sorted(self.cfg_mgr.snapshots_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            to_archive.extend(snaps[keep_recent_snapshots:])
        if self.cfg_mgr.playbooks_dir.exists():
            digests = sorted(self.cfg_mgr.playbooks_dir.glob("weekly_digest_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
            to_archive.extend(digests[5:])

        if not to_archive:
            return 0, None

        archive_zip = self.archives_dir / f"consolidated_history_{int(time.time())}.zip"
        archived_count = 0
        with zipfile.ZipFile(archive_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in to_archive:
                if item.exists():
                    zf.write(item, arcname=f"{item.parent.name}/{item.name}")
                    archived_count += 1
        for item in to_archive:
            try:
                item.unlink(missing_ok=True)
            except OSError:
                pass
        if archived_count:
            self.journal.log_event(
                "AIDiagnostics",
                "consolidate_archives",
                f"Consolidated {archived_count} older snapshot/digest file(s) into {archive_zip.name}",
                "Keep local AstraCradle data folder tidy while preserving full history",
                "ok",
            )
        return archived_count, archive_zip

    def export_ai_handoff_bundle(
        self,
        snapshot_data: dict | None = None,
        sensors_data: dict | None = None,
        gpu_data: dict | None = None,
        conflicts_data: list[dict] | None = None,
        shield_status: dict | None = None,
        crash_report: dict | None = None,
        self_audit: list[dict] | None = None,
        dest_dir: Path | None = None,
    ) -> dict[str, Path | int]:
        """Exports AI_HANDOFF_REPORT.md + ai_diagnostic_bundle.json + zip archive for any AI agent."""
        self.cfg_mgr.ensure_directories()
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        archived_count, consolidated_zip = self.consolidate_historical_data()

        out_dir = dest_dir or self.archives_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        ts_slug = int(time.time())
        md_path = out_dir / "AI_HANDOFF_REPORT.md"
        json_path = out_dir / "ai_diagnostic_bundle.json"
        zip_path = out_dir / f"astracradle_ai_diagnostics_{ts_slug}.zip"

        entries = self.journal.read_entries(limit=300, newest_first=True)
        error_entries = [
            {
                "timestamp": e.timestamp,
                "component": e.component,
                "action": e.action,
                "outcome": e.outcome,
                "how": e.how,
                "why": e.why,
            }
            for e in entries
            if e.outcome in ("error", "warn", "warning")
        ]

        # Detect irregular issues automatically so both human and AI see them first
        anomalies: list[dict[str, str]] = []
        temps = (sensors_data or {}).get("temperatures") or []
        for t in temps:
            if t.get("status") in ("warn", "crit"):
                anomalies.append(
                    {
                        "severity": str(t.get("status")).upper(),
                        "area": "Thermals",
                        "issue": f"{t.get('chip')}:{t.get('label')} at {t.get('current_c')}°C",
                        "fix_hint": "Check fan curve, dust buildup, or reduce sustained load.",
                    }
                )
        snap = snapshot_data or {}
        if float(snap.get("mem_percent") or 0.0) >= 88.0:
            anomalies.append(
                {
                    "severity": "WARN",
                    "area": "Memory",
                    "issue": f"High RAM usage ({snap.get('mem_percent')}%)",
                    "fix_hint": "Run Free Standby RAM or hibernate idle AI/browser processes.",
                }
            )
        if float(snap.get("swap_percent") or 0.0) >= 75.0:
            anomalies.append(
                {
                    "severity": "WARN",
                    "area": "Swap",
                    "issue": f"Heavy swap usage ({snap.get('swap_percent')}%)",
                    "fix_hint": "Close memory-heavy apps to stop disk thrashing.",
                }
            )
        for p_info in snap.get("partitions") or []:
            if float(p_info.get("percent") or 0.0) >= 90.0:
                anomalies.append(
                    {
                        "severity": "WARN",
                        "area": "Storage",
                        "issue": f"Mount {p_info.get('mountpoint')} is {p_info.get('percent')}% full",
                        "fix_hint": "Run AstraCradle Storage Sweep / Duplicate & Dev Vault cleaner.",
                    }
                )
        for cf in conflicts_data or []:
            anomalies.append(
                {
                    "severity": str(cf.get("severity") or "medium").upper(),
                    "area": "Conflict",
                    "issue": f"Overlapping tool running: {cf.get('name')} ({cf.get('reason')})",
                    "fix_hint": "Stop the overlapping daemon/program in AstraShield or Command Deck.",
                }
            )
        if crash_report and crash_report.get("dirty_shutdown"):
            anomalies.append(
                {
                    "severity": "CRIT",
                    "area": "CrashRecovery",
                    "issue": f"Previous session ended unexpectedly: {crash_report.get('summary')}",
                    "fix_hint": str(crash_report.get("fix_steps") or "Inspect crash_history.json and journal errors."),
                }
            )
        if error_entries:
            anomalies.append(
                {
                    "severity": "INFO",
                    "area": "ForensicJournal",
                    "issue": f"{len(error_entries)} warning/error event(s) logged in ForensicJournal",
                    "fix_hint": "Review recent error_events inside ai_diagnostic_bundle.json.",
                }
            )

        bundle = {
            "generated_at": utc_now_iso(),
            "host": {
                "os": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "anomalies_detected": anomalies,
            "snapshot": snap,
            "sensors": sensors_data or {},
            "gpu": gpu_data or {},
            "conflicting_programs": conflicts_data or [],
            "astrashield": shield_status or {},
            "crash_report": crash_report or {},
            "self_audit": self_audit or [],
            "error_and_warning_events": error_entries[:80],
            "recent_journal_events": [
                {
                    "timestamp": e.timestamp,
                    "component": e.component,
                    "action": e.action,
                    "outcome": e.outcome,
                    "how": e.how,
                    "why": e.why,
                }
                for e in entries[:120]
            ],
            "archived_old_files_count": archived_count,
        }
        json_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

        md_lines = [
            "# AstraCradle AI Diagnostic & Forensic Handoff Report",
            "",
            "> **Instructions for AI Agent (Arena / Claude / ChatGPT / Gemini / Grok):**",
            "> Inspect the anomalies, hardware temperatures, conflicting services, crash post-mortem, and forensic log events below (plus `ai_diagnostic_bundle.json`). Explain any irregular issues in plain English and give exact, safe step-by-step commands or AstraCradle actions to fix them.",
            "",
            f"- **Generated:** {bundle['generated_at']}",
            f"- **OS / Kernel:** {bundle['host']['os']} ({bundle['host']['machine']})",
            f"- **Python:** {bundle['host']['python']}",
            f"- **Historical files consolidated:** {archived_count}",
            "",
            "## 1. Detected Anomalies & Irregular Issues",
        ]
        if anomalies:
            for a in anomalies:
                md_lines.append(f"- **[{a['severity']}] ({a['area']})** {a['issue']} — *Suggested Fix:* {a['fix_hint']}")
        else:
            md_lines.append("- No critical anomalies or thermal/memory alerts detected.")

        md_lines.extend(["", "## 2. Temperatures, Fans & Hardware Summary"])
        if temps:
            for t in temps[:20]:
                md_lines.append(f"- `{t.get('chip')}:{t.get('label')}`: **{t.get('current_c')}°C** [{str(t.get('status')).upper()}]")
        else:
            md_lines.append("- No direct hardware temperature sensors exposed by OS.")
        fans = (sensors_data or {}).get("fans") or []
        for f in fans[:10]:
            md_lines.append(f"- Fan `{f.get('chip')}:{f.get('label')}`: **{f.get('rpm')} RPM**")
        if gpu_data:
            md_lines.append(
                f"- GPU: `{gpu_data.get('name', 'Unknown')}` · Temp `{gpu_data.get('temp_c', 'N/A')}°C` · "
                f"Power `{gpu_data.get('power_draw_w', 'N/A')}W` · Fan `{gpu_data.get('fan_percent', 'N/A')}%`"
            )

        md_lines.extend(["", "## 3. Conflicting Background Tools"])
        if conflicts_data:
            for c in conflicts_data:
                md_lines.append(f"- **{c.get('name')}** (PID `{c.get('pid')}`, unit `{c.get('service_unit')}`): {c.get('reason')}")
        else:
            md_lines.append("- Zero overlapping DNS/fan/optimizer tools detected.")

        md_lines.extend(["", "## 4. Recent Warnings / Errors from Forensic Journal"])
        if error_entries:
            for ev in error_entries[:25]:
                md_lines.append(f"- `{ev['timestamp']}` [{ev['component']}.{ev['action']}] ({ev['outcome']}): {ev['how']}")
        else:
            md_lines.append("- Zero errors or warnings in recent journal entries.")

        md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(md_path, arcname="AI_HANDOFF_REPORT.md")
            zf.write(json_path, arcname="ai_diagnostic_bundle.json")
            for f in (
                self.cfg_mgr.forensic_log_path,
                self.cfg_mgr.self_heal_log_path,
                self.cfg_mgr.crash_history_path,
                self.cfg_mgr.rollback_path,
                self.cfg_mgr.config_path,
            ):
                if f.exists():
                    zf.write(f, arcname=f"logs/{f.name}")

        self.journal.log_event(
            "AIDiagnostics",
            "export_ai_bundle",
            f"Exported AI diagnostic bundle ({len(anomalies)} anomaly flags) -> {zip_path.name}",
            "Provide a consolidated, AI-readable full log so any AI assistant can diagnose & help fix issues",
            "ok",
        )
        return {
            "markdown": md_path,
            "json": json_path,
            "zip": zip_path,
            "archived_count": archived_count,
            "anomaly_count": len(anomalies),
            "consolidated_zip": consolidated_zip or zip_path,
        }

    def export_forensic_crash_log(
        self,
        config_obj: AppConfig | None = None,
        self_heal_events: list | None = None,
        self_audit: list | None = None,
        crash_report: dict | None = None,
        shield_status: dict | None = None,
        tv_failsafe_state: dict | None = None,
        dest_file: Path | None = None,
    ) -> dict[str, Path | str | int]:
        """Generates a clean, paste-ready Forensic & Crash Vault log file for debugging & fixes."""
        from .. import __app_name__, __version__

        self.cfg_mgr.ensure_directories()
        exports_dir = self.cfg_mgr.base_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        ts_slug = time.strftime("%Y%m%d_%H%M%S")
        out_path = dest_file or (exports_dir / f"AstraCradle_Crash_Forensic_Log_{ts_slug}.txt")
        latest_path = exports_dir / "AstraCradle_Crash_Forensic_Log_latest.txt"

        lines: list[str] = [
            f"=== {__app_name__} v{__version__} — Forensic & Crash Vault Log (Paste-Ready) ===",
            f"Generated UTC : {utc_now_iso()}",
            f"OS / Platform : {platform.platform()} ({platform.machine()})",
            f"Python        : {platform.python_version()}",
            f"Workspace Dir : {self.cfg_mgr.base_dir}",
            "",
        ]

        if config_obj is not None:
            lines.extend(
                [
                    "--- Active Configuration Summary ---",
                    f"Profile: {config_obj.active_profile} | SimpleMode: {config_obj.simple_mode} | DryRun: {config_obj.dry_run_default}",
                    f"Theme: {config_obj.theme} | Scale: {config_obj.ui_scale} | Lang: {config_obj.language} | Tray: {config_obj.show_tray_icon}/{config_obj.minimize_to_tray}",
                    f"AstraShield: hosts={config_obj.astrashield_enabled}, dns={config_obj.astrashield_dns_enabled}, lan_tv={config_obj.astrashield_lan_tv_mode}, auto_start={config_obj.astrashield_auto_start}, tv_failsafe={config_obj.astrashield_tv_auto_failsafe}",
                    f"Guards: BadUSB={config_obj.badusb_guard_enabled} (strict={config_obj.usb_strict_gate}), RAMGuard={config_obj.ram_guard_enabled}@{config_obj.ram_guard_threshold}%, ProBalance={config_obj.probalance_enabled}, SelfHeal={config_obj.auto_self_heal}",
                    "",
                ]
            )

        if shield_status or tv_failsafe_state:
            lines.append("--- AstraShield & TV Failsafe State ---")
            if shield_status:
                lines.append(
                    f"Shield: hosts_active={shield_status.get('hosts_active')} | dns_running={shield_status.get('dns_server_running')} | listen={shield_status.get('dns_listen_addr')} | blocked={shield_status.get('blocked_queries')}/{shield_status.get('total_queries')}"
                )
            if tv_failsafe_state:
                lines.append(f"TV Failsafe: {json.dumps(tv_failsafe_state)}")
            lines.append("")

        lines.append("--- Crash History & Unhandled Tracebacks ---")
        if crash_report:
            lines.append(f"Startup Crash Report: {json.dumps(crash_report, default=str)}")
        if self.cfg_mgr.crash_history_path.exists():
            try:
                hist = json.loads(self.cfg_mgr.crash_history_path.read_text(encoding="utf-8"))
                if isinstance(hist, list):
                    for item in hist[-5:]:
                        lines.append(f"CrashRecord: {json.dumps(item, default=str)}")
            except Exception as exc:
                lines.append(f"(Failed reading crash_history.json: {exc})")
        crash_files = sorted(self.cfg_mgr.crash_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
        if crash_files:
            for cf in crash_files[:4]:
                lines.append(f"[Crash File: {cf.name}]")
                txt = cf.read_text(encoding="utf-8", errors="ignore").strip()
                lines.extend(txt.splitlines()[:45])
                lines.append("")
        else:
            lines.append("No unhandled crash_reports/*.log files recorded.")
        lines.append("")

        lines.append("--- 7-Invariant Self-Heal & Forensic Self-Audit ---")
        for ev in (self_heal_events or [])[:10]:
            nm = getattr(ev, "check_name", None) or (ev.get("check_name") if isinstance(ev, dict) else str(ev))
            st = getattr(ev, "status", None) or (ev.get("status") if isinstance(ev, dict) else "unknown")
            dt = getattr(ev, "details", None) or (ev.get("details") if isinstance(ev, dict) else "")
            lines.append(f"[{str(st).upper()}] {nm}: {dt}")
        for af in (self_audit or [])[:6]:
            sev = getattr(af, "severity", None) or (af.get("severity") if isinstance(af, dict) else "info")
            ttl = getattr(af, "title", None) or (af.get("title") if isinstance(af, dict) else "")
            exp = getattr(af, "explanation", None) or (af.get("explanation") if isinstance(af, dict) else "")
            lines.append(f"[AUDIT:{str(sev).upper()}] {ttl} — {exp}")
        lines.append("")

        entries = self.journal.read_entries(limit=120, newest_first=True)
        errs = [e for e in entries if e.outcome in ("error", "warn", "warning")]
        lines.append(f"--- Forensic Journal Errors/Warnings ({len(errs)}) ---")
        if errs:
            for e in errs[:30]:
                lines.append(f"[{e.timestamp}] {e.component}.{e.action} ({e.outcome.upper()}): {e.how} | WHY: {e.why}")
        else:
            lines.append("None.")
        lines.append("")

        lines.append("--- Recent Forensic Journal Activity (Latest 25) ---")
        for e in entries[:25]:
            lines.append(f"[{e.timestamp}] {e.component}.{e.action} ({e.outcome.upper()}): {e.how}")

        payload = "\n".join(lines) + "\n"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        if out_path != latest_path:
            latest_path.write_text(payload, encoding="utf-8")

        self.journal.log_event(
            "ForensicVault",
            "export_crash_forensic_log",
            f"Exported paste-ready forensic/crash log ({len(lines)} lines) -> {out_path.name}",
            "Allow user to paste crash and diagnostic logs directly for fast fixes",
            "ok",
        )
        return {
            "path": out_path,
            "latest_path": latest_path,
            "text": payload,
            "line_count": len(lines),
        }


class PortableProfileBackup:
    """Exports & imports SHA-256 checksummed .astracradle-profile bundles across PCs."""

    @staticmethod
    def export_profile_archive(cfg_mgr: ConfigManager, dest_path: Path, journal: ForensicJournal | None = None) -> tuple[bool, str, Path]:
        dest = Path(dest_path)
        if not dest.name.endswith(".astracradle-profile") and not dest.name.endswith(".zip"):
            dest = dest.with_suffix(".astracradle-profile")
        dest.parent.mkdir(parents=True, exist_ok=True)
        files_to_pack = [
            ("config.json", cfg_mgr.config_path),
            ("scripts.json", cfg_mgr.scripts_path),
            ("astrashield_rules.json", cfg_mgr.base_dir / "astrashield_rules.json"),
            ("hardware_presets.json", cfg_mgr.state_dir / "hardware_presets.json"),
        ]
        manifest: dict[str, str] = {}
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arc_name, src in files_to_pack:
                if src.exists() and src.is_file():
                    raw = src.read_bytes()
                    manifest[arc_name] = hashlib.sha256(raw).hexdigest()
                    zf.writestr(arc_name, raw)
            zf.writestr(
                "manifest.json",
                json.dumps({"format": "astracradle-profile-v1", "created_at": utc_now_iso(), "sha256": manifest}, indent=2),
            )
        if journal:
            journal.log_event("ProfileBackup", "export_profile", f"Exported portable profile -> {dest}", "Cross-PC config backup", "ok")
        return True, f"Exported portable profile ({len(manifest)} file(s)) -> {dest}", dest

    @staticmethod
    def import_profile_archive(archive_path: Path, cfg_mgr: ConfigManager, journal: ForensicJournal | None = None) -> tuple[bool, str]:
        ap = Path(archive_path)
        if not ap.exists():
            return False, f"Profile archive not found: {ap}"
        cfg_mgr.ensure_directories()
        target_map = {
            "config.json": cfg_mgr.config_path,
            "scripts.json": cfg_mgr.scripts_path,
            "astrashield_rules.json": cfg_mgr.base_dir / "astrashield_rules.json",
            "hardware_presets.json": cfg_mgr.state_dir / "hardware_presets.json",
        }
        try:
            with zipfile.ZipFile(ap, "r") as zf:
                names = set(zf.namelist())
                if "manifest.json" not in names:
                    return False, "Invalid profile archive: missing manifest.json."
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                hashes = manifest.get("sha256") or {}
                restored = 0
                for arc_name, expected_sha in hashes.items():
                    if arc_name not in target_map or ".." in arc_name:
                        continue
                    raw = zf.read(arc_name)
                    if hashlib.sha256(raw).hexdigest() != expected_sha:
                        return False, f"Checksum mismatch on {arc_name} — aborted import."
                    target_map[arc_name].parent.mkdir(parents=True, exist_ok=True)
                    target_map[arc_name].write_bytes(raw)
                    if arc_name == "config.json":
                        cfg_mgr._write_checksum("config.json", expected_sha)
                        cfg_mgr.save(cfg_mgr.load())
                    restored += 1
            if journal:
                journal.log_event("ProfileBackup", "import_profile", f"Imported {restored} file(s) from {ap.name}", "Portable profile restore", "ok")
            return True, f"Imported {restored} verified configuration file(s) from {ap.name}."
        except Exception as exc:
            return False, f"Failed to import profile archive: {exc}"


