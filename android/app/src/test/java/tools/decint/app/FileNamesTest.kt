package tools.decint.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FileNamesTest {
    @Test fun plainNamesPassThrough() {
        assertEquals("decint-darkweb-bitcoin-2026-10-04.csv",
            FileNames.sanitize("decint-darkweb-bitcoin-2026-10-04.csv"))
    }

    @Test fun pathsAreStripped() {
        assertEquals("passwd", FileNames.sanitize("../../etc/passwd"))
        assertEquals("x.csv", FileNames.sanitize("a\\b\\x.csv"))
    }

    @Test fun unsafeCharactersAreReplaced() {
        assertEquals("a_b_c_.json", FileNames.sanitize("a:b*c?.json"))
        assertEquals("line_break.txt", FileNames.sanitize("line\nbreak.txt"))
    }

    @Test fun emptyOrDotsFallBack() {
        assertEquals("download", FileNames.sanitize(null))
        assertEquals("download", FileNames.sanitize(""))
        assertEquals("download", FileNames.sanitize(".."))
        assertEquals("export", FileNames.sanitize("  ", fallback = "export"))
    }

    @Test fun longNamesKeepTheirExtension() {
        val name = FileNames.sanitize("x".repeat(300) + ".json")
        assertEquals(120, name.length)
        assertTrue(name.endsWith(".json"))
    }
}
