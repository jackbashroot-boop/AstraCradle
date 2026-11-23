"""Wi-Fi Channel Scanner, Congestion Scorer & CachyOS + Fish Auto-Optimizer."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Any

from .elevation import ElevationRunner
from .journal import ForensicJournal
from .models import utc_now_iso


_CHANNELS_24 = list(range(1, 12))
_NON_OVERLAP_24 = {1, 6, 11}
_CHANNELS_5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165]
_CHANNELS_6 = [1, 5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 45, 49, 53, 57, 61]

_BSSID_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass
class WifiApObservation:
    ssid: str
    bssid: str
    channel: int
    freq_mhz: int
    band: str  # 2.4GHz | 5GHz | 6GHz
    signal_pct: int
    rate_mbps: int = 0
    in_use: bool = False
    security: str = ""


@dataclass
class ChannelScore:
    band: str
    channel: int
    freq_mhz: int
    ap_count: int
    interference_score: float  # lower is cleaner (0 = completely clean)
    grade: str  # Clean | Moderate | Congested
    recommended: bool = False


@dataclass
class WifiOptimizationResult:
    ok: bool
    interface: str
    connected_ssid: str
    connected_bssid: str
    connected_channel: int
    connected_band: str
    connected_signal: int
    best_24_channel: int
    best_5_channel: int
    best_6_channel: int
    best_bssid_for_ssid: str
    best_bssid_channel: int
    best_bssid_band: str
    switched: bool
    action_taken: str
    notes: list[str] = field(default_factory=list)
    channel_scores: list[ChannelScore] = field(default_factory=list)
    aps: list[WifiApObservation] = field(default_factory=list)


class WifiChannelOptimizer:
    """Scans Wi-Fi congestion, picks cleanest channels/BSSIDs, and integrates with CachyOS + Fish."""

    def __init__(self, journal: ForensicJournal | None = None, state_dir: Path | None = None) -> None:
        self.journal = journal
        self.state_dir = state_dir or (journal.cfg_mgr.base_dir if journal is not None else Path.home() / ".astracradle")
        self.rollback_path = self.state_dir / "wifi_rollback_state.json"

    @staticmethod
    def _freq_to_band_and_chan(freq_mhz: int, chan_hint: int = 0) -> tuple[str, int]:
        if 2400 <= freq_mhz <= 2500:
            ch = chan_hint or (14 if freq_mhz == 2484 else max(1, (freq_mhz - 2407) // 5))
            return "2.4GHz", ch
        if 5000 <= freq_mhz <= 5925:
            ch = chan_hint or max(36, (freq_mhz - 5000) // 5)
            return "5GHz", ch
        if 5925 < freq_mhz <= 7125:
            ch = chan_hint or max(1, (freq_mhz - 5950) // 5)
            return "6GHz", ch
        if chan_hint >= 36:
            return "5GHz", chan_hint
        return "2.4GHz", max(1, chan_hint)

    @staticmethod
    def _split_nmcli_escaped(line: str) -> list[str]:
        """Splits an nmcli `-t` colon-delimited line respecting `\\:` escapes."""
        fields: list[str] = []
        buf: list[str] = []
        esc = False
        for ch in line:
            if esc:
                buf.append(ch)
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == ":":
                fields.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        fields.append("".join(buf))
        return fields

    @staticmethod
    def detect_wifi_interface() -> str | None:
        if platform.system() == "Linux":
            net_dir = Path("/sys/class/net")
            if net_dir.exists():
                try:
                    for iface in sorted(net_dir.iterdir()):
                        if (iface / "wireless").exists():
                            return iface.name
                except OSError:
                    pass
            if shutil.which("nmcli"):
                try:
                    proc = subprocess.run(
                        ["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        check=False,
                    )
                    for ln in (proc.stdout or "").splitlines():
                        parts = ln.split(":")
                        if len(parts) >= 2 and parts[1].strip() == "wifi":
                            return parts[0].strip()
                except Exception:
                    pass
        return None

    @staticmethod
    def _get_active_wifi_connection(iface: str | None = None) -> tuple[str | None, str | None]:
        """Returns (connection_name, ssid) for active Wi-Fi via nmcli."""
        if not shutil.which("nmcli"):
            return None, None
        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "con", "show", "--active"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            for ln in (proc.stdout or "").splitlines():
                parts = WifiChannelOptimizer._split_nmcli_escaped(ln)
                if len(parts) >= 3 and "wireless" in parts[1].lower():
                    if iface is None or parts[2].strip() == iface:
                        return parts[0].strip(), parts[0].strip()
        except Exception:
            pass
        return None, None

    @staticmethod
    def _is_powersave_enabled(iface: str) -> bool | None:
        if not iface or not _IFACE_RE.match(iface) or not shutil.which("iw"):
            return None
        try:
            proc = subprocess.run(
                ["iw", "dev", iface, "get", "power_save"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            txt = (proc.stdout or "").lower()
            if "power save: on" in txt:
                return True
            if "power save: off" in txt:
                return False
        except Exception:
            pass
        return None

    def scan_wifi_environment(self, rescan: bool = True) -> tuple[list[WifiApObservation], dict[str, Any]]:
        """Scans nearby Wi-Fi APs and live link metadata using nmcli (with iw fallback)."""
        iface = self.detect_wifi_interface() or "wlan0"
        aps: list[WifiApObservation] = []
        link_meta: dict[str, Any] = {
            "interface": iface,
            "powersave": self._is_powersave_enabled(iface),
            "rx_retries": 0,
        }

        if shutil.which("nmcli"):
            cmd = [
                "nmcli",
                "-t",
                "-f",
                "IN-USE,SSID,BSSID,CHAN,FREQ,RATE,SIGNAL,SECURITY",
                "dev",
                "wifi",
                "list",
            ]
            if rescan:
                cmd.extend(["--rescan", "yes"])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
                for ln in (proc.stdout or "").splitlines():
                    if not ln.strip():
                        continue
                    parts = self._split_nmcli_escaped(ln)
                    if len(parts) < 7:
                        continue
                    in_use = parts[0].strip() == "*"
                    ssid = parts[1].strip() or "<hidden>"
                    bssid = parts[2].strip().upper()
                    chan_m = re.search(r"\d+", parts[3])
                    freq_m = re.search(r"\d+", parts[4])
                    rate_m = re.search(r"\d+", parts[5])
                    sig_m = re.search(r"\d+", parts[6])
                    sec = parts[7].strip() if len(parts) > 7 else ""
                    freq_mhz = int(freq_m.group(0)) if freq_m else 0
                    chan_raw = int(chan_m.group(0)) if chan_m else 0
                    band, chan = self._freq_to_band_and_chan(freq_mhz, chan_raw)
                    aps.append(
                        WifiApObservation(
                            ssid=ssid,
                            bssid=bssid,
                            channel=chan,
                            freq_mhz=freq_mhz,
                            band=band,
                            signal_pct=int(sig_m.group(0)) if sig_m else 0,
                            rate_mbps=int(rate_m.group(0)) if rate_m else 0,
                            in_use=in_use,
                            security=sec,
                        )
                    )
            except Exception:
                pass

        # Fallback to `iw dev <iface> scan` if nmcli yielded nothing
        if not aps and shutil.which("iw") and _IFACE_RE.match(iface):
            try:
                proc = subprocess.run(
                    ["iw", "dev", iface, "scan"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                cur: dict[str, Any] = {}
                for ln in (proc.stdout or "").splitlines():
                    s = ln.strip()
                    if s.startswith("BSS "):
                        if cur.get("bssid"):
                            band, ch = self._freq_to_band_and_chan(cur.get("freq", 0), cur.get("chan", 0))
                            aps.append(
                                WifiApObservation(
                                    ssid=cur.get("ssid", "<hidden>"),
                                    bssid=cur["bssid"],
                                    channel=ch,
                                    freq_mhz=cur.get("freq", 0),
                                    band=band,
                                    signal_pct=cur.get("sig", 50),
                                    in_use=cur.get("in_use", False),
                                )
                            )
                        bm = re.search(r"([0-9a-fA-F:]{17})", s)
                        cur = {"bssid": bm.group(1).upper() if bm else "", "in_use": "associated" in s.lower()}
                    elif s.startswith("freq:"):
                        fm = re.search(r"\d+", s)
                        if fm:
                            cur["freq"] = int(fm.group(0))
                    elif s.startswith("SSID:"):
                        cur["ssid"] = s.split(":", 1)[1].strip() or "<hidden>"
                    elif s.startswith("signal:"):
                        sm = re.search(r"-?\d+", s)
                        if sm:
                            dbm = int(sm.group(0))
                            cur["sig"] = max(0, min(100, 2 * (dbm + 100)))
                    elif "DS Parameter set: channel" in s:
                        cm = re.search(r"channel\s+(\d+)", s)
                        if cm:
                            cur["chan"] = int(cm.group(1))
                if cur.get("bssid"):
                    band, ch = self._freq_to_band_and_chan(cur.get("freq", 0), cur.get("chan", 0))
                    aps.append(
                        WifiApObservation(
                            ssid=cur.get("ssid", "<hidden>"),
                            bssid=cur["bssid"],
                            channel=ch,
                            freq_mhz=cur.get("freq", 0),
                            band=band,
                            signal_pct=cur.get("sig", 50),
                            in_use=cur.get("in_use", False),
                        )
                    )
            except Exception:
                pass

        return aps, link_meta

    @staticmethod
    def score_channels(aps: list[WifiApObservation]) -> dict[str, list[ChannelScore]]:
        """Computes RF congestion & adjacent-channel overlap score per band (lower = cleaner)."""
        # 2.4 GHz scoring (accounts for +/- 4 channel RF skirt overlap, favors non-overlapping 1, 6, 11)
        scores_24: list[ChannelScore] = []
        aps_24 = [a for a in aps if a.band == "2.4GHz"]
        for ch in _CHANNELS_24:
            interf = 0.0
            co_cnt = 0
            for a in aps_24:
                if a.in_use:
                    continue
                dist = abs(ch - a.channel)
                if dist == 0:
                    co_cnt += 1
                    interf += max(10.0, float(a.signal_pct)) * 1.0
                elif dist <= 4:
                    overlap_weight = (5 - dist) / 5.0
                    interf += max(8.0, float(a.signal_pct)) * overlap_weight * 0.72
            if ch not in _NON_OVERLAP_24:
                interf += 6.0  # slight preference for standard non-overlapping 1, 6, 11
            grade = "Clean" if interf < 35 else ("Moderate" if interf < 95 else "Congested")
            scores_24.append(
                ChannelScore("2.4GHz", ch, 2407 + ch * 5, co_cnt, round(interf, 1), grade, False)
            )
        if scores_24:
            best24 = min(scores_24, key=lambda c: (c.interference_score, 0 if c.channel in _NON_OVERLAP_24 else 1))
            best24.recommended = True

        # 5 GHz scoring (20 MHz non-overlapping channels)
        scores_5: list[ChannelScore] = []
        aps_5 = [a for a in aps if a.band == "5GHz"]
        for ch in _CHANNELS_5:
            interf = 0.0
            co_cnt = 0
            for a in aps_5:
                if a.in_use:
                    continue
                dist = abs(ch - a.channel)
                if dist == 0:
                    co_cnt += 1
                    interf += max(10.0, float(a.signal_pct)) * 1.0
                elif dist <= 4:
                    interf += max(6.0, float(a.signal_pct)) * 0.35
            grade = "Clean" if interf < 30 else ("Moderate" if interf < 85 else "Congested")
            scores_5.append(
                ChannelScore("5GHz", ch, 5000 + ch * 5, co_cnt, round(interf, 1), grade, False)
            )
        if scores_5:
            best5 = min(scores_5, key=lambda c: (c.interference_score, 0 if c.channel in (36, 40, 44, 48, 149, 153, 157, 161) else 1))
            best5.recommended = True

        # 6 GHz scoring
        scores_6: list[ChannelScore] = []
        aps_6 = [a for a in aps if a.band == "6GHz"]
        for ch in _CHANNELS_6:
            interf = 0.0
            co_cnt = 0
            for a in aps_6:
                if a.in_use:
                    continue
                if a.channel == ch:
                    co_cnt += 1
                    interf += max(10.0, float(a.signal_pct))
            grade = "Clean" if interf < 30 else ("Moderate" if interf < 80 else "Congested")
            scores_6.append(
                ChannelScore("6GHz", ch, 5950 + ch * 5, co_cnt, round(interf, 1), grade, False)
            )
        if scores_6:
            best6 = min(scores_6, key=lambda c: c.interference_score)
            best6.recommended = True

        return {"2.4GHz": scores_24, "5GHz": scores_5, "6GHz": scores_6}

    def _save_rollback_state(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.rollback_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def optimize_connection(
        self,
        dry_run: bool = False,
        prefer_5ghz: bool = True,
        disable_powersave: bool = True,
        min_improvement: float = 12.0,
        rescan: bool = True,
        aps_override: list[WifiApObservation] | None = None,
    ) -> WifiOptimizationResult:
        """Evaluates current channel congestion and switches/tunes Wi-Fi to the cleanest BSSID/band."""
        if aps_override is not None:
            aps = list(aps_override)
            iface = self.detect_wifi_interface() or "wlan0"
            link_meta = {"interface": iface, "powersave": self._is_powersave_enabled(iface)}
        else:
            aps, link_meta = self.scan_wifi_environment(rescan=rescan)
            iface = str(link_meta.get("interface") or "wlan0")

        by_band = self.score_channels(aps)
        all_scores = by_band["2.4GHz"] + by_band["5GHz"] + by_band["6GHz"]
        best24 = next((c.channel for c in by_band["2.4GHz"] if c.recommended), 1)
        best5 = next((c.channel for c in by_band["5GHz"] if c.recommended), 149)
        best6 = next((c.channel for c in by_band["6GHz"] if c.recommended), 37)

        cur_ap = next((a for a in aps if a.in_use), None)
        cur_ssid = cur_ap.ssid if cur_ap else ""
        cur_bssid = cur_ap.bssid if cur_ap else ""
        cur_chan = cur_ap.channel if cur_ap else 0
        cur_band = cur_ap.band if cur_ap else "5GHz"
        cur_sig = cur_ap.signal_pct if cur_ap else 0

        score_lookup = {(c.band, c.channel): c.interference_score for c in all_scores}
        cur_interf = score_lookup.get((cur_band, cur_chan), 0.0) if cur_ap else 0.0

        # Candidate BSSIDs sharing the exact same SSID (e.g. dual-band 2.4/5/6 GHz or mesh nodes)
        same_ssid = [
            a
            for a in aps
            if cur_ssid and a.ssid == cur_ssid and a.bssid and a.bssid != cur_bssid and a.signal_pct >= 32
        ]

        def _candidate_rank(ap: WifiApObservation) -> float:
            ch_pen = score_lookup.get((ap.band, ap.channel), 25.0)
            band_bonus = -22.0 if (prefer_5ghz and ap.band in ("5GHz", "6GHz")) else 0.0
            sig_bonus = -0.25 * float(ap.signal_pct)
            return ch_pen + band_bonus + sig_bonus

        best_alt: WifiApObservation | None = None
        if same_ssid:
            best_alt = min(same_ssid, key=_candidate_rank)

        notes: list[str] = [
            f"Cleanest RF channels right now -> 2.4GHz: ch {best24} | 5GHz: ch {best5} | 6GHz: ch {best6}."
        ]
        switched = False
        actions: list[str] = []

        con_name, _ = self._get_active_wifi_connection(iface)
        if cur_ap:
            notes.append(
                f"Active link: '{cur_ssid}' ({cur_bssid}) on {cur_band} ch {cur_chan} (Signal {cur_sig}%, Interference {cur_interf:.1f})."
            )
            if best_alt is not None:
                alt_interf = score_lookup.get((best_alt.band, best_alt.channel), 25.0)
                should_switch = (
                    (prefer_5ghz and cur_band == "2.4GHz" and best_alt.band in ("5GHz", "6GHz") and best_alt.signal_pct >= 40)
                    or ((cur_interf - alt_interf) >= min_improvement)
                )
                if should_switch:
                    if dry_run:
                        actions.append(
                            f"DRY-RUN: Would roam '{cur_ssid}' from ch {cur_chan} ({cur_band}) -> BSSID {best_alt.bssid} ch {best_alt.channel} ({best_alt.band}, Interference {alt_interf:.1f})"
                        )
                    elif shutil.which("nmcli") and _BSSID_RE.match(best_alt.bssid):
                        self._save_rollback_state(
                            {
                                "saved_at": utc_now_iso(),
                                "interface": iface,
                                "connection": con_name or cur_ssid,
                                "previous_bssid": cur_bssid,
                                "previous_band": cur_band,
                                "powersave_was": link_meta.get("powersave"),
                            }
                        )
                        try:
                            if con_name:
                                band_val = "a" if best_alt.band in ("5GHz", "6GHz") else "bg"
                                subprocess.run(
                                    ["nmcli", "con", "modify", con_name, "802-11-wireless.band", band_val],
                                    capture_output=True,
                                    text=True,
                                    timeout=4,
                                    check=False,
                                )
                            proc = subprocess.run(
                                ["nmcli", "dev", "wifi", "connect", best_alt.bssid, "ifname", iface],
                                capture_output=True,
                                text=True,
                                timeout=12,
                                check=False,
                            )
                            if proc.returncode == 0:
                                switched = True
                                actions.append(
                                    f"Switched '{cur_ssid}' -> cleaner BSSID {best_alt.bssid} (ch {best_alt.channel}, {best_alt.band})"
                                )
                            else:
                                notes.append(f"Roam notice: {(proc.stderr or proc.stdout).strip()}")
                        except Exception as exc:
                            notes.append(f"Roam error: {exc}")
                else:
                    notes.append("Current BSSID is already on the cleanest available channel for this SSID.")
            else:
                notes.append(
                    f"Single-AP SSID detected; set router/AP to ch {best5} (5GHz) / ch {best24} (2.4GHz) for lowest congestion."
                )

        if disable_powersave and link_meta.get("powersave") is True and _IFACE_RE.match(iface):
            if dry_run:
                actions.append(f"DRY-RUN: Would disable Wi-Fi power_save on {iface}")
            elif shutil.which("iw"):
                try:
                    proc = subprocess.run(
                        ["iw", "dev", iface, "set", "power_save", "off"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                        check=False,
                    )
                    if proc.returncode == 0:
                        actions.append(f"Disabled Wi-Fi power_save on {iface} for lower jitter")
                except Exception:
                    pass

        action_str = "; ".join(actions) if actions else f"Optimal channels verified (2.4GHz ch {best24}, 5GHz ch {best5})"
        self.journal.log_event(
            "WifiChannelOptimizer",
            "optimize_connection",
            action_str,
            "Select cleanest Wi-Fi channel/BSSID on connect and during traffic congestion checks",
            "ok",
        )
        return WifiOptimizationResult(
            ok=True,
            interface=iface,
            connected_ssid=cur_ssid,
            connected_bssid=cur_bssid,
            connected_channel=cur_chan,
            connected_band=cur_band,
            connected_signal=cur_sig,
            best_24_channel=best24,
            best_5_channel=best5,
            best_6_channel=best6,
            best_bssid_for_ssid=best_alt.bssid if best_alt else "",
            best_bssid_channel=best_alt.channel if best_alt else 0,
            best_bssid_band=best_alt.band if best_alt else "",
            switched=switched,
            action_taken=action_str,
            notes=notes,
            channel_scores=all_scores,
            aps=aps,
        )

    def recommend_channels(
        self,
        prefer_5ghz: bool = True,
        disable_powersave: bool = True,
        rescan: bool = True,
        aps_override: list[WifiApObservation] | None = None,
    ) -> dict[str, Any]:
        """Scans channels non-destructively and builds a 1-button Yes/No recommendation when less traffic is found."""
        res = self.optimize_connection(
            dry_run=True,
            prefer_5ghz=prefer_5ghz,
            disable_powersave=disable_powersave,
            rescan=rescan,
            aps_override=aps_override,
        )
        can_roam = bool(res.best_bssid_for_ssid and "DRY-RUN:" in res.action_taken)
        powersave_note = "Wi-Fi power_save is ON (can disable for lower latency)" if "power_save" in res.action_taken else ""
        has_rec = bool(can_roam or powersave_note)
        if can_roam:
            summary = (
                f"Switch '{res.connected_ssid}' from {res.connected_band} Ch {res.connected_channel} "
                f"to cleaner {res.best_bssid_band} Ch {res.best_bssid_channel} ({res.best_bssid_for_ssid})?"
            )
        elif res.connected_ssid:
            summary = (
                f"Best low-traffic channels for '{res.connected_ssid}': "
                f"2.4 GHz Ch {res.best_24_channel} · 5 GHz Ch {res.best_5_channel} · 6 GHz Ch {res.best_6_channel}."
            )
        else:
            summary = (
                f"Cleanest channels nearby: 2.4 GHz Ch {res.best_24_channel} · "
                f"5 GHz Ch {res.best_5_channel} · 6 GHz Ch {res.best_6_channel}."
            )
        details = list(res.notes)
        if powersave_note:
            details.append(powersave_note)
        return {
            "has_recommendation": has_rec,
            "can_roam": can_roam,
            "summary": summary,
            "details": details,
            "best_24_channel": res.best_24_channel,
            "best_5_channel": res.best_5_channel,
            "best_6_channel": res.best_6_channel,
            "recommended_bssid": res.best_bssid_for_ssid,
            "recommended_channel": res.best_bssid_channel,
            "recommended_band": res.best_bssid_band,
            "result": res,
        }

    def restore_wifi_settings(self) -> tuple[bool, str]:
        if not self.rollback_path.exists():
            return True, "No custom Wi-Fi band/BSSID override recorded; NetworkManager defaults are intact."
        try:
            st = json.loads(self.rollback_path.read_text(encoding="utf-8"))
        except Exception:
            return False, "Could not parse wifi_rollback_state.json."

        con_name = str(st.get("connection") or "")
        iface = str(st.get("interface") or "")
        notes: list[str] = []
        if con_name and shutil.which("nmcli"):
            subprocess.run(
                ["nmcli", "con", "modify", con_name, "802-11-wireless.band", "", "802-11-wireless.bssid", ""],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            notes.append(f"Cleared band/BSSID lock on '{con_name}'")
        if st.get("powersave_was") is True and iface and _IFACE_RE.match(iface) and shutil.which("iw"):
            subprocess.run(["iw", "dev", iface, "set", "power_save", "on"], capture_output=True, text=True, timeout=3, check=False)
            notes.append(f"Restored power_save on {iface}")
        self.rollback_path.unlink(missing_ok=True)
        msg = "Wi-Fi overrides restored cleanly" + (f" ({'; '.join(notes)})" if notes else "")
        self.journal.log_event("WifiChannelOptimizer", "restore_wifi", msg, "User reverted Wi-Fi optimizer overrides", "ok")
        return True, msg

    # ------------------------------------------------------------------
    # CachyOS + Fish Shell + NetworkManager + Systemd User Timer Setup
    # ------------------------------------------------------------------
    @staticmethod
    def _fish_functions_content() -> str:
        return """# AstraCradle Wi-Fi Channel Auto-Optimizer for CachyOS + Fish Shell
# Auto-generated by AstraCradle — safe to re-generate anytime.

function wifi-scan --description "Scan Wi-Fi channels and show congestion scores"
    python3 -m astracradle --wifi-scan $argv
end

function wifi-best --description "Switch/tune Wi-Fi to the cleanest channel and BSSID right now"
    python3 -m astracradle --wifi-optimize $argv
end

function wifi-watch --description "Monitor Wi-Fi traffic congestion and re-optimize periodically"
    python3 -m astracradle --wifi-watch $argv
end

function wifi-restore --description "Revert any AstraCradle Wi-Fi band/BSSID overrides"
    python3 -m astracradle --wifi-restore $argv
end

function wifi-status --description "Show systemd timer & NetworkManager Wi-Fi optimizer status"
    systemctl --user status astracradle-wifi-opt.timer --no-pager 2>/dev/null; or echo "Timer not running"
    nmcli -f IN-USE,SSID,CHAN,FREQ,SIGNAL,RATE dev wifi list
end
"""

    @staticmethod
    def _nm_dispatcher_script() -> str:
        return """#!/bin/sh
# /etc/NetworkManager/dispatcher.d/90-astracradle-wifi-channel
# Automatically optimizes Wi-Fi channel/BSSID/power-save whenever a Wi-Fi interface connects.
IFACE="$1"
ACTION="$2"

if [ "$ACTION" = "up" ] || [ "$ACTION" = "connectivity-change" ]; then
    if [ -d "/sys/class/net/$IFACE/wireless" ]; then
        /usr/bin/env python3 -m astracradle --wifi-optimize >/dev/null 2>&1 &
    fi
fi
exit 0
"""

    def install_cachyos_fish_integration(
        self,
        install_dispatcher: bool = False,
        enable_timer: bool = True,
        interval_min: int = 10,
        elevation: ElevationRunner | None = None,
    ) -> tuple[bool, list[str]]:
        """Installs Fish conf.d functions, CLI wrapper, systemd user timer, and optional NM dispatcher."""
        home = Path.home()
        created: list[str] = []

        # 1. Fish conf.d helper functions
        fish_dir = home / ".config" / "fish" / "conf.d"
        fish_dir.mkdir(parents=True, exist_ok=True)
        fish_file = fish_dir / "astracradle_wifi.fish"
        fish_file.write_text(self._fish_functions_content(), encoding="utf-8")
        created.append(f"Fish shell commands (wifi-best, wifi-scan, wifi-watch, wifi-restore) -> {fish_file}")

        # 2. Standalone user wrapper script in ~/.local/bin
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        bin_file = bin_dir / "astracradle-wifi-opt"
        bin_file.write_text(
            "#!/usr/bin/env fish\npython3 -m astracradle --wifi-optimize $argv\n",
            encoding="utf-8",
        )
        os.chmod(bin_file, 0o755)
        created.append(f"Standalone Fish runner -> {bin_file}")

        # 3. Systemd user service + timer for periodic congestion checks
        sd_dir = home / ".config" / "systemd" / "user"
        sd_dir.mkdir(parents=True, exist_ok=True)
        svc_file = sd_dir / "astracradle-wifi-opt.service"
        tmr_file = sd_dir / "astracradle-wifi-opt.timer"
        mins = max(2, min(120, int(interval_min)))
        svc_file.write_text(
            "[Unit]\n"
            "Description=AstraCradle Wi-Fi Best-Channel & Congestion Optimizer\n"
            "After=network-online.target\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart=/usr/bin/env python3 -m astracradle --wifi-optimize\n",
            encoding="utf-8",
        )
        tmr_file.write_text(
            "[Unit]\n"
            "Description=Periodic AstraCradle Wi-Fi Channel Traffic Check\n\n"
            "[Timer]\n"
            "OnBootSec=45s\n"
            f"OnUnitActiveSec={mins}m\n"
            "Persistent=true\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n",
            encoding="utf-8",
        )
        created.append(f"Systemd user timer (every {mins}m) -> {tmr_file}")

        if enable_timer and shutil.which("systemctl"):
            try:
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=4, check=False)
                res_en = subprocess.run(
                    ["systemctl", "--user", "enable", "--now", "astracradle-wifi-opt.timer"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if res_en.returncode == 0:
                    created.append("Activated systemd user timer: astracradle-wifi-opt.timer")
            except Exception:
                pass

        # 4. NetworkManager on-connect dispatcher recipe + optional privileged install
        scripts_dir = self.state_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        local_disp = scripts_dir / "90-astracradle-wifi-channel"
        local_disp.write_text(self._nm_dispatcher_script(), encoding="utf-8")
        os.chmod(local_disp, 0o755)
        created.append(f"NM on-connect dispatcher script -> {local_disp}")

        if install_dispatcher and platform.system() == "Linux":
            target_disp = Path("/etc/NetworkManager/dispatcher.d/90-astracradle-wifi-channel")
            if os.geteuid() == 0:
                target_disp.parent.mkdir(parents=True, exist_ok=True)
                target_disp.write_text(self._nm_dispatcher_script(), encoding="utf-8")
                os.chmod(target_disp, 0o755)
                created.append(f"Installed system NM hook -> {target_disp}")
            elif elevation is not None:
                res_el = elevation.run_privileged(
                    [
                        "sh",
                        "-c",
                        f'install -Dm755 "{local_disp}" "{target_disp}"',
                    ],
                    reason="Install NetworkManager Wi-Fi on-connect channel optimizer hook",
                )
                if res_el.ok:
                    created.append(f"Installed system NM hook -> {target_disp}")
                else:
                    created.append(f"Manual NM hook install: sudo install -Dm755 {local_disp} {target_disp}")

        self.journal.log_event(
            "WifiChannelOptimizer",
            "install_cachyos_fish",
            f"Configured {len(created)} CachyOS/Fish Wi-Fi optimizer components",
            "Enable automatic Wi-Fi best-channel optimization on connect and periodic checks in CachyOS + Fish",
            "ok",
        )
        return True, created

    def integration_status(self) -> dict[str, Any]:
        home = Path.home()
        fish_ok = (home / ".config" / "fish" / "conf.d" / "astracradle_wifi.fish").exists()
        tmr_ok = (home / ".config" / "systemd" / "user" / "astracradle-wifi-opt.timer").exists()
        nm_ok = Path("/etc/NetworkManager/dispatcher.d/90-astracradle-wifi-channel").exists()
        return {
            "fish_installed": fish_ok,
            "systemd_timer_installed": tmr_ok,
            "nm_dispatcher_installed": nm_ok,
        }
