#!/bin/bash
# Home CCTV installer for Linux (Debian, Ubuntu, Raspberry Pi OS, Armbian; x86-64 or ARM).
# For Windows and macOS use Docker instead (see README).
#   First time:  sudo git clone https://github.com/<you>/home-cctv /opt/cctv-src
#                sudo /opt/cctv-src/install.sh
#   Updates:     automatic (cctv-update.timer), or: sudo cctv-update
set -euo pipefail
UPDATE=0; [ "${1:-}" = "--update" ] && UPDATE=1
GO2RTC_VERSION=${GO2RTC_VERSION:-v1.9.9}
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo:  sudo ./install.sh"; exit 1; }

case "$(uname -m)" in
  armv7l|armv7*) ARCH=arm ;;
  aarch64|arm64) ARCH=arm64 ;;
  x86_64)        ARCH=amd64 ;;
  armv6l)        ARCH=armv6 ;;
  *) echo "Unsupported CPU: $(uname -m)"; exit 1 ;;
esac
echo "==> CPU: $(uname -m)  (go2rtc build: $ARCH)"

# Recordings are named by local time, so the board should use your time zone.
if [ "$UPDATE" = 0 ]; then
  CUR_TZ=$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
  if [ "$CUR_TZ" = "Etc/UTC" ] || [ "$CUR_TZ" = "UTC" ]; then
    read -r -p "Time zone for recordings [Asia/Dhaka]: " NEW_TZ || true
    timedatectl set-timezone "${NEW_TZ:-Asia/Dhaka}" && echo "Time zone: $(timedatectl show -p Timezone --value)"
  fi
fi

echo "==> Packages"
need_apt=1
if [ "$UPDATE" = 1 ]; then
  need_apt=0
  for c in motion ffprobe rclone python3 curl parted; do command -v "$c" >/dev/null || need_apt=1; done
fi
[ "$need_apt" = 1 ] && apt-get update -qq
[ "$need_apt" = 1 ] && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  motion ffmpeg python3 rclone curl ca-certificates parted e2fsprogs psmisc util-linux >/dev/null
# for using the SSD without formatting (exFAT / NTFS); not fatal if missing
[ "$need_apt" = 1 ] && { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq exfatprogs ntfs-3g >/dev/null 2>&1 || true; }
systemctl disable --now motion 2>/dev/null || true     # we run our own motion service

echo "==> User and folders"
id cctv &>/dev/null || useradd --system --home-dir /srv/cctv --shell /usr/sbin/nologin cctv
install -d -o root -g root -m 755 /opt/cctv /opt/cctv/bin /opt/cctv/lib /opt/cctv/web
install -d -o root -g cctv -m 770 /etc/cctv
install -d -o cctv -g cctv -m 750 /etc/cctv/generated
install -d -o cctv -g cctv -m 700 /etc/cctv/auth
install -d -o cctv -g cctv -m 755 /srv/cctv /srv/cctv/sd-buffer
install -d -m 755 /mnt/cctv-ssd
echo "d /run/cctv 0775 root cctv -" > /etc/tmpfiles.d/cctv.conf
systemd-tmpfiles --create /etc/tmpfiles.d/cctv.conf

echo "==> Files"
install -m 755 "$HERE"/bin/cctv-* /opt/cctv/bin/
install -m 644 "$HERE"/lib/*.py /opt/cctv/lib/
install -d -m 755 /opt/cctv/web/lang
install -m 644 "$HERE"/web/*.* /opt/cctv/web/
install -m 644 "$HERE"/web/lang/* /opt/cctv/web/lang/
for f in /opt/cctv/bin/cctv-*; do ln -sf "$f" /usr/local/bin/; done

if [ ! -x /opt/cctv/bin/go2rtc ]; then
  echo "==> Downloading go2rtc $GO2RTC_VERSION"
  url="https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VERSION/go2rtc_linux_$ARCH"
  if ! curl -fsSL -o /opt/cctv/bin/go2rtc.tmp "$url"; then
    echo "    pinned version failed, trying latest"
    curl -fsSL -o /opt/cctv/bin/go2rtc.tmp \
      "https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_$ARCH"
  fi
  chmod 755 /opt/cctv/bin/go2rtc.tmp && mv /opt/cctv/bin/go2rtc.tmp /opt/cctv/bin/go2rtc
fi
/opt/cctv/bin/go2rtc -version || true

if [ ! -f /etc/cctv/cctv.conf ]; then
  install -o root -g cctv -m 640 "$HERE/cctv.conf.example" /etc/cctv/cctv.conf
  FIRST_INSTALL=1
fi
if [ ! -f /etc/cctv/cameras.conf ]; then
  install -o cctv -g cctv -m 640 "$HERE/cameras.conf.example" /etc/cctv/cameras.conf
fi
chown cctv:cctv /etc/cctv/cameras.conf
[ -f /etc/cctv/rclone.conf ] || install -o cctv -g cctv -m 600 /dev/null /etc/cctv/rclone.conf

echo "==> Services"
install -m 644 "$HERE"/systemd/* /etc/systemd/system/
systemctl daemon-reload
/opt/cctv/bin/cctv-storage || true
/opt/cctv/bin/cctv-apply --no-restart
systemctl enable -q cctv-go2rtc cctv-motion cctv-recorder cctv-web cctv-storage.timer cctv-upload.timer cctv-drive-clean.timer cctv-apply.path cctv-ssd-eject.path cctv-ssd-attach.path cctv-watchdog.timer cctv-merge.timer
systemctl restart cctv-ssd-eject.path cctv-ssd-attach.path
if [ -d "$HERE/.git" ]; then
  [ "$HERE" = /opt/cctv-src ] || ln -sfn "$HERE" /opt/cctv-src
  git config --global --add safe.directory /opt/cctv-src 2>/dev/null || true
  git config --global --add safe.directory "$HERE" 2>/dev/null || true
  systemctl enable -q cctv-update.timer cctv-update.path
  systemctl restart cctv-update.timer cctv-update.path
fi
systemctl restart cctv-storage.timer cctv-upload.timer cctv-drive-clean.timer cctv-apply.path cctv-watchdog.timer cctv-merge.timer
# restarts go2rtc, web, motion and one recorder per camera
/opt/cctv/bin/cctv-apply || true

has_admin() {
  python3 -c 'import json,sys; d=json.load(open("/etc/cctv/auth/users.json")); sys.exit(0 if any(u.get("role")=="admin" for u in d["users"].values()) else 1)' 2>/dev/null
}
if [ "$UPDATE" = 0 ] && ! has_admin; then
  echo
  echo "==> Create the dashboard admin account"
  read -r -p "Admin username [admin]: " ADMIN_NAME
  /opt/cctv/bin/cctv-user add "${ADMIN_NAME:-admin}" --admin || echo "Create it later with: sudo cctv-user add admin --admin"
fi
chown -R cctv:cctv /etc/cctv/auth
chown cctv:cctv /etc/cctv/cameras.conf 2>/dev/null || true

IP=$(hostname -I | awk '{print $1}')
TS=$(command -v tailscale >/dev/null && tailscale ip -4 2>/dev/null | head -1 || true)
echo
echo "============================================================"
echo " Install finished!"
echo " Dashboard (home Wi-Fi):   http://$IP:8085"
[ -n "$TS" ] && echo " Dashboard (Tailscale):     http://$TS:8085"
echo
if [ "${FIRST_INSTALL:-0}" = 1 ]; then
  echo " Next steps:"
  echo "  1) Log in as admin → Settings → Cameras: enter IP/password, Test, Save"
  echo "  2) SSD (keeps your files):  sudo cctv-ssd-setup"
  echo "  3) Google Drive: Settings → Google Drive → Connect with Google"
fi
echo " Health check:  cctv-status"
echo "============================================================"
