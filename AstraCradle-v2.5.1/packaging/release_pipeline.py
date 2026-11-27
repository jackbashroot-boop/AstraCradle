"""Generates standalone native installer scripts for Windows (Inno Setup .iss), macOS (.dmg), and Linux (.AppImage)."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astracradle import __version__


INNO_SETUP_TEMPLATE = f"""; AstraCradle Windows Installer Script (Inno Setup 6+)
[Setup]
AppName=AstraCradle
AppVersion={__version__}
AppPublisher=AstraCradle Local Studio
DefaultDirName={{localappdata}}\\Programs\\AstraCradle
DefaultGroupName=AstraCradle
OutputDir=dist
OutputBaseFilename=AstraCradle-Setup-v{__version__}
SetupIconFile=..\\assets\\astracradle_icon.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "..\\dist\\AstraCradle\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\AstraCradle"; Filename: "{{app}}\\AstraCradle.exe"
Name: "{{autodesktop}}\\AstraCradle"; Filename: "{{app}}\\AstraCradle.exe"

[UninstallRun]
Filename: "{{app}}\\AstraCradle.exe"; Parameters: "--uninstall"; RunOnceId: "AstraCradleCleanUninstall"
"""


MACOS_DMG_SCRIPT = f"""#!/usr/bin/env bash
set -euo pipefail
# Build AstraCradle macOS .app bundle + compressed .dmg image
cd "$(dirname "$0")/.."
python3 packaging/build_standalone.py --onedir
mkdir -p dist/dmg_root
cp -R dist/AstraCradle.app dist/dmg_root/ || cp -R dist/AstraCradle dist/dmg_root/
ln -sfn /Applications dist/dmg_root/Applications
hdiutil create -volname "AstraCradle v{__version__}" -srcfolder dist/dmg_root -ov -format UDZO "dist/AstraCradle-v{__version__}-macOS.dmg"
"""


LINUX_APPIMAGE_SCRIPT = f"""#!/usr/bin/env bash
set -euo pipefail
# Build AstraCradle Linux AppDir / AppImage
cd "$(dirname "$0")/.."
python3 packaging/build_standalone.py --onedir
APPDIR="dist/AstraCradle.AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -R dist/AstraCradle/* "$APPDIR/usr/bin/"
cp assets/astracradle_256.png "$APPDIR/astracradle.png"
cat > "$APPDIR/astracradle.desktop" <<'EOF'
[Desktop Entry]
Name=AstraCradle
Exec=AstraCradle
Icon=astracradle
Type=Application
Categories=System;Utility;
EOF
cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/AstraCradle" "$@"
EOF
chmod +x "$APPDIR/AppRun"
echo "AppDir prepared at $APPDIR (run appimagetool $APPDIR dist/AstraCradle-v{__version__}-x86_64.AppImage)"
"""


def generate_all(out_dir: Path | None = None) -> list[Path]:
    target = out_dir or (ROOT / "packaging" / "installers")
    target.mkdir(parents=True, exist_ok=True)
    files = {
        target / "AstraCradle.iss": INNO_SETUP_TEMPLATE,
        target / "build_macos_dmg.sh": MACOS_DMG_SCRIPT,
        target / "build_linux_appimage.sh": LINUX_APPIMAGE_SCRIPT,
    }
    created: list[Path] = []
    for p, content in files.items():
        p.write_text(content, encoding="utf-8")
        if p.suffix == ".sh":
            p.chmod(0o755)
        created.append(p)
    return created


if __name__ == "__main__":
    for path in generate_all():
        print("Generated:", path)
