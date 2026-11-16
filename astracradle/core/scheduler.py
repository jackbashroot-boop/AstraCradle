"""Cross-platform Native Background Scheduler & Threshold Alert Evaluator."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import subprocess
import sys

from .config import AppConfig, ConfigManager
from .diagnostics import DiagnosticReport
from .journal import ForensicJournal
from .models import SystemSnapshot
from .telemetry import format_bytes


@dataclass
class ThresholdAlert:
    alert_id: str
    severity: str  # warning | critical | info
    title: str
    message: str
    recommended_action: str | None = None


@dataclass
class ScheduleStatus:
    installed: bool
    os_family: str
    mechanism: str
    target_path: str
    details: str


class BackgroundSchedulerManager:
    """Manages native background scheduling (Task Scheduler / launchd / systemd --user) & alerts."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal

    @staticmethod
    def _python_exe() -> str:
        return sys.executable or "python3"

    def linux_unit_paths(self) -> tuple[Path, Path]:
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        return unit_dir / "astracradle-check.service", unit_dir / "astracradle-check.timer"

    def macos_plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / "io.astracradle.check.plist"

    def render_schedule_payload(self, interval_minutes: int = 180) -> dict[str, str]:
        py = self._python_exe()
        mins = max(15, min(1440, int(interval_minutes)))
        return {
            "linux_service": (
                "[Unit]\n"
                "Description=AstraCradle Local Equilibrium & Self-Heal Check\n\n"
                "[Service]\n"
                "Type=oneshot\n"
                f"ExecStart=\"{py}\" -m astracradle --self-check\n"
            ),
            "linux_timer": (
                "[Unit]\n"
                "Description=Run AstraCradle Equilibrium Check Periodically\n\n"
                "[Timer]\n"
                "OnBootSec=5min\n"
                f"OnUnitActiveSec={mins}min\n"
                "Persistent=true\n\n"
                "[Install]\n"
                "WantedBy=timers.target\n"
            ),
            "macos_plist": (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                "  <key>Label</key><string>io.astracradle.check</string>\n"
                "  <key>ProgramArguments</key><array>\n"
                f"    <string>{py}</string><string>-m</string><string>astracradle</string><string>--self-check</string>\n"
                "  </array>\n"
                f"  <key>StartInterval</key><integer>{mins * 60}</integer>\n"
                "  <key>RunAtLoad</key><true/>\n"
                "</dict></plist>\n"
            ),
            "windows_cmd": f'schtasks /Create /SC MINUTE /MO {mins} /TN "AstraCradleCheck" /TR "\\"{py}\\" -m astracradle --self-check" /F',
        }

    def schedule_status(self) -> ScheduleStatus:
        sys_name = platform.system()
        if sys_name == "Darwin":
            p = self.macos_plist_path()
            return ScheduleStatus(
                installed=p.exists(),
                os_family="Darwin",
                mechanism="launchd LaunchAgent",
                target_path=str(p),
                details="Active user LaunchAgent" if p.exists() else "Not scheduled",
            )
        if sys_name == "Windows":
            exists = False
            try:
                proc = subprocess.run(
                    ["schtasks", "/Query", "/TN", "AstraCradleCheck"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                exists = proc.returncode == 0
            except Exception:
                exists = False
            return ScheduleStatus(
                installed=exists,
                os_family="Windows",
                mechanism="Windows Task Scheduler",
                target_path="Task: AstraCradleCheck",
                details="Active scheduled task" if exists else "Not scheduled",
            )
        svc, tmr = self.linux_unit_paths()
        return ScheduleStatus(
            installed=svc.exists() and tmr.exists(),
            os_family="Linux",
            mechanism="systemd --user timer",
            target_path=str(tmr),
            details="User systemd timer installed" if (svc.exists() and tmr.exists()) else "Not scheduled",
        )

    def install_schedule(self, interval_minutes: int = 180) -> tuple[bool, str]:
        sys_name = platform.system()
        payload = self.render_schedule_payload(interval_minutes)
        try:
            if sys_name == "Darwin":
                plist = self.macos_plist_path()
                plist.parent.mkdir(parents=True, exist_ok=True)
                plist.write_text(payload["macos_plist"], encoding="utf-8")
                subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, timeout=3)
                msg = f"Installed launchd schedule at {plist}"
            elif sys_name == "Windows":
                py = self._python_exe()
                mins = max(15, min(1440, int(interval_minutes)))
                proc = subprocess.run(
                    [
                        "schtasks",
                        "/Create",
                        "/SC",
                        "MINUTE",
                        "/MO",
                        str(mins),
                        "/TN",
                        "AstraCradleCheck",
                        "/TR",
                        f'"{py}" -m astracradle --self-check',
                        "/F",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode != 0:
                    return False, proc.stderr.strip() or "schtasks returned error"
                msg = "Installed Windows Task Scheduler job 'AstraCradleCheck'"
            else:
                svc, tmr = self.linux_unit_paths()
                svc.parent.mkdir(parents=True, exist_ok=True)
                svc.write_text(payload["linux_service"], encoding="utf-8")
                tmr.write_text(payload["linux_timer"], encoding="utf-8")
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=3)
                subprocess.run(["systemctl", "--user", "enable", "--now", "astracradle-check.timer"], capture_output=True, timeout=3)
                msg = f"Installed systemd --user timer at {tmr}"

            self.journal.log_event(
                component="Scheduler",
                action="install_schedule",
                how=msg,
                why="User enabled native background health scheduling",
                outcome="ok",
            )
            return True, msg
        except Exception as exc:
            return False, f"Could not install schedule: {exc}"

    def remove_schedule(self) -> tuple[bool, str]:
        sys_name = platform.system()
        try:
            if sys_name == "Darwin":
                plist = self.macos_plist_path()
                if plist.exists():
                    subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True, timeout=3)
                    plist.unlink()
            elif sys_name == "Windows":
                subprocess.run(["schtasks", "/Delete", "/TN", "AstraCradleCheck", "/F"], capture_output=True, timeout=4)
            else:
                svc, tmr = self.linux_unit_paths()
                if tmr.exists():
                    subprocess.run(["systemctl", "--user", "disable", "--now", "astracradle-check.timer"], capture_output=True, timeout=3)
                    tmr.unlink()
                if svc.exists():
                    svc.unlink()
            self.journal.log_event(
                component="Scheduler",
                action="remove_schedule",
                how="Removed native OS background schedule",
                why="User disabled background scheduling or uninstalled",
                outcome="ok",
            )
            return True, "Background schedule removed cleanly."
        except Exception as exc:
            return False, f"Failed removing schedule: {exc}"

    def evaluate_threshold_alerts(
        self,
        snapshot: SystemSnapshot,
        report: DiagnosticReport,
        config: AppConfig,
    ) -> list[ThresholdAlert]:
        alerts: list[ThresholdAlert] = []
        floor = max(2.0, min(40.0, config.disk_safety_floor_percent))
        for part in snapshot.partitions:
            free_pct = max(0.0, 100.0 - float(part.percent))
            if free_pct <= floor:
                alerts.append(
                    ThresholdAlert(
                        alert_id=f"disk_{part.mountpoint}",
                        severity="critical",
                        title=f"Low Disk Headroom on {part.mountpoint}",
                        message=(
                            f"Only {free_pct:.1f}% free ({format_bytes(part.free_bytes)} remaining), "
                            f"below your {floor:.0f}% safety floor."
                        ),
                        recommended_action="clean_temp_caches",
                    )
                )
                break

        reclaimable = sum(c.size_bytes for c in report.cache_targets if c.exists)
        if reclaimable >= 512 * 1024 * 1024:
            alerts.append(
                ThresholdAlert(
                    alert_id="reclaimable_spike",
                    severity="warning",
                    title="Reclaimable Cache Threshold Reached",
                    message=(
                        f"{format_bytes(reclaimable)} of safe temporary/cache data "
                        "can be quarantined without touching personal files."
                    ),
                    recommended_action="clean_temp_caches",
                )
            )

        if snapshot.mem_percent >= 88.0 and report.recommendations:
            alerts.append(
                ThresholdAlert(
                    alert_id="ram_ai_pressure",
                    severity="warning",
                    title="High Memory + Idle AI Runtime",
                    message=(
                        f"RAM is at {snapshot.mem_percent:.0f}% while {len(report.recommendations)} "
                        "AI/idle process suggestion(s) are available."
                    ),
                    recommended_action="scan_idle_ai_helpers",
                )
            )
        return alerts
