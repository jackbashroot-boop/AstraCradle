"""AstraShield — 100% Local Pi-hole-Style Toggleable DNS & Hosts Ad/Tracker/Telemetry Blocker."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import json
from pathlib import Path
import platform
import re
import shutil
import socket
import struct
import threading
import time
from typing import Any
from urllib.parse import urlparse
import urllib.request

from .config import ConfigManager
from .elevation import ElevationRunner
from .journal import ForensicJournal
from .models import utc_now_iso
from .rollback import RollbackManager


# Verified standard Pi-hole / community blocklist & whitelist feeds (HTTPS only)
PIHOLE_STANDARD_SOURCES: dict[str, dict[str, str]] = {
    "stevenblack_unified": {
        "name": "StevenBlack Unified Hosts (Ads + Malware)",
        "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
        "kind": "block",
    },
    "hagezi_pro": {
        "name": "HaGeZi Multi Pro DNS Blocklist",
        "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/pro.txt",
        "kind": "block",
    },
    "oisd_big": {
        "name": "OISD Big Blocklist",
        "url": "https://big.oisd.nl/domainswild",
        "kind": "block",
    },
    "firebog_easyprivacy": {
        "name": "Firebog EasyPrivacy Trackers",
        "url": "https://v.firebog.net/hosts/Easyprivacy.txt",
        "kind": "block",
    },
    "peter_lowe_ads": {
        "name": "Peter Lowe's Ad & Tracking Servers",
        "url": "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext",
        "kind": "block",
    },
    "adaway_hosts": {
        "name": "AdAway Mobile & Web Ads",
        "url": "https://adaway.org/hosts.txt",
        "kind": "block",
    },
    "youtube_ads_pihole": {
        "name": "YouTube Ads for Pi-hole (kboghdady safe list)",
        "url": "https://raw.githubusercontent.com/kboghdady/youTube_ads_4_pi-hole/refs/heads/master/youtubelist.txt",
        "kind": "block",
    },
    "youtube_ignore_pihole": {
        "name": "YouTube Playback-Safe Ignore List",
        "url": "https://raw.githubusercontent.com/kboghdady/youTube_ads_4_pi-hole/refs/heads/master/ignore.list",
        "kind": "allow",
    },
    "anudeep_whitelist": {
        "name": "AnudeepND Pi-hole Safe Whitelist",
        "url": "https://raw.githubusercontent.com/anudeepND/whitelist/master/domains/whitelist.txt",
        "kind": "allow",
    },
}

# Domains that must NEVER be blocked so YouTube video streaming, thumbnails & watch progress never break
YOUTUBE_PLAYBACK_ALLOWLIST: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "tv.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "s.youtube.com",
        "ytimg.com",
        "i.ytimg.com",
        "i9.ytimg.com",
        "s.ytimg.com",
        "ggpht.com",
        "yt3.ggpht.com",
        "yt4.ggpht.com",
        "yt3.googleusercontent.com",
        "youtubei.googleapis.com",
        "youtube.googleapis.com",
        "youtubeembeddedplayer.googleapis.com",
        "jnn-pa.googleapis.com",
        "googlevideo.com",
        "manifest.googlevideo.com",
        "redirector.googlevideo.com",
        "video-stats.l.google.com",
    }
)

# Samsung / Smart TV essential domains that must stay unblocked so native TV apps, store & clock keep working
SMART_TV_ESSENTIAL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "time.samsungcloudsolution.com",
        "time.samsungcloudsolution.net",
        "lcprd1.samsungcloudsolution.net",
        "cdn.samsungcloudsolution.com",
        "auth.samsungosp.com",
        "osb-ussvc.samsungqbe.com",
        "imasdk.googleapis.com",
    }
)

_VALID_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

DEFAULT_BLOCK_CATEGORIES: dict[str, list[str]] = {
    "ads": [
        "doubleclick.net",
        "ad.doubleclick.net",
        "static.doubleclick.net",
        "m.doubleclick.net",
        "mediavisor.doubleclick.net",
        "pubads.g.doubleclick.net",
        "securepubads.g.doubleclick.net",
        "googleads.g.doubleclick.net",
        "googleads4.g.doubleclick.net",
        "adservice.google.com",
        "pagead2.googlesyndication.com",
        "tpc.googlesyndication.com",
        "partnerad.l.doubleclick.net",
        "www.googleadservices.com",
        "ads.google.com",
        "afs.googlesyndication.com",
        "s0.2mdn.net",
        "s1.2mdn.net",
        "ads.pubmatic.com",
        "hbopenbid.pubmatic.com",
        "adnxs.com",
        "ib.adnxs.com",
        "ads.yahoo.com",
        "gemini.yahoo.com",
        "advertising.com",
        "adtech.de",
        "taboola.com",
        "cdn.taboola.com",
        "trc.taboola.com",
        "outbrain.com",
        "widgets.outbrain.com",
        "criteo.com",
        "static.criteo.net",
        "bidder.criteo.com",
        "rubiconproject.com",
        "fastlane.rubiconproject.com",
        "openx.net",
        "rtb.openx.net",
        "adsafeprotected.com",
        "pixel.adsafeprotected.com",
        "moatads.com",
        "z.moatads.com",
        "amazon-adsystem.com",
        "aax.amazon-adsystem.com",
        "fls-na.amazon-adsystem.com",
        "media.net",
        "contextual.media.net",
        "adcolony.com",
        "applovin.com",
        "d.applovin.com",
        "unityads.unity3d.com",
        "adserver.unityads.unity3d.com",
        "casalemedia.com",
        "htlb.casalemedia.com",
        "bidswitch.net",
        "smartadserver.com",
        "ww251.smartadserver.com",
        "lijit.com",
        "ap.lijit.com",
        "triplelift.com",
        "tlx.3lift.com",
        "indexww.com",
        "adsrvr.org",
        "match.adsrvr.org",
        "sharethrough.com",
        "btlr.sharethrough.com",
        "zemanta.com",
        "revcontent.com",
        "trends.revcontent.com",
        "mgid.com",
        "jsc.mgid.com",
        "popads.net",
        "serve.popads.net",
        "propellerads.com",
        "adcash.com",
        "exoclick.com",
        "syndication.exoclick.com",
        "juicyads.com",
        "carbonads.com",
        "srv.carbonads.net",
        "buysellads.com",
        "ads.linkedin.com",
        "ads.pinterest.com",
        "ads.reddit.com",
        "events.redditmedia.com",
        "alb.reddit.com",
        "ads-twitter.com",
        "static.ads-twitter.com",
        "an.facebook.com",
        "ads.snapchat.com",
        "sc-static.net",
        "tr.snapchat.com",
    ],
    "youtube_ads": [
        "ads.youtube.com",
        "yt.moatads.com",
        "pixel.moatads.com",
        "googleads.g.doubleclick.net",
        "adservice.google.com",
        "pagead2.googlesyndication.com",
        "pubads.g.doubleclick.net",
        "securepubads.g.doubleclick.net",
        "static.doubleclick.net",
        "www.googleadservices.com",
        "s0.2mdn.net",
        "fundingchoicesmessages.google.com",
        "ade.googlesyndication.com",
        "adclick.g.doubleclick.net",
        "cm.g.doubleclick.net",
        "stats.g.doubleclick.net",
    ],
    "smart_tv_ads": [
        "samsungads.com",
        "ads.samsungads.com",
        "events.samsungads.com",
        "samsungtvads.com",
        "ads.samsung.com",
        "samsungacr.com",
        "log-config.samsungacr.com",
        "acr-us-prd.samsungcloud.tv",
        "acr-eu-prd.samsungcloud.tv",
        "acr0.samsungcloudsolution.com",
        "config.samsungads.com",
        "samsungadhub.com",
        "ad.samsungadhub.com",
        "samsungqbe.com",
        "gpm.samsungqbe.com",
        "osb-apps.samsungqbe.com",
        "api-hub.samsungyosemite.com",
        "d179kwmlpc4o47.cloudfront.net",
        "d1jwpcr0q4pcq0.cloudfront.net",
        "ad.lgappstv.com",
        "us.info.lgsmartad.com",
        "lgad.cjpowercast.com",
        "smartshare.lgtvsdp.com",
        "ibs.lgappstv.com",
        "ngfts.lge.com",
        "ads.roku.com",
        "scribe.logs.roku.com",
        "cooper.logs.roku.com",
        "austin.logs.roku.com",
        "liberty.logs.roku.com",
        "p.ads.roku.com",
        "mads.amazon.com",
        "mads.amazon-adsystem.com",
        "arcus-uswest.amazon.com",
        "fls-na.amazon.com",
        "tv.samba.tv",
        "ads.vizio.com",
        "api.vizio.com",
        "logs.vizio.com",
        "androidtvchannels-pa.googleapis.com",
    ],
    "trackers": [
        "www.google-analytics.com",
        "ssl.google-analytics.com",
        "analytics.google.com",
        "googletagmanager.com",
        "www.googletagmanager.com",
        "google-analytics.com",
        "pixel.facebook.com",
        "connect.facebook.net",
        "graph.facebook.com",
        "analytics.tiktok.com",
        "ads-api.tiktok.com",
        "analytics-sg.tiktok.com",
        "bat.bing.com",
        "c.bing.com",
        "analytics.twitter.com",
        "t.co",
        "sb.scorecardresearch.com",
        "b.scorecardresearch.com",
        "quantserve.com",
        "pixel.quantserve.com",
        "hotjar.com",
        "static.hotjar.com",
        "script.hotjar.com",
        "mixpanel.com",
        "api.mixpanel.com",
        "cdn.mxpnl.com",
        "segment.io",
        "api.segment.io",
        "cdn.segment.com",
        "amplitude.com",
        "api.amplitude.com",
        "api2.amplitude.com",
        "fullstory.com",
        "rs.fullstory.com",
        "clarity.ms",
        "www.clarity.ms",
        "heapanalytics.com",
        "cdn.heapanalytics.com",
        "mouseflow.com",
        "crazyegg.com",
        "script.crazyegg.com",
        "luckyorange.com",
        "newrelic.com",
        "js-agent.newrelic.com",
        "bam.nr-data.net",
        "sentry.io",
        "browser-intake-datadoghq.com",
        "logx.optimizely.com",
        "cdn.optimizely.com",
        "demdex.net",
        "dpm.demdex.net",
        "omtrdc.net",
        "2o7.net",
        "everesttech.net",
        "krxd.net",
        "beacon.krxd.net",
        "bluekai.com",
        "tags.bluekai.com",
        "rlcdn.com",
        "id.rlcdn.com",
        "exelator.com",
        "loadm.exelator.com",
        "agkn.com",
        "crwdcntrl.net",
        "bcp.crwdcntrl.net",
        "tapad.com",
        "pixel.tapad.com",
        "mathtag.com",
        "pixel.mathtag.com",
        "chartbeat.com",
        "static.chartbeat.com",
        "ping.chartbeat.net",
        "parsely.com",
        "srv.pixel.parsely.com",
        "branch.io",
        "api2.branch.io",
        "app.adjust.com",
        "app.appsflyer.com",
        "t.appsflyer.com",
        "kochava.com",
        "control.kochava.com",
        "bugsnag.com",
        "notify.bugsnag.com",
    ],
    "telemetry": [
        "vortex.data.microsoft.com",
        "vortex-win.data.microsoft.com",
        "telecommand.telemetry.microsoft.com",
        "watson.telemetry.microsoft.com",
        "settings-win.data.microsoft.com",
        "oca.telemetry.microsoft.com",
        "sqm.telemetry.microsoft.com",
        "ceuswatcab01.blob.core.windows.net",
        "watson.microsoft.com",
        "self.events.data.microsoft.com",
        "mobile.events.data.microsoft.com",
        "telemetry.mozilla.org",
        "incoming.telemetry.mozilla.org",
        "metrics.icloud.com",
        "metrics.mzstatic.com",
        "xp.apple.com",
        "crashlytics.com",
        "e.crashlytics.com",
        "reports.crashlytics.com",
        "app-measurement.com",
        "firebaselogging-pa.googleapis.com",
        "telemetry.nvidia.com",
        "events.gfe.nvidia.com",
        "ls.d3.nvidia.com",
        "telemetry.dropbox.com",
        "log.spotify.com",
        "adeventtracker.spotify.com",
        "analytics.spotify.com",
        "spclient.wg.spotify.com",
        "metrics.plex.tv",
        "analytics.plex.tv",
        "samsungads.com",
        "lcprd1.samsungcloudsolution.net",
        "ad.lgappstv.com",
        "us.info.lgsmartad.com",
        "scribe.logs.roku.com",
        "cooper.logs.roku.com",
        "device-metrics-us.amazon.com",
        "device-metrics-us-2.amazon.com",
        "metrics.ubuntu.com",
        "popcon.ubuntu.com",
        "telemetry.brave.com",
        "usage-ping.duckduckgo.com",
    ],
    "malware": [
        "coinhive.com",
        "coin-hive.com",
        "authedmine.com",
        "cryptoloot.pro",
        "crypto-loot.com",
        "webminepool.com",
        "jsecoin.com",
        "minero.cc",
        "browsermine.com",
        "papoto.com",
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
    rtt_ms: float | None = None


DOH_ENDPOINTS: dict[str, str] = {
    "cloudflare": "https://1.1.1.1/dns-query",
    "quad9": "https://9.9.9.9:5053/dns-query",
    "google": "https://8.8.8.8/dns-query",
}


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
    pihole_cached_count: int = 0
    pihole_last_updated: str = ""
    youtube_safe_prune: bool = True


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
        self.pihole_cache_path = cfg_mgr.base_dir / "astrashield_pihole_cache.txt"
        self.pihole_meta_path = cfg_mgr.base_dir / "astrashield_pihole_meta.json"
        self.hosts_path = hosts_path or self.default_hosts_path()

        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._listen_addr = ""
        self._upstream_dns = "1.1.1.1"
        self._nat_redirect_port: int | None = None
        self._nat_backend: str = ""
        self._cached_pihole_domains: set[str] | None = None
        self._paused_until: float = 0.0
        self.passthrough_mode: bool = False
        self.doh_enabled: bool = False
        self.doh_provider: str = "cloudflare"

        self.total_queries = 0
        self.blocked_queries = 0
        self._top_blocked: Counter[str] = Counter()
        self._client_queries: Counter[str] = Counter()
        self._client_blocked: Counter[str] = Counter()
        self._recent: deque[DnsQueryEvent] = deque(maxlen=60)

    @staticmethod
    def default_hosts_path() -> Path:
        if platform.system() == "Windows":
            sys_root = Path(r"C:\Windows\System32\drivers\etc\hosts")
            return sys_root
        return Path("/etc/hosts")

    def load_rules(self) -> dict[str, Any]:
        default = {
            "categories": ["ads", "youtube_ads", "smart_tv_ads", "trackers", "telemetry", "malware"],
            "custom_block": [],
            "custom_allow": [],
            "upstream_dns": "1.1.1.1",
            "youtube_safe_prune": True,
            "use_pihole_cache": True,
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
        self._cached_pihole_domains = None

    def _load_pihole_cache(self) -> set[str]:
        if self._cached_pihole_domains is not None:
            return self._cached_pihole_domains
        if not self.pihole_cache_path.exists():
            self._cached_pihole_domains = set()
            return self._cached_pihole_domains
        try:
            doms = {
                ln.strip().lower()
                for ln in self.pihole_cache_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if ln.strip() and not ln.startswith("#")
            }
            self._cached_pihole_domains = doms
            return doms
        except OSError:
            return set()

    @staticmethod
    def parse_blocklist_text(raw_text: str) -> set[str]:
        """Parses hosts, AdBlock (||domain^), and plain-domain Pi-hole feeds safely."""
        out: set[str] = set()
        for raw_ln in raw_text.splitlines():
            ln = raw_ln.split("#", 1)[0].split("!", 1)[0].strip().lower()
            if not ln:
                continue
            if ln.startswith("||"):
                ln = ln[2:].split("^", 1)[0].split("$", 1)[0].strip()
            elif ln.startswith("0.0.0.0 ") or ln.startswith("127.0.0.1 ") or "\t" in ln or " " in ln:
                parts = ln.split()
                if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1", "::", "::1"):
                    ln = parts[1].strip()
                else:
                    continue
            ln = ln.lstrip("*.").rstrip(".")
            if ln in ("localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost", "ip6-loopback"):
                continue
            if _VALID_DOMAIN_RE.match(ln):
                out.add(ln)
        return out

    def download_pihole_blocklists(
        self,
        source_keys: list[str] | None = None,
        timeout: float = 12.0,
    ) -> tuple[bool, str, int]:
        """Downloads official Pi-hole community blocklists + YouTube safe ignore/whitelists."""
        keys = source_keys or list(PIHOLE_STANDARD_SOURCES.keys())
        blocked_all: set[str] = set()
        allow_all: set[str] = set(YOUTUBE_PLAYBACK_ALLOWLIST) | set(SMART_TV_ESSENTIAL_ALLOWLIST)
        fetched_sources: list[str] = []
        failed_sources: list[str] = []

        for key in keys:
            meta = PIHOLE_STANDARD_SOURCES.get(key)
            if not meta:
                continue
            url = meta["url"]
            parsed_url = urlparse(url)
            if parsed_url.scheme != "https":
                failed_sources.append(f"{key} (non-https)")
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "AstraCradle-AstraShield/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read(25 * 1024 * 1024).decode("utf-8", errors="ignore")
                parsed = self.parse_blocklist_text(raw)
                if meta.get("kind") == "allow":
                    allow_all.update(parsed)
                else:
                    blocked_all.update(parsed)
                fetched_sources.append(f"{key} ({len(parsed):,})")
            except Exception as exc:
                failed_sources.append(f"{key}: {exc}")

        if not fetched_sources:
            return False, f"Could not download Pi-hole feeds ({'; '.join(failed_sources[:3])}). Built-in compiled list remains active.", 0

        # Protect YouTube playback & essential whitelisted domains
        blocked_all -= allow_all
        self.cfg_mgr.ensure_directories()
        self.pihole_cache_path.write_text("\n".join(sorted(blocked_all)) + "\n", encoding="utf-8")
        self._cached_pihole_domains = blocked_all
        meta_doc = {
            "updated_at": utc_now_iso(),
            "domain_count": len(blocked_all),
            "fetched": fetched_sources,
            "failed": failed_sources,
        }
        self.pihole_meta_path.write_text(json.dumps(meta_doc, indent=2), encoding="utf-8")
        self.journal.log_event(
            "AstraShield",
            "download_pihole_lists",
            f"Downloaded {len(blocked_all):,} Pi-hole domains from {len(fetched_sources)} feed(s)",
            "Synced community Pi-hole blocklists with YouTube playback safety filter",
            "ok" if fetched_sources else "warning",
        )
        warn_suffix = f" ({len(failed_sources)} feed(s) offline/skipped)" if failed_sources else ""
        return True, f"Synced {len(blocked_all):,} Pi-hole domains from {len(fetched_sources)} feeds{warn_suffix}.", len(blocked_all)

    @staticmethod
    def prune_youtube_player_payload(raw_json: str) -> tuple[str, int]:
        """Strips YouTube ad metadata keys from player JSON while preserving streamingData & videoDetails."""
        try:
            data = json.loads(raw_json)
        except Exception:
            return raw_json, 0
        if not isinstance(data, dict):
            return raw_json, 0
        ad_keys = ("adPlacements", "playerAds", "adSlots", "adBreakHeartbeatParams", "adNextParams")
        pruned = 0
        for k in ad_keys:
            if k in data:
                val = data.get(k)
                if isinstance(val, list):
                    pruned += len(val)
                    data[k] = []
                else:
                    pruned += 1
                    data.pop(k, None)
        return json.dumps(data), pruned

    @staticmethod
    def detect_installed_browsers() -> list[dict[str, Any]]:
        candidates = (
            ("Firefox", ("firefox", "firefox-esr"), Path.home() / ".mozilla" / "firefox"),
            ("LibreWolf", ("librewolf",), Path.home() / ".librewolf"),
            ("Zen Browser", ("zen", "zen-browser"), Path.home() / ".zen"),
            ("Brave", ("brave", "brave-browser"), Path.home() / ".config" / "BraveSoftware"),
            ("Chromium", ("chromium", "chromium-browser"), Path.home() / ".config" / "chromium"),
            ("Google Chrome", ("google-chrome", "chrome"), Path.home() / ".config" / "google-chrome"),
            ("Vivaldi", ("vivaldi",), Path.home() / ".config" / "vivaldi"),
        )
        found: list[dict[str, Any]] = []
        for label, bins, cfg_dir in candidates:
            bin_path = next((shutil.which(b) for b in bins if shutil.which(b)), None)
            if bin_path or cfg_dir.exists():
                has_ublock = False
                if cfg_dir.exists():
                    try:
                        for p in cfg_dir.rglob("*ublock*"):
                            has_ublock = True
                            break
                        if not has_ublock:
                            for p in cfg_dir.rglob("uBlock0@rayhill*.xpi"):
                                has_ublock = True
                                break
                    except OSError:
                        pass
                found.append({"name": label, "bin": bin_path or "", "has_ublock": has_ublock})
        return found

    def export_youtube_ad_prune_assets(self, dest_dir: Path | None = None) -> tuple[Path, Path]:
        """Generates a browser-safe YouTube ad-pruning userscript + uBlock filter list."""
        out_dir = dest_dir or self.cfg_mgr.base_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        js_path = out_dir / "astracradle_youtube_adprune.user.js"
        flt_path = out_dir / "astracradle_ublock_youtube_filters.txt"

        userscript = """// ==UserScript==
// @name         AstraCradle YouTube Safe Ad-Pruner
// @namespace    https://github.com/jackbashroot-boop/AstraCradle
// @version      1.0.0
// @description  Prunes YouTube adPlacements / playerAds / adSlots JSON payloads without breaking googlevideo playback.
// @match        *://*.youtube.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==
(function() {
  'use strict';
  const AD_KEYS = ['adPlacements', 'playerAds', 'adSlots', 'adBreakHeartbeatParams'];
  function pruneObj(obj) {
    if (!obj || typeof obj !== 'object') return obj;
    for (const k of AD_KEYS) {
      if (k in obj) {
        if (Array.isArray(obj[k])) obj[k] = [];
        else delete obj[k];
      }
    }
    return obj;
  }
  const origParse = JSON.parse;
  JSON.parse = function(text, reviver) {
    const res = origParse.call(this, text, reviver);
    if (res && typeof res === 'object' && ('streamingData' in res || 'adPlacements' in res || 'playerAds' in res)) {
      pruneObj(res);
    }
    return res;
  };
  if (window.fetch) {
    const origFetch = window.fetch;
    window.fetch = async function(...args) {
      const resp = await origFetch.apply(this, args);
      const url = String(args[0] && args[0].url ? args[0].url : args[0] || '');
      if (url.includes('/youtubei/v1/player')) {
        try {
          const clone = resp.clone();
          const data = pruneObj(await clone.json());
          return new Response(JSON.stringify(data), { status: resp.status, statusText: resp.statusText, headers: resp.headers });
        } catch (_) {}
      }
      return resp;
    };
  }
  setInterval(() => {
    const skip = document.querySelector('.ytp-ad-skip-button, .ytp-skip-ad-button, .ytp-ad-skip-button-modern');
    if (skip) skip.click();
    const player = document.querySelector('#movie_player');
    if (player && player.classList.contains('ad-showing')) {
      const v = document.querySelector('video.html5-main-video');
      if (v && Number.isFinite(v.duration) && v.duration > 0) v.currentTime = v.duration;
    }
  }, 350);
})();
"""
        ublock_filters = """! AstraCradle YouTube Safe Ad-Prune Filters (for uBlock Origin / AdGuard)
! Prunes player ad pods & page ads while keeping *.googlevideo.com video playback intact
youtube.com##+js(json-prune, adPlacements playerAds adSlots)
youtube.com##ytd-ad-slot-renderer, ytd-banner-promo-renderer, ytd-in-feed-ad-layout-renderer, #masthead-ad, ytd-player-legacy-desktop-watch-ads-renderer
||googleads.g.doubleclick.net^
||pagead2.googlesyndication.com^
||adservice.google.com^
||ads.youtube.com^
@@||manifest.googlevideo.com^
@@||s.youtube.com^
@@||i.ytimg.com^
"""
        js_path.write_text(userscript, encoding="utf-8")
        flt_path.write_text(ublock_filters, encoding="utf-8")
        self.journal.log_event(
            "AstraShield",
            "export_youtube_prune",
            f"Generated YouTube safe ad-prune userscript & filter pack in {out_dir}",
            "Provide in-stream YouTube ad pruning without breaking googlevideo playback",
            "ok",
        )
        return js_path, flt_path

    def build_domain_set(self, categories: list[str] | None = None) -> tuple[set[str], set[str]]:
        rules = self.load_rules()
        cats = categories if categories is not None else list(rules.get("categories", []))
        blocked: set[str] = set()
        for c in cats:
            for dom in DEFAULT_BLOCK_CATEGORIES.get(c, []):
                blocked.add(dom.lower().strip())
        if rules.get("use_pihole_cache", True):
            blocked.update(self._load_pihole_cache())
        for dom in rules.get("custom_block", []):
            d = str(dom).lower().strip()
            if d:
                blocked.add(d)
        allowed = {str(d).lower().strip() for d in rules.get("custom_allow", []) if str(d).strip()}
        if rules.get("youtube_safe_prune", True):
            allowed.update(YOUTUBE_PLAYBACK_ALLOWLIST)
            allowed.update(SMART_TV_ESSENTIAL_ALLOWLIST)
        blocked -= allowed
        return blocked, allowed

    def is_domain_blocked(self, domain: str, categories: list[str] | None = None) -> tuple[bool, str]:
        dom = domain.lower().strip().rstrip(".")
        if not dom:
            return False, "empty"
        blocked, allowed = self.build_domain_set(categories)
        if dom in allowed:
            return False, f"allowlist:{dom}"
        if dom in blocked:
            return True, f"blocked:{dom}"
        parts = dom.split(".")
        # Check parent domain suffixes (allowlist protects *.googlevideo.com; blocklist catches sub.doubleclick.net)
        for i in range(1, len(parts)):
            sub = ".".join(parts[i:])
            if sub in allowed:
                return False, f"allowlist:{sub}"
        for i in range(1, len(parts)):
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

    def allow_domain_live(self, domain: str, dry_run: bool = False) -> tuple[bool, str]:
        """1-click allowlist helper for Live Query Feed: allows domain & refreshes active hosts sinkhole."""
        ok, msg = self.add_custom_domain(domain, allow=True)
        if not ok:
            return False, msg
        if self.is_hosts_active():
            rules = self.load_rules()
            self.enable_hosts_sinkhole(rules.get("categories"), dry_run=dry_run)
        return True, f"Allowed '{domain.strip().lower()}' live in AstraShield."

    @property
    def recent_events(self) -> deque[DnsQueryEvent]:
        return self._recent

    def recent_query_feed(self, limit: int = 50) -> list[DnsQueryEvent]:
        return list(self._recent)[-max(1, limit):][::-1]

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
        self.cleanup_lan_nat_redirect()
        if addr:
            self.journal.log_event(
                "AstraShield",
                "stop_dns_sinkhole",
                f"Stopped local UDP DNS sinkhole ({addr})",
                "User toggled AstraShield DNS Sinkhole OFF",
                "ok",
            )
        return True, "AstraShield DNS Sinkhole stopped."

    def cleanup_lan_nat_redirect(self) -> None:
        port = self._nat_redirect_port
        backend = getattr(self, "_nat_backend", "")
        self._nat_redirect_port = None
        self._nat_backend = ""
        if not port or platform.system() != "Linux" or self.elevation is None:
            return
        if (not backend or backend == "iptables") and shutil.which("iptables"):
            for proto in ("udp", "tcp"):
                try:
                    self.elevation.run_privileged(
                        [
                            "iptables",
                            "-t",
                            "nat",
                            "-D",
                            "PREROUTING",
                            "-p",
                            proto,
                            "--dport",
                            "53",
                            "-j",
                            "REDIRECT",
                            "--to-ports",
                            str(port),
                        ],
                        reason=f"Remove AstraShield LAN DNS {proto.upper()} port 53 -> {port} redirect",
                    )
                except Exception:
                    pass
        if (not backend or backend == "nft") and shutil.which("nft"):
            try:
                self.elevation.run_privileged(
                    ["nft", "delete", "table", "ip", "astrashield_nat"],
                    reason="Remove AstraShield nftables LAN DNS port 53 redirect",
                )
            except Exception:
                pass

    def pause_temporarily(self, minutes: int = 5) -> str:
        """Temporarily bypasses blocking for N minutes without altering persistent lists."""
        import time as _time

        secs = max(60, int(minutes) * 60)
        self._paused_until = _time.time() + secs
        self.journal.log_event("AstraShield", "pause_temp", f"Paused AstraShield blocking for {minutes} min", "Tray/UI temporary bypass", "ok")
        return f"AstraShield paused for {minutes} minute(s)."

    def is_paused(self) -> bool:
        import time as _time

        return bool(getattr(self, "passthrough_mode", False)) or (_time.time() < getattr(self, "_paused_until", 0.0))

    def set_passthrough_mode(self, enabled: bool) -> str:
        """Forwards all DNS queries transparently without blocking so single-DNS TVs keep working normally."""
        self.passthrough_mode = bool(enabled)
        self.journal.log_event(
            "AstraShield",
            "passthrough_mode",
            f"Pass-through mode {'ENABLED' if self.passthrough_mode else 'DISABLED'}",
            "User toggled transparent unblocked DNS forwarding for LAN TV",
            "ok",
        )
        return (
            "AstraShield Pass-Through ON: all LAN/TV DNS queries forward normally with zero blocking."
            if self.passthrough_mode
            else "AstraShield Pass-Through OFF: active ad/tracker blocking resumed."
        )

    def resume_from_pause(self) -> None:
        self._paused_until = 0.0
        self.passthrough_mode = False

    @staticmethod
    def _query_upstream_doh(packet: bytes, provider: str = "cloudflare", timeout: float = 2.5) -> bytes | None:
        """Forwards wire-format DNS query over HTTPS (RFC 8484 DoH)."""
        import urllib.request

        url = DOH_ENDPOINTS.get(provider, DOH_ENDPOINTS["cloudflare"])
        try:
            req = urllib.request.Request(
                url,
                data=packet,
                method="POST",
                headers={
                    "Content-Type": "application/dns-message",
                    "Accept": "application/dns-message",
                    "User-Agent": "AstraShield-DoH/1.9",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if getattr(resp, "status", 200) == 200:
                    data = resp.read()
                    if len(data) >= 12:
                        return data
        except Exception:
            return None
        return None

    def handle_dns_packet(
        self,
        packet: bytes,
        allow_upstream: bool = True,
        client_ip: str = "",
    ) -> bytes | None:
        """Processes one raw UDP DNS packet, recording Pi-hole telemetry and returning reply bytes."""
        parsed = _parse_dns_qname(packet)
        if parsed is None:
            return None
        qname, qtype, qend = parsed
        import time as _time

        t_start = _time.monotonic()
        if self.is_paused():
            blocked, reason = False, "paused_temporarily"
        else:
            blocked, reason = self.is_domain_blocked(qname)
        if not allow_upstream:
            with self._lock:
                self.total_queries += 1
                if client_ip:
                    self._client_queries[client_ip] += 1
                if blocked:
                    self.blocked_queries += 1
                    self._top_blocked[qname] += 1
                    if client_ip:
                        self._client_blocked[client_ip] += 1
                    self._recent.appendleft(DnsQueryEvent(utc_now_iso(), qname, "blocked", reason, 0.1))
                else:
                    self._recent.appendleft(
                        DnsQueryEvent(utc_now_iso(), qname, "allowed" if "allowlist" in reason else "forwarded", reason, None)
                    )
            return _build_dns_sinkhole_reply(packet, qend, qtype) if blocked else None

        if blocked:
            with self._lock:
                self.total_queries += 1
                self.blocked_queries += 1
                self._top_blocked[qname] += 1
                if client_ip:
                    self._client_queries[client_ip] += 1
                    self._client_blocked[client_ip] += 1
                self._recent.appendleft(DnsQueryEvent(utc_now_iso(), qname, "blocked", reason, 0.1))
            return _build_dns_sinkhole_reply(packet, qend, qtype)

        resp_bytes: bytes | None = None
        used_doh = False
        if getattr(self, "doh_enabled", False):
            resp_bytes = self._query_upstream_doh(packet, provider=getattr(self, "doh_provider", "cloudflare"))
            used_doh = resp_bytes is not None

        if resp_bytes is None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as up:
                    up.settimeout(2.5)
                    up.sendto(packet, (self._upstream_dns, 53))
                    resp_bytes, _ = up.recvfrom(4096)
            except OSError:
                resp_bytes = None

        rtt = round((_time.monotonic() - t_start) * 1000.0, 1)
        act = "allowed" if ("allowlist" in reason or "paused" in reason) else ("doh" if used_doh else "forwarded")
        with self._lock:
            self.total_queries += 1
            if client_ip:
                self._client_queries[client_ip] += 1
            self._recent.appendleft(DnsQueryEvent(utc_now_iso(), qname, act, reason, rtt))

        if resp_bytes is not None:
            return resp_bytes
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
            cip = str(client[0]) if client else ""
            reply = self.handle_dns_packet(data, allow_upstream=True, client_ip=cip)
            if reply:
                try:
                    sock.sendto(reply, client)
                except OSError:
                    pass

    def get_client_stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            ips = set(self._client_queries.keys()) | set(self._client_blocked.keys())
            return {
                ip: {
                    "queries": int(self._client_queries.get(ip, 0)),
                    "blocked": int(self._client_blocked.get(ip, 0)),
                }
                for ip in sorted(ips)
            }

    @staticmethod
    def get_lan_ipv4() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("1.1.1.1", 80))
                ip = s.getsockname()[0]
                if ip and not ip.startswith("127."):
                    return str(ip)
        except OSError:
            pass
        try:
            host_ip = socket.gethostbyname(socket.gethostname())
            if host_ip and not host_ip.startswith("127."):
                return host_ip
        except OSError:
            pass
        return "127.0.0.1"

    def start_lan_dns_sinkhole(
        self,
        port: int = 53,
        fallback_port: int = 5353,
        upstream: str = "1.1.1.1",
        allow_elevation: bool = True,
    ) -> tuple[bool, str]:
        """Starts LAN-reachable (0.0.0.0) DNS sinkhole for Smart TVs & network devices."""
        if self.dns_running and self._listen_addr.startswith("0.0.0.0:"):
            return True, f"LAN AstraShield DNS already listening on {self._listen_addr} (TV DNS: {self.get_lan_ipv4()})."
        if self.dns_running:
            self.stop_dns_sinkhole()

        ok, msg = self.start_dns_sinkhole(host="0.0.0.0", port=port, upstream=upstream)
        lan_ip = self.get_lan_ipv4()
        if ok:
            return True, f"LAN AstraShield DNS active on {lan_ip}:{port}! Point your Smart TV or router DNS to {lan_ip}."

        # Port 53 requires root/CAP_NET_BIND_SERVICE — try binding fallback_port + optional Linux redirect
        ok_fb, msg_fb = self.start_dns_sinkhole(host="0.0.0.0", port=fallback_port, upstream=upstream)
        if not ok_fb:
            return False, msg_fb
        if allow_elevation and platform.system() == "Linux" and self.elevation is not None and port == 53 and fallback_port != 53:
            if shutil.which("iptables"):
                res_u = self.elevation.run_privileged(
                    [
                        "iptables", "-t", "nat", "-A", "PREROUTING",
                        "-p", "udp", "--dport", "53", "-j", "REDIRECT", "--to-ports", str(fallback_port),
                    ],
                    reason=f"Forward LAN DNS UDP port 53 to AstraShield on {lan_ip}:{fallback_port} for Smart TVs",
                )
                if res_u.ok:
                    self.elevation.run_privileged(
                        [
                            "iptables", "-t", "nat", "-A", "PREROUTING",
                            "-p", "tcp", "--dport", "53", "-j", "REDIRECT", "--to-ports", str(fallback_port),
                        ],
                        reason=f"Forward LAN DNS TCP port 53 to AstraShield on {lan_ip}:{fallback_port}",
                    )
                    self._nat_redirect_port = int(fallback_port)
                    self._nat_backend = "iptables"
                    return True, f"LAN AstraShield DNS active on {lan_ip}:53 (forwarded -> :{fallback_port})! Set TV Primary DNS={lan_ip}."
            if shutil.which("nft"):
                nft_script = (
                    f"add table ip astrashield_nat; "
                    f"add chain ip astrashield_nat prerouting {{ type nat hook prerouting priority dstnat ; policy accept ; }}; "
                    f"add rule ip astrashield_nat prerouting udp dport 53 redirect to :{int(fallback_port)}; "
                    f"add rule ip astrashield_nat prerouting tcp dport 53 redirect to :{int(fallback_port)}"
                )
                res_nft = self.elevation.run_privileged(
                    ["nft", nft_script],
                    reason=f"Forward LAN DNS port 53 to AstraShield :{fallback_port} via nftables for Smart TVs",
                )
                if res_nft.ok:
                    self._nat_redirect_port = int(fallback_port)
                    self._nat_backend = "nft"
                    return True, f"LAN AstraShield DNS active on {lan_ip}:53 (nftables -> :{fallback_port})! Set TV Primary DNS={lan_ip}."
        return True, (
            f"LAN AstraShield DNS listening on 0.0.0.0:{fallback_port} (LAN IP {lan_ip}). "
            f"For TVs that require port 53, run elevated or forward UDP 53 -> {fallback_port}."
        )

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
        pihole_doms = self._load_pihole_cache()
        last_upd = ""
        if self.pihole_meta_path.exists():
            try:
                last_upd = str(json.loads(self.pihole_meta_path.read_text(encoding="utf-8")).get("updated_at", ""))
            except Exception:
                pass
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
            pihole_cached_count=len(pihole_doms),
            pihole_last_updated=last_upd,
            youtube_safe_prune=bool(rules.get("youtube_safe_prune", True)),
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
