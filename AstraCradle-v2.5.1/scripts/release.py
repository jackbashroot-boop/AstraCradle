#!/usr/bin/env python3
"""AstraCradle release automation: sync version strings, run checks, bundle zip + SHA-256, and optionally tag/push."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

SYNC_FILES = (
    "astracradle/__init__.py",
    "pyproject.toml",
    "install_astracradle.sh",
    "install_astracradle.fish",
    "Install_AstraCradle.bat",
    "Install_AstraCradle.command",
    "START_HERE.txt",
    "README.md",
    "INSTALL_GUIDE.md",
    "packaging/winget/astracradle.yaml",
    "packaging/homebrew/astracradle.rb",
    "packaging/linux/PKGBUILD",
)


def read_current_version() -> str:
    init_text = (ROOT / "astracradle" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if not m:
        raise RuntimeError("Could not parse __version__ in astracradle/__init__.py")
    return m.group(1)


def sync_version_strings(version: str) -> list[str]:
    updated: list[str] = []
    ver_pat = re.compile(r"\b\d+\.\d+\.\d+\b")
    for rel in SYNC_FILES:
        p = ROOT / rel
        if not p.exists():
            continue
        orig = p.read_text(encoding="utf-8")
        new = orig
        if rel == "astracradle/__init__.py":
            new = re.sub(r'(__version__\s*=\s*")([^"]+)(")', rf"\g<1>{version}\3", orig)
        elif rel == "pyproject.toml":
            new = re.sub(r'(^version\s*=\s*")([^"]+)(")', rf"\g<1>{version}\3", orig, flags=re.MULTILINE)
        elif rel in ("packaging/winget/astracradle.yaml", "packaging/homebrew/astracradle.rb", "packaging/linux/PKGBUILD"):
            new = re.sub(r"(v?)\d+\.\d+\.\d+", rf"\g<1>{version}", orig)
        else:
            # Replace AstraCradle vX.Y.Z / AstraCradle-vX.Y.Z occurrences cleanly without touching Python min version 3.10
            new = re.sub(r"(AstraCradle[- ]v)\d+\.\d+\.\d+", rf"\g<1>{version}", orig)
            new = re.sub(r"(\(v)\d+\.\d+\.\d+(\))", rf"\g<1>{version}\2", new)
            new = re.sub(r"(AstraCradle v)\d+\.\d+\.\d+", rf"\g<1>{version}", new)
            if rel in ("install_astracradle.sh", "install_astracradle.fish", "Install_AstraCradle.bat", "Install_AstraCradle.command"):
                new = ver_pat.sub(lambda m: version if m.group(0).startswith(("1.", "2.")) else m.group(0), new)
        if new != orig:
            p.write_text(new, encoding="utf-8")
            updated.append(rel)
    return updated


def build_bundle(version: str, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_zip = out_dir / f"AstraCradle-v{version}-bundle.zip"
    sha_file = out_dir / f"SHA256-v{version}.txt"
    skip_parts = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", ".venv"}
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ROOT.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            if any(part in skip_parts or part.endswith(".egg-info") for part in rel.parts):
                continue
            zf.write(p, arcname=str(Path(f"AstraCradle-v{version}") / rel))
    digest = hashlib.sha256(out_zip.read_bytes()).hexdigest()
    sha_file.write_text(f"{digest}  {out_zip.name}\n", encoding="utf-8")
    return out_zip, sha_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AstraCradle Release Automation Helper")
    parser.add_argument("--version", type=str, default=None, help="Target X.Y.Z version (defaults to current __version__).")
    parser.add_argument("--sync-only", action="store_true", help="Sync version strings across installers/docs and exit.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip compileall and pytest verification.")
    parser.add_argument("--out-dir", type=str, default=str(ROOT.parent), help="Directory for output bundle zip + SHA256 file.")
    parser.add_argument("--tag", action="store_true", help="Create annotated git tag vX.Y.Z.")
    parser.add_argument("--push", action="store_true", help="Push commit & tag to origin (implies --tag).")
    args = parser.parse_args(argv)

    version = args.version or read_current_version()
    touched = sync_version_strings(version)
    print(f"Synced v{version} across {len(touched)} file(s): {touched}")
    if args.sync_only:
        return 0

    if not args.skip_tests:
        subprocess.run([sys.executable, "-m", "compileall", "-q", "astracradle", "tests"], cwd=ROOT, check=True)
        subprocess.run(["xvfb-run", "-a", sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)

    sys.path.insert(0, str(ROOT))
    from astracradle.core.updater import ModuleAndUpdateManager

    info = ModuleAndUpdateManager.get_local_build_info(ROOT)
    ModuleAndUpdateManager.write_build_info_file(ROOT / "astracradle", info)

    zip_path, sha_path = build_bundle(version, Path(args.out_dir))
    print(f"Built {zip_path} and {sha_path}")

    if args.tag or args.push:
        tag_name = f"v{version}"
        subprocess.run(["git", "tag", "-fa", tag_name, "-m", f"Release {tag_name}"], cwd=ROOT, check=True)
        if args.push:
            subprocess.run(["git", "push", "origin", "HEAD", "--follow-tags"], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
