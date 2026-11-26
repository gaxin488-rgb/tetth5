package com.example.livesub

import org.junit.Assert.*
import kotlinx.coroutines.async
import org.junit.Test
import java.net.InetAddress
import java.net.SocketTimeoutException

class HlsMediaAccessPolicyTest {
    @Test fun accessDeniedNeverRetried() {
        for (code in listOf(401,403,404)) {
            assertNull(HlsMediaAccessPolicy.retryDelay(HlsMediaHttpException(code),0))
        }
        assertEquals("access_denied",HlsMediaAccessPolicy.reason(HlsMediaHttpException(403)))
    }
    @Test fun throttlingHonorsServerDelayOrStops() {
        assertEquals(30000L,HlsMediaAccessPolicy.retryDelay(HlsMediaHttpException(429),0))
        assertEquals(45000L,HlsMediaAccessPolicy.retryDelay(HlsMediaHttpException(429,45000),0))
        assertNull(HlsMediaAccessPolicy.retryDelay(HlsMediaHttpException(429,61000),0))
        assertNull(HlsMediaAccessPolicy.retryDelay(HlsMediaHttpException(429),2))
    }
    @Test fun transientFailuresBounded() {
        assertEquals(1000L,HlsMediaAccessPolicy.retryDelay(HlsMediaHttpException(503),0))
        assertEquals(2000L,HlsMediaAccessPolicy.retryDelay(SocketTimeoutException(),1))
        assertNull(HlsMediaAccessPolicy.retryDelay(SocketTimeoutException(),2))
        assertEquals("timeout",HlsMediaAccessPolicy.reason(SocketTimeoutException()))
        assertEquals("server_error",HlsMediaAccessPolicy.reason(HlsMediaHttpException(503)))
    }
    @Test fun fetchCadenceMatchesMediaDuration() {
        assertEquals(2000L,HlsMediaAccessPolicy.cadenceMs(4.0))
        assertEquals(1000L,HlsMediaAccessPolicy.cadenceMs(0.3))
    }
    @Test fun retryAfterSecondsAndHttpDate() {
        assertEquals(45000L,HlsMediaAccessPolicy.parseRetryAfter("45",0))
        assertEquals(30000L,HlsMediaAccessPolicy.parseRetryAfter("Thu, 01 Jan 1970 00:00:30 GMT",0))
        assertNull(HlsMediaAccessPolicy.parseRetryAfter("invalid",0))
        assertEquals(Long.MAX_VALUE,HlsMediaAccessPolicy.parseRetryAfter("999999999999999999999",0))
    }

    @Test fun prefetchFailureIsAValueAndDoesNotCancelSibling() = kotlinx.coroutines.runBlocking {
        val channel = kotlinx.coroutines.channels.Channel<Int>(2)
        val sibling = async { channel.receive() }
        val failed = async {
            HlsMediaAccessPolicy.captureFetch<Int> {
                throw HlsMediaPausedException("access_denied", HlsMediaHttpException(403))
            }
        }
        assertTrue(failed.await().exceptionOrNull() is HlsMediaPausedException)
        channel.send(42)
        assertEquals(42, sibling.await())
        channel.close()
        Unit
    }

    @Test fun browserSegmentReaderAllowsOnlyHttpsSurritHostsOnMissavPages() {
        val page = "https://missav.ai/vi/arso-26212"
        assertTrue(HlsBrowserSegmentReader.canFetch("https://surrit.com/film/video.m3u8", page))
        assertTrue(HlsBrowserSegmentReader.canFetch("https://c.surrit.com/film/video.m3u8", page))
        assertTrue(HlsBrowserSegmentReader.canFetch("https://c.surrit.com/film/video.m3u8", "https://missav.ws/vi/video"))
        assertFalse(HlsBrowserSegmentReader.canFetch("https://surrit.com.evil.example/film/video.m3u8", page))
        assertFalse(HlsBrowserSegmentReader.canFetch("https://evil-surrit.com/film/video.m3u8", page))
        assertFalse(HlsBrowserSegmentReader.canFetch("http://c.surrit.com/film/video.m3u8", page))
        assertFalse(HlsBrowserSegmentReader.canFetch("https://user@c.surrit.com/film/video.m3u8", page))
        assertFalse(HlsBrowserSegmentReader.canFetch("https://c.surrit.com/film/video.m3u8", "https://example.org/video"))
    }

    @Test fun proxyAddressPolicyPrefersIpv4WhenAvailable() {
        val ipv6 = InetAddress.getByName("2001:db8::1")
        val ipv4 = InetAddress.getByName("192.0.2.10")
        assertEquals(listOf(ipv4), ProxyAddressPolicy.preferIpv4(listOf(ipv6, ipv4, ipv4)))
        assertEquals(listOf(ipv6), ProxyAddressPolicy.preferIpv4(listOf(ipv6)))
    }
}
