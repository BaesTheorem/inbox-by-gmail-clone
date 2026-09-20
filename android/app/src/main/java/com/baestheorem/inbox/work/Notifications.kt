package com.baestheorem.inbox.work

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.app.PendingIntent
import android.graphics.Color
import android.net.Uri
import androidx.core.app.NotificationManagerCompat
import com.baestheorem.inbox.MainActivity
import com.baestheorem.inbox.R

object Notifications {
    const val CHANNEL_MAIL = "mail"
    const val CHANNEL_SNOOZE = "snooze"

    fun ensureChannels(context: Context) {
        val mgr = context.getSystemService(NotificationManager::class.java) ?: return
        mgr.createNotificationChannel(
            NotificationChannel(CHANNEL_MAIL, "New mail", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "A banner per new unread message in your inbox"
                enableLights(true)
                lightColor = Color.parseColor("#4285F4")
            }
        )
        mgr.createNotificationChannel(
            NotificationChannel(CHANNEL_SNOOZE, "Snoozed mail", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Fires when a snoozed thread comes back to the inbox"
            }
        )
    }

    /** Every banner opens the thread it is about, same rule as the Mac app. */
    private fun openThread(context: Context, threadId: String): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            action = Intent.ACTION_VIEW
            data = Uri.parse("inboxclone://thread/$threadId")
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        return PendingIntent.getActivity(
            context, threadId.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    fun post(
        context: Context,
        id: String,
        channel: String,
        title: String,
        text: String,
        subtext: String? = null,
        threadId: String = "",
    ) {
        val n = Notification.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_stat_inbox)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setColor(Color.parseColor("#4285F4"))
            .apply {
                if (!subtext.isNullOrEmpty()) setSubText(subtext)
                if (threadId.isNotEmpty()) {
                    setContentIntent(openThread(context, threadId))
                    // same thread replaces its stale banner instead of stacking
                    setGroup("thread-$threadId")
                }
            }
            .build()
        try {
            NotificationManagerCompat.from(context).notify(id.hashCode(), n)
        } catch (e: SecurityException) {
            // POST_NOTIFICATIONS not granted; the label flip already happened
        }
    }
}
