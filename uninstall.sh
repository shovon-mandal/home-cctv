#!/bin/bash
# Remove the CCTV system. Recordings and /etc/cctv are kept unless --purge.
set -u
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
for u in cctv-power cctv-power.timer cctv-go2rtc cctv-motion cctv-web cctv-storage.timer cctv-upload.timer cctv-drive-clean.timer cctv-watchdog.timer cctv-merge.timer cctv-apply.path cctv-update.timer cctv-update.path cctv-ssd-eject.path cctv-ssd-attach.path; do
  systemctl disable --now "$u" 2>/dev/null
done
systemctl disable --now cctv-recorder 2>/dev/null
rm -f /etc/systemd/system/cctv-*.service /etc/systemd/system/cctv-*.timer /etc/systemd/system/cctv-*.path /etc/tmpfiles.d/cctv.conf
systemctl daemon-reload
for f in /opt/cctv/bin/cctv-*; do rm -f "/usr/local/bin/$(basename "$f")"; done
umount -l /mnt/cctv-ssd 2>/dev/null
rm -rf /opt/cctv /run/cctv
if [ "${1:-}" = "--purge" ]; then
  rm -rf /etc/cctv /srv/cctv
  userdel cctv 2>/dev/null
  echo "Everything removed (SSD contents untouched)."
else
  echo "Removed. Config (/etc/cctv) and recordings (/srv/cctv) kept. Use --purge to delete them."
fi
