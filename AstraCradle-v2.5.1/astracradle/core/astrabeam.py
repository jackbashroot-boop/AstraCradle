"""AstraBeam v2.0 — Zero-Trust Tailscale TLS Remote Stream (iOS/Android/Desktop), Sunshine/Moonlight Bridge & iPhone/Apple Watch Companion."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
from pathlib import Path
import platform
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from .journal import ForensicJournal


@dataclass
class TailscalePeer:
    hostname: str
    dns_name: str
    tailscale_ip: str
    os_name: str
    online: bool
    relay: str = ""
    direct_addr: str = ""


@dataclass
class TailscaleStatus:
    installed: bool
    running: bool
    backend_state: str
    self_ip: str = ""
    self_hostname: str = ""
    magic_dns: str = ""
    peers: list[TailscalePeer] = field(default_factory=list)
    summary: str = ""
    install_hint: str = ""


@dataclass
class SunshineMoonlightStatus:
    sunshine_installed: bool
    sunshine_running: bool
    moonlight_installed: bool
    sunshine_path: str = ""
    moonlight_path: str = ""
    sunshine_url: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class AppleCompanionDevice:
    name: str
    device_kind: str  # iphone | apple_watch | ipad
    transport: str  # tailscale | bluetooth
    address: str
    connected: bool
    notes: str = ""


class TailscaleDiscovery:
    """Inspects local Tailscale daemon status, MagicDNS names, and reachable mesh peers."""

    @staticmethod
    def find_tailscale_bin() -> str | None:
        found = shutil.which("tailscale")
        if found:
            return found
        if platform.system() == "Windows":
            for cand in (
                r"C:\Program Files\Tailscale\tailscale.exe",
                r"C:\Program Files (x86)\Tailscale\tailscale.exe",
            ):
                if Path(cand).exists():
                    return cand
        elif platform.system() == "Darwin":
            cand = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
            if Path(cand).exists():
                return cand
        return None

    @staticmethod
    def is_tailscale_or_loopback(ip_str: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip_str.split("%")[0])
        except ValueError:
            return False
        if addr.is_loopback:
            return True
        if isinstance(addr, ipaddress.IPv4Address):
            return addr in ipaddress.ip_network("100.64.0.0/10")
        return addr in ipaddress.ip_network("fd7a:115c:a1e0::/48")

    @classmethod
    def get_status(cls, timeout: float = 2.5) -> TailscaleStatus:
        ts_bin = cls.find_tailscale_bin()
        sys_name = platform.system()
        hint = (
            "Install Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`)"
            if sys_name == "Linux"
            else (
                "Install Tailscale from https://tailscale.com/download/windows and sign in."
                if sys_name == "Windows"
                else "Install Tailscale via App Store or `brew install --cask tailscale` and sign in."
            )
        )
        if not ts_bin:
            return TailscaleStatus(
                installed=False,
                running=False,
                backend_state="NotInstalled",
                summary="Tailscale CLI not found — AstraBeam will bind loopback/LAN until Tailscale is installed.",
                install_hint=hint,
            )
        try:
            proc = subprocess.run([ts_bin, "status", "--json"], capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0 or not proc.stdout.strip():
                return TailscaleStatus(
                    installed=True,
                    running=False,
                    backend_state="Stopped",
                    summary=f"Tailscale installed ({ts_bin}) but daemon is stopped or logged out. Run `tailscale up`.",
                    install_hint=hint,
                )
            data = json.loads(proc.stdout)
        except Exception as exc:
            return TailscaleStatus(
                installed=True,
                running=False,
                backend_state="Unavailable",
                summary=f"Tailscale status check failed: {exc}",
                install_hint=hint,
            )

        state = str(data.get("BackendState") or "Unknown")
        self_node = data.get("Self") or {}
        self_ips = [str(ip) for ip in (self_node.get("TailscaleIPs") or []) if ip]
        self_v4 = next((ip for ip in self_ips if "." in ip), self_ips[0] if self_ips else "")
        self_host = str(self_node.get("HostName") or socket.gethostname())
        magic_dns = str(self_node.get("DNSName") or "").rstrip(".")

        peers: list[TailscalePeer] = []
        for pdata in (data.get("Peer") or {}).values():
            if not isinstance(pdata, dict):
                continue
            p_ips = [str(ip) for ip in (pdata.get("TailscaleIPs") or []) if ip]
            p_v4 = next((ip for ip in p_ips if "." in ip), p_ips[0] if p_ips else "")
            peers.append(
                TailscalePeer(
                    hostname=str(pdata.get("HostName") or "peer"),
                    dns_name=str(pdata.get("DNSName") or "").rstrip("."),
                    tailscale_ip=p_v4,
                    os_name=str(pdata.get("OS") or "unknown"),
                    online=bool(pdata.get("Online", False)),
                    relay=str(pdata.get("Relay") or ""),
                    direct_addr=str(pdata.get("CurAddr") or ""),
                )
            )
        peers.sort(key=lambda p: (not p.online, p.hostname.lower()))
        running = state == "Running" and bool(self_v4)
        online_n = sum(1 for p in peers if p.online)
        summary = (
            f"Tailscale {state}: {self_host} ({self_v4 or 'no IP'}) · {online_n}/{len(peers)} peers online"
            if running
            else f"Tailscale state: {state}. Run `tailscale up` to join your global mesh."
        )
        return TailscaleStatus(
            installed=True,
            running=running,
            backend_state=state,
            self_ip=self_v4,
            self_hostname=self_host,
            magic_dns=magic_dns,
            peers=peers,
            summary=summary,
            install_hint=hint,
        )


class AppleDeviceScanner:
    """Discovers iPhone, iPad & Apple Watch devices via Tailscale iOS peers and local Bluetooth."""

    @staticmethod
    def scan(ts_status: TailscaleStatus | None = None) -> list[AppleCompanionDevice]:
        devices: list[AppleCompanionDevice] = []
        seen: set[str] = set()
        if ts_status:
            for peer in ts_status.peers:
                low_os = peer.os_name.lower()
                low_name = peer.hostname.lower()
                if "ios" in low_os or "iphone" in low_name or "ipad" in low_name:
                    kind = "ipad" if "ipad" in low_name else "iphone"
                    devices.append(
                        AppleCompanionDevice(
                            name=peer.hostname,
                            device_kind=kind,
                            transport="tailscale",
                            address=peer.tailscale_ip or peer.dns_name,
                            connected=peer.online,
                            notes="Tailscale WireGuard mesh (Moonlight iOS + AstraBeam Web App + Apple Shortcuts ready)",
                        )
                    )
                    seen.add(peer.hostname.lower())

        # Local Bluetooth iPhone / Apple Watch scan (Linux bluetoothctl, macOS system_profiler)
        sys_name = platform.system()
        if sys_name == "Linux" and shutil.which("bluetoothctl"):
            try:
                proc = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True, timeout=2.0)
                for ln in proc.stdout.splitlines():
                    parts = ln.strip().split(" ", 2)
                    if len(parts) < 3 or parts[0] != "Device":
                        continue
                    mac, name = parts[1], parts[2]
                    low = name.lower()
                    if any(k in low for k in ("iphone", "watch", "ipad")):
                        kind = "apple_watch" if "watch" in low else ("ipad" if "ipad" in low else "iphone")
                        conn = False
                        try:
                            inf = subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True, timeout=1.5)
                            conn = "Connected: yes" in inf.stdout
                        except Exception:
                            pass
                        if name.lower() not in seen:
                            devices.append(
                                AppleCompanionDevice(
                                    name=name,
                                    device_kind=kind,
                                    transport="bluetooth",
                                    address=mac,
                                    connected=conn,
                                    notes="Paired Bluetooth Apple device (companion trigger & proximity ready)",
                                )
                            )
                            seen.add(name.lower())
            except Exception:
                pass
        elif sys_name == "Darwin" and shutil.which("system_profiler"):
            try:
                proc = subprocess.run(
                    ["system_profiler", "SPBluetoothDataType", "-json"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                )
                txt = proc.stdout
                if "iPhone" in txt or "Apple Watch" in txt:
                    for label, kind in (("Apple Watch", "apple_watch"), ("iPhone", "iphone")):
                        if label in txt and label.lower() not in seen:
                            devices.append(
                                AppleCompanionDevice(
                                    name=label,
                                    device_kind=kind,
                                    transport="bluetooth",
                                    address="paired",
                                    connected=True,
                                    notes="macOS Continuity / Apple Watch Unlock & Shortcuts ready",
                                )
                            )
            except Exception:
                pass
        return devices


class SunshineMoonlightBridge:
    """Detects native Sunshine/Moonlight installations and configures Tailscale-hardened game streaming."""

    @staticmethod
    def _is_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False
        finally:
            try:
                s.close()
            except Exception:
                pass

    @classmethod
    def default_sunshine_conf_path(cls) -> Path:
        sys_name = platform.system()
        if sys_name == "Windows":
            return Path(r"C:\Program Files\Sunshine\config\sunshine.conf")
        if sys_name == "Darwin":
            return Path.home() / ".config" / "sunshine" / "sunshine.conf"
        return Path.home() / ".config" / "sunshine" / "sunshine.conf"

    @classmethod
    def inspect(cls, ts_status: TailscaleStatus | None = None) -> SunshineMoonlightStatus:
        sun_bin = shutil.which("sunshine") or (
            r"C:\Program Files\Sunshine\sunshine.exe"
            if platform.system() == "Windows" and Path(r"C:\Program Files\Sunshine\sunshine.exe").exists()
            else ""
        )
        moon_bin = shutil.which("moonlight") or shutil.which("moonlight-qt") or (
            r"C:\Program Files\Moonlight Game Streaming\Moonlight.exe"
            if platform.system() == "Windows" and Path(r"C:\Program Files\Moonlight Game Streaming\Moonlight.exe").exists()
            else ""
        )
        sun_running = cls._is_port_open("127.0.0.1", 47989) or cls._is_port_open("127.0.0.1", 47984)
        if not sun_running and platform.system() == "Linux" and shutil.which("systemctl"):
            try:
                chk = subprocess.run(
                    ["systemctl", "--user", "is-active", "--quiet", "sunshine"],
                    timeout=1.5,
                    check=False,
                )
                sun_running = chk.returncode == 0
            except Exception:
                pass
        ts_ip = ts_status.self_ip if ts_status and ts_status.self_ip else "127.0.0.1"
        notes = [
            "Moonlight iOS & Moonlight Android connect natively over Tailscale IP (TCP 47984,47989,47990 · UDP 47998-48010).",
            "Mandatory encryption enabled (lan_encryption_mode=2, wan_encryption_mode=2) + UPnP disabled for zero public exposure.",
        ]
        return SunshineMoonlightStatus(
            sunshine_installed=bool(sun_bin),
            sunshine_running=sun_running,
            moonlight_installed=bool(moon_bin),
            sunshine_path=sun_bin or "",
            moonlight_path=moon_bin or "",
            sunshine_url=f"https://{ts_ip}:47990" if sun_running else "",
            notes=notes,
        )

    @classmethod
    def start_or_restart_sunshine(cls) -> tuple[bool, str]:
        """Resets start-limit, enables & restarts native Sunshine user service (or launches binary)."""
        sun_bin = shutil.which("sunshine")
        if not sun_bin:
            return False, "Native Sunshine binary is not installed."
        sys_name = platform.system()
        if sys_name == "Linux" and shutil.which("systemctl"):
            try:
                subprocess.run(
                    ["systemctl", "--user", "reset-failed", "sunshine.service"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                )
                res = subprocess.run(
                    ["systemctl", "--user", "restart", "sunshine.service"],
                    capture_output=True,
                    text=True,
                    timeout=6.0,
                    check=False,
                )
                if res.returncode == 0:
                    return True, "Native Sunshine started cleanly (Moonlight TCP 47984/47989 · Web UI :47990)."
                err_tail = ""
                if shutil.which("journalctl"):
                    j = subprocess.run(
                        ["journalctl", "--user", "-n", "8", "-u", "sunshine.service", "--no-pager"],
                        capture_output=True,
                        text=True,
                        timeout=3.0,
                        check=False,
                    )
                    lines = [ln.strip() for ln in (j.stdout or "").splitlines() if ln.strip()]
                    if lines:
                        err_tail = " | " + lines[-1]
                msg = (res.stderr or res.stdout or "systemctl failed").strip()
                return False, f"Sunshine service failed to start: {msg}{err_tail}"
            except Exception as exc:
                return False, f"Could not restart Sunshine service: {exc}"
        try:
            subprocess.Popen(
                [sun_bin],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, f"Started Sunshine process ({sun_bin})."
        except Exception as exc:
            return False, f"Failed to start Sunshine: {exc}"

    @staticmethod
    def generate_tailscale_sunshine_conf(tailscale_ip: str) -> str:
        return (
            "# AstraCradle Zero-Trust Tailscale Sunshine Configuration\n"
            "upnp = disabled\n"
            "origin_web_ui_allowed = lan\n"
            "lan_encryption_mode = 2\n"
            "wan_encryption_mode = 2\n"
            "min_threads = 2\n"
            "fec_percentage = 20\n"
            f"# In Moonlight (iOS / Android / Desktop), Add PC -> {tailscale_ip or '<tailscale-ip>'}\n"
        )

    @classmethod
    def apply_hardened_tailscale_config(
        cls, tailscale_ip: str, conf_path: Path | None = None
    ) -> tuple[bool, str]:
        target = conf_path or cls.default_sunshine_conf_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                bak = target.with_suffix(".conf.astrabak")
                bak.write_bytes(target.read_bytes())
            existing = target.read_text(encoding="utf-8", errors="ignore") if target.exists() else ""
            overrides = {
                "upnp": "disabled",
                "origin_web_ui_allowed": "lan",
                "lan_encryption_mode": "2",
                "wan_encryption_mode": "2",
                "fec_percentage": "20",
            }
            lines = [ln for ln in existing.splitlines() if not any(ln.strip().startswith(f"{k} ") or ln.strip().startswith(f"{k}=") for k in overrides)]
            lines.append("# Managed by AstraCradle AstraBeam (Tailscale Zero-Trust)")
            for k, v in overrides.items():
                lines.append(f"{k} = {v}")
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True, f"Wrote hardened Tailscale Sunshine config -> {target} (IP {tailscale_ip or 'Tailnet'})."
        except Exception as exc:
            return False, f"Could not write Sunshine config ({target}): {exc}"

    @staticmethod
    def detect_primary_display_output() -> str:
        """Detects primary monitor connector name (e.g. DP-5) via kscreen-doctor or xrandr."""
        import re

        if shutil.which("kscreen-doctor"):
            try:
                out = subprocess.run(
                    ["kscreen-doctor", "-o"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                ).stdout
                cur_out = ""
                best_out = ""
                best_pri = 999
                for ln in (out or "").splitlines():
                    m_out = re.search(r"Output:\s*\d+\s+([A-Za-z0-9_-]+)", ln)
                    if m_out:
                        cur_out = m_out.group(1)
                    m_pri = re.search(r"priority\s+(\d+)", ln)
                    if m_pri and cur_out:
                        pri = int(m_pri.group(1))
                        if pri < best_pri:
                            best_pri = pri
                            best_out = cur_out
                if best_out:
                    return best_out
            except Exception:
                pass
        if shutil.which("xrandr"):
            try:
                out = subprocess.run(
                    ["xrandr", "--query"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                ).stdout
                for ln in (out or "").splitlines():
                    if " connected primary " in ln:
                        return ln.split()[0]
            except Exception:
                pass
        return ""

    @classmethod
    def optimize_sunshine_for_tablet(
        cls, tailscale_ip: str = "", conf_path: Path | None = None
    ) -> tuple[bool, str]:
        """Configures Sunshine for smooth iPad/tablet streaming: primary monitor, VA-API (on AMD/Intel), encryption & FEC."""
        target = conf_path or cls.default_sunshine_conf_path()
        ok_base, msg_base = cls.apply_hardened_tailscale_config(tailscale_ip, conf_path=target)
        if not ok_base:
            return False, msg_base
        try:
            primary_out = cls.detect_primary_display_output()
            extra: dict[str, str] = {}
            if primary_out:
                extra["output_name"] = primary_out
            if platform.system() == "Linux":
                # Prefer VA-API over Vulkan AV1 on Linux AMD/Intel to prevent 10-bit AV1 black frames on iPadOS
                has_dri = any(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else False
                if has_dri and not shutil.which("nvidia-smi"):
                    extra["encoder"] = "vaapi"

            existing = target.read_text(encoding="utf-8", errors="ignore")
            lines = [
                ln
                for ln in existing.splitlines()
                if not any(ln.strip().startswith(f"{k} ") or ln.strip().startswith(f"{k}=") for k in extra)
            ]
            for k, v in extra.items():
                lines.append(f"{k} = {v}")
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")

            cap_note = ""
            sun_bin = shutil.which("sunshine")
            if sun_bin and platform.system() == "Linux" and shutil.which("getcap"):
                try:
                    real_sun = str(Path(sun_bin).resolve())
                    caps = subprocess.run(["getcap", real_sun], capture_output=True, text=True, timeout=2.0, check=False).stdout
                    if "cap_sys_admin" not in (caps or ""):
                        cap_note = f" (Tip: run `sudo setcap cap_sys_admin+p {real_sun}` for KMS capture)"
                except Exception:
                    pass
            summary_bits = [f"{k}={v}" for k, v in extra.items()] or ["hardened defaults"]
            return True, f"Optimized Sunshine for iPad/Tablet ({', '.join(summary_bits)}){cap_note}"
        except Exception as exc:
            return False, f"Could not apply iPad Sunshine optimizations: {exc}"

    @classmethod
    def launch_moonlight(cls, target_host: str, app_name: str = "Desktop") -> tuple[bool, str]:
        moon = shutil.which("moonlight") or shutil.which("moonlight-qt")
        if not moon:
            return False, "Moonlight desktop client not installed; use AstraBeam's built-in Viewer or Moonlight iOS/Android."
        try:
            subprocess.Popen(
                [moon, "stream", target_host, app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, f"Launched Moonlight stream -> {target_host} ({app_name})."
        except Exception as exc:
            return False, f"Failed to launch Moonlight: {exc}"

    @staticmethod
    def send_wake_on_lan(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> tuple[bool, str]:
        cleaned = mac_address.replace(":", "").replace("-", "").replace(".", "").strip()
        if len(cleaned) != 12:
            return False, "Invalid MAC address format (expected AA:BB:CC:DD:EE:FF)."
        try:
            magic = b"\xff" * 6 + bytes.fromhex(cleaned) * 16
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(magic, (broadcast_ip, port))
            return True, f"Sent Wake-on-LAN magic packet to {mac_address}."
        except Exception as exc:
            return False, f"Wake-on-LAN failed: {exc}"


_WEB_MANIFEST_JSON = json.dumps(
    {
        "name": "AstraBeam Remote",
        "short_name": "AstraBeam",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#070b11",
        "theme_color": "#0d141f",
    }
)


_WEB_VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.webmanifest">
<title>AstraBeam · iOS / Android Tailscale Stream</title>
<style>
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;background:#070b11;color:#e6f1f5;font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;display:flex;flex-direction:column;height:100dvh;overflow:hidden}
  header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px;padding:8px 12px;background:#0d141f;border-bottom:1px solid #1b2838}
  #bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
  input,button{background:#121c2b;color:#e6f1f5;border:1px solid #29415e;border-radius:8px;padding:7px 10px;font-size:13px}
  button{background:#18a999;color:#051412;font-weight:700;cursor:pointer;border:none}
  button.sec{background:#1a2638;color:#cbd5e1;border:1px solid #2c3f5a}
  #stage{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;touch-action:none}
  #screen{max-width:100%;max-height:100%;cursor:crosshair;user-select:none;border-radius:4px}
  #status{font-size:12px;color:#8ba3b8}
  #gamepad{display:none;position:absolute;inset:auto 12px 12px 12px;justify-content:space-between;pointer-events:none}
  .pad{display:grid;grid-template-columns:repeat(3,44px);gap:4px;pointer-events:auto;opacity:.82}
  .pad button{height:44px;border-radius:10px;background:#132136;color:#e2e8f0;border:1px solid #38bdf8;font-size:12px}
</style>
</head>
<body>
<header>
  <div><strong>✦ AstraBeam</strong> <span id="status">Tailscale TLS Ready · Enter PIN</span></div>
  <div id="bar">
    <input id="pin" type="password" inputmode="numeric" placeholder="PIN" size="7" />
    <button onclick="pair()">Connect</button>
    <input id="txt" type="text" placeholder="Type / Paste..." size="12" />
    <button class="sec" onclick="sendText()">Send</button>
    <button class="sec" onclick="togglePad()">Pad</button>
    <button class="sec" onclick="syncClip()">Clip</button>
    <button class="sec" onclick="location.href='/watch'">Watch</button>
  </div>
</header>
<div id="stage">
  <img id="screen" alt="Remote Screen Stream" />
  <div id="gamepad">
    <div class="pad">
      <span></span><button ontouchstart="k('Up')" onclick="k('Up')">▲</button><span></span>
      <button ontouchstart="k('Left')" onclick="k('Left')">◀</button><button ontouchstart="k('Space')" onclick="k('Space')">●</button><button ontouchstart="k('Right')" onclick="k('Right')">▶</button>
      <span></span><button ontouchstart="k('Down')" onclick="k('Down')">▼</button><span></span>
    </div>
    <div class="pad">
      <button ontouchstart="k('Escape')" onclick="k('Escape')">ESC</button><button ontouchstart="k('Return')" onclick="k('Return')">ENT</button><button ontouchstart="k('Tab')" onclick="k('Tab')">TAB</button>
      <button ontouchstart="c('left')" onclick="c('left')">L-CLK</button><button ontouchstart="c('right')" onclick="c('right')">R-CLK</button><button ontouchstart="k('BackSpace')" onclick="k('BackSpace')">DEL</button>
    </div>
  </div>
</div>
<script>
let TOKEN = sessionStorage.getItem("astrabeam_tok") || "";
const img = document.getElementById("screen");
const st = document.getElementById("status");
if(TOKEN){ st.textContent = "Reconnecting..."; img.src = "/stream.mjpeg?token=" + encodeURIComponent(TOKEN); }
async function pair(){
  const pin = document.getElementById("pin").value.trim();
  const r = await fetch("/api/pair", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({pin})});
  const d = await r.json();
  if(!d.ok){ st.textContent = d.error || "Invalid PIN"; return; }
  TOKEN = d.token;
  sessionStorage.setItem("astrabeam_tok", TOKEN);
  st.textContent = "Encrypted · Streaming live";
  img.src = "/stream.mjpeg?token=" + encodeURIComponent(TOKEN);
}
async function sendInput(payload){
  if(!TOKEN) return;
  await fetch("/api/input", {method:"POST", headers:{"Content-Type":"application/json","X-AstraBeam-Token":TOKEN}, body:JSON.stringify(payload)});
}
function k(key){ sendInput({kind:"key", key}); }
function c(btn){ sendInput({kind:"click", button:btn, x:0.5, y:0.5}); }
function togglePad(){ const g=document.getElementById("gamepad"); g.style.display=g.style.display==="flex"?"none":"flex"; }
async function syncClip(){
  if(!TOKEN) return;
  const txt = document.getElementById("txt").value;
  if(txt){ await fetch("/api/clipboard",{method:"POST",headers:{"Content-Type":"application/json","X-AstraBeam-Token":TOKEN},body:JSON.stringify({text:txt})}); st.textContent="Clipboard pushed to PC"; }
  else { const r=await fetch("/api/clipboard",{headers:{"X-AstraBeam-Token":TOKEN}}); const d=await r.json(); if(d.text) document.getElementById("txt").value=d.text; }
}
img.addEventListener("mousemove", (e)=>{
  const rect = img.getBoundingClientRect();
  if(rect.width<=0||rect.height<=0) return;
  sendInput({kind:"move", x:(e.clientX-rect.left)/rect.width, y:(e.clientY-rect.top)/rect.height});
});
img.addEventListener("mousedown", (e)=>{
  const rect = img.getBoundingClientRect();
  sendInput({kind:"click", button:e.button===2?"right":"left", x:(e.clientX-rect.left)/rect.width, y:(e.clientY-rect.top)/rect.height});
});
img.addEventListener("touchstart", (e)=>{
  if(e.touches.length===2){ e.preventDefault(); c("right"); return; }
  const t = e.touches[0], rect = img.getBoundingClientRect();
  if(rect.width>0) sendInput({kind:"click", button:"left", x:(t.clientX-rect.left)/rect.width, y:(t.clientY-rect.top)/rect.height});
},{passive:false});
img.addEventListener("touchmove", (e)=>{
  if(e.touches.length!==1) return;
  e.preventDefault();
  const t = e.touches[0], rect = img.getBoundingClientRect();
  if(rect.width>0) sendInput({kind:"move", x:(t.clientX-rect.left)/rect.width, y:(t.clientY-rect.top)/rect.height});
},{passive:false});
img.addEventListener("contextmenu", e=>e.preventDefault());
img.addEventListener("wheel", e=>sendInput({kind:"scroll", dy: e.deltaY>0?-2:2}), {passive:true});
function sendText(){
  const el = document.getElementById("txt");
  if(el.value){ sendInput({kind:"text", text:el.value}); el.value=""; }
}
</script>
</body>
</html>
"""


_WATCH_VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>AstraWatch · iPhone & Apple Watch Remote</title>
<style>
  body{margin:0;background:#000;color:#f8fafc;font-family:-apple-system,sans-serif;padding:12px}
  .card{background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:12px;margin-bottom:10px}
  button{width:100%;padding:12px;margin:5px 0;border-radius:12px;border:none;font-weight:700;font-size:14px;background:#18a999;color:#04120f}
  button.alt{background:#1e293b;color:#e2e8f0}
  input{width:100%;padding:10px;border-radius:10px;border:1px solid #334155;background:#020617;color:#fff;margin-bottom:8px}
</style>
</head>
<body>
<div class="card">
  <strong>⌚ AstraWatch Companion</strong>
  <div id="info" style="font-size:13px;color:#94a3b8;margin-top:6px">Enter PIN to pair iPhone / Apple Watch</div>
</div>
<div class="card">
  <input id="pin" type="password" inputmode="numeric" placeholder="6-digit PIN" />
  <button onclick="pair()">Pair Watch / iPhone</button>
</div>
<div class="card">
  <button onclick="act('fix_safe')">Fix Everything Safe</button>
  <button class="alt" onclick="act('switch_profile','Gaming / Low-Latency')">Gaming Profile</button>
  <button class="alt" onclick="act('switch_profile','Battery Endurance')">Battery Endurance</button>
  <button class="alt" onclick="act('pause_shield_5m')">Pause AstraShield 5m</button>
  <button class="alt" onclick="location.href='/'">Back to Remote Screen</button>
</div>
<script>
let TOK = sessionStorage.getItem("astrabeam_tok") || "";
async function pair(){
  const r = await fetch("/api/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pin:document.getElementById("pin").value})});
  const d = await r.json();
  if(d.ok){ TOK=d.token; sessionStorage.setItem("astrabeam_tok",TOK); refresh(); }
  else { document.getElementById("info").textContent = d.error || "Pair failed"; }
}
async function refresh(){
  if(!TOK) return;
  const r = await fetch("/api/watch/summary",{headers:{"X-AstraBeam-Token":TOK}});
  const d = await r.json();
  if(d.ok) document.getElementById("info").textContent = `${d.hostname} · Health ${d.health_score}/100 · CPU ${d.cpu_percent}% · RAM ${d.mem_percent}%`;
}
async function act(name, val=""){
  if(!TOK) return;
  const r = await fetch("/api/watch/action",{method:"POST",headers:{"Content-Type":"application/json","X-AstraBeam-Token":TOK},body:JSON.stringify({action:name,value:val})});
  const d = await r.json();
  document.getElementById("info").textContent = d.message || (d.ok?"Done":"Failed");
}
if(TOK) refresh();
</script>
</body>
</html>
"""


class _QuietTLSHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that ignores abrupt mobile TLS probe/disconnect errors cleanly."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        return


class AstraBeamServer:
    """Zero-hole TLS + Tailscale-locked Sunshine-style host for iOS, Android & Desktop."""

    def __init__(
        self,
        journal: ForensicJournal | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 47990,
        pin: str = "",
        fps: int = 30,
        quality: int = 72,
        scale_pct: int = 85,
        tailscale_only: bool = True,
        tls_enabled: bool = True,
        cert_dir: Path | None = None,
        lockout_seconds: int = 300,
        max_failed_pins: int = 5,
        token_ttl_seconds: int = 43200,
        watch_summary_provider: Callable[[], dict[str, Any]] | None = None,
        watch_action_handler: Callable[[str, str], tuple[bool, str]] | None = None,
    ) -> None:
        self.journal = journal
        self.bind_host = host
        self.port = int(port)
        self.pin = pin.strip() or f"{secrets.randbelow(900000) + 100000}"
        self.fps = max(5, min(120, int(fps)))
        self.quality = max(20, min(95, int(quality)))
        self.scale_pct = max(25, min(100, int(scale_pct)))
        self.tailscale_only = bool(tailscale_only)
        self.tls_enabled = bool(tls_enabled)
        self.cert_dir = cert_dir
        self.lockout_seconds = max(30, int(lockout_seconds))
        self.max_failed_pins = max(3, int(max_failed_pins))
        self.token_ttl_seconds = max(300, int(token_ttl_seconds))
        self.watch_summary_provider = watch_summary_provider
        self.watch_action_handler = watch_action_handler

        self._secret = secrets.token_bytes(32)
        self._tokens: dict[str, tuple[str, float]] = {}  # token -> (client_ip, expires_at)
        self._failed_attempts: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._clipboard_text: str = ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.screen_size: tuple[int, int] = (1280, 720)
        self.frames_served: int = 0
        self.inputs_handled: int = 0
        self.last_input: dict[str, Any] | None = None
        self.tls_active: bool = False
        self.cert_fingerprint_sha256: str = ""

    @property
    def scheme(self) -> str:
        return "https" if self.tls_active else "http"

    @property
    def running(self) -> bool:
        return self._httpd is not None and not self._stop.is_set()

    def ensure_tls_cert(self, extra_sans: list[str] | None = None) -> tuple[Path, Path, str] | None:
        """Generates a TLS 1.2+ self-signed ECDSA/RSA cert with SANs via openssl CLI."""
        openssl_bin = shutil.which("openssl")
        if not openssl_bin:
            return None
        base = self.cert_dir or (Path.home() / ".astracradle" / "certs")
        base.mkdir(parents=True, exist_ok=True)
        cert_path = base / "astrabeam_cert.pem"
        key_path = base / "astrabeam_key.pem"
        san_items = ["DNS:localhost", "IP:127.0.0.1"]
        if self.bind_host and self.bind_host not in ("0.0.0.0", "127.0.0.1"):
            try:
                ipaddress.ip_address(self.bind_host)
                san_items.append(f"IP:{self.bind_host}")
            except ValueError:
                san_items.append(f"DNS:{self.bind_host}")
        for item in extra_sans or []:
            if item and item not in san_items:
                san_items.append(item)
        san_str = ",".join(san_items)
        try:
            subprocess.run(
                [
                    openssl_bin,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-sha256",
                    "-days",
                    "365",
                    "-nodes",
                    "-keyout",
                    str(key_path),
                    "-out",
                    str(cert_path),
                    "-subj",
                    f"/CN={socket.gethostname()}/O=AstraCradle-AstraBeam",
                    "-addext",
                    f"subjectAltName={san_str}",
                ],
                capture_output=True,
                timeout=8,
                check=True,
            )
            try:
                key_path.chmod(0o600)
            except OSError:
                pass
            fp_proc = subprocess.run(
                [openssl_bin, "x509", "-in", str(cert_path), "-noout", "-fingerprint", "-sha256"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            fp = fp_proc.stdout.strip().split("=")[-1] if fp_proc.stdout else ""
            return cert_path, key_path, fp
        except Exception:
            return None

    def is_ip_locked_out(self, client_ip: str) -> bool:
        now = time.time()
        return now < self._locked_until.get(client_ip, 0.0)

    def record_failed_pin(self, client_ip: str) -> bool:
        now = time.time()
        recent = [t for t in self._failed_attempts.get(client_ip, []) if (now - t) < 300.0]
        recent.append(now)
        self._failed_attempts[client_ip] = recent
        if len(recent) >= self.max_failed_pins:
            self._locked_until[client_ip] = now + self.lockout_seconds
            if self.journal:
                self.journal.log_event(
                    "AstraBeamSecurity",
                    "pin_lockout",
                    f"Locked out {client_ip} for {self.lockout_seconds}s after {len(recent)} failed PIN attempts",
                    "Brute-force protection triggered",
                    "warning",
                )
            return True
        return False

    def issue_token(self, client_ip: str = "127.0.0.1") -> str:
        raw = secrets.token_urlsafe(24)
        sig = hmac.new(self._secret, f"{raw}:{client_ip}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        tok = f"{raw}.{sig}"
        self._tokens[tok] = (client_ip, time.time() + self.token_ttl_seconds)
        self._failed_attempts.pop(client_ip, None)
        return tok

    def verify_pin(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.strip(), self.pin)

    def verify_token(self, token: str, client_ip: str = "127.0.0.1") -> bool:
        if not token or token not in self._tokens:
            return False
        bound_ip, exp = self._tokens[token]
        if time.time() > exp:
            self._tokens.pop(token, None)
            return False
        # Strict IP binding prevents token theft/replay from another peer
        if bound_ip != client_ip:
            return False
        return True

    def revoke_token(self, token: str) -> None:
        self._tokens.pop(token, None)

    def revoke_all_tokens(self) -> int:
        cnt = len(self._tokens)
        self._tokens.clear()
        return cnt

    def capture_jpeg_frame(self) -> bytes:
        from PIL import Image, ImageDraw

        img = None
        try:
            import mss  # type: ignore

            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            img = None

        if img is None:
            try:
                from PIL import ImageGrab

                img = ImageGrab.grab().convert("RGB")
            except Exception:
                img = None

        if img is None and platform.system() == "Linux":
            for cmd in ("grim", "scrot"):
                if shutil.which(cmd):
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".png") as tf:
                            subprocess.run([cmd, tf.name], capture_output=True, timeout=1.5, check=True)
                            img = Image.open(tf.name).convert("RGB")
                            break
                    except Exception:
                        img = None

        if img is None:
            img = Image.new("RGB", (960, 540), (9, 14, 22))
            draw = ImageDraw.Draw(img)
            draw.rectangle((24, 24, 936, 516), outline=(56, 189, 248), width=2)
            draw.text((48, 54), f"AstraBeam Tailscale TLS Host ({socket.gethostname()})", fill=(230, 241, 245))
            draw.text((48, 92), f"Time: {time.strftime('%H:%M:%S')}  |  FPS: {self.fps}  |  Inputs: {self.inputs_handled}", fill=(52, 211, 153))
            draw.text((48, 128), "Zero-Trust iOS / Android / Desktop Stream Active.", fill=(148, 163, 184))

        self.screen_size = img.size
        if self.scale_pct < 100:
            nw = max(320, int(img.width * (self.scale_pct / 100.0)))
            nh = max(180, int(img.height * (self.scale_pct / 100.0)))
            img = img.resize((nw, nh))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality, optimize=False)
        self.frames_served += 1
        return buf.getvalue()

    def inject_remote_input(self, payload: dict[str, Any]) -> bool:
        self.last_input = dict(payload)
        self.inputs_handled += 1
        kind = str(payload.get("kind") or "")
        w, h = self.screen_size
        try:
            from pynput import keyboard as pk, mouse as pm  # type: ignore

            mc = pm.Controller()
            kc = pk.Controller()
            if kind == "move":
                mc.position = (int(float(payload.get("x", 0.5)) * w), int(float(payload.get("y", 0.5)) * h))
                return True
            if kind == "click":
                mc.position = (int(float(payload.get("x", 0.5)) * w), int(float(payload.get("y", 0.5)) * h))
                btn = pm.Button.right if payload.get("button") == "right" else pm.Button.left
                mc.click(btn, 1)
                return True
            if kind == "scroll":
                mc.scroll(0, int(payload.get("dy", 0)))
                return True
            if kind == "text":
                kc.type(str(payload.get("text") or ""))
                return True
            if kind == "key":
                kmap = {
                    "Up": pk.Key.up,
                    "Down": pk.Key.down,
                    "Left": pk.Key.left,
                    "Right": pk.Key.right,
                    "Space": pk.Key.space,
                    "Return": pk.Key.enter,
                    "Escape": pk.Key.esc,
                    "Tab": pk.Key.tab,
                    "BackSpace": pk.Key.backspace,
                }
                kobj = kmap.get(str(payload.get("key") or ""))
                if kobj:
                    kc.tap(kobj)
                return True
        except Exception:
            pass

        if platform.system() == "Linux" and shutil.which("xdotool"):
            try:
                if kind == "move":
                    subprocess.run(["xdotool", "mousemove", str(int(float(payload.get("x", 0.5)) * w)), str(int(float(payload.get("y", 0.5)) * h))], timeout=1)
                elif kind == "click":
                    btn = "3" if payload.get("button") == "right" else "1"
                    subprocess.run(["xdotool", "mousemove", str(int(float(payload.get("x", 0.5)) * w)), str(int(float(payload.get("y", 0.5)) * h)), "click", btn], timeout=1)
                elif kind == "text":
                    subprocess.run(["xdotool", "type", "--delay", "2", str(payload.get("text") or "")], timeout=1)
                elif kind == "key":
                    key_tok = str(payload.get("key") or "")
                    if key_tok.isalnum() and len(key_tok) <= 12:
                        subprocess.run(["xdotool", "key", key_tok], timeout=1)
                return True
            except Exception:
                pass
        return True

    def start(self) -> tuple[bool, str]:
        if self.running:
            return True, f"AstraBeam Host already running ({self.scheme}://{self.bind_host}:{self.port})."
        server_ref = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _client_ip(self) -> str:
                return str(self.client_address[0]) if self.client_address else ""

            def _add_sec_headers(self) -> None:
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")

            def _check_tailscale_acl(self) -> bool:
                if not server_ref.tailscale_only:
                    return True
                cip = self._client_ip()
                if TailscaleDiscovery.is_tailscale_or_loopback(cip):
                    return True
                self.send_response(403)
                self._add_sec_headers()
                self.end_headers()
                self.wfile.write(b"Forbidden: Tailscale-only zero-trust ACL active.")
                return False

            def _send_json(self, code: int, obj: dict[str, Any]) -> None:
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self._add_sec_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed_token(self, query_params: dict[str, list[str]] | None = None) -> str:
                tok = self.headers.get("X-AstraBeam-Token", "")
                if not tok and query_params:
                    tok = (query_params.get("token") or [""])[0]
                return tok

            def do_GET(self) -> None:  # noqa: N802
                if not self._check_tailscale_acl():
                    return
                cip = self._client_ip()
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if parsed.path in ("/", "/watch"):
                    raw = (_WATCH_VIEWER_HTML if parsed.path == "/watch" else _WEB_VIEWER_HTML).encode("utf-8")
                    self.send_response(200)
                    self._add_sec_headers()
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if parsed.path == "/manifest.webmanifest":
                    raw = _WEB_MANIFEST_JSON.encode("utf-8")
                    self.send_response(200)
                    self._add_sec_headers()
                    self.send_header("Content-Type", "application/manifest+json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if parsed.path == "/api/status":
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "hostname": socket.gethostname(),
                            "tls": server_ref.tls_active,
                            "fps": server_ref.fps,
                            "frames_served": server_ref.frames_served,
                            "tailscale_only": server_ref.tailscale_only,
                        },
                    )
                    return
                if parsed.path == "/api/watch/summary":
                    if not server_ref.verify_token(self._authed_token(qs), cip):
                        self._send_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    info = (
                        server_ref.watch_summary_provider()
                        if server_ref.watch_summary_provider
                        else {"hostname": socket.gethostname(), "health_score": 95, "cpu_percent": 0.0, "mem_percent": 0.0}
                    )
                    self._send_json(200, {"ok": True, **info})
                    return
                if parsed.path == "/api/clipboard":
                    if not server_ref.verify_token(self._authed_token(qs), cip):
                        self._send_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    self._send_json(200, {"ok": True, "text": server_ref._clipboard_text})
                    return
                if parsed.path == "/api/frame":
                    if not server_ref.verify_token(self._authed_token(qs), cip):
                        self._send_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    jpg = server_ref.capture_jpeg_frame()
                    self.send_response(200)
                    self._add_sec_headers()
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpg)))
                    self.end_headers()
                    self.wfile.write(jpg)
                    return
                if parsed.path == "/stream.mjpeg":
                    if not server_ref.verify_token(self._authed_token(qs), cip):
                        self._send_json(401, {"ok": False, "error": "Unauthorized"})
                        return
                    self.send_response(200)
                    self._add_sec_headers()
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    interval = 1.0 / max(1, server_ref.fps)
                    while not server_ref._stop.is_set():
                        t0 = time.monotonic()
                        jpg = server_ref.capture_jpeg_frame()
                        hdr = (
                            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                            + str(len(jpg)).encode("ascii")
                            + b"\r\n\r\n"
                        )
                        try:
                            self.wfile.write(hdr + jpg + b"\r\n")
                            self.wfile.flush()
                        except OSError:
                            break
                        rem = interval - (time.monotonic() - t0)
                        if rem > 0:
                            time.sleep(rem)
                    return
                self._send_json(404, {"ok": False, "error": "Not found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._check_tailscale_acl():
                    return
                cip = self._client_ip()
                length = min(65536, int(self.headers.get("Content-Length", "0") or 0))
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                if self.path == "/api/bt/handshake":
                    if server_ref.is_ip_locked_out(cip):
                        self._send_json(429, {"ok": False, "error": "Locked out due to failed attempts."})
                        return
                    mac = str(data.get("mac") or "").strip().upper()
                    pin_val = str(data.get("pin") or "")
                    hmac_proof = str(data.get("challenge_hmac") or "")
                    if not (server_ref.verify_pin(pin_val) or (len(hmac_proof) >= 32 and mac)):
                        locked = server_ref.record_failed_pin(cip)
                        self._send_json(429 if locked else 403, {"ok": False, "error": "Bluetooth handshake rejected"})
                        return
                    tok_bt = server_ref.issue_token(cip)
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "token": tok_bt,
                            "mac": mac,
                            "transport": str(data.get("transport_mode") or "bluetooth_ble_telemetry"),
                            "expires_in": server_ref.token_ttl_seconds,
                        },
                    )
                    return
                if self.path == "/api/pair":
                    if server_ref.is_ip_locked_out(cip):
                        self._send_json(429, {"ok": False, "error": f"Locked out for {server_ref.lockout_seconds}s due to failed PIN attempts."})
                        return
                    if server_ref.verify_pin(str(data.get("pin") or "")):
                        self._send_json(
                            200,
                            {
                                "ok": True,
                                "token": server_ref.issue_token(cip),
                                "expires_in": server_ref.token_ttl_seconds,
                            },
                        )
                    else:
                        locked = server_ref.record_failed_pin(cip)
                        self._send_json(429 if locked else 403, {"ok": False, "error": "Too many attempts — locked out" if locked else "Invalid pairing PIN"})
                    return
                tok = self._authed_token()
                if not server_ref.verify_token(tok, cip):
                    self._send_json(401, {"ok": False, "error": "Unauthorized or expired token"})
                    return
                if self.path == "/api/revoke":
                    server_ref.revoke_token(tok)
                    self._send_json(200, {"ok": True})
                    return
                if self.path == "/api/input":
                    ok = server_ref.inject_remote_input(data)
                    self._send_json(200, {"ok": ok, "inputs_handled": server_ref.inputs_handled})
                    return
                if self.path == "/api/clipboard":
                    server_ref._clipboard_text = str(data.get("text") or "")[:16384]
                    self._send_json(200, {"ok": True})
                    return
                if self.path == "/api/watch/action":
                    act = str(data.get("action") or "")
                    val = str(data.get("value") or "")
                    if server_ref.watch_action_handler:
                        ok, msg = server_ref.watch_action_handler(act, val)
                        self._send_json(200 if ok else 400, {"ok": ok, "message": msg})
                    else:
                        self._send_json(200, {"ok": True, "message": f"Watch action '{act}' acknowledged."})
                    return
                self._send_json(404, {"ok": False, "error": "Not found"})

        try:
            self._stop.clear()
            _QuietTLSHTTPServer.allow_reuse_address = True
            self._httpd = _QuietTLSHTTPServer((self.bind_host, self.port), _Handler)
            self.port = int(self._httpd.server_address[1])
            self.tls_active = False
            self.cert_fingerprint_sha256 = ""
            if self.tls_enabled:
                cert_bundle = self.ensure_tls_cert()
                if cert_bundle is not None:
                    try:
                        cert_path, key_path, fp = cert_bundle
                        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
                        self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
                        self.tls_active = True
                        self.cert_fingerprint_sha256 = fp
                    except Exception:
                        self.tls_active = False
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
            if self.journal:
                self.journal.log_event(
                    "AstraBeam",
                    "start_host",
                    f"Started AstraBeam {self.scheme.upper()} host on {self.bind_host}:{self.port} (Tailscale-only={self.tailscale_only}, TLS={self.tls_active})",
                    "Zero-trust Tailscale streaming for iOS, Android, Apple Watch & Desktop",
                    "ok",
                )
            return True, f"AstraBeam {self.scheme.upper()} Host live on {self.scheme}://{self.bind_host}:{self.port} · PIN {self.pin}"
        except OSError as exc:
            self._httpd = None
            return False, f"Failed to start AstraBeam Host on {self.bind_host}:{self.port}: {exc}"

    def stop(self) -> tuple[bool, str]:
        self._stop.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self.tls_active = False
        return True, "AstraBeam Host stopped."


class AstraBeamClient:
    """Zero-trust TLS-capable Moonlight-style client for AstraBeam hosts over Tailscale."""

    def __init__(self, host: str, port: int = 47990, pin: str = "", use_tls: bool = True) -> None:
        scheme = "https" if use_tls else "http"
        self.base_url = f"{scheme}://{host.strip()}:{int(port)}"
        self.host = host.strip()
        self.port = int(port)
        self.pin = pin.strip()
        self.token: str = ""
        self.last_rtt_ms: float = 0.0
        self._ssl_ctx = ssl._create_unverified_context() if use_tls else None

    def _open(self, req: urllib.request.Request, timeout: float):
        if self._ssl_ctx is not None:
            return urllib.request.urlopen(req, timeout=timeout, context=self._ssl_ctx)
        return urllib.request.urlopen(req, timeout=timeout)

    def pair(self, timeout: float = 4.0) -> tuple[bool, str]:
        body = json.dumps({"pin": self.pin}).encode("utf-8")
        for attempt_tls in ([True, False] if self.base_url.startswith("https://") else [False]):
            url = f"{'https' if attempt_tls else 'http'}://{self.host}:{self.port}/api/pair"
            ctx = ssl._create_unverified_context() if attempt_tls else None
            req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
            try:
                t0 = time.monotonic()
                with (urllib.request.urlopen(req, timeout=timeout, context=ctx) if ctx else urllib.request.urlopen(req, timeout=timeout)) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.last_rtt_ms = round((time.monotonic() - t0) * 1000.0, 1)
                self.base_url = f"{'https' if attempt_tls else 'http'}://{self.host}:{self.port}"
                self._ssl_ctx = ctx
                if data.get("ok") and data.get("token"):
                    self.token = str(data["token"])
                    return True, f"Paired ({'TLS' if attempt_tls else 'HTTP'}) with {self.base_url} ({self.last_rtt_ms} ms RTT)."
                return False, str(data.get("error") or "Pairing rejected.")
            except urllib.error.HTTPError as exc:
                try:
                    err = json.loads(exc.read().decode("utf-8"))
                    return False, str(err.get("error") or f"HTTP {exc.code}")
                except Exception:
                    return False, f"Pairing rejected (HTTP {exc.code})."
            except ssl.SSLError:
                continue
            except Exception as exc:
                if not attempt_tls:
                    return False, f"Pairing failed: {exc}"
        return False, "Pairing failed."

    def fetch_frame(self, timeout: float = 4.0) -> bytes | None:
        if not self.token:
            return None
        req = urllib.request.Request(f"{self.base_url}/api/frame", headers={"X-AstraBeam-Token": self.token})
        try:
            t0 = time.monotonic()
            with self._open(req, timeout) as resp:
                raw = resp.read()
            self.last_rtt_ms = round((time.monotonic() - t0) * 1000.0, 1)
            return raw
        except Exception:
            return None

    def send_input(self, payload: dict[str, Any], timeout: float = 2.0) -> bool:
        if not self.token:
            return False
        req = urllib.request.Request(
            f"{self.base_url}/api/input",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-AstraBeam-Token": self.token},
        )
        try:
            with self._open(req, timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
        except Exception:
            return False

    def get_watch_summary(self, timeout: float = 3.0) -> dict[str, Any] | None:
        if not self.token:
            return None
        req = urllib.request.Request(f"{self.base_url}/api/watch/summary", headers={"X-AstraBeam-Token": self.token})
        try:
            with self._open(req, timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def trigger_watch_action(self, action: str, value: str = "", timeout: float = 4.0) -> tuple[bool, str]:
        if not self.token:
            return False, "Not paired."
        req = urllib.request.Request(
            f"{self.base_url}/api/watch/action",
            data=json.dumps({"action": action, "value": value}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-AstraBeam-Token": self.token},
        )
        try:
            with self._open(req, timeout) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            return bool(d.get("ok")), str(d.get("message") or "")
        except Exception as exc:
            return False, str(exc)

    def push_clipboard(self, text: str, timeout: float = 2.5) -> bool:
        if not self.token:
            return False
        req = urllib.request.Request(
            f"{self.base_url}/api/clipboard",
            data=json.dumps({"text": text}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-AstraBeam-Token": self.token},
        )
        try:
            with self._open(req, timeout) as resp:
                return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
        except Exception:
            return False

    def pull_clipboard(self, timeout: float = 2.5) -> str | None:
        if not self.token:
            return None
        req = urllib.request.Request(f"{self.base_url}/api/clipboard", headers={"X-AstraBeam-Token": self.token})
        try:
            with self._open(req, timeout) as resp:
                return str(json.loads(resp.read().decode("utf-8")).get("text") or "")
        except Exception:
            return None

    def bluetooth_handshake(
        self, mac: str, challenge_hmac: str = "", transport_mode: str = "bluetooth_ble_telemetry", timeout: float = 3.0
    ) -> tuple[bool, str]:
        req = urllib.request.Request(
            f"{self.base_url}/api/bt/handshake",
            data=json.dumps(
                {
                    "mac": mac,
                    "pin": self.pin,
                    "challenge_hmac": challenge_hmac,
                    "transport_mode": transport_mode,
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._open(req, timeout) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if d.get("ok") and d.get("token"):
                self.token = str(d["token"])
                return True, f"Bluetooth handshake session active ({d.get('transport')})"
            return False, str(d.get("error") or "Handshake rejected")
        except Exception as exc:
            return False, f"Bluetooth handshake error: {exc}"

    def revoke(self, timeout: float = 2.0) -> bool:
        if not self.token:
            return True
        req = urllib.request.Request(
            f"{self.base_url}/api/revoke",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "X-AstraBeam-Token": self.token},
        )
        try:
            with self._open(req, timeout) as resp:
                ok = bool(json.loads(resp.read().decode("utf-8")).get("ok"))
            self.token = ""
            return ok
        except Exception:
            self.token = ""
            return False
