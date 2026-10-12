#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -ne 0 ]] || { echo 'Run without sudo: bash repair-input.sh' >&2; exit 1; }
MATCH="$(python3 - "${XDG_CONFIG_HOME:-$HOME/.config}/voice-bridge/config.toml" <<'PY'
from pathlib import Path
import sys,tomllib
p=Path(sys.argv[1]); print(tomllib.loads(p.read_text()).get('trigger',{}).get('evdev_device_match','') if p.exists() else '')
PY
)"
sudo /opt/voice-bridge/setup-input-access.sh "$(id -un)" "$MATCH"
echo 'Input access updated. Reopen Voice Bridge; do not run it with sudo.'
