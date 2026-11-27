"""LAN Network Device, Smart TV & Bluetooth Discovery + Safe YouTube Ad-Shield with Failsafe DNS Rollback."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
from typing import Any

from .adblock_shield import AstraShieldManager
from .journal import ForensicJournal
from .models import utc_now_iso


_TV_BRAND_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    ("lg", "LG webOS TV", "smart_tv"),
    ("webos", "LG webOS TV", "smart_tv"),
    ("lgtv", "LG webOS TV", "smart_tv"),
    ("samsung", "Samsung Tizen TV", "smart_tv"),
    ("tizen", "Samsung Tizen TV", "smart_tv"),
    ("bravia", "Sony Bravia (Google TV)", "smart_tv"),
    ("sony", "Sony TV / PlayStation", "smart_tv"),
    ("roku", "Roku TV / Stick", "streaming_box"),
    ("chromecast", "Google TV / Chromecast", "streaming_box"),
    ("androidtv", "Android TV / Google TV", "smart_tv"),
    ("shield", "NVIDIA Shield TV", "streaming_box"),
    ("firetv", "Amazon Fire TV", "streaming_box"),
    ("aft", "Amazon Fire TV", "streaming_box"),
    ("appletv", "Apple TV", "streaming_box"),
    ("hisense", "Hisense Smart TV", "smart_tv"),
    ("tcl", "TCL Smart TV", "smart_tv"),
    ("vizio", "Vizio SmartCast TV", "smart_tv"),
    ("xbox", "Xbox Console", "console"),
    ("playstation", "PlayStation Console", "console"),
)

_OUI_HINTS: dict[str, tuple[str, str]] = {
    "00:e0:91": ("LG webOS TV", "smart_tv"),
    "3c:bd:d8": ("LG webOS TV", "smart_tv"),
    "a8:23:fe": ("LG webOS TV", "smart_tv"),
    "b8:3e:59": ("Roku TV / Stick", "streaming_box"),
    "d8:31:34": ("Roku TV / Stick", "streaming_box"),
    "cc:6d:a0": ("Roku TV / Stick", "streaming_box"),
    "00:12:fb": ("Samsung Tizen TV", "smart_tv"),
    "50:85:69": ("Samsung Tizen TV", "smart_tv"),
    "8c:79:f5": ("Samsung Tizen TV", "smart_tv"),
    "f4:7b:5e": ("Samsung Tizen TV", "smart_tv"),
    "54:60:09": ("Google TV / Chromecast", "streaming_box"),
    "f4:f5:d8": ("Google TV / Chromecast", "streaming_box"),
    "00:1f:a7": ("Sony TV / PlayStation", "smart_tv"),
    "f0:bf:97": ("Sony TV / PlayStation", "smart_tv"),
    "74:c2:46": ("Amazon Fire TV", "streaming_box"),
    "fc:65:de": ("Amazon Fire TV", "streaming_box"),
    "28:cf:e9": ("Apple TV", "streaming_box"),
}

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


@dataclass
class LanDeviceInfo:
    ip: str
    mac: str
    hostname: str
    brand: str
    device_type: str  # smart_tv | streaming_box | console | network_device
    ssdp_info: str = ""
    dns_queries: int = 0
    ads_blocked: int = 0
    protection_status: str = "Ready for TV DNS"


@dataclass
class BluetoothDeviceInfo:
    mac: str
    name: str
    device_kind: str  # tv | audio | input | device
    paired: bool = False
    connected: bool = False
    trusted: bool = False


class LanTvAdShield:
    """Discovers Smart TVs, LAN & Bluetooth devices and enforces failsafe YouTube/TV ad pruning."""

    def __init__(self, journal: ForensicJournal, state_dir: Path | None = None) -> None:
        self.journal = journal
        self.state_dir = state_dir or journal.cfg_mgr.base_dir
        self.failsafe_path = self.state_dir / "tv_failsafe_state.json"

    @staticmethod
    def _validate_ipv4(ip: str) -> str | None:
        try:
            addr = ipaddress.ip_address(ip.strip())
            if isinstance(addr, ipaddress.IPv4Address) and not addr.is_multicast:
                return str(addr)
        except Exception:
            pass
        return None

    @staticmethod
    def _validate_mac(mac: str) -> str | None:
        clean = mac.strip().replace("-", ":").upper()
        return clean if _MAC_RE.match(clean) else None

    @staticmethod
    def get_default_gateway_ip() -> str:
        """Detects the router / default gateway IPv4 for failsafe Secondary DNS."""
        if platform.system() == "Linux":
            route_p = Path("/proc/net/route")
            if route_p.exists():
                try:
                    for ln in route_p.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
                        cols = ln.split()
                        if len(cols) >= 3 and cols[1] == "00000000":
                            gw_hex = cols[2]
                            if len(gw_hex) == 8:
                                b = bytes.fromhex(gw_hex)
                                return ".".join(str(x) for x in reversed(b))
                except Exception:
                    pass
            if shutil.which("ip"):
                try:
                    proc = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=2, check=False)
                    m = re.search(r"via\s+(\d{1,3}(?:\.\d{1,3}){3})", proc.stdout or "")
                    if m:
                        return m.group(1)
                except Exception:
                    pass
        return "1.1.1.1"

    @staticmethod
    def _classify_device(hostname: str, mac: str, ssdp: str) -> tuple[str, str]:
        hay = f"{hostname} {ssdp}".lower()
        for kw, brand, dtype in _TV_BRAND_KEYWORDS:
            if kw in hay:
                return brand, dtype
        mac_prefix = mac.lower()[:8]
        if mac_prefix in _OUI_HINTS:
            return _OUI_HINTS[mac_prefix]
        if "dial" in hay or "mediarenderer" in hay or "dlna" in hay or "googlecast" in hay or "airplay" in hay:
            return "Smart TV / Media Renderer", "smart_tv"
        return "Network Device", "network_device"

    @staticmethod
    def _read_arp_table() -> dict[str, str]:
        """Reads OS ARP / neighbor cache safely without raw shell strings."""
        entries: dict[str, str] = {}
        sys_name = platform.system()
        cmds: list[list[str]] = []
        if sys_name == "Linux" and shutil.which("ip"):
            cmds.append(["ip", "neigh", "show"])
        if shutil.which("arp"):
            cmds.append(["arp", "-a"])
        for cmd in cmds:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5, check=False)
                for ln in (proc.stdout or "").splitlines():
                    ip_m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", ln)
                    mac_m = re.search(r"([0-9a-fA-F]{2}(?::|-)[0-9a-fA-F]{2}(?:(?::|-)[0-9a-fA-F]{2}){4})", ln)
                    if ip_m and mac_m:
                        ip = ip_m.group(1)
                        mac = mac_m.group(1).replace("-", ":").lower()
                        if not ip.startswith(("127.", "224.", "239.", "255.")):
                            entries[ip] = mac
                if entries:
                    break
            except Exception:
                continue
        return entries

    @staticmethod
    def _probe_ssdp(timeout: float = 0.8) -> dict[str, str]:
        """Sends a passive SSDP M-SEARCH discovery packet on the local LAN."""
        found: dict[str, str] = {}
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 1\r\n"
            "ST: urn:dial-multiscreen-org:service:dial:1\r\n\r\n"
        ).encode("ascii")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(msg, ("239.255.255.250", 1900))
                while True:
                    try:
                        data, addr = sock.recvfrom(2048)
                    except socket.timeout:
                        break
                    ip = str(addr[0])
                    txt = data.decode("utf-8", errors="ignore")
                    server_m = re.search(r"(?im)^SERVER:\s*(.+)$", txt)
                    st_m = re.search(r"(?im)^ST:\s*(.+)$", txt)
                    summary = " | ".join(
                        p for p in (server_m.group(1).strip() if server_m else "", st_m.group(1).strip() if st_m else "") if p
                    )
                    if summary:
                        found[ip] = summary[:140]
        except OSError:
            pass
        return found

    @staticmethod
    def _probe_mdns_tvs() -> dict[str, str]:
        """Queries Avahi/mDNS for Smart TVs, Chromecast, AirPlay & Android TV services if available."""
        found: dict[str, str] = {}
        avahi = shutil.which("avahi-browse")
        if not avahi:
            return found
        for svc in ("_googlecast._tcp", "_airplay._tcp", "_androidtvremote2._tcp"):
            try:
                proc = subprocess.run(
                    [avahi, "-rt", "-p", svc],
                    capture_output=True,
                    text=True,
                    timeout=1.2,
                    check=False,
                )
                for ln in (proc.stdout or "").splitlines():
                    if not ln.startswith("="):
                        continue
                    parts = ln.split(";")
                    if len(parts) >= 8:
                        name, ip = parts[3], parts[7]
                        if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", ip):
                            found[ip] = f"{svc}: {name}"[:120]
            except Exception:
                continue
        return found

    def scan_lan_devices(
        self,
        shield: AstraShieldManager | None = None,
        ssdp_timeout: float = 0.6,
    ) -> list[LanDeviceInfo]:
        """Discovers Smart TVs, streaming boxes & LAN devices and merges AstraShield DNS stats."""
        arp = self._read_arp_table()
        ssdp = self._probe_ssdp(timeout=ssdp_timeout) if ssdp_timeout > 0 else {}
        mdns = self._probe_mdns_tvs() if ssdp_timeout > 0 else {}
        client_stats = shield.get_client_stats() if shield is not None else {}

        all_ips = sorted(
            set(arp.keys())
            | set(ssdp.keys())
            | set(mdns.keys())
            | {ip for ip in client_stats.keys() if not ip.startswith("127.")},
            key=lambda x: tuple(int(p) if p.isdigit() else 0 for p in x.split(".")),
        )
        devices: list[LanDeviceInfo] = []
        for ip in all_ips:
            if not self._validate_ipv4(ip):
                continue
            mac = arp.get(ip, "unknown")
            ssdp_info = " | ".join(x for x in (ssdp.get(ip, ""), mdns.get(ip, "")) if x)
            host = ip
            try:
                socket.setdefaulttimeout(0.2)
                host = socket.gethostbyaddr(ip)[0]
            except Exception:
                host = ip
            finally:
                socket.setdefaulttimeout(None)
            brand, dtype = self._classify_device(host, mac, ssdp_info)
            st = client_stats.get(ip, {})
            dq = int(st.get("queries", 0))
            db = int(st.get("blocked", 0))
            pstat = (
                f"Active via AstraShield ({dq} queries · {db} ads blocked)"
                if dq > 0
                else ("TV / Streamer Ready" if dtype in ("smart_tv", "streaming_box") else "Discovered on LAN")
            )
            devices.append(
                LanDeviceInfo(
                    ip=ip,
                    mac=mac,
                    hostname=host,
                    brand=brand,
                    device_type=dtype,
                    ssdp_info=ssdp_info,
                    dns_queries=dq,
                    ads_blocked=db,
                    protection_status=pstat,
                )
            )
        order = {"smart_tv": 0, "streaming_box": 1, "console": 2, "network_device": 3}
        devices.sort(key=lambda d: (order.get(d.device_type, 9), d.ip))
        return devices

    # ------------------------------------------------------------------
    # Bluetooth Discovery & Quick Connect
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_bt_kind(name: str, icon: str = "") -> str:
        low = f"{name} {icon}".lower()
        if any(k in low for k in ("tv", "bravia", "samsung", "lg ", "webos", "roku", "shield", "fire tv")):
            return "tv"
        if any(k in low for k in ("headphone", "buds", "airpods", "speaker", "soundbar", "audio", "sony wh", "bose", "jbl")):
            return "audio"
        if any(k in low for k in ("remote", "controller", "gamepad", "xbox", "dualsense", "keyboard", "mouse")):
            return "input"
        return "device"

    def scan_bluetooth_devices(self) -> list[BluetoothDeviceInfo]:
        """Scans paired and nearby Bluetooth devices (TVs, speakers, remotes, headphones)."""
        out: list[BluetoothDeviceInfo] = []
        sys_name = platform.system()
        if sys_name == "Linux" and shutil.which("bluetoothctl"):
            try:
                proc = subprocess.run(
                    ["bluetoothctl", "devices"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                for ln in (proc.stdout or "").splitlines():
                    m = re.match(r"^\s*Device\s+([0-9A-Fa-f:]{17})\s+(.+)$", ln)
                    if not m:
                        continue
                    mac, name = m.group(1).upper(), m.group(2).strip()
                    paired = connected = trusted = False
                    icon = ""
                    try:
                        iproc = subprocess.run(
                            ["bluetoothctl", "info", mac],
                            capture_output=True,
                            text=True,
                            timeout=2,
                            check=False,
                        )
                        itxt = iproc.stdout or ""
                        paired = "Paired: yes" in itxt
                        connected = "Connected: yes" in itxt
                        trusted = "Trusted: yes" in itxt
                        im = re.search(r"Icon:\s*(.+)", itxt)
                        if im:
                            icon = im.group(1).strip()
                    except Exception:
                        pass
                    out.append(
                        BluetoothDeviceInfo(
                            mac=mac,
                            name=name,
                            device_kind=self._classify_bt_kind(name, icon),
                            paired=paired,
                            connected=connected,
                            trusted=trusted,
                        )
                    )
            except Exception:
                pass
        elif sys_name == "Darwin" and shutil.which("system_profiler"):
            try:
                proc = subprocess.run(
                    ["system_profiler", "SPBluetoothDataType", "-json"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                data = json.loads(proc.stdout or "{}")
                for blk in data.get("SPBluetoothDataType", []):
                    for sec in ("device_connected", "device_not_connected"):
                        for item in blk.get(sec, []) or []:
                            if isinstance(item, dict):
                                for name, meta in item.items():
                                    mac = str((meta or {}).get("device_address", "")).upper()
                                    if mac:
                                        out.append(
                                            BluetoothDeviceInfo(
                                                mac=mac,
                                                name=str(name),
                                                device_kind=self._classify_bt_kind(str(name)),
                                                paired=True,
                                                connected=(sec == "device_connected"),
                                            )
                                        )
            except Exception:
                pass
        elif sys_name == "Windows" and shutil.which("powershell"):
            try:
                proc = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "Get-PnpDevice -Class Bluetooth | Select-Object FriendlyName,InstanceId,Status | ConvertTo-Json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                rows = json.loads(proc.stdout or "[]")
                if isinstance(rows, dict):
                    rows = [rows]
                for r in rows:
                    fn = str(r.get("FriendlyName") or "")
                    if fn:
                        out.append(
                            BluetoothDeviceInfo(
                                mac=str(r.get("InstanceId") or "")[-17:],
                                name=fn,
                                device_kind=self._classify_bt_kind(fn),
                                paired=True,
                                connected=(str(r.get("Status") or "").lower() == "ok"),
                            )
                        )
            except Exception:
                pass
        out.sort(key=lambda d: (0 if d.connected else 1, 0 if d.device_kind == "tv" else 1, d.name.lower()))
        return out

    def connect_bluetooth_device(self, mac: str, connect: bool = True) -> tuple[bool, str]:
        valid_mac = self._validate_mac(mac)
        if not valid_mac:
            return False, f"Invalid Bluetooth MAC address: {mac}"
        if platform.system() == "Linux" and shutil.which("bluetoothctl"):
            action = "connect" if connect else "disconnect"
            try:
                proc = subprocess.run(
                    ["bluetoothctl", action, valid_mac],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                msg = (proc.stdout or proc.stderr or "").strip()
                ok = proc.returncode == 0 and ("successful" in msg.lower() or "connected" in msg.lower())
                self.journal.log_event(
                    "LanTvAdShield",
                    f"bt_{action}",
                    f"bluetoothctl {action} {valid_mac} -> {msg}",
                    "User clicked Bluetooth quick-connect",
                    "ok" if ok else "warn",
                )
                return ok, msg or f"Bluetooth {action} attempted for {valid_mac}"
            except Exception as exc:
                return False, f"Bluetooth {action} failed: {exc}"
        return False, "Direct CLI Bluetooth quick-connect uses bluetoothctl on Linux; pair via OS Bluetooth settings."

    # ------------------------------------------------------------------
    # TV Failsafe State, Connection & Automatic Reset on Failure
    # ------------------------------------------------------------------
    def _save_failsafe_state(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.failsafe_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def load_failsafe_state(self) -> dict[str, Any] | None:
        if not self.failsafe_path.exists():
            return None
        try:
            data = json.loads(self.failsafe_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def connect_and_protect_tv(
        self,
        device: LanDeviceInfo,
        lan_ip: str,
        dns_port: int = 53,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Records TV failsafe rollback state, connects ADB if supported, and prepares zero-breakage TV DNS."""
        router_ip = self.get_default_gateway_ip()
        plan = self.generate_tv_setup_plan(device, lan_ip=lan_ip, dns_port=dns_port, router_ip=router_ip)
        adb_connected = False
        adb_note = ""
        if "android" in device.brand.lower() or "google" in device.brand.lower() or "fire" in device.brand.lower() or "shield" in device.brand.lower():
            if shutil.which("adb"):
                adb_connected, adb_note = self.adb_connect_tv(device.ip)

        state = {
            "active": True,
            "tv_ip": device.ip,
            "tv_mac": device.mac,
            "tv_brand": device.brand,
            "previous_dns": "dhcp",
            "router_failsafe_dns": router_ip,
            "applied_dns": lan_ip,
            "dns_port": int(dns_port),
            "adb_managed": bool(adb_connected),
            "connected_at": utc_now_iso(),
        }
        self._save_failsafe_state(state)
        self.journal.log_event(
            "LanTvAdShield",
            "connect_protect_tv",
            f"Registered failsafe TV session for {device.brand} ({device.ip}) -> DNS1={lan_ip}, Failsafe DNS2={router_ip}",
            "Ensure TV can automatically revert to router/DHCP DNS if AstraCradle fails or exits",
            "ok",
        )
        msg = (
            f"TV Failsafe Armed for {device.brand} ({device.ip}): Primary DNS={lan_ip}, Failsafe Secondary DNS={router_ip}."
            + (f" ADB: {adb_note}" if adb_note else "")
        )
        return True, msg, plan

    def restore_tv_settings(
        self,
        tv_ip: str | None = None,
        reason: str = "failsafe_reset",
        shield: AstraShieldManager | None = None,
    ) -> tuple[bool, str]:
        """Resets connected TV DNS/ADB overrides & host port-53 NAT rules if AstraCradle stops or fails."""
        st = self.load_failsafe_state() or {}
        target_ip = self._validate_ipv4(tv_ip or str(st.get("tv_ip") or ""))
        notes: list[str] = []

        if shield is not None:
            shield.cleanup_lan_nat_redirect()
            notes.append("Cleared AstraShield host LAN NAT redirect")

        if target_ip and st.get("adb_managed") and shutil.which("adb"):
            for cmd in (
                ["adb", "-s", f"{target_ip}:5555", "shell", "settings", "put", "global", "private_dns_mode", "opportunistic"],
                ["adb", "-s", f"{target_ip}:5555", "shell", "settings", "delete", "global", "http_proxy"],
                ["adb", "disconnect", f"{target_ip}:5555"],
            ):
                try:
                    subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
                except Exception:
                    pass
            notes.append(f"Restored ADB TV ({target_ip}) DNS/proxy back to stock DHCP/opportunistic")

        if st:
            st["active"] = False
            st["restored_at"] = utc_now_iso()
            st["restore_reason"] = reason
            self._save_failsafe_state(st)

        summary = (
            f"TV Failsafe Reset complete ({reason})"
            + (f": {'; '.join(notes)}" if notes else f" — TV falls back to router DNS ({st.get('router_failsafe_dns', 'DHCP')}).")
        )
        self.journal.log_event(
            "LanTvAdShield",
            "restore_tv_settings",
            summary,
            "Reset TV & network DNS rules when AstraCradle stops or fails",
            "ok",
        )
        return True, summary

    def check_and_run_startup_failsafe(self, shield: AstraShieldManager) -> str | None:
        """If previous session crashed/exited with an active TV session and sinkhole isn't running, restore cleanly."""
        st = self.load_failsafe_state()
        if st and st.get("active") and not shield.dns_running:
            _, msg = self.restore_tv_settings(reason="startup_recovery_failsafe", shield=shield)
            return msg
        return None

    @staticmethod
    def generate_tv_setup_plan(
        device: LanDeviceInfo,
        lan_ip: str,
        dns_port: int = 53,
        router_ip: str = "1.1.1.1",
    ) -> dict[str, Any]:
        """Produces zero-breakage YouTube + TV ad-blocking instructions with automatic failsafe Secondary DNS."""
        brand_low = device.brand.lower()
        dns_line = f"Primary DNS = {lan_ip} | Secondary (Failsafe) DNS = {router_ip}"
        steps = [
            f"1. Turn ON 'Auto-Start Sinkhole on Startup' + 'LAN TV DNS' in AstraCradle ({lan_ip}:{dns_port}).",
            f"2. On your {device.brand} ({device.ip}), open Network Settings -> Manual DNS and set:\n     • Primary DNS: {lan_ip}\n     • Secondary (Failsafe) DNS: {router_ip} (so your TV never loses internet if this PC sleeps or AstraCradle exits!)",
            "3. AstraCradle blocks Smart TV ad servers, Roku/LG/Samsung telemetry & external YouTube ad-networks while protecting *.googlevideo.com streams.",
        ]
        if any(k in brand_low for k in ("android", "google", "shield", "fire", "sony", "tcl", "hisense")):
            steps.append(
                f"4. For 100% ad-free in-stream YouTube on {device.brand}: enable ADB Debugging and install SmartTube (ad-free YouTube TV client) via: adb connect {device.ip}:5555"
            )
            app_companion = "SmartTube (Android/Google/Fire TV)"
        elif "lg" in brand_low:
            steps.append(
                "4. For 100% in-stream YouTube ad removal on LG webOS: install youtube-webos via LG Developer Mode / Homebrew Channel alongside AstraShield TV DNS."
            )
            app_companion = "youtube-webos (LG Homebrew)"
        else:
            steps.append(
                "4. AstraShield keeps all YouTube streams smooth & unbroken while blocking TV OS ads; if AstraCradle is closed or fails, the Failsafe Secondary DNS + automatic restore immediately returns your TV to stock routing."
            )
            app_companion = "LAN DNS + Failsafe Secondary DNS"

        return {
            "device_ip": device.ip,
            "brand": device.brand,
            "dns_target": lan_ip,
            "router_failsafe": router_ip,
            "dns_port": dns_port,
            "dns_instruction": dns_line,
            "companion_mode": app_companion,
            "steps": steps,
        }

    def adb_connect_tv(self, tv_ip: str) -> tuple[bool, str]:
        """Connects to an Android TV / Google TV / Fire TV over ADB safely if adb is installed."""
        valid_ip = self._validate_ipv4(tv_ip)
        if not valid_ip:
            return False, f"Invalid TV IPv4 address: {tv_ip}"
        adb_bin = shutil.which("adb")
        if not adb_bin:
            return False, f"ADB tool not installed. Install android-tools, then run: adb connect {valid_ip}:5555"
        try:
            proc = subprocess.run(
                [adb_bin, "connect", f"{valid_ip}:5555"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            ok = proc.returncode == 0 and ("connected" in out.lower() or "already" in out.lower())
            self.journal.log_event(
                "LanTvAdShield",
                "adb_connect_tv",
                f"ADB connect {valid_ip}:5555 -> {out}",
                "User initiated Android/Google/Fire TV companion connection",
                "ok" if ok else "warn",
            )
            return ok, out or ("Connected" if ok else "Connection refused (enable ADB Debugging on TV).")
        except Exception as exc:
            return False, f"ADB connect failed: {exc}"
