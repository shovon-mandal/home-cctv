# Camera guide: connecting any IP camera

Home CCTV works with any camera that can send a video stream over your network. Almost every
Wi-Fi or PoE camera can do this with **RTSP** and/or **ONVIF**; the brand's own app keeps
working at the same time.

- [The 4 things you need](#the-4-things-you-need)
- [Adding a camera in the dashboard](#adding-a-camera-in-the-dashboard)
- [Brand list: stream URLs and how to turn RTSP on](#brand-list)
- [Testing a URL with VLC](#testing-a-url-with-vlc)
- [Cameras without RTSP](#cameras-without-rtsp)
- [Common problems](#common-problems)

## The 4 things you need

| What | Where to find it |
|---|---|
| **IP address** of the camera | The camera app (device info / network), or your router's list of connected devices. The dashboard can also **scan your network** for you. Give the camera a fixed IP in the router (DHCP reservation) so it never changes. |
| **Username and password** for RTSP/ONVIF | Usually *not* your cloud-app login. Depends on the brand, see the table (e.g. a code on the sticker, or a "camera account" you create in the app). |
| **RTSP port** | Usually `554`. Some brands use `8554`, `10554` or `88`. |
| **Stream path** | The part after the port, e.g. `/Streaming/Channels/101`. Pick the brand in the dashboard and it is filled in, or use **Find stream paths via ONVIF**. |

A full stream URL looks like this:

```
rtsp://USERNAME:PASSWORD@IP:PORT/PATH
rtsp://admin:MyPass123@192.168.1.64:554/Streaming/Channels/101
```

Most cameras have two streams: a **main** stream (full resolution) and a **sub** stream (small).
Home CCTV uses the sub stream for motion detection and the low-quality 24/7 recording, and the
main stream for full-quality recording and the "MAX" live view.

## Adding a camera in the dashboard

**Settings → Cameras**:

1. *(optional)* **🔎 Scan my network for cameras**: lists every device with an open RTSP port or
   an ONVIF answer. Tap one to fill in its IP. (In Docker on Windows/macOS type your home network,
   e.g. `192.168.1`.)
2. **Camera brand**: choose your brand. Port, username and paths are filled in, and a hint tells
   you what the password is. Not listed? Choose **Any ONVIF camera (auto-detect)**.
3. Enter the **IP** and **password**.
4. Press **🔍 Find stream paths via ONVIF** if the brand has no fixed paths (or if Test fails).
   This asks the camera itself for its streams.
5. Press **▶ Test**. Both streams should show ✅ with their resolution.
6. Choose the recording mode, tick **Sound** / **Pan/tilt (PTZ)** if the camera has them, and **Save**.

Already have a working URL (from VLC, your NVR, UniFi Protect, a bridge, …)? Choose
**Full stream URL** and paste it.

## Brand list

Paths below go after `rtsp://user:password@IP:port`. Models and firmware differ, so if one
doesn't work, try the alternatives, ONVIF auto-detect, or search "<your model> RTSP URL".

| Brand | Port | Main stream | Sub stream | Username / password and how to enable |
|---|---|---|---|---|
| **Hikvision**, HiWatch, Annke | 554 | `/Streaming/Channels/101` | `/Streaming/Channels/102` | The password set when the camera was activated. RTSP is on by default. |
| **EZVIZ** | 554 | `/h264/ch1/main/av_stream` | `/h264/ch1/sub/av_stream` | User `admin`, password = the 6-letter **verification code** on the sticker. Many models need RTSP turned on in the EZVIZ app (device settings → *LAN Live View* / *Local Service Settings* → RTSP) and **Image Encryption off**. Alternatives: `/Streaming/Channels/101`, `/h265/ch1/main/av_stream`. |
| **Dahua**, Amcrest, Lorex | 554 | `/cam/realmonitor?channel=1&subtype=0` | `/cam/realmonitor?channel=1&subtype=1` | Web page / app login. |
| **Imou** | 554 | `/cam/realmonitor?channel=1&subtype=0` | `/cam/realmonitor?channel=1&subtype=1` | User `admin`, password = **Safety Code** on the sticker. Turn **Video Encryption off** in the Imou app. |
| **Reolink** | 554 | `/h264Preview_01_main` | `/h264Preview_01_sub` | Turn RTSP on in the app/client (*Network → Advanced → Server/Port settings*). H.265 models: `/h265Preview_01_main`. Battery cameras, E1 and some kit cameras have **no RTSP**. Avoid special characters in the password. |
| **TP-Link Tapo** | 554 | `/stream1` | `/stream2` | Create a **Camera Account** in the Tapo app (*Settings → Advanced Settings → Camera Account*). ONVIF port `2020`. |
| **TP-Link VIGI / Omada** | 554 | `/stream1` | `/stream2` | Web page login. |
| **Uniview (UNV)** | 554 | `/media/video1` | `/media/video2` | Web page login. NVR channels: `/unicast/c1/s0/live`, `/unicast/c1/s1/live`. |
| **Axis** | 554 | `/axis-media/media.amp` | `/axis-media/media.amp?resolution=640x360` | Web page login (often `root`). |
| **Foscam** | 554 (old: 88) | `/videoMain` | `/videoSub` | App/web login. |
| **eufy** (models with RTSP) | 554 | `/live0` | – | In the eufy app: camera *Settings → Storage → NAS (RTSP)*: turn on, set username/password. |
| **Meari**, CloudEdge, ZUMIMALL | 8554 | `/Streaming/Channels/101` | via ONVIF | Turn on **ONVIF** in the app (*Settings → Onvif Settings*) and set its password, then use **Find via ONVIF**. |
| **V380** | 554 | `/live/ch00_0` | `/live/ch00_1` | Turn on ONVIF in the V380 app; password = device password. |
| **Yoosee** | 554 | `/onvif1` | `/onvif2` | Set the RTSP/NVR password in the app. ONVIF port `5000`. Some only stream over UDP: use *Full stream URL* `ffmpeg:rtsp://admin:PASS@IP:554/onvif1`. |
| **ICSee / XMEye** (Xiongmai) | 554 | `/user=admin&password=PASS&channel=1&stream=0.sdp?real_stream` | `…&stream=1.sdp?real_stream` | Default `admin` with empty password (set one!). The dashboard fills `{user}`/`{password}` into the path for you. ONVIF port `8899`. |
| **Vstarcam / Eye4** | 10554 | `/tcp/av0_0` | `/tcp/av0_1` | Device password from the Eye4 app. |
| **Wansview** | 554 | `/live/ch0` | `/live/ch1` | Turn on RTSP in the app and set its password. |
| **UniFi Protect** | 7441 | copy the RTSPS link | – | Protect → camera → Settings → Advanced → RTSP: enable and copy the `rtsps://…` link; paste it as **Full stream URL** (use `rtspx://` instead of `rtsps://` to skip the certificate check). |
| **Any NVR / DVR** | – | – | – | Most NVRs stream each channel; use the NVR brand's paths with the channel number (e.g. Hikvision `/Streaming/Channels/201` = channel 2). |
| **Anything else with ONVIF** | 554 | auto | auto | Choose **Any ONVIF camera**, enter IP/user/password and press **Find via ONVIF**. |

Other URL types work too as **Full stream URL**: `rtsps://`, `rtmp://`, `http://…/video.mjpg`
(MJPEG) and any [go2rtc source](https://github.com/AlexxIT/go2rtc#module-streams).

## Testing a URL with VLC

If the dashboard test fails, try the same URL in [VLC](https://www.videolan.org/) on a PC on the
same network: **Media → Open Network Stream** → paste `rtsp://user:password@IP:554/path`.
If VLC can't play it either, the URL, password or camera setting is wrong. If VLC plays it,
paste exactly that URL as **Full stream URL**.

[ONVIF Device Manager](https://sourceforge.net/projects/onvifdm/) (Windows) and the
*Onvifer* app (Android) can also show a camera's stream URLs.

## Cameras without RTSP

Some cameras only talk to their own cloud: most **Ring, Arlo, Nest, Blink**, many **Xiaomi /
Mi Home** and **Wyze** cameras, and battery cameras in general. They can't be added directly.
Options: firmware or settings that add RTSP (some Wyze models had an official RTSP firmware), or
a bridge that turns the cloud stream into RTSP (for example
[docker-wyze-bridge](https://github.com/mrlt8/docker-wyze-bridge)); then paste the bridge's RTSP
URL as **Full stream URL**.

## Common problems

| The test says | Fix |
|---|---|
| Wrong username/password | Use the RTSP/ONVIF credentials from the table, not your cloud-app login. |
| Wrong path | Pick the brand again or use **Find via ONVIF**; try the alternatives above. |
| Port closed | RTSP is off in the camera app, or the port is different (8554, 10554, 88). |
| Camera answers but the video can't be read | Turn off *video / image / stream encryption* in the camera app (Imou, Dahua, EZVIZ). |
| Camera not reachable | Wrong IP, or the camera is on a different network (guest Wi-Fi, another router). |
| Live view black but Test is OK | The browser can't play H.265: the tile falls back to SD by itself. Set the camera to H.264 if you want MAX in every browser. Recordings are not affected. |
