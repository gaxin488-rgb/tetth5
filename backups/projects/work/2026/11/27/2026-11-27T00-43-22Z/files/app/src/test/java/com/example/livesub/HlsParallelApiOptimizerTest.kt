package com.example.livesub

import org.junit.Assert.*
import org.junit.Test

class HlsParallelApiOptimizerTest {

    @Test fun adaptiveBatchStartsSmallAndScalesUpInBulkPhase() {
        assertEquals(6, HlsAdaptiveBatchPolicy.targetBatchSize(0L, hasCloudLlm = true))
        assertEquals(6, HlsAdaptiveBatchPolicy.targetBatchSize(120_000L, hasCloudLlm = false))
        assertEquals(25, HlsAdaptiveBatchPolicy.targetBatchSize(200_000L, hasCloudLlm = true))
        assertEquals(15, HlsAdaptiveBatchPolicy.targetBatchSize(200_000L, hasCloudLlm = false))
        assertEquals(20_000L, HlsAdaptiveBatchPolicy.maxPartialWaitMs(60_000L))
        assertEquals(45_000L, HlsAdaptiveBatchPolicy.maxPartialWaitMs(300_000L))
        assertEquals(5, HlsAdaptiveBatchPolicy.maxPendingAsr(hasCloudAsr = true))
        assertEquals(1, HlsAdaptiveBatchPolicy.maxPendingAsr(hasCloudAsr = false))
    }

    @Test fun phraseCacheCachesShortRepeatedLinesPerRoleAndIgnoresLongLines() {
        val cache = HlsPhraseTranslationCache(maxEntries = 4)
        cache.put("ja", "ごめんなさい。", "aunt_nephew", "Cháu xin lỗi dì.")
        assertEquals("Cháu xin lỗi dì.", cache.get("ja", "ごめんなさい！", "aunt_nephew"))
        assertNull(cache.get("ja", "ごめんなさい。", "boss_staff"))

        // Long dialogue sentences must never be cached
        val longLine = "これはとても長い会話の文なのでキャッシュしてはいけません。"
        cache.put("ja", longLine, "aunt_nephew", "Đây là câu thoại rất dài.")
        assertNull(cache.get("ja", longLine, "aunt_nephew"))
    }

    @Test fun activeActiveSchedulerSpreadsLoadAcrossGroqAndBackupCloud() {
        val scheduler = HlsActiveActiveScheduler(groqSoftConcurrency = 2)
        assertEquals(
            HlsActiveActiveScheduler.Lane.GROQ_FIRST,
            scheduler.chooseLane(hasGroq = true, backupProviderCount = 2, groqInCooldown = false)
        )
        assertEquals(
            HlsActiveActiveScheduler.Lane.BACKUP_FIRST,
            scheduler.chooseLane(hasGroq = true, backupProviderCount = 2, groqInCooldown = true)
        )
        scheduler.withGroq {
            scheduler.withGroq {
                assertEquals(
                    HlsActiveActiveScheduler.Lane.BACKUP_FIRST,
                    scheduler.chooseLane(hasGroq = true, backupProviderCount = 2, groqInCooldown = false)
                )
            }
        }
        assertEquals(0 to 0, scheduler.inflightCounts())
    }

    @Test fun selectivePolishSkipsCleanCloudBatchesAndPolishesFallbackBatches() {
        assertFalse(
            HlsSelectivePolishPolicy.shouldPolish(
                totalTurns = 25,
                translatedCount = 25,
                fallbackItems = 0,
                hasCloudPolishProvider = true
            )
        )
        assertTrue(
            HlsSelectivePolishPolicy.shouldPolish(
                totalTurns = 25,
                translatedCount = 25,
                fallbackItems = 3,
                hasCloudPolishProvider = true
            )
        )
        assertFalse(
            HlsSelectivePolishPolicy.shouldPolish(
                totalTurns = 10,
                translatedCount = 8,
                fallbackItems = 2,
                hasCloudPolishProvider = false
            )
        )
    }
}
