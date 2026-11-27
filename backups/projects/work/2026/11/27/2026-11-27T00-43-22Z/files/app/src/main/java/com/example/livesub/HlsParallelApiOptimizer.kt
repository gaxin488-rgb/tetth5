package com.example.livesub

import java.util.concurrent.atomic.AtomicInteger

/**
 * Parallel API optimization primitives for long (~2,000+ HLS segment) movies:
 * 1. Adaptive batch sizing (fast small batches early, large 25-turn batches in bulk).
 * 2. Bounded short-phrase translation cache keyed by (lang, normalizedSource, roleKey).
 * 3. Active-Active multi-provider lane scheduler (Groq + Gemini/Cloudflare in parallel).
 * 4. Selective Worker 9 policy (skip redundant polish pass on clean cloud LLM batches).
 */
internal object HlsAdaptiveBatchPolicy {
    const val STARTUP_WINDOW_MEDIA_MS = 150_000L
    const val STARTUP_BATCH_SIZE = 6
    const val BULK_CLOUD_BATCH_SIZE = 25
    const val BULK_FALLBACK_BATCH_SIZE = 15
    const val STARTUP_PARTIAL_WAIT_MS = 20_000L
    const val BULK_PARTIAL_WAIT_MS = 45_000L

    fun targetBatchSize(mediaElapsedMs: Long, hasCloudLlm: Boolean): Int =
        if (mediaElapsedMs < STARTUP_WINDOW_MEDIA_MS) STARTUP_BATCH_SIZE
        else if (hasCloudLlm) BULK_CLOUD_BATCH_SIZE
        else BULK_FALLBACK_BATCH_SIZE

    fun maxPartialWaitMs(mediaElapsedMs: Long): Long =
        if (mediaElapsedMs < STARTUP_WINDOW_MEDIA_MS) STARTUP_PARTIAL_WAIT_MS
        else BULK_PARTIAL_WAIT_MS

    // Local SenseVoice is serialized; avoid queueing multiple clips behind its recognizer.
    fun maxPendingAsr(hasCloudAsr: Boolean): Int = if (hasCloudAsr) 5 else 1
}

internal class HlsPhraseTranslationCache(private val maxEntries: Int = 512) {
    private val map = object : LinkedHashMap<String, String>(64, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>?): Boolean =
            size > maxEntries
    }

    private fun isCacheableSource(source: String): Boolean {
        val trimmed = source.trim()
        if (trimmed.isEmpty() || trimmed.length > 18) return false
        val words = trimmed.split(Regex("\\s+")).size
        return words <= 4
    }

    private fun key(lang: String, source: String, roleKey: String): String {
        val normalized = source.trim()
            .trimEnd('。', '！', '!', '？', '?', '…', '.', ',', '、')
            .lowercase()
        return "$lang|$roleKey|$normalized"
    }

    @Synchronized
    fun get(lang: String, source: String, roleKey: String): String? {
        if (!isCacheableSource(source)) return null
        return map[key(lang, source, roleKey)]
    }

    @Synchronized
    fun put(lang: String, source: String, roleKey: String, vietnamese: String) {
        val vi = vietnamese.trim()
        if (!isCacheableSource(source) || vi.isEmpty() || vi.startsWith("[Chưa dịch]")) return
        map[key(lang, source, roleKey)] = vi
    }

    @Synchronized
    fun clear() {
        map.clear()
    }

    @Synchronized
    fun size(): Int = map.size
}

internal class HlsActiveActiveScheduler(private val groqSoftConcurrency: Int = 2) {
    enum class Lane { GROQ_FIRST, BACKUP_FIRST }

    private val groqInflight = AtomicInteger(0)
    private val backupInflight = AtomicInteger(0)

    fun chooseLane(
        hasGroq: Boolean,
        backupProviderCount: Int,
        groqInCooldown: Boolean
    ): Lane {
        if (!hasGroq) return Lane.BACKUP_FIRST
        if (backupProviderCount <= 0) return Lane.GROQ_FIRST
        if (groqInCooldown) return Lane.BACKUP_FIRST
        val g = groqInflight.get()
        val b = backupInflight.get()
        // Spread concurrent batches across Groq and Gemini/Cloudflare simultaneously
        // once Groq already has active in-flight work.
        return if (g >= groqSoftConcurrency && b < backupProviderCount * 2) {
            Lane.BACKUP_FIRST
        } else {
            Lane.GROQ_FIRST
        }
    }

    inline fun <T> withGroq(block: () -> T): T {
        groqInflight.incrementAndGet()
        return try {
            block()
        } finally {
            groqInflight.decrementAndGet()
        }
    }

    inline fun <T> withBackup(block: () -> T): T {
        backupInflight.incrementAndGet()
        return try {
            block()
        } finally {
            backupInflight.decrementAndGet()
        }
    }

    fun inflightCounts(): Pair<Int, Int> = groqInflight.get() to backupInflight.get()
}

internal data class HlsBatchTranslationResult(
    val translations: Map<Long, String>,
    val needsPolish: Boolean,
    val cacheHits: Int = 0,
    val cloudItems: Int = 0,
    val fallbackItems: Int = 0
)

internal object HlsSelectivePolishPolicy {
    /**
     * Skip the redundant Worker 9 LLM polish call when a cloud LLM cleanly
     * translated every uncached turn in the batch with film_context attached.
     */
    fun shouldPolish(
        totalTurns: Int,
        translatedCount: Int,
        fallbackItems: Int,
        hasCloudPolishProvider: Boolean
    ): Boolean {
        if (!hasCloudPolishProvider || totalTurns <= 0) return false
        return fallbackItems > 0 || translatedCount < totalTurns
    }
}
