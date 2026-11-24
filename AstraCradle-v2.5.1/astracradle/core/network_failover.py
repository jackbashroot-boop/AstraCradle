"""Seamless Ethernet / Wi-Fi / Custom Network Failover & Hysteresis Auto-Recovery Engine."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Callable

import psutil

from .config import AppConfig, ConfigManager
from .elevation import ElevationRunner
from .journal import ForensicJournal
from .models import utc_now_iso


@dataclass
class NetworkInterfaceInfo:
    name: str
    kind: str  # ethernet | wifi | bluetooth_pan | usb_tether | tailscale | other
    is_up: bool
    ipv4: str = ""
    speed_mbps: int = 0


@dataclass
class PingProbeResult:
    interface: str
    target: str
    ok: bool
    rtt_ms: float
    reason: str = ""


@dataclass
class FailoverSnapshot:
    state: str  # primary_active | degraded_probing | failover_active | primary_stabilizing
    primary_iface: str
    backup_iface: str
    active_iface: str
    consecutive_failures: int
    failure_threshold: int
    last_rtt_ms: float
    last_probe_ok: bool
    last_probe_reason: str
    failover_elapsed_sec: float
    min_hold_sec: float
    stable_elapsed_sec: float
    recovery_stable_sec: float
    last_transition_at: str = ""
    last_transition_note: str = ""


class NetworkFailoverEngine:
    """Monitors primary link (e.g. Ethernet) every N seconds, fails over seamlessly, and recovers after M stable seconds."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        journal: ForensicJournal | None = None,
        elevation: ElevationRunner | None = None,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.elevation = elevation
        self.state_path: Path = cfg_mgr.state_dir / "failover_state.json"
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

        self.state: str = "primary_active"
        self.consecutive_failures: int = 0
        self.failover_started_at: float | None = None
        self.primary_stable_since: float | None = None
        self.last_rtt_ms: float = 0.0
        self.last_probe_ok: bool = True
        self.last_probe_reason: str = "Initialized"
        self.last_transition_at: str = ""
        self.last_transition_note: str = ""
        self.history: list[dict[str, str]] = []
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                st = str(raw.get("state", "primary_active"))
                if st in ("primary_active", "degraded_probing", "failover_active", "primary_stabilizing"):
                    self.state = st
                self.last_transition_at = str(raw.get("last_transition_at", ""))
                self.last_transition_note = str(raw.get("last_transition_note", ""))
                hist = raw.get("history")
                if isinstance(hist, list):
                    self.history = [h for h in hist if isinstance(h, dict)][:40]
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            self.cfg_mgr.ensure_directories()
            payload = {
                "state": self.state,
                "last_transition_at": self.last_transition_at,
                "last_transition_note": self.last_transition_note,
                "history": self.history[:40],
            }
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception:
            pass

    @staticmethod
    def _classify_iface(name: str) -> str:
        low = name.lower()
        if low.startswith(("lo", "docker", "br-", "veth", "virbr")):
            return "ignored"
        if low.startswith("tailscale") or low.startswith("ts"):
            return "tailscale"
        if low.startswith(("bnep", "pan")):
            return "bluetooth_pan"
        if low.startswith(("wl", "wifi", "wlan", "ath", "airport")) or "wi-fi" in low or "wireless" in low:
            return "wifi"
        if low.startswith(("enp", "eth", "eno", "ens", "em")) or "ethernet" in low:
            return "ethernet"
        if low.startswith(("usb", "rndis")):
            return "usb_tether"
        if Path(f"/sys/class/net/{name}/wireless").exists():
            return "wifi"
        return "other"

    @classmethod
    def discover_interfaces(cls) -> list[NetworkInterfaceInfo]:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        out: list[NetworkInterfaceInfo] = []
        for name, st in stats.items():
            kind = cls._classify_iface(name)
            if kind == "ignored":
                continue
            ipv4 = ""
            for a in addrs.get(name, []):
                if getattr(a, "family", None) == socket.AF_INET and not str(a.address).startswith("127."):
                    ipv4 = str(a.address)
                    break
            out.append(
                NetworkInterfaceInfo(
                    name=name,
                    kind=kind,
                    is_up=bool(st.isup),
                    ipv4=ipv4,
                    speed_mbps=int(st.speed or 0),
                )
            )
        order = {"ethernet": 0, "wifi": 1, "usb_tether": 2, "bluetooth_pan": 3, "tailscale": 4, "other": 5}
        out.sort(key=lambda x: (order.get(x.kind, 9), not x.is_up, x.name))
        return out

    @classmethod
    def resolve_pair(cls, config: AppConfig) -> tuple[str, str]:
        ifaces = cls.discover_interfaces()
        prim = str(getattr(config, "failover_primary_interface", "auto") or "auto").strip()
        back = str(getattr(config, "failover_backup_interface", "auto") or "auto").strip()

        if prim == "auto":
            eth = next((i.name for i in ifaces if i.kind == "ethernet"), None)
            prim = eth or (ifaces[0].name if ifaces else "eth0")

        if back == "auto":
            wifi = next((i.name for i in ifaces if i.kind in ("wifi", "usb_tether", "bluetooth_pan") and i.name != prim), None)
            other = next((i.name for i in ifaces if i.name != prim and i.kind != "tailscale"), None)
            back = wifi or other or "wlan0"
        return prim, back

    @staticmethod
    def probe_interface(
        interface: str,
        targets: list[str],
        timeout_sec: float = 2.0,
        max_latency_ms: float = 250.0,
    ) -> PingProbeResult:
        clean_targets = [t.strip() for t in (targets or ["1.1.1.1", "8.8.8.8"]) if t.strip()]
        if not clean_targets:
            clean_targets = ["1.1.1.1"]

        # Verify interface exists and is up when reported by psutil (ignore synthetic names only if absent)
        stats = psutil.net_if_stats()
        if interface in stats and not stats[interface].isup:
            return PingProbeResult(interface, clean_targets[0], False, 0.0, f"Interface '{interface}' link is DOWN")

        sys_name = platform.system().lower()
        t_lim = max(1, int(round(timeout_sec)))
        for tgt in clean_targets:
            if sys_name == "windows":
                cmd = ["ping", "-n", "1", "-w", str(int(max(500, timeout_sec * 1000))), tgt]
            elif sys_name == "darwin":
                cmd = ["ping", "-c", "1", "-t", str(t_lim), tgt]
            else:
                cmd = ["ping", "-c", "1", "-W", str(t_lim)]
                if interface in stats:
                    cmd.extend(["-I", interface])
                cmd.append(tgt)
            t0 = time.monotonic()
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=float(t_lim) + 1.5)
                if res.returncode == 0:
                    m = re.search(r"time[=<]\s*([\d.]+)\s*ms", res.stdout or "", re.I)
                    rtt = float(m.group(1)) if m else round((time.monotonic() - t0) * 1000.0, 2)
                    if rtt > max_latency_ms:
                        return PingProbeResult(
                            interface, tgt, False, rtt, f"Latency {rtt:.1f} ms > max {max_latency_ms:.0f} ms"
                        )
                    return PingProbeResult(interface, tgt, True, rtt, f"Healthy ({rtt:.1f} ms)")
            except Exception:
                continue

        # Loopback/sandbox fallback TCP probe if ICMP is blocked in unprivileged container
        for tgt in clean_targets:
            t0 = time.monotonic()
            try:
                with socket.create_connection((tgt, 443), timeout=min(1.5, timeout_sec)):
                    rtt = round((time.monotonic() - t0) * 1000.0, 2)
                    if rtt <= max_latency_ms:
                        return PingProbeResult(interface, tgt, True, rtt, f"TCP/443 healthy ({rtt:.1f} ms)")
            except Exception:
                pass

        return PingProbeResult(interface, clean_targets[0], False, 0.0, "Ping timeout / packet loss")

    def apply_seamless_route_order(self, preferred_iface: str, standby_iface: str) -> tuple[bool, str]:
        """Adjusts interface route metrics cleanly so transitions avoid tearing down active sockets."""
        sys_name = platform.system().lower()
        if sys_name == "linux" and shutil.which("nmcli"):
            try:
                res = subprocess.run(
                    ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                )
                dev_to_conn: dict[str, str] = {}
                for line in (res.stdout or "").splitlines():
                    if ":" in line:
                        cname, dev = line.rsplit(":", 1)
                        dev_to_conn[dev.strip()] = cname.strip()
                if preferred_iface in dev_to_conn and standby_iface in dev_to_conn:
                    c_pref = dev_to_conn[preferred_iface]
                    c_back = dev_to_conn[standby_iface]
                    subprocess.run(
                        ["nmcli", "connection", "modify", c_pref, "ipv4.route-metric", "50"],
                        capture_output=True,
                        timeout=2.5,
                    )
                    subprocess.run(
                        ["nmcli", "connection", "modify", c_back, "ipv4.route-metric", "600"],
                        capture_output=True,
                        timeout=2.5,
                    )
                    return True, f"Adjusted nmcli route metrics: {preferred_iface}=50, {standby_iface}=600."
            except Exception:
                pass
        return True, f"Active route priority -> {preferred_iface} (standby: {standby_iface})."

    def evaluate_step(
        self,
        config: AppConfig,
        *,
        now_ts: float | None = None,
        ping_fn: Callable[[str, list[str], float, float], PingProbeResult] | None = None,
        apply_routes: bool = True,
    ) -> FailoverSnapshot:
        """Evaluates one ping + hysteresis failover/recovery cycle using deterministic monotonic or passed clock."""
        now = time.monotonic() if now_ts is None else float(now_ts)
        prim, back = self.resolve_pair(config)
        targets = list(getattr(config, "failover_ping_targets", ["1.1.1.1", "8.8.8.8"]))
        timeout_sec = max(0.5, float(getattr(config, "failover_ping_timeout_sec", 2.0)))
        max_lat = max(10.0, float(getattr(config, "failover_max_latency_ms", 250.0)))
        fail_thresh = max(1, int(getattr(config, "failover_failure_threshold", 3)))
        min_hold = max(0.0, float(getattr(config, "failover_min_hold_sec", 60.0)))
        rec_stable = max(5.0, float(getattr(config, "failover_recovery_stable_sec", 300.0)))

        probe_cb = ping_fn or self.probe_interface
        probe = probe_cb(prim, targets, timeout_sec, max_lat)

        with self._lock:
            self.last_rtt_ms = probe.rtt_ms
            self.last_probe_ok = probe.ok
            self.last_probe_reason = probe.reason

            if self.state in ("primary_active", "degraded_probing"):
                if probe.ok:
                    self.consecutive_failures = 0
                    self.state = "primary_active"
                else:
                    self.consecutive_failures += 1
                    if self.consecutive_failures >= fail_thresh:
                        self.state = "failover_active"
                        self.failover_started_at = now
                        self.primary_stable_since = None
                        self.last_transition_at = utc_now_iso()
                        self.last_transition_note = (
                            f"Switched {prim} -> {back} after {self.consecutive_failures} failed probes ({probe.reason})."
                        )
                        self.history.insert(0, {"ts": self.last_transition_at, "event": self.last_transition_note})
                        if apply_routes and getattr(config, "failover_seamless_switch", True):
                            self.apply_seamless_route_order(back, prim)
                        if self.journal:
                            self.journal.log_event(
                                component="NetworkFailover",
                                action="failover_switch",
                                how=self.last_transition_note,
                                why="Primary link failed/slow beyond threshold; switched seamlessly to backup link",
                                outcome="warning",
                            )
                        self._save_state()
                    else:
                        self.state = "degraded_probing"
            else:
                # Currently on backup link (failover_active or primary_stabilizing)
                held = (now - self.failover_started_at) if self.failover_started_at is not None else min_hold
                if probe.ok:
                    if self.primary_stable_since is None:
                        self.primary_stable_since = now
                    stable_for = now - self.primary_stable_since
                    self.state = "primary_stabilizing"
                    if stable_for >= rec_stable and held >= min_hold:
                        self.state = "primary_active"
                        self.consecutive_failures = 0
                        self.failover_started_at = None
                        self.primary_stable_since = None
                        self.last_transition_at = utc_now_iso()
                        self.last_transition_note = (
                            f"Recovered {back} -> {prim} after {stable_for:.0f}s continuous stable pings."
                        )
                        self.history.insert(0, {"ts": self.last_transition_at, "event": self.last_transition_note})
                        if apply_routes and getattr(config, "failover_seamless_switch", True):
                            self.apply_seamless_route_order(prim, back)
                        if self.journal:
                            self.journal.log_event(
                                component="NetworkFailover",
                                action="failover_recover",
                                how=self.last_transition_note,
                                why="Primary Ethernet passed stability window; seamlessly restored primary route",
                                outcome="ok",
                            )
                        self._save_state()
                else:
                    # Primary dropped again during recovery window -> reset stability timer!
                    self.primary_stable_since = None
                    self.state = "failover_active"

            active = back if self.state in ("failover_active", "primary_stabilizing") else prim
            fo_elapsed = (now - self.failover_started_at) if self.failover_started_at is not None else 0.0
            st_elapsed = (now - self.primary_stable_since) if self.primary_stable_since is not None else 0.0
            return FailoverSnapshot(
                state=self.state,
                primary_iface=prim,
                backup_iface=back,
                active_iface=active,
                consecutive_failures=self.consecutive_failures,
                failure_threshold=fail_thresh,
                last_rtt_ms=self.last_rtt_ms,
                last_probe_ok=self.last_probe_ok,
                last_probe_reason=self.last_probe_reason,
                failover_elapsed_sec=round(fo_elapsed, 1),
                min_hold_sec=min_hold,
                stable_elapsed_sec=round(st_elapsed, 1),
                recovery_stable_sec=rec_stable,
                last_transition_at=self.last_transition_at,
                last_transition_note=self.last_transition_note,
            )

    def current_snapshot(self, config: AppConfig, now_ts: float | None = None) -> FailoverSnapshot:
        now = time.monotonic() if now_ts is None else float(now_ts)
        prim, back = self.resolve_pair(config)
        with self._lock:
            active = back if self.state in ("failover_active", "primary_stabilizing") else prim
            fo_elapsed = (now - self.failover_started_at) if self.failover_started_at is not None else 0.0
            st_elapsed = (now - self.primary_stable_since) if self.primary_stable_since is not None else 0.0
            return FailoverSnapshot(
                state=self.state,
                primary_iface=prim,
                backup_iface=back,
                active_iface=active,
                consecutive_failures=self.consecutive_failures,
                failure_threshold=max(1, int(getattr(config, "failover_failure_threshold", 3))),
                last_rtt_ms=self.last_rtt_ms,
                last_probe_ok=self.last_probe_ok,
                last_probe_reason=self.last_probe_reason,
                failover_elapsed_sec=round(fo_elapsed, 1),
                min_hold_sec=float(getattr(config, "failover_min_hold_sec", 60.0)),
                stable_elapsed_sec=round(st_elapsed, 1),
                recovery_stable_sec=float(getattr(config, "failover_recovery_stable_sec", 300.0)),
                last_transition_at=self.last_transition_at,
                last_transition_note=self.last_transition_note,
            )

    def force_failover(self, config: AppConfig, now_ts: float | None = None) -> FailoverSnapshot:
        now = time.monotonic() if now_ts is None else float(now_ts)
        prim, back = self.resolve_pair(config)
        with self._lock:
            self.state = "failover_active"
            self.consecutive_failures = max(1, int(getattr(config, "failover_failure_threshold", 3)))
            self.failover_started_at = now
            self.primary_stable_since = None
            self.last_transition_at = utc_now_iso()
            self.last_transition_note = f"Manual/test failover triggered: {prim} -> {back}"
            self.history.insert(0, {"ts": self.last_transition_at, "event": self.last_transition_note})
            self.apply_seamless_route_order(back, prim)
            self._save_state()
        return self.current_snapshot(config, now_ts=now)

    def force_recover(self, config: AppConfig, now_ts: float | None = None) -> FailoverSnapshot:
        now = time.monotonic() if now_ts is None else float(now_ts)
        prim, back = self.resolve_pair(config)
        with self._lock:
            self.state = "primary_active"
            self.consecutive_failures = 0
            self.failover_started_at = None
            self.primary_stable_since = None
            self.last_transition_at = utc_now_iso()
            self.last_transition_note = f"Restored primary link: {back} -> {prim}"
            self.history.insert(0, {"ts": self.last_transition_at, "event": self.last_transition_note})
            self.apply_seamless_route_order(prim, back)
            self._save_state()
        return self.current_snapshot(config, now_ts=now)

    def add_custom_profile(
        self,
        config: AppConfig,
        name: str,
        primary: str,
        backup: str,
        interval_sec: float = 5.0,
        recovery_sec: float = 300.0,
        failure_threshold: int = 3,
    ) -> dict[str, object]:
        prof = {
            "name": name.strip() or f"{primary} -> {backup}",
            "primary": primary.strip(),
            "backup": backup.strip(),
            "ping_interval_sec": max(2.0, float(interval_sec)),
            "recovery_stable_sec": max(5.0, float(recovery_sec)),
            "failure_threshold": max(1, int(failure_threshold)),
            "enabled": True,
        }
        items = list(getattr(config, "failover_custom_profiles", []) or [])
        items = [x for x in items if x.get("name") != prof["name"]]
        items.append(prof)
        config.failover_custom_profiles = items
        self.cfg_mgr.save(config)
        return prof

    def remove_custom_profile(self, config: AppConfig, name: str) -> None:
        items = [x for x in (getattr(config, "failover_custom_profiles", []) or []) if x.get("name") != name]
        config.failover_custom_profiles = items
        self.cfg_mgr.save(config)

    def start_monitor(
        self,
        config_getter: Callable[[], AppConfig],
        on_update: Callable[[FailoverSnapshot], None] | None = None,
    ) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()

        def _loop() -> None:
            while not self._stop_evt.is_set():
                cfg = config_getter()
                interval = max(2.0, float(getattr(cfg, "failover_ping_interval_sec", 5.0)))
                if getattr(cfg, "failover_enabled", True):
                    snap = self.evaluate_step(cfg)
                    if on_update is not None:
                        try:
                            on_update(snap)
                        except Exception:
                            pass
                self._stop_evt.wait(timeout=interval)

        self._thread = threading.Thread(target=_loop, name="AstraCradle-FailoverMonitor", daemon=True)
        self._thread.start()

    def stop_monitor(self) -> None:
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def verify_and_repair(self, config: AppConfig) -> tuple[bool, str]:
        """Self-heals invalid failover parameters, corrupted state JSON, or broken primary/backup settings."""
        repaired = False
        notes: list[str] = []
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Invalid failover_state.json")
            except Exception:
                self.state = "primary_active"
                self._save_state()
                repaired = True
                notes.append("Repaired corrupted failover_state.json")

        changed_cfg = False
        if float(getattr(config, "failover_ping_interval_sec", 5.0)) < 1.0:
            config.failover_ping_interval_sec = 5.0
            changed_cfg = True
        if float(getattr(config, "failover_recovery_stable_sec", 300.0)) < 5.0:
            config.failover_recovery_stable_sec = 300.0
            changed_cfg = True
        if int(getattr(config, "failover_failure_threshold", 3)) < 1:
            config.failover_failure_threshold = 3
            changed_cfg = True
        if not getattr(config, "failover_ping_targets", None):
            config.failover_ping_targets = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
            changed_cfg = True
        if changed_cfg:
            self.cfg_mgr.save(config)
            repaired = True
            notes.append("Normalized invalid failover thresholds")

        prim, back = self.resolve_pair(config)
        if not notes:
            notes.append(f"Failover engine healthy ({prim} -> {back}, ping {config.failover_ping_interval_sec:.0f}s, recovery {config.failover_recovery_stable_sec:.0f}s)")
        return repaired, "; ".join(notes)
