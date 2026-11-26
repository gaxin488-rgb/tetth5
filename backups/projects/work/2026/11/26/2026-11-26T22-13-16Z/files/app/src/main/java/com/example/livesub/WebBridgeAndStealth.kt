package com.example.livesub

import android.util.Base64
import android.webkit.JavascriptInterface
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * WebBridgeAndStealth: Cầu nối JavaScript và Cơ chế ngụy trang trình duyệt
 * 1. WebAudioTapBridge: JavascriptInterface trích xuất luồng âm thanh HTML5 Video
 *    qua AudioContext và bộ lọc BiquadFilter (90Hz - 3800Hz) xuyên iframe.
 * 2. CaptchaStealthAndSolver: Script ngụy trang chống bot, vô hiệu hóa Clickjacking,
 *    xóa sạch Popunder/Popup và tự động giải reCAPTCHA/Turnstile.
 */

// =========================================================================
// 1. CẦU NỐI JAVASCRIPT GIAO TIẾP VỚI WEBVIEW (WEBAUDIO TAP BRIDGE)
// =========================================================================
class WebAudioTapBridge(
    private val aiEngine: AsrTranslatorEngine,
    private val onFullscreenToggle: (Boolean) -> Unit = {},
    private val onCaptchaSolved: (String) -> Unit = {},
    private val onRequestNativeTouch: (Float, Float) -> Unit = { _, _ -> },
    private val onCachedSubFound: ((CachedSubtitleEntry) -> Unit)? = null,
    private val onStatusUpdate: (String) -> Unit = {}
) {
    private val firstPcmSeen = java.util.concurrent.atomic.AtomicBoolean(false)
    private val mediaGate = MediaSessionGate()

    @Synchronized fun pageChanged() { mediaGate.invalidate() }

    @JavascriptInterface
    @Synchronized fun onAudioChunkForMedia(base64Pcm16:String, positionMs:Long, playbackRate:Float, mediaId:String) {
        when(mediaGate.accept(mediaId)) {
            MediaSessionGate.Decision.REJECT -> return
            MediaSessionGate.Decision.SWITCH -> {
                currentVideoSecond = 0
                aiEngine.resetSceneContext("Đổi nguồn video", clearPageMetadata = true)
            }
            MediaSessionGate.Decision.CONTINUE -> Unit
        }
        onAudioChunkWithPositionMs(base64Pcm16, positionMs, playbackRate)
    }
    @Volatile var currentVideoSecond: Int = 0
        private set

    @JavascriptInterface
    fun onAudioChunkBase64(base64Pcm16: String) {
        processChunk(base64Pcm16, currentVideoSecond, 1.0f)
    }

    @JavascriptInterface
    fun onAudioChunkWithTime(base64Pcm16: String, second: Int) {
        currentVideoSecond = second
        processChunk(base64Pcm16, second, 1.0f)
    }

    @JavascriptInterface
    fun onAudioChunkWithPlaybackRate(base64Pcm16: String, second: Int, playbackRate: Float) {
        currentVideoSecond = second
        processChunk(base64Pcm16, second, playbackRate)
    }

    @JavascriptInterface
    fun onAudioChunkWithPositionMs(base64Pcm16: String, positionMs: Long, playbackRate: Float) {
        currentVideoSecond = (positionMs / 1000L).toInt()
        processChunk(base64Pcm16, (positionMs / 1000L).toInt(), playbackRate, positionMs)
    }

    private fun processChunk(base64Pcm16: String, second: Int, playbackRate: Float = 1.0f, positionMs: Long = second * 1000L) {
        try {
            val bytes = Base64.decode(base64Pcm16, Base64.DEFAULT)
            val shortCount = bytes.size / 2
            if (shortCount == 0) return

            val shortBuffer = ByteBuffer.wrap(bytes)
                .order(ByteOrder.LITTLE_ENDIAN)
                .asShortBuffer()

            val floatSamples = FloatArray(shortCount) { i ->
                shortBuffer.get(i) / 32768.0f
            }

            if (firstPcmSeen.compareAndSet(false, true)) {
                android.util.Log.i("LiveSubAudio", "WebView PCM bridge active")
            }
            aiEngine.processAudioSamples(floatSamples, second, playbackRate, positionMs)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    @JavascriptInterface
    fun onHookStatus(status: String) {
        onStatusUpdate(status)
    }

    @JavascriptInterface
    fun onVideoChanged(eventReason: String) {
        TimelineSubtitleCache.clear()
        aiEngine.resetSceneContext("Đổi video", clearPageMetadata = true)
    }

    @JavascriptInterface
    fun onPlaybackEnded(positionMs: Long) {
        aiEngine.flushOnPlaybackEnd(positionMs.coerceAtLeast(0L))
    }

    @JavascriptInterface
    fun onQuickSeek() {
        aiEngine.flushAudioOnlyOnQuickSeek()
    }

    @JavascriptInterface
    fun onSeekResult(deltaSeconds: Int, detail: String) {
        android.util.Log.i("LiveSubSeek", "javascript_seek_result delta=" + deltaSeconds +
            " detail=" + detail.take(700))
    }

    /**
     * Báo về MainActivity để ẩn/hiện thanh nhập URL và thanh trạng thái Android
     */
    @JavascriptInterface
    fun onFullscreenChanged(isFullscreen: Boolean) {
        onFullscreenToggle(isFullscreen)
    }

    /**
     * Khi tua lùi (-5s), kiểm tra ngay bảng băm xem giây đó đã có sub chưa
     */
    @JavascriptInterface
    fun onSeekBackwardCheckCache(second: Int) {
        val cached = TimelineSubtitleCache.findCachedSubAt(second)
        if (cached != null) {
            onCachedSubFound?.invoke(cached)
        }
    }

    /**
     * Nhận link âm thanh MP3 từ iframe reCAPTCHA và nhờ SenseVoice đọc đáp án
     */
    @JavascriptInterface
    fun solveRecaptchaAudioUrl(mp3Url: String) {
        aiEngine.solveCaptchaAudioFromUrl(mp3Url) { answerText ->
            onCaptchaSolved(answerText)
        }
    }

    /**
     * Nhận yêu cầu chạm phần cứng thật từ JavaScript khi gặp iframe Cloudflare/reCAPTCHA
     */
    @JavascriptInterface
    fun requestNativeTouch(x: Float, y: Float) {
        onRequestNativeTouch(x, y)
    }

    companion object {
        fun getInjectionScript(initialSilentMode: Boolean): String {
            val initialGain = if (initialSilentMode) "0.0" else "1.0"
            return """
                (function() {
                    const host = (window.location.hostname || '').toLowerCase();
                    const path = (window.location.pathname || '').toLowerCase();
                    const title = (document.title || '').toLowerCase();
                    if (typeof window._cf_chl_opt !== 'undefined' ||
                        host.includes('cloudflare.com') || host.includes('challenges.cloudflare.com') ||
                        path.includes('/cdn-cgi/') || window.location.href.includes('__cf_chl') ||
                        title.includes('just a moment') || title.includes('chờ một chút') || title.includes('xác minh') || title.includes('verifying') ||
                        title.includes('security check') || title.includes('attention required') || title.includes('un momento') ||
                        title.includes('un instant') || title.includes('bitte warten') || title.includes('稍后') || title.includes('少しお待ちください') ||
                        document.getElementById('challenge-form') || document.getElementById('challenge-running') ||
                        document.querySelector('input[name="cf-turnstile-response"]') ||
                        document.querySelector('.cf-turnstile') || document.querySelector('iframe[src*="cloudflare"]') ||
                        document.querySelector('script[src*="orchestrate/chl_page"]')
                    ) {
                        return;
                    }
                    if (window.__liveSubInjected) return;
                    window.__liveSubInjected = true;

                    const mediaDocumentId = Date.now().toString(36) + Math.random().toString(36).slice(2);
                    let mediaSerial = 0;
                    let activeMediaVideo = null;
                    function mediaToken(video) {
                        const src = video.currentSrc || video.src || "";
                        const page = window.location.pathname + window.location.search;
                        const signature = page + "\\n" + src;
                        if (!video.__liveSubMediaId || video.__liveSubMediaSignature !== signature) {
                            video.__liveSubMediaSignature = signature;
                            video.__liveSubMediaId = mediaDocumentId + ":" + (++mediaSerial);
                        }
                        return video.__liveSubMediaId;
                    }
                    window.__liveSubAudioCtx = null;
                    window.__liveSubGainNode = null;
                    window.__isCssFullscreen = false;

                    // 1. MỞ KHÓA QUYỀN FULLSCREEN CHO MỌI THẺ IFRAME TRÊN TRANG PHIM
                    function unlockIframeFullscreen() {
                        document.querySelectorAll('iframe').forEach(f => {
                            const src = (f.src || '').toLowerCase();
                            if (src.includes('cloudflare') || src.includes('turnstile')) return;
                            if (!f.__fsUnlocked) {
                                f.__fsUnlocked = true;
                                f.setAttribute('allowfullscreen', 'true');
                                f.setAttribute('webkitallowfullscreen', 'true');
                                f.setAttribute('mozallowfullscreen', 'true');
                                const currentAllow = f.getAttribute('allow') || '';
                                if (!currentAllow.includes('fullscreen')) {
                                    f.setAttribute('allow', currentAllow + '; fullscreen *; autoplay *');
                                }
                            }
                        });
                    }

                    // 2. ÉP KHUNG VIDEO HOẶC IFRAME BUNG TRÀN 100% CỬA SỔ WEBVIEW KHI BẬT FULLSCREEN
                    function applyCssFullscreen(enable) {
                        window.__isCssFullscreen = enable;
                        let styleEl = document.getElementById('__livesub_fs_style');
                        if (enable) {
                            if (!styleEl) {
                                styleEl = document.createElement('style');
                                styleEl.id = '__livesub_fs_style';
                                document.head.appendChild(styleEl);
                            }
                            styleEl.innerHTML = `
                                body, html { overflow: hidden !important; background: #000 !important; }
                                video, .jwplayer.jw-flag-fullscreen, .plyr--fullscreen-fallback,
                                iframe.__livesub_active_iframe {
                                    position: fixed !important;
                                    top: 0 !important; left: 0 !important; right: 0 !important; bottom: 0 !important;
                                    width: 100vw !important; height: 100vh !important;
                                    max-width: 100vw !important; max-height: 100vh !important;
                                    z-index: 2147483646 !important;
                                    background: #000 !important;
                                    object-fit: contain !important;
                                }
                            `;
                        } else if (styleEl) {
                            styleEl.innerHTML = '';
                        }
                        if (window.AndroidAudioBridge && window.AndroidAudioBridge.onFullscreenChanged) {
                            window.AndroidAudioBridge.onFullscreenChanged(enable);
                        }
                        if (window.parent && window.parent !== window) {
                            window.parent.postMessage({ type: 'LIVESUB_FS_STATE', fullscreen: enable }, '*');
                        }
                    }

                    // 3. BẮT SỰ KIỆN KHI NGƯỜI DÙNG BẤM NÚT PHÓNG TO (⛶) TRÊN PLAYER
                    document.addEventListener('fullscreenchange', () => {
                        const isFs = !!document.fullscreenElement;
                        if (window.AndroidAudioBridge && window.AndroidAudioBridge.onFullscreenChanged) {
                            window.AndroidAudioBridge.onFullscreenChanged(isFs);
                        }
                    });
                    document.addEventListener('webkitfullscreenchange', () => {
                        const isFs = !!document.webkitFullscreenElement;
                        if (window.AndroidAudioBridge && window.AndroidAudioBridge.onFullscreenChanged) {
                            window.AndroidAudioBridge.onFullscreenChanged(isFs);
                        }
                    });

                    // Phím tắt fullscreen được kích hoạt thông qua click listener bên dưới

                    // Bắt trực tiếp nút Fullscreen của JWPlayer (cobephim), Plyr (missav), VideoJS
                    document.addEventListener('click', function(e) {
                        const btn = e.target.closest(
                            '.jw-icon-fullscreen, [data-plyr="fullscreen"], .vjs-fullscreen-control, ' +
                            '.art-icon-fullscreen, .art-icon-fullscreenWeb, button[title*="Full"], button[aria-label*="Full"]'
                        );
                        if (btn) {
                            setTimeout(() => {
                                const isNativeFs = !!(document.fullscreenElement || document.webkitFullscreenElement);
                                if (!isNativeFs) {
                                    applyCssFullscreen(!window.__isCssFullscreen);
                                }
                            }, 150);
                        }
                    }, true);

                    // Hàm để nút Back của Android thoát chế độ CSS Fullscreen
                    window.exitLiveSubFullscreen = function() {
                        if (document.fullscreenElement && document.exitFullscreen) {
                            document.exitFullscreen().catch(() => {});
                        }
                        if (window.__isCssFullscreen) {
                            applyCssFullscreen(false);
                        }
                        document.querySelectorAll('iframe').forEach(f => {
                            f.classList.remove('__livesub_active_iframe');
                            try { f.contentWindow.postMessage({ type: 'LIVESUB_EXIT_FS' }, '*'); } catch(e) {}
                        });
                    };

                    // 4. HÀM TUA VIDEO 5 GIÂY XUYÊN MỌI IFRAME
                    // SEEK THE ACTIVE HTML5 VIDEO, THEN FORWARD TO NESTED IFRAME PLAYERS.
                    // Prefer the visible, currently playing video. If paused, use the
                    // largest visible, ready video. Never seek hidden previews/ad videos.
                    window.seekLiveSubVideo = function(deltaSeconds) {
                        const delta = Number(deltaSeconds);
                        if (!Number.isFinite(delta) || delta === 0)
                            return JSON.stringify({ok:false, reason:'invalid_delta'});

                        const videos = Array.from(document.querySelectorAll('video'));
                        const visible = videos.filter(v => {
                            const r = v.getBoundingClientRect();
                            const st = window.getComputedStyle(v);
                            return r.width > 40 && r.height > 40 &&
                                st.display !== 'none' && st.visibility !== 'hidden' &&
                                Number(st.opacity || 1) > 0.01;
                        });
                        const ready = visible.filter(v => v.readyState > 0 || v.currentTime > 0);
                        const playing = ready.filter(v => !v.paused && !v.ended);
                        const candidates = (playing.length ? playing : ready).sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (br.width * br.height) - (ar.width * ar.height);
                        });

                        const target = candidates[0];
                        if (target) {
                            const oldTime = Number(target.currentTime || 0);
                            let minTime = 0;
                            let maxTime = Number.isFinite(target.duration)
                                ? target.duration : Number.POSITIVE_INFINITY;
                            try {
                                if (target.seekable && target.seekable.length > 0) {
                                    let selectedRange = target.seekable.length - 1;
                                    for (let i = 0; i < target.seekable.length; i++) {
                                        if (oldTime >= target.seekable.start(i) &&
                                            oldTime <= target.seekable.end(i)) {
                                            selectedRange = i;
                                            break;
                                        }
                                    }
                                    minTime = Math.max(0, target.seekable.start(selectedRange));
                                    maxTime = target.seekable.end(selectedRange);
                                }
                            } catch (_) {}

                            const targetTime = Math.max(minTime, Math.min(maxTime, oldTime + delta));
                            try {
                                target.currentTime = targetTime;
                                showSeekToastOnVideo(delta);
                                const result = {
                                    ok:true, delta:delta, oldTime:oldTime, targetTime:targetTime,
                                    paused:target.paused, videoCount:videos.length,
                                    source:'active_html5_video'
                                };
                                if (window.AndroidAudioBridge && window.AndroidAudioBridge.onSeekResult)
                                    window.AndroidAudioBridge.onSeekResult(delta, JSON.stringify(result));
                                if (delta < 0 && window.AndroidAudioBridge &&
                                    window.AndroidAudioBridge.onSeekBackwardCheckCache) {
                                    try { window.AndroidAudioBridge.onSeekBackwardCheckCache(Math.floor(targetTime)); } catch (_) {}
                                }
                                if (window.AndroidAudioBridge && window.AndroidAudioBridge.onQuickSeek) {
                                    try { window.AndroidAudioBridge.onQuickSeek(); } catch (_) {}
                                }
                                return JSON.stringify(result);
                            } catch (error) {
                                const result = {ok:false, reason:'currentTime_set_failed', error:String(error)};
                                if (window.AndroidAudioBridge && window.AndroidAudioBridge.onSeekResult)
                                    window.AndroidAudioBridge.onSeekResult(delta, JSON.stringify(result));
                                return JSON.stringify(result);
                            }
                        }

                        // The player may live in a cross-origin iframe. Send the same
                        // request into each child; the injected handler runs in that frame.
                        const frames = Array.from(document.querySelectorAll('iframe'));
                        frames.forEach(f => {
                            try { f.contentWindow.postMessage({type:'LIVESUB_SEEK', delta:delta}, '*'); } catch (_) {}
                        });
                        const result = {ok:frames.length > 0, reason:'no_local_video',
                            forwardedFrames:frames.length, videoCount:videos.length};
                        if (window.AndroidAudioBridge && window.AndroidAudioBridge.onSeekResult)
                            window.AndroidAudioBridge.onSeekResult(delta, JSON.stringify(result));
                        return JSON.stringify(result);
                    };

                    // Hiển thị hiệu ứng ⏪ -5s / ⏩ +5s ngay trên khung video
                    function showSeekToastOnVideo(delta) {
                        try {
                            let badge = document.getElementById('__livesub_seek_badge');
                            if (!badge) {
                                badge = document.createElement('div');
                                badge.id = '__livesub_seek_badge';
                                badge.style.cssText = 'position:fixed;top:40%;left:50%;transform:translate(-50%,-50%);' +
                                    'background:rgba(0,0,0,0.85);color:#FFE81F;padding:12px 24px;border-radius:28px;' +
                                    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;letter-spacing:0.8px;' +
                                    'font-size:19px;font-weight:bold;z-index:2147483647;pointer-events:none;transition:opacity 0.2s;' +
                                    'box-shadow:0 6px 20px rgba(0,0,0,0.7);border:1.5px solid rgba(255,232,31,0.35);backdrop-filter:blur(6px);';
                                document.body.appendChild(badge);
                            }
                            badge.innerText = delta > 0 ? ('⏩ +' + delta + 's') : ('⏪ ' + delta + 's');
                            badge.style.opacity = '1';
                            clearTimeout(window.__seekBadgeTimer);
                            window.__seekBadgeTimer = setTimeout(() => { badge.style.opacity = '0'; }, 650);
                        } catch(e) {}
                    }

                    // 5. BẮT LỆNH TUA & LỆNH TẮT LOA TỪ TRANG CHÍNH GỬI VÀO IFRAME
                    window.addEventListener('message', function(ev) {
                        if (!ev.data) return;
                        if (ev.data.type === 'LIVESUB_SILENT') {
                            window.setLiveSubSilentMode(ev.data.silent);
                        } else if (ev.data.type === 'LIVESUB_SEEK') {
                            window.seekLiveSubVideo(ev.data.delta);
                        } else if (ev.data.type === 'LIVESUB_EXIT_FS') {
                            window.exitLiveSubFullscreen();
                        } else if (ev.data.type === 'LIVESUB_FS_STATE') {
                            // Nếu iframe con báo đang phóng to -> Phóng to luôn thẻ iframe ở trang cha!
                            document.querySelectorAll('iframe').forEach(f => {
                                if (f.contentWindow === ev.source) {
                                    if (ev.data.fullscreen) f.classList.add('__livesub_active_iframe');
                                    else f.classList.remove('__livesub_active_iframe');
                                }
                            });
                            applyCssFullscreen(ev.data.fullscreen);
                        }
                    });

                    // Chặn trình phát web tự phóng to/thu nhỏ màn hình khi nhấp đúp ở 2 mép trái/phải
                    window.addEventListener('dblclick', function(e) {
                        const w = window.innerWidth || document.documentElement.clientWidth;
                        if (e.clientX < w * 0.4 || e.clientX > w * 0.6) {
                            e.stopPropagation();
                            e.preventDefault();
                        }
                    }, true);

                    let lastTouchEnd = 0;
                    let lastTouchX = 0;
                    window.addEventListener('touchend', function(e) {
                        const now = Date.now();
                        const touch = e.changedTouches[0];
                        if (touch) {
                            const x = touch.clientX;
                            const w = window.innerWidth || document.documentElement.clientWidth;
                            if (now - lastTouchEnd < 320 && Math.abs(x - lastTouchX) < 40) {
                                if (x < w * 0.4 || x > w * 0.6) {
                                    e.preventDefault();
                                }
                            }
                            lastTouchEnd = now;
                            lastTouchX = x;
                        }
                    }, { passive: false });

                    // 6. ĐIỀU KHIỂN LOA NGOÀI CỦA VIDEO
                    window.__liveSubIsSilent = ($initialGain === 0.0 || "$initialGain" === "0.0");
                    window.setLiveSubSilentMode = function(isSilent) {
                        window.__liveSubIsSilent = isSilent;
                        if (window.__liveSubGainNode && window.__liveSubAudioCtx) {
                            window.__liveSubGainNode.gain.setValueAtTime(
                                isSilent ? 0.0 : 1.0,
                                window.__liveSubAudioCtx.currentTime
                            );
                        }
                        // Điều khiển âm lượng thẻ video khi dùng captureStream fallback
                        document.querySelectorAll('video').forEach(v => {
                            if (!v.__isMediaElementSource) {
                                v.volume = isSilent ? 0.001 : 1.0;
                            }
                        });
                        document.querySelectorAll('iframe').forEach(f => {
                            try { f.contentWindow.postMessage({ type: 'LIVESUB_SILENT', silent: isSilent }, '*'); } catch(e) {}
                        });
                    };

                    // 7. MÓC (HOOK) LUỒNG ÂM THANH CỦA VIDEO KÈM BỘ LỌC TÁCH GIỌNG NÓI
                    function hookVideoElement(video) {
                        if (!video || video.__liveSubHooked) return;
                        if (video.id === 'liveSubBgVideo' || video.classList.contains('ambient-video')) return;
                        // Bỏ qua các thẻ video quảng cáo nhỏ (chiều rộng < 180px hoặc nằm trong khung chứa từ 2 video trở lên)
                        const rect = video.getBoundingClientRect();
                        if (rect.width > 0 && rect.width < 180) return;

                        video.__liveSubHooked = true;

                        // Keep the media element's CORS mode under the site's control.
                        // Setting anonymous before src is assigned can reject playable CDN media.

                        // Bắt sự kiện phóng to riêng của thẻ <video> trên WebKit
                        video.addEventListener('webkitbeginfullscreen', () => {
                            if (window.AndroidAudioBridge && window.AndroidAudioBridge.onFullscreenChanged) {
                                window.AndroidAudioBridge.onFullscreenChanged(true);
                            }
                        });
                        video.addEventListener('webkitendfullscreen', () => {
                            if (window.AndroidAudioBridge && window.AndroidAudioBridge.onFullscreenChanged) {
                                window.AndroidAudioBridge.onFullscreenChanged(false);
                            }
                        });

                        const attachAudioGraph = function() {
                            try {
                                if (!window.__liveSubAudioCtx || window.__liveSubAudioCtx.state === 'closed') {
                                    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
                                    window.__liveSubAudioCtx = new AudioContextClass({ sampleRate: 16000 });
                                }
                                const ctx = window.__liveSubAudioCtx;
                                if (ctx.state === 'suspended') ctx.resume();

                                activeMediaVideo = video;
                                if (video.__sourceNodeConnected) return;
                                video.__sourceNodeConnected = true;

                                // Ngắt kết nối các node cũ để tránh rò rỉ bộ nhớ & CPU
                                try { if (window.__liveSubProcessor) window.__liveSubProcessor.disconnect(); } catch(e) {}
                                try { if (window.__liveSubSourceNode) window.__liveSubSourceNode.disconnect(); } catch(e) {}
                                try { if (window.__liveSubDelayNode) window.__liveSubDelayNode.disconnect(); } catch(e) {}
                                try { if (window.__liveSubGainNode) window.__liveSubGainNode.disconnect(); } catch(e) {}

                                let source = null;
                                let isMediaElement = false;
                                const isCorsSafeElement = (video.crossOrigin === 'anonymous') ||
                                                          (window.location.hostname === 'livesub.local');

                                if (!isCorsSafeElement && typeof video.captureStream === 'function') {
                                    try {
                                        const stream = video.captureStream();
                                        if (stream && stream.getAudioTracks().length > 0) {
                                            source = ctx.createMediaStreamSource(stream);
                                            isMediaElement = false;
                                            video.__isMediaElementSource = false;
                                        }
                                    } catch (_) {}
                                }

                                if (!source) {
                                    try {
                                        source = ctx.createMediaElementSource(video);
                                        isMediaElement = true;
                                        video.__isMediaElementSource = true;
                                    } catch (_) {}
                                }
                                if (!source) return;

                                window.__liveSubSourceNode = source;
                                video.__liveSubSourceNode = source;

                                const processor = ctx.createScriptProcessor(2048, 2, 1); // 128ms/chunk (nhanh gấp đôi 4096)
                                window.__liveSubProcessor = processor;
                                video.__liveSubProcessor = processor;

                                const speakerGain = ctx.createGain();
                                speakerGain.gain.value = window.__liveSubIsSilent ? 0.0 : 1.0;
                                window.__liveSubGainNode = speakerGain;

                                if (isMediaElement) {
                                    video.muted = false;
                                    video.volume = 1.0;
                                    // Tăng delay loa ngoài lên 0.45s để tiếng nói phát ra khớp khẩu hình với phụ đề VAD 0.45s
                                    const audioDelay = ctx.createDelay(1.0);
                                    audioDelay.delayTime.value = 0.45;
                                    window.__liveSubDelayNode = audioDelay;

                                    source.connect(audioDelay);
                                    audioDelay.connect(speakerGain);
                                    speakerGain.connect(ctx.destination);
                                } else {
                                    video.muted = false;
                                    video.volume = window.__liveSubIsSilent ? 0.001 : 1.0;
                                }

                                // Nhánh đưa vào AI: Giữ nguyên dải động tự nhiên của giọng nói
                                const highPassFilter = ctx.createBiquadFilter();
                                highPassFilter.type = 'highpass';
                                highPassFilter.frequency.value = 75; // Chỉ lọc tạp âm cực trầm dưới 75Hz
                                window.__liveSubHighPass = highPassFilter;

                                source.connect(highPassFilter);
                                highPassFilter.connect(processor);
                                processor.connect(ctx.destination);

                                processor.onaudioprocess = function(e) {
                                    if (video.paused || video.ended || activeMediaVideo !== video || !video.isConnected) return;
                                    window.__liveSubChunkCount = (window.__liveSubChunkCount || 0) + 1;

                                    // Tách kênh Mid-Side: Giữ nguyên giọng nói trung tâm (Mid), giảm nhạc nền Stereo (Side) hai bên
                                    const left = e.inputBuffer.getChannelData(0);
                                    const right = e.inputBuffer.numberOfChannels > 1 ? e.inputBuffer.getChannelData(1) : left;
                                    const monoCenter = new Float32Array(left.length);
                                    for (let i = 0; i < left.length; i++) {
                                        monoCenter[i] = (left[i] + right[i]) * 0.5;
                                    }

                                    const actualRate = ctx.sampleRate || 16000;

                                    // Đảm bảo luôn downsample chính xác về 16000Hz nếu WebView chạy ở 44.1kHz / 48kHz
                                    let floatData = monoCenter;
                                    if (actualRate !== 16000) {
                                        const ratio = actualRate / 16000;
                                        const newLength = Math.round(monoCenter.length / ratio);
                                        floatData = new Float32Array(newLength);
                                        for (let i = 0; i < newLength; i++) {
                                            const start = Math.floor(i * ratio);
                                            const end = Math.min(Math.floor((i + 1) * ratio), monoCenter.length);
                                            let sum = 0;
                                            let count = 0;
                                            for (let j = start; j < end; j++) {
                                                sum += monoCenter[j];
                                                count++;
                                            }
                                            floatData[i] = count > 0 ? (sum / count) : monoCenter[Math.min(start, monoCenter.length - 1)];
                                        }
                                    }

                                    // Nếu dùng captureStream ở chế độ tắt loa (video.volume = 0.001), khôi phục biên độ về mức chuẩn cho AI
                                    const gainBoost = (!isMediaElement && window.__liveSubIsSilent && video.volume < 0.05) ? (1.0 / Math.max(video.volume, 0.001)) : 1.0;

                                    let maxPeak = 0;
                                    const int16 = new Int16Array(floatData.length);
                                    for (let i = 0; i < floatData.length; i++) {
                                        let s = floatData[i] * gainBoost;
                                        let a = s < 0 ? -s : s;
                                        if (a > maxPeak) maxPeak = a;
                                        s = s < -1 ? -1 : (s > 1 ? 1 : s);
                                        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                                    }

                                    // Nếu đoạn video hoàn toàn im lặng (< 0.001) quá 28 khung liên tiếp -> Ngừng gửi Base64 để tiết kiệm Pin & CPU
                                    if (maxPeak < 0.001) {
                                        window.__liveSubSilentCount = (window.__liveSubSilentCount || 0) + 1;
                                        if (window.__liveSubSilentCount > 28) return; // Đủ xả hết PAUSE_TRIGGER_SAMPLES trên cả 16kHz lẫn 48kHz
                                    } else {
                                        window.__liveSubSilentCount = 0;
                                    }

                                    const uint8 = new Uint8Array(int16.buffer);
                                    // Tăng tốc 8x trong V8 Engine so với phép cộng chuỗi từng ký tự
                                    let binary = '';
                                    const chunkSize = 4096;
                                    for (let i = 0; i < uint8.length; i += chunkSize) {
                                        binary += String.fromCharCode.apply(null, uint8.subarray(i, i + chunkSize));
                                    }

                                    const currentSec = Math.floor(video.currentTime || 0);
                                    const playbackRate = video.playbackRate || 1.0;
                                    try {
                                        if (window.AndroidAudioBridge && window.AndroidAudioBridge.onAudioChunkForMedia) {
                                            window.AndroidAudioBridge.onAudioChunkForMedia(btoa(binary), Math.round((video.currentTime || 0) * 1000), playbackRate, mediaToken(video));
                                        } else if (window.AndroidAudioBridge && window.AndroidAudioBridge.onAudioChunkWithPositionMs) {
                                                window.AndroidAudioBridge.onAudioChunkWithPositionMs(btoa(binary), Math.round((video.currentTime || 0) * 1000), playbackRate);
                                            } else if (window.AndroidAudioBridge && window.AndroidAudioBridge.onAudioChunkWithPlaybackRate) {
                                            window.AndroidAudioBridge.onAudioChunkWithPlaybackRate(btoa(binary), currentSec, playbackRate);
                                        } else if (window.AndroidAudioBridge && window.AndroidAudioBridge.onAudioChunkWithTime) {
                                            window.AndroidAudioBridge.onAudioChunkWithTime(btoa(binary), currentSec);
                                        } else if (window.AndroidAudioBridge && window.AndroidAudioBridge.onAudioChunkBase64) {
                                            window.AndroidAudioBridge.onAudioChunkBase64(btoa(binary));
                                        }
                                    } catch (_) {}
                                };
                            } catch (err) {
                                try {
                                    if (window.AndroidAudioBridge && window.AndroidAudioBridge.onHookStatus) {
                                        window.AndroidAudioBridge.onHookStatus("Lỗi kết nối Audio: " + err.message);
                                    }
                                } catch (_) {}
                            }
                        };

                        video.addEventListener('play', attachAudioGraph);
                        video.addEventListener('playing', attachAudioGraph);
                        // Khi tua bằng thanh trượt của web: chỉ xả bộ đệm âm thanh để không mất ngữ cảnh
                        video.addEventListener('ended', () => {
                            if (activeMediaVideo !== video) return;
                            try { window.AndroidAudioBridge.onPlaybackEnded(Math.round((video.currentTime || 0)*1000)); } catch(e) {}
                        });
                        video.addEventListener('seeking', () => {
                            if (activeMediaVideo !== video) return;
                            if (window.AndroidAudioBridge && window.AndroidAudioBridge.onQuickSeek) {
                                try { window.AndroidAudioBridge.onQuickSeek(); } catch(e) {}
                            }
                        });

                        if (!video.paused) attachAudioGraph();
                    }

                    function scanAll() {
                        if (typeof window._cf_chl_opt !== 'undefined' || (document.title || '').toLowerCase().includes('just a moment')) return;
                        unlockIframeFullscreen();
                        document.querySelectorAll('video').forEach(hookVideoElement);
                    }

                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', scanAll);
                    } else {
                        scanAll();
                    }
                    setInterval(scanAll, 1000);
                })();
            """.trimIndent()
        }
    }
}

// =========================================================================
// 2. NGỤY TRANG CHỐNG BOT, XÓA CLICKJACKING & GIẢI CAPTCHA
// =========================================================================
object CaptchaStealthAndSolver {

    fun getStealthAndAutoSolveScript(): String {
        return """
            (function() {
                const host = (window.location.hostname || '').toLowerCase();
                const path = (window.location.pathname || '').toLowerCase();
                const url = (window.location.href || '').toLowerCase();
                const title = (document.title || '').toLowerCase();

                // 1. NẾU LÀ TRANG CỦA CLOUDFLARE HOẶC ĐANG Ở MÀN HÌNH XÁC MINH TURNSTILE -> TUYỆT ĐỐI KHÔNG CAN THIỆP!
                if (typeof window._cf_chl_opt !== 'undefined' ||
                    host.includes('cloudflare.com') || host.includes('challenges.cloudflare.com') ||
                    path.includes('/cdn-cgi/') || url.includes('__cf_chl') ||
                    title.includes('just a moment') || title.includes('chờ một chút') || title.includes('xác minh') || title.includes('verifying') ||
                    title.includes('security check') || title.includes('attention required') || title.includes('un momento') ||
                    title.includes('un instant') || title.includes('bitte warten') || title.includes('稍后') || title.includes('少しお待ちください') ||
                    document.getElementById('challenge-form') || document.getElementById('challenge-running') ||
                    document.querySelector('input[name="cf-turnstile-response"]') ||
                    document.querySelector('.cf-turnstile') || document.querySelector('iframe[src*="cloudflare"]') ||
                    document.querySelector('script[src*="orchestrate/chl_page"]')
                ) {
                    return;
                }

                if (window.__captchaStealthInjected) return;
                window.__captchaStealthInjected = true;

                // 2. Chống mở cửa sổ nhảy trang popup (đã được Android WebView xử lý triệt để qua setSupportMultipleWindows(false))
                // Giữ nguyên window.open gốc để vượt qua bài kiểm tra nguyên mẫu (Prototype Integrity) của Cloudflare Turnstile.

                // 3. KIỂM TRA BẢO VỆ PHẦN TỬ (TRÌNH PHÁT PHIM & XÁC MINH CLOUDFLARE TURNSTILE)
                function isProtectedElement(el) {
                    if (!el || !el.tagName) return false;
                    if (el.id === '__livesub_seek_badge') return true;

                    // A. Tuyệt đối không bảo vệ các hộp quảng cáo Live Cam / Slider / Popunder / Mayzaent / Greentrafsa
                    try {
                        if (el.matches && (
                            el.matches('[class*="bottomRight"], [class*="slideAnimation"], [class*="modelWrapper"], [class*="playerThumbWrapper"], [class*="rootContent---scng"], button[class*="closeButtonMinimal"], a[id^="model-"], a[href*="greentrafsa" i], iframe[src*="mayzaent" i], iframe[src*="snaptrckr" i], [id*="catfish" i], [class*="catfish" i], [id*="popunder" i], [class*="popunder" i], [x-ref*="stripchat" i], .under_player, [class*="under_player" i], div.fixed.right-2.bottom-2, div[class*="right-2"][class*="bottom-2"], iframe[src*="rallytrck" i], iframe[src*="myavlive" i]') ||
                            (el.closest && el.closest('[class*="bottomRight"], [class*="slideAnimation"], [class*="modelWrapper"], a[id^="model-"], a[href*="greentrafsa" i], iframe[src*="mayzaent" i], div.fixed.right-2.bottom-2, [x-ref*="stripchat" i], .under_player'))
                        )) {
                            return false;
                        }
                    } catch(_) {}

                    // B. Bảo vệ tuyệt đối Cloudflare Turnstile, reCAPTCHA và mọi thẻ xác minh bảo mật
                    const id = (el.id || '').toLowerCase();
                    const cls = typeof el.className === 'string' ? el.className.toLowerCase() : '';
                    if (id.includes('cf-') || id.includes('challenge') || id.includes('turnstile') || id.includes('captcha')) return true;
                    if (cls.includes('cf-') || cls.includes('turnstile') || cls.includes('challenge') || cls.includes('captcha')) return true;
                    if (el.querySelector && el.querySelector('iframe[src*="cloudflare"], iframe[src*="turnstile"], iframe[src*="recaptcha"], [id*="cf-"], [class*="cf-"], [id*="turnstile"], [class*="turnstile"]')) return true;
                    if (el.closest && el.closest('#challenge-form, #challenge-stage, .cf-turnstile, [id*="cf-"], [id*="turnstile"], [class*="turnstile"], [class*="cf-"]')) return true;

                    // C. Bảo vệ khung chứa trình phát Plyr (MissAV), JWPlayer, VideoJS
                    if (el.classList && (el.classList.contains('plyr') || el.classList.contains('jwplayer') || el.classList.contains('aspect-w-16'))) {
                        return true;
                    }
                    if (el.closest && el.closest('.plyr, .jwplayer, .aspect-w-16, .plyr__video-wrapper')) {
                        return true;
                    }
                    if (el.querySelector && el.querySelector('.plyr, .jwplayer, video#player, .aspect-w-16 video, .plyr__video-wrapper video')) {
                        return true;
                    }
                    return false;
                }

                function isCloudflareChallengeActive() {
                    try {
                        const t = (document.title || '').toLowerCase();
                        if (t.includes('just a moment') || t.includes('xác minh')) return true;
                        const u = (window.location.href || '').toLowerCase();
                        if (u.includes('/cdn-cgi/challenge') || u.includes('challenges.cloudflare.com') || u.includes('__cf_chl')) return true;
                        if (document.getElementById('challenge-form') || document.getElementById('challenge-running') || document.querySelector('.cf-turnstile') || document.querySelector('iframe[src*="cloudflare"]')) return true;
                    } catch (_) {}
                    return false;
                }

                // 4. TIÊU DIỆT HỘP LIVE CAM GÓC DƯỚI PHẢI & CLICKJACKING
                function nukeInPagePopupsAndOverlays() {
                    if (isCloudflareChallengeActive()) return;

                    const vw = window.innerWidth || document.documentElement.clientWidth || 360;
                    const vh = window.innerHeight || document.documentElement.clientHeight || 640;

                    // A. Xóa trực tiếp các thẻ truyền thông/link của mạng quảng cáo Live Cam
                    const adSelectors = [
                        'iframe[src*="stripchat" i]', 'iframe[src*="chaturbate" i]', 'iframe[src*="magsrv" i]',
                        'iframe[src*="exoclick" i]', 'iframe[src*="juicyads" i]', 'iframe[src*="myavlive" i]',
                        'iframe[src*="growcdn" i]', 'iframe[src*="bcvcdn" i]', 'iframe[src*="strpst" i]',
                        'iframe[src*="mayzaent" i]', 'iframe[src*="snaptrckr" i]', 'iframe[src*="smartpop" i]',
                        'iframe[src*="rallytrck" i]', 'iframe[src*="tsyndicate" i]',
                        'a[href*="stripchat" i]', 'a[href*="chaturbate" i]', 'a[href*="xlirdr" i]',
                        'a[href*="myavlive" i]', 'a[href*="doppiocdn" i]', 'a[href*="growcdn" i]',
                        'a[href*="greentrafsa" i]', 'a[href*="mayzaent" i]', 'a[href*="rallytrck" i]', 'a[id^="model-"]',
                        'div[class*="bottomRight"]', 'div[class*="slideAnimation"]',
                        'div[class*="modelWrapper"]', 'div[class*="playerThumbWrapper"]',
                        'div[class*="rootContent---scng"]', 'button[class*="closeButtonMinimal"]',
                        'img[alt*="webcam" i]', 'img[alt*="sex chat" i]', 'img[alt*="nude photos" i]',
                        '.exo-native-widget', 'div[id*="catfish" i]', 'div[class*="catfish" i]',
                        '[x-ref="stripchat"]', '[x-ref*="stripchat" i]', '.under_player', 'div.under_player', '[class*="under_player" i]',
                        'div.fixed.right-2.bottom-2', 'div[class*="right-2"][class*="bottom-2"]', 'div[class*="bottom-2"][class*="right-2"]',
                        'div[id*="StripchatSpot" i]', 'div[class*="StripchatSpot" i]', '#SCSpotScript',
                        'div:has(iframe[src*="rallytrck" i])', 'div:has(iframe[src*="myavlive" i])',
                        'a[rel*="sponsored" i]', 'ul:has(a[rel*="sponsored" i])', 'li:has(a[rel*="sponsored" i])', 'ul.text-nord14'
                    ];
                    adSelectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(node => {
                            const wrapper = node.closest('div[class*="bottomRight"], div[class*="slideAnimation"], div[class*="modelWrapper"], div[style*="fixed"], div.fixed, div.mx-auto') || node;
                            if (!isProtectedElement(wrapper)) {
                                try {
                                    wrapper.style.setProperty('display', 'none', 'important');
                                    wrapper.remove();
                                } catch(_) {}
                            }
                        });
                    });

                    document.querySelectorAll('button').forEach(btn => {
                        if (btn.querySelector && btn.querySelector('svg path[d*="M6 18L18 6M6 6l12 12"]')) {
                            const wrapper = btn.closest('div.fixed, div[class*="bottom-2"], div[class*="right-2"]') || btn.parentElement;
                            if (wrapper && !isProtectedElement(wrapper)) {
                                try {
                                    wrapper.style.setProperty('display', 'none', 'important');
                                    wrapper.remove();
                                } catch(_) {}
                            }
                        }
                    });

                    // B. Quét mọi phần tử nổi (position: fixed) bám ở góc dưới màn hình
                    const allNodes = document.querySelectorAll('div, aside, section, iframe, a');
                    for (let i = 0; i < allNodes.length; i++) {
                        const el = allNodes[i];
                        if (isProtectedElement(el)) continue;

                        const style = window.getComputedStyle(el);
                        if (style.position !== 'fixed' && style.position !== 'sticky') continue;

                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;

                        // Nhận diện hộp nổi nằm ở nửa dưới / góc phải màn hình (như hộp Live Cam 2 ô của MissAV, banner quảng cáo góc phải)
                        const isBottomCornerBox = (rect.bottom >= vh - 60) &&
                                                  (rect.top > vh * 0.25) &&
                                                  (rect.width < vw * 0.85) &&
                                                  (rect.height < vh * 0.65);

                        const isAdCloseBtn = b => {
                            try {
                                return b.matches && (
                                    b.matches('button[class*="closeButtonMinimal"], button[class*="opacity-70"]') ||
                                    b.querySelector('svg path[d*="M6 18L18 6M6 6l12 12"]')
                                );
                            } catch(_) { return false; }
                        };
                        const realButtons = Array.from(el.querySelectorAll('button')).filter(b => !isAdCloseBtn(b));
                        const hasRealUi = el.querySelector('input, form, video, .plyr, .jwplayer, #player') || realButtons.length > 0;

                        // Nhận diện tấm kính tàng hình bẫy click (Clickjacking)
                        const zIndex = parseInt(style.zIndex, 10) || 0;
                        const isTransparentTrap = (parseFloat(style.opacity) < 0.15 || style.backgroundColor === 'rgba(0, 0, 0, 0)') &&
                                                  zIndex >= 20 &&
                                                  rect.width > vw * 0.35 && rect.height > vh * 0.25 &&
                                                  !el.querySelector('input, button, a, video');

                        if ((isBottomCornerBox && !hasRealUi) || isTransparentTrap) {
                            // Tắt âm và ngắt luồng nếu có thẻ video bên trong hộp quảng cáo
                            el.querySelectorAll('video, audio, iframe').forEach(media => {
                                try {
                                    if (media.pause) media.pause();
                                    media.src = 'about:blank';
                                    media.remove();
                                } catch(e) {}
                            });
                            el.style.setProperty('display', 'none', 'important');
                            el.remove();
                        }
                    }
                }

                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', nukeInPagePopupsAndOverlays);
                } else {
                    nukeInPagePopupsAndOverlays();
                }

                const observer = new MutationObserver(() => {
                    nukeInPagePopupsAndOverlays();
                });
                observer.observe(document.documentElement, { childList: true, subtree: true });
                setInterval(nukeInPagePopupsAndOverlays, 800);

                // =========================================================================
                // PHẦN 5: TỰ ĐỘNG GIẢI RECAPTCHA BẰNG SENSEVOICE
                // =========================================================================
                function simulateHumanClick(el) {
                    if (!el) return;
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return;
                    const clientX = rect.left + rect.width * (0.35 + Math.random() * 0.3);
                    const clientY = rect.top + rect.height * (0.35 + Math.random() * 0.3);
                    const opts = { bubbles: true, cancelable: true, view: window, clientX: clientX, clientY: clientY };
                    el.dispatchEvent(new PointerEvent('pointerdown', opts));
                    el.dispatchEvent(new MouseEvent('mousedown', opts));
                    setTimeout(() => {
                        el.dispatchEvent(new PointerEvent('pointerup', opts));
                        el.dispatchEvent(new MouseEvent('mouseup', opts));
                        el.click();
                    }, 60 + Math.floor(Math.random() * 80));
                }

                setInterval(() => {
                    const href = window.location.href;

                    // A. Nếu đang ở trong iframe Checkbox của Google reCAPTCHA (anchor)
                    if (href.includes('google.com/recaptcha') && href.includes('/anchor')) {
                        const checkbox = document.querySelector('#recaptcha-anchor');
                        if (checkbox && checkbox.getAttribute('aria-checked') === 'false' && !window.__rcClicked) {
                            window.__rcClicked = true;
                            setTimeout(() => simulateHumanClick(checkbox), 500 + Math.random() * 400);
                        }
                    }

                    // B. Nếu bị bật bảng chọn hình ảnh reCAPTCHA (bframe) -> Chuyển sang Audio để SenseVoice tự giải!
                    if (href.includes('google.com/recaptcha') && href.includes('/bframe')) {
                        const audioBtn = document.querySelector('#recaptcha-audio-button');
                        const imageTable = document.querySelector('#rc-imageselect');
                        if (imageTable && audioBtn && !window.__switchedToAudio) {
                            window.__switchedToAudio = true;
                            setTimeout(() => simulateHumanClick(audioBtn), 700);
                        }

                        // Khi đã ở giao diện Audio Challenge: Lấy link file MP3 gửi về cho SenseVoice giải mã
                        const audioSrc = document.querySelector('audio#audio-source')?.src 
                            || document.querySelector('.rc-audiochallenge-tdownload-link')?.href
                            || document.querySelector('a.rc-audiochallenge-download-link')?.href
                            || document.querySelector('audio source')?.src
                            || document.querySelector('a[href*="audio.mp3"], a[href*="payload?c="]')?.href;

                        const responseInput = document.querySelector('#audio-response');
                        if (audioSrc && responseInput && !window.__audioSolving) {
                            window.__audioSolving = true;
                            if (window.AndroidAudioBridge && window.AndroidAudioBridge.solveRecaptchaAudioUrl) {
                                window.AndroidAudioBridge.solveRecaptchaAudioUrl(audioSrc);
                            }
                        }
                    }
                }, 800);

                // D. Giám sát và tự động khôi phục CSS nếu phát hiện bị vỡ giao diện (MIME text/html hoặc 403)
                function recoverBrokenStylesheets() {
                    const pageTitle = document.title || '';
                    if (pageTitle.includes('Just a moment') || pageTitle.includes('xác minh') || window.location.href.includes('challenge')) {
                        return; // Đang ở trang thử thách Cloudflare, tuyệt đối không can thiệp
                    }
                    const cssLinks = document.querySelectorAll('link[rel="stylesheet"]');
                    if (cssLinks.length === 0) return;

                    let hasValidRules = false;
                    for (let i = 0; i < document.styleSheets.length; i++) {
                        try {
                            const sheet = document.styleSheets[i];
                            if (sheet.cssRules && sheet.cssRules.length > 3) {
                                hasValidRules = true;
                                break;
                            }
                        } catch (_) {}
                    }

                    if (!hasValidRules && !window.__livesubCssRecoveryActive) {
                        window.__livesubCssRecoveryActive = true;
                        cssLinks.forEach(link => {
                            if (!link.href || link.href.startsWith('data:')) return;
                            fetch(link.href)
                                .then(r => r.text())
                                .then(txt => {
                                    if (txt && !txt.includes('<!DOCTYPE') && (txt.includes('{') || txt.includes('@'))) {
                                        const s = document.createElement('style');
                                        s.setAttribute('data-recovered-css', 'true');
                                        s.textContent = txt;
                                        document.head.appendChild(s);
                                        console.log('✅ [LiveSub] Đã khôi phục CSS thành công:', link.href);
                                    }
                                })
                                .catch(() => {});
                        });
                    }
                }
                setTimeout(recoverBrokenStylesheets, 1200);
                setTimeout(recoverBrokenStylesheets, 3000);

                window.addEventListener('message', function(ev) {
                    if (ev.data && ev.data.type === 'LIVESUB_CAPTCHA_SOLVED') {
                        const answer = ev.data.answer;
                        const responseInput = document.querySelector('#audio-response');
                        const verifyBtn = document.querySelector('#recaptcha-verify-button');
                        if (responseInput && verifyBtn && answer) {
                            responseInput.focus();
                            responseInput.value = answer;
                            responseInput.dispatchEvent(new Event('input', { bubbles: true }));
                            setTimeout(() => simulateHumanClick(verifyBtn), 400);
                        }
                    }
                });
            })();
        """.trimIndent()
    }
}
