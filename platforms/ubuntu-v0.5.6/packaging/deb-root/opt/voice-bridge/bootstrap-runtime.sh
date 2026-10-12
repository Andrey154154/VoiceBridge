#!/usr/bin/env bash
set -euo pipefail
umask 022
APP=/opt/voice-bridge
PYTHON_BIN="${PYTHON_BIN:-python3}"
PROFILE="${1:-auto}"
[[ $EUID -eq 0 ]] || { echo 'Runtime installation requires sudo.' >&2; exit 1; }
case "$PROFILE" in auto|cuda|cpu|diagnostics) ;; *) echo 'Profile: auto, cuda, cpu, diagnostics' >&2; exit 2;; esac
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3,11), "Python >= 3.11 required"'
[[ -x "$APP/runtime/bin/python" ]] || "$PYTHON_BIN" -m venv "$APP/runtime"
PY="$APP/runtime/bin/python"
printf "%s\n" "$PROFILE" > "$APP/runtime/bootstrap.pending"
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
# Cache persists across retries; never delete user models or a functioning venv.
export PIP_CACHE_DIR=/var/cache/voice-bridge/pip
mkdir -p "$PIP_CACHE_DIR"
"$PY" -m pip install --index-url https://pypi.org/simple 'pip>=25' setuptools wheel
"$PY" -m pip install --index-url https://pypi.org/simple 'numpy>=2.0' 'sounddevice>=0.5.2' 'PySide6>=6.8,<7' 'evdev>=1.9.0' 'pynput>=1.8.1'
if [[ "$PROFILE" == auto ]]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then PROFILE=cuda; else PROFILE=cpu; fi
fi
if [[ "$PROFILE" != diagnostics ]]; then
  INDEX=https://download.pytorch.org/whl/cpu
  [[ "$PROFILE" != cuda ]] || INDEX=https://download.pytorch.org/whl/cu128
  # This branch is used only for a fresh install or an explicitly chosen profile.
  # Existing keep-mode upgrades never enter this dependency installation.
  "$PY" -m pip install --index-url "$INDEX" 'torch>=2.8'
  "$PY" -m pip install --index-url https://pypi.org/simple 'transformers>=5.13,<6' 'accelerate>=1.12' 'librosa>=0.11,<1'
fi
"$PY" -m pip install --no-index --no-deps --force-reinstall "$APP/wheels/voice_input_bridge-0.5.6-py3-none-any.whl"
rm -f "$APP/runtime/bootstrap.pending"
printf 'Voice Bridge runtime installed, profile=%s. Model weights are selected/downloaded in the app.\n' "$PROFILE"
