#!/usr/bin/env bash
set -euo pipefail
# Build AstraCradle Linux AppDir / AppImage with multi-size XDG Start Menu icons
cd "$(dirname "$0")/../.."
python3 packaging/build_standalone.py --onedir
APPDIR="dist/AstraCradle.AppDir"
mkdir -p "$APPDIR/usr/bin" \
  "$APPDIR/usr/share/icons/hicolor/32x32/apps" \
  "$APPDIR/usr/share/icons/hicolor/64x64/apps" \
  "$APPDIR/usr/share/icons/hicolor/128x128/apps" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
  "$APPDIR/usr/share/applications"

cp -R dist/AstraCradle/* "$APPDIR/usr/bin/"
cp assets/astracradle_emblem_32.png "$APPDIR/usr/share/icons/hicolor/32x32/apps/astracradle.png"
cp assets/astracradle_emblem_64.png "$APPDIR/usr/share/icons/hicolor/64x64/apps/astracradle.png"
cp assets/astracradle_emblem_128.png "$APPDIR/usr/share/icons/hicolor/128x128/apps/astracradle.png"
cp assets/astracradle_emblem_256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/astracradle.png"
cp assets/astracradle_emblem_256.png "$APPDIR/astracradle.png"

cat > "$APPDIR/astracradle.desktop" <<'EOF'
[Desktop Entry]
Name=AstraCradle
GenericName=System Optimizer & Diagnostics
Comment=Dyson-Core OS Optimizer & Self-Healing Studio
Exec=AstraCradle
Icon=astracradle
StartupWMClass=AstraCradle
Type=Application
Categories=System;Utility;
EOF
cp "$APPDIR/astracradle.desktop" "$APPDIR/usr/share/applications/astracradle.desktop"

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/AstraCradle" "$@"
EOF
chmod +x "$APPDIR/AppRun"
echo "AppDir prepared at $APPDIR (run appimagetool $APPDIR dist/AstraCradle-v1.0.0-x86_64.AppImage)"
