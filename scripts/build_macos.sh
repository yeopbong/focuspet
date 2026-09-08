#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo 'This build target requires macOS arm64.' >&2
  exit 1
fi
export PYINSTALLER_CONFIG_DIR="$PWD/.runtime/pyinstaller"
.venv/bin/python - <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
dirty = subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
if dirty:
    raise SystemExit('Build from a clean, committed checkout so the bundle has a verifiable source revision.')
target = Path('.runtime/build-info.json')
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps({
    'application': 'Focus Pet', 'version': '0.1.0', 'source_commit': commit,
    'built_at_utc': datetime.now(timezone.utc).isoformat(),
    'target': 'macOS 14+ arm64', 'signing': 'ad-hoc; not notarized',
}, indent=2) + '\n')
PY
.venv/bin/python -m PyInstaller --noconfirm --clean FocusPet.spec
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "dist/Focus Pet.app" "dist/Focus-Pet-0.1.0-macos-arm64.zip"
echo 'Built dist/Focus Pet.app and ZIP. Developer ID signing/notarization are not configured.'
