package com.example.livesub

import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebView
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.URI
import java.util.UUID
import java.util.concurrent.atomic.AtomicLong

/**
 * Reads normal CORS-permitted media through the user's already open
 * video page. The browser enforces the source's CORS policy.
 * Only one bounded request is allowed at a time; transfer is chunked.
 */
internal class HlsBrowserSegmentReader(private val webView: WebView) {
    private val lock = Any()
    private val token = UUID.randomUUID().toString()
    private val sequence = AtomicLong(1L)
    private data class Pending(
        val id: Long,
        val url: String,
        val maxBytes: Int,
        val data: ByteArrayOutputStream,
        val response: CompletableDeferred<ByteArray>
    )
    private var pending: Pending? = null

    fun isFetching(url: String): Boolean = synchronized(lock) { pending?.url == url }

    suspend fun fetch(url: String, maxBytes: Int, page: String): ByteArray = withTimeout(45_000L) {
        check(canFetch(url, page)) { "WebView không hỗ trợ nguồn HLS này" }
        val origin = "https://" + URI(page).host.lowercase(java.util.Locale.ROOT)
        val id = sequence.incrementAndGet()
        val deferred = CompletableDeferred<ByteArray>()
        synchronized(lock) {
            check(pending == null) { "Another browser media request is still active" }
            pending = Pending(id, url, maxBytes, ByteArrayOutputStream(), deferred)
        }
        try {
            withContext(Dispatchers.Main) {
                check(webView.url == page) {
                    "Trang video đã thay đổi"
                }
                val script = """
                    (async function() {
                      const bridge = window.LiveSubHlsBridge;
                      if (!bridge) return;
                      const secret = ${JSONObject.quote(token)};
                      const requestId = $id;
                      if (location.origin !== ${JSONObject.quote(origin)} ||
                          location.href !== ${JSONObject.quote(page)} ||
                          typeof window._cf_chl_opt !== 'undefined' ||
                          document.querySelector('input[name="cf-turnstile-response"]')) {
                        bridge.onFailed(secret, requestId, 'Page origin not permitted');
                        return;
                      }
                      try {
                        const reply = await fetch(${JSONObject.quote(url)}, {
                          method: 'GET', mode: 'cors', credentials: 'omit', redirect: 'error'
                        });
                        if (!reply.ok) throw new Error('HTTP ' + reply.status);
                        const binary = new Uint8Array(await reply.arrayBuffer());
                        if (binary.length > $maxBytes) throw new Error('Segment too large');
                        const size = 24576;
                        for (let k = 0; k < binary.length; k += size) {
                          const part = binary.subarray(k, k + size);
                          const encoded = btoa(String.fromCharCode.apply(null, part));
                          bridge.onChunk(secret, requestId, encoded, k + size >= binary.length);
                        }
                        if (binary.length === 0) bridge.onFailed(secret, requestId, 'Empty response');
                      } catch (err) {
                        bridge.onFailed(secret, requestId, String(err.message || 'Fetch failed').slice(0, 120));
                      }
                    })();
                """.trimIndent()
                webView.evaluateJavascript(script, null)
            }
            deferred.await()
        } finally {
            synchronized(lock) {
                if (pending?.id == id) pending = null
            }
        }
    }

    @JavascriptInterface
    fun onChunk(providedToken: String, requestId: Long, base64: String, finalChunk: Boolean) {
        if (providedToken != token || base64.length > 40000) return
        val bytes = try { Base64.decode(base64, Base64.DEFAULT) } catch (_: Exception) { return }
        synchronized(lock) {
            val task = pending ?: return
            if (task.id != requestId || task.response.isCompleted) return
            if (task.data.size() + bytes.size > task.maxBytes) {
                task.response.completeExceptionally(IllegalArgumentException("Media segment too large"))
                return
            }
            task.data.write(bytes)
            if (finalChunk) task.response.complete(task.data.toByteArray())
        }
    }

    @JavascriptInterface
    fun onFailed(providedToken: String, requestId: Long, message: String) {
        if (providedToken != token) return
        synchronized(lock) {
            val task = pending ?: return
            if (task.id == requestId && !task.response.isCompleted)
                task.response.completeExceptionally(
                    IllegalStateException("Browser CORS fetch unavailable: " + message.take(120))
                )
        }
    }

    companion object {
        private fun httpsHost(url: String): String? {
            val uri = runCatching { URI(url) }.getOrNull() ?: return null
            if (uri.scheme != "https" || uri.userInfo != null || uri.port !in listOf(-1, 443)) return null
            return uri.host?.lowercase(java.util.Locale.ROOT)
        }

        private fun isTrustedMediaHost(host: String?): Boolean =
            host == "surrit.com" || host?.endsWith(".surrit.com") == true

        fun canFetch(url: String, page: String): Boolean =
            isTrustedMediaHost(httpsHost(url)) &&
                httpsHost(page) in setOf("missav.ai", "missav.ws")
    }
}
