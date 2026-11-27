package com.example.livesub

import android.content.Context
import android.media.MediaCodec
import android.media.MediaDataSource
import android.media.MediaExtractor
import android.media.MediaFormat
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Deferred
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/** Reads an approved HTTPS VOD media playlist one segment at a time. */
internal class HlsVodSubtitleGenerator(
    private val context: Context,
    private val engine: AsrTranslatorEngine,
    private val browserReader: HlsBrowserSegmentReader,
    private val page: String,
    private val localFixture: HlsLocalQaFixture? = null
) {
    private companion object {
        const val BATCH_SIZE = 15
        const val MAX_PARTIAL_BATCH_CONTENT_WAIT_MS = 30_000L
        const val MAX_IN_FLIGHT_BATCHES = 8
        const val WINDOW_MS = 600_000L
        const val WINDOW_TARGET_MS = 300_000L
    }
    data class Progress(val processed: Int, val total: Int, val subtitles: Int)
    data class Output(
        val vietnameseSrt: String,
        val originalSrt: String,
        val segmentsProcessed: Int,
        val subtitles: Int,
        val untranslated: Int,
        val pauseReason: String? = null,
        val checkpointSaved: Boolean = true
    )

    private val http = OkHttpClient.Builder()
        .followRedirects(false)
        .retryOnConnectionFailure(false)
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(25, TimeUnit.SECONDS)
        .callTimeout(35, TimeUnit.SECONDS)
        .build()

    private fun requestBytes(url: String, maxBytes: Int): ByteArray {
        val uri = java.net.URI(url)
        require(uri.scheme == "https" && uri.host != null && uri.userInfo == null) {
            "Cần nguồn HTTPS trực tiếp, hợp lệ"
        }
        // Ordinary media fetches share the WebView user agent and page origin/referer
        // (matching NetworkSecurityInterceptor's media CDN headers).
        // This does not bypass DRM, CORS or authorization restrictions.
        val pageUri = runCatching { java.net.URI(page) }.getOrNull()
        val pageOrigin = if (pageUri?.scheme == "https" && !pageUri.host.isNullOrEmpty()) {
            "https://" + pageUri.rawAuthority
        } else null
        val requestBuilder = Request.Builder().url(url)
            .header("User-Agent", android.webkit.WebSettings.getDefaultUserAgent(context))
            .header("Referer", pageOrigin?.let { "$it/" } ?: page)
            .header("Accept", "*/*")
        if (pageOrigin != null) {
            requestBuilder.header("Origin", pageOrigin)
        }
        val request = requestBuilder.get().build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                android.util.Log.w("LiveSub_Hls",
                    "media_http=" + response.code +
                    " playlist=" + url.contains(".m3u8", ignoreCase = true) +
                    " browser_fallback_supported=" + HlsBrowserSegmentReader.canFetch(url, page))
                throw HlsMediaHttpException(response.code,
                    HlsMediaAccessPolicy.parseRetryAfter(response.header("Retry-After"),
                        System.currentTimeMillis()))
            }
            val body = response.body ?: error("Empty media response")
            require(body.contentLength() <= maxBytes || body.contentLength() < 0) {
                "Media response exceeds per-segment limit"
            }
            val output = ByteArrayOutputStream()
            body.byteStream().use { input ->
                val buf = ByteArray(16 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    output.write(buf, 0, n)
                    require(output.size() <= maxBytes) { "Media response too large" }
                }
            }
            return output.toByteArray()
        }
    }

    suspend fun generate(
        playlistUrl: String,
        onCue: suspend (HlsSubtitleCue) -> Unit = {},
        onMediaPause: suspend (String) -> Unit = {},
        reportProgress: suspend (Progress) -> Unit
    ): Output = withContext(Dispatchers.IO) {
        var mcpRunId: String? = null
        try { coroutineScope {
            require(HlsSourceDetector.candidates(listOf(playlistUrl)).isNotEmpty()) {
                "Cần URL HTTPS của media playlist .m3u8"
            }
            require(localFixture == null || playlistUrl == localFixture.playlistUrl)
            // Only ordinary authorized direct requests. In particular, a
            // 401/403 never triggers another access path or an automatic retry.
            suspend fun readMedia(url: String, maxBytes: Int, minIntervalMs: Long = 0L): ByteArray {
                // The private, debug-only synthetic fixture never makes a network request.
                localFixture?.let { return it.read(url, maxBytes) }
                var retries = 0
                while (true) {
                    currentCoroutineContext().ensureActive()
                    try {
                        return requestBytes(url, maxBytes)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        if (e !is HlsMediaHttpException && e !is java.io.IOException) throw e
                        val reason = HlsMediaAccessPolicy.reason(e)
                        val wait = HlsMediaAccessPolicy.retryDelay(e, retries)
                        if (wait == null) {
                            android.util.Log.w("LiveSub_HlsResume", "media_paused reason=" + reason)
                            onMediaPause(reason)
                            throw HlsMediaPausedException(reason, e)
                        }
                        retries++
                        android.util.Log.w("LiveSub_HlsResume",
                            "media_retry=" + retries + " reason=" + reason + " wait_ms=" + wait)
                        kotlinx.coroutines.delay(maxOf(wait, minIntervalMs))
                    }
                }
            }

            val raw = readMedia(playlistUrl, 1024 * 1024)
            val manifest = HlsVodManifestParser.parse(playlistUrl, raw.toString(Charsets.UTF_8))
            val qaTrace = HlsQaTrace.open(context, manifest.items.size)
            val progressStore = HlsProgressStore(context.filesDir, playlistUrl, manifest.items)
            runCatching { progressStore.pruneOldFiles() }
            val restored = progressStore.load()
            val benchmark = HlsBenchmarkReport.open(context, progressStore.path(), manifest.items.size, restored)
            if (restored != null) benchmark.checkpoint(restored.nextSegment, restored.recognizedTurns,
                restored.untranslated, restored.writtenCues, restored.mediaElapsedMs,
                if (restored.complete) "source_complete" else "resumed")
            if (restored != null) {
                android.util.Log.i("LiveSub_HlsResume",
                    "checkpoint_loaded=true next_segment=" + restored.nextSegment +
                    " total=" + manifest.items.size + " completed=" + restored.complete +
                    " committed_cues=" + restored.writtenCues)
                for (cue in progressStore.cuesFromSrt(restored.vietnameseSrt)) {
                    currentCoroutineContext().ensureActive()
                    onCue(cue)
                }
                reportProgress(Progress(restored.nextSegment, manifest.items.size, restored.writtenCues))
                if (restored.complete) {
                    android.util.Log.i("LiveSub_HlsResume", "already_complete_cached=true")
                    return@coroutineScope Output(
                        restored.vietnameseSrt, restored.originalSrt,
                        restored.nextSegment, restored.writtenCues, restored.untranslated
                    )
                }
            }
            val resumeIndex = restored?.nextSegment ?: 0
            val runtimeId = McpHlsRuntime.begin(manifest.items.size, resumeIndex)
            mcpRunId = runtimeId
            android.util.Log.i(
                "LiveSub_Hls",
                "VOD playlist accepted; segments=" + manifest.items.size +
                    " duration_seconds=" + manifest.totalSeconds.toLong()
            )

            val vad = Vad(
                assetManager = context.assets,
                config = VadModelConfig(
                    sileroVadModelConfig = SileroVadModelConfig(
                        model = "model/silero_vad.onnx",
                        threshold = 0.45f,
                        minSilenceDuration = 0.42f,
                        minSpeechDuration = 0.12f,
                        windowSize = 512,
                        maxSpeechDuration = 8.0f
                    ),
                    sampleRate = 16000,
                    numThreads = 2
                )
            )
            val decoderSession = HlsTsAudioDecoder.Session()
            val translated = StringBuilder(restored?.vietnameseSrt.orEmpty())
            val original = StringBuilder(restored?.originalSrt.orEmpty())
            val dialogueContext = SourceDialogueWindow(maxTurns = 18, maxChars = 2200)
            // One memory per HLS video. Nothing leaks across different films.
            val filmAddressMemory = FilmAddressMemory()
            // HLS has no trustworthy diarization yet. Only direct vocatives in the
            // current source may constrain an API; U1 never anchors later turns.
            val directAddressDetector = AutomaticPronounContextLock()
            // Cloud-first ASR with a three-request cap; native SenseVoice runs
            // only on failure and remains protected by its own mutex.
            val balancedAsr = BalancedHlsAsr(context, engine)
            // Restore only recent source/context examples from the same video.
            val recentSources = ArrayDeque<HlsSavedSource>()
            val recentTranslations = ArrayDeque<HlsSavedTranslation>()
            restored?.recentSources?.forEach {
                dialogueContext.precedingAndRemember(1L, it.id, it.startMs,
                    it.endMs, it.language, it.source)
                recentSources.addLast(it)
            }
            restored?.recentTranslations?.forEach {
                filmAddressMemory.remember(it.source, it.vi, it.endMs)
                recentTranslations.addLast(it)
            }
            var processed = resumeIndex
            var processedMediaMs = restored?.mediaElapsedMs ?: 0L
            // Native VAD timestamps start from zero after each reset or restart.
            // This offset restores timestamps in the complete video's timeline.
            var vadInputBaseSamples = restored?.audioSamples ?: 0L
            var audioSamplesFedTotal = vadInputBaseSamples
            var activeVadStartSample: Long? = null
            var lastCheckpointIndex = resumeIndex
            var fetchWallMs = 0L
            var fetchCadenceWaitMs = 0L
            var fetchNetworkMs = 0L
            var decodeWallMs = 0L
            var recognizeWallMs = 0L
            var vadWallMs = 0L
            var asrWallMs = 0L
            var asrCalls = 0
            var asrAudioMs = 0L
            var asrGroqCalls = 0
            var asrCloudflareCalls = 0
            var asrGeminiCalls = 0
            var asrLocalFallbackCalls = 0
            var asrPeakPending = 0
            var asrReadyDrained = 0
            var previousWindowReadyAt = android.os.SystemClock.elapsedRealtime()
            val writtenCount = java.util.concurrent.atomic.AtomicInteger(restored?.writtenCues ?: 0)
            var recognizedCount = restored?.recognizedTurns ?: 0
            var noiseOnlyAsr = 0
            var overlongVadRegions = 0
            var longestVadRegionMs = 0L
            var asrSpeechChunks = 0
            var shortVadRegions = 0
            var droppedShortVadMs = 0L
            var totalVadRegions = 0
            var rescuedShortVadRegions = 0
            var untranslated = restored?.untranslated ?: 0
            var lastSubtitleEndMs = restored?.lastSubtitleEndMs ?: 0L
            val runStartedAtMs = android.os.SystemClock.elapsedRealtime()
            var activeWindow = processedMediaMs / WINDOW_MS

            data class BatchWork(
                val workerId: Int,
                val turns: List<HlsTranslationTurn>,
                val result: kotlinx.coroutines.Deferred<HlsBatchTranslationResult>
            )

            val pendingBatch = ArrayList<HlsTranslationTurn>(HlsAdaptiveBatchPolicy.BULK_CLOUD_BATCH_SIZE)
            val batchJobs = ArrayDeque<BatchWork>()
            val activeBatchWorkers = java.util.concurrent.atomic.AtomicInteger(0)
            val peakBatchWorkers = java.util.concurrent.atomic.AtomicInteger(0)
            val activeEditorWorkers = java.util.concurrent.atomic.AtomicInteger(0)
            val editorOutstanding = java.util.concurrent.atomic.AtomicInteger(0)
            val peakTotalWorkers = java.util.concurrent.atomic.AtomicInteger(0)
            var nextWorkerId = 1
            data class PendingAsr(
                val startMs: Long,
                val endMs: Long,
                val result: kotlinx.coroutines.Deferred<HlsAsrResult>
            )
            val pendingAsr = ArrayDeque<PendingAsr>()
            val hasCloudProviders = GroqKeyConfig.parse(engine.groqFreeApiKey).isNotEmpty() ||
                HlsCloudProviderRouter.configured(
                    engine.geminiApiKeys, engine.cloudflareAccountId, engine.cloudflareApiToken
                )
            val MAX_PENDING_ASR = HlsAdaptiveBatchPolicy.maxPendingAsr(hasCloudProviders)

            data class EditorWork(
                val turns: List<HlsTranslationTurn>,
                val drafts: Map<Long, String>,
                val needsPolish: Boolean = true,
                val checkpoint: CompletableDeferred<Unit>? = null
            )

            // One independent editor coroutine consumes completed translation batches in order.
            // This keeps eight translation slots available while Worker 9 polishes earlier batches.
            val editorQueue = Channel<EditorWork>(capacity = MAX_IN_FLIGHT_BATCHES)
            val editorJob = launch(Dispatchers.IO) {
                for (work in editorQueue) {
                    if (work.checkpoint != null) {
                        work.checkpoint.complete(Unit)
                        continue
                    }
                    val editorActive = activeEditorWorkers.incrementAndGet()
                    val totalActive = activeBatchWorkers.get() + editorActive
                    peakTotalWorkers.updateAndGet { previous -> maxOf(previous, totalActive) }
                    val firstTurnId = work.turns.first().id
                    val lastTurnId = work.turns.last().id
                    val editorStartedAt = android.os.SystemClock.elapsedRealtime()
                    android.util.Log.i(
                        "LiveSub_HlsWorker9",
                        "worker_started id=9 active_translation_workers=" + activeBatchWorkers.get() +
                            " active_total_workers=" + totalActive +
                            " batch_items=" + work.turns.size +
                            " first_turn=" + firstTurnId +
                            " last_turn=" + lastTurnId
                    )
                    val results = try {
                        if (!work.needsPolish) {
                            android.util.Log.i(
                                "LiveSub_HlsPolish",
                                "worker=9 skipped_clean_cloud_batch=true batch_items=" + work.turns.size
                            )
                            work.drafts
                        } else {
                            engine.polishBatchForExport(work.turns, work.drafts)
                        }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        android.util.Log.w("LiveSub_HlsWorker9", "worker_error=" + e.javaClass.simpleName)
                        work.drafts
                    } finally {
                        val remaining = activeEditorWorkers.decrementAndGet()
                        benchmark.event("worker_finished", 9, work.turns.size.toLong(),
                            android.os.SystemClock.elapsedRealtime() - editorStartedAt)
                        android.util.Log.i(
                            "LiveSub_HlsWorker9",
                            "worker_finished id=9 active=" + remaining +
                                " elapsed_ms=" + (android.os.SystemClock.elapsedRealtime() - editorStartedAt) +
                                " first_turn=" + firstTurnId +
                                " last_turn=" + lastTurnId
                        )
                    }

                    for (turn in work.turns) {
                        val vi = results[turn.id].orEmpty()
                        qaTrace.event("final", turn.id, turn.startMs, turn.endMs,
                            source = turn.source, translation = vi, language = turn.language,
                            note = if (vi.isBlank()) "untranslated" else "translated")
                        // Learn only from accepted final translations, in worker-9 order.
                        if (vi.isNotBlank()) {
                            filmAddressMemory.remember(turn.source, vi, turn.endMs)
                            synchronized(recentTranslations) {
                                recentTranslations.addLast(HlsSavedTranslation(turn.source, vi, turn.endMs))
                                while (recentTranslations.size > 18) recentTranslations.removeFirst()
                            }
                        }
                        val safeTranslation = if (vi.isBlank()) {
                            untranslated++
                            "[Chưa dịch] " + turn.source
                        } else vi

                        for (cue in splitHlsSubtitle(turn.startMs, turn.endMs, safeTranslation)) {
                            SrtTextWriter.append(
                                translated,
                                writtenCount.incrementAndGet(),
                                cue.startMs,
                                cue.endMs,
                                cue.text
                            )
                            if (vi.isNotBlank()) onCue(cue)
                        }
                    }
                    editorOutstanding.decrementAndGet()
                }
            }

            suspend fun emitOldestBatch() {
                val work = batchJobs.removeFirst()
                val batchResult = try {
                    work.result.await()
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    android.util.Log.w("LiveSub_HlsBatch", "batch_worker_error=" + e.javaClass.simpleName)
                    HlsBatchTranslationResult(emptyMap(), needsPolish = false)
                }
                editorOutstanding.incrementAndGet()
                editorQueue.send(EditorWork(work.turns, batchResult.translations, needsPolish = batchResult.needsPolish))
            }

            suspend fun drainBatchJobs() {
                while (batchJobs.isNotEmpty()) emitOldestBatch()
            }

            // A window is ready only after Worker 9 has actually written every
            // subtitle queued before the boundary. Queue order is preserved.
            suspend fun awaitEditorCheckpoint() {
                val completed = CompletableDeferred<Unit>()
                editorQueue.send(EditorWork(emptyList(), emptyMap(), needsPolish = false, checkpoint = completed))
                completed.await()
            }

            suspend fun emitReadyBatches() {
                while (batchJobs.firstOrNull()?.result?.isCompleted == true) {
                    emitOldestBatch()
                }
            }

            suspend fun flushPendingBatch() {
                if (pendingBatch.isEmpty()) return
                val turns = pendingBatch.map { turn ->
                    turn.copy(filmAddressHints = filmAddressMemory.hintsFor(turn.source, turn.startMs))
                }
                pendingBatch.clear()

                while (batchJobs.size >= McpHlsRuntime.limit(runtimeId)) emitOldestBatch()
                val workerId = nextWorkerId
                nextWorkerId = if (nextWorkerId >= MAX_IN_FLIGHT_BATCHES) 1 else nextWorkerId + 1
                val firstTurnId = turns.first().id
                val lastTurnId = turns.last().id

                val task = async(Dispatchers.IO) {
                    val workerStartedAt = android.os.SystemClock.elapsedRealtime()
                    val active = activeBatchWorkers.incrementAndGet()
                    val peak = peakBatchWorkers.updateAndGet { previous -> maxOf(previous, active) }
                    val total = active + activeEditorWorkers.get()
                    peakTotalWorkers.updateAndGet { previous -> maxOf(previous, total) }
                    android.util.Log.i(
                        "LiveSub_HlsWorker",
                        "worker_started id=" + workerId +
                            " active=" + active +
                            " active_total=" + total +
                            " peak=" + peak +
                            " turns=" + turns.size +
                            " first_turn=" + firstTurnId +
                            " last_turn=" + lastTurnId
                    )
                    try {
                        engine.translateBatchForExportDetailed(turns)
                    } finally {
                        val remaining = activeBatchWorkers.decrementAndGet()
                        benchmark.event("worker_finished", workerId, turns.size.toLong(),
                            android.os.SystemClock.elapsedRealtime() - workerStartedAt)
                        android.util.Log.i(
                            "LiveSub_HlsWorker",
                            "worker_finished id=" + workerId +
                                " active=" + remaining +
                                " elapsed_ms=" + (android.os.SystemClock.elapsedRealtime() - workerStartedAt) +
                                " turns=" + turns.size +
                                " first_turn=" + firstTurnId +
                                " last_turn=" + lastTurnId
                        )
                    }
                }

                batchJobs.addLast(BatchWork(workerId, turns, task))
                android.util.Log.d(
                    "LiveSub_HlsWorker",
                    "batch_queued worker=" + workerId +
                        " inflight=" + batchJobs.size +
                        " max_workers=" + MAX_IN_FLIGHT_BATCHES
                )

                // Keep at most eight translation jobs in flight. Worker 9 consumes
                // completed drafts independently, preserving batch order for the SRT writer.
                if (batchJobs.size >= McpHlsRuntime.limit(runtimeId)) emitOldestBatch()
            }

            suspend fun advanceWindow(nextWindow: Long) {
                if (nextWindow <= activeWindow) return
                flushPendingBatch()
                drainBatchJobs()
                awaitEditorCheckpoint()
                val now = android.os.SystemClock.elapsedRealtime()
                val elapsed = now - runStartedAtMs
                val windowWallMs = now - previousWindowReadyAt
                previousWindowReadyAt = now
                android.util.Log.i(
                    "LiveSub_HlsBatch",
                    "window_ready=" + activeWindow +
                        " content_start_ms=" + (activeWindow * WINDOW_MS) +
                        " content_end_ms=" + (nextWindow * WINDOW_MS) +
                        " skipped_empty_windows=" + (nextWindow - activeWindow - 1L).coerceAtLeast(0L) +
                        " wall_elapsed_ms=" + elapsed +
                        " window_wall_ms=" + windowWallMs +
                        " target_ms=" + WINDOW_TARGET_MS +
                        " window_target_met=" + (windowWallMs <= WINDOW_TARGET_MS) +
                        " first_window_target_met=" +
                        (if (activeWindow == 0L) (elapsed <= WINDOW_TARGET_MS).toString() else "not_first_window") +
                        " subtitles=" + writtenCount.get()
                )
                activeWindow = nextWindow
            }

            suspend fun enqueueTurn(turn: HlsTranslationTurn) {
                val window = turn.startMs / WINDOW_MS
                if (window > activeWindow) advanceWindow(window)
                pendingBatch.add(turn)
                val targetSize = HlsAdaptiveBatchPolicy.targetBatchSize(processedMediaMs, hasCloudProviders)
                if (pendingBatch.size >= targetSize) flushPendingBatch()
            }

            /**
             * Only this ordered consumer mutates dialogue memory and writes SRT.
             * Up to three API requests may run concurrently; their results are
             * emitted strictly in source timeline order, not network finish order.
             */
            suspend fun emitOldestAsr() {
                val task = pendingAsr.removeFirst()
                val result = task.result.await()
                asrWallMs += result.elapsedMs
                asrCalls++
                val providerId = when (result.provider) {
                    "groq_whisper_turbo" -> 1
                    "cloudflare_whisper_turbo" -> 2
                    "gemini_flash_lite_audio" -> 3
                    else -> 4
                }
                benchmark.event("asr_result", providerId, result.segments.size.toLong(), result.elapsedMs)
                asrLocalFallbackCalls += result.localCalls
                when (result.provider) {
                    "groq_whisper_turbo" -> asrGroqCalls++
                    "cloudflare_whisper_turbo" -> asrCloudflareCalls++
                    "gemini_flash_lite_audio" -> asrGeminiCalls++
                }
                qaTrace.event("asr_provider",
                    startMs = task.startMs, endMs = task.endMs,
                    note = result.provider.take(65))
                android.util.Log.i("LiveSub_ASR",
                    "asr_provider=" + result.provider +
                    " speech_ms=" + (task.endMs - task.startMs) +
                    " elapsed_ms=" + result.elapsedMs +
                    " segments=" + result.segments.size)

                for (item in result.segments.sortedBy { it.startMs }) {
                    currentCoroutineContext().ensureActive()
                    val from = maxOf(task.startMs + item.startMs, lastSubtitleEndMs)
                    val to = minOf(task.startMs + item.endMs, task.endMs)
                    if (to <= from) continue
                    val source = item.text.trim()
                    if (source.none { it.isLetterOrDigit() } ||
                        !HlsAsrWire.meaningful(source)) {
                        noiseOnlyAsr++
                        qaTrace.event("asr_noise", startMs = from, endMs = to)
                        continue
                    }
                    val language = item.lang
                    val turnId = (recognizedCount + 1).toLong()
                    val history = dialogueContext.precedingAndRemember(
                        sessionId = 1L,
                        utteranceId = turnId,
                        startMs = from,
                        endMs = to,
                        language = language,
                        source = source
                    )
                    val utteranceId = ++recognizedCount
                    original.append(StringBuilder().also {
                        SrtTextWriter.append(it, utteranceId, from, to, source)
                    })
                    qaTrace.event("asr", turnId, from, to,
                        source = source, language = language)
                    recentSources.addLast(HlsSavedSource(turnId,
                        from, to, language.take(20), source.take(320)))
                    while (recentSources.size > 18) recentSources.removeFirst()

                    enqueueTurn(HlsTranslationTurn(
                        id = turnId,
                        startMs = from,
                        endMs = to,
                        language = language,
                        source = source,
                        historyBefore = history,
                        confirmedPronouns = directAddressDetector.resolve(
                            source = source, speakerId = "U1", switched = false,
                            topology = ConversationTopology.DYADIC_CROSS_GENDER)
                    ))
                    lastSubtitleEndMs = to
                }
                if (result.segments.isEmpty())
                    qaTrace.event("asr_empty", startMs = task.startMs,
                        endMs = task.endMs, note = result.provider.take(65))
            }

            suspend fun drainPendingAsr() {
                while (pendingAsr.isNotEmpty()) emitOldestAsr()
            }

            // A completed head must not wait for 2 more speech regions in
            // sparse dialogue. Never emit a later result before its predecessor.
            suspend fun drainReadyAsr() {
                while (pendingAsr.isNotEmpty() && pendingAsr.first().result.isCompleted) {
                    emitOldestAsr()
                    asrReadyDrained++
                }
            }

            suspend fun drain() {
                while (!vad.empty()) {
                    currentCoroutineContext().ensureActive()
                    val speech = vad.front()
                    vad.pop()
                    val samples = speech.samples
                    val startSample = vadInputBaseSamples + speech.start.toLong()
                    val regionStart = startSample * 1000L / 16000L
                    val regionEnd = (startSample + samples.size) * 1000L / 16000L
                    val regionDurationMs = samples.size * 1000L / 16000L
                    totalVadRegions++
                    longestVadRegionMs = maxOf(longestVadRegionMs, regionDurationMs)
                    if (HlsVadBufferPolicy.isBelowMinimum(samples.size)) {
                        shortVadRegions++
                        droppedShortVadMs += regionDurationMs
                        qaTrace.event("short_vad", startMs = regionStart,
                            endMs = regionEnd)
                        continue
                    }
                    // The native VAD can emit >24 seconds if it ignores the
                    // built-in limit. Preserve every sample before dispatch.
                    if (samples.size < HlsVadBufferPolicy.SHORT_SPEECH_SAMPLES) {
                        rescuedShortVadRegions++
                        qaTrace.event("short_vad_rescued", startMs = regionStart, endMs = regionEnd)
                    }
                    val slices = HlsSpeechChunker.split(
                        samples, sampleRate = 16000, maxSeconds = 12)
                    if (slices.size > 1) {
                        overlongVadRegions++
                        val ms = regionDurationMs
                        longestVadRegionMs = maxOf(longestVadRegionMs, ms)
                        android.util.Log.w("LiveSub_HlsASR",
                            "long_vad_split=true duration_ms=" + ms +
                            " chunks=" + slices.size)
                    }
                    for (slice in slices) {
                        currentCoroutineContext().ensureActive()
                        val chunkStart = (startSample + slice.from) * 1000L / 16000L
                        val chunkEnd = (startSample + slice.until) * 1000L / 16000L
                        if (chunkEnd <= lastSubtitleEndMs) continue
                        val clip = if (slice.from == 0 && slice.until == samples.size)
                            samples else samples.copyOfRange(slice.from, slice.until)
                        asrSpeechChunks++
                        asrAudioMs += clip.size * 1000L / 16000L
                        pendingAsr.addLast(PendingAsr(chunkStart, chunkEnd,
                            async(Dispatchers.IO) { balancedAsr.transcribe(clip) }))
                        asrPeakPending = maxOf(asrPeakPending, pendingAsr.size)
                        if (pendingAsr.size >= MAX_PENDING_ASR) emitOldestAsr()
                    }
                }
            }

            /**
             * Commit an atomic prefix only after every writer has finished:
             * VAD -> ASR -> batch translators -> worker 9 -> SRT.
             * An interrupted batch remains after the previous checkpoint.
             */
            fun persistCheckpoint(nextSegment: Int, complete: Boolean = false): Boolean {
                if (nextSegment <= 0) return false
                try {
                    val finalRecent = synchronized(recentTranslations) {
                        recentTranslations.toList()
                    }
                    progressStore.save(HlsCheckpoint(
                        nextSegment = nextSegment,
                        audioSamples = audioSamplesFedTotal,
                        mediaElapsedMs = processedMediaMs,
                        recognizedTurns = recognizedCount,
                        writtenCues = writtenCount.get(),
                        untranslated = untranslated,
                        lastSubtitleEndMs = lastSubtitleEndMs,
                        originalSrt = original.toString(),
                        vietnameseSrt = translated.toString(),
                        recentSources = recentSources.toList(),
                        recentTranslations = finalRecent,
                        complete = complete
                    ))
                    lastCheckpointIndex = nextSegment
                    McpHlsRuntime.saved(runtimeId, nextSegment)
                    benchmark.checkpoint(nextSegment, recognizedCount, untranslated, writtenCount.get(),
                        processedMediaMs, if (complete) "source_complete" else "running")
                    android.util.Log.i("LiveSub_HlsResume",
                        "checkpoint_saved=true next_segment=" + nextSegment +
                        " total=" + manifest.items.size +
                        " last_turn=" + recognizedCount +
                        " written_cues=" + writtenCount.get() +
                        " complete=" + complete)
                    return true
                } catch (e: Exception) {
                    android.util.Log.w("LiveSub_HlsResume",
                        "checkpoint_error=" + e.javaClass.simpleName)
                    return false
                }
            }

            // One decoder coroutine prepares audio in order, ahead of the ASR loop.
            // Keep a bounded two-segment decode-ahead queue to limit CPU contention with SenseVoice;
            // the decoder only prepares two segments ahead. No recognizer is shared between
            // coroutines; VAD and SenseVoice remain entirely on the consumer.
            data class DecodedSegment(
                val index: Int,
                val pcm: FloatArray,
                val fetchMs: Long,
                val cadenceWaitMs: Long,
                val networkMs: Long,
                val decodeMs: Long,
                val reuseCount: Int,
                val createCount: Int
            )
            val decoded = Channel<DecodedSegment>(capacity = 2)
            val decoderJob = launch(Dispatchers.IO) {
                data class FetchedSegment(val bytes: ByteArray, val totalMs: Long, val waitMs: Long, val networkMs: Long)
                var download: Deferred<Result<FetchedSegment>>? = null
                var nextFetchAt = 0L
                suspend fun fetchAt(index: Int): FetchedSegment {
                    val before = android.os.SystemClock.elapsedRealtime()
                    // At most one segment per media-duration interval (minimum
                    // 1s). This is a conservative client cap, not a CDN guarantee.
                    val wait = nextFetchAt - android.os.SystemClock.elapsedRealtime()
                    if (wait > 0) kotlinx.coroutines.delay(wait)
                    while (McpHlsRuntime.fetchBlocked(runtimeId)) kotlinx.coroutines.delay(200)
                    val segment = manifest.items[index]
                    val interval = if (localFixture != null) 0L else HlsMediaAccessPolicy.cadenceMs(segment.seconds)
                    val networkStarted = android.os.SystemClock.elapsedRealtime()
                    val bytes = readMedia(segment.url, 8 * 1024 * 1024, interval)
                    val networkFinished = android.os.SystemClock.elapsedRealtime()
                    nextFetchAt = networkStarted + interval
                    return FetchedSegment(bytes, networkFinished - before, networkStarted - before, networkFinished - networkStarted)
                }
                try {
                    // Capture failures as values; an async child exception would
                    // otherwise cancel the consumer and Worker 9 before commit.
                    if (resumeIndex < manifest.items.size)
                        download = async(Dispatchers.IO) { HlsMediaAccessPolicy.captureFetch { fetchAt(resumeIndex) } }
                    for (index in resumeIndex until manifest.items.size) {
                        val segment = manifest.items[index]
                        currentCoroutineContext().ensureActive()
                        val (bytes, fetchMs, cadenceWaitMs, networkMs) = requireNotNull(download).await().getOrThrow()
                        download = if (index + 1 < manifest.items.size) {
                            async(Dispatchers.IO) { HlsMediaAccessPolicy.captureFetch { fetchAt(index + 1) } }
                        } else null
                        require(HlsVodManifestParser.isMpegTs(bytes)) {
                            "Segment " + (index + 1) + " is not ordinary MPEG-TS; cannot decode safely"
                        }
                        val started = android.os.SystemClock.elapsedRealtime()
                        val monoPcm = decoderSession.decodeTo16kMono(bytes)
                        val decodeMs = android.os.SystemClock.elapsedRealtime() - started
                        require(monoPcm.isNotEmpty()) {
                            "No decodable audio in segment " + (index + 1)
                        }
                        val expectedCount = (segment.seconds * 16000.0).roundToInt()
                        val pcm = if (kotlin.math.abs(monoPcm.size - expectedCount) <= 8000) {
                            if (monoPcm.size == expectedCount) monoPcm else monoPcm.copyOf(expectedCount)
                        } else {
                            throw IllegalStateException("Unexpected audio length at segment " + (index + 1))
                        }
                        decoded.send(DecodedSegment(index, pcm, fetchMs, cadenceWaitMs, networkMs, decodeMs,
                            decoderSession.reuseCount, decoderSession.createCount))
                    }
                } catch (e: CancellationException) {
                    decoded.close(e)
                    throw e
                } catch (e: Throwable) {
                    // Deliver the original fetch/decode cause to the consumer.
                    // Re-throwing from a launch child would cancel coroutineScope
                    // before the consumer could receive IOException and retry.
                    decoded.close(e)
                    android.util.Log.w("LiveSub_HlsResume",
                        "decoder_stopped=" + e.javaClass.simpleName)
                } finally {
                    download?.cancel()
                    decoded.close()
                    decoderSession.close()
                }
            }
            var latestCodecReused = 0
            var latestCodecCreated = 0
            var paused: HlsMediaPausedException? = null
            try {
                while (true) {
                    val received = decoded.receiveCatching()
                    if (received.isClosed) {
                        val cause = received.exceptionOrNull()
                        if (cause is HlsMediaPausedException) paused = cause
                        else if (cause != null) throw cause
                        break
                    }
                    val frame = received.getOrThrow()
                    currentCoroutineContext().ensureActive()
                    val index = frame.index
                    val segment = manifest.items[index]
                    fetchWallMs += frame.fetchMs
                    fetchCadenceWaitMs += frame.cadenceWaitMs
                    fetchNetworkMs += frame.networkMs
                    decodeWallMs += frame.decodeMs
                    latestCodecReused = frame.reuseCount
                    latestCodecCreated = frame.createCount
                    if (index == resumeIndex) android.util.Log.i("LiveSub_Hls", "First audio segment decoded")
                    val recognitionStartedAt = android.os.SystemClock.elapsedRealtime()
                    var pcmOffset = 0
                    while (pcmOffset < frame.pcm.size) {
                        currentCoroutineContext().ensureActive()
                        val end = minOf(frame.pcm.size, pcmOffset + HlsVadBufferPolicy.FEED_BLOCK_SAMPLES)
                        val block = frame.pcm.copyOfRange(pcmOffset, end)
                        val blockStart = audioSamplesFedTotal
                        val vadStartedAt = android.os.SystemClock.elapsedRealtime()
                        vad.acceptWaveform(block)
                        audioSamplesFedTotal += block.size.toLong()
                        val active = vad.isSpeechDetected()
                        activeVadStartSample = HlsVadBufferPolicy.activeStart(activeVadStartSample, blockStart, active)
                        vadWallMs += android.os.SystemClock.elapsedRealtime() - vadStartedAt
                        drain()
                        val activeSamples = activeVadStartSample?.let { audioSamplesFedTotal - it } ?: 0L
                        if (HlsVadBufferPolicy.shouldFlush(activeSamples, active)) {
                            val bufferedMs = activeSamples * 1000L / HlsVadBufferPolicy.SAMPLE_RATE
                            android.util.Log.w("LiveSub_HlsASR",
                                "native_active_vad_flush_ms=" + bufferedMs + " segment=" + (index + 1))
                            vad.flush()
                            drain()
                            vad.reset()
                            vadInputBaseSamples = audioSamplesFedTotal
                            activeVadStartSample = null
                        }
                        pcmOffset = end
                    }
                    drainReadyAsr()
                    recognizeWallMs += android.os.SystemClock.elapsedRealtime() - recognitionStartedAt
                    emitReadyBatches()

                    processed = index + 1
                    benchmark.segment(processed, frame.pcm.size, frame.fetchMs, frame.cadenceWaitMs, frame.networkMs, frame.decodeMs)
                    processedMediaMs += (segment.seconds * 1000.0).toLong()
                    // Sparse dialogue must not wait for a full batch or a 10-minute boundary.
                    // Early video flushes fast (20s); bulk video batches deeper (45s) to save API RPM.
                    if (pendingBatch.isNotEmpty() &&
                        processedMediaMs - pendingBatch.first().startMs >= HlsAdaptiveBatchPolicy.maxPartialWaitMs(processedMediaMs)) {
                        android.util.Log.i("LiveSub_HlsBatch",
                            "partial_batch_flush=true turns=" + pendingBatch.size +
                                " media_elapsed_ms=" + processedMediaMs)
                        flushPendingBatch()
                    }
                    val userPause = McpHlsRuntime.wantsPause(runtimeId)
                    if (processed < manifest.items.size &&
                        (userPause || HlsCheckpointPolicy.shouldCommit(processed, lastCheckpointIndex,
                            vad.isSpeechDetected()))) {
                        if (userPause) McpHlsRuntime.draining(runtimeId)
                        // Prefer a silent boundary. By 80 segments, force a flush
                        // to keep crash recovery bounded even during continuous speech.
                        val forced = vad.isSpeechDetected()
                        vad.flush()
                        drain()
                        drainPendingAsr()
                        flushPendingBatch()
                        drainBatchJobs()
                        awaitEditorCheckpoint()
                        val checkpointOk = persistCheckpoint(processed)
                        // Reset the native VAD safely without reloading its ONNX model.
                        vad.reset()
                        vadInputBaseSamples = audioSamplesFedTotal
                        activeVadStartSample = null
                        android.util.Log.i("LiveSub_HlsResume",
                            "checkpoint_vad_reset=true forced=" + forced +
                            " segment=" + processed)
                        if (userPause) {
                            McpHlsRuntime.update(runtimeId, processed, pendingAsr.size, pendingBatch.size, batchJobs.size,
                                activeBatchWorkers.get(), activeEditorWorkers.get(), editorOutstanding.get())
                            McpHlsRuntime.paused(runtimeId, checkpointOk)
                            while (McpHlsRuntime.fetchBlocked(runtimeId)) kotlinx.coroutines.delay(200)
                        }
                    }
                    McpHlsRuntime.update(runtimeId, processed, pendingAsr.size, pendingBatch.size, batchJobs.size,
                        activeBatchWorkers.get(), activeEditorWorkers.get(), editorOutstanding.get())
                    if (processed == 1 || processed % 10 == 0 || processed == manifest.items.size) {
                        android.util.Log.i("LiveSub_HlsProgress",
                            "segments=" + processed + "/" + manifest.items.size +
                                " recognized_turns=" + recognizedCount +
                                " written_cues=" + writtenCount.get() +
                                " pending_turns=" + pendingBatch.size +
                                " active_translators=" + activeBatchWorkers.get() +
                                " active_editor=" + activeEditorWorkers.get() +
                                " fetch_total_ms=" + fetchWallMs +
                                " fetch_cadence_wait_ms=" + fetchCadenceWaitMs +
                                " fetch_network_retry_ms=" + fetchNetworkMs +
                                " segments_this_run=" + (processed - resumeIndex) +
                                " media_elapsed_ms=" + processedMediaMs +
                                " decode_total_ms=" + decodeWallMs +
                                " recognize_total_ms=" + recognizeWallMs +
                                " vad_total_ms=" + vadWallMs +
                                " asr_inference_total_ms=" + asrWallMs +
                                 " asr_inference_calls=" + asrCalls +
                         " asr_groq_calls=" + asrGroqCalls +
                         " asr_cloudflare_calls=" + asrCloudflareCalls +
                         " asr_gemini_calls=" + asrGeminiCalls +
                         " asr_local_calls=" + asrLocalFallbackCalls +
                         " asr_inflight_peak=" + asrPeakPending +
                          " asr_ready_drained=" + asrReadyDrained +
                          " asr_pending=" + pendingAsr.size +
                             " speech_chunks=" + asrSpeechChunks +
                             " split_vad_regions=" + overlongVadRegions +
                             " longest_vad_region_ms=" + longestVadRegionMs +
                             " short_vad_regions=" + shortVadRegions +
                                " total_vad_regions=" + totalVadRegions +
                                " dropped_short_vad_ms=" + droppedShortVadMs +
                              " rescued_short_vad_regions=" + rescuedShortVadRegions +
                                " asr_input_audio_ms=" + asrAudioMs +
                                " codec_reused=" + latestCodecReused +
                                " codec_created=" + latestCodecCreated +
                                " decoded_queue_capacity=2" +
                                " audio_pipeline=decode_ahead_ordered_cloud_asr" +
                                " wall_elapsed_ms=" +
                                (android.os.SystemClock.elapsedRealtime() - runStartedAtMs))
                        reportProgress(Progress(processed, manifest.items.size, writtenCount.get()))
                    }
                }

                val vadFlushStarted = android.os.SystemClock.elapsedRealtime()
                vad.flush()
                vadWallMs += android.os.SystemClock.elapsedRealtime() - vadFlushStarted
                drain()
                drainPendingAsr()
                flushPendingBatch()
                drainBatchJobs()
                editorQueue.close()
                editorJob.join()
                val committed = persistCheckpoint(processed,
                    complete = paused == null && processed == manifest.items.size)
                if (paused != null) {
                    if (committed) benchmark.checkpoint(processed, recognizedCount, untranslated, writtenCount.get(),
                        processedMediaMs, "paused_" + paused.reason)
                    android.util.Log.i("LiveSub_HlsResume",
                        "safe_pause=true reason=" + paused.reason +
                        " next_segment=" + processed + " checkpoint_saved=" + committed)
                    reportProgress(Progress(processed, manifest.items.size, writtenCount.get()))
                }

                val elapsed = android.os.SystemClock.elapsedRealtime() - runStartedAtMs
                android.util.Log.i(
                    "LiveSub_HlsBatch",
                    "translation_complete=" + (paused == null && processed == manifest.items.size) +
                        " paused=" + (paused != null) + " segments=" + processed +
                        " recognized_turns=" + recognizedCount +
                        " translated_cues=" + writtenCount.get() +
                        " untranslated=" + untranslated +
                        " noise_only_asr_rejected=" + noiseOnlyAsr +
                        " peak_translation_workers=" + peakBatchWorkers.get() +
                        " peak_total_workers=" + peakTotalWorkers.get() +
                        " configured_translation_workers=" + MAX_IN_FLIGHT_BATCHES +
                        " configured_total_workers=" + (MAX_IN_FLIGHT_BATCHES + 1) +
                        " batch_size=" + BATCH_SIZE +
                        " vad_total_ms=" + vadWallMs +
                        " asr_inference_total_ms=" + asrWallMs +
                         " asr_inference_calls=" + asrCalls +
                         " asr_groq_calls=" + asrGroqCalls +
                         " asr_cloudflare_calls=" + asrCloudflareCalls +
                         " asr_gemini_calls=" + asrGeminiCalls +
                         " asr_local_calls=" + asrLocalFallbackCalls +
                         " asr_inflight_peak=" + asrPeakPending +
                          " asr_ready_drained=" + asrReadyDrained +
                          " asr_pending=" + pendingAsr.size +
                        " wall_elapsed_ms=" + elapsed
                )
                if (paused == null) require(writtenCount.get() > 0) { "ASR produced no subtitle lines" }
                Output(
                    vietnameseSrt = translated.toString(),
                    originalSrt = original.toString(),
                    segmentsProcessed = processed,
                    subtitles = writtenCount.get(),
                    untranslated = untranslated,
                    pauseReason = paused?.reason,
                    checkpointSaved = committed
                )
            } finally {
                benchmark.event("run_end", processed, recognizedCount.toLong(),
                    android.os.SystemClock.elapsedRealtime() - runStartedAtMs)
                decoderJob.cancel()
                decoded.cancel()
                editorQueue.cancel()
                editorJob.cancel()
                vad.release()
            }
        } } finally { mcpRunId?.let { McpHlsRuntime.end(it) } }
    }
}

/**
 * Decode MPEG-TS AAC with a reusable synchronous MediaCodec session.
 * A file gets its own MediaExtractor, while a compatible decoder is flushed and
 * re-used for the next segment. Only one thread ever calls this session at a time.
 * A MIME/sample-rate/channel change causes a fresh decoder to be configured.
 */
internal object HlsTsAudioDecoder {
    class Session(private val reuseOutputScratch: Boolean = true) : java.io.Closeable {
        private var outputScratch: FloatArray? = null
        private var shortScratch: ShortArray? = null
        var scratchAllocatedSamples: Long = 0L
            private set
        private var codec: MediaCodec? = null
        private var formatKey: String? = null
        var reuseCount: Int = 0
            private set
        var createCount: Int = 0
            private set

        private fun releaseCodec() {
            val previous = codec
            codec = null
            formatKey = null
            try { previous?.stop() } catch (_: Exception) {}
            try { previous?.release() } catch (_: Exception) {}
        }

        override fun close() {
            releaseCodec()
            outputScratch = null
            shortScratch = null
        }

        fun decodeTo16kMono(bytes: ByteArray): FloatArray {
            val extractor = MediaExtractor()
            val source = object : MediaDataSource() {
                override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
                    if (position >= bytes.size) return -1
                    val avail = minOf(size.toLong(), bytes.size - position).toInt()
                    System.arraycopy(bytes, position.toInt(), buffer, offset, avail)
                    return avail
                }
                override fun getSize(): Long = bytes.size.toLong()
                override fun close() {}
            }
            return try {
                extractor.setDataSource(source)
                decodeExtractorTo16kMono(extractor)
            } finally {
                extractor.release()
            }
        }

        fun decodeTo16kMono(file: File): FloatArray {
            val extractor = MediaExtractor()
            return try {
                extractor.setDataSource(file.absolutePath)
                decodeExtractorTo16kMono(extractor)
            } finally {
                extractor.release()
            }
        }

        private fun decodeExtractorTo16kMono(extractor: MediaExtractor): FloatArray {
                val track = (0 until extractor.trackCount).firstOrNull { i ->
                    extractor.getTrackFormat(i).getString(MediaFormat.KEY_MIME)
                        ?.startsWith("audio/") == true
                } ?: return FloatArray(0)
                extractor.selectTrack(track)
                val format = extractor.getTrackFormat(track)
                val mime = format.getString(MediaFormat.KEY_MIME) ?: return FloatArray(0)
                val inputSampleRate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                val inputChannels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT).coerceAtLeast(1)
                val key = "$mime|$inputSampleRate|$inputChannels"

                var active = codec
                if (active != null && formatKey == key) {
                    try {
                        active.flush()
                        reuseCount++
                    } catch (_: Exception) {
                        releaseCodec()
                        active = null
                    }
                } else {
                    releaseCodec()
                    active = null
                }

                if (active == null) {
                    val fresh = MediaCodec.createDecoderByType(mime)
                    try {
                        fresh.configure(format, null, null, 0)
                        fresh.start()
                    } catch (e: Exception) {
                        try { fresh.release() } catch (_: Exception) {}
                        throw e
                    }
                    codec = fresh
                    formatKey = key
                    createCount++
                    active = fresh
                }

                val decoder = active
                val info = MediaCodec.BufferInfo()
                var output = outputScratch ?: FloatArray(80_000).also { scratchAllocatedSamples += it.size.toLong() }
                var outputCount = 0
                var inputEnded = false
                var outputEnded = false
                var sampleRate = inputSampleRate
                var channels = inputChannels
                val startedAt = android.os.SystemClock.elapsedRealtime()
                var lastProgressAt = startedAt
                while (!outputEnded) {
                    val now = android.os.SystemClock.elapsedRealtime()
                    check(!HlsDecodePollingPolicy.stalled(now, lastProgressAt, startedAt)) {
                        "AAC decoder stalled before EOS"
                    }
                    var inputAdvanced = false
                    var outputAdvanced = false
                    if (!inputEnded) {
                        val index = decoder.dequeueInputBuffer(HlsDecodePollingPolicy.INPUT_WAIT_US)
                        if (index >= 0) {
                            inputAdvanced = true
                            val dst = decoder.getInputBuffer(index)!!
                            dst.clear()
                            val n = extractor.readSampleData(dst, 0)
                            if (n < 0) {
                                decoder.queueInputBuffer(
                                    index, 0, 0, 0L, MediaCodec.BUFFER_FLAG_END_OF_STREAM
                                )
                                inputEnded = true
                            } else {
                                decoder.queueInputBuffer(index, 0, n, extractor.sampleTime, 0)
                                extractor.advance()
                            }
                        }
                    }
                    val outputWait = HlsDecodePollingPolicy.outputWaitUs(inputAdvanced, inputEnded)
                    when (val index = decoder.dequeueOutputBuffer(info, outputWait)) {
                        MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                            outputAdvanced = true
                            val actual = decoder.outputFormat
                            sampleRate = actual.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                            channels = actual.getInteger(MediaFormat.KEY_CHANNEL_COUNT).coerceAtLeast(1)
                        }
                        else -> if (index >= 0) {
                            outputAdvanced = true
                            val buffer = decoder.getOutputBuffer(index)
                            if (buffer != null && info.size > 0) {
                                val rustMono = RustCore.resampleDirectPcm16To16kMono(
                                    buffer, info.offset, info.size, sampleRate, channels
                                )
                                if (rustMono != null) {
                                    if (rustMono.isNotEmpty()) {
                                        val needed = outputCount + rustMono.size
                                        if (needed > output.size) {
                                            var newSize = output.size * 2
                                            while (newSize < needed) newSize *= 2
                                            output = output.copyOf(newSize).also { scratchAllocatedSamples += it.size.toLong() }
                                        }
                                        System.arraycopy(rustMono, 0, output, outputCount, rustMono.size)
                                        outputCount += rustMono.size
                                    }
                                } else {
                                val data = buffer.duplicate().order(ByteOrder.LITTLE_ENDIAN)
                                data.position(info.offset)
                                data.limit(info.offset + info.size)
                                val shorts = data.slice().order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
                                val totalShorts = shorts.remaining()
                                val frames = totalShorts / channels
                                if (frames > 0) {
                                    var pcmShorts = shortScratch
                                    if (pcmShorts == null || pcmShorts.size < totalShorts) {
                                        pcmShorts = ShortArray(totalShorts)
                                        shortScratch = pcmShorts
                                    }
                                    shorts.get(pcmShorts, 0, totalShorts)
                                    val invScale = 1.0f / (32768.0f * channels)
                                    val integerStride = if (sampleRate % 16000 == 0) sampleRate / 16000 else 0
                                    if (integerStride > 0) {
                                        val needed = outputCount + (frames + integerStride - 1) / integerStride
                                        if (needed > output.size) {
                                            var newSize = output.size * 2
                                            while (newSize < needed) newSize *= 2
                                            output = output.copyOf(newSize).also { scratchAllocatedSamples += it.size.toLong() }
                                        }
                                        // Fast exact-ratio path (16/32/48 kHz) over bulk-copied ShortArray.
                                        var frame = 0
                                        if (channels == 2) {
                                            while (frame < frames) {
                                                val base = frame shl 1
                                                output[outputCount++] = (pcmShorts[base].toInt() + pcmShorts[base + 1].toInt()) * invScale
                                                frame += integerStride
                                            }
                                        } else if (channels == 1) {
                                            while (frame < frames) {
                                                output[outputCount++] = pcmShorts[frame].toInt() * invScale
                                                frame += integerStride
                                            }
                                        } else {
                                            while (frame < frames) {
                                                var sum = 0
                                                val base = frame * channels
                                                for (c in 0 until channels) {
                                                    sum += pcmShorts[base + c].toInt()
                                                }
                                                output[outputCount++] = sum * invScale
                                                frame += integerStride
                                            }
                                        }
                                    } else {
                                        val step = sampleRate.toDouble() / 16000.0
                                        val needed = outputCount + (frames / step).toInt() + 2
                                        if (needed > output.size) {
                                            var newSize = output.size * 2
                                            while (newSize < needed) newSize *= 2
                                            output = output.copyOf(newSize).also { scratchAllocatedSamples += it.size.toLong() }
                                        }
                                        var frame = 0.0
                                        while (frame < frames) {
                                            val f = frame.toInt()
                                            var sum = 0
                                            val base = f * channels
                                            for (c in 0 until channels) {
                                                sum += pcmShorts[base + c].toInt()
                                            }
                                            output[outputCount++] = sum * invScale
                                            frame += step
                                        }
                                    }
                                }
                                }
                            }
                            decoder.releaseOutputBuffer(index, false)
                            outputEnded = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                        }
                    }
                    if (inputAdvanced || outputAdvanced) {
                        lastProgressAt = android.os.SystemClock.elapsedRealtime()
                    }
                }
                check(outputEnded) { "AAC decoder did not reach EOS" }
                if (reuseOutputScratch) outputScratch = output
                // Publish an independent array; the decoder can reuse scratch immediately.
                return output.copyOf(outputCount)
        }
    }
}
