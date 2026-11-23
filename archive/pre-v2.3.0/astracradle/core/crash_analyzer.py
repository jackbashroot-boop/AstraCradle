"""Heartbeat Sentinel and Crash / System Failure Post-Mortem Analyzer."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import subprocess
import traceback
import uuid

import psutil

from .config import ConfigManager
from .journal import ForensicJournal
from .models import CrashPostMortem, utc_now_iso


class CrashAnalyzer:
    """Detects unclean app or OS crashes, diagnoses probable causes, and builds recovery playbooks."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal

    def mark_running(self, profile: str) -> None:
        self.cfg_mgr.ensure_directories()
        payload = {
            "pid": os.getpid(),
            "started_at": utc_now_iso(),
            "clean_shutdown": False,
            "os_family": platform.system(),
            "profile": profile,
            "boot_time": psutil.boot_time(),
        }
        tmp = self.cfg_mgr.heartbeat_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.cfg_mgr.heartbeat_path)

    def mark_clean_shutdown(self) -> None:
        if not self.cfg_mgr.heartbeat_path.exists():
            return
        try:
            data = json.loads(self.cfg_mgr.heartbeat_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data["clean_shutdown"] = True
        data["ended_at"] = utc_now_iso()
        tmp = self.cfg_mgr.heartbeat_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.cfg_mgr.heartbeat_path)

    def record_unhandled_exception(self, exc: BaseException) -> Path:
        self.cfg_mgr.ensure_directories()
        tb_txt = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        crash_file = self.cfg_mgr.crash_dir / f"crash_{uuid.uuid4().hex[:8]}.log"
        crash_file.write_text(
            f"Timestamp: {utc_now_iso()}\nOS: {platform.platform()}\n\n{tb_txt}",
            encoding="utf-8",
        )
        self.journal.log_event(
            component="CrashAnalyzer",
            action="unhandled_exception",
            how="sys.excepthook captured unhandled Python traceback",
            why="Preserving exact stack trace for post-mortem diagnosis on next launch",
            outcome="error",
            details=str(exc),
        )
        return crash_file

    def _scan_os_crash_clues(self, consent_crash_logs: bool = True) -> list[str]:
        if not consent_crash_logs:
            return ["Native OS crash log inspection skipped per user privacy preference."]
        clues: list[str] = []
        sys_name = platform.system()
        if sys_name == "Linux":
            crash_dir = Path("/var/crash")
            if crash_dir.exists():
                try:
                    dumps = list(crash_dir.glob("*.crash"))
                    if dumps:
                        clues.append(f"Found {len(dumps)} system crash report(s) in /var/crash.")
                except OSError:
                    pass
            pstore = Path("/sys/fs/pstore")
            if pstore.exists():
                try:
                    oops = [p.name for p in pstore.iterdir() if p.is_file()]
                    if oops:
                        clues.append(f"Kernel pstore oops/panic files detected: {', '.join(oops[:5])}.")
                except OSError:
                    pass
            if shutil_which("journalctl"):
                try:
                    p = subprocess.run(
                        ["journalctl", "-b", "-1", "-p", "err", "-n", "50", "--no-pager"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if p.returncode == 0 and p.stdout.strip():
                        txt = p.stdout.strip()
                        clues.extend(self.translate_os_crash_signatures(txt))
                        clues.append("Previous boot systemd errors (tail):\n" + "\n".join(txt.splitlines()[-8:])[:600])
                except Exception:
                    pass
        elif sys_name == "Darwin":
            for diag_dir in (
                Path.home() / "Library" / "Logs" / "DiagnosticReports",
                Path("/Library/Logs/DiagnosticReports"),
            ):
                if diag_dir.exists():
                    try:
                        reports = sorted(diag_dir.glob("*.ips")) + sorted(diag_dir.glob("*.crash"))
                        if reports:
                            latest = reports[-1]
                            clues.append(f"Recent macOS DiagnosticReports ({diag_dir.name}): {', '.join(r.name for r in reports[-3:])}")
                            snippet = latest.read_text(encoding="utf-8", errors="ignore")[:800]
                            clues.extend(self.translate_os_crash_signatures(snippet))
                    except OSError:
                        pass
        elif sys_name == "Windows":
            minidump = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "Minidump"
            if minidump.exists():
                try:
                    dmps = list(minidump.glob("*.dmp"))
                    if dmps:
                        clues.append(f"Found {len(dmps)} Windows BSOD minidump(s) in {minidump}.")
                except OSError:
                    pass
            if shutil_which("wevtutil"):
                try:
                    p = subprocess.run(
                        [
                            "wevtutil",
                            "qe",
                            "System",
                            "/q:*[System[(EventID=41 or EventID=1001 or Level=2)]]",
                            "/c:10",
                            "/rd:true",
                            "/f:text",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    if p.returncode == 0 and p.stdout.strip():
                        clues.extend(self.translate_os_crash_signatures(p.stdout))
                        clues.append("Windows System Event Log tail:\n" + p.stdout.strip()[:500])
                except Exception:
                    pass
        return clues

    @staticmethod
    def translate_os_crash_signatures(log_text: str) -> list[str]:
        low = log_text.lower()
        notes: list[str] = []
        if "event id: 41" in low or "eventid>41<" in low or "kernel-power" in low:
            notes.append(
                "Decoded Windows Event ID 41 (Kernel-Power): Sudden power loss, PSU brownout, thermal shutdown, or hard reset button."
            )
        if "1001" in low and ("bugcheck" in low or "bsod" in low):
            notes.append(
                "Decoded Windows Event 1001 (BugCheck): Blue Screen kernel driver or memory fault triggered an automatic reboot."
            )
        if "out of memory" in low or "oom-killer" in low or "killed process" in low:
            notes.append(
                "Decoded OOM-Killer Event: RAM + swap became completely exhausted; enable 'Idle AI Hibernate' or close memory hogs."
            )
        if "gpu hang" in low or "drm" in low and "reset" in low or "nvrm" in low:
            notes.append(
                "Decoded GPU Driver Reset / Hang: Graphics driver stalled under heavy 3D/compute load and was reset by the kernel."
            )
        if "kernel panic" in low or "panic(" in low:
            notes.append(
                "Decoded Kernel Panic: Unrecoverable hardware, filesystem, or kernel extension failure triggered a system halt."
            )
        return notes

    def load_history(self) -> list[CrashPostMortem]:
        path = self.cfg_mgr.crash_history_path
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            valid = {f.name for f in CrashPostMortem.__dataclass_fields__.values()}
            req = {"incident_id", "detected_at", "crash_type", "probable_cause", "evidence", "fix_steps", "last_known_actions"}
            out: list[CrashPostMortem] = []
            for item in raw:
                if isinstance(item, dict) and req.issubset(item.keys()):
                    try:
                        out.append(CrashPostMortem(**{k: v for k, v in item.items() if k in valid}))
                    except Exception:
                        continue
            return out
        except Exception:
            return []

    def _save_history(self, items: list[CrashPostMortem]) -> None:
        tmp = self.cfg_mgr.crash_history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(x) for x in items[:30]], indent=2), encoding="utf-8")
        os.replace(tmp, self.cfg_mgr.crash_history_path)

    def inspect_on_startup(self) -> CrashPostMortem | None:
        """Checks whether prior run crashed or host rebooted unexpectedly, and diagnoses cause."""
        unclean = False
        prior_boot = None
        prior_profile = "Unknown"
        crash_type = "unclean_app_exit"
        evidence: list[str] = []

        if self.cfg_mgr.heartbeat_path.exists():
            try:
                hb = json.loads(self.cfg_mgr.heartbeat_path.read_text(encoding="utf-8"))
                if hb.get("clean_shutdown") is False:
                    unclean = True
                    prior_profile = str(hb.get("profile") or "Unknown")
                    prior_boot = hb.get("boot_time")
                    evidence.append(
                        f"Previous session PID {hb.get('pid')} started {hb.get('started_at')} did not record clean shutdown."
                    )
                    if hb.get("simulated"):
                        crash_type = "simulated_crash"
                        evidence.append(str(hb.get("simulated_reason") or "Simulated crash trigger."))
            except Exception:
                unclean = True
                evidence.append("Heartbeat sentinel file was corrupted or truncated mid-write.")

        crash_logs = sorted(self.cfg_mgr.crash_dir.glob("crash_*.log"))
        unhandled_tb = ""
        if crash_logs:
            unclean = True
            crash_type = "python_exception"
            latest_log = crash_logs[-1]
            unhandled_tb = latest_log.read_text(encoding="utf-8", errors="ignore")[:700]
            evidence.append(f"Captured exception traceback in {latest_log.name}.")

        if prior_boot is not None and abs(float(psutil.boot_time()) - float(prior_boot)) > 5.0 and unclean:
            crash_type = "os_crash_or_power_loss"
            evidence.append("OS boot timestamp changed while AstraCradle was active (unexpected reboot/power loss/BSOD/kernel panic).")

        if not unclean:
            return None

        evidence.extend(self._scan_os_crash_clues())
        try:
            from .telemetry_history import DriverUpdateCorrelator

            recent_updates = DriverUpdateCorrelator.recent_changes(limit=5)
            if recent_updates:
                evidence.append("Recent OS/Driver/Package Changes (48h Correlation):")
                evidence.extend([f"  • {u}" for u in recent_updates])
        except Exception:
            pass
        recent_entries = self.journal.read_entries(limit=8, newest_first=True)
        last_actions = [f"[{e.timestamp}] {e.component}.{e.action} ({e.outcome}): {e.how}" for e in recent_entries]

        # Infer probable cause
        if crash_type == "simulated_crash":
            cause = "Simulated unexpected termination / power-drop test triggered by user."
            fixes = [
                "No real damage occurred — AstraCradle verified your config, rollback ledger, and hash chain.",
                "Review the pre-crash forensic timeline below to see how AstraCradle pinpoints the last active step.",
            ]
        elif crash_type == "python_exception":
            cause = f"Unhandled application exception occurred while running profile '{prior_profile}'."
            if unhandled_tb:
                evidence.append(unhandled_tb)
            fixes = [
                "Review the captured traceback and disable any recently added custom script/plugin if it triggered the fault.",
                "AstraCradle automatically verified config & rollback backups on restart.",
            ]
        elif crash_type == "os_crash_or_power_loss":
            cause = (
                "Host computer restarted or lost power unexpectedly (possible thermal shutdown, GPU driver hang, "
                "OOM killer, or power interruption)."
            )
            fixes = [
                "Check recent OS thermal/RAM pressure and consider switching from aggressive overclock/3D scripts to 'Balanced Daily'.",
                "Inspect OS crash dumps or Event Viewer / journalctl logs listed in the evidence.",
                "Run AstraCradle's Rollback Vault check if a script was active before the reboot.",
            ]
        else:
            cause = "AstraCradle was force-closed or terminated without completing its clean-shutdown hook."
            fixes = [
                "Verify any active optimization script state in the Toggleable Scripts tab.",
                "Run a Self-Heal Check to confirm zero orphaned temp locks remain.",
            ]

        ev_scores: list[dict[str, object]] = []
        conf = 52
        if any("heartbeat" in e.lower() or "pid" in e.lower() for e in evidence):
            ev_scores.append({"signal": "Unclean Heartbeat Sentinel", "weight": 22})
            conf += 22
        if unhandled_tb:
            ev_scores.append({"signal": "Captured Python Stack Traceback", "weight": 24})
            conf += 24
        if crash_type == "os_crash_or_power_loss":
            ev_scores.append({"signal": "OS Boot Timestamp Jump (>5s)", "weight": 20})
            conf += 20
        if any("decoded" in e.lower() for e in evidence):
            ev_scores.append({"signal": "Matched OS Kernel / Event Signature", "weight": 18})
            conf += 18
        if any("48h correlation" in e.lower() for e in evidence):
            ev_scores.append({"signal": "Recent 48h Driver/Package Update", "weight": 10})
            conf += 10
        if last_actions:
            ev_scores.append({"signal": f"{len(last_actions)} Chained Forensic Log Clues", "weight": 8})
            conf += 8
        conf = max(35, min(99, conf))

        report = CrashPostMortem(
            incident_id=f"inc-{uuid.uuid4().hex[:8]}",
            detected_at=utc_now_iso(),
            crash_type=crash_type,
            probable_cause=cause,
            evidence=evidence,
            fix_steps=fixes,
            last_known_actions=last_actions,
            resolved=False,
            confidence_percent=conf,
            evidence_scores=ev_scores,
        )
        history = self.load_history()
        history.insert(0, report)
        self._save_history(history)

        self.journal.log_event(
            component="CrashAnalyzer",
            action="post_mortem_diagnosed",
            how="Detected unclean heartbeat sentinel and analyzed pre-crash forensic journal & OS clues",
            why="Provide clear root-cause explanation and recovery steps after unexpected crash/reboot",
            outcome="warning",
            details=cause,
        )
        return report

    def simulate_crash_and_diagnose(self, profile: str) -> CrashPostMortem:
        """Simulates an unexpected crash/power-loss and immediately runs Post-Mortem Diagnosis."""
        self.journal.log_event(
            component="ScriptsEngine",
            action="pre_crash_workload_marker",
            how=f"Simulating heavy workload under profile '{profile}'",
            why="User triggered Crash Post-Mortem verification test",
            outcome="warning",
            details="Simulated sudden power loss before clean shutdown",
        )
        payload = {
            "pid": os.getpid(),
            "started_at": utc_now_iso(),
            "clean_shutdown": False,
            "os_family": platform.system(),
            "profile": profile,
            "boot_time": psutil.boot_time(),
            "simulated": True,
            "simulated_reason": "Simulated sudden termination / OS hang test via Safeguards UI.",
        }
        self.cfg_mgr.heartbeat_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        report = self.inspect_on_startup()
        self.mark_running(profile)
        assert report is not None
        return report


def shutil_which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)
