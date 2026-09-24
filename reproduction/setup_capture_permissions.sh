#!/usr/bin/env bash
# One-time Desktop setup. Runtime start/stop commands do not need sudo.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo 'Run once with: sudo bash reproduction/setup_capture_permissions.sh <Desktop-user>' >&2
  exit 1
fi
capture_user="${1:-${SUDO_USER:-}}"
if [[ -z "$capture_user" || "$capture_user" == root ]]; then
  echo 'Specify the non-root Desktop account that runs the capture portal.' >&2
  exit 1
fi
capture_group="$(id -g -- "$capture_user")"
[[ "$capture_group" =~ ^[0-9]+$ ]]
for capture_program in /usr/bin/setfacl /usr/bin/udevadm /usr/bin/install; do
  [[ -x "$capture_program" ]] || { echo "Missing: $capture_program" >&2; exit 1; }
done

capture_rules="$(mktemp)"
trap 'rm -f -- "$capture_rules"' EXIT
cat > "$capture_rules" <<EOF
# HIL-SERL capture access for the primary group of $capture_user (GID $capture_group).
# Group ACLs preserve existing owners, modes and desktop-session user ACLs.
# Match the configured exterior USB port and wrist model, not /dev/video numbers.
ACTION=="add|change", SUBSYSTEM=="video4linux", ATTR{index}=="0", ENV{ID_VENDOR_ID}=="2b03", ENV{ID_MODEL_ID}=="f880", ENV{ID_PATH}=="pci-0000:00:14.0-usb-0:6.1:1.0", RUN+="/usr/bin/setfacl -m g:$capture_group:rw /dev/%k"
ACTION=="add|change", SUBSYSTEM=="video4linux", ATTR{index}=="0", ENV{ID_VENDOR_ID}=="2b03", ENV{ID_MODEL_ID}=="f682", ENV{ID_SERIAL}=="Technologies__Inc._ZED-M", RUN+="/usr/bin/setfacl -m g:$capture_group:rw /dev/%k"
ACTION=="add|change", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="256f", ATTRS{idProduct}=="c63a", RUN+="/usr/bin/setfacl -m g:$capture_group:rw /dev/%k"
EOF
capture_target=/etc/udev/rules.d/99-z-hilserl-capture.rules
if [[ -e "$capture_target" ]] && ! cmp -s "$capture_rules" "$capture_target"; then
  cp -a -- "$capture_target" "$capture_target.backup-$(date -u +%Y%m%dT%H%M%S%N)"
fi
/usr/bin/install -o root -g root -m 0644 "$capture_rules" "$capture_target"
/usr/bin/udevadm control --reload-rules

# Apply the rules to these attached devices; no USB reset or robot operation.
for capture_device in \
  /dev/v4l/by-path/pci-0000:00:14.0-usb-0:6.1:1.0-video-index0 \
  /dev/v4l/by-id/usb-Technologies__Inc._ZED-M-video-index0; do
  if [[ -e "$capture_device" ]]; then
    capture_sysfs="$(/usr/bin/udevadm info --query=path --name="$capture_device")"
    /usr/bin/udevadm trigger --action=change "/sys$capture_sysfs"
  fi
done
for capture_hid in /sys/class/hidraw/hidraw*; do
  [[ -r "$capture_hid/device/uevent" ]] || continue
  if /usr/bin/grep -q '^HID_ID=0003:0000256F:0000C63A$' "$capture_hid/device/uevent"; then
    /usr/bin/udevadm trigger --action=change "$capture_hid"
  fi
done
/usr/bin/udevadm settle --timeout=10
echo "Installed persistent capture-device access for $capture_user (group $capture_group)."
echo "Rule: $capture_target"
