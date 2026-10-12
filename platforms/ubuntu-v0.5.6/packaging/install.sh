#!/usr/bin/env bash
# Install the application, not a replacement model/runtime on every update.
set -Eeuo pipefail
umask 077
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION=0.5.6
APP=/opt/voice-bridge
RUNTIME="$APP/runtime"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/voice-bridge"
CFG="$CFG_DIR/config.toml"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/voice-bridge"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/voice-bridge.desktop"
PROFILE=keep
REPLACE=0
while (($#)); do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || { echo 'Missing profile'; exit 2; }; PROFILE="$2"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    -h|--help) echo 'bash install.sh [--replace] [--profile auto|cuda|cpu|diagnostics]'; echo 'Default: preserve existing dependencies; auto for a new runtime.'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$PROFILE" in keep|auto|cuda|cpu|diagnostics) ;; *) echo 'Unknown profile' >&2; exit 2 ;; esac
[[ $EUID -ne 0 ]] || { echo 'Запустите bash install.sh БЕЗ sudo. Системные шаги сами запросят права.' >&2; exit 1; }
[[ $(uname -s) == Linux && $(dpkg --print-architecture) == amd64 ]] || { echo 'Нужна Ubuntu/Debian amd64.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python >= 3.11 required"'
DEB="$HERE/VoiceBridge-Ubuntu-$VERSION-amd64.deb"
WHEEL="$HERE/voice_input_bridge-$VERSION-py3-none-any.whl"
[[ -f "$DEB" && -f "$WHEEL" && -f "$HERE/SHA256SUMS" ]] || { echo 'Распакуйте весь архив, не только install.sh.' >&2; exit 2; }
(cd "$HERE"; sha256sum --quiet -c SHA256SUMS)
mkdir -p "$CFG_DIR" "$DATA/install-snapshots"
exec 7>>"$DATA/installer.lock"
flock -n 7 || { echo 'Другая установка уже выполняется.' >&2; exit 1; }
python3 "$HERE/stop_old.py"
exec 8>>"$DATA/desktop.lock"; exec 9>>"$DATA/engine.lock"
flock -w 5 8 && flock -w 5 9 || { echo 'Закройте Voice Bridge и CLI перед обновлением.' >&2; exit 1; }
# Locks remain held until exit; a newly opened GUI cannot race file replacement.
OLD="$(dpkg-query -W -f='${Version}' voice-bridge 2>/dev/null || true)"
old_package() {
  if [[ "$1" == "$VERSION" ]]; then printf '%s' "$HERE"; else printf '%s' "$HERE/previous"; fi
}
if [[ -n "$OLD" ]]; then
  case "$OLD" in 0.3.3|0.4.0|0.5.5|0.5.6) ;; *) echo "Неизвестная установленная версия $OLD. Автоматический откат для неё отсутствует; установка не изменена." >&2; exit 1 ;; esac
  PREV_DIR="$(old_package "$OLD")"
  [[ -f "$PREV_DIR/VoiceBridge-Ubuntu-$OLD-amd64.deb" && -f "$PREV_DIR/voice_input_bridge-$OLD-py3-none-any.whl" ]] || { echo 'Не найден комплект отката; установка не изменена.' >&2; exit 2; }
fi
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
BACKUP="$DATA/install-snapshots/$STAMP"
mkdir -p "$BACKUP"
printf '%s\n' "$OLD" > "$BACKUP/previous-version"
[[ ! -f "$CFG" ]] || cp -p "$CFG" "$BACKUP/config.toml"
[[ ! -f "$AUTOSTART" ]] || cp -p "$AUTOSTART" "$BACKUP/autostart.desktop"
TOUCHED=0
recover() {
  local rc=$?
  trap - ERR
  echo "ОШИБКА установки (код $rc). Резервная копия: $BACKUP" >&2
  if [[ "$TOUCHED" == 1 && -n "$OLD" ]]; then
    local dir; dir="$(old_package "$OLD")"
    echo "Восстановление файлов приложения $OLD (модели/AI-зависимости не удаляются)…" >&2
    if sudo dpkg -i "$dir/VoiceBridge-Ubuntu-$OLD-amd64.deb" && sudo "$RUNTIME/bin/python" -m pip install --no-deps --no-index --force-reinstall "$dir/voice_input_bridge-$OLD-py3-none-any.whl"; then
      if [[ -f "$BACKUP/config.toml" ]]; then cp -p "$BACKUP/config.toml" "$CFG"; else rm -f "$CFG"; fi
      if [[ -f "$BACKUP/autostart.desktop" ]]; then mkdir -p "$(dirname "$AUTOSTART")"; cp -p "$BACKUP/autostart.desktop" "$AUTOSTART"; else rm -f "$AUTOSTART"; fi
      echo "Приложение $OLD восстановлено. Явно установленные через --profile библиотеки и системные права не откатываются." >&2
    else
      echo 'ОШИБКА ОТКАТА. Не запускайте приложение до восстановления; сохраните этот журнал.' >&2
    fi
  else
    echo 'Незавершённая новая установка оставлена для повторного запуска. Успешная установка НЕ заявляется.' >&2
  fi
  exit "$rc"
}
trap recover ERR
printf '\nVoice Bridge %s — Ubuntu\n' "$VERSION"
# Backups above are private. Root-owned runtime files must remain user-readable.
umask 022
TOUCHED=1
if [[ "$REPLACE" == 1 && -n "$OLD" ]]; then
  echo "Удаляю пакет приложения $OLD. Runtime, настройки и модель сохраняются."
  # apt remove deletes dpkg-owned application files, not the managed venv or
  # user data. Never purge, autoremove, rm -rf /opt or touch model directories.
  sudo apt-get remove -y voice-bridge
fi
sudo apt-get install -y --no-install-recommends "$DEB"
if [[ "$PROFILE" == keep && -f "$RUNTIME/bootstrap.pending" ]]; then
  PROFILE="$(cat "$RUNTIME/bootstrap.pending")"
  case "$PROFILE" in auto|cpu|cuda|diagnostics) ;; *) echo 'Некорректный профиль незавершённой установки'; false ;; esac
  echo "Продолжаю незавершённую установку зависимостей: $PROFILE"
fi
if [[ -x "$RUNTIME/bin/python" && "$PROFILE" == keep ]]; then
  echo 'Сохраняю существующее окружение: без загрузки Python/ASR/модели.'
  sudo "$RUNTIME/bin/python" -m pip install --no-index --no-deps --force-reinstall "$WHEEL"
else
  [[ "$PROFILE" != keep ]] || PROFILE=auto
  sudo "$APP/bootstrap-runtime.sh" "$PROFILE"
fi
export PYTHONPATH="$APP/app/src" PYTHONNOUSERSITE=1
# Check actual installed imports/UI, not just wheel metadata; model is not loaded.
"$RUNTIME/bin/python" -c 'import voice_bridge; assert voice_bridge.__version__ == "0.5.6", voice_bridge.__file__'
"$RUNTIME/bin/python" "$APP/preflight.py"
"$RUNTIME/bin/python" - "$CFG" <<'PY'
from pathlib import Path
import sys
from voice_bridge.config import load_config
from voice_bridge.desktop.settings_store import ensure_user_config
p = Path(sys.argv[1])
if p.exists():
    load_config(p)  # Read/validate; do not rewrite a working configuration.
    print('Настройки сохранены без изменений:', p)
else:
    ensure_user_config()
    assert p.is_file(), p
    print('Создан новый профиль. Выберите модель и микрофон в приложении:', p)
PY
MOUSE_MATCH="$(python3 - "$CFG" <<'PY'
import sys,tomllib
with open(sys.argv[1],'rb') as f:
    print(tomllib.load(f).get('trigger',{}).get('evdev_device_match',''))
PY
)"
sudo "$APP/setup-input-access.sh" "$(id -un)" "$MOUSE_MATCH"
if [[ -f "$AUTOSTART" ]]; then
  "$RUNTIME/bin/python" -c 'from voice_bridge.desktop.autostart import set_enabled; set_enabled(True)'
fi
# Keep the previous-version rollback across an idempotent reinstall of 0.5.6.
if [[ -n "$OLD" && "$OLD" != "$VERSION" ]]; then
  printf '%s\n' "$BACKUP" > "$DATA/rollback-0.5.6.path.tmp"
  mv "$DATA/rollback-0.5.6.path.tmp" "$DATA/rollback-0.5.6.path"
fi
trap - ERR
printf '\nDONE: VoiceBridge %s\n' "$VERSION"
printf 'Первый запуск: voice-bridge-desktop\n'
printf 'Проверка установки и доступа: bash check.sh\n'
printf 'Проверка вставки без модели: voice-bridge-desktop --diagnostics\n'
printf 'Модель выберите локально либо скачайте на странице «Модели».\n'
printf 'Если устройство подключено заново и доступа нет: bash repair-input.sh\n'
printf 'Проверка реальной диктовки/вставки выполняется в рабочем сеансе, не установщиком.\n'
