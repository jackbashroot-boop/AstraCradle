"""LAN Network Device & Smart TV Discovery + Safe YouTube Ad-Shield Engine."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import platform
import re
import shutil
import socket
import subprocess
from typing import Any

from .adblock_shield import AstraShieldManager
from .journal import ForensicJournal


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


class LanTvAdShield:
    """Discovers Smart TVs & network devices and configures zero-breakage YouTube/TV ad pruning."""

    def __init__(self, journal: ForensicJournal) -> None:
        self.journal = journal

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
    def _classify_device(hostname: str, mac: str, ssdp: str) -> tuple[str, str]:
        hay = f"{hostname} {ssdp}".lower()
        for kw, brand, dtype in _TV_BRAND_KEYWORDS:
            if kw in hay:
                return brand, dtype
        mac_prefix = mac.lower()[:8]
        if mac_prefix in _OUI_HINTS:
            return _OUI_HINTS[mac_prefix]
        if "dial" in hay or "mediarenderer" in hay or "dlna" in hay:
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

    def scan_lan_devices(
        self,
        shield: AstraShieldManager | None = None,
        ssdp_timeout: float = 0.6,
    ) -> list[LanDeviceInfo]:
        """Discovers Smart TVs, streaming boxes & LAN devices and merges AstraShield DNS stats."""
        arp = self._read_arp_table()
        ssdp = self._probe_ssdp(timeout=ssdp_timeout) if ssdp_timeout > 0 else {}
        client_stats = shield.get_client_stats() if shield is not None else {}

        all_ips = sorted(
            set(arp.keys()) | set(ssdp.keys()) | {ip for ip in client_stats.keys() if not ip.startswith("127.")},
            key=lambda x: tuple(int(p) if p.isdigit() else 0 for p in x.split(".")),
        )
        devices: list[LanDeviceInfo] = []
        for ip in all_ips:
            if not self._validate_ipv4(ip):
                continue
            mac = arp.get(ip, "unknown")
            ssdp_info = ssdp.get(ip, "")
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
        # Prioritize TVs & streaming devices first
        order = {"smart_tv": 0, "streaming_box": 1, "console": 2, "network_device": 3}
        devices.sort(key=lambda d: (order.get(d.device_type, 9), d.ip))
        return devices

    @staticmethod
    def generate_tv_setup_plan(device: LanDeviceInfo, lan_ip: str, dns_port: int = 53) -> dict[str, Any]:
        """Produces zero-breakage YouTube + TV ad-blocking instructions for the detected TV brand."""
        brand_low = device.brand.lower()
        dns_line = f"Set TV Primary DNS to {lan_ip}" + (f" (port {dns_port})" if dns_port != 53 else "")
        steps = [
            f"1. Turn ON 'LAN Smart TV Ad-Shield DNS' in AstraCradle so this PC listens on {lan_ip}:{dns_port}.",
            f"2. On your {device.brand} ({device.ip}), open Network Settings -> IP/DNS -> Manual and set DNS = {lan_ip}.",
            "3. AstraCradle blocks Smart TV ad servers, Roku/LG/Samsung telemetry & YouTube external ad-networks while strictly protecting *.googlevideo.com so streams never buffer-freeze.",
        ]
        if any(k in brand_low for k in ("android", "google", "shield", "fire", "sony", "tcl", "hisense")):
            steps.append(
                f"4. For 100% ad-free in-stream YouTube on {device.brand}: enable ADB Debugging on the TV and install SmartTube (ad-free YouTube TV client using native googlevideo streams), or run: adb connect {device.ip}:5555"
            )
            app_companion = "SmartTube (Android/Google/Fire TV)"
        elif "lg" in brand_low:
            steps.append(
                "4. For 100% in-stream YouTube ad removal on LG webOS: enable LG Developer Mode / Homebrew Channel and install youtube-webos (AdFree) alongside AstraShield TV DNS."
            )
            app_companion = "youtube-webos (LG Homebrew)"
        else:
            steps.append(
                "4. Note: Stock YouTube TV apps serve some video ads from *.googlevideo.com (same server as real videos). AstraShield keeps streams unbroken while blocking TV-OS ads & external ad pods; pair with an HTPC/Chromecast/Android TV SmartTube for 100% in-stream ad removal."
            )
            app_companion = "LAN DNS + YouTube Safe Allowlist"

        return {
            "device_ip": device.ip,
            "brand": device.brand,
            "dns_target": lan_ip,
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
