# 📹 Home CCTV: Tinker Board NVR

Asus Tinker Board-এ চলা একটা সম্পূর্ণ free আর open-source CCTV system। এতে যা যা আছে:

- **এক screen-এ সব camera live**, delay ১ সেকেন্ডের কম। Phone, PC আর Android app সবখানে চলে।
- যেকোনো camera-র view on/off করা, tap করে বড় করা, SD/HD বদলানো আর fullscreen দেখা।
- **Motion হলে recording**, যতক্ষণ motion থাকে ততক্ষণ চলে। লম্বা motion ৩ মিনিটের টুকরোয় ভাগ হয়।
- **Storage নিজে থেকে বদলায়:**
  - SSD লাগানো থাকলে recording SSD-তে যায়
  - SSD না থাকলে Tinker Board-এর SD card-এ যায় (সর্বোচ্চ ৭GB)
  - দুই অবস্থাতেই **Google Drive-এ upload হয়** (একটা folder-এ, সর্বোচ্চ ১২GB)
- **SSD format হয় না।** আপনার SSD-র আগের file যেমন আছে তেমনই থাকে, recording যায় শুধু `CCTV-Recordings` folder-এ।
- **Admin আর Viewer account।** Viewer শুধু সেই camera-গুলো দেখে যেগুলো admin অনুমতি দেয়।
- **Dashboard থেকেই camera যোগ করা, IP বা password বদলানো, আর connection test** করা যায়। ONVIF দিয়ে camera-র stream path নিজে খুঁজে নেয়।
- **Google Drive connect হয় Google login দিয়ে**, কোনো API key লাগে না।
- **GitHub থেকে auto-update।** নতুন version এলে board নিজে থেকেই নামিয়ে নেয়।
- Camera-র নিজের app (Imou/Meari/V380) আর SD card আগের মতোই চলে।

```
Camera ──RTSP──► go2rtc ──► Dashboard / Android app (live)
                   │
                   └──► Motion ──► SSD বা SD card ──► Google Drive
```

ব্যবহৃত open-source: [go2rtc](https://github.com/AlexxIT/go2rtc) · [Motion](https://motion-project.github.io/) · [rclone](https://rclone.org/) · [Tailscale](https://tailscale.com/)

---

## ১. Camera app-এ যা করবেন

**Imou (Imou Life app)**
- Camera-র নিচের sticker-এর **Safety Code** লিখে রাখুন। এটাই RTSP password।
- Settings-এ ONVIF/RTSP option থাকলে চালু করুন। Video encoding বদলানোর option থাকলে **H.264** দিন।

**Meari (Meari app)**
- Camera → Settings → **Onvif Settings** চালু করুন, তারপর একটা password দিয়ে OK চাপুন।

**V380 (V380 Pro app)**
- Settings → Advanced settings → **Onvif setting** চালু করুন।

**Router-এ:** প্রতিটা camera আর Tinker Board-কে **fixed IP** দিন (DHCP reservation)।

## ২. Tinker Board-এ install

```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/shovon-mandal/home-cctv /opt/cctv-src
sudo /opt/cctv-src/install.sh
```

Install-এর সময় **admin username আর password** চাওয়া হবে। শেষে dashboard-এর link দেখাবে: `http://<tinker-board-IP>:8085`

এরপর থেকে board প্রতি ১০ মিনিট পর পর GitHub-এ নতুন version আছে কিনা দেখবে, থাকলে নিজে থেকেই update করবে। Update-এ সমস্যা হলে আগের চালু version-এ ফিরে যাবে। আপনার setting, camera, user আর recording-এ কখনো হাত পড়ে না।

## ৩. Dashboard থেকে camera যোগ করা

Admin দিয়ে login করে **⚙️ Settings → Cameras**-এ যান:
1. যে camera বসাবেন তার **Edit** চাপুন, অথবা নতুন camera-র জন্য নিচের form ব্যবহার করুন।
2. **Brand** বাছুন, এতে port আর path নিজে বসে যাবে।
3. **IP** আর **Password** দিন।
4. Meari বা অজানা camera হলে **🔍 ONVIF থেকে stream path খুঁজুন** চাপুন।
5. **▶ Test** চাপুন। ✅ দেখালে **চালু**-তে টিক দিয়ে **Save** করুন।

পরে IP বদলাতে চাইলে Edit করে নতুন IP বসিয়ে Save দিলেই হবে। কয়েক সেকেন্ডের মধ্যে নতুন setting চালু হয়ে যাবে।

## ৪. SSD (data মুছবে না)

SSD-টা Tinker Board-এর USB-তে লাগিয়ে:
```bash
sudo cctv-ssd-setup              # USB disk আর partition-এর list দেখাবে
sudo cctv-ssd-setup /dev/sda1    # list-এ যেটা আপনার SSD-র partition
```
- exFAT, NTFS আর ext4, যেকোনো format চলবে। **আপনার আগের file মুছবে না।**
- Recording যাবে `CCTV-Recordings` folder-এ, সর্বোচ্চ ২০০GB। এটা `/etc/cctv/cctv.conf`-এর `ssd_max_gb` দিয়ে বদলানো যায়। ভরে গেলে শুধু ওই folder-এর পুরোনো clip মুছবে।
- **SSD খোলার আগে অবশ্যই এটা চালান:** `sudo cctv-ssd-eject`
- (চাইলে পুরো SSD মুছে ext4 করতে পারেন, তাতে একটু দ্রুত চলে: `sudo cctv-ssd-setup --format /dev/sda`)

## ৫. Google Drive (Google login দিয়ে)

**⚙️ Settings → ☁️ Google Drive → "Google দিয়ে connect"** চাপুন:
1. **"Google দিয়ে login"** চাপুন, আপনার Google account বাছুন, তারপর **Allow** দিন।
2. এরপর একটা "can't be reached / পাওয়া যাচ্ছে না" page আসবে। **এটাই স্বাভাবিক।**
3. ওই page-এর **address bar-এর পুরো link** copy করে dashboard-এ paste করুন, তারপর **Connect শেষ করুন** চাপুন।

শুধু `drive.file` অনুমতি চাওয়া হয়, তাই এই system আপনার Drive-এর অন্য কোনো file দেখতে পায় না। সব clip যায় **CCTV** folder-এ। ১২GB ভরে গেলে সবচেয়ে পুরোনো clip মুছে যায়, আর ৫ দিনের বেশি পুরোনো clip-ও মুছে যায়।

> Free ১৫GB আপনার Gmail আর Photos-এর সাথে ভাগ হয়। Settings-এ দেখাবে কত জায়গা খালি আছে। দরকার হলে `/etc/cctv/cctv.conf`-এ `drive_max_gb` কমিয়ে দিন।

## ৬. বাইরে থেকে দেখা (Real IP লাগবে না)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4          # 100.x.x.x, এটা app-এ "বাইরের ঠিকানা" হিসেবে বসাবেন
```
Phone-এ **Tailscale** app install করে একই account দিয়ে login করুন।

## ৭. Android app

Repo-র **[Releases → Android app (latest)](../../releases/tag/app-latest)** থেকে `HomeCCTV.apk` download করে install করুন। প্রথমবার "Install unknown apps"-এর অনুমতি দিতে হবে।

App প্রথমবার খুললে দুটো ঠিকানা চাইবে:
- 🏠 **বাসার ঠিকানা:** Tinker Board-এর IP (যেমন `192.168.0.10`)
- 🌍 **বাইরের ঠিকানা:** Tailscale IP (`100.x.x.x`)

বাসায় থাকলে app প্রথম ঠিকানা দিয়ে খোলে, বাইরে থাকলে নিজে থেকেই Tailscale-এ চলে যায়। App-এর নতুন version GitHub Actions নিজে থেকেই build করে।

App ছাড়াও চলে: Chrome-এ dashboard খুলে ⋮ → **Add to Home screen** দিলেই হবে।

## ৮. Account (Admin / Viewer)

| | Admin | Viewer |
|---|---|---|
| Live দেখা | সব camera | শুধু অনুমতি পাওয়া camera |
| Recordings | দেখা, download, delete | অনুমতি থাকলে দেখা আর download |
| Camera, user আর Drive setting | ✅ | ❌ |

**⚙️ Settings → Users** থেকে user যোগ বা বাদ দিন। Terminal থেকেও করা যায়:
```bash
sudo cctv-user list
sudo cctv-user add abba --viewer --cams cam1,cam2
sudo cctv-user passwd admin
```

## ৯. দরকারি command

| কাজ | Command |
|---|---|
| সবকিছুর অবস্থা | `cctv-status` |
| এখনই update | `sudo cctv-update` |
| Recording log | `journalctl -u cctv-motion -f` |
| Live stream log | `journalctl -u cctv-go2rtc -f` |
| SSD নিরাপদে খোলা | `sudo cctv-ssd-eject` |
| হাতে config বদলানোর পর | `sudo cctv-apply` |
| Uninstall | `sudo /opt/cctv-src/uninstall.sh` |

## ১০. সমস্যা হলে

| সমস্যা | সমাধান |
|---|---|
| Test-এ "username/password ভুল" | Imou হলে Safety Code দিন, Meari হলে ONVIF page-এর password দিন |
| Test-এ "path ভুল" | ONVIF থেকে খুঁজুন চাপুন |
| "camera পাওয়া যাচ্ছে না" | IP বদলে গেছে কিনা দেখুন, router-এ fixed IP দিন |
| Phone-এ live কালো দেখাচ্ছে | Camera-য় H.264 দিন (H.265 অনেক phone-এ চলে না) |
| CPU বেশি | Camera-র sensitivity মান বাড়ান, অথবা recording "Low (sub)" করুন |
| Drive-এ লাল দেখাচ্ছে | Settings → Google Drive → Test চাপুন, দরকার হলে আবার connect করুন |

## ১১. অন্য জায়গার camera (পরে যোগ করতে চাইলে)

অন্য বাড়ির camera আনতে হলে ওই বাড়ির Wi-Fi-তে সারাক্ষণ চালু থাকে এমন একটা Android phone লাগবে, তাতে **Tailscale** আর **Termux** (F-Droid থেকে) থাকতে হবে:
```bash
pkg install socat
socat TCP-LISTEN:8554,fork,reuseaddr TCP:<camera-IP>:554 &
```
তারপর dashboard-এ নতুন camera যোগ করুন: IP = phone-এর Tailscale IP, port = `8554`, recording quality = **Low (sub)**।

---
**Repo-তে কোনো ব্যক্তিগত তথ্য নেই।** Camera-র IP, password, user account, Drive login আর recording সব থাকে শুধু Tinker Board-এর `/etc/cctv` আর `/srv/cctv`-এ।
