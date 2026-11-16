"""Autoruns-Style Startup, Scheduled Task & Service Audit + Ghost Driver Cleanup."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import uuid

from .config import ConfigManager
from .journal import ForensicJournal
from .models import RollbackEntry, utc_now_iso
from .os_adapters import BaseOSAdapter
from .rollback import RollbackManager


@dataclass
class AutorunEntry:
    entry_id: str
    category: str  # startup | scheduled_task | service | driver
    name: str
    publisher_or_scope: str
    command_or_path: str
    plain_english: str
    impact: str  # low | medium | high
    enabled: bool
    can_toggle: bool
    location: str


@dataclass
class GhostDriverEntry:
    driver_id: str
    name: str
    subsystem: str  # usb | audio | gpu | storage | network | config
    device_node: str
    status: str  # disconnected | orphaned_config | stale_module
    plain_english: str
    reclaimable_note: str
    artifact_path: str = ""
    safe_to_clean: bool = True


_PLAIN_ENGLISH_HINTS: dict[str, str] = {
    "cron": "Scheduled background timer running periodic user or system jobs.",
    "systemd": "Systemd background service or user timer unit.",
    "ssh": "OpenSSH authentication agent or remote login service.",
    "bluetooth": "Wireless Bluetooth radio and peripheral pairing daemon.",
    "cups": "Print spooler service for printers and PDF queues.",
    "pipewire": "Modern low-latency desktop audio & video routing daemon.",
    "pulseaudio": "Desktop audio mixing server.",
    "docker": "Container engine background daemon.",
    "ollama": "Local AI model server; uses significant RAM when left loaded.",
    "steam": "Game launcher background updater and overlay client.",
    "onedrive": "Cloud drive sync helper.",
    "dropbox": "Cloud file synchronization agent.",
}


def _explain_entry(name: str, cat: str) -> str:
    low = name.lower()
    for kw, desc in _PLAIN_ENGLISH_HINTS.items():
        if kw in low:
            return desc
    if cat == "scheduled_task":
        return "Timed background task/cron job that wakes up automatically on a schedule."
    if cat == "service":
        return "Background daemon running continuously while your computer is powered on."
    if cat == "driver":
        return "Loaded kernel/hardware module connecting OS software to physical hardware."
    return "User login entry that starts automatically when you sign in."


class AutorunsAndGhostAuditor:
    """Inspects startup entries, scheduled tasks, services, and disconnected/ghost drivers."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.rollback_mgr = rollback_mgr
        self.journal = journal

    def scan_autoruns(self, adapter: BaseOSAdapter, include_system: bool = False) -> list[AutorunEntry]:
        entries: list[AutorunEntry] = []

        # 1. Startup items via OS adapter
        for s in adapter.discover_startup_items():
            loc_p = Path(s.location)
            can_t = loc_p.exists() and not adapter.is_path_protected(loc_p)
            entries.append(
                AutorunEntry(
                    entry_id=f"ar_start_{s.item_id}",
                    category="startup",
                    name=s.name,
                    publisher_or_scope=s.scope,
                    command_or_path=s.command,
                    plain_english=_explain_entry(s.name, "startup"),
                    impact=s.impact,
                    enabled=s.enabled,
                    can_toggle=can_t,
                    location=s.location,
                )
            )

        sys_name = platform.system()
        if sys_name == "Linux":
            # User systemd timers & services
            user_systemd = Path.home() / ".config" / "systemd" / "user"
            if user_systemd.exists():
                for unit in sorted(user_systemd.glob("*")):
                    if unit.is_file() and unit.suffix in (".timer", ".service"):
                        cat = "scheduled_task" if unit.suffix == ".timer" else "service"
                        entries.append(
                            AutorunEntry(
                                entry_id=f"ar_unit_{unit.name}",
                                category=cat,
                                name=unit.name,
                                publisher_or_scope="user systemd",
                                command_or_path=str(unit),
                                plain_english=_explain_entry(unit.name, cat),
                                impact="low",
                                enabled=True,
                                can_toggle=not unit.name.startswith("astracradle"),
                                location=str(unit),
                            )
                        )
            # User crontab
            if shutil.which("crontab"):
                try:
                    p = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=2)
                    if p.returncode == 0:
                        for idx, ln in enumerate(p.stdout.splitlines()):
                            s = ln.strip()
                            if s and not s.startswith("#"):
                                entries.append(
                                    AutorunEntry(
                                        entry_id=f"ar_cron_{idx}",
                                        category="scheduled_task",
                                        name=f"User Cron #{idx + 1}",
                                        publisher_or_scope="crontab",
                                        command_or_path=s[:100],
                                        plain_english=_explain_entry(s, "scheduled_task"),
                                        impact="low",
                                        enabled=True,
                                        can_toggle=False,
                                        location="user crontab",
                                    )
                                )
                except Exception:
                    pass
            # Loaded kernel modules (read-only audit unless ghost)
            proc_mods = Path("/proc/modules")
            if proc_mods.exists():
                try:
                    for ln in proc_mods.read_text(encoding="utf-8", errors="ignore").splitlines()[: (24 if include_system else 8)]:
                        parts = ln.split()
                        if parts:
                            mname = parts[0]
                            entries.append(
                                AutorunEntry(
                                    entry_id=f"ar_kmod_{mname}",
                                    category="driver",
                                    name=mname,
                                    publisher_or_scope="kernel module",
                                    command_or_path=f"/sys/module/{mname}",
                                    plain_english=_explain_entry(mname, "driver"),
                                    impact="low",
                                    enabled=True,
                                    can_toggle=False,
                                    location="/proc/modules",
                                )
                            )
                except OSError:
                    pass

        elif sys_name == "Darwin":
            la = Path.home() / "Library" / "LaunchAgents"
            if la.exists():
                for pl in sorted(la.glob("*.plist"))[:20]:
                    entries.append(
                        AutorunEntry(
                            entry_id=f"ar_la_{pl.stem}",
                            category="scheduled_task",
                            name=pl.stem,
                            publisher_or_scope="LaunchAgent",
                            command_or_path=str(pl),
                            plain_english=_explain_entry(pl.stem, "scheduled_task"),
                            impact="medium",
                            enabled=True,
                            can_toggle="astracradle" not in pl.stem.lower(),
                            location=str(pl),
                        )
                    )

        elif sys_name == "Windows" and shutil.which("schtasks"):
            try:
                p = subprocess.run(
                    ["schtasks", "/Query", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if p.returncode == 0:
                    for idx, ln in enumerate(p.stdout.splitlines()[:15]):
                        cols = [c.strip('"') for c in ln.split('","')]
                        if cols and cols[0]:
                            entries.append(
                                AutorunEntry(
                                    entry_id=f"ar_sch_{idx}",
                                    category="scheduled_task",
                                    name=cols[0],
                                    publisher_or_scope="Task Scheduler",
                                    command_or_path=cols[0],
                                    plain_english=_explain_entry(cols[0], "scheduled_task"),
                                    impact="medium",
                                    enabled=True,
                                    can_toggle=False,
                                    location="Windows Task Scheduler",
                                )
                            )
            except Exception:
                pass

        return entries

    def toggle_autorun_entry(
        self,
        entry: AutorunEntry,
        enable: bool,
        adapter: BaseOSAdapter,
        dry_run: bool = True,
    ) -> tuple[bool, str]:
        loc = Path(entry.location)
        if not entry.can_toggle or adapter.is_path_protected(loc) or loc.is_symlink():
            return False, f"'{entry.name}' is system-scoped or protected; inspect via OS administrative tools."
        if not loc.exists() and enable:
            disabled_sidecar = loc.with_suffix(loc.suffix + ".astracradle_disabled")
            if disabled_sidecar.exists():
                if dry_run:
                    return True, f"[DRY RUN] Would re-enable '{entry.name}' from {disabled_sidecar.name}."
                os.replace(disabled_sidecar, loc)
                self.journal.log_event(
                    "AutorunsAudit", f"enable_{entry.entry_id}", f"Re-enabled {entry.name}", "User restored autorun entry", "ok"
                )
                return True, f"Re-enabled '{entry.name}'."
            return False, f"Startup file missing for '{entry.name}'."

        if not enable and loc.exists():
            disabled_sidecar = loc.with_suffix(loc.suffix + ".astracradle_disabled")
            if dry_run:
                return True, f"[DRY RUN] Would safely disable '{entry.name}' (reversible sidecar {disabled_sidecar.name})."
            os.replace(loc, disabled_sidecar)
            rb_entry = RollbackEntry(
                entry_id=f"act-{uuid.uuid4().hex[:8]}",
                timestamp=utc_now_iso(),
                action_id="toggle_autorun",
                title=f"Disabled Autorun: {entry.name}",
                dry_run=False,
                status="applied",
                summary=f"Moved {loc} -> {disabled_sidecar}",
                quarantine_paths=[{"src": str(loc), "quarantine": str(disabled_sidecar)}],
            )
            self.rollback_mgr.record(rb_entry)
            self.journal.log_event(
                "AutorunsAudit",
                f"disable_{entry.entry_id}",
                f"Disabled autorun '{entry.name}' reversibly",
                "User disabled non-essential startup/task entry",
                "ok",
            )
            return True, f"Safely disabled '{entry.name}' (reversible in Rollback Vault)."
        return True, f"'{entry.name}' is already in requested state."

    def scan_ghost_drivers(self, extra_search_dirs: list[Path] | None = None) -> list[GhostDriverEntry]:
        """Finds disconnected/ghost hardware drivers, orphaned driver caches, or broken modprobe/INF remnants."""
        ghosts: list[GhostDriverEntry] = []
        sys_name = platform.system()

        # Check user/supplied directories for stale driver/device cache remnants & dangling device configs
        search_dirs = list(extra_search_dirs or [])
        if sys_name == "Linux":
            # Check for unused (refcount == 0) non-essential USB/audio/legacy kernel modules in /proc/modules
            proc_mods = Path("/proc/modules")
            if proc_mods.exists():
                try:
                    for ln in proc_mods.read_text(encoding="utf-8", errors="ignore").splitlines():
                        parts = ln.split()
                        if len(parts) >= 4:
                            mname, _sz, refcnt, deps = parts[0], parts[1], parts[2], parts[3]
                            if refcnt == "0" and deps == "-" and any(
                                k in mname.lower() for k in ("usb_storage", "uvcvideo", "joydev", "btusb", "snd_usb")
                            ):
                                ghosts.append(
                                    GhostDriverEntry(
                                        driver_id=f"ghost_kmod_{mname}",
                                        name=f"Idle Kernel Driver: {mname}",
                                        subsystem="usb" if "usb" in mname else "audio",
                                        device_node=f"/sys/module/{mname}",
                                        status="stale_module",
                                        plain_english=f"Driver '{mname}' is loaded in memory with 0 active devices attached.",
                                        reclaimable_note="Optional unload via: sudo modprobe -r " + mname,
                                        artifact_path="",
                                        safe_to_clean=True,
                                    )
                                )
                except OSError:
                    pass
            # Check user pulse/pipewire/xorg/vulkan stale driver caches
            for cdir in [
                Path.home() / ".cache" / "mesa_shader_cache",
                Path.home() / ".nv" / "ComputeCache",
            ]:
                if cdir.exists():
                    search_dirs.append(cdir)
        elif sys_name == "Windows" and shutil.which("powershell"):
            try:
                p = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "Get-PnpDevice -Status Unknown | Select-Object -First 8 FriendlyName,Class,InstanceId | ConvertTo-Json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if p.returncode == 0 and p.stdout.strip().startswith("["):
                    import json as _j
                    for idx, item in enumerate(_j.loads(p.stdout)):
                        fn = str(item.get("FriendlyName") or "Disconnected Device")
                        cls = str(item.get("Class") or "usb").lower()
                        iid = str(item.get("InstanceId") or "")
                        ghosts.append(
                            GhostDriverEntry(
                                driver_id=f"ghost_pnp_{idx}",
                                name=fn,
                                subsystem=cls if cls in ("usb", "audio", "gpu", "storage", "network") else "usb",
                                device_node=iid,
                                status="disconnected",
                                plain_english="Phantom Windows PnP device left behind after unplugging old hardware.",
                                reclaimable_note=f"pnputil /remove-device \"{iid}\"",
                                safe_to_clean=True,
                            )
                        )
            except Exception:
                pass

        for sdir in search_dirs:
            if not sdir.exists() or sdir.is_symlink():
                continue
            try:
                for p in sorted(sdir.iterdir()):
                    if p.is_symlink() and not p.exists():
                        ghosts.append(
                            GhostDriverEntry(
                                driver_id=f"ghost_cfg_{uuid.uuid4().hex[:6]}",
                                name=f"Orphaned Driver Link: {p.name}",
                                subsystem="config",
                                device_node=str(p),
                                status="orphaned_config",
                                plain_english="Broken driver/cache symlink pointing to a removed hardware target.",
                                reclaimable_note="Safe to quarantine broken link.",
                                artifact_path=str(p),
                                safe_to_clean=True,
                            )
                        )
                    elif p.is_file() and p.suffix.lower() in (".ghost_drv", ".old_inf", ".stale_drv"):
                        ghosts.append(
                            GhostDriverEntry(
                                driver_id=f"ghost_file_{p.stem[:14]}",
                                name=f"Orphaned Driver Artifact: {p.name}",
                                subsystem="config",
                                device_node=str(p),
                                status="orphaned_config",
                                plain_english="Disconnected legacy driver descriptor left behind after device removal.",
                                reclaimable_note="Reversible quarantine cleanup available.",
                                artifact_path=str(p),
                                safe_to_clean=True,
                            )
                        )
            except OSError:
                continue

        return ghosts

    def clean_ghost_driver(
        self,
        entry: GhostDriverEntry,
        adapter: BaseOSAdapter,
        dry_run: bool = True,
    ) -> tuple[bool, str]:
        if entry.artifact_path:
            art = Path(entry.artifact_path)
            if adapter.is_path_protected(art):
                return False, f"Protected path blocked for ghost artifact '{entry.name}'."
            if dry_run:
                return True, f"[DRY RUN] Would quarantine orphaned driver artifact {art.name}."
            if art.exists() or art.is_symlink():
                q_dest = self.cfg_mgr.quarantine_dir / f"ghost_{uuid.uuid4().hex[:6]}_{art.name}"
                shutil.move(str(art), str(q_dest))
                rb = RollbackEntry(
                    entry_id=f"act-{uuid.uuid4().hex[:8]}",
                    timestamp=utc_now_iso(),
                    action_id="clean_ghost_driver",
                    title=f"Quarantined Ghost Driver: {entry.name}",
                    dry_run=False,
                    status="applied",
                    summary=f"Moved {art} -> {q_dest}",
                    quarantine_paths=[{"src": str(art), "quarantine": str(q_dest)}],
                )
                self.rollback_mgr.record(rb)
                self.journal.log_event(
                    "AutorunsAudit",
                    f"clean_{entry.driver_id}",
                    f"Quarantined ghost driver artifact {art}",
                    entry.plain_english,
                    "ok",
                )
                return True, f"Quarantined '{entry.name}' safely (reversible in Rollback Vault)."
            return False, f"Artifact '{art}' no longer exists."

        msg = (
            f"{'[DRY RUN] ' if dry_run else ''}Ghost driver cleanup prepared for '{entry.name}': "
            f"{entry.reclaimable_note}"
        )
        self.journal.log_event("AutorunsAudit", f"advise_{entry.driver_id}", msg, entry.plain_english, "ok")
        return True, msg
