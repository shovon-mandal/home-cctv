# 📹 Home CCTV: a self-hosted NVR for the Asus Tinker Board

A complete, free and open-source CCTV system for cheap Wi-Fi cameras (Imou, Meari, V380, Tapo, …).
English by default, with a one-tap **বাংলা** switch everywhere (dashboard, viewer accounts, Android app).
**বাংলা setup guide:** [SETUP-BN.md](SETUP-BN.md)

## Features

- **Live view** of every camera on one screen (phone, PC, Android app), under 1 s delay. Show/hide any camera, tap to enlarge, SD/HD switch, fullscreen.
- **Sound**: 🔊 per camera in live view; recordings keep the sound too.
- **PTZ**: hold the arrows to pan and tilt the camera (ONVIF), for example on the Imou Ranger 2.
- **24/7 recording** in 1-minute segments (stream copied, no re-encoding, very low CPU).
- **Motion detection** with a red "Motion" badge in live view, motion events with snapshots, and an "only when motion" recording mode.
- **Timeline**: a 24-hour bar per day where recorded time is grey and motion is red. You get thumbnails for every minute and per-hour navigation; tap anywhere to play, and playback continues across minutes.
- **Storage**:
  - SSD plugged into USB → it is used **automatically**: a `CCTV` folder is created on it and recordings go there. Nothing is formatted, and your files stay.
  - No SSD → recordings go to a capped buffer on the SD card (7 GB).
  - When space runs low, minutes **without** motion are deleted first.
- **Google Drive**: every minute that contains motion (even a tiny one) is uploaded, together with its snapshot, into one folder (12 GB cap). Everything else stays local. You connect with your Google login; no API keys.
- **Accounts**: admins can do everything. Viewers only see the cameras you tick, and you decide whether they can open recordings and move PTZ cameras.
- **Camera manager**: add cameras, change IP or password, test the connection, and find stream paths automatically via ONVIF.
- **Access from anywhere**: a public https link through Tailscale Funnel. No real IP and no VPN app on the phone.
- **Auto-update from GitHub**, with automatic rollback if an update fails.
- The cameras' own apps and SD cards keep working as before.

```
camera ──RTSP──► go2rtc ──► dashboard / app (live, sound)
                   ├──► ffmpeg recorder ──► 1-min MP4 + thumbnail ──► SSD or SD card
                   └──► Motion (sub stream) ──► motion events + snapshot
                                                     └──► minutes with motion ──► Google Drive
```

Built with [go2rtc](https://github.com/AlexxIT/go2rtc), [FFmpeg](https://ffmpeg.org), [Motion](https://motion-project.github.io/), [rclone](https://rclone.org/) and [Tailscale](https://tailscale.com/).

## 1. Camera apps

| Camera | What to do |
|---|---|
| **Imou** (Imou Life) | Username `admin`, password = the **Safety Code** on the sticker. Turn on ONVIF/RTSP if the setting exists and **turn off "Video Encryption"**. Prefer H.264. |
| **Meari** (Meari app) | Settings → **Onvif Settings** → on, set a password. |
| **V380** (V380 Pro) | Settings → Advanced → **Onvif setting** → on. |

Give every camera and the Tinker Board a **fixed IP** in your router (DHCP reservation).

## 2. Install on the Tinker Board

```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/shovon-mandal/home-cctv /opt/cctv-src
sudo /opt/cctv-src/install.sh
```
The installer asks for your time zone (recordings are named by local time) and for the **admin username and password**. At the end it prints the dashboard address, `http://<board-ip>:8085`.

## 3. Add cameras (dashboard)

Log in as admin → **⚙️ Settings → Cameras**:
1. Pick the **brand**: port, user and paths are filled in for you.
2. Enter the **IP** and **password**. For Meari or unknown cameras, press **🔍 Find stream paths via ONVIF**.
3. Choose the recording mode: **24/7**, **only when motion**, or **off**. Then tick **Sound** and **Pan/tilt (PTZ)** if the camera has them.
4. Press **▶ Test**. When it shows ✅, tick **Enabled** and press **Save**. The change applies within seconds.

## 4. SSD (automatic, keeps your files)

Just plug the SSD into a USB port. Within a minute it is mounted, a **`CCTV`** folder is created on it and recording moves there. Clips already on the SD card are moved over too. Without the SSD, recording continues on the SD card.
- Works with exFAT, NTFS and ext4. Nothing is formatted and your other files are never touched.
- **Before unplugging, run** `sudo cctv-ssd-eject`.
- `ssd_max_gb` in `/etc/cctv/cctv.conf` limits the CCTV folder (default 200 GB).
- With several USB disks plugged in, the largest is used; pin a specific one with `sudo cctv-ssd-setup /dev/sdXN`.

## 5. Google Drive

**⚙️ Settings → Google Drive → Connect with Google** → log in and press **Allow**. A "can't be reached" page opens next, which is expected. Copy the whole link from the address bar, paste it, then press **Finish connecting**. Only the `drive.file` permission is requested, so the system can't see any of your other Drive files.

## 6. Access from anywhere

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up   # if not installed
sudo cctv-funnel          # prints https://<board>.<tailnet>.ts.net:8443
```
Use strong passwords (10+ characters). After 5 wrong attempts, login is blocked for 10 minutes for that address.

## 7. Android app

Download **[HomeCCTV.apk](https://github.com/shovon-mandal/home-cctv/releases/download/app-latest/HomeCCTV.apk)** and install it. Enter the home address (board IP) and the outside address (the `https://….ts.net:8443` link). The app picks whichever works and has an English/বাংলা switch. GitHub Actions builds a new APK on every change.

## 8. Accounts

**⚙️ Settings → Users**, or from the terminal:
```bash
sudo cctv-user list
sudo cctv-user add abba --viewer --cams cam1,cam2 --ptz
sudo cctv-user passwd admin
```

## 9. Useful commands

| Task | Command |
|---|---|
| Health check (services, recording, storage, Drive) | `cctv-status` |
| Update now | `sudo cctv-update` |
| Recorder log | `journalctl -u cctv-recorder -n 50` |
| Motion log | `journalctl -u cctv-motion -n 50` |
| Internet link on/off | `sudo cctv-funnel` / `sudo cctv-funnel off` |
| After editing config by hand | `sudo cctv-apply` |

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| "Wrong username/password" | Imou: Safety Code. Meari: the ONVIF password. |
| "Camera answers but the video can't be read" | Imou: turn off **Video Encryption** in the app. |
| A camera shows "⚠ not recording" | `cctv-status` and `journalctl -u cctv-recorder -n 50` |
| Live view black on a phone | Set the camera to H.264 (many phones can't decode H.265). |
| High CPU | Raise the motion sensitivity value, or use "Low (sub)" recording. |

## Security

- Live streams, the go2rtc admin API and the cameras' RTSP passwords never leave the board: go2rtc only listens on `127.0.0.1`.
- Login: passwords are stored as PBKDF2 hashes, with a minimum of 8 characters. Sessions are signed, HttpOnly, SameSite=Strict, and also Secure over https. Changing a password or deleting a user signs out its sessions.
- Brute force: 5 wrong passwords block that address for 10 minutes. 10 wrong passwords from the internet lock the account from the internet for 15 minutes, but it still works at home.
- Requests from other websites are refused (Origin check, CSRF header), and the page can't be framed.
- Viewers are limited on the server, not just hidden in the page: their cameras, recordings and PTZ permissions are all checked there.
- Google Drive uses the `drive.file` scope, so the system can only see the files it created itself.
- **Your part:** keep GitHub 2FA on (updates come from this repo), use strong passwords, and keep cameras on a Wi-Fi that guests don't use.

## Files

| Path | What |
|---|---|
| `/etc/cctv/cctv.conf` | general settings |
| `/etc/cctv/cameras.conf` | cameras (managed by the dashboard) |
| `/srv/cctv/rec` → SSD or SD buffer | `camN/YYYY-MM-DD/HH-MM-SS.mp4` + `.jpg`, `events.jsonl` |

No private data is in this repository. Camera IPs and passwords, accounts, the Drive login and recordings only live on the board.
