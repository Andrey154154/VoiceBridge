#!/usr/bin/env bash
set -euo pipefail
TARGET_USER="${1:-${SUDO_USER:-}}"
DEVICE_MATCH="${2:-}"
[[ $EUID -eq 0 ]] || { echo 'Run input-access setup with sudo.' >&2; exit 1; }
[[ -n "$TARGET_USER" && "$TARGET_USER" != root ]] || { echo 'Specify the desktop username, not root.' >&2; exit 2; }
id "$TARGET_USER" >/dev/null
command -v setfacl >/dev/null || { echo 'Install the acl package.' >&2; exit 1; }
modprobe uinput
[[ -c /dev/uinput ]] || { echo '/dev/uinput is missing.' >&2; exit 1; }
# These rules run before seat-late, where logind applies uaccess ACLs.
# No world-writable devices and no new membership in the broad input group.
rm -f /etc/udev/rules.d/80-voice-bridge-uinput.rules
cat >/etc/udev/rules.d/70-voice-bridge-input.rules <<'RULE'
KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", TAG+="uaccess", OPTIONS+="static_node=uinput"
KERNEL=="event*", SUBSYSTEM=="input", ENV{ID_INPUT_MOUSE}=="1", TAG+="uaccess"
KERNEL=="event*", SUBSYSTEM=="input", ATTRS{name}=="Voice Bridge Paste Keyboard", TAG+="uaccess"
RULE
printf 'uinput\n' >/etc/modules-load.d/voice-bridge-uinput.conf
udevadm control --reload-rules
udevadm trigger --subsystem-match=misc --sysname-match=uinput
udevadm trigger --subsystem-match=input
udevadm settle --timeout=5
setfacl -m "u:$TARGET_USER:rw" /dev/uinput
# Apply to the selected physical mouse now, so a logout isn't a hidden
# prerequisite of this upgrade. Do not grant read access to every keyboard.
python3 - "$TARGET_USER" "$DEVICE_MATCH" <<'PY'
import subprocess, sys
from pathlib import Path
user, match = sys.argv[1], sys.argv[2].lower()
count = 0
for entry in Path('/sys/class/input').glob('event*'):
    try:
        name=(entry/'device/name').read_text().strip()
    except OSError:
        continue
    if name.lower().startswith(('voice bridge','voice input bridge','ydotool')):
        continue
    node=Path('/dev/input')/entry.name
    info=subprocess.run(['udevadm','info','--query=property','--path',str(entry)],
                        capture_output=True,text=True,timeout=3,check=False)
    properties=dict(line.split('=',1) for line in info.stdout.splitlines() if '=' in line)
    is_mouse=properties.get('ID_INPUT_MOUSE') == '1'
    selected=is_mouse and ((match in name.lower() or match == str(node).lower()) if match else True)
    if selected and node.is_char_device():
        subprocess.run(['setfacl','-m',f'u:{user}:r',str(node)],check=True)
        print(f'Input access: {node} — {name}')
        count += 1
print(f'Current-session mouse devices configured: {count}')
if not count:
    print('No matching physical mouse found. Select the correct device in Voice Bridge.')
PY
printf '\nInput access configured for %s. The desktop app must run WITHOUT sudo.\n' "$TARGET_USER"
