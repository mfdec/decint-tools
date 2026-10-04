package tools.decint.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import tools.decint.app.UrlPolicy.Decision.BLOCK
import tools.decint.app.UrlPolicy.Decision.EXTERNAL
import tools.decint.app.UrlPolicy.Decision.IN_APP

class UrlPolicyTest {
    private val policy = UrlPolicy("https://decint.tools")

    @Test fun theSiteStaysInTheApp() {
        assertEquals(IN_APP, policy.decide("https://decint.tools/console"))
        assertEquals(IN_APP, policy.decide("https://decint.tools"))
        assertEquals(IN_APP, policy.decide("https://DECINT.tools/login?next=/console"))
        assertEquals(IN_APP, policy.decide("https://www.decint.tools/pricing"))
        assertEquals(IN_APP, policy.decide("https://decint.tools:443/account"))
        assertEquals(IN_APP, policy.decide("https://decint.tools./account"))
    }

    @Test fun lookalikesAndOtherSchemesLeave() {
        assertEquals(EXTERNAL, policy.decide("https://decint.tools.evil.example/console"))
        assertEquals(EXTERNAL, policy.decide("https://evildecint.tools/"))
        assertEquals(EXTERNAL, policy.decide("https://admin.decint.tools/"))
        assertEquals(EXTERNAL, policy.decide("https://decint.tools@evil.example/"))
        assertEquals(EXTERNAL, policy.decide("http://decint.tools/console"))
        assertEquals(EXTERNAL, policy.decide("https://decint.tools:8443/"))
    }

    @Test fun otherLinksOpenOutside() {
        assertEquals(EXTERNAL, policy.decide("https://discord.com/invite/abc"))
        assertEquals(EXTERNAL, policy.decide("http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/"))
        assertEquals(EXTERNAL, policy.decide("mailto:admin@decint.tools"))
        assertEquals(EXTERNAL, policy.decide("tel:+15550100"))
    }

    @Test fun dangerousSchemesAreRefused() {
        assertEquals(BLOCK, policy.decide("javascript:alert(1)"))
        assertEquals(BLOCK, policy.decide("file:///data/data/tools.decint.app/"))
        assertEquals(BLOCK, policy.decide("content://media/external/1"))
        assertEquals(BLOCK, policy.decide("intent://scan/#Intent;scheme=zxing;end"))
        assertEquals(BLOCK, policy.decide("data:text/html,<h1>x</h1>"))
        assertEquals(BLOCK, policy.decide("not a url"))
        assertEquals(IN_APP, policy.decide("about:blank"))
        assertEquals(BLOCK, policy.decide("about:config"))
    }

    @Test fun adHostsAreBlockedAsResources() {
        assertTrue(policy.isBlockedResource("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=x"))
        assertTrue(policy.isBlockedResource("https://googleads.g.doubleclick.net/pagead/ads"))
        assertTrue(policy.isBlockedResource("https://adservice.google.com/x"))
        assertFalse(policy.isBlockedResource("https://www.google.com/recaptcha/api.js"))
        assertFalse(policy.isBlockedResource("https://decint.tools/_next/static/chunk.js"))
        assertFalse(policy.isBlockedResource("https://notdoubleclick.net/"))
    }

    @Test fun originMatchesTheBaseUrl() {
        assertEquals("https://decint.tools", policy.origin)
        assertEquals("http://10.0.2.2:3000", UrlPolicy("http://10.0.2.2:3000/").origin)
        assertEquals(IN_APP, UrlPolicy("http://10.0.2.2:3000").decide("http://10.0.2.2:3000/console"))
        assertEquals(EXTERNAL, UrlPolicy("http://10.0.2.2:3000").decide("http://10.0.2.2:8000/"))
    }
}
