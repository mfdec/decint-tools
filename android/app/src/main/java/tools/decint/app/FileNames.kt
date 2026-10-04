package tools.decint.app

/**
 * Turns whatever name a page asked for into one that is safe to create in
 * Downloads: no directories, no characters file systems reject, not empty, not
 * absurdly long, and the extension kept when it has to be shortened.
 * Pure Kotlin, unit-tested in FileNamesTest.
 */
object FileNames {
    private const val MAX = 120
    private val UNSAFE = Regex("[\\u0000-\\u001f\\u007f\\\\/:*?\"<>|]")

    fun sanitize(requested: String?, fallback: String = "download"): String {
        // Keep only the last path segment: "../../x.csv" and "a/b.csv" both become a plain name.
        val last = requested.orEmpty().replace('\\', '/').substringAfterLast('/')
        var name = UNSAFE.replace(last, "_").trim().trim('.', ' ')
        if (name.isEmpty()) name = fallback
        if (name.length > MAX) {
            val dot = name.lastIndexOf('.')
            val ext = if (dot > 0 && name.length - dot <= 10) name.substring(dot) else ""
            name = name.take(MAX - ext.length).trimEnd('.', ' ') + ext
        }
        return name
    }
}
