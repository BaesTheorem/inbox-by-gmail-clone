package com.baestheorem.inbox.work

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.baestheorem.inbox.auth.AuthStore
import com.baestheorem.inbox.data.Prefs
import com.baestheorem.inbox.data.SnoozeStore
import com.baestheorem.inbox.gmail.GmailClient
import com.baestheorem.inbox.gmail.decodeEntities
import com.baestheorem.inbox.gmail.headerValue
import com.baestheorem.inbox.gmail.parseAddr
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * The background half of the app: wake due snoozes, then post one banner per
 * new unread inbox message. Android's floor for periodic work is 15 minutes,
 * which is the same ballpark as the iPhone build's BGAppRefresh, minus the APNs
 * relay that gives that one instant delivery.
 */
class NewMailWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    companion object {
        private const val UNIQUE = "new-mail-poll"

        fun schedule(context: Context) {
            val req = PeriodicWorkRequestBuilder<NewMailWorker>(15, TimeUnit.MINUTES)
                .setConstraints(
                    Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
                )
                .build()
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(UNIQUE, ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(UNIQUE)
        }
    }

    override suspend fun doWork(): Result {
        if (!AuthStore.isSignedIn) return Result.success()
        try {
            SnoozeStore.processDue()
            if (!Prefs.notifyNewMail) return Result.success()
            val resp = GmailClient.listMessages(labelIds = listOf("INBOX", "UNREAD"), maxResults = 20)
            val ids = resp.messages.map { it.id }
            val seen = Prefs.seenUnread()
            val fresh = ids.filterNot { it in seen }
            Prefs.noteSeenUnread(ids)
            if (fresh.isEmpty()) return Result.success()
            for (m in GmailClient.batchMeta(fresh.take(5))) {
                val headers = m.payload?.headers ?: emptyList()
                val a = parseAddr(headerValue(headers, "From"))
                Notifications.post(
                    applicationContext,
                    id = "mail-${m.id}",
                    channel = Notifications.CHANNEL_MAIL,
                    title = a.name.ifEmpty { a.email },
                    text = decodeEntities(m.snippet ?: ""),
                    subtext = headerValue(headers, "Subject").ifEmpty { null },
                    threadId = m.threadId ?: "",
                )
            }
            return Result.success()
        } catch (e: IOException) {
            return Result.retry()
        }
    }
}
