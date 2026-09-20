package com.baestheorem.inbox

import androidx.test.platform.app.InstrumentationRegistry
import com.baestheorem.inbox.auth.AuthStore
import com.baestheorem.inbox.gmail.Bundles
import com.baestheorem.inbox.gmail.GmailClient
import com.baestheorem.inbox.gmail.summarize
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test

/**
 * End-to-end check against a real mailbox: token refresh, the REST layer, the
 * metadata cache and the classifier, on device.
 *
 * Skips itself unless a refresh token is handed in at run time, so it never
 * needs credentials in the repo:
 *
 *   ./gradlew :app:connectedDebugAndroidTest \
 *     -Pandroid.testInstrumentationRunnerArguments.refreshToken=...
 *
 * With client.properties baked into the build the client id and secret come
 * from there; otherwise pass clientId and clientSecret the same way.
 * It asserts shapes and counts only, never mail content.
 */
class GmailSmokeTest {
    private lateinit var refreshToken: String

    @Before
    fun setUp() {
        val args = InstrumentationRegistry.getArguments()
        refreshToken = args.getString("refreshToken") ?: ""
        assumeTrue("no refreshToken argument, skipping live Gmail test", refreshToken.isNotEmpty())
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        AuthStore.init(context)
        GmailClient.init(context)
        val id = args.getString("clientId") ?: AuthStore.embeddedClientId
        val secret = args.getString("clientSecret") ?: AuthStore.embeddedClientSecret
        AuthStore.setOwnClient(id, secret)
        AuthStore.refreshToken = refreshToken
        runBlocking { GmailClient.resetForNewAccount() }
        AuthStore.refreshToken = refreshToken
    }

    @Test
    fun readsInboxAndClassifiesIt() = runBlocking {
        val email = GmailClient.profileEmail()
        assertTrue("profile should return an address", email.contains("@"))

        val labels = GmailClient.listLabels()
        assertTrue("mailbox should expose system labels", labels.any { it.id == "INBOX" })
        val overrides = labels.mapNotNull { l ->
            val n = l.name ?: return@mapNotNull null
            if (n.startsWith("Bundle/")) l.id to n.removePrefix("Bundle/") else null
        }.toMap()

        val page = GmailClient.listMessages(labelIds = listOf("INBOX"), maxResults = 10)
        assumeTrue("inbox is empty, nothing to classify", page.messages.isNotEmpty())

        val msgs = GmailClient.batchMeta(page.messages.map { it.id })
        assertTrue("metadata fetch should return rows", msgs.isNotEmpty())

        val rows = summarize(msgs, overrides, outgoing = false)
        assertTrue("every row needs a thread id", rows.all { it.id.isNotEmpty() })
        assertTrue("every row needs a subject", rows.all { it.subject.isNotEmpty() })
        assertTrue("every row needs a sender", rows.all { it.sender.isNotEmpty() })
        assertTrue("rows sort newest first", rows.zipWithNext().all { (a, b) -> a.ts >= b.ts })
        assertTrue(
            "any bundle assigned must be one this build knows",
            rows.mapNotNull { it.bundle }.all { it in Bundles.order },
        )

        // Second pass must be served from the metadata cache, not the network
        val again = GmailClient.batchMeta(page.messages.map { it.id })
        assertTrue("cache should return the same row count", again.size == msgs.size)

        // The reader path: parse the newest thread end to end
        val detail = GmailClient.getThread(rows.first().id)
        assertTrue("thread should carry messages", detail.messages.isNotEmpty())
    }
}
