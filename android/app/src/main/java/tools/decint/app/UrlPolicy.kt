package tools.decint.app

import java.util.Locale

/**
 * Where a URL is allowed to go. Pure Kotlin (no android.*), so it is unit-tested
 * on the JVM — see UrlPolicyTest.
 *
 * - The site itself (the configured host, and its www. alias) stays in the app.
 * - Other web links, mail and phone links leave for the browser or the right app.
 *   That includes .onion results: Tor Browser picks those up if it's installed.
 * - Anything that could run code or read local files in the WebView's context
 *   (javascript:, file:, content:, intent: …) is refused as a navigation.
 * - Ad and ad-tracking hosts are blocked as resources. AdSense's rules don't allow
 *   its ads inside an app's WebView, and the app shows none.
 */
class UrlPolicy(baseUrl: String) {

    enum class Decision { IN_APP, EXTERNAL, BLOCK }

    private val baseScheme: String = schemeOf(baseUrl)
        ?: throw IllegalArgumentException("base URL has no scheme: $baseUrl")
    private val baseHost: String = hostOf(baseUrl)
        ?: throw IllegalArgumentException("base URL has no host: $baseUrl")
    private val basePort: String = portOf(baseUrl)

    /** "https://decint.tools" — what the page's own origin looks like. */
    val origin: String = "$baseScheme://$baseHost" + if (basePort.isEmpty()) "" else ":$basePort"

    fun decide(url: String): Decision {
        val scheme = schemeOf(url) ?: return Decision.BLOCK
        return when (scheme) {
            "http", "https" -> if (isInApp(url)) Decision.IN_APP else Decision.EXTERNAL
            "mailto", "tel", "sms" -> Decision.EXTERNAL
            "about" -> if (url.equals("about:blank", ignoreCase = true)) Decision.IN_APP else Decision.BLOCK
            else -> Decision.BLOCK
        }
    }

    /** Same scheme, same port, and the site's host or its www. alias. */
    fun isInApp(url: String): Boolean {
        if (schemeOf(url) != baseScheme) return false
        val host = hostOf(url) ?: return false
        if (portOf(url) != basePort) return false
        return host == baseHost || host == "www.$baseHost"
    }

    /** Sub-resource requests (scripts, frames, pixels) that are never loaded. */
    fun isBlockedResource(url: String): Boolean {
        val host = hostOf(url) ?: return false
        return BLOCKED_RESOURCE_HOSTS.any { host == it || host.endsWith(".$it") }
    }

    companion object {
        /** Appended to the WebView's user agent; frontend/lib/platform.ts looks for it. */
        const val UA_TOKEN = "DecintAndroid/"

        val BLOCKED_RESOURCE_HOSTS = listOf(
            "googlesyndication.com",
            "doubleclick.net",
            "adservice.google.com",
            "googleadservices.com",
            "fundingchoicesmessages.google.com",
        )

        private val SCHEME = Regex("^([a-zA-Z][a-zA-Z0-9+.-]*):")

        // scheme://[userinfo@]host[:port] — host may be a bracketed IPv6 literal.
        private val AUTHORITY = Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^/?#@]*@)?(\\[[^\\]]*\\]|[^/?#:]*)(?::(\\d*))?")

        fun schemeOf(url: String): String? =
            SCHEME.find(url.trim())?.groupValues?.get(1)?.lowercase(Locale.ROOT)

        fun hostOf(url: String): String? =
            AUTHORITY.find(url.trim())?.groupValues?.get(1)
                ?.lowercase(Locale.ROOT)?.trimEnd('.')?.takeIf { it.isNotEmpty() }

        /** The explicit port, or "" when the URL uses the scheme's default. */
        fun portOf(url: String): String {
            val port = AUTHORITY.find(url.trim())?.groupValues?.get(2).orEmpty()
            return when {
                port.isEmpty() -> ""
                port == "443" && schemeOf(url) == "https" -> ""
                port == "80" && schemeOf(url) == "http" -> ""
                else -> port
            }
        }
    }
}
