"""AstraShield — 100% Local Pi-hole-Style Toggleable DNS & Hosts Ad/Tracker/Telemetry Blocker."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import platform
import socket
import struct
import threading
import time
from typing import Any

from .config import ConfigManager
from .elevation import ElevationRunner
from .journal import ForensicJournal
from .models import utc_now_iso
from .rollback import RollbackManager


DEFAULT_BLOCK_CATEGORIES: dict[str, list[str]] = {
    "ads": [
        "doubleclick.net",
        "adservice.google.com",
        "pagead2.googlesyndication.com",
        "googleads.g.doubleclick.net",
        "ads.pubmatic.com",
        "adnxs.com",
        "ads.yahoo.com",
        "advertising.com",
        "taboola.com",
        "outbrain.com",
        "criteo.com",
        "rubiconproject.com",
        "openx.net",
        "adsafeprotected.com",
        "moatads.com",
        "amazon-adsystem.com",
        "media.net",
        "adcolony.com",
        "applovin.com",
        "unityads.unity3d.com",
    ],
    "trackers": [
        "www.google-analytics.com",
        "ssl.google-analytics.com",
        "analytics.google.com",
        "pixel.facebook.com",
        "connect.facebook.net",
        "analytics.tiktok.com",
        "ads-api.tiktok.com",
        "bat.bing.com",
        "analytics.twitter.com",
        "t.co",
        "sb.scorecardresearch.com",
        "quantserve.com",
        "hotjar.com",
        "mixpanel.com",
        "segment.io",
        "amplitude.com",
        "fullstory.com",
        "clarity.ms",
    ],
    "telemetry": [
        "vortex.data.microsoft.com",
        "vortex-win.data.microsoft.com",
        "telecommand.telemetry.microsoft.com",
        "watson.telemetry.microsoft.com",
        "settings-win.data.microsoft.com",
        "telemetry.mozilla.org",
        "incoming.telemetry.mozilla.org",
        "metrics.icloud.com",
        "metrics.mzstatic.com",
        "crashlytics.com",
        "app-measurement.com",
        "telemetry.nvidia.com",
        "events.gfe.nvidia.com",
    ],
    "malware": [
        "coinhive.com",
        "coin-hive.com",
        "authedmine.com",
        "cryptoloot.pro",
        "malicious-tracking-example.invalid",
    ],
}

MARKER_BEGIN = "# >>> AstraCradle AstraShield BEGIN (Auto-Managed - Do Not Edit Inside)"
MARKER_END = "# <<< AstraCradle AstraShield END"


@dataclass
class DnsQueryEvent:
    timestamp: str
    domain: str
    action: str  # blocked | allowed | forwarded
    reason: str


@dataclass
class AstraShieldStatus:
    hosts_active: bool
    dns_server_running: bool
    dns_listen_addr: str
    blocked_domain_count: int
    total_queries: int
    blocked_queries: int
    blocked_percent: float
    categories: list[str]
    top_blocked: list[tuple[str, int]] = field(default_factory=list)
    recent_events: list[DnsQueryEvent] = field(default_factory=list)
    hosts_path: str = ""
    note: str = ""


def _parse_dns_qname(packet: bytes) -> tuple[str, int, int] | None:
    """Extracts (qname, qtype, end_offset) from a standard UDP DNS packet."""
    if len(packet) < 12:
        return None
    idx = 12
    labels: list[str] = []
    while idx < len(packet):
        length = packet[idx]
        idx += 1
        if length == 0:
            break
        if length & 0xC0 or idx + length > len(packet):
            return None
        labels.append(packet[idx : idx + length].decode("ascii", errors="ignore").lower())
        idx += length
    if idx + 4 > len(packet) or not labels:
        return None
    qtype = struct.unpack("!H", packet[idx : idx + 2])[0]
    return ".".join(labels), qtype, idx + 4


def _build_dns_sinkhole_reply(query: bytes, qend: int, qtype: int) -> bytes:
    """Builds a valid DNS response returning 0.0.0.0 (A) / :: (AAAA) / NXDOMAIN."""
    txid = query[:2]
    question = query[12:qend]
    qdcount = 1
    if qtype == 1:  # A -> 0.0.0.0
        flags = 0x8180  # standard response, no error
        ancount = 1
        header = struct.pack("!2sHHHHH", txid, flags, qdcount, ancount, 0, 0)
        answer = struct.pack("!HHHIH4s", 0xC00C, 1, 1, 120, 4, b"\x00\x00\x00\x00")
        return header + question + answer
    if qtype == 28:  # AAAA -> ::
        flags = 0x8180
        ancount = 1
        header = struct.pack("!2sHHHHH", txid, flags, qdcount, ancount, 0, 0)
        answer = struct.pack("!HHHIH16s", 0xC00C, 28, 1, 120, 16, b"\x00" * 16)
        return header + question + answer
    # Other query types -> NXDOMAIN
    flags = 0x8183
    header = struct.pack("!2sHHHHH", txid, flags, qdcount, 0, 0, 0)
    return header + question


class AstraShieldManager:
    """Pi-hole-inspired toggleable local ad, tracker, telemetry & malware DNS/hosts blocker."""

    def __init__(
        self,
        cfg_mgr: ConfigManager,
        rollback_mgr: RollbackManager,
        journal: ForensicJournal,
        hosts_path: Path | None = None,
        elevation: ElevationRunner | None = None,
    ) -> None:
        self.cfg_mgr = cfg_mgr
        self.rollback_mgr = rollback_mgr
        self.journal = journal
        self.elevation = elevation
        self.rules_path = cfg_mgr.base_dir / "astrashield_rules.json"
        self.hosts_path = hosts_path or self.default_hosts_path()

        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._listen_addr = ""
        self._upstream_dns = "1.1.1.1"

        self.total_queries = 0
        self.blocked_queries = 0
        self._top_blocked: Counter[str] = Counter()
        self._recent: deque[DnsQueryEvent] = deque(maxlen=60)

    @staticmethod
    def default_hosts_path() -> Path:
        if platform.system() == "Windows":
            sys_root = Path(r"C:\Windows\System32\drivers\etc\hosts")
            return sys_root
        return Path("/etc/hosts")

    def load_rules(self) -> dict[str, Any]:
        default = {
            "categories": ["ads", "trackers", "telemetry", "malware"],
            "custom_block": [],
            "custom_allow": [],
            "upstream_dns": "1.1.1.1",
        }
        if not self.rules_path.exists():
            self.save_rules(default)
            return default
        try:
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
        except Exception:
            pass
        return default

    def save_rules(self, rules: dict[str, Any]) -> None:
        self.cfg_mgr.ensure_directories()
        self.rules_path.write_text(json.dumps(rules, indent=2), encoding="utf-8")

    def build_domain_set(self, categories: list[str] | None = None) -> tuple[set[str], set[str]]:
        rules = self.load_rules()
        cats = categories if categories is not None else list(rules.get("categories", []))
        blocked: set[str] = set()
        for c in cats:
            for dom in DEFAULT_BLOCK_CATEGORIES.get(c, []):
                blocked.add(dom.lower().strip())
        for dom in rules.get("custom_block", []):
            d = str(dom).lower().strip()
            if d:
                blocked.add(d)
        allowed = {str(d).lower().strip() for d in rules.get("custom_allow", []) if str(d).strip()}
        blocked -= allowed
        return blocked, allowed

    def is_domain_blocked(self, domain: str, categories: list[str] | None = None) -> tuple[bool, str]:
        dom = domain.lower().strip().rstrip(".")
        if not dom:
            return False, "empty"
        blocked, allowed = self.build_domain_set(categories)
        parts = dom.split(".")
        # Check exact + parent domain suffixes (so sub.doubleclick.net matches doubleclick.net)
        for i in range(len(parts)):
            sub = ".".join(parts[i:])
            if sub in allowed:
                return False, f"allowlist:{sub}"
        for i in range(len(parts)):
            sub = ".".join(parts[i:])
            if sub in blocked:
                return True, f"blocked:{sub}"
        return False, "clean"

    def add_custom_domain(self, domain: str, allow: bool = False) -> tuple[bool, str]:
        dom = domain.strip().lower().lstrip("https://").lstrip("http://").split("/")[0]
        if not dom or "." not in dom or " " in dom:
            return False, "Enter a valid domain name (e.g. ads.example.com)."
        rules = self.load_rules()
        target_key = "custom_allow" if allow else "custom_block"
        other_key = "custom_block" if allow else "custom_allow"
        if dom in rules[other_key]:
            rules[other_key].remove(dom)
        if dom not in rules[target_key]:
            rules[target_key].append(dom)
        self.save_rules(rules)
        self.journal.log_event(
            "AstraShield",
            "allow_domain" if allow else "block_domain",
            f"Added '{dom}' to AstraShield {target_key}",
            "User customized local Pi-hole-style domain rule",
            "ok",
        )
        return True, f"Added '{dom}' to {'Allowlist' if allow else 'Blocklist'}."

    # ------------------------------------------------------------------
    # Hosts-File Sinkhole Layer (Zero-Dependency System-Wide Blocking)
    # ------------------------------------------------------------------
    def is_hosts_active(self) -> bool:
        try:
            if not self.hosts_path.exists():
                return False
            txt = self.hosts_path.read_text(encoding="utf-8", errors="ignore")
            return MARKER_BEGIN in txt and MARKER_END in txt
        except OSError:
            return False

    def _strip_marker_block(self, content: str) -> str:
        lines = content.splitlines()
        out: list[str] = []
        inside = False
        for ln in lines:
            if ln.strip() == MARKER_BEGIN:
                inside = True
                continue
            if ln.strip() == MARKER_END:
                inside = False
                continue
            if not inside:
                out.append(ln)
        return "\n".join(out).rstrip() + ("\n" if out else "")

    def enable_hosts_sinkhole(self, categories: list[str] | None = None, dry_run: bool = False) -> tuple[bool, str]:
        blocked, _ = self.build_domain_set(categories)
        sorted_doms = sorted(blocked)
        block_lines = [MARKER_BEGIN, f"# Updated {utc_now_iso()} ({len(sorted_doms)} domains)"]
        for d in sorted_doms:
            block_lines.append(f"0.0.0.0 {d}")
        block_lines.append(MARKER_END)
        block_txt = "\n".join(block_lines) + "\n"

        if dry_run:
            return True, f"Dry-run preview: would add {len(sorted_doms)} AstraShield 0.0.0.0 sinkhole entries to {self.hosts_path}."

        try:
            orig = self.hosts_path.read_text(encoding="utf-8", errors="ignore") if self.hosts_path.exists() else ""
            # Save exact backup inside Rollback vault before touching hosts
            backup_dir = self.cfg_mgr.quarantine_dir / "hosts_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / f"hosts_backup_{int(time.time())}.txt"
            backup_file.write_text(orig, encoding="utf-8")

            clean_base = self._strip_marker_block(orig)
            updated = clean_base + ("\n" if clean_base and not clean_base.endswith("\n") else "") + block_txt
            self.hosts_path.write_text(updated, encoding="utf-8")

            self.journal.log_event(
                "AstraShield",
                "enable_hosts_sinkhole",
                f"Wrote {len(sorted_doms)} sinkhole domains to {self.hosts_path} (backup: {backup_file.name})",
                "System-wide Pi-hole-style ad, tracker & telemetry blocking toggled ON",
                "ok",
            )
            return True, f"AstraShield Hosts Sinkhole enabled ({len(sorted_doms)} domains blocked in {self.hosts_path})."
        except PermissionError:
            if self.elevation is not None:
                res = self.elevation.write_privileged_file(
                    self.hosts_path,
                    updated,
                    reason=f"Write {len(sorted_doms)} AstraShield ad/tracker sinkhole entries to {self.hosts_path}",
                )
                if res.ok:
                    self.journal.log_event(
                        "AstraShield",
                        "enable_hosts_sinkhole",
                        f"Elevated write of {len(sorted_doms)} sinkhole domains to {self.hosts_path}",
                        "System-wide Pi-hole-style blocking enabled via sudo prompt",
                        "ok",
                    )
                    return True, f"AstraShield Hosts Sinkhole enabled ({len(sorted_doms)} domains blocked in {self.hosts_path})."
                return False, res.message
            hint = (
                f"Run AstraCradle elevated (sudo / Administrator) to write {self.hosts_path}, "
                "or use AstraShield Local DNS Sinkhole (no admin needed for browsers/apps)."
            )
            self.journal.log_event(
                "AstraShield",
                "enable_hosts_sinkhole",
                f"Permission denied writing {self.hosts_path}",
                "System hosts file requires admin/root rights; offered user-space DNS sinkhole fallback",
                "warn",
            )
            return False, hint
        except Exception as exc:
            return False, f"Could not update hosts file: {exc}"

    def disable_hosts_sinkhole(self) -> tuple[bool, str]:
        try:
            if not self.hosts_path.exists():
                return True, "Hosts file untouched (already clean)."
            orig = self.hosts_path.read_text(encoding="utf-8", errors="ignore")
            if MARKER_BEGIN not in orig:
                return True, "AstraShield hosts entries already removed."
            cleaned = self._strip_marker_block(orig)
            self.hosts_path.write_text(cleaned, encoding="utf-8")
            self.journal.log_event(
                "AstraShield",
                "disable_hosts_sinkhole",
                f"Removed AstraShield block from {self.hosts_path}",
                "User toggled AstraShield Hosts Sinkhole OFF cleanly",
                "ok",
            )
            return True, f"AstraShield Hosts Sinkhole disabled and {self.hosts_path} restored cleanly."
        except PermissionError:
            if self.elevation is not None:
                res = self.elevation.write_privileged_file(
                    self.hosts_path,
                    cleaned,
                    reason=f"Restore {self.hosts_path} by removing AstraShield sinkhole block",
                )
                if res.ok:
                    self.journal.log_event(
                        "AstraShield",
                        "disable_hosts_sinkhole",
                        f"Removed AstraShield block from {self.hosts_path} via sudo",
                        "User toggled AstraShield Hosts Sinkhole OFF cleanly",
                        "ok",
                    )
                    return True, f"AstraShield Hosts Sinkhole disabled and {self.hosts_path} restored cleanly."
                return False, res.message
            return False, f"Permission denied restoring {self.hosts_path} (run elevated to remove system hosts entries)."
        except Exception as exc:
            return False, f"Failed to restore hosts file: {exc}"

    # ------------------------------------------------------------------
    # Pure-Python Local Pi-hole-Style UDP DNS Sinkhole Server
    # ------------------------------------------------------------------
    @property
    def dns_running(self) -> bool:
        return self._sock is not None and not self._stop_event.is_set()

    @property
    def dns_listen_addr(self) -> str:
        return self._listen_addr

    def start_dns_sinkhole(self, host: str = "127.0.0.1", port: int = 5353, upstream: str = "1.1.1.1") -> tuple[bool, str]:
        if self.dns_running:
            return True, f"AstraShield DNS Sinkhole already running on {self._listen_addr}."
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.settimeout(0.4)
            bound_host, bound_port = sock.getsockname()
            self._sock = sock
            self._listen_addr = f"{bound_host}:{bound_port}"
            self._upstream_dns = upstream or "1.1.1.1"
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._dns_loop, daemon=True)
            self._thread.start()
            self.journal.log_event(
                "AstraShield",
                "start_dns_sinkhole",
                f"Started local UDP DNS sinkhole on {self._listen_addr} (upstream {self._upstream_dns})",
                "Real-time Pi-hole-style DNS filtering & query telemetry enabled",
                "ok",
            )
            return True, f"AstraShield DNS Sinkhole listening on {self._listen_addr} (upstream {self._upstream_dns})."
        except OSError as exc:
            return False, f"Could not bind local DNS sinkhole on {host}:{port}: {exc}"

    def stop_dns_sinkhole(self) -> tuple[bool, str]:
        self._stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        addr = self._listen_addr
        self._listen_addr = ""
        if addr:
            self.journal.log_event(
                "AstraShield",
                "stop_dns_sinkhole",
                f"Stopped local UDP DNS sinkhole ({addr})",
                "User toggled AstraShield DNS Sinkhole OFF",
                "ok",
            )
        return True, "AstraShield DNS Sinkhole stopped."

    def handle_dns_packet(self, packet: bytes, allow_upstream: bool = True) -> bytes | None:
        """Processes one raw UDP DNS packet, recording Pi-hole telemetry and returning reply bytes."""
        parsed = _parse_dns_qname(packet)
        if parsed is None:
            return None
        qname, qtype, qend = parsed
        blocked, reason = self.is_domain_blocked(qname)
        with self._lock:
            self.total_queries += 1
            if blocked:
                self.blocked_queries += 1
                self._top_blocked[qname] += 1
                self._recent.appendleft(DnsQueryEvent(utc_now_iso(), qname, "blocked", reason))
            else:
                self._recent.appendleft(
                    DnsQueryEvent(utc_now_iso(), qname, "allowed" if "allowlist" in reason else "forwarded", reason)
                )
        if blocked:
            return _build_dns_sinkhole_reply(packet, qend, qtype)

        if not allow_upstream:
            return None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as up:
                up.settimeout(2.5)
                up.sendto(packet, (self._upstream_dns, 53))
                resp, _ = up.recvfrom(4096)
                return resp
        except OSError:
            # SERVFAIL fallback if offline
            return struct.pack("!2sHHHHH", packet[:2], 0x8182, 1, 0, 0, 0) + packet[12:qend]

    def _dns_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop_event.is_set():
            try:
                data, client = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            reply = self.handle_dns_packet(data, allow_upstream=True)
            if reply:
                try:
                    sock.sendto(reply, client)
                except OSError:
                    pass

    def test_query_domain(self, domain: str) -> tuple[bool, str]:
        """Simulates a DNS lookup through AstraShield (works offline & in tests)."""
        dom = domain.strip().lower().rstrip(".")
        if not dom or "." not in dom:
            return False, "Enter a domain like ads.doubleclick.net or example.com."
        blocked, reason = self.is_domain_blocked(dom)
        with self._lock:
            self.total_queries += 1
            if blocked:
                self.blocked_queries += 1
                self._top_blocked[dom] += 1
                self._recent.appendleft(DnsQueryEvent(utc_now_iso(), dom, "blocked", reason))
                return True, f"BLOCKED -> 0.0.0.0 ({dom} matched {reason})"
            self._recent.appendleft(DnsQueryEvent(utc_now_iso(), dom, "allowed", reason))
            return False, f"ALLOWED -> forwarded ({dom}: {reason})"

    def status(self) -> AstraShieldStatus:
        rules = self.load_rules()
        blocked_set, _ = self.build_domain_set()
        with self._lock:
            tot = self.total_queries
            blk = self.blocked_queries
            pct = round((blk / tot) * 100.0, 1) if tot > 0 else 0.0
            top = self._top_blocked.most_common(8)
            rec = list(self._recent)[:15]
        return AstraShieldStatus(
            hosts_active=self.is_hosts_active(),
            dns_server_running=self.dns_running,
            dns_listen_addr=self._listen_addr or "stopped",
            blocked_domain_count=len(blocked_set),
            total_queries=tot,
            blocked_queries=blk,
            blocked_percent=pct,
            categories=list(rules.get("categories", [])),
            top_blocked=top,
            recent_events=rec,
            hosts_path=str(self.hosts_path),
            note="Pi-hole-style local DNS & hosts blocker ready.",
        )

    def cleanup_on_uninstall(self) -> list[str]:
        notes: list[str] = []
        if self.dns_running:
            _, msg = self.stop_dns_sinkhole()
            notes.append(msg)
        if self.is_hosts_active():
            _, msg = self.disable_hosts_sinkhole()
            notes.append(msg)
        return notes
