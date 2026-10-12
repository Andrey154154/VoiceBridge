#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -ne 0 ]] || { echo 'Run without sudo: bash check.sh' >&2; exit 1; }
PY=/opt/voice-bridge/runtime/bin/python
[[ -x "$PY" ]] || { echo 'First run: bash install.sh' >&2; exit 1; }
export PYTHONPATH=/opt/voice-bridge/app/src PYTHONNOUSERSITE=1
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/voice-bridge/config.toml"
exec "$PY" /opt/voice-bridge/preflight.py --devices --config "$CFG" "$@"
