package io.github.shovonmandal.homecctv;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Two addresses: home Wi-Fi (fast) and Tailscale (from anywhere). */
public class SettingsActivity extends Activity {
    static final String KEY_LANG = "lang";
    private EditText home, remote;
    private TextView status;
    private boolean bn;

    /** English / Bangla text, picked by the language switch. */
    private String tx(String en, String bnText) {
        return bn ? bnText : en;
    }
    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        SharedPreferences p = getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE);
        bn = "bn".equals(p.getString(KEY_LANG, "en"));
        build(p);
    }

    private void build(SharedPreferences p) {
        String keepHome = home != null ? home.getText().toString() : null;
        String keepRemote = remote != null ? remote.getText().toString() : null;
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(22);
        col.setPadding(pad, dp(36), pad, pad);

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        TextView title = text(tx("📹 Home CCTV", "📹 আমার CCTV"), 24, "#e6edf3", true);
        top.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        Button lang = new Button(this);
        lang.setText(bn ? "EN" : "বাং");
        lang.setAllCaps(false);
        lang.setTextColor(Color.parseColor("#e6edf3"));
        GradientDrawable lbg = new GradientDrawable();
        lbg.setColor(Color.parseColor("#161b22"));
        lbg.setStroke(dp(1), Color.parseColor("#2a313c"));
        lbg.setCornerRadius(dp(10));
        lang.setBackground(lbg);
        lang.setOnClickListener(v -> {
            bn = !bn;
            p.edit().putString(KEY_LANG, bn ? "bn" : "en").apply();
            build(p);
        });
        top.addView(lang, new LinearLayout.LayoutParams(dp(64), dp(42)));
        col.addView(top);
        col.addView(text(tx("Enter the Tinker Board address. Just the IP is enough (e.g. 192.168.0.10); port 8085 is added for you.",
                "Tinker Board-এর ঠিকানা দিন। শুধু IP দিলেই হবে (যেমন 192.168.0.10), port 8085 নিজে বসে যাবে।"), 14, "#8b949e", false));

        col.addView(label(tx("🏠 Home Wi-Fi address", "🏠 বাসার Wi-Fi ঠিকানা")));
        home = input("192.168.0.10", keepHome != null ? keepHome : p.getString(MainActivity.KEY_HOME, ""));
        col.addView(home);

        col.addView(label(tx("🌍 From anywhere (https://….ts.net:8443 link)", "🌍 বাইরে থেকে (https://….ts.net:8443 link)")));
        remote = input("https://tinkerboard.xxxx.ts.net:8443", keepRemote != null ? keepRemote : p.getString(MainActivity.KEY_REMOTE, ""));
        col.addView(remote);

        TextView hint = text(tx("At home the app uses the first address (faster); outside it switches to the second one by itself. The link is shown in the dashboard under ⚙️ Settings → System. (A Tailscale IP also works.)",
                "বাসায় থাকলে app প্রথম ঠিকানা দিয়ে খুলবে (দ্রুত), বাইরে থাকলে নিজে থেকে দ্বিতীয় ঠিকানায় যাবে। Link-টা dashboard-এর ⚙️ Settings → System-এ পাবেন। (Tailscale IP-ও দেওয়া যায়।)"), 13, "#8b949e", false);
        hint.setPadding(0, dp(12), 0, 0);
        col.addView(hint);

        Button save = new Button(this);
        save.setText("Save");
        save.setAllCaps(false);
        save.setTextColor(Color.parseColor("#04140a"));
        save.setTextSize(16);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor("#3fb950"));
        bg.setCornerRadius(dp(10));
        save.setBackground(bg);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(50));
        lp.topMargin = dp(22);
        col.addView(save, lp);

        status = text("", 14, "#d29922", false);
        status.setPadding(0, dp(12), 0, 0);
        col.addView(status);

        save.setOnClickListener(v -> save());

        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Color.parseColor("#0d1117"));
        sv.addView(col);
        setContentView(sv);
    }

    private void save() {
        final String h = normalize(home.getText().toString());
        final String r = normalize(remote.getText().toString());
        if (h.isEmpty() && r.isEmpty()) {
            status.setText(tx("Enter at least one address.", "অন্তত একটা ঠিকানা দিন।"));
            return;
        }
        getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE).edit()
                .putString(MainActivity.KEY_HOME, h)
                .putString(MainActivity.KEY_REMOTE, r)
                .putBoolean(MainActivity.KEY_DIRTY, true)
                .apply();
        status.setText(tx("Checking…", "পরীক্ষা করছি…"));
        new Thread(() -> {
            boolean hOk = !h.isEmpty() && MainActivity.reachable(h, 2000);
            boolean rOk = !r.isEmpty() && MainActivity.reachable(r, 4000);
            ui.post(() -> {
                if (hOk || rOk) {
                    finish();
                } else {
                    status.setText(tx("Saved, but the Tinker Board does not answer on either address right now. Is the IP right? Press back to open anyway.",
                            "Save হয়েছে, কিন্তু এখন কোনো ঠিকানাতেই Tinker Board সাড়া দিচ্ছে না। IP ঠিক আছে তো? তবুও খুলতে back চাপুন।"));
                }
            });
        }).start();
    }

    /** "192.168.0.10" -> "http://192.168.0.10:8085" */
    static String normalize(String s) {
        s = s.trim();
        if (s.isEmpty()) return "";
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://" + s;
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        Uri u = Uri.parse(s);
        if (u.getPort() == -1 && "http".equals(u.getScheme())) s = s + ":8085";
        return s;
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private TextView text(String t, int sp, String color, boolean bold) {
        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(sp);
        tv.setTextColor(Color.parseColor(color));
        if (bold) tv.setTypeface(tv.getTypeface(), android.graphics.Typeface.BOLD);
        tv.setPadding(0, 0, 0, dp(8));
        return tv;
    }

    private TextView label(String t) {
        TextView tv = text(t, 13, "#8b949e", false);
        tv.setPadding(0, dp(16), 0, dp(6));
        return tv;
    }

    private EditText input(String hint, String value) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setText(value.replace("http://", "").replace(":8085", ""));
        e.setSingleLine(true);
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        e.setTextColor(Color.parseColor("#e6edf3"));
        e.setHintTextColor(Color.parseColor("#57606a"));
        e.setGravity(Gravity.CENTER_VERTICAL);
        e.setPadding(dp(12), dp(10), dp(12), dp(10));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor("#161b22"));
        bg.setStroke(dp(1), Color.parseColor("#2a313c"));
        bg.setCornerRadius(dp(10));
        e.setBackground(bg);
        return e;
    }
}
