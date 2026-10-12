#!/usr/bin/env bash
# Restore the pre-0.5.6 application/config snapshot, never remove model weights.
set -Eeuo pipefail
umask 077
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/voice-bridge"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/voice-bridge/config.toml"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/voice-bridge.desktop"
[[ $EUID -ne 0 ]] || { echo 'Запустите bash rollback.sh без sudo.' >&2; exit 1; }
[[ -f "$DATA/rollback-0.5.6.path" ]] || { echo 'Нет снимка предыдущей установки. Откат не выполнялся.' >&2; exit 2; }
BACKUP="$(cat "$DATA/rollback-0.5.6.path")"
# Reject malformed/out-of-scope snapshot paths before reading their contents.
case "$BACKUP" in "$DATA"/install-snapshots/*) ;; *) echo 'Некорректный путь снимка' >&2; exit 2 ;; esac
[[ -f "$BACKUP/previous-version" ]] || { echo 'Снимок отсутствует'; exit 2; }
OLD="$(cat "$BACKUP/previous-version")"
case "$OLD" in 0.3.3|0.4.0|0.5.5) ;; *) echo "Неизвестная версия отката: $OLD" >&2; exit 2 ;; esac
(cd "$HERE"; sha256sum --quiet -c SHA256SUMS)
exec 7>>"$DATA/installer.lock"; flock -n 7 || { echo 'Установка уже выполняется'; exit 1; }
python3 "$HERE/stop_old.py"
exec 8>>"$DATA/desktop.lock"; exec 9>>"$DATA/engine.lock"
flock -w 5 8 && flock -w 5 9 || { echo 'Программа ещё работает'; exit 1; }
cp -p "$CFG" "$CFG.before-rollback-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
trap 'echo "ОШИБКА ОТКАТА. Восстановление не подтверждено, сохраните журнал." >&2' ERR
umask 022
sudo dpkg -i "$HERE/previous/VoiceBridge-Ubuntu-$OLD-amd64.deb"
sudo /opt/voice-bridge/runtime/bin/python -m pip install --no-index --no-deps --force-reinstall "$HERE/previous/voice_input_bridge-$OLD-py3-none-any.whl"
if [[ -f "$BACKUP/config.toml" ]]; then cp -p "$BACKUP/config.toml" "$CFG"; else rm -f "$CFG"; fi
if [[ -f "$BACKUP/autostart.desktop" ]]; then mkdir -p "$(dirname "$AUTOSTART")"; cp -p "$BACKUP/autostart.desktop" "$AUTOSTART"; else rm -f "$AUTOSTART"; fi
PYTHONPATH=/opt/voice-bridge/app/src PYTHONNOUSERSITE=1 /opt/voice-bridge/runtime/bin/python -c "import voice_bridge; assert voice_bridge.__version__ == '$OLD'"
trap - ERR
printf 'RESTORED: VoiceBridge %s\nМодели и Python/AI-библиотеки не удалялись. Системные зависимости и права доступа не откатываются.\n' "$OLD"
