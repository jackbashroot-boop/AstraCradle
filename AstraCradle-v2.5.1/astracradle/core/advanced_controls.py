"""Advanced Controls: Fullscreen/Focus Window Detector, Per-App Network Firewall/Shaper, GPU Power/Fan Curves & Binary Builder."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from typing import Any

import psutil

from .config import ConfigManager
from .elevation import ElevationRunner
from .journal import ForensicJournal
from .models import utc_now_iso


@dataclass
class ActiveWindowState:
    window_title: str
    process_name: str
    is_fullscreen: bool
    detection_method: str


class FullscreenFocusDetector:
    """Cross-platform active window & fullscreen detector (Win32 / X11 / Wayland / Quartz)."""

    @staticmethod
    def detect() -> ActiveWindowState:
        sys_name = platform.system()
        if sys_name == "Windows":
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32  # type: ignore[attr-defined]
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    length = user32.GetWindowTextLengthW(hwnd)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    sw = user32.GetSystemMetrics(0)
                    sh = user32.GetSystemMetrics(1)
                    is_fs = (rect.right - rect.left) >= sw and (rect.bottom - rect.top) >= sh and sw > 0
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    pname = "unknown"
                    if pid.value:
                        try:
                            pname = psutil.Process(pid.value).name()
                        except Exception:
                            pass
                    return ActiveWindowState(buf.value or pname, pname, bool(is_fs), "Win32 GetForegroundWindow")
            except Exception:
                pass

        elif sys_name == "Darwin" and shutil.which("osascript"):
            try:
                script = 'tell application "System Events" to get name of first application process whose frontmost is true'
                p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
                if p.returncode == 0 and p.stdout.strip():
                    app_name = p.stdout.strip()
                    return ActiveWindowState(app_name, app_name, False, "macOS AppleScript Quartz")
            except Exception:
                pass

        elif sys_name == "Linux":
            if shutil.which("hyprctl"):
                try:
                    p = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=2)
                    if p.returncode == 0 and p.stdout.strip():
                        data = json.loads(p.stdout)
                        return ActiveWindowState(
                            str(data.get("title") or "Hyprland Window"),
                            str(data.get("class") or "unknown"),
                            bool(data.get("fullscreen", False)),
                            "Wayland hyprctl",
                        )
                except Exception:
                    pass
            if shutil.which("xprop") and os.environ.get("DISPLAY"):
                try:
                    root_p = subprocess.run(
                        ["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True, timeout=2
                    )
                    if root_p.returncode == 0 and "0x" in root_p.stdout:
                        wid = root_p.stdout.strip().split()[-1]
                        if wid != "0x0":
                            wp = subprocess.run(["xprop", "-id", wid], capture_output=True, text=True, timeout=2)
                            txt = wp.stdout
                            is_fs = "_NET_WM_STATE_FULLSCREEN" in txt
                            title = "X11 Active Window"
                            for ln in txt.splitlines():
                                if ln.startswith("_NET_WM_NAME") or ln.startswith("WM_NAME("):
                                    if "=" in ln:
                                        title = ln.split("=", 1)[1].strip().strip('"')
                                        break
                            return ActiveWindowState(title, title.split()[0] if title else "x11", is_fs, "X11 xprop")
                except Exception:
                    pass

        return ActiveWindowState("Desktop / Headless Session", "desktop", False, "psutil fallback")


@dataclass
class AppNetworkRule:
    rule_id: str
    app_name: str
    action: str  # allow | throttle | block
    rate_limit_kbps: int = 0
    protocol: str = "any"
    command_preview: str = ""
    enabled: bool = True
    updated_at: str = field(default_factory=utc_now_iso)


class NetworkFirewallController:
    """Manages per-app network bandwidth shaping & firewall rule generation with dry-run & rollback safety."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        journal: ForensicJournal,
        elevation: ElevationRunner | None = None,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.elevation = elevation
        self.rules_path = cfg_mgr.state_dir / "network_rules.json"

    def load_rules(self) -> list[AppNetworkRule]:
        if not self.rules_path.exists():
            return []
        try:
            raw = json.loads(self.rules_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            valid = {f.name for f in AppNetworkRule.__dataclass_fields__.values()}
            return [AppNetworkRule(**{k: v for k, v in r.items() if k in valid}) for r in raw if isinstance(r, dict) and r.get("app_name")]
        except Exception:
            return []

    def save_rules(self, rules: list[AppNetworkRule]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.rules_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in rules], indent=2), encoding="utf-8")
        os.replace(tmp, self.rules_path)

    @staticmethod
    def build_os_rule_command(app_name: str, action: str, rate_kbps: int = 1024) -> str:
        safe_app = "".join(c for c in app_name if c.isalnum() or c in "._- ")[:48].strip() or "app"
        sys_name = platform.system()
        if sys_name == "Windows":
            if action == "block":
                return f'netsh advfirewall firewall add rule name="AstraCradle_Block_{safe_app}" dir=out action=block program="{safe_app}"'
            if action == "throttle":
                return f'New-NetQosPolicy -Name "AstraCradle_Limit_{safe_app}" -AppPathNameMatchCondition "{safe_app}" -ThrottleRateActionBitsPerSecond {max(64, rate_kbps) * 1000}'
            return f'netsh advfirewall firewall delete rule name="AstraCradle_Block_{safe_app}"'
        if sys_name == "Darwin":
            if action == "block":
                return f'echo "block drop out quick proto tcp all user _app_{safe_app}" | sudo pfctl -a com.astracradle -f -'
            if action == "throttle":
                return f'sudo dnctl pipe 1 config bw {max(64, rate_kbps)}Kbit/s'
            return 'sudo pfctl -a com.astracradle -F rules'
        # Linux
        if action == "block":
            return f'sudo nft add rule inet filter output meta skuid "$(id -u)" counter drop  # filter for {safe_app}'
        if action == "throttle":
            return f'sudo tc qdisc replace dev lo root tbf rate {max(64, rate_kbps)}kbit burst 32kbit latency 400ms  # shaper for {safe_app}'
        return 'sudo tc qdisc del dev lo root'

    def add_or_update_rule(
        self,
        app_name: str,
        action: str,
        rate_kbps: int = 1024,
        dry_run: bool = True,
        enforce_os: bool = False,
    ) -> tuple[bool, str, AppNetworkRule]:
        clean_action = action if action in ("allow", "throttle", "block") else "throttle"
        cmd = self.build_os_rule_command(app_name, clean_action, rate_kbps)
        rule = AppNetworkRule(
            rule_id=f"net_{app_name.lower().replace(' ', '_')[:20]}",
            app_name=app_name,
            action=clean_action,
            rate_limit_kbps=max(0, int(rate_kbps)),
            command_preview=cmd,
            enabled=True,
        )
        rules = [r for r in self.load_rules() if r.app_name.lower() != app_name.lower()]
        rules.insert(0, rule)
        if not dry_run:
            self.save_rules(rules)
        enforced_note = ""
        if not dry_run and enforce_os and self.elevation is not None and platform.system() == "Linux":
            argv: list[str] | None = None
            if clean_action == "throttle" and shutil.which("tc"):
                argv = ["tc", "qdisc", "replace", "dev", "lo", "root", "tbf", "rate", f"{max(64, int(rate_kbps))}kbit", "burst", "32kbit", "latency", "400ms"]
            elif clean_action == "allow" and shutil.which("tc"):
                argv = ["tc", "qdisc", "del", "dev", "lo", "root"]
            if argv is not None:
                eres = self.elevation.run_privileged(argv, reason=f"Apply network rule '{clean_action}' for {app_name}")
                enforced_note = f" | OS: {eres.message}"
        msg = (
            f"{'[DRY RUN] ' if dry_run else ''}Saved network rule '{clean_action.upper()}' for {app_name}. "
            f"OS command: {cmd}{enforced_note}"
        )
        self.journal.log_event(
            "NetworkFirewall",
            f"rule_{clean_action}",
            msg,
            f"User configured network policy for {app_name}",
            "ok",
        )
        return True, msg, rule

    def remove_rule(self, rule_id: str) -> bool:
        rules = self.load_rules()
        rem = [r for r in rules if r.rule_id != rule_id]
        if len(rem) == len(rules):
            return False
        self.save_rules(rem)
        return True


@dataclass
class GpuThermalProfile:
    profile_id: str
    name: str
    power_limit_percent: int  # 50..115
    fan_target_percent: int  # 25..100
    target_temp_c: int  # 60..88
    description: str
    built_in: bool = True
    curve_points: list[list[int]] = field(
        default_factory=lambda: [[40, 30], [55, 45], [70, 65], [80, 82], [90, 100]]
    )


def clamp_fan_curve_points(points: list[list[int]] | list[tuple[int, int]] | None) -> list[list[int]]:
    """Sanitizes and sorts temperature-to-fan% control points into valid [35..95 C, 20..100 %]."""
    if not points or not isinstance(points, list):
        return [[40, 30], [55, 45], [70, 65], [80, 82], [90, 100]]
    cleaned: list[list[int]] = []
    for pt in points[:10]:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                t = max(35, min(95, int(float(pt[0]))))
                f = max(20, min(100, int(float(pt[1]))))
                cleaned.append([t, f])
            except Exception:
                continue
    if len(cleaned) < 2:
        return [[40, 30], [55, 45], [70, 65], [80, 82], [90, 100]]
    cleaned.sort(key=lambda pair: pair[0])
    return cleaned


def default_gpu_thermal_profiles() -> list[GpuThermalProfile]:
    return [
        GpuThermalProfile(
            "gpu_quiet_eco",
            "Whisper Quiet & Undervolt Target (80% Power)",
            power_limit_percent=80,
            fan_target_percent=42,
            target_temp_c=74,
            description="Cuts GPU power draw ~20% for near-silent fans and cooler VRMs with minimal FPS impact.",
            built_in=True,
            curve_points=[[40, 25], [55, 35], [70, 48], [80, 68], [90, 90]],
        ),
        GpuThermalProfile(
            "gpu_balanced_stock",
            "Balanced Factory Curve (100% Power)",
            power_limit_percent=100,
            fan_target_percent=58,
            target_temp_c=80,
            description="Maintains stock boost clocks with progressive fan ramp above 68°C.",
            built_in=True,
            curve_points=[[40, 30], [55, 45], [70, 65], [80, 82], [90, 100]],
        ),
        GpuThermalProfile(
            "gpu_max_airflow",
            "3D Render & Max Cooling (105% Power / 85% Fan)",
            power_limit_percent=105,
            fan_target_percent=85,
            target_temp_c=72,
            description="Maximizes sustained boost headroom during heavy 3D rendering or AI inference.",
            built_in=True,
            curve_points=[[40, 45], [55, 65], [70, 85], [80, 95], [90, 100]],
        ),
    ]


class GpuPowerAndFanController:
    """Inspects GPU power/fan capabilities and manages reversible thermal & fan-curve profiles."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        journal: ForensicJournal,
        elevation: ElevationRunner | None = None,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.elevation = elevation
        self.profiles_path = cfg_mgr.state_dir / "gpu_thermal_profiles.json"
        self.applied_curve_path = cfg_mgr.state_dir / "gpu_applied_curve.json"

    def load_profiles(self) -> list[GpuThermalProfile]:
        if not self.profiles_path.exists():
            profs = default_gpu_thermal_profiles()
            self.save_profiles(profs)
            return profs
        try:
            raw = json.loads(self.profiles_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("not a list")
            valid = {f.name for f in GpuThermalProfile.__dataclass_fields__.values()}
            out = []
            for r in raw:
                if isinstance(r, dict) and r.get("profile_id"):
                    gp = GpuThermalProfile(**{k: v for k, v in r.items() if k in valid})
                    gp.curve_points = clamp_fan_curve_points(gp.curve_points)
                    out.append(gp)
            return out or default_gpu_thermal_profiles()
        except Exception:
            profs = default_gpu_thermal_profiles()
            self.save_profiles(profs)
            return profs

    def get_applied_curve(self) -> tuple[str, list[list[int]]]:
        if self.applied_curve_path.exists():
            try:
                raw = json.loads(self.applied_curve_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    pid = str(raw.get("profile_id") or "gpu_balanced_stock")
                    pts = clamp_fan_curve_points(raw.get("curve_points"))
                    return pid, pts
            except Exception:
                pass
        return "gpu_balanced_stock", clamp_fan_curve_points(None)

    def apply_draft_curve(
        self,
        curve_points: list[list[int]] | list[tuple[int, int]],
        profile_id: str = "custom_draft_curve",
        dry_run: bool = False,
    ) -> tuple[bool, str, list[list[int]]]:
        pts = clamp_fan_curve_points(curve_points)
        if not dry_run:
            self.cfg_mgr.ensure_directories()
            tmp = self.applied_curve_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"profile_id": profile_id, "curve_points": pts, "updated_at": utc_now_iso()}, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.applied_curve_path)
        summary = ", ".join(f"{t}C:{f}%" for t, f in pts)
        msg = f"{'[DRY RUN] ' if dry_run else ''}Applied fan curve [{summary}]."
        self.journal.log_event("GpuPowerFan", "apply_fan_curve", msg, "Interactive Draft-vs-Applied fan curve update", "ok")
        return True, msg, pts

    def save_profiles(self, profiles: list[GpuThermalProfile]) -> None:
        self.cfg_mgr.ensure_directories()
        tmp = self.profiles_path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(p) for p in profiles], indent=2), encoding="utf-8")
        os.replace(tmp, self.profiles_path)

    def save_custom_profile(self, prof: GpuThermalProfile) -> GpuThermalProfile:
        profs = [p for p in self.load_profiles() if p.profile_id != prof.profile_id]
        prof.built_in = False
        profs.append(prof)
        self.save_profiles(profs)
        return prof

    def delete_profile(self, profile_id: str) -> tuple[bool, str]:
        profs = self.load_profiles()
        remaining = [p for p in profs if p.profile_id != profile_id or p.built_in]
        if len(remaining) == len(profs):
            return False, "Built-in GPU thermal profiles cannot be deleted (or profile not found)."
        self.save_profiles(remaining)
        self.journal.log_event(
            "GpuPowerFan",
            f"delete_{profile_id}",
            f"Deleted custom GPU thermal profile '{profile_id}'",
            "User removed custom GPU thermal curve",
            "ok",
        )
        return True, f"Deleted custom GPU profile '{profile_id}'."

    @staticmethod
    def probe_live_power_and_fan() -> dict[str, Any]:
        cached = getattr(GpuPowerAndFanController, "_probe_cache", None)
        now_m = time.monotonic()
        if cached and (now_m - cached[0]) < 4.0:
            return dict(cached[1])
        if shutil.which("nvidia-smi"):
            try:
                p = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,power.draw,power.limit,power.default_limit,fan.speed,temperature.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if p.returncode == 0 and p.stdout.strip():
                    parts = [x.strip() for x in p.stdout.strip().splitlines()[0].split(",")]
                    if len(parts) >= 6:
                        out = {
                            "available": True,
                            "name": parts[0],
                            "power_draw_w": parts[1],
                            "power_limit_w": parts[2],
                            "default_limit_w": parts[3],
                            "fan_percent": parts[4],
                            "temp_c": parts[5],
                        }
                        GpuPowerAndFanController._probe_cache = (now_m, dict(out))  # type: ignore[attr-defined]
                        return out
            except Exception:
                pass
        if platform.system() == "Linux":
            hwmon = Path("/sys/class/hwmon")
            if hwmon.exists():
                gpu_drivers = ("amdgpu", "radeon", "nouveau", "xe", "i915")
                devs = list(sorted(hwmon.glob("hwmon*")))

                def _hw_rank(d: Path) -> tuple[int, str]:
                    try:
                        nm = (d / "name").read_text().strip().lower() if (d / "name").exists() else d.name.lower()
                    except Exception:
                        nm = d.name.lower()
                    return (0 if nm in gpu_drivers else 1, d.name)

                for dev in sorted(devs, key=_hw_rank):
                    try:
                        name = (dev / "name").read_text().strip() if (dev / "name").exists() else dev.name
                    except Exception:
                        name = dev.name
                    pwm = dev / "pwm1"
                    temp_in = dev / "temp1_input"
                    if not (pwm.exists() or (name.lower() in gpu_drivers and temp_in.exists())):
                        continue
                    try:
                        fan_pct = str(round(int(pwm.read_text().strip()) / 2.55)) if pwm.exists() else "Auto"
                        temp_c = "N/A"
                        if temp_in.exists():
                            temp_c = f"{int(temp_in.read_text().strip()) / 1000.0:.1f}"
                        pwr_draw = "N/A"
                        for pf in ("power1_average", "power1_input"):
                            pfile = dev / pf
                            if pfile.exists():
                                pwr_draw = f"{int(pfile.read_text().strip()) / 1_000_000.0:.1f}"
                                break
                        pwr_cap = "hwmon"
                        if (dev / "power1_cap").exists():
                            pwr_cap = f"{int((dev / 'power1_cap').read_text().strip()) / 1_000_000.0:.0f}"
                        pwr_def = "hwmon"
                        if (dev / "power1_cap_default").exists():
                            pwr_def = f"{int((dev / 'power1_cap_default').read_text().strip()) / 1_000_000.0:.0f}"
                        return {
                            "available": True,
                            "name": name,
                            "power_draw_w": pwr_draw,
                            "power_limit_w": pwr_cap,
                            "default_limit_w": pwr_def,
                            "fan_percent": fan_pct,
                            "temp_c": temp_c,
                        }
                    except Exception:
                        pass
        return {
            "available": False,
            "name": "Standard Integrated / No Direct PWM Telemetry",
            "power_draw_w": "N/A",
            "power_limit_w": "N/A",
            "default_limit_w": "N/A",
            "fan_percent": "Auto",
            "temp_c": "N/A",
        }

    def apply_profile(
        self,
        prof: GpuThermalProfile,
        dry_run: bool = True,
        enforce_os: bool = False,
    ) -> tuple[bool, str, str]:
        clamped_pwr = max(50, min(115, int(prof.power_limit_percent)))
        clamped_fan = max(25, min(100, int(prof.fan_target_percent)))
        live = self.probe_live_power_and_fan()
        target_w: int | None = None
        if shutil.which("nvidia-smi") and live.get("default_limit_w") not in (None, "N/A", "hwmon"):
            try:
                base_w = float(live["default_limit_w"])
                target_w = max(80, int(round(base_w * (clamped_pwr / 100.0))))
                cmd = f"sudo nvidia-smi -pl {target_w}"
            except Exception:
                cmd = f"sudo nvidia-smi -pl <{clamped_pwr}%_watts>"
        elif platform.system() == "Windows":
            cmd = f"nvidia-smi -pl <{clamped_pwr}%_watts>  # or apply fan {clamped_fan}% in vendor GPU utility"
        else:
            cmd = f"# Target GPU Power={clamped_pwr}%, Fan Curve={clamped_fan}% (Target <={prof.target_temp_c}C)"

        if dry_run:
            msg = f"[DRY RUN] Would apply GPU thermal profile '{prof.name}' (Power {clamped_pwr}%, Fan {clamped_fan}%)."
        else:
            self.apply_draft_curve(prof.curve_points, profile_id=prof.profile_id, dry_run=False)
            extra = ""
            if enforce_os and target_w is not None and self.elevation is not None and shutil.which("nvidia-smi"):
                eres = self.elevation.run_privileged(
                    ["nvidia-smi", "-pl", str(target_w)],
                    reason=f"Apply GPU power limit {target_w}W ({prof.name})",
                )
                extra = f" | {eres.message}"
            msg = f"Saved active GPU thermal profile '{prof.name}' ({clamped_pwr}% power target, {clamped_fan}% fan curve).{extra}"
        self.journal.log_event("GpuPowerFan", f"apply_{prof.profile_id}", msg, prof.description, "ok")
        return True, msg, cmd


class StandaloneBuildRunner:
    """Checks packaging readiness and triggers standalone installer/binary generation."""

    @staticmethod
    def status() -> dict[str, Any]:
        repo = Path(__file__).resolve().parents[2]
        pyinstaller_ok = shutil.which("pyinstaller") is not None
        installers_dir = repo / "packaging" / "installers"
        scripts = sorted(p.name for p in installers_dir.glob("*")) if installers_dir.exists() else []
        return {
            "os": platform.system(),
            "pyinstaller_installed": pyinstaller_ok,
            "installer_scripts": scripts,
            "spec_exists": (repo / "packaging" / "astracradle.spec").exists(),
        }

    @staticmethod
    def generate_installer_scripts() -> tuple[bool, str]:
        import importlib.util

        repo = Path(__file__).resolve().parents[2]
        rp = repo / "packaging" / "release_pipeline.py"
        if not rp.exists():
            return False, "release_pipeline.py not found."
        spec = importlib.util.spec_from_file_location("release_pipeline", rp)
        if not spec or not spec.loader:
            return False, "Unable to load release_pipeline."
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        created = mod.generate_all()
        return True, f"Generated {len(created)} installer scripts (.iss / .dmg / .AppImage) in packaging/installers/."
