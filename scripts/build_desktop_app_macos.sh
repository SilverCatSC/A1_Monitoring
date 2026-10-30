#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="$ROOT/artifacts/desktop_app/A1 Monitoring.app"
if [ -e "$OUTPUT" ]; then echo "Build already exists: $OUTPUT" >&2; exit 1; fi
mkdir -p "$OUTPUT/Contents/MacOS" "$OUTPUT/Contents/Resources"
/usr/bin/swiftc -module-cache-path "$ROOT/artifacts/desktop_app/swift-cache" \
  "$ROOT/desktop_app/A1Monitoring.swift" -o "$OUTPUT/Contents/MacOS/A1Monitoring" -framework Cocoa
"$OUTPUT/Contents/MacOS/A1Monitoring" --make-icon "$ROOT/artifacts/desktop_app/AppIcon.iconset"
/usr/bin/iconutil -c icns "$ROOT/artifacts/desktop_app/AppIcon.iconset" \
  -o "$OUTPUT/Contents/Resources/AppIcon.icns"
"$ROOT/.venv312/bin/python" - "$OUTPUT" "$ROOT" <<'PY'
import plistlib,sys
from pathlib import Path
bundle,root=map(Path,sys.argv[1:])
info={'CFBundleExecutable':'A1Monitoring','CFBundleIdentifier':'ru.a1auto.monitoring.launcher',
      'CFBundleName':'A1 Monitoring','CFBundleDisplayName':'A1 Monitoring',
      'CFBundlePackageType':'APPL','CFBundleShortVersionString':'1.0', 'CFBundleVersion':'1',
      'CFBundleIconFile':'AppIcon','NSHighResolutionCapable':True,'LSMinimumSystemVersion':'12.0',
      'MonitoringProjectPath':str(root),
      'MonitoringReportURL':'http://127.0.0.1:18000/api/v1/dashboard/placement-identity'}
(bundle/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
PY
/usr/bin/codesign --force --sign - "$OUTPUT"
/usr/bin/codesign --verify --deep --strict "$OUTPUT"
echo "$OUTPUT"
