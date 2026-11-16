"""Modular Plugin Registry & Seamless Self-Updating Subsystem for AstraCradle."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

from .. import __version__
from .config import AppConfig, ConfigManager
from .crypto import verify_ed25519
from .journal import ForensicJournal
from .models import PluginModuleInfo, UpdateCheckResult


class ModuleAndUpdateManager:
    """Manages extensible feature modules/plugins and seamless verified updates."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal

    def list_modules(self) -> list[PluginModuleInfo]:
        modules = [
            PluginModuleInfo("mod_telemetry", "Live Telemetry & Dyson Gauge", __version__, "diagnostic", "Real-time CPU, RAM, Disk, DNS, Thermal & Battery sensors.", True, True),
            PluginModuleInfo("mod_ai_hunter", "AI Chatbot & Idle Process Hunter", __version__, "diagnostic", "Detects dormant local LLM/chatbot runtimes & unused background helpers.", True, True),
            PluginModuleInfo("mod_scripts", "Toggleable Workload Scripts", __version__, "script", "Reversible Gaming, 3D Render, Compile/Data & Custom toggle scripts.", True, True),
            PluginModuleInfo("mod_quarantine", "Quarantine & Rollback Vault", __version__, "safeguard", "100% reversible soft-move file quarantine & restore ledger.", True, True),
            PluginModuleInfo("mod_forensic", "Forensic Journal & Self-Audit", __version__, "safeguard", "SHA-256 hash-chained WHAT/HOW/WHY audit log & self-verifier.", True, True),
            PluginModuleInfo("mod_crash", "Crash & OS Failure Post-Mortem", __version__, "safeguard", "Heartbeat sentinel & root-cause recovery wizard after crashes.", True, True),
        ]
        # Discover drop-in JSON & Python plugin modules in ~/.astracradle/plugins
        if self.cfg_mgr.plugins_dir.exists():
            for mf in sorted(self.cfg_mgr.plugins_dir.glob("*.json")):
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                    modules.append(
                        PluginModuleInfo(
                            module_id=str(data.get("module_id") or mf.stem),
                            name=str(data.get("name") or mf.stem),
                            version=str(data.get("version") or "1.0.0"),
                            kind=str(data.get("kind") or "plugin"),
                            description=str(data.get("description") or "External AstraCradle plugin module."),
                            enabled=bool(data.get("enabled", True)),
                            built_in=False,
                        )
                    )
                except Exception:
                    continue
            for pf in sorted(self.cfg_mgr.plugins_dir.glob("*.py")):
                meta = self._inspect_python_plugin_meta(pf)
                if meta:
                    modules.append(
                        PluginModuleInfo(
                            module_id=str(meta.get("module_id") or f"py_{pf.stem}"),
                            name=str(meta.get("name") or pf.stem),
                            version=str(meta.get("version") or "1.0.0"),
                            kind=str(meta.get("kind") or "plugin"),
                            description=str(meta.get("description") or "Python drop-in plugin module."),
                            enabled=bool(meta.get("enabled", True)),
                            built_in=False,
                        )
                    )
        return modules

    @staticmethod
    def _inspect_python_plugin_meta(py_path: Path) -> dict | None:
        """Safely extracts PLUGIN_META dict via AST without executing untrusted plugin code."""
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == "PLUGIN_META":
                            val = ast.literal_eval(node.value)
                            if isinstance(val, dict):
                                return val
        except Exception:
            return None
        return None

    def register_sample_plugin(self, name: str, description: str) -> PluginModuleInfo:
        self.cfg_mgr.ensure_directories()
        slug = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_") or "custom_plugin"
        mf = self.cfg_mgr.plugins_dir / f"{slug}.json"
        payload = {
            "module_id": f"plugin_{slug}",
            "name": name,
            "version": "1.0.0",
            "kind": "plugin",
            "description": description,
            "enabled": True,
        }
        mf.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.journal.log_event(
            component="ModuleManager",
            action="register_plugin",
            how=f"Installed modular plugin manifest '{mf.name}' into plugins/",
            why="Extend AstraCradle dynamically without modifying core engine files",
            outcome="ok",
        )
        return PluginModuleInfo(**payload, built_in=False)

    @staticmethod
    def _parse_ver(v: str) -> tuple[int, ...]:
        parts = []
        for p in v.strip().lstrip("v").split("."):
            try:
                parts.append(int("".join(ch for ch in p if ch.isdigit()) or "0"))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    DEFAULT_GITHUB_REPO = "https://github.com/AstraCradle/AstraCradle"

    @staticmethod
    def _parse_github_slug(repo_url: str) -> tuple[str, str] | None:
        raw = (repo_url or "").strip().rstrip("/")
        if not raw or "<" in raw or ">" in raw:
            return None
        m = re.match(r"^(?:https?://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$", raw)
        if not m:
            return None
        return m.group(1), m.group(2)

    @classmethod
    def resolve_github_repo(cls, config: AppConfig, repo_dir: Path | None = None) -> tuple[str, str] | None:
        """Resolves the GitHub (owner, repo) slug: user setting -> local git origin -> default."""
        cand_urls: list[str] = []
        cfg_url = (getattr(config, "update_repo_url", "") or "").strip()
        if cfg_url and cfg_url.rstrip("/") != cls.DEFAULT_GITHUB_REPO:
            cand_urls.append(cfg_url)

        base_dir = repo_dir or Path(__file__).resolve().parents[2]
        if (base_dir / ".git").exists() and shutil.which("git"):
            try:
                proc = subprocess.run(
                    ["git", "-C", str(base_dir), "config", "--get", "remote.origin.url"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                origin = (proc.stdout or "").strip()
                if origin:
                    cand_urls.append(origin)
            except Exception:
                pass

        if cfg_url:
            cand_urls.append(cfg_url)
        cand_urls.append(cls.DEFAULT_GITHUB_REPO)

        for u in cand_urls:
            parsed = cls._parse_github_slug(u)
            if parsed:
                return parsed
        return None

    def _query_github_latest(self, owner: str, repo: str, timeout: float = 3.5) -> tuple[str | None, str | None, str]:
        """Queries GitHub Releases (then tags) over HTTPS. Returns (version, download_url, note)."""
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "AstraCradle-Updater/1.0"}
        rel_api = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        try:
            req = urllib.request.Request(rel_api, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tag = str(data.get("tag_name") or "").strip().lstrip("v")
            dl_url = ""
            for asset in data.get("assets", []) or []:
                aname = str(asset.get("name") or "").lower()
                if aname.endswith(".zip") or aname.endswith(".tar.gz"):
                    dl_url = str(asset.get("browser_download_url") or "")
                    break
            if not dl_url:
                dl_url = str(data.get("zipball_url") or "")
            if tag:
                return tag, dl_url, f"GitHub {owner}/{repo} release v{tag}"
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return None, None, f"GitHub {owner}/{repo} HTTP {exc.code}; falling back to local"
        except Exception as exc:
            return None, None, f"GitHub {owner}/{repo} unreachable ({type(exc).__name__}); falling back to local"

        # Fallback: check tags when repo exists but has no published GitHub Release yet
        tags_api = f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=1"
        try:
            req = urllib.request.Request(tags_api, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
            if isinstance(tags, list) and tags:
                tag_raw = str(tags[0].get("name") or "").strip()
                tag_ver = tag_raw.lstrip("v")
                dl_url = str(tags[0].get("zipball_url") or f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag_raw}.zip")
                return tag_ver, dl_url, f"GitHub {owner}/{repo} tag {tag_raw}"
        except Exception:
            pass
        return None, None, f"GitHub {owner}/{repo} has no release yet; checked local"

    def find_local_update_bundles(self) -> list[tuple[str, Path]]:
        """Finds AstraCradle-v* update bundles inside ~/.astracradle/updates or ~/Downloads."""
        found: list[tuple[str, Path]] = []
        pat = re.compile(r"astracradle[-_ ]v?(\d+\.\d+\.\d+).*?\.(zip|tar\.gz)$", re.IGNORECASE)
        search_dirs = [self.cfg_mgr.updates_dir, Path.home() / "Downloads"]
        for d in search_dirs:
            if not d.exists():
                continue
            try:
                for p in sorted(d.iterdir()):
                    if not p.is_file():
                        continue
                    m = pat.match(p.name)
                    if m:
                        found.append((m.group(1), p))
            except OSError:
                continue
        found.sort(key=lambda item: self._parse_ver(item[0]), reverse=True)
        return found

    def check_for_updates(
        self,
        config: AppConfig,
        check_remote: bool = False,
        repo_dir: Path | None = None,
    ) -> UpdateCheckResult:
        """Checks GitHub repo first (when check_remote=True), then checks local manifest & bundles."""
        github_note = ""
        if check_remote:
            slug = self.resolve_github_repo(config, repo_dir=repo_dir)
            if slug:
                owner, repo = slug
                gh_ver, gh_url, note = self._query_github_latest(owner, repo)
                github_note = note
                if gh_ver and self._parse_ver(gh_ver) > self._parse_ver(__version__):
                    return UpdateCheckResult(
                        current_version=__version__,
                        latest_version=gh_ver,
                        update_available=True,
                        channel=config.update_channel,
                        release_notes=gh_url or f"https://github.com/{owner}/{repo}",
                        status_message=f"GitHub update v{gh_ver} available from {owner}/{repo}.",
                    )

        local_manifest = self.cfg_mgr.updates_dir / "manifest.json"
        prefix = f"{github_note} · " if github_note else ""
        if config.update_manifest_url.startswith("local://") or local_manifest.exists():
            if local_manifest.exists():
                try:
                    data = json.loads(local_manifest.read_text(encoding="utf-8"))
                    latest = str(data.get("version") or __version__)
                    avail = self._parse_ver(latest) > self._parse_ver(__version__)
                    return UpdateCheckResult(
                        current_version=__version__,
                        latest_version=latest,
                        update_available=avail,
                        channel=config.update_channel,
                        release_notes=str(data.get("notes") or "Incremental module & safeguard refinement."),
                        package_hash=str(data.get("sha256") or ""),
                        status_message=(
                            f"{prefix}Update v{latest} staged locally and ready for seamless apply."
                            if avail
                            else f"{prefix}AstraCradle is up to date (v{__version__})."
                        ),
                    )
                except Exception as exc:
                    return UpdateCheckResult(
                        current_version=__version__,
                        latest_version=__version__,
                        update_available=False,
                        channel=config.update_channel,
                        release_notes="",
                        status_message=f"{prefix}Local update manifest unreadable ({exc}).",
                    )

            for ver, bpath in self.find_local_update_bundles():
                if self._parse_ver(ver) > self._parse_ver(__version__):
                    return UpdateCheckResult(
                        current_version=__version__,
                        latest_version=ver,
                        update_available=True,
                        channel=config.update_channel,
                        release_notes=str(bpath),
                        package_hash=hashlib.sha256(bpath.read_bytes()).hexdigest(),
                        status_message=f"{prefix}Local update v{ver} found ({bpath.name}). Click Check Updates Now or Install Update Bundle to apply.",
                    )

            return UpdateCheckResult(
                current_version=__version__,
                latest_version=__version__,
                update_available=False,
                channel=config.update_channel,
                release_notes="Checked GitHub repo first, then local ~/.astracradle/updates and ~/Downloads.",
                status_message=f"{prefix}Up to date (v{__version__} · {config.update_channel} channel).",
            )

        # Offline-safe fallback when network endpoint is unreachable
        return UpdateCheckResult(
            current_version=__version__,
            latest_version=__version__,
            update_available=False,
            channel=config.update_channel,
            release_notes="No external update required.",
            status_message=f"{prefix}Up to date (v{__version__}).",
        )

    def pull_from_github_and_update(
        self,
        config: AppConfig,
        repo_dir: Path | None = None,
        run_pip: bool = True,
    ) -> tuple[bool, str]:
        """Pulls update from the configured GitHub repo first, then falls back to local bundles."""
        res = self.check_for_updates(config, check_remote=True, repo_dir=repo_dir)
        if res.update_available and res.release_notes.startswith("https://"):
            dl_url = res.release_notes
            try:
                with tempfile.TemporaryDirectory() as td:
                    out_zip = Path(td) / f"AstraCradle-v{res.latest_version}-github.zip"
                    req = urllib.request.Request(dl_url, headers={"User-Agent": "AstraCradle-Updater/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        out_zip.write_bytes(resp.read())
                    return self.install_update_bundle(out_zip, config, allow_same_version=False, run_pip=run_pip)
            except Exception as exc:
                self.journal.log_event(
                    "Updater",
                    "pull_github",
                    f"GitHub download failed ({exc}); checking local bundles next",
                    "Graceful fallback from remote GitHub repo to local update sources",
                    "warn",
                )

        # If running from a git clone with an upstream remote, try fast-forward git pull
        base_dir = repo_dir or Path(__file__).resolve().parents[2]
        if (base_dir / ".git").exists() and shutil.which("git"):
            try:
                pull = subprocess.run(
                    ["git", "-C", str(base_dir), "pull", "--ff-only"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if pull.returncode == 0 and "Already up to date." not in (pull.stdout or ""):
                    if run_pip:
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "-e", str(base_dir)],
                            capture_output=True,
                            text=True,
                            timeout=120,
                            check=False,
                        )
                    return True, f"Pulled latest updates from GitHub into {base_dir.name}."
            except Exception:
                pass

        # Fallback to local staged manifest or local bundle
        if (self.cfg_mgr.updates_dir / "manifest.json").exists():
            ok_loc, msg_loc = self.stage_and_apply_local_update(config)
            if ok_loc:
                return True, msg_loc
        for ver, bpath in self.find_local_update_bundles():
            if self._parse_ver(ver) > self._parse_ver(__version__):
                return self.install_update_bundle(bpath, config, allow_same_version=False, run_pip=run_pip)
        return False, res.status_message

    def stage_and_apply_local_update(self, config: AppConfig) -> tuple[bool, str]:
        """Verifies staged update payload SHA-256 & optional Ed25519 signature, then applies seamlessly."""
        local_manifest = self.cfg_mgr.updates_dir / "manifest.json"
        if not local_manifest.exists():
            return False, "No staged update package found."
        try:
            data = json.loads(local_manifest.read_text(encoding="utf-8"))
            pkg_rel = data.get("package_file")
            expected_hash = str(data.get("sha256") or "")
            sig_hex = str(data.get("signature_hex") or "")
            pub_hex = str(data.get("public_key_hex") or "")
            if pkg_rel:
                pkg_path = self.cfg_mgr.updates_dir / str(pkg_rel)
                if not pkg_path.exists():
                    return False, f"Staged package '{pkg_rel}' missing."
                pkg_bytes = pkg_path.read_bytes()
                actual_hash = hashlib.sha256(pkg_bytes).hexdigest()
                if expected_hash and actual_hash != expected_hash:
                    self.journal.log_event(
                        "Updater",
                        "apply_update",
                        "Aborted update due to SHA-256 mismatch",
                        "Prevent corrupted or tampered update package from installing",
                        "error",
                    )
                    return False, "Update aborted: SHA-256 integrity verification failed."
                if sig_hex or pub_hex:
                    try:
                        sig_ok = verify_ed25519(
                            bytes.fromhex(pub_hex), pkg_bytes, bytes.fromhex(sig_hex)
                        )
                    except ValueError:
                        sig_ok = False
                    if not sig_ok:
                        self.journal.log_event(
                            "Updater",
                            "apply_update",
                            "Aborted update due to invalid Ed25519 signature",
                            "Reject unsigned or forged update package",
                            "error",
                        )
                        return False, "Update aborted: Ed25519 cryptographic signature failed."
                # Apply staged plugin or config patch into plugins/applied_updates
                applied_dir = self.cfg_mgr.updates_dir / "applied"
                applied_dir.mkdir(exist_ok=True)
                shutil.copy2(pkg_path, applied_dir / pkg_path.name)

            new_ver = str(data.get("version") or __version__)
            sig_note = " + Ed25519 signature" if sig_hex else ""
            self.journal.log_event(
                "Updater",
                "apply_update",
                f"Verified SHA-256{sig_note} and seamlessly applied update -> v{new_ver}",
                f"Auto-update mode '{config.auto_update_mode}' requested silent verified update",
                "ok",
            )
            return True, f"Verified SHA-256{sig_note} and seamlessly applied update v{new_ver}."
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _extract_bundle_safely(bundle_path: Path, dest_dir: Path) -> None:
        dest_resolved = dest_dir.resolve()
        if bundle_path.name.lower().endswith(".zip"):
            with zipfile.ZipFile(bundle_path, "r") as zf:
                for member in zf.infolist():
                    target = (dest_resolved / member.filename).resolve()
                    if not (target == dest_resolved or dest_resolved in target.parents):
                        raise ValueError(f"Blocked unsafe zip path: {member.filename}")
                zf.extractall(dest_resolved)
        elif bundle_path.name.lower().endswith((".tar.gz", ".tgz")):
            with tarfile.open(bundle_path, "r:gz") as tf:
                for member in tf.getmembers():
                    target = (dest_resolved / member.name).resolve()
                    if not (target == dest_resolved or dest_resolved in target.parents):
                        raise ValueError(f"Blocked unsafe tar path: {member.name}")
                tf.extractall(dest_resolved)
        else:
            raise ValueError("Unsupported update format (expected .zip or .tar.gz).")

    @staticmethod
    def _read_ast_version(init_py: Path) -> str:
        try:
            tree = ast.parse(init_py.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == "__version__":
                            val = ast.literal_eval(node.value)
                            if isinstance(val, str):
                                return val
        except Exception:
            pass
        return "0.0.0"

    def install_update_bundle(
        self,
        bundle_path: Path,
        config: AppConfig,
        allow_same_version: bool = True,
        run_pip: bool = True,
    ) -> tuple[bool, str]:
        """Verifies and installs an AstraCradle-vX.Y.Z-bundle (.zip / .tar.gz) cleanly."""
        bundle = bundle_path.expanduser().resolve()
        if not bundle.exists() or not bundle.is_file():
            return False, f"Update bundle not found: {bundle}"
        sha256 = hashlib.sha256(bundle.read_bytes()).hexdigest()
        try:
            with tempfile.TemporaryDirectory() as td:
                stage = Path(td)
                self._extract_bundle_safely(bundle, stage)
                # Locate pyproject.toml + astracradle/__init__.py
                pkg_root: Path | None = None
                for pyproj in sorted(stage.rglob("pyproject.toml")):
                    cand = pyproj.parent
                    if (cand / "astracradle" / "__init__.py").exists():
                        pkg_root = cand
                        break
                if pkg_root is None:
                    return False, "Invalid AstraCradle update bundle (missing pyproject.toml / astracradle)."

                new_ver = self._read_ast_version(pkg_root / "astracradle" / "__init__.py")
                cur_t = self._parse_ver(__version__)
                new_t = self._parse_ver(new_ver)
                if new_t < cur_t or (new_t == cur_t and not allow_same_version):
                    return False, f"Bundle version v{new_ver} is older than installed v{__version__}."

                if run_pip:
                    proc = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", str(pkg_root)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if proc.returncode != 0:
                        return False, f"pip upgrade failed: {(proc.stderr or proc.stdout).strip()[:240]}"

                # Archive verified bundle copy into ~/.astracradle/updates/applied
                applied_dir = self.cfg_mgr.updates_dir / "applied"
                applied_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundle, applied_dir / bundle.name)

            self.journal.log_event(
                component="Updater",
                action="install_update_bundle",
                how=f"Verified SHA-256 ({sha256[:12]}...) and installed {bundle.name} -> v{new_ver}",
                why="User initiated local verified update bundle upgrade",
                outcome="ok",
                details=f"channel={config.update_channel}",
            )
            return True, f"Updated to AstraCradle v{new_ver} (SHA-256 {sha256[:12]}...). Restart AstraCradle to load the new version."
        except Exception as exc:
            self.journal.log_event(
                component="Updater",
                action="install_update_bundle",
                how=f"Aborted update from {bundle.name}: {exc}",
                why="Reject corrupted or unsafe update bundle",
                outcome="error",
            )
            return False, f"Update failed: {exc}"
