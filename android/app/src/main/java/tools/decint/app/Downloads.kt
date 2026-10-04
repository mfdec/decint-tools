package tools.decint.app

import android.app.DownloadManager
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Base64
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.widget.Toast
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * Files the site hands the user.
 *
 * Two kinds reach here:
 *  - Plain links (the admin CSV exports): handed to DownloadManager with the
 *    session cookie, so they arrive in Downloads with a notification.
 *  - Files the page builds itself (the dark-web JSON/CSV export) are blob: URLs.
 *    DownloadManager can't fetch those, so the page script in [BRIDGE_JS] reads
 *    the blob and posts it here, where it is written to Downloads.
 */
class Downloads(private val context: Context) {
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    fun enqueue(url: String, userAgent: String, contentDisposition: String?, mimeType: String?) {
        val name = FileNames.sanitize(URLUtil.guessFileName(url, contentDisposition, mimeType))
        val request = DownloadManager.Request(Uri.parse(url))
            .setTitle(name)
            .setMimeType(mimeType)
            .addRequestHeader("User-Agent", userAgent)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
        CookieManager.getInstance().getCookie(url)?.let { request.addRequestHeader("Cookie", it) }
        context.getSystemService(DownloadManager::class.java).enqueue(request)
        toast(context.getString(R.string.download_started, name))
    }

    /** A message from [BRIDGE_JS]: {type:"save", name, mime, data(base64)} or {type:"save-failed"}. */
    fun onPageMessage(json: String) {
        val msg = runCatching { JSONObject(json) }.getOrNull() ?: return
        if (msg.optString("type") != "save") {
            toast(context.getString(R.string.download_failed))
            return
        }
        val name = FileNames.sanitize(msg.optString("name"))
        val mime = msg.optString("mime").ifBlank { "application/octet-stream" }
        val data = msg.optString("data")
        io.execute {
            val ok = runCatching { save(name, mime, Base64.decode(data, Base64.DEFAULT)) }.isSuccess
            toast(if (ok) context.getString(R.string.download_saved, name) else context.getString(R.string.download_failed))
        }
    }

    private fun save(name: String, mime: String, bytes: ByteArray) {
        require(bytes.size <= MAX_BYTES) { "too large" }
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: error("insert failed")
        try {
            resolver.openOutputStream(uri)!!.use { it.write(bytes) }
            values.clear()
            values.put(MediaStore.Downloads.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
        } catch (e: Exception) {
            resolver.delete(uri, null, null)
            throw e
        }
    }

    private fun toast(text: String) = main.post { Toast.makeText(context, text, Toast.LENGTH_SHORT).show() }

    companion object {
        private const val MAX_BYTES = 50 * 1024 * 1024

        /** Name the page script posts to; only the site's own origin can see it. */
        const val BRIDGE_NAME = "DecintNative"

        /**
         * Runs at document start, on the site's origin only (WebViewCompat
         * .addDocumentStartJavaScript). It does two things:
         *  1. tags <html> with `in-app` before the page renders, so the purchase UI
         *     hidden by frontend/lib/platform.ts never flashes;
         *  2. turns `<a download href="blob:…">` clicks into a message to
         *     [onPageMessage], keeping a reference to each blob so a page that
         *     revokes the URL straight after clicking still gets its file.
         */
        val BRIDGE_JS = """
            (function () {
              function mark() { if (document.documentElement) document.documentElement.classList.add('in-app'); }
              mark();
              document.addEventListener('readystatechange', mark);

              var blobs = new Map();
              var create = URL.createObjectURL;
              URL.createObjectURL = function (obj) {
                var url = create.call(URL, obj);
                if (obj instanceof Blob) {
                  blobs.set(url, obj);
                  setTimeout(function () { blobs.delete(url); }, 120000);
                }
                return url;
              };

              function send(name, href) {
                var known = blobs.get(href);
                var get = known ? Promise.resolve(known) : fetch(href).then(function (r) { return r.blob(); });
                get.then(function (blob) {
                  var reader = new FileReader();
                  reader.onload = function () {
                    var s = String(reader.result);
                    $BRIDGE_NAME.postMessage(JSON.stringify({
                      type: 'save', name: name, mime: blob.type || 'application/octet-stream',
                      data: s.slice(s.indexOf(',') + 1)
                    }));
                  };
                  reader.readAsDataURL(blob);
                }).catch(function () {
                  $BRIDGE_NAME.postMessage(JSON.stringify({ type: 'save-failed', name: name }));
                });
              }

              function local(a) {
                var href = a.href || '';
                return a.hasAttribute('download') && (href.indexOf('blob:') === 0 || href.indexOf('data:') === 0);
              }

              var click = HTMLAnchorElement.prototype.click;
              HTMLAnchorElement.prototype.click = function () {
                if (local(this)) { send(this.getAttribute('download') || 'download', this.href); return; }
                return click.apply(this, arguments);
              };
              document.addEventListener('click', function (e) {
                var a = e.target && e.target.closest ? e.target.closest('a') : null;
                if (a && local(a)) { e.preventDefault(); send(a.getAttribute('download') || 'download', a.href); }
              }, true);
            })();
        """.trimIndent()
    }
}
