package com.baestheorem.inbox.work

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.baestheorem.inbox.data.SnoozeStore

/**
 * One job per snoozed thread. It does the Gmail label flip (add INBOX, drop
 * Snoozed) and posts the banner. The periodic worker and app foreground both
 * call processDue() as well, so a job the OS drops is not a lost snooze.
 */
class SnoozeWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    companion object {
        const val KEY_THREAD_ID = "threadId"
        const val KEY_SUBJECT = "subject"
    }

    override suspend fun doWork(): Result {
        val threadId = inputData.getString(KEY_THREAD_ID) ?: return Result.success()
        val subject = inputData.getString(KEY_SUBJECT) ?: ""
        // Gone from the store means it was unsnoozed or already woken
        if (SnoozeStore.wake(threadId) == null) return Result.success()
        val ok = SnoozeStore.wakeThread(threadId)
        if (!ok) return Result.retry()
        Notifications.post(
            applicationContext,
            id = "snooze-$threadId",
            channel = Notifications.CHANNEL_SNOOZE,
            title = "Back in your inbox",
            text = subject.ifEmpty { "A snoozed thread is back" },
            threadId = threadId,
        )
        return Result.success()
    }
}
