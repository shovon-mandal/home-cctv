# 📹 Home CCTV

A free, self-hosted CCTV system (NVR) for **any IP camera**: Hikvision, Dahua, Imou, EZVIZ,
Reolink, TP-Link Tapo, Uniview, Axis, Meari, V380, Yoosee, ICSee and anything else with RTSP or
ONVIF. Live view in the browser and on Android, 24/7 + motion recording with a timeline,
motion clips to Google Drive, and user accounts for the family.

Runs on **Windows, macOS and Linux** with Docker, or natively on any Debian-based Linux, including
small boards like the Raspberry Pi, Asus Tinker Board or Orange Pi. No cloud account, no
subscription, and your cameras' own apps keep working.

```
camera ──RTSP──► go2rtc ──► dashboard / Android app (live view with sound)
                   ├──► FFmpeg recorder ──► 1-minute MP4 files + thumbnails ──► disk / SSD
                   └──► Motion (low-res stream) ──► motion events + snapshots
                                                         └──► motion clips ──► Google Drive
```

Built on [go2rtc](https://github.com/AlexxIT/go2rtc), [FFmpeg](https://ffmpeg.org),
[Motion](https://motion-project.github.io/), [rclone](https://rclone.org/) and (optionally)
[Tailscale](https://tailscale.com/).

## Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Install with Docker (Windows, macOS, Linux)](#install-with-docker-windows-macos-linux)
4. [Install natively on Linux / Raspberry Pi](#install-natively-on-linux--raspberry-pi)
5. [Add your cameras](#add-your-cameras)
6. [Recording and storage](#recording-and-storage)
7. [Google Drive backup](#google-drive-backup)
8. [Access from anywhere](#access-from-anywhere)
9. [Android app](#android-app)
10. [Users](#users)
11. [Updating](#updating)
12. [Commands and troubleshooting](#commands-and-troubleshooting)
13. [Security](#security)
14. [Project layout](#project-layout)

## Features

- **Live view** of all cameras on one screen (PC, phone, Android app) with under 1 s delay. Show or
  hide cameras, tap to enlarge, SD/MAX quality switch, fullscreen, **sound**.
- **Any camera**: brand presets, **network scan**, **ONVIF auto-detect** of stream paths, or paste
  any RTSP/RTSPS/RTMP/HTTP URL. See the [camera guide](docs/CAMERAS.md).
- **Smart recording**: 24/7 in low quality plus full quality around every motion, in 1-minute
  files (stream copy, no re-encoding, very low CPU). Or 24/7 full quality, motion-only, or live-only.
- **Motion detection** with adjustable sensitivity, a live "Motion" badge, events with snapshots.
- **Timeline**: a 24-hour bar per day (recorded / full quality / motion), thumbnails, zoom,
  jump between motion events, playback across minutes at up to 16× speed.
- **PTZ**: pan and tilt ONVIF cameras from the live view. **Picture settings** (HDR/WDR,
  brightness, color, resolution, bitrate) over ONVIF.
- **Storage that cleans itself**: size limits, oldest minutes *without* motion are deleted first.
  On Linux a USB SSD is used automatically when plugged in (nothing is formatted).
- **Google Drive**: every minute with motion is uploaded (optional), with a size and age limit.
- **Accounts**: admins and viewers; viewers only see the cameras you allow, with or without
  recordings and PTZ. One-tap "share access" text.
- **Access from anywhere** through Tailscale (no public IP, no port forwarding).
- **Self-healing**: services restart themselves, and a watchdog reconnects frozen camera streams.
- English, with an optional Bangla language pack (add more in `web/lang/`).

## Requirements

| | Docker (Windows / macOS / Linux) | Native Linux |
|---|---|---|
| Software | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows 10/11 with WSL 2, macOS) or Docker Engine + Compose plugin (Linux) | Debian 11+, Ubuntu 22.04+, Raspberry Pi OS, Armbian (`install.sh` installs the rest) |
| CPU | any 64-bit x86 or ARM | x86-64, ARM64 or ARMv7 (Raspberry Pi 3/4/5, Tinker Board, …) |
| RAM | 1 GB free | 1 GB (2 GB for 4+ cameras) |
| Disk | a few GB per camera per day for 24/7 recording (much less with "smart"/"motion") | same; SD card or USB SSD |

No Python packages are needed: everything else is listed in [requirements.txt](requirements.txt)
and installed for you.

Before you start, give every camera (and the computer running Home CCTV) a **fixed IP address** in
your router (DHCP reservation), so the addresses never change.

## Install with Docker (Windows, macOS, Linux)

1. Install **Docker Desktop** (Windows/macOS) or Docker Engine (Linux) and start it.
2. Get the code (with [Git](https://git-scm.com/downloads), or download the ZIP from GitHub and unpack it):
   ```bash
   git clone https://github.com/shovon-mandal/home-cctv
   cd home-cctv
   ```
3. Create your settings file and set your time zone (recordings are named by local time):
   ```bash
   cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
   ```
   Open `.env` in a text editor and set `TZ=` (e.g. `Europe/London`, `America/New_York`,
   `Asia/Kolkata`). You can also choose where recordings are saved (`CCTV_RECORDINGS_DIR`).
4. Start it:
   ```bash
   docker compose up -d --build
   ```
   The first build downloads everything and takes a few minutes.
5. Open **http://localhost:8085** and create the admin account. From a phone on the same Wi-Fi,
   open `http://<this-computer's-IP>:8085`. (Windows may ask to allow Docker through the firewall:
   allow it for private networks.)

Settings, accounts and the Drive login are kept in `data/config`, recordings in `data/recordings`
(or the folders you set in `.env`). They survive updates and container restarts.

```bash
docker compose logs -f              # what is happening
docker compose exec cctv cctv-status   # health check
docker compose restart              # restart
docker compose down                 # stop
```

> On Windows and macOS Docker runs inside a small virtual machine. The dashboard's **network scan**
> then needs you to type your home network (e.g. `192.168.1`); adding cameras by IP works as usual.

## Install natively on Linux / Raspberry Pi

Best for a small always-on board. Installs systemd services, auto-updates from GitHub and can use
a USB SSD automatically.

```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/shovon-mandal/home-cctv /opt/cctv-src
sudo /opt/cctv-src/install.sh
```

The installer asks for your time zone and the admin account, then prints the dashboard address,
`http://<board-ip>:8085`.

(You can also use Docker on a Raspberry Pi; it works the same, without the SSD auto-mount.)

## Add your cameras

Log in as admin → **⚙️ Settings → Cameras**:

1. *(optional)* **🔎 Scan my network for cameras** and tap your camera to fill in its IP.
2. Pick the **brand**: port, username and paths are filled in, and a hint says which password to use.
   Unknown brand? Pick **Any ONVIF camera** and press **🔍 Find stream paths via ONVIF**.
   Have a URL already? Pick **Full stream URL** and paste it.
3. Press **▶ Test**. When it shows ✅, choose the recording mode and press **Save**.
   The change is live within seconds.

**Brand-by-brand instructions** (where to find the password, how to turn on RTSP, stream paths)
are in the **[camera guide](docs/CAMERAS.md)**.

## Recording and storage

Recording modes per camera:

| Mode | What is recorded |
|---|---|
| **Smart** (default) | 24/7 from the low-quality stream, plus full quality for every minute with motion |
| 24/7 full quality | everything in full quality (uses the most space) |
| Only when motion | full quality, only minutes with motion |
| Off | live view only |

Files are plain MP4s: `<recordings>/<camera name>/<YYYY-MM-DD>/HH-MM-SS.mp4` (+ `.jpg` thumbnail,
`low/` for the low-quality copy, `events.jsonl` for motion events). When the size limit
(**Settings → Storage limits**) is reached, the oldest minutes without motion are deleted first.

**Linux only, USB SSD:** plug it in and within a minute a `CCTV` folder is created on it and
recording moves there (exFAT, NTFS and ext4 work; nothing is formatted, other files are never
touched). Before unplugging, tap the storage pill → *Eject SSD* (or `sudo cctv-ssd-eject`).

## Google Drive backup

**⚙️ Settings → Google Drive → Connect with Google**, log in and press **Allow**. A "can't be
reached" page opens next, which is expected: copy the whole link from the address bar, paste it,
and press **Finish connecting**. Only the `drive.file` permission is requested, so the system
can't see any other files in your Drive. Every minute with motion is uploaded to the `CCTV`
folder; old clips are removed after the days / size you set.

## Access from anywhere

The safest way is [Tailscale](https://tailscale.com/) (free for personal use):

- **Native Linux:** `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`, then
  `sudo cctv-funnel` prints a public `https://<name>.<tailnet>.ts.net:8443` link that works on
  any phone, without an app or a public IP. (`sudo cctv-funnel off` turns it off.)
- **Docker:** install Tailscale on the computer itself, then either open
  `http://<tailscale-ip>:8085` from devices that have Tailscale, or publish it with
  `tailscale funnel --bg --https=8443 http://127.0.0.1:8085`. Put the resulting `https://…` link into
  `data/config/public_url` so the dashboard can share it.

Never forward port 8085 on your router directly to the internet.

## Android app

Download **[HomeCCTV.apk](https://github.com/shovon-mandal/home-cctv/releases/download/app-latest/HomeCCTV.apk)**
and install it. Enter the home address (`192.168.x.x`) and, optionally, the outside address
(the `https://…` link). The app uses whichever works. GitHub Actions builds a new APK on every
change. (In a fork, set `app_url` in `cctv.conf` to your own release link.)

## Users

**⚙️ Settings → Users**: admins can change everything; viewers see only the cameras you tick, and
you decide whether they can open recordings and move PTZ cameras. *Share access* copies the
addresses and username for WhatsApp/SMS (never the password).

From the command line (Docker: prefix with `docker compose exec cctv`):

```bash
sudo cctv-user list
sudo cctv-user add mom --viewer --cams cam1,cam2
sudo cctv-user passwd admin
```

## Updating

- **Native Linux:** automatic every 10 minutes from GitHub (with rollback if an update fails), or
  the **🔄 Update** button in the admin menu, or `sudo cctv-update`.
- **Docker:** `git pull && docker compose up -d --build`

## Commands and troubleshooting

| Task | Native Linux | Docker |
|---|---|---|
| Health check (services, network, cameras, storage, Drive) | `sudo cctv-status` | `docker compose exec cctv cctv-status` |
| Logs | `journalctl -u cctv-recorder -n 50` (also `cctv-go2rtc`, `cctv-motion`, `cctv-web`) | `docker compose logs --tail 200` |
| Apply hand-edited config | `sudo cctv-apply` | `docker compose restart` |

| Problem | Fix |
|---|---|
| Test: wrong username/password, wrong path, port closed | See [common problems](docs/CAMERAS.md#common-problems) in the camera guide. |
| A camera shows "⚠ not recording" | Run the health check; check the camera's IP hasn't changed. |
| Live view black on some browsers | The camera sends H.265: the tile falls back to SD by itself. Set H.264 on the camera for MAX everywhere. |
| Live view stops after a while | Usually weak Wi-Fi to the camera. The watchdog reconnects frozen streams every 2 minutes; a cable or a closer access point fixes the cause. |
| High CPU | Use "Smart" or "Only when motion", lower the motion sensitivity, or record the low-quality stream. |
| Can't open the dashboard from a phone | Use the computer's IP (not `localhost`), same Wi-Fi, and allow the port in the computer's firewall. |

## Security

- Camera passwords and the go2rtc admin API never leave the server: go2rtc listens on `127.0.0.1` only.
- Passwords are stored as PBKDF2 hashes (min. 8 characters). Sessions are signed, HttpOnly,
  SameSite=Strict, and Secure over https. Changing a password signs out that user's sessions.
- 5 wrong passwords block that address for 10 minutes; 10 wrong passwords from the internet lock
  the account from the internet for 15 minutes (it still works at home).
- Requests from other websites are refused (Origin + CSRF header check) and the page can't be framed.
- Viewer limits are enforced on the server, not just hidden in the page.
- The first admin account can only be created from your home network, and only while no account exists.
- Google Drive uses the `drive.file` scope: only files this system created are visible to it.
- Keep cameras on a network that guests don't use, use strong passwords, and keep GitHub 2FA on if
  you run the auto-updater from your own fork.

No personal data is stored in this repository: camera addresses and passwords, accounts, the
Drive login and recordings only live on your computer.

## Project layout

| Path | What |
|---|---|
| `bin/` | services and tools: `cctv-recorder`, `cctv-apply`, `cctv-storage`, `cctv-upload`, `cctv-watchdog`, `cctv-supervisor` (Docker), `cctv-status`, … |
| `lib/` | shared Python modules (config, recordings, ONVIF, accounts, network scan) |
| `web/` | dashboard: `server.py` (stdlib HTTP server) and `index.html`; translations in `web/lang/` |
| `systemd/` | service units for the native Linux install |
| `android/` | the Android app (a WebView wrapper with home/outside address switching) |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | Docker install |
| `install.sh`, `uninstall.sh` | native Linux install |
| `cctv.conf.example`, `cameras.conf.example` | settings templates (`/etc/cctv/` or `data/config/`) |
| `docs/CAMERAS.md` | camera guide |

Contributions are welcome: brand presets live in `lib/cctvconf.py` (`PRESETS`), and a new
dashboard language is one file in `web/lang/`.

## License

[MIT](LICENSE) © 2026 Shovon Mandal. The tools it runs (go2rtc, FFmpeg, Motion, rclone) are
downloaded separately at install time and keep their own licenses.
