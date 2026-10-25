package com.book2course.learner.data.config

import org.junit.Assert.assertEquals
import org.junit.Test

class ServerUrlTest {
    @Test
    fun `https urls are normalised`() {
        assertEquals(UrlCheck.Ok("https://host.tail.ts.net/"), normalizeServerUrl(" https://Host.tail.ts.net/ ", false))
        assertEquals(UrlCheck.Ok("https://host.tail.ts.net:8443/api/"), normalizeServerUrl("host.tail.ts.net:8443/api", false))
    }

    @Test
    fun `plain http only for the emulator in debug`() {
        assertEquals(UrlCheck.NotHttps, normalizeServerUrl("http://host.tail.ts.net", true))
        assertEquals(UrlCheck.NotHttps, normalizeServerUrl("http://10.0.2.2:8100", false))
        assertEquals(UrlCheck.Ok("http://10.0.2.2:8100/"), normalizeServerUrl("http://10.0.2.2:8100", true))
    }

    @Test
    fun `credentials, queries and other schemes are refused`() {
        assertEquals(UrlCheck.Empty, normalizeServerUrl("  ", false))
        assertEquals(UrlCheck.Invalid, normalizeServerUrl("https://user:pw@host", false))
        assertEquals(UrlCheck.Invalid, normalizeServerUrl("https://host/?token=1", false))
        assertEquals(UrlCheck.Invalid, normalizeServerUrl("ftp://host", false))
    }
}
