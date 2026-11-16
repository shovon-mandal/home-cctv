package io.github.shovonmandal.homecctv;

import java.util.HashMap;
import java.util.Map;

/** Bangla translation of the app's own screens (the dashboard has its own, web/lang/bn.js).
 *  Keys are the English texts used in tx(...); a missing key shows the English text. */
final class Bn {
    static final String LABEL = "বাং";
    private static final Map<String, String> T = new HashMap<>();

    static String t(String en) {
        String v = T.get(en);
        return v != null ? v : en;
    }

    static {
        T.put("📹 Home CCTV",
              "📹 Khulna CCTV");
        T.put("Enter the CCTV server address. Just the IP is enough (e.g. 192.168.0.10); port 8085 is added for you.",
              "CCTV server-এর ঠিকানা দিন। শুধু IP দিলেই হবে (যেমন 192.168.0.10), port 8085 নিজে বসে যাবে।");
        T.put("🏠 Home Wi-Fi address",
              "🏠 বাসার Wi-Fi ঠিকানা");
        T.put("🌍 From anywhere (https://….ts.net:8443 link)",
              "🌍 বাইরে থেকে (https://….ts.net:8443 link)");
        T.put("At home the app uses the first address (faster); outside it switches to the second one by itself. The link is shown in the dashboard under ⚙️ Settings → System. (A Tailscale IP also works.)",
              "বাসায় থাকলে app প্রথম ঠিকানা দিয়ে খুলবে (দ্রুত), বাইরে থাকলে নিজে থেকে দ্বিতীয় ঠিকানায় যাবে। Link-টা dashboard-এর ⚙️ Settings → System-এ পাবেন। (Tailscale IP-ও দেওয়া যায়।)");
        T.put("Enter at least one address.",
              "অন্তত একটা ঠিকানা দিন।");
        T.put("Checking…",
              "পরীক্ষা করছি…");
        T.put("Saved, but the CCTV server does not answer on either address right now. Is the IP right? Press back to open anyway.",
              "Save হয়েছে, কিন্তু এখন কোনো ঠিকানাতেই CCTV server সাড়া দিচ্ছে না। IP ঠিক আছে তো? তবুও খুলতে back চাপুন।");
        T.put("Download started",
              "Download শুরু হয়েছে");
        T.put("Download failed: ",
              "Download হয়নি: ");
        T.put("Connecting…",
              "সংযোগ হচ্ছে…");
        T.put("The CCTV server can't be reached at the home or the outside address.",
              "বাসার বা বাইরের, কোনো ঠিকানাতেই CCTV server পাওয়া যাচ্ছে না।");
        T.put("Is the CCTV server on? Is there internet?",
              "CCTV server চালু আছে তো? Internet আছে তো?");
        T.put("Try again",
              "আবার চেষ্টা");
        T.put("Server address",
              "Server ঠিকানা");
        T.put("No server address yet",
              "Server ঠিকানা দেওয়া হয়নি");
        T.put("Can't connect to the server.",
              "Server-এর সাথে যোগাযোগ হচ্ছে না।");
    }

    private Bn() { }
}
