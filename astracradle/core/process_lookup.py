"""Live PID# Inspection, Local Knowledge Base & Direct Web Search Process Explainer."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import time
from typing import Callable
import urllib.parse
import urllib.request

import psutil

from .config import ConfigManager


LOCAL_PROC_KB: dict[str, str] = {
    "ollama": "Local LLM inference server running GGUF models in RAM/VRAM; safe to pause or stop when not chatting.",
    "lmstudio": "LM Studio desktop local AI model runner and OpenAI-compatible API server.",
    "llama-server": "llama.cpp HTTP inference server hosting local language models.",
    "koboldcpp": "Single-file local LLM/storytelling inference server built on llama.cpp.",
    "sunshine": "LizardByte Sunshine low-latency desktop & game streaming host for Moonlight clients.",
    "moonlight": "Moonlight open-source NVIDIA GameStream / Sunshine remote client.",
    "tailscaled": "Tailscale WireGuard mesh VPN background daemon enabling zero-trust device access.",
    "tailscale": "Tailscale CLI / tray helper for private tailnet networking.",
    "systemd": "Linux init system and service manager (PID 1 / user session supervisor).",
    "dbus-daemon": "D-Bus inter-process communication message bus daemon used by desktop & system services.",
    "pipewire": "Modern low-latency Linux multimedia server handling audio and Wayland screen video streams.",
    "wireplumber": "Session and policy manager for PipeWire audio/video streams.",
    "kwin_wayland": "KDE Plasma Wayland window compositor handling display rendering and input.",
    "kwin_x11": "KDE Plasma X11 window manager and compositor.",
    "gnome-shell": "GNOME desktop user interface shell and Wayland/X11 compositor.",
    "xorg": "X.Org X11 display server managing graphical output and input devices.",
    "xwayland": "X11 compatibility server running legacy X11 apps inside a Wayland session.",
    "networkmanager": "System network daemon managing Ethernet, Wi-Fi, VPNs, and interface failover metrics.",
    "bluetoothd": "BlueZ Bluetooth protocol daemon handling device pairing, audio, and proximity handshakes.",
    "dockerd": "Docker Engine daemon managing Linux containers and virtual bridge networks.",
    "containerd": "Industry-standard container runtime daemon supervising container execution.",
    "python3": "Python 3 interpreter running a script, service, or AstraCradle component (check cmdline).",
    "python": "Python interpreter running a user script or tool.",
    "node": "Node.js JavaScript runtime executing a local dev server, Electron app, or build tool.",
    "code": "Visual Studio Code editor / language server process.",
    "chrome": "Google Chrome web browser process (tab, GPU, or extension worker).",
    "chromium": "Chromium open-source web browser process.",
    "firefox": "Mozilla Firefox web browser process.",
    "steam": "Valve Steam gaming client, overlay, and game launcher daemon.",
    "discord": "Discord voice, chat, and community desktop client.",
    "adb": "Android Debug Bridge daemon communicating with USB/Wi-Fi Android devices.",
    "scrcpy": "High-performance Android screen mirroring and remote input client.",
    "sshd": "OpenSSH server daemon listening for encrypted remote terminal connections.",
    "cupsd": "CUPS printing daemon managing local and network printers.",
    "docker": "Docker CLI / container management client.",
    "bash": "GNU Bourne-Again SHell interactive or script session.",
    "fish": "Friendly Interactive SHell (Fish) user shell session.",
    "zsh": "Z shell interactive session.",
}


@dataclass
class ProcessLookupResult:
    pid: int | None
    name: str
    exe: str
    cmdline: str
    cpu_percent: float
    memory_mb: float
    status: str
    brief_description: str
    source_name: str  # Web Search (DuckDuckGo) | Web Search (Wikipedia) | Cache | Local Process KB | OS Commandline
    source_url: str = ""
    safe_note: str = ""


class ProcessIntelligenceLookup:
    """Inspects live PIDs and performs direct cached web search lookups for plain-English descriptions."""

    def __init__(self, cfg_mgr: ConfigManager) -> None:
        self.cfg_mgr = cfg_mgr
        self.cache_path: Path = cfg_mgr.state_dir / "process_web_cache.json"
        self._cache: dict[str, dict[str, str]] = self._load_cache()

    def _load_cache(self) -> dict[str, dict[str, str]]:
        if not self.cache_path.exists():
            return {}
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_cache(self) -> None:
        try:
            self.cfg_mgr.ensure_directories()
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception:
            pass

    @staticmethod
    def _clean_proc_key(name: str) -> str:
        base = Path(name.strip()).name.lower()
        base = re.sub(r"\.(exe|bin|appimage)$", "", base)
        return base

    @staticmethod
    def inspect_pid(pid: int) -> dict[str, object]:
        try:
            pr = psutil.Process(int(pid))
            with pr.oneshot():
                name = pr.name()
                try:
                    exe = pr.exe() or ""
                except Exception:
                    exe = ""
                try:
                    cmd = " ".join(pr.cmdline())[:220]
                except Exception:
                    cmd = ""
                try:
                    mem_mb = round(pr.memory_info().rss / (1024 * 1024), 1)
                except Exception:
                    mem_mb = 0.0
                try:
                    cpu = round(pr.cpu_percent(interval=None), 1)
                except Exception:
                    cpu = 0.0
                try:
                    st = pr.status()
                except Exception:
                    st = "running"
                return {
                    "found": True,
                    "pid": int(pid),
                    "name": name,
                    "exe": exe,
                    "cmdline": cmd,
                    "mem_mb": mem_mb,
                    "cpu": cpu,
                    "status": st,
                }
        except Exception:
            return {
                "found": False,
                "pid": int(pid),
                "name": f"PID {pid}",
                "exe": "",
                "cmdline": "",
                "mem_mb": 0.0,
                "cpu": 0.0,
                "status": "exited",
            }

    @staticmethod
    def _default_http_get_json(url: str, timeout: float = 2.5) -> dict | None:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AstraCradle/2.4 ProcessInspector (+https://github.com/jackbashroot-boop/AstraCradle)"},
        )
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception:
            return None

    @staticmethod
    def _whatis_hint(proc_key: str) -> str:
        if not proc_key or not shutil.which("whatis"):
            return ""
        try:
            res = subprocess.run(["whatis", proc_key], capture_output=True, text=True, timeout=1.2)
            if res.returncode == 0 and res.stdout:
                line = res.stdout.splitlines()[0].strip()
                if " - " in line:
                    return line.split(" - ", 1)[1].strip()
        except Exception:
            pass
        return ""

    def lookup(
        self,
        pid_or_name: int | str,
        *,
        allow_web: bool = True,
        force_refresh: bool = False,
        fetch_fn: Callable[[str], dict | None] | None = None,
    ) -> ProcessLookupResult:
        pid_num: int | None = None
        proc_info: dict[str, object] = {"found": False, "name": str(pid_or_name), "exe": "", "cmdline": "", "mem_mb": 0.0, "cpu": 0.0, "status": "unknown"}

        raw_s = str(pid_or_name).strip()
        if isinstance(pid_or_name, int) or raw_s.isdigit():
            pid_num = int(raw_s)
            proc_info = self.inspect_pid(pid_num)
            proc_name = str(proc_info["name"])
        else:
            proc_name = raw_s
            for pr in psutil.process_iter(["pid", "name"]):
                try:
                    if (pr.info.get("name") or "").lower() == raw_s.lower():
                        pid_num = int(pr.info["pid"])
                        proc_info = self.inspect_pid(pid_num)
                        break
                except Exception:
                    continue

        key = self._clean_proc_key(proc_name)
        exe = str(proc_info.get("exe") or "")
        cmdline = str(proc_info.get("cmdline") or "")
        mem_mb = float(proc_info.get("mem_mb") or 0.0)
        cpu = float(proc_info.get("cpu") or 0.0)
        status = str(proc_info.get("status") or "unknown")

        # 1. Check fresh cache
        cached = self._cache.get(key)
        if cached and not force_refresh:
            age = time.time() - float(cached.get("ts", 0) or 0)
            if age < 7 * 86400 and cached.get("summary"):
                return ProcessLookupResult(
                    pid=pid_num,
                    name=proc_name,
                    exe=exe,
                    cmdline=cmdline,
                    cpu_percent=cpu,
                    memory_mb=mem_mb,
                    status=status,
                    brief_description=str(cached["summary"]),
                    source_name=str(cached.get("source_name") or "Cached Web Lookup"),
                    source_url=str(cached.get("source_url") or ""),
                    safe_note=LOCAL_PROC_KB.get(key, ""),
                )

        # 2. Direct live web search (DuckDuckGo Instant Answer -> Wikipedia Summary)
        if allow_web and key and not key.startswith("pid "):
            http_get = fetch_fn or self._default_http_get_json
            q_enc = urllib.parse.quote_plus(f"{key} software process")
            ddg = http_get(f"https://api.duckduckgo.com/?q={q_enc}&format=json&no_html=1&skip_disambig=1")
            if isinstance(ddg, dict):
                abstract = str(ddg.get("AbstractText") or "").strip()
                if abstract:
                    short = ". ".join(abstract.split(". ")[:2]).strip()
                    src_url = str(ddg.get("AbstractURL") or "https://duckduckgo.com")
                    self._cache[key] = {
                        "summary": short,
                        "source_name": "Web Search (DuckDuckGo)",
                        "source_url": src_url,
                        "ts": str(time.time()),
                    }
                    self._save_cache()
                    return ProcessLookupResult(
                        pid=pid_num,
                        name=proc_name,
                        exe=exe,
                        cmdline=cmdline,
                        cpu_percent=cpu,
                        memory_mb=mem_mb,
                        status=status,
                        brief_description=short,
                        source_name="Web Search (DuckDuckGo)",
                        source_url=src_url,
                        safe_note=LOCAL_PROC_KB.get(key, ""),
                    )

            wiki_slug = urllib.parse.quote(key.replace(" ", "_"))
            wiki = http_get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_slug}")
            if isinstance(wiki, dict):
                extract = str(wiki.get("extract") or "").strip()
                if extract and "may refer to:" not in extract.lower():
                    short = ". ".join(extract.split(". ")[:2]).strip()
                    wurl = str(((wiki.get("content_urls") or {}).get("desktop") or {}).get("page") or "")
                    self._cache[key] = {
                        "summary": short,
                        "source_name": "Web Search (Wikipedia)",
                        "source_url": wurl,
                        "ts": str(time.time()),
                    }
                    self._save_cache()
                    return ProcessLookupResult(
                        pid=pid_num,
                        name=proc_name,
                        exe=exe,
                        cmdline=cmdline,
                        cpu_percent=cpu,
                        memory_mb=mem_mb,
                        status=status,
                        brief_description=short,
                        source_name="Web Search (Wikipedia)",
                        source_url=wurl,
                        safe_note=LOCAL_PROC_KB.get(key, ""),
                    )

        # 3. Local Process KB -> `whatis` man page -> Commandline fallback
        if key in LOCAL_PROC_KB:
            return ProcessLookupResult(
                pid=pid_num,
                name=proc_name,
                exe=exe,
                cmdline=cmdline,
                cpu_percent=cpu,
                memory_mb=mem_mb,
                status=status,
                brief_description=LOCAL_PROC_KB[key],
                source_name="Local Process KB",
            )
        man_desc = self._whatis_hint(key)
        if man_desc:
            return ProcessLookupResult(
                pid=pid_num,
                name=proc_name,
                exe=exe,
                cmdline=cmdline,
                cpu_percent=cpu,
                memory_mb=mem_mb,
                status=status,
                brief_description=f"System binary ({man_desc}).",
                source_name="Local Manual (whatis)",
            )
        cmd_desc = f"Running command `{cmdline[:110]}`" if cmdline else f"Active process '{proc_name}' ({exe or 'system binary'})."
        return ProcessLookupResult(
            pid=pid_num,
            name=proc_name,
            exe=exe,
            cmdline=cmdline,
            cpu_percent=cpu,
            memory_mb=mem_mb,
            status=status,
            brief_description=cmd_desc,
            source_name="OS Process Inspection",
        )
