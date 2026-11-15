# 📹 Home CCTV: বাংলা setup guide

[English README](README.md)

## যা যা পাবেন
- **Live:** সব camera এক screen-এ দেখা যায়। যেকোনো camera on/off করা, tap করে বড় করা আর SD/HD বদলানো যায়।
- **🔊 শব্দ:** live view-এ শোনা যায়, recording-এও শব্দ থাকে।
- **🎮 PTZ:** arrow চেপে ধরে রাখলে camera ঘোরে (যেমন Imou Ranger 2)।
- **২৪ ঘণ্টা recording:** ১ মিনিটের টুকরোয় হয়। CPU প্রায় লাগে না।
- **Motion:** live-এ লাল "Motion" badge দেখায়। Motion event-গুলো snapshot-সহ list-এ থাকে। চাইলে "শুধু motion হলে" mode-ও বাছতে পারেন।
- **Timeline:** ২৪ ঘণ্টার একটা bar থাকে। ধূসর অংশ মানে recording আছে, লাল অংশ মানে motion। প্রতি মিনিটের thumbnail থাকে, যেকোনো জায়গায় tap করলে সেখান থেকে চলে, আর পরের মিনিট নিজে থেকে চলতে থাকে।
- **Storage:**
  - SSD লাগানো থাকলে recording SSD-তে যায়। SSD format হয় না, recording যায় `CCTV-Recordings` folder-এ।
  - SSD না থাকলে recording যায় SD card-এ, সর্বোচ্চ ৭GB।
  - জায়গা কমে গেলে আগে motion ছাড়া অংশ মোছে।
- **Google Drive:** সামান্য motion হলেও সেই মিনিটের পুরো recording আর তার snapshot Drive-এর একটা folder-এ যায় (সর্বোচ্চ ১২GB)। বাকি recording শুধু local-এ থাকে।
- **Account:** admin সব পারে। Viewer শুধু অনুমতি পাওয়া camera দেখে, আর recordings ও PTZ-এর অনুমতিও admin আলাদা করে ঠিক করে দেয়।
- **English / বাংলা:** উপরের ডানদিকের button দিয়ে ভাষা বদলায়।

## ১. Camera app
- **Imou:** username `admin`, password হলো sticker-এর **Safety Code**। Imou app-এ **Video Encryption বন্ধ** রাখবেন।
- **Meari:** Settings → Onvif Settings চালু করে একটা password দিন।
- **V380:** Advanced settings → Onvif setting চালু করুন।
- Router-এ প্রতিটা camera আর Tinker Board-কে **fixed IP** দিন।

## ২. Install
```bash
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/shovon-mandal/home-cctv /opt/cctv-src
sudo /opt/cctv-src/install.sh
```
Install-এর সময় time zone জিজ্ঞেস করবে, Enter চাপলে `Asia/Dhaka` থাকবে। তারপর admin-এর username আর password চাইবে। শেষে link দেখাবে: `http://<board-IP>:8085`

আগে থেকেই install করা থাকলে নতুন কিছু করতে হবে না। Board নিজে থেকেই update নিয়ে নেবে, অথবা এখনই নিতে: `sudo cctv-update`

## ৩. Camera যোগ করা
Admin দিয়ে login করে **⚙️ Settings → Cameras**-এ যান। Brand বাছুন, IP আর password দিন। Meari হলে **🔍 ONVIF** চাপুন। তারপর **▶ Test** চাপুন, ✅ দেখালে **Enabled**-এ টিক দিয়ে **Save** করুন।
- Recording mode: **২৪ ঘণ্টা**, **শুধু motion হলে**, অথবা **বন্ধ**।
- Camera-য় শব্দ থাকলে **Sound**-এ টিক দিন। ঘোরানো যায় এমন camera হলে (Ranger 2) **PTZ**-এ টিক দিন।

## ৪. SSD (আগের file মুছবে না)
```bash
sudo cctv-ssd-setup
sudo cctv-ssd-setup /dev/sda1
sudo cctv-ssd-eject      # SSD খোলার আগে অবশ্যই
```

## ৫. Google Drive
**⚙️ Settings → Google Drive → Google দিয়ে connect** চাপুন। Login করে **Allow** দিন। এরপর "পাওয়া যাচ্ছে না" page আসবে, এটাই স্বাভাবিক। ওই page-এর address bar-এর পুরো link copy করে paste করুন, তারপর **Connect শেষ করুন** চাপুন।

## ৬. যেকোনো জায়গা থেকে দেখা
```bash
sudo cctv-funnel
```
এটা `https://….ts.net:8443` ধরনের একটা link দেবে। Internet-এ খোলা থাকছে, তাই password শক্ত রাখবেন: `sudo cctv-user passwd admin`

## ৭. Android app
[HomeCCTV.apk](https://github.com/shovon-mandal/home-cctv/releases/download/app-latest/HomeCCTV.apk) download করে install করুন। 🏠 ঘরে বাসার ঠিকানা (board-এর IP) আর 🌍 ঘরে বাইরের ঠিকানা (`https://….ts.net:8443` link) দিন। App-এর উপরে English/বাংলা button আছে।

## ৮. সমস্যা হলে
- `cctv-status` চালালে সব service, recording, storage আর Drive-এর অবস্থা দেখাবে।
- কোনো camera-য় "⚠ record হচ্ছে না" দেখালে: `journalctl -u cctv-recorder -n 50`
- Test-এ "video পড়া যাচ্ছে না" দেখালে: Imou app-এ Video Encryption বন্ধ করুন।
- Phone-এ live কালো দেখালে: camera-য় H.264 দিন।
