#!/usr/bin/env bash
set -euo pipefail
# Build AstraCradle macOS .app bundle + compressed .dmg image with native .icns icon
cd "$(dirname "$0")/../.."
python3 packaging/build_standalone.py --onedir
mkdir -p dist/dmg_root
if [ -d "dist/AstraCradle.app" ]; then
  mkdir -p "dist/AstraCradle.app/Contents/Resources"
  cp -f assets/astracradle.icns "dist/AstraCradle.app/Contents/Resources/astracradle.icns" || true
  cp -R dist/AstraCradle.app dist/dmg_root/
else
  APP="dist/dmg_root/AstraCradle.app"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cp -R dist/AstraCradle/* "$APP/Contents/MacOS/"
  cp -f assets/astracradle.icns "$APP/Contents/Resources/astracradle.icns" || true
  cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>AstraCradle</string>
  <key>CFBundleDisplayName</key><string>AstraCradle</string>
  <key>CFBundleIdentifier</key><string>local.astracradle.studio</string>
  <key>CFBundleExecutable</key><string>AstraCradle</string>
  <key>CFBundleIconFile</key><string>astracradle.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
fi
ln -sfn /Applications dist/dmg_root/Applications
hdiutil create -volname "AstraCradle v1.0.0" -srcfolder dist/dmg_root -ov -format UDZO "dist/AstraCradle-v1.0.0-macOS.dmg"
