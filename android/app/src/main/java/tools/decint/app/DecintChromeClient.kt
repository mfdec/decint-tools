package tools.decint.app

import android.net.Uri
import android.webkit.GeolocationPermissions
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView

/** What the chrome callbacks need from the activity. */
interface ChromeHost {
    fun onProgress(percent: Int)
    /** Open the system file picker; [callback] must be answered exactly once. */
    fun chooseFile(callback: ValueCallback<Array<Uri>>, mimeTypes: Array<String>): Boolean
}

class DecintChromeClient(private val host: ChromeHost) : WebChromeClient() {

    override fun onProgressChanged(view: WebView, newProgress: Int) = host.onProgress(newProgress)

    override fun onShowFileChooser(
        webView: WebView,
        filePathCallback: ValueCallback<Array<Uri>>,
        fileChooserParams: FileChooserParams,
    ): Boolean = host.chooseFile(filePathCallback, mimeTypesFor(fileChooserParams.acceptTypes))

    // The site never needs the camera, microphone or location; nothing a page
    // asks for is granted.
    override fun onPermissionRequest(request: PermissionRequest) = request.deny()

    override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) =
        callback.invoke(origin, false, false)

    companion object {
        /**
         * `accept` lists extensions (".csv,.json") as often as MIME types, and the
         * system picker only filters by MIME type. File providers label CSVs
         * inconsistently, so any extension means "show everything" and the site
         * checks the file itself.
         */
        fun mimeTypesFor(accept: Array<String>?): Array<String> {
            val entries = accept.orEmpty().flatMap { it.split(',') }.map { it.trim() }.filter { it.isNotEmpty() }
            if (entries.isEmpty() || entries.any { !it.contains('/') }) return arrayOf("*/*")
            return entries.toTypedArray()
        }
    }
}
