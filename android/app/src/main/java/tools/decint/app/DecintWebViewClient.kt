package tools.decint.app

import android.graphics.Bitmap
import android.net.Uri
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.ByteArrayInputStream

/** What the WebView callbacks need from the activity. */
interface WebHost {
    fun openExternally(uri: Uri)
    fun onPageStarted()
    fun onPageFinished()
    fun onMainFrameError(description: String)
    fun onRendererGone()
}

class DecintWebViewClient(
    private val policy: UrlPolicy,
    private val host: WebHost,
) : WebViewClient() {

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        // Frames (the captcha widget, mostly) load whatever they need; only the
        // page the person is looking at is held to the policy.
        if (!request.isForMainFrame) return false
        return when (policy.decide(request.url.toString())) {
            UrlPolicy.Decision.IN_APP -> false
            UrlPolicy.Decision.EXTERNAL -> { host.openExternally(request.url); true }
            UrlPolicy.Decision.BLOCK -> true
        }
    }

    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        if (!policy.isBlockedResource(request.url.toString())) return null
        return WebResourceResponse(
            "text/plain", "utf-8", 204, "No Content", emptyMap(), ByteArrayInputStream(ByteArray(0)),
        )
    }

    override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) = host.onPageStarted()

    override fun onPageFinished(view: WebView, url: String?) = host.onPageFinished()

    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
        if (request.isForMainFrame) host.onMainFrameError(error.description?.toString().orEmpty())
    }

    override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
        // The renderer crashed or was killed for memory. The WebView is unusable
        // from here; returning true keeps the app alive so the activity can rebuild it.
        host.onRendererGone()
        return true
    }
}
