package com.baestheorem.inbox.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.FileProvider
import com.baestheorem.inbox.data.MailStore
import com.baestheorem.inbox.gmail.ATTACH_HOST
import com.baestheorem.inbox.gmail.CID_HOST
import com.baestheorem.inbox.gmail.GmailClient
import com.baestheorem.inbox.gmail.ThreadDetail
import com.baestheorem.inbox.gmail.ThreadSummary
import com.baestheorem.inbox.gmail.UnsubInfo
import com.baestheorem.inbox.gmail.UnsubMethod
import com.baestheorem.inbox.gmail.gmailPermalink
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException

@Composable
fun ThreadScreen(
    store: MailStore,
    threadId: String,
    onClose: () -> Unit,
    onSnooze: (ThreadSummary) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var detail by remember(threadId) { mutableStateOf<ThreadDetail?>(null) }
    var pinned by remember(threadId) { mutableStateOf(false) }
    var replyText by remember(threadId) { mutableStateOf("") }
    var replyAll by remember(threadId) { mutableStateOf(false) }
    var sending by remember { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
    var confirmUnsub by remember { mutableStateOf(false) }

    LaunchedEffect(threadId) {
        try {
            val d = store.loadThread(threadId)
            detail = d
            pinned = d.pinned
        } catch (e: IOException) {
            store.showSnack("Couldn't load thread (${store.shortError(e)})")
            onClose()
        }
    }

    // Reader actions need a list-row shape; build one from what the reader knows.
    fun stub(): ThreadSummary = ThreadSummary(
        id = threadId,
        messageId = detail?.messages?.lastOrNull()?.id ?: "",
        sender = detail?.messages?.firstOrNull()?.sender ?: "",
        senderEmail = detail?.messages?.firstOrNull()?.senderEmail ?: "",
        subject = detail?.subject ?: "(email)",
        snippet = "",
        time = "",
        ts = 0,
        unread = false,
        pinned = pinned,
        important = false,
        permalink = gmailPermalink(threadId),
    )

    Column(Modifier.fillMaxSize().background(Theme.pageBg)) {
        // .reader-bar: white, hairline bottom, gray icon buttons, green Done
        Row(
            Modifier
                .fillMaxWidth()
                .background(Theme.cardBg)
                .statusBarsPadding()
                .height(56.dp)
                .padding(horizontal = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            BarIcon("arrow_back") { onClose() }
            Text(
                detail?.subject ?: "",
                style = robotoStyle(17),
                color = Theme.textPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f).padding(start = 4.dp),
            )
            BarIcon("push_pin", if (pinned) Theme.pinYellow else Theme.textSecondary) {
                // togglePin flips from the row it is handed, so pass the pre-toggle state
                store.togglePin(stub().copy(pinned = pinned))
                pinned = !pinned
            }
            BarIcon("playlist_add_check", Theme.doneGreen) {
                store.markDone(stub())
                onClose()
            }
            Box {
                BarIcon("more_vert") { menuOpen = true }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    MenuRow("Snooze", "schedule") {
                        menuOpen = false
                        onSnooze(stub())
                    }
                    MenuRow("Share", "send") {
                        menuOpen = false
                        store.share(stub())
                    }
                    MenuRow("Mark unread", "markunread") {
                        menuOpen = false
                        store.toggleUnread(stub().copy(unread = false))
                        onClose()
                    }
                    MenuRow("Report spam", "report") {
                        menuOpen = false
                        store.spam(stub())
                        onClose()
                    }
                    MenuRow("Open in Gmail", "open_in_new") {
                        menuOpen = false
                        store.openUrl(gmailPermalink(threadId))
                    }
                }
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(Theme.divider))

        val d = detail
        if (d == null) {
            Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator(color = Theme.blue) }
        } else {
            d.unsub?.let { u -> UnsubBanner(d, u) { confirmUnsub = true } }
            Box(Modifier.weight(1f)) { ThreadWebView(store, d) }
            ReplyBar(
                store = store,
                detail = d,
                text = replyText,
                onText = { replyText = it },
                replyAll = replyAll,
                onToggleReplyAll = { replyAll = !replyAll },
                sending = sending,
                onSend = {
                    val body = replyText
                    sending = true
                    scope.launch {
                        try {
                            store.sendReply(d, body, replyAll)
                            replyText = ""
                            store.showSnack("Sent")
                        } catch (e: IOException) {
                            store.showSnack("Send failed")
                        }
                        sending = false
                    }
                },
            )
        }
    }

    if (confirmUnsub && detail?.unsub != null) {
        val d = detail!!
        val u = d.unsub!!
        val sender = d.messages.firstOrNull()?.sender ?: "this sender"
        AlertDialog(
            onDismissRequest = { confirmUnsub = false },
            title = { Text("Unsubscribe from $sender?", style = robotoStyle(17, FontWeight.Medium)) },
            text = {
                Text(
                    when (u.method) {
                        UnsubMethod.ONE_CLICK -> "Sends the one-click unsubscribe request straight to the sender."
                        UnsubMethod.MAILTO -> "Sends an unsubscribe email from your address."
                        UnsubMethod.LINK -> "Opens the sender's unsubscribe page in your browser."
                    },
                    style = robotoStyle(14),
                    color = Theme.textSecondary,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmUnsub = false
                    store.unsubscribe(u, sender)
                }) { Text("Unsubscribe", color = Theme.blue) }
            },
            dismissButton = {
                TextButton(onClick = { confirmUnsub = false }) {
                    Text("Cancel", color = Theme.textSecondary)
                }
            },
        )
    }
}

@Composable
private fun BarIcon(name: String, color: Color = Theme.textSecondary, onClick: () -> Unit) {
    Box(
        Modifier.size(40.dp).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { MIcon(name, size = 22, color = color) }
}

/** The yellow mailing-list banner with the blue filled button. */
@Composable
private fun UnsubBanner(d: ThreadDetail, u: UnsubInfo, onAct: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 10.dp)
            .background(Theme.bannerBg, RoundedCornerShape(8.dp))
            .border(1.dp, Theme.bannerBorder, RoundedCornerShape(8.dp))
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        MIcon("unsubscribe", size = 18, color = Theme.bannerIcon)
        Spacer(Modifier.width(10.dp))
        Text(
            "You're subscribed to this mailing list.",
            style = robotoStyle(13),
            color = Theme.bannerText,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(8.dp))
        Box(
            Modifier
                .background(Theme.blue, RoundedCornerShape(6.dp))
                .clickable(onClick = onAct)
                .padding(horizontal = 14.dp, vertical = 7.dp)
        ) {
            Text("Unsubscribe", style = robotoStyle(13, FontWeight.Medium), color = Color.White)
        }
    }
}

/** Bordered editor plus the 44dp blue send square. */
@Composable
private fun ReplyBar(
    store: MailStore,
    detail: ThreadDetail,
    text: String,
    onText: (String) -> Unit,
    replyAll: Boolean,
    onToggleReplyAll: () -> Unit,
    sending: Boolean,
    onSend: () -> Unit,
) {
    val canSend = text.isNotBlank() && !sending
    Column(Modifier.background(Theme.cardBg).imePadding().navigationBarsPadding()) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(Theme.divider))
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            if (store.replyAllAvailable(detail)) {
                Box(
                    Modifier.size(width = 36.dp, height = 44.dp).clickable(onClick = onToggleReplyAll),
                    contentAlignment = Alignment.Center,
                ) {
                    MIcon("people", size = 20, color = if (replyAll) Theme.blue else Theme.textSecondary)
                }
                Spacer(Modifier.width(8.dp))
            }
            Box(
                Modifier
                    .weight(1f)
                    .background(Theme.cardBg, RoundedCornerShape(8.dp))
                    .border(1.dp, Theme.divider, RoundedCornerShape(8.dp))
                    .padding(horizontal = 12.dp, vertical = 12.dp)
            ) {
                BasicTextField(
                    value = text,
                    onValueChange = onText,
                    textStyle = robotoStyle(14).copy(color = Theme.textPrimary),
                    cursorBrush = SolidColor(Theme.blue),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (text.isEmpty()) {
                    Text(
                        if (replyAll) "Reply all…" else "Reply…",
                        style = robotoStyle(14),
                        color = Theme.textFaint,
                    )
                }
            }
            Spacer(Modifier.width(8.dp))
            Box(
                Modifier
                    .size(44.dp)
                    .background(
                        if (canSend) Theme.blue else Theme.textSecondary.copy(alpha = 0.5f),
                        RoundedCornerShape(8.dp),
                    )
                    .clickable(enabled = canSend, onClick = onSend),
                contentAlignment = Alignment.Center,
            ) {
                if (sending) {
                    CircularProgressIndicator(color = Color.White, modifier = Modifier.size(20.dp))
                } else {
                    MIcon("send", size = 20, color = Color.White)
                }
            }
        }
    }
}

/**
 * The reader WebView. Two hosts ride inside the document: CID_HOST is answered
 * from the Gmail attachment endpoint so embedded images render, and ATTACH_HOST
 * links are intercepted and handed to whatever app opens that file type.
 */
@Composable
private fun ThreadWebView(store: MailStore, detail: ThreadDetail) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val loadedId = remember { mutableStateOf<String?>(null) }
    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { ctx ->
            WebView(ctx).apply {
                setBackgroundColor(Theme.pageBg.toArgb())
                settings.javaScriptEnabled = true // only our own quote-toggle script
                settings.loadWithOverviewMode = true
                settings.useWideViewPort = true
                settings.builtInZoomControls = true
                settings.displayZoomControls = false
                settings.blockNetworkLoads = false
                webViewClient = object : WebViewClient() {
                    override fun shouldInterceptRequest(
                        view: WebView,
                        request: WebResourceRequest,
                    ): WebResourceResponse? {
                        val url = request.url
                        if (url.host != CID_HOST) return null
                        val parts = url.pathSegments
                        if (parts.size < 2) return null
                        val mime = url.getQueryParameter("mime") ?: "image/png"
                        // These bytes arrived with the mail, so rendering them
                        // tells the sender nothing.
                        val data = runBlocking {
                            runCatching { GmailClient.attachmentData(parts[0], parts[1]) }.getOrNull()
                        } ?: return null
                        return WebResourceResponse(mime, null, ByteArrayInputStream(data))
                    }

                    override fun shouldOverrideUrlLoading(
                        view: WebView,
                        request: WebResourceRequest,
                    ): Boolean {
                        val url = request.url
                        if (url.host == ATTACH_HOST) {
                            val parts = url.pathSegments
                            if (parts.size >= 2) {
                                openAttachment(
                                    store, scope, context,
                                    messageId = parts[0],
                                    attachmentId = parts[1],
                                    name = url.getQueryParameter("name") ?: "attachment",
                                    mime = url.getQueryParameter("mime") ?: "application/octet-stream",
                                )
                            }
                            return true
                        }
                        // Email links open in the browser, never inside the reader
                        store.openUrl(url.toString())
                        return true
                    }
                }
            }
        },
        update = { web ->
            // Track what is loaded in Compose state: View.setTag(key, _) only
            // accepts an application-specific resource id and throws otherwise.
            if (loadedId.value != detail.id) {
                loadedId.value = detail.id
                web.loadDataWithBaseURL(
                    "https://mail.google.com/",
                    buildThreadHtml(detail),
                    "text/html",
                    "utf-8",
                    null,
                )
            }
        },
    )
}

private fun openAttachment(
    store: MailStore,
    scope: kotlinx.coroutines.CoroutineScope,
    context: android.content.Context,
    messageId: String,
    attachmentId: String,
    name: String,
    mime: String,
) {
    store.showSnack("Downloading $name")
    scope.launch {
        try {
            val bytes = GmailClient.attachmentData(messageId, attachmentId)
            val file = withContext(Dispatchers.IO) {
                val dir = File(context.cacheDir, "attachments").apply { mkdirs() }
                File(dir, name.replace('/', '-')).apply { writeBytes(bytes) }
            }
            val uri: Uri = FileProvider.getUriForFile(
                context, "${context.packageName}.files", file,
            )
            val view = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, mime)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            store.dismissSnack()
            try {
                context.startActivity(view)
            } catch (e: ActivityNotFoundException) {
                store.showSnack("No app can open $name")
            }
        } catch (e: IOException) {
            store.showSnack("Couldn't download attachment")
        }
    }
}
