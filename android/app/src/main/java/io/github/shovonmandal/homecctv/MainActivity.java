package io.github.shovonmandal.homecctv;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Thin wrapper around the CCTV dashboard. Tries the home (Wi-Fi) address
 * first and falls back to the Tailscale address when you are outside.
 */
public class MainActivity extends Activity {
    static final String PREFS = "cctv";
    static final String KEY_HOME = "home_url";
    static final String KEY_REMOTE = "remote_url";
    static final String KEY_DIRTY = "dirty";

    private WebView web;
    private FrameLayout root;
    private View fullscreenView;
    private WebChromeClient.CustomViewCallback fullscreenCallback;
    private String baseUrl;
    private boolean showingError;
    private final Handler ui = new Handler(Looper.getMainLooper());

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0d1117"));
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setUserAgentString(s.getUserAgentString() + " HomeCCTV-App");
        CookieManager.getInstance().setAcceptCookie(true);
        web.setBackgroundColor(Color.parseColor("#0d1117"));
        web.addJavascriptInterface(new Bridge(), "CCTVApp");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (baseUrl != null && !baseUrl.isEmpty() && u.getHost() != null && u.getHost().equals(Uri.parse(baseUrl).getHost())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // first run: start the dashboard in the language chosen in the app
                if (url != null && url.startsWith("http")) {
                    String lang = prefs().getString(SettingsActivity.KEY_LANG, "en");
                    view.evaluateJavascript("(function(){try{if(!localStorage.getItem('cctv.lang')){"
                            + "localStorage.setItem('cctv.lang','\\\"" + lang + "\\\"');"
                            + "if('" + lang + "'!=='en')location.reload();}}catch(e){}})()", null);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) showError("Server-এর সাথে যোগাযোগ হচ্ছে না।");
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback cb) {      // video fullscreen
                if (fullscreenView != null) { cb.onCustomViewHidden(); return; }
                fullscreenView = view; fullscreenCallback = cb;
                root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                setFullscreenMode(true);
            }

            @Override
            public void onHideCustomView() {
                if (fullscreenView == null) return;
                root.removeView(fullscreenView);
                fullscreenView = null;
                if (fullscreenCallback != null) fullscreenCallback.onCustomViewHidden();
                setFullscreenMode(false);
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimetype, length) -> {
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                r.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
                String name = URLUtil.guessFileName(url, contentDisposition, mimetype);
                r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "CCTV-" + name);
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                ((DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(this, tx("Download started", "Download শুরু হয়েছে"), Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                Toast.makeText(this, tx("Download failed: ", "Download হয়নি: ") + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        });

        if (prefs().getString(KEY_HOME, "").isEmpty() && prefs().getString(KEY_REMOTE, "").isEmpty()) {
            startActivity(new Intent(this, SettingsActivity.class));
        } else {
            connect();
        }
    }

    SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    String tx(String en, String bn) {
        return "bn".equals(prefs().getString(SettingsActivity.KEY_LANG, "en")) ? bn : en;
    }

    /** Pick the first address that answers, then load the dashboard. */
    void connect() {
        showingError = false;
        baseUrl = "";
        web.loadDataWithBaseURL(null, page(tx("Connecting…", "সংযোগ হচ্ছে…"), "", false, this), "text/html", "utf-8", null);
        final String home = prefs().getString(KEY_HOME, "");
        final String remote = prefs().getString(KEY_REMOTE, "");
        new Thread(() -> {
            String pick = null;
            if (!home.isEmpty() && reachable(home, 1500)) pick = home;
            else if (!remote.isEmpty() && reachable(remote, 4000)) pick = remote;
            final String chosen = pick;
            ui.post(() -> {
                if (chosen == null) {
                    showError(tx("The Tinker Board can't be reached at the home or the outside address.", "বাসার বা বাইরের, কোনো ঠিকানাতেই Tinker Board পাওয়া যাচ্ছে না।"));
                } else {
                    baseUrl = chosen;
                    web.loadUrl(chosen + "/");
                }
            });
        }).start();
    }

    static boolean reachable(String base, int timeoutMs) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/api/me").openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            int code = c.getResponseCode();     // 200 or 401 both mean "server is there"
            return code > 0;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    void showError(String msg) {
        showingError = true;
        web.loadDataWithBaseURL(null, page("⚠️ " + msg,
                tx("Is the Tinker Board on? Is there internet?", "Tinker Board চালু আছে তো? Internet আছে তো?"), true, this), "text/html", "utf-8", null);
    }

    static String page(String title, String sub, boolean buttons, MainActivity a) {
        String b = buttons
                ? "<button onclick='CCTVApp.retry()'>" + a.tx("Try again", "আবার চেষ্টা") + "</button><button class=g onclick='CCTVApp.openSettings()'>"
                  + a.tx("Server address", "Server ঠিকানা") + "</button>"
                : "<div class=spin></div>";
        return "<html><head><meta name=viewport content='width=device-width,initial-scale=1'><style>"
                + "body{margin:0;height:100vh;display:grid;place-items:center;background:#0d1117;color:#e6edf3;font-family:sans-serif;text-align:center;padding:24px;box-sizing:border-box}"
                + "p{color:#8b949e;font-size:14px}button{display:block;width:240px;margin:12px auto 0;padding:12px;border:0;border-radius:10px;background:#3fb950;color:#04140a;font-size:16px;font-weight:600}"
                + "button.g{background:transparent;color:#e6edf3;border:1px solid #2a313c}"
                + ".spin{width:36px;height:36px;margin:20px auto;border:3px solid #2a313c;border-top-color:#3fb950;border-radius:50%;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(360deg)}}"
                + "</style></head><body><div><div style='font-size:42px'>📹</div><h3>" + title + "</h3><p>" + sub + "</p>" + b + "</div></body></html>";
    }

    @SuppressWarnings("deprecation")
    void setFullscreenMode(boolean on) {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = getWindow().getInsetsController();
            if (c == null) return;
            if (on) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            } else {
                c.show(WindowInsets.Type.systemBars());
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(on
                    ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    : 0);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        SharedPreferences p = prefs();
        if (p.getBoolean(KEY_DIRTY, false)) {        // addresses changed in settings
            p.edit().putBoolean(KEY_DIRTY, false).apply();
            connect();
        } else if (showingError || baseUrl == null) {
            if (p.getString(KEY_HOME, "").isEmpty() && p.getString(KEY_REMOTE, "").isEmpty()) {
                showingError = true;
                web.loadDataWithBaseURL(null, page(tx("No server address yet", "Server ঠিকানা দেওয়া হয়নি"), "", true, this), "text/html", "utf-8", null);
            } else {
                connect();
            }
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        web.onPause();                              // stops live streams in the background
        CookieManager.getInstance().flush();
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (fullscreenView != null) {
            web.getWebChromeClient().onHideCustomView();
        } else if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    /** Called from the dashboard page (window.CCTVApp). */
    class Bridge {
        @JavascriptInterface
        public void openSettings() {
            ui.post(() -> startActivity(new Intent(MainActivity.this, SettingsActivity.class)));
        }

        @JavascriptInterface
        public void retry() {
            ui.post(MainActivity.this::connect);
        }
    }
}
