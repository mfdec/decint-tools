package tools.decint.app

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebSettings
import android.webkit.WebView
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.addCallback
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature

/**
 * The whole app: decint.tools in a locked-down WebView, plus the native pieces
 * a WebView doesn't do on its own — splash, offline screen, back button,
 * downloads, file picking, and sending other links out to the right app.
 */
class MainActivity : ComponentActivity(), WebHost, ChromeHost {

    private val policy = UrlPolicy(BuildConfig.BASE_URL)
    private lateinit var downloads: Downloads
    private lateinit var container: FrameLayout
    private lateinit var webView: WebView
    private lateinit var progress: ProgressBar
    private lateinit var offline: View

    private var firstPageDone = false
    private val startedAt = SystemClock.uptimeMillis()
    private var pendingFile: ValueCallback<Array<Uri>>? = null

    private val pickFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        pendingFile?.onReceiveValue(uri?.let { arrayOf(it) })
        pendingFile = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        val splash = installSplashScreen()
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
        )
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Hold the splash until the first page has drawn, so there is no blank
        // flash in between — but never longer than a few seconds.
        splash.setKeepOnScreenCondition {
            !firstPageDone && SystemClock.uptimeMillis() - startedAt < SPLASH_MAX_MS
        }

        val root = findViewById<View>(R.id.root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            // System bars, the display cutout and the keyboard all push the page
            // in: with edge-to-edge, nothing else resizes it for the keyboard.
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or
                    WindowInsetsCompat.Type.displayCutout() or
                    WindowInsetsCompat.Type.ime(),
            )
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        downloads = Downloads(applicationContext)
        container = findViewById(R.id.web_container)
        progress = findViewById(R.id.progress)
        offline = findViewById(R.id.offline)
        findViewById<Button>(R.id.retry).setOnClickListener { retry() }

        webView = createWebView()
        val restored = savedInstanceState?.let { webView.restoreState(it) } != null
        if (!restored) webView.loadUrl(startUrl(intent))

        onBackPressedDispatcher.addCallback(this) {
            when {
                offline.isVisible && webView.url != null -> retry()
                webView.canGoBack() -> webView.goBack()
                else -> finish()
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled") // the site is a JavaScript app; nothing else loads in this WebView
    private fun createWebView(): WebView {
        val view = WebView(this)
        view.setBackgroundColor(getColor(R.color.bg))
        view.isVerticalScrollBarEnabled = false
        with(view.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            javaScriptCanOpenWindowsAutomatically = false
            // target=_blank links then arrive as ordinary navigations and go
            // through the URL policy like everything else.
            setSupportMultipleWindows(false)
            setGeolocationEnabled(false)
            userAgentString = "$userAgentString ${UrlPolicy.UA_TOKEN}${BuildConfig.VERSION_NAME}"
        }
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        CookieManager.getInstance().setAcceptCookie(true)

        view.webViewClient = DecintWebViewClient(policy, this)
        view.webChromeClient = DecintChromeClient(this)
        view.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            when {
                policy.isInApp(url) -> downloads.enqueue(url, userAgent, contentDisposition, mimeType)
                // blob:/data: files are caught by the page script before they get here.
                url.startsWith("http://") || url.startsWith("https://") -> openExternally(Uri.parse(url))
                else -> Toast.makeText(this, R.string.download_failed, Toast.LENGTH_SHORT).show()
            }
        }

        val origins = setOf(policy.origin)
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(view, Downloads.BRIDGE_NAME, origins) { _, message, _, isMainFrame, _ ->
                if (isMainFrame) message.data?.let(downloads::onPageMessage)
            }
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            WebViewCompat.addDocumentStartJavaScript(view, Downloads.BRIDGE_JS, origins)
        }

        container.addView(view, 0, ViewGroup.LayoutParams(MATCH, MATCH))
        return view
    }

    /** The deep link this launch carries, if it points into the site; else the console. */
    private fun startUrl(intent: Intent?): String {
        val link = intent?.takeIf { it.action == Intent.ACTION_VIEW }?.dataString
        return if (link != null && policy.isInApp(link)) link else BuildConfig.BASE_URL.trimEnd('/') + START_PATH
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == Intent.ACTION_VIEW) webView.loadUrl(startUrl(intent))
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onPause() {
        webView.onPause()
        CookieManager.getInstance().flush() // keep the session if the process is killed
        super.onPause()
    }

    override fun onDestroy() {
        pendingFile?.onReceiveValue(null)
        pendingFile = null
        container.removeView(webView)
        webView.destroy()
        super.onDestroy()
    }

    private fun retry() {
        offline.isVisible = false
        if (webView.url.isNullOrEmpty()) webView.loadUrl(startUrl(null)) else webView.reload()
    }

    // ── WebHost ──

    override fun openExternally(uri: Uri) {
        val intent = Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)
        try {
            startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, R.string.no_app_for_link, Toast.LENGTH_SHORT).show()
        }
    }

    override fun onPageStarted() {
        offline.isVisible = false
    }

    override fun onPageFinished() {
        firstPageDone = true
    }

    override fun onMainFrameError(description: String) {
        firstPageDone = true
        findViewById<TextView>(R.id.offline_detail).text = description
        offline.isVisible = true
    }

    override fun onRendererGone() {
        // A dead renderer's WebView can't be reused; swap in a fresh one.
        container.removeView(webView)
        webView.destroy()
        webView = createWebView()
        webView.loadUrl(startUrl(null))
    }

    // ── ChromeHost ──

    override fun onProgress(percent: Int) {
        progress.progress = percent
        progress.isVisible = percent in 1..99
    }

    override fun chooseFile(callback: ValueCallback<Array<Uri>>, mimeTypes: Array<String>): Boolean {
        pendingFile?.onReceiveValue(null)
        pendingFile = callback
        return try {
            pickFile.launch(mimeTypes)
            true
        } catch (e: ActivityNotFoundException) {
            pendingFile = null
            callback.onReceiveValue(null)
            Toast.makeText(this, R.string.no_file_picker, Toast.LENGTH_SHORT).show()
            true
        }
    }

    private companion object {
        const val START_PATH = "/console"
        const val SPLASH_MAX_MS = 4000L
        const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
    }
}
