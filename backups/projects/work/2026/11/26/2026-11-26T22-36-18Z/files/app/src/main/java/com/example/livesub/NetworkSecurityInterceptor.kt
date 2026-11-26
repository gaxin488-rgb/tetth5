package com.example.livesub

import android.content.Context
import android.net.Uri
import android.system.Os
import android.system.OsConstants
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import androidx.webkit.ProxyConfig
import androidx.webkit.ProxyController
import androidx.webkit.WebViewFeature
import okhttp3.ConnectionSpec
import okhttp3.Dns
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Protocol
import okhttp3.dnsoverhttps.DnsOverHttps
import java.io.ByteArrayInputStream
import java.io.FileDescriptor
import java.io.FilterOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketAddress
import java.net.SocketImpl
import java.net.URLEncoder
import java.security.KeyStore
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.SocketFactory
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * NetworkSecurityInterceptor: Module mạng và an ninh trình duyệt
 * 1. DpiBypassSocketFactory: Kỹ thuật TCP/TLS Record Fragmentation chẻ nhỏ gói tin ClientHello
 *    vượt tường lửa kiểm duyệt gói tin sâu (DPI) của ISP mà không cần cài app VPN.
 * 2. AdBlockAndCorsInterceptor (NetworkSecurityInterceptor):
 *    - Chặn quảng cáo, popunder, clickjacking và URL chuyển hướng độc hại.
 *    - Mở khóa nội dung qua DoH 1.1.1.1 + DPI Bypass + Multi-Geo Relay.
 *    - Inject header CORS phục vụ WebAudio capture trên HTML5 Video.
 */

// =========================================================================
// 1. KỸ THUẬT PHÂN MẢNH TLS CLIENTHELLO (SNI DPI BYPASS TRÊN MỌI ANDROID)
// =========================================================================
class FragmentedTlsSocket(val delegate: Socket) : Socket() {
    private var isFirstWrite = true

    init {
        syncImpl()
    }

    private fun syncImpl() {
        try {
            val implField = Socket::class.java.getDeclaredField("impl").apply { isAccessible = true }
            val delegateImpl = implField.get(delegate)
            if (delegateImpl != null) {
                implField.set(this, delegateImpl)
            }
        } catch (_: Exception) {}
    }

    override fun connect(endpoint: SocketAddress?) {
        delegate.tcpNoDelay = true
        delegate.connect(endpoint)
        syncImpl()
    }

    override fun connect(endpoint: SocketAddress?, timeout: Int) {
        delegate.tcpNoDelay = true
        delegate.connect(endpoint, timeout)
        syncImpl()
    }

    override fun getInputStream(): InputStream = delegate.getInputStream()

    override fun getOutputStream(): OutputStream {
        val rawOut = delegate.getOutputStream()
        return object : FilterOutputStream(rawOut) {
            override fun write(b: ByteArray, off: Int, len: Int) {
                // Kiểm tra gói tin TLS Handshake (0x16 = 22) đầu tiên (ClientHello chứa SNI)
                if (isFirstWrite && len > 20 && b[off].toInt() == 0x16) {
                    isFirstWrite = false
                    android.util.Log.i("LiveSub_Net", "✂️ [DPI BYPASS] Chẻ nhỏ TLS ClientHello (len=$len) thành 3 mảnh!")
                    // Chẻ nhỏ gói ClientHello thành 3 mảnh để tường lửa DPI của ISP không ghép được tên miền SNI
                    val split1 = 5
                    val split2 = minOf(32, len - split1)
                    rawOut.write(b, off, split1)
                    rawOut.flush()
                    rawOut.write(b, off + split1, split2)
                    rawOut.flush()
                    rawOut.write(b, off + split1 + split2, len - split1 - split2)
                    rawOut.flush()
                } else {
                    rawOut.write(b, off, len)
                }
            }
        }
    }

    override fun isConnected(): Boolean = delegate.isConnected
    override fun isClosed(): Boolean = delegate.isClosed
    override fun close() = delegate.close()
    override fun setSoTimeout(timeout: Int) { delegate.soTimeout = timeout }
    override fun getSoTimeout(): Int = delegate.soTimeout
    override fun setTcpNoDelay(on: Boolean) { delegate.tcpNoDelay = on }
    override fun getTcpNoDelay(): Boolean = delegate.tcpNoDelay
    override fun setKeepAlive(on: Boolean) { delegate.keepAlive = on }
    override fun getKeepAlive(): Boolean = delegate.keepAlive
    override fun getInetAddress(): InetAddress? = delegate.inetAddress
    override fun getLocalAddress(): InetAddress = delegate.localAddress
    override fun getPort(): Int = delegate.port
    override fun getLocalPort(): Int = delegate.localPort
    override fun getRemoteSocketAddress(): SocketAddress? = delegate.remoteSocketAddress
    override fun getLocalSocketAddress(): SocketAddress? = delegate.localSocketAddress
}

class DpiBypassPlainSocketFactory : SocketFactory() {
    private val delegate = getDefault()

    private fun wrap(s: Socket): Socket {
        try { s.tcpNoDelay = true } catch (_: Exception) {}
        return FragmentedTlsSocket(s)
    }

    override fun createSocket(): Socket = wrap(delegate.createSocket())
    override fun createSocket(host: String?, port: Int): Socket = wrap(delegate.createSocket(host, port))
    override fun createSocket(host: String?, port: Int, localHost: InetAddress?, localPort: Int): Socket =
        wrap(delegate.createSocket(host, port, localHost, localPort))
    override fun createSocket(host: InetAddress?, port: Int): Socket = wrap(delegate.createSocket(host, port))
    override fun createSocket(address: InetAddress?, port: Int, localAddress: InetAddress?, localPort: Int): Socket =
        wrap(delegate.createSocket(address, port, localAddress, localPort))
}

class DpiBypassSocketFactory(
    private val delegate: SSLSocketFactory = getDefault() as SSLSocketFactory
) : SSLSocketFactory() {

    override fun getDefaultCipherSuites(): Array<String> = delegate.defaultCipherSuites
    override fun getSupportedCipherSuites(): Array<String> = delegate.supportedCipherSuites

    override fun createSocket(s: Socket?, host: String?, port: Int, autoClose: Boolean): Socket {
        val baseSocket = if (s != null && s !is FragmentedTlsSocket) FragmentedTlsSocket(s) else s
        return delegate.createSocket(baseSocket, host, port, autoClose)
    }

    override fun createSocket(host: String?, port: Int): Socket {
        val plain = FragmentedTlsSocket(Socket())
        plain.connect(InetSocketAddress(host, port), 15000)
        return delegate.createSocket(plain, host, port, true)
    }

    override fun createSocket(host: String?, port: Int, localHost: InetAddress?, localPort: Int): Socket =
        delegate.createSocket(host, port, localHost, localPort)

    override fun createSocket(host: InetAddress?, port: Int): Socket =
        delegate.createSocket(host, port)

    override fun createSocket(address: InetAddress?, port: Int, localAddress: InetAddress?, localPort: Int): Socket =
        delegate.createSocket(address, port, localAddress, localPort)
}

internal object ProxyAddressPolicy {
    fun preferIpv4(addresses: List<InetAddress>): List<InetAddress> {
        val distinct = addresses.distinctBy { it.hostAddress }
        val ipv4 = distinct.filterIsInstance<Inet4Address>()
        return if (ipv4.isNotEmpty()) ipv4 else distinct
    }
}

// =========================================================================
// 1b. LOCAL DOH HTTP CONNECT PROXY SERVER (ĐỊNH TUYẾN CHROMIUM QUA DOH 1.1.1.1)
// =========================================================================
object LocalDohProxyServer {
    private var serverSocket: ServerSocket? = null
    @Volatile
    private var isRunning = false
    private val threadPool = Executors.newCachedThreadPool()

    @Volatile
    var localPort: Int = 0
        private set

    @Synchronized
    fun ensureStarted(): Int {
        if (isRunning && serverSocket != null && !serverSocket!!.isClosed) {
            return localPort
        }
        try {
            val ss = ServerSocket(0, 100, InetAddress.getByName("127.0.0.1"))
            serverSocket = ss
            localPort = ss.localPort
            isRunning = true
            android.util.Log.i("LiveSub_Proxy", "🚀 Local DoH Proxy đã khởi động tại 127.0.0.1:$localPort")
            threadPool.execute {
                while (isRunning) {
                    try {
                        val client = ss.accept()
                        threadPool.execute { handleClient(client) }
                    } catch (e: Exception) {
                        if (!isRunning) break
                    }
                }
            }
            return localPort
        } catch (e: Exception) {
            android.util.Log.e("LiveSub_Proxy", "Lỗi khởi động Local DoH Proxy: ${e.message}", e)
            return 0
        }
    }

    private fun readAsciiLine(input: InputStream): String? {
        val sb = StringBuilder()
        var b: Int
        while (input.read().also { b = it } != -1) {
            if (b == '\n'.code) {
                val s = sb.toString()
                return if (s.endsWith("\r")) s.substring(0, s.length - 1) else s
            }
            sb.append(b.toChar())
        }
        return if (sb.isNotEmpty()) sb.toString() else null
    }

    private fun handleClient(client: Socket) {
        var remoteSocket: Socket? = null
        try {
            client.soTimeout = 10000
            val clientIn = client.getInputStream()
            val clientOut = client.getOutputStream()

            val reqLine = readAsciiLine(clientIn) ?: run {
                android.util.Log.w("LiveSub_Proxy", "Client closed before sending reqLine")
                try { client.close() } catch (_: Exception) {}
                return
            }
            android.util.Log.i("LiveSub_Proxy", ">> $reqLine")

            val parts = reqLine.split(" ")
            if (parts.size < 2) {
                android.util.Log.w("LiveSub_Proxy", "Invalid reqLine: $reqLine")
                try { client.close() } catch (_: Exception) {}
                return
            }

            val method = parts[0]
            val target = parts[1]

            if (method.equals("CONNECT", ignoreCase = true)) {
                var hLine = readAsciiLine(clientIn)
                while (!hLine.isNullOrEmpty()) {
                    hLine = readAsciiLine(clientIn)
                }

                val hostPort = target.split(":")
                val host = hostPort[0]
                if (host.equals("livesub.local", ignoreCase = true)) {
                    try { client.close() } catch (_: Exception) {}
                    return
                }
                val port = if (hostPort.size > 1) hostPort[1].toIntOrNull() ?: 443 else 443

                val remoteIp = NetworkSecurityInterceptor.resolveDoh(host)
                android.util.Log.i("LiveSub_Proxy", "CONNECT $host:$port -> $remoteIp:$port")
                val remote = Socket()
                remoteSocket = remote
                remote.connect(InetSocketAddress(remoteIp, port), 10000)
                remote.tcpNoDelay = true

                clientOut.write("HTTP/1.1 200 Connection Established\r\n\r\n".toByteArray(Charsets.ISO_8859_1))
                clientOut.flush()

                client.soTimeout = 0
                remote.soTimeout = 0

                val remoteOut = remote.getOutputStream()

                // Phân mảnh TLS Record (ClientHello) theo RFC 5246/8446 để vượt qua kiểm duyệt SNI của ISP
                val recordHeader = ByteArray(5)
                var headerRead = 0
                while (headerRead < 5) {
                    val r = clientIn.read(recordHeader, headerRead, 5 - headerRead)
                    if (r == -1) break
                    headerRead += r
                }

                if (headerRead == 5 && recordHeader[0].toInt() == 0x16) {
                    val payloadLen = ((recordHeader[3].toInt() and 0xff) shl 8) or (recordHeader[4].toInt() and 0xff)
                    val hsPayload = ByteArray(payloadLen)
                    var payloadRead = 0
                    while (payloadRead < payloadLen) {
                        val r = clientIn.read(hsPayload, payloadRead, payloadLen - payloadRead)
                        if (r == -1) break
                        payloadRead += r
                    }
                    if (payloadRead == payloadLen) {
                        val splitAt = minOf(80, payloadLen / 2)
                        val part1 = hsPayload.copyOfRange(0, splitAt)
                        val part2 = hsPayload.copyOfRange(splitAt, payloadLen)

                        val rec1 = ByteArray(5 + part1.size)
                        rec1[0] = 0x16.toByte()
                        rec1[1] = recordHeader[1]
                        rec1[2] = recordHeader[2]
                        rec1[3] = ((part1.size shr 8) and 0xff).toByte()
                        rec1[4] = (part1.size and 0xff).toByte()
                        System.arraycopy(part1, 0, rec1, 5, part1.size)

                        val rec2 = ByteArray(5 + part2.size)
                        rec2[0] = 0x16.toByte()
                        rec2[1] = recordHeader[1]
                        rec2[2] = recordHeader[2]
                        rec2[3] = ((part2.size shr 8) and 0xff).toByte()
                        rec2[4] = (part2.size and 0xff).toByte()
                        System.arraycopy(part2, 0, rec2, 5, part2.size)

                        remoteOut.write(rec1)
                        remoteOut.flush()
                        try { Thread.sleep(20) } catch (_: Exception) {}
                        remoteOut.write(rec2)
                        remoteOut.flush()
                        android.util.Log.i("LiveSub_Proxy", "✂️ [TLS RECORD FRAGMENTATION] Đã phân mảnh ClientHello cho $host (p1=${part1.size}, p2=${part2.size})!")
                    } else {
                        remoteOut.write(recordHeader, 0, headerRead)
                        if (payloadRead > 0) remoteOut.write(hsPayload, 0, payloadRead)
                        remoteOut.flush()
                    }
                } else if (headerRead > 0) {
                    remoteOut.write(recordHeader, 0, headerRead)
                    remoteOut.flush()
                }

                pipeBidirectional(client, remote)
            } else {
                val headers = mutableListOf<String>()
                var hLine = readAsciiLine(clientIn)
                while (!hLine.isNullOrEmpty()) {
                    headers.add(hLine)
                    hLine = readAsciiLine(clientIn)
                }

                var host = ""
                var port = 80
                for (h in headers) {
                    if (h.startsWith("Host:", ignoreCase = true)) {
                        val v = h.substring(5).trim()
                        val hp = v.split(":")
                        host = hp[0]
                        if (hp.size > 1) port = hp[1].toIntOrNull() ?: 80
                        break
                    }
                }
                if (host.isEmpty()) {
                    val uri = Uri.parse(target)
                    host = uri.host ?: ""
                    if (uri.port != -1) port = uri.port
                }

                if (host.isEmpty()) {
                    try { client.close() } catch (_: Exception) {}
                    return
                }

                val remoteIp = NetworkSecurityInterceptor.resolveDoh(host)
                android.util.Log.i("LiveSub_Proxy", "HTTP $method $host:$port -> $remoteIp:$port")
                val remote = Socket()
                remoteSocket = remote
                remote.connect(InetSocketAddress(remoteIp, port), 10000)

                val cleanPath = if (target.startsWith("http://", ignoreCase = true) || target.startsWith("https://", ignoreCase = true)) {
                    val uri = Uri.parse(target)
                    val p = uri.encodedPath?.ifEmpty { "/" } ?: "/"
                    val q = uri.encodedQuery
                    if (q != null) "$p?$q" else p
                } else {
                    target
                }
                val httpVersion = if (parts.size >= 3) parts[2] else "HTTP/1.1"
                val normalizedReqLine = "$method $cleanPath $httpVersion"

                val remoteOut = remote.getOutputStream()
                remoteOut.write("$normalizedReqLine\r\n".toByteArray(Charsets.ISO_8859_1))
                for (h in headers) {
                    remoteOut.write("$h\r\n".toByteArray(Charsets.ISO_8859_1))
                }
                remoteOut.write("\r\n".toByteArray(Charsets.ISO_8859_1))
                remoteOut.flush()

                pipeBidirectional(client, remote)
            }
        } catch (e: Exception) {
            android.util.Log.e("LiveSub_Proxy", "Lỗi xử lý request proxy: ${e.message}", e)
            try { remoteSocket?.close() } catch (_: Exception) {}
            try { client.close() } catch (_: Exception) {}
        }
    }

    private fun pipeBidirectional(s1: Socket, s2: Socket) {
        val in1 = s1.getInputStream()
        val out1 = s1.getOutputStream()
        val in2 = s2.getInputStream()
        val out2 = s2.getOutputStream()

        threadPool.execute {
            try {
                val buf = ByteArray(32768)
                var n: Int
                while (in1.read(buf).also { n = it } != -1) {
                    out2.write(buf, 0, n)
                    out2.flush()
                }
            } catch (_: Exception) {}
            finally {
                try { s1.close() } catch (_: Exception) {}
                try { s2.close() } catch (_: Exception) {}
            }
        }

        threadPool.execute {
            try {
                val buf = ByteArray(32768)
                var n: Int
                while (in2.read(buf).also { n = it } != -1) {
                    out1.write(buf, 0, n)
                    out1.flush()
                }
            } catch (_: Exception) {}
            finally {
                try { s1.close() } catch (_: Exception) {}
                try { s2.close() } catch (_: Exception) {}
            }
        }
    }

    fun stop() {
        isRunning = false
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        localPort = 0
    }
}

// =========================================================================
// 2. BỘ CHẶN QUẢNG CÁO, CƠ CHẾ VƯỢT CHẶN & CORS INTERCEPTOR
// =========================================================================
object NetworkSecurityInterceptor {

    // Trạng thái của công tắc "Vượt chặn" trên giao diện (Mặc định BẬT = true)
    @Volatile
    var isUnblockEnabled: Boolean = true

    @Volatile
    var isProxyOverrideActive: Boolean = false

    @Volatile
    var currentUserAgent: String = ""

    @Volatile
    var currentMainFrameOrigin: String = ""

    private val dnsCache = ConcurrentHashMap<String, Pair<InetAddress, Long>>()

    fun resolveDoh(hostname: String): InetAddress {
        if (hostname.matches(Regex("^\\d+\\.\\d+\\.\\d+\\.\\d+$"))) {
            return InetAddress.getByName(hostname)
        }
        val cached = dnsCache[hostname]
        val now = System.currentTimeMillis()
        if (cached != null && now - cached.second < 300_000) {
            return cached.first
        }
        val ip = resolveHostAddresses(hostname).first()
        dnsCache[hostname] = Pair(ip, now)
        return ip
    }

    fun enableProxyOverride(context: Context) {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)) {
            android.util.Log.w("LiveSub_Proxy", "PROXY_OVERRIDE không hỗ trợ trên thiết bị này")
            isProxyOverrideActive = false
            return
        }
        val port = LocalDohProxyServer.ensureStarted()
        if (port <= 0) return
        val proxyConfig = ProxyConfig.Builder()
            .addProxyRule("127.0.0.1:$port")
            .addBypassRule("<-loopback>")
            .addBypassRule("livesub.local")
            .build()
        try {
            ProxyController.getInstance().setProxyOverride(
                proxyConfig,
                { command -> command.run() },
                {
                    android.util.Log.i("LiveSub_Proxy", "✅ Proxy Override đã kích hoạt qua Local DoH Proxy: 127.0.0.1:$port")
                    isProxyOverrideActive = true
                }
            )
        } catch (e: Exception) {
            android.util.Log.e("LiveSub_Proxy", "Lỗi setProxyOverride: ${e.message}", e)
        }
    }

    fun disableProxyOverride() {
        if (WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)) {
            try {
                ProxyController.getInstance().clearProxyOverride(
                    { command -> command.run() },
                    {
                        android.util.Log.i("LiveSub_Proxy", "Đã tắt Proxy Override")
                        isProxyOverrideActive = false
                    }
                )
            } catch (e: Exception) {}
        }
    }

    private fun getSystemTrustManager(): X509TrustManager {
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(null as KeyStore?)
        return tmf.trustManagers.first { it is X509TrustManager } as X509TrustManager
    }

    private val bootstrapClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    // 1. DNS-over-HTTPS chuẩn 1.1.1.1 của Cloudflare
    private val cloudflareDohDns = DnsOverHttps.Builder()
        .client(bootstrapClient)
        .url("https://1.1.1.1/dns-query".toHttpUrl())
        .bootstrapDnsHosts(
            InetAddress.getByName("1.1.1.1"),
            InetAddress.getByName("1.0.0.1"),
            InetAddress.getByName("162.159.36.1")
        )
        .build()

    private fun resolveHostAddresses(hostname: String): List<InetAddress> {
        val dohAnswers = runCatching { cloudflareDohDns.lookup(hostname) }.getOrDefault(emptyList())
        if (dohAnswers.any { it is Inet4Address }) {
            return ProxyAddressPolicy.preferIpv4(dohAnswers)
        }
        val systemAnswers = runCatching { InetAddress.getAllByName(hostname).toList() }.getOrDefault(emptyList())
        val candidates = ProxyAddressPolicy.preferIpv4(dohAnswers + systemAnswers)
        if (candidates.isEmpty()) throw java.net.UnknownHostException(hostname)
        return candidates
    }

    // 2. OkHttpClient kết hợp DoH 1.1.1.1 + Định tuyến qua Local DoH Proxy (TLS Record Fragmentation)
    // Bật cả HTTP/2 và HTTP/1.1 để khớp 100% TLS ALPN fingerprint của trình duyệt Chrome thật
    private val unlockedHttpClient by lazy {
        val proxyPort = LocalDohProxyServer.ensureStarted()
        val builder = bootstrapClient.newBuilder()
            .dns(object : Dns {
                override fun lookup(hostname: String): List<InetAddress> = resolveHostAddresses(hostname)
            })
            .connectionSpecs(listOf(ConnectionSpec.MODERN_TLS, ConnectionSpec.COMPATIBLE_TLS, ConnectionSpec.CLEARTEXT))
            .protocols(listOf(Protocol.HTTP_2, Protocol.HTTP_1_1))
            .followRedirects(true)
            .followSslRedirects(true)

        if (proxyPort > 0) {
            builder.proxy(java.net.Proxy(java.net.Proxy.Type.HTTP, InetSocketAddress("127.0.0.1", proxyPort)))
        }
        builder.build()
    }

    // CHỈ để các domain quốc tế KHÔNG BỊ NHÀ MẠNG CHẶN chạy trực tiếp.
    // Đã xóa missav, jable, spankbang, subav... khỏi đây để chúng bắt buộc đi qua DoH 1.1.1.1 + DPI Bypass!
    private val NATIVE_BYPASS_DOMAINS = setOf(
        "google.com", "google.com.vn", "gstatic.com", "googleapis.com",
        "youtube.com", "ytimg.com", "googlevideo.com", "translate.goog",
        "challenges.cloudflare.com", "cloudflare.com", "hcaptcha.com", "recaptcha.net"
    )

    // CÁC TRANG BỊ CLOUDFRONT / CDN GEO-BLOCK THEO IP
    private val CLOUDFRONT_GEOBLOCK_DOMAINS = setOf(
        "setn.com", "www.setn.com",
        "ctv.ca", "www.ctv.ca",
        "nbcnews.com", "www.nbcnews.com",
        "abcnews.go.com",
        "hulu.com", "www.hulu.com"
    )

    // Danh sách chặn quảng cáo & máy chủ phát luồng Live Cam góc màn hình
    private val AD_DOMAINS = setOf(
        "doubleclick.net", "googlesyndication.com", "googleadservices.com",
        "pagead2.googlesyndication.com", "adservice.google.com", "imasdk.googleapis.com",
        "adnxs.com", "adsrvr.org", "criteo.com", "outbrain.com", "taboola.com",
        "popads.net", "popcash.net", "juicyads.com", "exoclick.com", "propellerads.com",
        "adsterra.com", "trafficjunky.net", "tsyndicate.com", "realsrv.com",
        // Máy chủ script & CDN phát video Live Cam (MissAV / Stripchat / ExoClick)
        "magsrv.com", "pemsrv.com", "doppiocdn.com", "doppiocdn.org", "doppiocdn.net",
        "bcvcdn.com", "strpst.com", "xlirdr.com", "stripchat.com", "chaturbate.com",
        "livejasmin.com", "bongacams.com", "cam4.com", "myavlive.com", "twinrdsyn.com",
        "sunnyads.com", "a-ads.com", "oyalun.com", "aucdn.net",
        "growcdnssedge.com", "superbcdn.com", "tzegilo.com", "wpadm.com", "etahub.com",
        "greentrafsa.com", "mayzaent.com", "snaptrckr.fun", "stripchats.io", "stripcdn.com",
        "chaturbates.org", "camsoda.com", "camversity.com", "tsyndicate.net",
        "rallytrck.website", "t.rallytrck.website", "adtng.com", "engine.adglare.net",
        "ad-score.com", "trafficfactory.biz", "twistcdn.com"
    )

    private val AD_PATH_KEYWORDS = listOf(
        "/pagead/", "/ptracking", "/api/stats/ads", "/get_midroll_", "ad_break", "/vast.xml",
        "/inpage-push", "/outstream", "stripchat", "chaturbate", "live-cams", "growcdn", "superbcdn",
        "/smartpop", "/api/models", "videoSlider", "trafficType=", "smartpopId=", "snaptrckr", "mayzaent", "greentrafsa",
        "rallytrck", "/Spot/lib.js", "StripchatSpot", "/widgets/v4/Universal", "SCSpotScript", "campaignId=under_player", "campaignId=inpage"
    )

    fun intercept(request: WebResourceRequest): WebResourceResponse? {
        val urlStr = request.url.toString()
        val scheme = request.url.scheme?.lowercase() ?: ""
        if (scheme != "http" && scheme != "https") {
            return null
        }
        val host = request.url.host?.lowercase() ?: ""

        if (request.isForMainFrame && host.isNotEmpty()) {
            currentMainFrameOrigin = "${request.url.scheme}://${request.url.host}"
        }

        // BƯỚC 1: Chặn quảng cáo
        if (isAdRequest(host, urlStr)) {
            android.util.Log.i("LiveSub_AdBlock", "🛡️ [Network Interceptor] Đã chặn quảng cáo: $urlStr (host: $host)")
            return createEmptyResponse()
        }

        // Keep HTML-facing assets in the Chromium network/cookie pipeline.
        // Re-fetching CSS/JS/fonts through a separate OkHttp client can lose
        // resource authentication and may make a 403 HTML page look like valid
        // text/css because fetchViaDohAndDpiBypass guesses MIME by extension.
        // Native WebView must own MIME, cookies, redirects and CSP for UI assets.
        val resourcePath = request.url.path?.lowercase(java.util.Locale.ROOT).orEmpty()
        val isPageAsset = listOf(".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".otf").any {
            resourcePath.endsWith(it)
        }
        if (!request.isForMainFrame && request.method.equals("GET", ignoreCase = true) && isPageAsset) {
            android.util.Log.d(
                "LiveSub_WebUi",
                "Native asset request host=" + host + " kind=" +
                    resourcePath.substringAfterLast('.').take(8)
            )
            return null
        }

        // Preserve first-party page navigation, scripts, styles, cookie sessions and
        // Cloudflare verification in the real Android WebView. Fetching the main HTML
        // using a different OkHttp/TLS client while its CSS/JS load via Chromium splits
        // the browser session and can strand a fully unstyled page (HTTP 403 assets).
        // HLS/media requests remain handled by the existing media pipeline below.
        if (PopupAdPolicy.isTargetSite(host) &&
            !shouldInjectCorsForMedia(request, urlStr) &&
            (request.method.equals("GET", ignoreCase = true) ||
             request.method.equals("POST", ignoreCase = true))) {
            return null
        }

        // BƯỚC 1b: KHI PROXY OVERRIDE ĐANG HOẠT ĐỘNG
        // Toàn bộ request (HTML, CSS, JS, Fonts, Ảnh, Video) đã được Chromium native định tuyến qua Local DoH Proxy
        // với đầy đủ bảo mật TLS 1.3 và Cookie Cloudflare.
        // Trả về null để Chromium tự tải trực tiếp qua HTTP/2, không bao giờ vỡ CSS!
        if (isProxyOverrideActive) {
            if (shouldInjectCorsForMedia(request, urlStr)) {
                return fetchViaDohAndDpiBypass(request, urlStr)
            }
            return null
        }

        // BƯỚC 2: Không can thiệp vào Google/YouTube/Cloudflare challenges CDN, token xác minh và toàn bộ endpoint /cdn-cgi/
        if (isNativeBypassDomain(host) || urlStr.contains("/cdn-cgi/") || urlStr.contains("__cf_chl") || host.contains("cloudflare.com") || host.contains("challenges.cloudflare.com")) {
            return null
        }

        // BƯỚC 3: Xử lý CORS Preflight (OPTIONS)
        if (request.method.equals("OPTIONS", ignoreCase = true)) {
            return WebResourceResponse(
                "text/plain", "UTF-8", 200, "OK",
                buildCorsHeaders(request),
                ByteArrayInputStream(ByteArray(0))
            )
        }

        // BƯỚC 4: Khi bật "Vượt chặn" (1.1.1.1 DoH + DPI Bypass), dẫn hướng TOÀN BỘ request GET
        // (bao gồm cả MainFrame, JS, CSS, Ảnh, M3U8) của trang web bị chặn đi qua unlockedHttpClient
        // ngoại trừ các đoạn mã giải Cloudflare Turnstile (/cdn-cgi/, __cf_chl).
        val isCfChallengeEndpoint = urlStr.contains("__cf_chl") || urlStr.contains("/cdn-cgi/")
        if (request.method.equals("GET", ignoreCase = true) && !isCfChallengeEndpoint) {
            if (isUnblockEnabled || shouldInjectCorsForMedia(request, urlStr)) {
                return fetchViaDohAndDpiBypass(request, urlStr)
            }
        }

        return null
    }

    fun isAdRequest(host: String, fullUrl: String): Boolean {
        if (AD_DOMAINS.any { host == it || host.endsWith(".$it") }) return true
        val lowerUrl = fullUrl.lowercase()
        return AD_PATH_KEYWORDS.any { lowerUrl.contains(it) }
    }

    private val REDIRECT_AD_KEYWORDS = listOf(
        "shopee.vn", "lazada.vn", "tiki.vn", "s.shopee", "s.lazada",
        "bet88", "fun88", "w88", "fb88", "m88", "kubet", "jun88", "hi88", "789bet", "new88", "shbet",
        "sunwin", "go88", "rikvip", "b52", "hitclub",
        "shorte.st", "adf.ly", "ouo.io", "bit.ly", "tinyurl.com",
        "chaturbate", "stripchat", "livejasmin", "bongacams", "cam4",
        "click.", "track.", "aff.", "partner.", "promo.", "/ads/", "popunder",
        "/pop?url=", "/pop?", "twinrd", "tsyndicate", "greentrafsa", "mayzaent", "snaptrckr", "rallytrck"
    )

    fun isSuspiciousRedirectUrl(host: String, fullUrl: String): Boolean {
        if (isAdRequest(host, fullUrl)) return true
        val lowerUrl = fullUrl.lowercase()
        if (AD_DOMAINS.any { lowerUrl.contains(it) }) return true
        return REDIRECT_AD_KEYWORDS.any { lowerUrl.contains(it) }
    }

    fun isNativeBypassDomain(host: String): Boolean {
        return NATIVE_BYPASS_DOMAINS.any { host == it || host.endsWith(".$it") }
    }

    fun isGeoblockedDomain(host: String): Boolean {
        return CLOUDFRONT_GEOBLOCK_DOMAINS.any { host == it || host.endsWith(".$it") }
    }

    private fun shouldInjectCorsForMedia(request: WebResourceRequest, urlStr: String): Boolean {
        val hasOriginHeader = request.requestHeaders.keys.any { it.equals("Origin", ignoreCase = true) }
        val uri = Uri.parse(urlStr)
        val host = uri.host?.lowercase() ?: ""
        val path = uri.path?.lowercase() ?: ""
        val isMediaCdn = host.contains("fourhoi") || host.contains("sixyik") || host.contains("surrit") || host.contains("fivetiu")
        return path.endsWith(".m3u8") || path.endsWith(".ts") || path.endsWith(".m4s") ||
                path.endsWith(".mpd") || path.endsWith(".aac") ||
                (isMediaCdn && (path.endsWith(".jpeg") || path.endsWith(".jpg"))) ||
                (hasOriginHeader && (path.endsWith(".mp4") || path.endsWith(".webm")))
    }

    private fun fetchViaDohAndDpiBypass(request: WebResourceRequest, urlStr: String): WebResourceResponse? {
        return try {
            val okRequestBuilder = Request.Builder().url(urlStr)

            // Sao chép headers từ WebView
            for ((key, value) in request.requestHeaders) {
                if (!key.equals("Accept-Encoding", ignoreCase = true) &&
                    !key.equals("Origin", ignoreCase = true) &&
                    !key.equals("User-Agent", ignoreCase = true)
                ) {
                    okRequestBuilder.addHeader(key, value)
                }
            }

            // 1. Luôn bảo đảm User-Agent Chrome chuẩn khớp 100% với WebView (tránh lệch dấu vân tay với Cloudflare)
            val defaultUa = if (currentUserAgent.isNotEmpty()) currentUserAgent else "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
            val userAgent = request.requestHeaders.entries.find { it.key.equals("User-Agent", ignoreCase = true) }?.value ?: defaultUa
            okRequestBuilder.header("User-Agent", userAgent)

            val host = request.url.host?.lowercase() ?: ""
            val isMediaCdn = host.contains("fourhoi") || host.contains("sixyik") || host.contains("surrit") || host.contains("fivetiu")
            val path = Uri.parse(urlStr).path?.lowercase() ?: ""
            val isMediaRequest = isMediaCdn || path.endsWith(".m3u8") || path.endsWith(".ts") || path.endsWith(".m4s") || path.endsWith(".mpd")

            // 2. Client Hints & Headers chuẩn của Chrome (chỉ cho các request web thông thường, CDN media tránh sec-ch-ua)
            if (!isMediaCdn) {
                val chromeVer = userAgent.substringAfter("Chrome/").substringBefore(".").ifEmpty { "126" }
                if (!request.requestHeaders.keys.any { it.equals("sec-ch-ua", ignoreCase = true) }) {
                    okRequestBuilder.header("sec-ch-ua", "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"$chromeVer\", \"Google Chrome\";v=\"$chromeVer\"")
                    okRequestBuilder.header("sec-ch-ua-mobile", "?1")
                    okRequestBuilder.header("sec-ch-ua-platform", "\"Android\"")
                }
                if (!request.requestHeaders.keys.any { it.equals("Accept-Language", ignoreCase = true) }) {
                    okRequestBuilder.header("Accept-Language", "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7")
                }
            }

            if (isMediaRequest) {
                okRequestBuilder.header("Accept", "*/*")
            } else if (!request.requestHeaders.keys.any { it.equals("Accept", ignoreCase = true) }) {
                val acceptVal = when {
                    path.endsWith(".css") -> "text/css,*/*;q=0.1"
                    path.endsWith(".js") -> "*/*"
                    path.endsWith(".png") || path.endsWith(".jpg") || path.endsWith(".webp") || path.endsWith(".svg") ->
                        "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
                    else -> "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
                }
                okRequestBuilder.header("Accept", acceptVal)
            }

            // 3. Đồng bộ Cookie từ CookieManager (bao gồm cả Cookie cấp theo tên miền gốc)
            val cookieManager = CookieManager.getInstance()
            val urlCookies = cookieManager.getCookie(urlStr)
            val hostCookies = if (host.isNotEmpty()) cookieManager.getCookie("https://$host") else null
            val combinedCookies = when {
                !urlCookies.isNullOrEmpty() && !hostCookies.isNullOrEmpty() && urlCookies != hostCookies -> "$urlCookies; $hostCookies"
                !urlCookies.isNullOrEmpty() -> urlCookies
                !hostCookies.isNullOrEmpty() -> hostCookies
                else -> null
            }
            if (!combinedCookies.isNullOrEmpty()) {
                okRequestBuilder.header("Cookie", combinedCookies)
            }

            // 4. Referer (Media CDNs như fourhoi/sixyik/surrit yêu cầu Referer từ trang phim chính)
            if (isMediaCdn) {
                if (currentMainFrameOrigin.isNotBlank()) {
                    okRequestBuilder.header("Referer", "$currentMainFrameOrigin/")
                }
            } else if (!request.requestHeaders.keys.any { it.equals("Referer", ignoreCase = true) }) {
                val referer = if (currentMainFrameOrigin.isNotEmpty()) "$currentMainFrameOrigin/" else "https://$host/"
                okRequestBuilder.header("Referer", referer)
            }

            val response = unlockedHttpClient.newCall(okRequestBuilder.build()).execute()
            android.util.Log.d("LiveSub_Net", "FETCH: $urlStr -> code ${response.code} (mainFrame=${request.isForMainFrame})")

            // 5. Nếu gặp Cloudflare Challenge (403/503) trên trang chính -> Bàn giao ngay cho Chromium native
            val isCloudflareChallenge = (response.code == 403 || response.code == 503) && (
                response.header("cf-mitigated") != null ||
                response.header("cf-ray") != null ||
                response.header("Server")?.contains("cloudflare", ignoreCase = true) == true
            )
            if (isCloudflareChallenge) {
                android.util.Log.i("LiveSub_Net", "🛡️ Cloudflare Challenge trên $urlStr -> Bàn giao trực tiếp cho Chromium native")
                response.close()
                return null
            }

            // 5b. Nếu gặp CloudFront Geoblock (403/451) trên trang chính -> Dùng MultiGeo Relay
            if ((response.code == 403 || response.code == 451) && request.isForMainFrame) {
                response.close()
                return fetchViaMultiGeoRelay(urlStr, request)
            }

            // 6. Nếu Subresource trả về lỗi (403, 404, 500...) -> Trả về null để Chromium native tự tải
            // Tuyệt đối không trả về CSS/JS rỗng vì sẽ làm mất sạch giao diện (vỡ CSS)
            if (!request.isForMainFrame && response.code >= 400) {
                android.util.Log.w("LiveSub_Net", "Subresource $urlStr trả về ${response.code} -> Fallback sang Chromium native")
                response.close()
                return null
            }

            val body = response.body ?: run {
                response.close()
                return null
            }

            // 7. Lưu lại Set-Cookie vào CookieManager và flush xuống đĩa
            var hasNewCookie = false
            val finalUrl = response.request.url.toString()
            for (cookie in response.headers("Set-Cookie")) {
                cookieManager.setCookie(urlStr, cookie)
                if (finalUrl != urlStr) {
                    cookieManager.setCookie(finalUrl, cookie)
                }
                hasNewCookie = true
            }
            if (hasNewCookie) {
                cookieManager.flush()
            }

            // 8. Định danh MIME type chính xác tuyệt đối theo đuôi file
            val mimeType = when {
                path.endsWith(".css") -> "text/css"
                path.endsWith(".js") -> "application/javascript"
                path.endsWith(".json") -> "application/json"
                path.endsWith(".png") -> "image/png"
                path.endsWith(".jpg") || path.endsWith(".jpeg") -> "image/jpeg"
                path.endsWith(".webp") -> "image/webp"
                path.endsWith(".svg") -> "image/svg+xml"
                path.endsWith(".woff2") -> "font/woff2"
                path.endsWith(".woff") -> "font/woff"
                path.endsWith(".ttf") -> "font/ttf"
                path.endsWith(".m3u8") -> "application/vnd.apple.mpegurl"
                path.endsWith(".ts") -> "video/mp2t"
                path.endsWith(".mp4") -> "video/mp4"
                else -> {
                    val contentTypeHeader = response.header("Content-Type") ?: guessMimeType(urlStr)
                    contentTypeHeader.substringBefore(";").trim()
                }
            }
            val contentTypeHeader = response.header("Content-Type") ?: ""
            val charset = if (contentTypeHeader.contains("charset=")) {
                contentTypeHeader.substringAfter("charset=", "UTF-8").substringBefore(";").trim()
            } else {
                "UTF-8"
            }

            val responseHeaders = mutableMapOf<String, String>()
            for (name in response.headers.names()) {
                if (!name.equals("Content-Security-Policy", ignoreCase = true) &&
                    !name.equals("Content-Encoding", ignoreCase = true) &&
                    !name.equals("X-Frame-Options", ignoreCase = true) &&
                    !name.startsWith("Access-Control-", ignoreCase = true)
                ) {
                    responseHeaders[name] = response.headers.values(name).joinToString(", ")
                }
            }
            for ((k, v) in buildCorsHeaders(request)) {
                responseHeaders[k] = v
            }
            val statusCode = response.code
            val reasonPhrase = response.message.ifEmpty { if (statusCode == 206) "Partial Content" else "OK" }

            val contentLength = body.contentLength()
            val isSmallOrChunk = (contentLength in 1..(10 * 1024 * 1024)) ||
                    path.endsWith(".jpeg") || path.endsWith(".jpg") || path.endsWith(".png") ||
                    path.endsWith(".m3u8") || path.endsWith(".ts") || path.endsWith(".m4s")

            val inputStream = if (isSmallOrChunk) {
                val bytes = body.bytes()
                response.close()
                ByteArrayInputStream(bytes)
            } else {
                body.byteStream()
            }

            WebResourceResponse(
                mimeType,
                charset,
                statusCode,
                reasonPhrase,
                responseHeaders,
                inputStream
            )
        } catch (e: Exception) {
            android.util.Log.e("LiveSub_Net", "FETCH ERROR for $urlStr: ${e.message}", e)
            if (request.isForMainFrame) {
                return fetchViaMultiGeoRelay(urlStr, request)
            }
            null
        }
    }

    private fun fetchViaMultiGeoRelay(originalUrl: String, request: WebResourceRequest): WebResourceResponse? {
        val encodedUrl = URLEncoder.encode(originalUrl, "UTF-8")
        val relayEndpoints = listOf(
            "https://corsproxy.io/?url=$encodedUrl",
            "https://api.codetabs.com/v1/proxy?quest=$encodedUrl",
            "https://api.allorigins.win/raw?url=$encodedUrl"
        )

        for (relayUrl in relayEndpoints) {
            try {
                val relayReq = Request.Builder()
                    .url(relayUrl)
                    .header("User-Agent", request.requestHeaders["User-Agent"] ?: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36")
                    .build()

                unlockedHttpClient.newCall(relayReq).execute().use { relayResp ->
                    if (relayResp.isSuccessful) {
                        val rawHtml = relayResp.body?.string() ?: ""
                        if (rawHtml.isNotEmpty() && !rawHtml.contains("The Amazon CloudFront distribution is configured to block access")) {
                            val originBase = "${request.url.scheme}://${request.url.host}/"
                            val fixedHtml = if (rawHtml.contains("<head>", ignoreCase = true)) {
                                rawHtml.replaceFirst(Regex("(?i)<head>"), "<head><base href=\"$originBase\">")
                            } else {
                                "<base href=\"$originBase\">$rawHtml"
                            }

                            return WebResourceResponse(
                                "text/html",
                                "UTF-8",
                                200,
                                "OK",
                                buildCorsHeaders(request),
                                ByteArrayInputStream(fixedHtml.toByteArray(Charsets.UTF_8))
                            )
                        }
                    }
                }
            } catch (_: Exception) {}
        }
        return null
    }

    fun buildGoogleTunnelUrl(originalUrl: String): String? {
        return try {
            val uri = Uri.parse(originalUrl)
            val host = uri.host ?: return null
            if (host.endsWith(".translate.goog")) return null
            val encodedHost = host.replace("-", "--").replace(".", "-")
            val pathAndQuery = originalUrl.substringAfter(host)
            val separator = if (pathAndQuery.contains("?")) "&" else "?"
            "https://$encodedHost.translate.goog$pathAndQuery${separator}_x_tr_sl=auto&_x_tr_tl=vi&_x_tr_hl=vi&_x_tr_pto=wapp"
        } catch (_: Exception) {
            null
        }
    }

    fun buildGoogleTranslateUrl(urlStr: String): String {
        return buildGoogleTunnelUrl(urlStr) ?: urlStr
    }

    fun extractOriginalUrlFromTranslate(translateUrl: String): String {
        return try {
            val uri = Uri.parse(translateUrl)
            val host = uri.host ?: return translateUrl
            if (!host.endsWith(".translate.goog")) return translateUrl

            val encodedHost = host.removeSuffix(".translate.goog")
            val originalHost = encodedHost.replace("--", "\u0000").replace("-", ".").replace("\u0000", "-")

            val path = uri.path ?: ""
            val queryParams = mutableListOf<String>()
            for (queryName in uri.queryParameterNames) {
                if (!queryName.startsWith("_x_tr_")) {
                    for (valStr in uri.getQueryParameters(queryName)) {
                        queryParams.add("$queryName=${URLEncoder.encode(valStr, "UTF-8")}")
                    }
                }
            }
            val queryString = if (queryParams.isNotEmpty()) "?${queryParams.joinToString("&")}" else ""
            "https://$originalHost$path$queryString"
        } catch (_: Exception) {
            translateUrl
        }
    }

    private fun buildCorsHeaders(request: WebResourceRequest): Map<String, String> {
        val requestOrigin = request.requestHeaders["Origin"]
            ?: request.requestHeaders["origin"]
            ?: "${request.url.scheme}://${request.url.host}"

        return mapOf(
            "Access-Control-Allow-Origin" to requestOrigin,
            "Access-Control-Allow-Methods" to "GET, POST, OPTIONS, HEAD",
            "Access-Control-Allow-Headers" to "*",
            "Access-Control-Allow-Credentials" to "true",
            "Access-Control-Max-Age" to "86400",
            "Timing-Allow-Origin" to "*"
        )
    }

    private fun guessMimeType(url: String): String {
        val path = Uri.parse(url).path?.lowercase() ?: ""
        return when {
            path.endsWith(".html") || path.endsWith(".htm") -> "text/html"
            path.endsWith(".js") -> "application/javascript"
            path.endsWith(".css") -> "text/css"
            path.endsWith(".m3u8") -> "application/vnd.apple.mpegurl"
            path.endsWith(".ts") -> "video/mp2t"
            path.endsWith(".m4s") || path.endsWith(".mp4") -> "video/mp4"
            path.endsWith(".webm") -> "video/webm"
            path.endsWith(".jpg") || path.endsWith(".jpeg") -> "image/jpeg"
            path.endsWith(".png") -> "image/png"
            path.endsWith(".gif") -> "image/gif"
            path.endsWith(".webp") -> "image/webp"
            path.endsWith(".svg") -> "image/svg+xml"
            path.endsWith(".json") -> "application/json"
            else -> "text/html"
        }
    }

    private fun createEmptyResponse(): WebResourceResponse {
        return WebResourceResponse(
            "text/plain",
            "UTF-8",
            200,
            "OK",
            mapOf("Cache-Control" to "no-cache"),
            ByteArrayInputStream(ByteArray(0))
        )
    }
}

// Giữ lại typealias để tương thích ngược 100% với toàn bộ mã nguồn hiện tại
typealias AdBlockAndCorsInterceptor = NetworkSecurityInterceptor
