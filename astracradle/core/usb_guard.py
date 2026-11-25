"""USB BadUSB Guard & Full Peripheral Hardware Inventory / Active New-Device Ping Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any

from .config import AppConfig, ConfigManager
from .elevation import ElevationRunner
from .journal import ForensicJournal
from .models import utc_now_iso


USB_CLASS_NAMES: dict[str, str] = {
    "00": "Composite / Per-Interface",
    "01": "Audio (DAC / Headset / Mic)",
    "02": "CDC / Serial / Network",
    "03": "HID (Keyboard / Mouse / Input)",
    "05": "Physical Input",
    "06": "Imaging / Scanner",
    "07": "Printer",
    "08": "Mass Storage (USB Drive)",
    "09": "USB Hub",
    "0a": "CDC-Data",
    "0b": "Smart Card",
    "0e": "Video / Webcam",
    "dc": "Diagnostic Device",
    "e0": "Wireless / Bluetooth / RNDIS",
    "ef": "Miscellaneous Composite",
    "fe": "Application Specific (DFU)",
    "ff": "Vendor Specific",
}

# Programmable microcontroller / BadUSB-capable vendor IDs when exposing HID/composite attack surfaces
PROGRAMMABLE_HID_VIDS: dict[str, str] = {
    "16c0": "V-USB / Teensy (PJRC)",
    "2341": "Arduino LLC",
    "2a03": "Arduino.org",
    "2e8a": "Raspberry Pi (RP2040 / Pico)",
    "303a": "Espressif (ESP32-S2/S3 HID-Capable)",
    "1b4f": "SparkFun Programmable Board",
    "239a": "Adafruit Programmable Board",
    "1d50": "OpenMoko / Custom Gadget",
    "0483": "STMicroelectronics Custom HID/DFU",
    "1209": "pid.codes Open-Hardware / Custom HID",
}


@dataclass
class PeripheralDevice:
    device_key: str
    bus: str  # usb | input | audio | display | network | bluetooth | storage
    name: str
    vendor: str
    vid: str = ""
    pid: str = ""
    serial: str = ""
    sysfs_path: str = ""
    interface_classes: list[str] = field(default_factory=list)
    has_hid_keyboard: bool = False
    authorized: bool = True
    status: str = "connected"  # cleared | trusted | blocked | connected | warning
    badusb_reasons: list[str] = field(default_factory=list)
    plain_description: str = ""


@dataclass
class UsbPingEvent:
    timestamp: str
    device_key: str
    event_type: str  # new_device_cleared | new_device_blocked | new_peripheral | removed
    device: PeripheralDevice
    message: str


class UsbAndPeripheralGuard:
    """Discovers all peripherals, pings newly attached devices, and gates BadUSB threats before connecting."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        journal: ForensicJournal,
        elevation: ElevationRunner | None = None,
        usb_sysfs_root: Path | None = None,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.elevation = elevation
        self.usb_sysfs_root = usb_sysfs_root or Path("/sys/bus/usb/devices")
        self._known_keys: dict[str, PeripheralDevice] = {}
        self._baseline_seeded: bool = False
        self.recent_events: list[UsbPingEvent] = []

    @staticmethod
    def _read_txt(path: Path, default: str = "") -> str:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            pass
        return default

    def scan_usb_devices(self) -> list[PeripheralDevice]:
        sys_name = platform.system()
        if sys_name == "Linux" and self.usb_sysfs_root.exists():
            return self._scan_linux_sysfs_usb()
        if sys_name == "Darwin" and shutil.which("system_profiler"):
            return self._scan_macos_usb()
        if sys_name == "Windows" and shutil.which("powershell"):
            return self._scan_windows_pnp()
        return []

    def _scan_linux_sysfs_usb(self) -> list[PeripheralDevice]:
        devices: list[PeripheralDevice] = []
        try:
            entries = sorted(self.usb_sysfs_root.iterdir(), key=lambda p: p.name)
        except OSError:
            return []

        for dev_dir in entries:
            name_dir = dev_dir.name
            # Skip individual interface subnodes like 1-2:1.0 and root hubs usb1 unless needed
            if ":" in name_dir or name_dir.startswith("usb"):
                continue
            vid = self._read_txt(dev_dir / "idVendor").lower()
            pid = self._read_txt(dev_dir / "idProduct").lower()
            if not vid and not pid:
                continue
            vendor = self._read_txt(dev_dir / "manufacturer", "Unknown Vendor")
            product = self._read_txt(dev_dir / "product", f"USB Device {vid}:{pid}")
            serial = self._read_txt(dev_dir / "serial")
            dev_class = self._read_txt(dev_dir / "bDeviceClass", "00").lower().zfill(2)
            auth_raw = self._read_txt(dev_dir / "authorized", "1")
            authorized = auth_raw != "0"

            iface_classes: set[str] = set()
            has_kbd = False
            try:
                for sub in dev_dir.iterdir():
                    if sub.is_dir() and sub.name.startswith(name_dir + ":"):
                        ic = self._read_txt(sub / "bInterfaceClass").lower().zfill(2)
                        ip = self._read_txt(sub / "bInterfaceProtocol").lower().zfill(2)
                        isub = self._read_txt(sub / "bInterfaceSubClass").lower().zfill(2)
                        if ic:
                            iface_classes.add(ic)
                        if ic == "03" and (ip == "01" or isub == "01"):
                            has_kbd = True
            except OSError:
                pass
            if not iface_classes and dev_class != "00":
                iface_classes.add(dev_class)

            readable_classes = [
                f"{c}:{USB_CLASS_NAMES.get(c, 'Device')}" for c in sorted(iface_classes)
            ]
            dev_key = f"usb:{vid}:{pid}:{serial or name_dir}"
            devices.append(
                PeripheralDevice(
                    device_key=dev_key,
                    bus="usb",
                    name=product,
                    vendor=vendor,
                    vid=vid,
                    pid=pid,
                    serial=serial,
                    sysfs_path=str(dev_dir),
                    interface_classes=readable_classes,
                    has_hid_keyboard=has_kbd,
                    authorized=authorized,
                    status="connected" if authorized else "blocked",
                    plain_description=", ".join(readable_classes) or "USB Peripheral",
                )
            )
        return devices

    @staticmethod
    def _scan_macos_usb() -> list[PeripheralDevice]:
        out: list[PeripheralDevice] = []
        try:
            p = subprocess.run(
                ["system_profiler", "SPUSBDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if p.returncode != 0:
                return out
            raw = json.loads(p.stdout or "{}")

            def _walk(items: list[dict[str, Any]]) -> None:
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    vid = str(it.get("vendor_id", "")).replace("0x", "").split()[0].lower()
                    pid = str(it.get("product_id", "")).replace("0x", "").split()[0].lower()
                    nm = str(it.get("_name", "USB Device"))
                    if vid or pid:
                        out.append(
                            PeripheralDevice(
                                device_key=f"usb:{vid}:{pid}:{nm}",
                                bus="usb",
                                name=nm,
                                vendor=str(it.get("manufacturer", "Unknown")),
                                vid=vid,
                                pid=pid,
                                serial=str(it.get("serial_num", "")),
                                plain_description="macOS USB Device",
                            )
                        )
                    if isinstance(it.get("_items"), list):
                        _walk(it["_items"])

            for bus_entry in raw.get("SPUSBDataType", []):
                if isinstance(bus_entry, dict) and isinstance(bus_entry.get("_items"), list):
                    _walk(bus_entry["_items"])
        except Exception:
            pass
        return out

    _win_pnp_cache: tuple[float, list[PeripheralDevice]] | None = None

    @staticmethod
    def _scan_windows_pnp() -> list[PeripheralDevice]:
        import time as _t

        now_m = _t.monotonic()
        cached = UsbAndPeripheralGuard._win_pnp_cache
        if cached is not None and (now_m - cached[0]) < 15.0:
            return list(cached[1])
        out: list[PeripheralDevice] = []
        try:
            p = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-PnpDevice -PresentOnly | Select-Object -First 40 Class,FriendlyName,InstanceId,Status | ConvertTo-Json",
                ],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if p.returncode != 0 or not p.stdout.strip():
                return out
            raw = json.loads(p.stdout)
            items = raw if isinstance(raw, list) else [raw]
            for it in items:
                if not isinstance(it, dict):
                    continue
                iid = str(it.get("InstanceId", ""))
                cls = str(it.get("Class", "USB"))
                nm = str(it.get("FriendlyName") or cls)
                out.append(
                    PeripheralDevice(
                        device_key=f"pnp:{iid or nm}",
                        bus="usb" if "USB" in iid.upper() else cls.lower(),
                        name=nm,
                        vendor=cls,
                        sysfs_path=iid,
                        authorized=(str(it.get("Status", "OK")).upper() == "OK"),
                        plain_description=f"{cls} ({it.get('Status', 'OK')})",
                    )
                )
        except Exception:
            pass
        UsbAndPeripheralGuard._win_pnp_cache = (now_m, list(out))
        return out

    def scan_all_peripherals(self) -> list[PeripheralDevice]:
        """Enumerates USB devices + input devices, audio cards, displays, network & Bluetooth radios."""
        peripherals = self.scan_usb_devices()
        seen = {d.device_key for d in peripherals}

        if platform.system() == "Linux":
            # Input devices (keyboards, mice, controllers, touchpads)
            inp_root = Path("/sys/class/input")
            if inp_root.exists():
                for node in sorted(inp_root.glob("input*"))[:25]:
                    nm = self._read_txt(node / "name")
                    if nm and f"input:{nm}" not in seen:
                        seen.add(f"input:{nm}")
                        peripherals.append(
                            PeripheralDevice(
                                device_key=f"input:{nm}",
                                bus="input",
                                name=nm,
                                vendor="Input Subsystem",
                                sysfs_path=str(node),
                                plain_description="Keyboard / Mouse / Input Device",
                            )
                        )
            # Displays / Monitors
            drm_root = Path("/sys/class/drm")
            if drm_root.exists():
                for conn in sorted(drm_root.glob("card*-*"))[:12]:
                    st = self._read_txt(conn / "status")
                    if st == "connected":
                        key = f"display:{conn.name}"
                        if key not in seen:
                            seen.add(key)
                            peripherals.append(
                                PeripheralDevice(
                                    device_key=key,
                                    bus="display",
                                    name=conn.name,
                                    vendor="Display / Monitor",
                                    sysfs_path=str(conn),
                                    plain_description="Connected Display Output",
                                )
                            )
            # Audio cards
            snd_root = Path("/sys/class/sound")
            if snd_root.exists():
                for card in sorted(snd_root.glob("card*"))[:10]:
                    if not (card / "id").exists():
                        continue
                    nm = self._read_txt(card / "id", card.name)
                    key = f"audio:{nm}"
                    if key not in seen:
                        seen.add(key)
                        peripherals.append(
                            PeripheralDevice(
                                device_key=key,
                                bus="audio",
                                name=nm,
                                vendor="Audio / DAC",
                                sysfs_path=str(card),
                                plain_description="Sound Card / Audio Endpoint",
                            )
                        )
            # Bluetooth radios
            bt_root = Path("/sys/class/bluetooth")
            if bt_root.exists():
                for hci in sorted(bt_root.glob("hci*"))[:4]:
                    key = f"bluetooth:{hci.name}"
                    if key not in seen:
                        seen.add(key)
                        peripherals.append(
                            PeripheralDevice(
                                device_key=key,
                                bus="bluetooth",
                                name=f"Bluetooth Adapter ({hci.name})",
                                vendor="Bluetooth",
                                sysfs_path=str(hci),
                                plain_description="Bluetooth Radio Controller",
                            )
                        )
            # Network interfaces
            net_root = Path("/sys/class/net")
            if net_root.exists():
                for nic in sorted(net_root.iterdir())[:12]:
                    if nic.name == "lo":
                        continue
                    oper = self._read_txt(nic / "operstate", "unknown")
                    key = f"net:{nic.name}"
                    if key not in seen:
                        seen.add(key)
                        peripherals.append(
                            PeripheralDevice(
                                device_key=key,
                                bus="network",
                                name=nic.name,
                                vendor="Network Interface",
                                sysfs_path=str(nic),
                                plain_description=f"Network Link ({oper})",
                            )
                        )

        return peripherals

    def evaluate_badusb(self, dev: PeripheralDevice, config: AppConfig) -> tuple[bool, list[str]]:
        """Inspects a USB device for BadUSB signatures. Returns (is_safe_to_connect, reasons)."""
        if dev.bus != "usb":
            return True, []

        vid_pid = f"{dev.vid}:{dev.pid}".lower()
        trusted = {t.lower() for t in getattr(config, "usb_trusted_ids", [])}
        blocked = {b.lower() for b in getattr(config, "usb_blocked_ids", [])}

        if vid_pid in blocked or dev.device_key.lower() in blocked:
            return False, ["Explicitly blocked by user USB blocklist."]
        if vid_pid in trusted or dev.device_key.lower() in trusted:
            return True, []

        class_codes = {c.split(":", 1)[0].lower() for c in dev.interface_classes}
        reasons: list[str] = []

        # 1. Classic BadUSB / Rubber-Ducky / BashBunny signature: HID + Mass Storage or CDC/RNDIS
        if "03" in class_codes and "08" in class_codes:
            reasons.append("Composite HID (Keyboard/Mouse) + Mass Storage detected (classic BadUSB spoof vector).")
        if ("03" in class_codes or dev.has_hid_keyboard) and ("02" in class_codes or "0a" in class_codes or "e0" in class_codes):
            reasons.append("Composite HID Keyboard + Serial/Network Gadget detected (BadUSB / PoisonTap vector).")

        # 2. Programmable microcontroller VID exposing HID keyboard or composite interfaces
        if dev.vid in PROGRAMMABLE_HID_VIDS and ("03" in class_codes or dev.has_hid_keyboard or "02" in class_codes):
            reasons.append(
                f"Programmable HID microcontroller ({PROGRAMMABLE_HID_VIDS[dev.vid]}) detected; held for manual trust approval."
            )

        # 3. Spoofed keyboard with missing vendor/product strings
        if dev.has_hid_keyboard and dev.vendor in ("Unknown Vendor", "") and dev.name.startswith("USB Device "):
            reasons.append("Anonymous HID keyboard without manufacturer descriptor.")

        return len(reasons) == 0, reasons

    def ping_device(self, dev: PeripheralDevice) -> str:
        """Actively pings sysfs/OS descriptors on a device and returns a human-readable info line."""
        if dev.bus == "usb" and dev.sysfs_path and Path(dev.sysfs_path).exists():
            p = Path(dev.sysfs_path)
            speed = self._read_txt(p / "speed", "?")
            power = self._read_txt(p / "bMaxPower", "?")
            cls_txt = ", ".join(dev.interface_classes) or "USB"
            return f"{dev.vendor} {dev.name} [{dev.vid}:{dev.pid}] · {cls_txt} · {speed} Mbps · MaxPower {power}"
        return f"{dev.name} ({dev.vendor}) · {dev.plain_description}"

    def set_device_authorized(
        self,
        dev: PeripheralDevice,
        allow: bool,
        dry_run: bool = False,
    ) -> tuple[bool, str]:
        """Enforces USB allow/block state on the OS (Linux sysfs authorized / Windows PnP)."""
        if dry_run:
            return True, f"[DRY RUN] Would {'authorize' if allow else 'block'} USB device '{dev.name}'."
        if platform.system() == "Linux" and dev.sysfs_path:
            auth_file = Path(dev.sysfs_path) / "authorized"
            if auth_file.exists():
                val = "1\n" if allow else "0\n"
                try:
                    auth_file.write_text(val, encoding="utf-8")
                    dev.authorized = allow
                    return True, f"{'Authorized' if allow else 'Blocked'} USB device '{dev.name}'."
                except PermissionError:
                    if self.elevation is not None:
                        eres = self.elevation.write_privileged_file(
                            auth_file,
                            val,
                            reason=f"{'Allow cleared' if allow else 'Block suspected BadUSB'} device {dev.vendor} {dev.name} ({dev.vid}:{dev.pid})",
                        )
                        if eres.ok:
                            dev.authorized = allow
                            return True, f"{'Authorized' if allow else 'Blocked'} USB device '{dev.name}' via sudo."
                        return False, eres.message
                    return False, f"Root permission required to {'authorize' if allow else 'block'} {dev.name}."
        elif platform.system() == "Windows" and dev.sysfs_path and shutil.which("powershell"):
            verb = "Enable-PnpDevice" if allow else "Disable-PnpDevice"
            if self.elevation is not None:
                eres = self.elevation.run_privileged(
                    ["powershell", "-NoProfile", "-Command", f'{verb} -InstanceId "{dev.sysfs_path}" -Confirm:$false'],
                    reason=f"{'Allow' if allow else 'Block'} USB device {dev.name}",
                )
                return eres.ok, eres.message
        return False, f"OS device gating logged only for '{dev.name}' (manual unplug recommended if blocked)."

    def set_strict_precheck_gate(self, enable: bool, dry_run: bool = False) -> tuple[bool, str]:
        """Sets Linux USB host controller authorized_default=0 so new USB devices await BadUSB check before connecting."""
        if platform.system() != "Linux" or not self.usb_sysfs_root.exists():
            return True, "Software BadUSB pre-check monitor active."
        hosts = sorted(self.usb_sysfs_root.glob("usb*"))
        if not hosts:
            return True, "No USB host controllers found."
        target_val = "0\n" if enable else "1\n"
        if dry_run:
            return True, f"[DRY RUN] Would set authorized_default={target_val.strip()} on {len(hosts)} USB host controller(s)."
        updated = 0
        for h in hosts:
            af = h / "authorized_default"
            if not af.exists():
                continue
            try:
                af.write_text(target_val, encoding="utf-8")
                updated += 1
            except PermissionError:
                if self.elevation is not None:
                    eres = self.elevation.write_privileged_file(
                        af,
                        target_val,
                        reason=f"{'Enable' if enable else 'Disable'} BadUSB pre-connection gate on {h.name}",
                    )
                    if eres.ok:
                        updated += 1
                    elif eres.cancelled:
                        return False, eres.message
        self.journal.log_event(
            "BadUSBGuard",
            "strict_precheck_gate",
            f"{'Enabled' if enable else 'Disabled'} USB pre-connection gate on {updated}/{len(hosts)} host controller(s)",
            "Prevent uninspected USB devices from binding until BadUSB check clears them",
            "ok" if updated else "warning",
        )
        return updated > 0, (
            f"BadUSB pre-connection gate {'ENABLED' if enable else 'DISABLED'} on {updated} USB controller(s)."
            if updated
            else "Could not modify USB host controller authorized_default."
        )

    def poll_and_guard(self, config: AppConfig, dry_run: bool = False) -> list[UsbPingEvent]:
        """Scans all attached hardware, pings newly added devices, runs BadUSB inspection, and enforces allow/block."""
        current = self.scan_all_peripherals()
        cur_map = {d.device_key: d for d in current}
        events: list[UsbPingEvent] = []

        # Initial seed: inspect existing USB devices quietly without spamming popups for pre-connected hardware
        if not self._baseline_seeded:
            for d in current:
                if d.bus == "usb" and getattr(config, "badusb_guard_enabled", True):
                    safe, reasons = self.evaluate_badusb(d, config)
                    if not safe:
                        d.status = "blocked"
                        d.badusb_reasons = reasons
                        self.journal.log_event(
                            "BadUSBGuard",
                            "baseline_badusb_flag",
                            f"Flagged already-connected USB '{d.vendor} {d.name}' ({d.vid}:{d.pid}): {'; '.join(reasons)}",
                            "Default-ON BadUSB Guard flagged suspicious composite/programmable device",
                            "warning",
                        )
                    else:
                        d.status = "cleared"
            self._known_keys = cur_map
            self._baseline_seeded = True
            return events

        # Detect newly attached peripherals & ping them for info
        for key, dev in cur_map.items():
            if key in self._known_keys:
                continue
            ping_info = self.ping_device(dev)
            if dev.bus == "usb" and getattr(config, "badusb_guard_enabled", True):
                safe, reasons = self.evaluate_badusb(dev, config)
                if safe:
                    dev.status = "cleared"
                    if not dev.authorized:
                        self.set_device_authorized(dev, True, dry_run=dry_run)
                    msg = f"USB Cleared & Connected: {ping_info}"
                    ev = UsbPingEvent(utc_now_iso(), key, "new_device_cleared", dev, msg)
                    self.journal.log_event("BadUSBGuard", "usb_cleared", msg, "Passed BadUSB inspection", "ok")
                else:
                    dev.status = "blocked"
                    dev.badusb_reasons = reasons
                    ok_blk, blk_msg = self.set_device_authorized(dev, False, dry_run=dry_run)
                    msg = (
                        f"BLOCKED Suspected BadUSB ({dev.vendor} {dev.name} [{dev.vid}:{dev.pid}]): "
                        f"{' '.join(reasons)} ({blk_msg})"
                    )
                    ev = UsbPingEvent(utc_now_iso(), key, "new_device_blocked", dev, msg)
                    self.journal.log_event(
                        "BadUSBGuard",
                        "usb_blocked",
                        msg,
                        "Device failed BadUSB security check — prevented connection",
                        "error" if not ok_blk else "warning",
                    )
                events.append(ev)
            else:
                msg = f"New Hardware Detected: {ping_info}"
                ev = UsbPingEvent(utc_now_iso(), key, "new_peripheral", dev, msg)
                self.journal.log_event("PeripheralMonitor", "new_peripheral", msg, "Active hardware ping", "ok")
                events.append(ev)

        self._known_keys = cur_map
        if events:
            self.recent_events = (events + self.recent_events)[:40]
        return events

    def trust_device(self, dev: PeripheralDevice, config: AppConfig, cfg_mgr: ConfigManager) -> tuple[bool, str]:
        ident = f"{dev.vid}:{dev.pid}".lower() if (dev.vid and dev.pid) else dev.device_key.lower()
        if ident in config.usb_blocked_ids:
            config.usb_blocked_ids.remove(ident)
        if ident not in config.usb_trusted_ids:
            config.usb_trusted_ids.append(ident)
        cfg_mgr.save(config)
        ok, msg = self.set_device_authorized(dev, True, dry_run=False)
        dev.status = "trusted"
        self.journal.log_event(
            "BadUSBGuard",
            "trust_usb_device",
            f"Trusted & authorized USB device '{dev.vendor} {dev.name}' ({ident})",
            "User explicitly approved device after BadUSB inspection",
            "ok",
        )
        return ok or True, f"Trusted '{dev.name}' ({ident}). {msg}"
