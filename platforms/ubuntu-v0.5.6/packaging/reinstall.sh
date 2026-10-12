#!/usr/bin/env bash
# Transactional application replacement. Not a wipe of models/runtime/config.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/install.sh" --replace "$@"
