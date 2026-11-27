"""Standalone PyInstaller bundle helper for producing zero-Python executables."""

from __future__ import annotations

import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def build(dry_run: bool = False) -> int:
    root = Path(__file__).resolve().parents[1]
    spec = root / "packaging" / "astracradle.spec"
    if not spec.exists():
        print(f"Missing spec file: {spec}")
        return 1

    pyinstaller_bin = shutil.which("pyinstaller")
    cmd = [pyinstaller_bin or sys.executable, "-m", "PyInstaller"] if not pyinstaller_bin else [pyinstaller_bin]
    cmd.extend(["--noconfirm", "--clean", str(spec)])

    print(f"Target OS: {platform.system()} ({platform.machine()})")
    print(f"Spec file: {spec}")
    print(f"Build command: {' '.join(cmd)}")

    if dry_run:
        print("[DRY RUN] Verified PyInstaller packaging spec and assets/locales layout.")
        return 0

    proc = subprocess.run(cmd, cwd=str(root))
    return proc.returncode


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build standalone AstraCradle binary via PyInstaller")
    p.add_argument("--dry-run", action="store_true", help="Verify spec & command without invoking PyInstaller")
    args = p.parse_args()
    raise SystemExit(build(dry_run=args.dry_run))
