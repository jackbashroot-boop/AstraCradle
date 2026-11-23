"""PC Wi-Fi Mesh Array & Interactive Mesh Assistant (Dual-Radio, Concurrent AP+STA & 802.11s)."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import shutil
import subprocess

import psutil

from .config import ConfigManager
from .journal import ForensicJournal


@dataclass
class MeshAdapterCapability:
    iface: str
    phy: str
    driver: str = ""
    supports_ap: bool = False
    supports_mesh_point: bool = False
    supports_ap_sta_concurrent: bool = False
    bands: list[str] = field(default_factory=list)
    current_state: str = "unknown"
    current_ssid: str = ""


@dataclass
class MeshAssistantPlan:
    ok: bool
    mode: str  # dual_nic_mesh | concurrent_ap_sta_relay | mesh_point_80211s | ethernet_to_wifi_root | hardware_guidance
    uplink_iface: str
    uplink_ssid: str
    mesh_iface: str
    mesh_ssid: str
    passphrase: str
    band: str
    recommended_channel: int
    hardware_summary: str
    notes: list[str] = field(default_factory=list)
    bash_commands: list[str] = field(default_factory=list)
    fish_commands: list[str] = field(default_factory=list)
    teardown_commands: list[str] = field(default_factory=list)
    limitations: str = ""


class WifiMeshAssistantEngine:
    """Inspects PC Wi-Fi radios and builds/applies a Wi-Fi Mesh Array plan from user inputs."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal, elevation=None) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.elevation = elevation

    @staticmethod
    def _run_cmd(cmd: list[str], timeout: float = 3.0) -> str:
        if not shutil.which(cmd[0]):
            return ""
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            return res.stdout or ""
        except Exception:
            return ""

    def probe_adapters(
        self,
        *,
        iw_dev_out: str | None = None,
        iw_list_out: str | None = None,
        nmcli_out: str | None = None,
    ) -> list[MeshAdapterCapability]:
        iw_dev = iw_dev_out if iw_dev_out is not None else self._run_cmd(["iw", "dev"])
        iw_list = iw_list_out if iw_list_out is not None else self._run_cmd(["iw", "list"])
        nmcli_dev = (
            nmcli_out
            if nmcli_out is not None
            else self._run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"])
        )

        # Parse nmcli states/SSIDs
        nm_map: dict[str, tuple[str, str]] = {}
        for line in nmcli_dev.splitlines():
            parts = line.split(":")
            if len(parts) >= 4:
                nm_map[parts[0].strip()] = (parts[2].strip(), parts[3].strip())

        # Parse phy blocks from `iw list`
        phy_caps: dict[str, dict[str, object]] = {}
        cur_phy = ""
        in_modes = False
        in_combos = False
        for line in iw_list.splitlines():
            m_phy = re.match(r"^Wiphy\s+(\S+)", line.strip())
            if m_phy:
                cur_phy = m_phy.group(1)
                phy_caps[cur_phy] = {"ap": False, "mesh": False, "ap_sta": False, "bands": set()}
                in_modes = False
                in_combos = False
                continue
            if not cur_phy:
                continue
            s = line.strip()
            if s.startswith("Supported interface modes:"):
                in_modes = True
                in_combos = False
                continue
            if s.startswith("valid interface combinations:"):
                in_combos = True
                in_modes = False
                continue
            if s.startswith("Band 1:"):
                phy_caps[cur_phy]["bands"].add("2.4 GHz")  # type: ignore[union-attr]
            elif s.startswith("Band 2:"):
                phy_caps[cur_phy]["bands"].add("5 GHz")  # type: ignore[union-attr]
            elif s.startswith("Band 4:"):
                phy_caps[cur_phy]["bands"].add("6 GHz")  # type: ignore[union-attr]
            if in_modes and s.startswith("*"):
                low = s.lower()
                if " ap" in low or low.endswith("ap"):
                    phy_caps[cur_phy]["ap"] = True
                if "mesh point" in low:
                    phy_caps[cur_phy]["mesh"] = True
                if not s.startswith("*"):
                    in_modes = False
            elif in_modes and not s.startswith("*"):
                in_modes = False
            if in_combos and "managed" in s.lower() and "ap" in s.lower():
                phy_caps[cur_phy]["ap_sta"] = True

        # Parse `iw dev` interfaces
        adapters: list[MeshAdapterCapability] = []
        cur_phy_dev = "phy0"
        cur_iface = ""
        cur_ssid = ""
        for line in iw_dev.splitlines():
            st = line.strip()
            if st.startswith("phy#"):
                cur_phy_dev = "phy" + st.split("#", 1)[1].strip()
            elif st.startswith("Interface "):
                if cur_iface:
                    caps = phy_caps.get(cur_phy_dev, {})
                    nm_st, nm_conn = nm_map.get(cur_iface, ("unknown", ""))
                    adapters.append(
                        MeshAdapterCapability(
                            iface=cur_iface,
                            phy=cur_phy_dev,
                            supports_ap=bool(caps.get("ap")),
                            supports_mesh_point=bool(caps.get("mesh")),
                            supports_ap_sta_concurrent=bool(caps.get("ap_sta")),
                            bands=sorted(caps.get("bands") or ["2.4 GHz", "5 GHz"]),
                            current_state=nm_st,
                            current_ssid=cur_ssid or nm_conn,
                        )
                    )
                cur_iface = st.split(None, 1)[1].strip()
                cur_ssid = ""
            elif st.startswith("ssid ") and cur_iface:
                cur_ssid = st.split(None, 1)[1].strip()
        if cur_iface:
            caps = phy_caps.get(cur_phy_dev, {})
            nm_st, nm_conn = nm_map.get(cur_iface, ("unknown", ""))
            adapters.append(
                MeshAdapterCapability(
                    iface=cur_iface,
                    phy=cur_phy_dev,
                    supports_ap=bool(caps.get("ap")),
                    supports_mesh_point=bool(caps.get("mesh")),
                    supports_ap_sta_concurrent=bool(caps.get("ap_sta")),
                    bands=sorted(caps.get("bands") or ["2.4 GHz", "5 GHz"]),
                    current_state=nm_st,
                    current_ssid=cur_ssid or nm_conn,
                )
            )

        # Fallback if `iw dev` unavailable (sandbox / Windows / macOS)
        if not adapters:
            for ifname in sorted(psutil.net_if_addrs().keys()):
                low = ifname.lower()
                if any(low.startswith(p) for p in ("wl", "wlan", "wifi", "en0")):
                    nm_st, nm_conn = nm_map.get(ifname, ("detected", ""))
                    adapters.append(
                        MeshAdapterCapability(
                            iface=ifname,
                            phy="phy0",
                            supports_ap=True,
                            supports_mesh_point=False,
                            supports_ap_sta_concurrent=False,
                            bands=["2.4 GHz", "5 GHz"],
                            current_state=nm_st,
                            current_ssid=nm_conn,
                        )
                    )
        return adapters

    @staticmethod
    def _pick_channel(band: str, requested_ch: int | None, wifi_scan_rec: dict | None = None) -> int:
        if requested_ch and requested_ch > 0:
            return int(requested_ch)
        if wifi_scan_rec:
            if "5" in band and wifi_scan_rec.get("best_5"):
                return int(wifi_scan_rec["best_5"])
            if "2.4" in band and wifi_scan_rec.get("best_24"):
                return int(wifi_scan_rec["best_24"])
        if "6" in band:
            return 37
        if "2.4" in band:
            return 11
        return 149

    def build_mesh_plan(
        self,
        *,
        uplink_input: str,
        mesh_ssid: str,
        passphrase: str = "",
        band: str = "5 GHz",
        role: str = "Auto (Best Available)",
        channel: int | None = None,
        wifi_scan_rec: dict | None = None,
        adapters: list[MeshAdapterCapability] | None = None,
    ) -> MeshAssistantPlan:
        adapters = adapters if adapters is not None else self.probe_adapters()
        clean_ssid = (mesh_ssid or "AstraMesh-Array").strip()[:32]
        clean_uplink = (uplink_input or "").strip()
        pwd = (passphrase or "").strip()
        if not pwd:
            pwd = f"AstraMesh-{secrets.token_hex(4)}"

        rec_ch = self._pick_channel(band, channel, wifi_scan_rec=wifi_scan_rec)
        nm_band = "bg" if "2.4" in band else "a"

        eth_ifaces = [
            ifn for ifn in sorted(psutil.net_if_addrs().keys()) if any(ifn.lower().startswith(p) for p in ("eth", "enp", "eno", "enx"))
        ]
        uplink_is_eth = any(clean_uplink.lower() == e.lower() for e in eth_ifaces) or "eth" in clean_uplink.lower()

        if not adapters:
            return MeshAssistantPlan(
                ok=False,
                mode="hardware_guidance",
                uplink_iface=clean_uplink or "eth0",
                uplink_ssid=clean_uplink or "None",
                mesh_iface="wlan0",
                mesh_ssid=clean_ssid,
                passphrase=pwd,
                band=band,
                recommended_channel=rec_ch,
                hardware_summary="No Wi-Fi hardware adapters detected on this host.",
                notes=["Connect a USB or PCIe Wi-Fi adapter that supports AP/Mesh mode to broadcast a PC mesh node."],
                limitations="No wireless NIC detected.",
            )

        primary_wlan = adapters[0]
        for a in adapters:
            if clean_uplink and (a.iface.lower() == clean_uplink.lower() or a.current_ssid.lower() == clean_uplink.lower()):
                primary_wlan = a
                break

        hw_summary = "; ".join(
            f"{a.iface} ({a.phy}: AP={'YES' if a.supports_ap else 'NO'}, 802.11s={'YES' if a.supports_mesh_point else 'NO'}, AP+STA={'YES' if a.supports_ap_sta_concurrent else 'NO'}, SSID='{a.current_ssid or 'idle'}')"
            for a in adapters
        )

        # Case 1: Dedicated Dual-Radio Mesh (2+ Wi-Fi adapters)
        if len(adapters) >= 2 and not uplink_is_eth and "802.11s" not in role:
            uplink_nic = primary_wlan
            mesh_nic = next((a for a in adapters if a.iface != uplink_nic.iface), adapters[1])
            bash_cmds = [
                f"nmcli device wifi connect {shlex_q(clean_uplink or uplink_nic.current_ssid)} ifname {uplink_nic.iface} || true",
                f"nmcli device wifi hotspot ifname {mesh_nic.iface} con-name AstraMesh-Node ssid {shlex_q(clean_ssid)} band {nm_band} channel {rec_ch} password {shlex_q(pwd)}",
                "sudo sysctl -w net.ipv4.ip_forward=1",
            ]
            fish_cmds = [
                f"nmcli device wifi connect {shlex_q(clean_uplink or uplink_nic.current_ssid)} ifname {uplink_nic.iface}; or true",
                f"nmcli device wifi hotspot ifname {mesh_nic.iface} con-name AstraMesh-Node ssid {shlex_q(clean_ssid)} band {nm_band} channel {rec_ch} password {shlex_q(pwd)}",
                "sudo sysctl -w net.ipv4.ip_forward=1",
            ]
            return MeshAssistantPlan(
                ok=True,
                mode="dual_nic_mesh",
                uplink_iface=uplink_nic.iface,
                uplink_ssid=clean_uplink or uplink_nic.current_ssid or "Upstream Wi-Fi",
                mesh_iface=mesh_nic.iface,
                mesh_ssid=clean_ssid,
                passphrase=pwd,
                band=band,
                recommended_channel=rec_ch,
                hardware_summary=hw_summary,
                notes=[
                    f"Dual-Radio Mesh Ready: '{uplink_nic.iface}' stays locked to your source Wi-Fi while '{mesh_nic.iface}' broadcasts Mesh SSID '{clean_ssid}' on Channel {rec_ch} ({band}).",
                    "Dedicated radios eliminate half-duplex repeater slowdown for nearby satellites/phones.",
                ],
                bash_commands=bash_cmds,
                fish_commands=fish_cmds,
                teardown_commands=[
                    "nmcli connection down AstraMesh-Node; nmcli connection delete AstraMesh-Node"
                ],
            )

        # Case 2: Ethernet -> Wi-Fi Mesh Root
        if uplink_is_eth or "Ethernet" in role:
            eth_if = clean_uplink if clean_uplink in eth_ifaces else (eth_ifaces[0] if eth_ifaces else "eth0")
            bash_cmds = [
                f"nmcli device wifi hotspot ifname {primary_wlan.iface} con-name AstraMesh-Node ssid {shlex_q(clean_ssid)} band {nm_band} channel {rec_ch} password {shlex_q(pwd)}",
                "sudo sysctl -w net.ipv4.ip_forward=1",
            ]
            return MeshAssistantPlan(
                ok=True,
                mode="ethernet_to_wifi_root",
                uplink_iface=eth_if,
                uplink_ssid=f"Ethernet ({eth_if})",
                mesh_iface=primary_wlan.iface,
                mesh_ssid=clean_ssid,
                passphrase=pwd,
                band=band,
                recommended_channel=rec_ch,
                hardware_summary=hw_summary,
                notes=[
                    f"Ethernet-to-Wi-Fi Mesh Root: wired backhaul on '{eth_if}' feeds Wi-Fi mesh array '{clean_ssid}' on '{primary_wlan.iface}' (Channel {rec_ch}).",
                    "Zero wireless backhaul bottleneck — full Ethernet speed shared cleanly to connected devices.",
                ],
                bash_commands=bash_cmds,
                fish_commands=list(bash_cmds),
                teardown_commands=[
                    "nmcli connection down AstraMesh-Node; nmcli connection delete AstraMesh-Node"
                ],
            )

        # Case 3: 802.11s Hardware Mesh Point
        if "802.11s" in role and primary_wlan.supports_mesh_point:
            mesh_if = "astra_mesh0"
            bash_cmds = [
                f"sudo iw dev {primary_wlan.iface} interface add {mesh_if} type mp mesh_id {shlex_q(clean_ssid)}",
                f"sudo ip link set {mesh_if} up",
                f"sudo iw dev {mesh_if} mesh join {shlex_q(clean_ssid)} freq {5745 if rec_ch >= 36 else 2462}",
            ]
            return MeshAssistantPlan(
                ok=True,
                mode="mesh_point_80211s",
                uplink_iface=primary_wlan.iface,
                uplink_ssid=clean_uplink or primary_wlan.current_ssid or "Source Wi-Fi",
                mesh_iface=mesh_if,
                mesh_ssid=clean_ssid,
                passphrase=pwd,
                band=band,
                recommended_channel=rec_ch,
                hardware_summary=hw_summary,
                notes=[
                    f"802.11s Mesh Point '{clean_ssid}' created on virtual interface '{mesh_if}' over {primary_wlan.phy}.",
                    "Other 802.11s Linux routers/nodes using the same mesh_id and channel will automatically peer.",
                ],
                bash_commands=bash_cmds,
                fish_commands=list(bash_cmds),
                teardown_commands=[f"sudo iw dev {mesh_if} del"],
            )

        # Case 4: Single-Radio Concurrent AP + Station Relay
        if primary_wlan.supports_ap_sta_concurrent and primary_wlan.supports_ap:
            ap_if = "astra_ap0"
            bash_cmds = [
                f"sudo iw dev {primary_wlan.iface} interface add {ap_if} type __ap",
                f"nmcli device set {ap_if} managed yes || true",
                f"nmcli device wifi hotspot ifname {ap_if} con-name AstraMesh-Node ssid {shlex_q(clean_ssid)} band {nm_band} password {shlex_q(pwd)}",
                "sudo sysctl -w net.ipv4.ip_forward=1",
            ]
            fish_cmds = [
                f"sudo iw dev {primary_wlan.iface} interface add {ap_if} type __ap",
                f"nmcli device set {ap_if} managed yes; or true",
                f"nmcli device wifi hotspot ifname {ap_if} con-name AstraMesh-Node ssid {shlex_q(clean_ssid)} band {nm_band} password {shlex_q(pwd)}",
                "sudo sysctl -w net.ipv4.ip_forward=1",
            ]
            return MeshAssistantPlan(
                ok=True,
                mode="concurrent_ap_sta_relay",
                uplink_iface=primary_wlan.iface,
                uplink_ssid=clean_uplink or primary_wlan.current_ssid or "Source Wi-Fi",
                mesh_iface=ap_if,
                mesh_ssid=clean_ssid,
                passphrase=pwd,
                band=band,
                recommended_channel=rec_ch,
                hardware_summary=hw_summary,
                notes=[
                    f"Single-Radio AP+STA Concurrency Confirmed on {primary_wlan.iface} ({primary_wlan.phy})!",
                    f"Virtual AP '{ap_if}' shares uplink '{clean_uplink or primary_wlan.current_ssid}' as mesh node '{clean_ssid}'.",
                    "Note: Single-radio AP+STA shares the same channel as your source Wi-Fi AP.",
                ],
                bash_commands=bash_cmds,
                fish_commands=fish_cmds,
                teardown_commands=[
                    "nmcli connection down AstraMesh-Node; nmcli connection delete AstraMesh-Node; sudo iw dev astra_ap0 del"
                ],
            )

        # Case 5: Single Wi-Fi adapter lacks AP+STA concurrency -> clear hardware guidance
        return MeshAssistantPlan(
            ok=False,
            mode="hardware_guidance",
            uplink_iface=primary_wlan.iface,
            uplink_ssid=clean_uplink or primary_wlan.current_ssid or "Source Wi-Fi",
            mesh_iface=primary_wlan.iface,
            mesh_ssid=clean_ssid,
            passphrase=pwd,
            band=band,
            recommended_channel=rec_ch,
            hardware_summary=hw_summary,
            notes=[
                f"Your Wi-Fi adapter '{primary_wlan.iface}' does not report simultaneous AP+Station concurrency while staying connected to '{clean_uplink or primary_wlan.current_ssid or 'Wi-Fi'}'.",
                "Option A (Zero Extra Hardware): Switch Mesh Role to 'Ethernet -> Wi-Fi Mesh Root' so wired Ethernet feeds Wi-Fi mesh node '" + clean_ssid + "'.",
                "Option B (True Dual-Band Mesh Array): Plug in a second USB Wi-Fi adapter (e.g. MediaTek/Intel/Realtek AX) — AstraCradle will auto-pair them into a dedicated dual-radio mesh!",
            ],
            bash_commands=[
                f"# If using Ethernet uplink instead, run:\nnmcli device wifi hotspot ifname {primary_wlan.iface} con-name AstraMesh-Node ssid {shlex_q(clean_ssid)} band {nm_band} channel {rec_ch} password {shlex_q(pwd)}"
            ],
            fish_commands=[
                f"# If using Ethernet uplink instead, run:\nnmcli device wifi hotspot ifname {primary_wlan.iface} con-name AstraMesh-Node ssid {shlex_q(clean_ssid)} band {nm_band} channel {rec_ch} password {shlex_q(pwd)}"
            ],
            limitations="Single wireless radio cannot simultaneously run managed STA + AP without hardware multi-channel/AP+STA concurrency.",
        )

    def apply_mesh_plan(self, plan: MeshAssistantPlan, *, dry_run: bool = True) -> tuple[bool, str]:
        if not plan.ok:
            return False, plan.limitations or "Current Wi-Fi hardware cannot run this mesh mode directly; see Mesh Assistant options."
        if dry_run:
            self.journal.log_event(
                "WifiMeshAssistant",
                "dry_run_mesh",
                f"Previewed {plan.mode}: '{plan.mesh_ssid}' on {plan.mesh_iface} (ch {plan.recommended_channel})",
                "Dry-Run Mode active",
                "ok",
            )
            return True, f"[DRY-RUN] Mesh plan '{plan.mesh_ssid}' ({plan.mode}) ready on {plan.mesh_iface} (ch {plan.recommended_channel})."

        if not shutil.which("nmcli"):
            return False, "NetworkManager (`nmcli`) not found — copy the generated Fish/Bash commands to run manually."

        if plan.mode in ("dual_nic_mesh", "ethernet_to_wifi_root"):
            nm_band = "bg" if "2.4" in plan.band else "a"
            cmd = [
                "nmcli",
                "device",
                "wifi",
                "hotspot",
                "ifname",
                plan.mesh_iface,
                "con-name",
                "AstraMesh-Node",
                "ssid",
                plan.mesh_ssid,
                "band",
                nm_band,
                "channel",
                str(plan.recommended_channel),
                "password",
                plan.passphrase,
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=12, check=False)
                ok = res.returncode == 0
                msg = (res.stdout or res.stderr or "").strip() or ("Mesh node active" if ok else "Failed")
                self.journal.log_event(
                    "WifiMeshAssistant",
                    "apply_mesh_node",
                    f"Started {plan.mode} '{plan.mesh_ssid}' on {plan.mesh_iface}: {msg}",
                    "User applied Mesh Assistant plan",
                    "ok" if ok else "warning",
                )
                return ok, msg
            except Exception as exc:
                return False, str(exc)

        return True, f"Copy & run the generated Fish/Bash commands (requires sudo iw/sysctl) to activate {plan.mode} '{plan.mesh_ssid}'."

    def stop_mesh_node(self, *, dry_run: bool = True) -> tuple[bool, str]:
        if dry_run:
            return True, "[DRY-RUN] Would bring down 'AstraMesh-Node' and remove virtual astra_ap0/astra_mesh0 if present."
        msgs: list[str] = []
        if shutil.which("nmcli"):
            subprocess.run(["nmcli", "connection", "down", "AstraMesh-Node"], capture_output=True, timeout=5, check=False)
            res = subprocess.run(["nmcli", "connection", "delete", "AstraMesh-Node"], capture_output=True, text=True, timeout=5, check=False)
            if res.returncode == 0:
                msgs.append("Stopped AstraMesh-Node.")
        if shutil.which("iw"):
            for vif in ("astra_ap0", "astra_mesh0"):
                subprocess.run(["iw", "dev", vif, "del"], capture_output=True, timeout=3, check=False)
        self.journal.log_event(
            "WifiMeshAssistant",
            "stop_mesh_node",
            " ".join(msgs) or "Mesh cleanup requested",
            "User stopped PC Wi-Fi mesh node",
            "ok",
        )
        return True, (" ".join(msgs) or "Mesh node stopped / cleaned up.")


def shlex_q(val: str) -> str:
    import shlex

    return shlex.quote(str(val or ""))
