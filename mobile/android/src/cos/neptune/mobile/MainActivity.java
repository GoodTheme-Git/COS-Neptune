package cos.neptune.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.MimeTypeMap;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * COS Neptune Mobile for Android.
 *
 * The shell is the same web/ folder the PWA uses, packed into assets/web and served
 * from https://neptune.local/ (intercepted here, never touches the network), so the
 * page gets a secure origin and its own localStorage.
 *
 * window.NeptuneNative is the bridge apps.js and neptune.js look for:
 *   ibbs(method, server, token, path, body) -> '{"data": ...}' or '{"error": "..."}'
 *       talks to a TCF_IBBS board directly (no CORS in the way inside the app)
 *   notify(title, text)   system notification while the app is in the background
 *   save(name, dataUrl)   writes a file into Downloads, returns a message for a toast
 */
public class MainActivity extends Activity {
    static final String HOST = "neptune.local";
    static final String HOME = "https://" + HOST + "/index.html";
    static final String CHANNEL = "neptune";
    static final int PICK_FILES = 7;
    // the same endpoints server.py relays: a board's read/post API and nothing else
    static final Pattern IBBS_PATH = Pattern.compile("^/(rooms|files|room/[A-Za-z0-9_-]+(\\?since=(\\d+|end))?|file/[^/?]+)$");
    static final Pattern HOSTPORT = Pattern.compile("^[A-Za-z0-9.\\-]{1,253}:\\d{1,5}$");

    WebView web;
    ValueCallback<Uri[]> fileCallback;
    final Handler ui = new Handler(Looper.getMainLooper());
    int noteId = 1;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
        if (Build.VERSION.SDK_INT >= 28)
            getWindow().getAttributes().layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;

        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);
        s.setSupportMultipleWindows(true);
        s.setJavaScriptCanOpenWindowsAutomatically(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);   // Webinator shows http:// sites
        s.setUserAgentString(s.getUserAgentString() + " NeptuneMobile/3.1");
        s.setTextZoom(100);

        web.addJavascriptInterface(new Bridge(), "NeptuneNative");
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());
        web.setDownloadListener((url, ua, disposition, mime, length) -> download(url, disposition, mime));

        setupNotifications();
        if (saved != null) web.restoreState(saved);
        else web.loadUrl(HOME);
        immersive();
    }

    // ------------------------------------------------------------ window

    /** The shell draws its own status bar and nav bar, so hide Android's. */
    @SuppressWarnings("deprecation")
    void immersive() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    @Override
    public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        if (focus) immersive();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onPause() { super.onPause(); web.onPause(); }

    @Override
    protected void onResume() { super.onResume(); web.onResume(); immersive(); }

    @Override
    protected void onDestroy() { web.destroy(); super.onDestroy(); }

    /** Back goes to the shell first (close a dialog, the shade, recents or the app);
     *  on a bare home screen it leaves Neptune running in the background. */
    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        String js = "(function(){try{var q=function(s){return document.querySelector(s)};"
                + "var busy=!!(q('.modal')||NM.current()||!q('#shade').classList.contains('hidden')"
                + "||!q('#recents').classList.contains('hidden')||q('#pages').scrollLeft>10);"
                + "NM.back();return busy}catch(e){return false}})()";
        web.evaluateJavascript(js, handled -> {
            if (!"true".equals(handled)) moveTaskToBack(true);
        });
    }

    // ------------------------------------------------------------ web view

    class Client extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
            Uri u = req.getUrl();
            if (!HOST.equals(u.getHost())) return null;
            String path = u.getPath() == null ? "/" : u.getPath();
            if (path.equals("/")) path = "/index.html";
            if (path.startsWith("/api/ibbs/file/") && "GET".equals(req.getMethod())) return boardFile(u);
            if (path.startsWith("/api/") || path.contains("..")) return text(404, "no such endpoint");
            try {
                InputStream in = getAssets().open("web" + path);
                WebResourceResponse r = new WebResourceResponse(mime(path), "utf-8", in);
                Map<String, String> h = new HashMap<>();
                h.put("Cache-Control", "no-cache");
                r.setResponseHeaders(h);
                return r;
            } catch (IOException e) {
                return text(404, "not found: " + path);
            }
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            Uri u = req.getUrl();
            if (HOST.equals(u.getHost()) || !req.isForMainFrame()) return false;
            openOutside(u);         // a link that would replace the whole shell: hand it to Android
            return true;
        }
    }

    class Chrome extends WebChromeClient {
        /** window.open (Webinator's "Open outside"): catch the URL, open it in the browser. */
        @Override
        public boolean onCreateWindow(WebView view, boolean dialog, boolean gesture, android.os.Message msg) {
            WebView catcher = new WebView(MainActivity.this);
            catcher.setWebViewClient(new WebViewClient() {
                @Override
                public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                    openOutside(req.getUrl());
                    v.destroy();
                    return true;
                }
            });
            ((WebView.WebViewTransport) msg.obj).setWebView(catcher);
            msg.sendToTarget();
            return true;
        }

        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb, FileChooserParams params) {
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = cb;
            Intent i = params.createIntent();
            if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
            try {
                startActivityForResult(i, PICK_FILES);
            } catch (ActivityNotFoundException e) {
                fileCallback = null;
                return false;
            }
            return true;
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    protected void onActivityResult(int req, int code, Intent data) {
        super.onActivityResult(req, code, data);
        if (req != PICK_FILES || fileCallback == null) return;
        Uri[] got = null;
        if (code == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                got = new Uri[data.getClipData().getItemCount()];
                for (int i = 0; i < got.length; i++) got[i] = data.getClipData().getItemAt(i).getUri();
            } else if (data.getData() != null) {
                got = new Uri[]{data.getData()};
            }
        }
        fileCallback.onReceiveValue(got);
        fileCallback = null;
    }

    void openOutside(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "Nothing on this phone can open " + u, Toast.LENGTH_SHORT).show();
        }
    }

    static String mime(String path) {
        String ext = path.substring(path.lastIndexOf('.') + 1).toLowerCase();
        switch (ext) {
            case "html": return "text/html";
            case "js": return "text/javascript";
            case "css": return "text/css";
            case "json": case "webmanifest": return "application/json";
            case "svg": return "image/svg+xml";
            case "wav": return "audio/wav";
            case "mp3": return "audio/mpeg";
            case "ogg": return "audio/ogg";
        }
        String m = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
        return m == null ? "application/octet-stream" : m;
    }

    static WebResourceResponse text(int code, String msg) {
        return new WebResourceResponse("text/plain", "utf-8", code, code == 200 ? "OK" : "Not Found",
                new HashMap<>(), new ByteArrayInputStream(msg.getBytes(StandardCharsets.UTF_8)));
    }

    // ------------------------------------------------------------ IBBS

    static String cleanServer(String server) {
        String s = server == null ? "" : server.trim().replaceFirst("^https?://", "");
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }

    /** One board request. Throws IOException with a message the shell can show. */
    static byte[] boardRequest(String method, String server, String token, String path, String body) throws IOException {
        server = cleanServer(server);
        if (!HOSTPORT.matcher(server).matches()) throw new IOException("board must be host:port");
        if (!IBBS_PATH.matcher(path).matches()) throw new IOException("not a board endpoint: " + path);
        HttpURLConnection c = (HttpURLConnection) new URL("http://" + server + path).openConnection();
        try {
            c.setConnectTimeout(5000);
            c.setReadTimeout(10000);
            c.setRequestMethod(method);
            c.setRequestProperty("Content-Type", "application/json");
            if (token != null && !token.isEmpty()) c.setRequestProperty("X-IBBS-Token", token);
            if (body != null && !body.isEmpty() && "POST".equals(method)) {
                byte[] b = body.getBytes(StandardCharsets.UTF_8);
                if (b.length > 64 * 1024) throw new IOException("message too big");
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(b.length);
                try (OutputStream o = c.getOutputStream()) { o.write(b); }
            }
            int code = c.getResponseCode();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            byte[] got = in == null ? new byte[0] : readAll(in);
            if (code >= 400) {
                String err = "board said " + code;
                try { err = new JSONObject(new String(got, StandardCharsets.UTF_8)).optString("error", err); } catch (Exception ignored) {}
                throw new IOException(err);
            }
            return got;
        } catch (java.net.ConnectException | java.net.SocketTimeoutException | java.net.UnknownHostException e) {
            throw new IOException("can't reach board " + server + " (same Wi-Fi?)");
        } finally {
            c.disconnect();
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[65536];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return out.toByteArray();
    }

    /** api/ibbs/file/<name>?server=host:port, the chat's download links. */
    WebResourceResponse boardFile(Uri u) {
        try {
            String name = u.getEncodedPath().substring("/api/ibbs".length());
            String server = u.getQueryParameter("server");
            byte[] data = boardRequest("GET", server, null, name, null);
            return new WebResourceResponse("application/octet-stream", null, new ByteArrayInputStream(data));
        } catch (IOException e) {
            return text(502, e.getMessage());
        }
    }

    // ------------------------------------------------------------ downloads

    void download(String url, String disposition, String mime) {
        new Thread(() -> {
            String msg;
            try {
                Uri u = Uri.parse(url);
                String name;
                byte[] data;
                if (url.startsWith("data:")) {
                    name = "neptune-file";
                    data = decodeDataUrl(url);
                } else if (HOST.equals(u.getHost()) && u.getPath() != null && u.getPath().startsWith("/api/ibbs/file/")) {
                    name = URLDecoder.decode(u.getEncodedPath().substring("/api/ibbs/file/".length()), "UTF-8");
                    data = boardRequest("GET", u.getQueryParameter("server"), null, u.getEncodedPath().substring("/api/ibbs".length()), null);
                } else {
                    ui.post(() -> openOutside(u));
                    return;
                }
                msg = saveDownload(name, mime, data);
            } catch (Exception e) {
                msg = "Download failed: " + e.getMessage();
            }
            final String m = msg;
            ui.post(() -> Toast.makeText(this, m, Toast.LENGTH_SHORT).show());
        }).start();
    }

    static byte[] decodeDataUrl(String url) throws IOException {
        int comma = url.indexOf(',');
        if (comma < 0) throw new IOException("bad data URL");
        String head = url.substring(5, comma), body = url.substring(comma + 1);
        if (head.endsWith(";base64")) return Base64.decode(body, Base64.DEFAULT);
        return URLDecoder.decode(body.replace("+", "%2B"), "UTF-8").getBytes(StandardCharsets.UTF_8);
    }

    @SuppressWarnings("deprecation")
    String saveDownload(String name, String mime, byte[] data) throws IOException {
        name = name.replaceAll("[/\\\\:*?\"<>|]", "_");
        if (name.isEmpty()) name = "neptune-file";
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues v = new ContentValues();
            v.put(MediaStore.Downloads.DISPLAY_NAME, name);
            if (mime != null && !mime.isEmpty()) v.put(MediaStore.Downloads.MIME_TYPE, mime);
            v.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/COS Neptune");
            Uri item = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (item == null) throw new IOException("Android refused the file");
            try (OutputStream o = getContentResolver().openOutputStream(item)) {
                if (o == null) throw new IOException("can't write");
                o.write(data);
            }
        } else {
            if (checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                ui.post(() -> requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, 2));
                throw new IOException("allow storage, then try again");
            }
            File dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "COS Neptune");
            if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("can't make " + dir);
            try (FileOutputStream o = new FileOutputStream(new File(dir, name))) { o.write(data); }
        }
        return "Saved to Downloads/COS Neptune/" + name;
    }

    // ------------------------------------------------------------ notifications

    void setupNotifications() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Neptune", NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("IBBS messages and other Neptune notifications");
            ((NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE)).createNotificationChannel(ch);
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
    }

    @SuppressWarnings("deprecation")
    void showNotification(String title, String text) {
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        b.setSmallIcon(android.R.drawable.stat_notify_chat).setContentTitle(title).setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text)).setContentIntent(pi).setAutoCancel(true);
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        nm.notify(noteId++ % 50, b.build());     // a flood reuses 50 slots instead of piling up
    }

    // ------------------------------------------------------------ bridge

    class Bridge {
        long lastNote = 0;

        @JavascriptInterface
        public String ibbs(String method, String server, String token, String path, String body) {
            JSONObject out = new JSONObject();
            try {
                if (!"GET".equals(method) && !"POST".equals(method)) throw new IOException("bad method");
                byte[] got = boardRequest(method, server, token, path, body);
                out.put("data", new JSONTokener(new String(got, StandardCharsets.UTF_8)).nextValue());
            } catch (Exception e) {
                try { out.put("error", e.getMessage() == null ? e.toString() : e.getMessage()); } catch (Exception ignored) {}
            }
            return out.toString();
        }

        @JavascriptInterface
        public void notify(String title, String text) {
            long now = System.currentTimeMillis();
            if (now - lastNote < 1000) return;        // never more than one a second
            lastNote = now;
            ui.post(() -> showNotification(title, text));
        }

        @JavascriptInterface
        public String save(String name, String dataUrl) {
            try {
                int comma = Math.max(5, dataUrl.indexOf(','));
                String mime = dataUrl.startsWith("data:") ? dataUrl.substring(5, comma).split(";")[0] : "";
                return saveDownload(name, mime, decodeDataUrl(dataUrl));
            } catch (Exception e) {
                return "Not saved: " + e.getMessage();
            }
        }
    }
}
