package com.baestheorem.inbox.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.baestheorem.inbox.data.MailMode
import com.baestheorem.inbox.data.MailStore
import com.baestheorem.inbox.data.SwipeActionKind
import com.baestheorem.inbox.data.SwipeConfig
import com.baestheorem.inbox.data.SwipeSlot
import com.baestheorem.inbox.gmail.BundleGroup
import com.baestheorem.inbox.gmail.ThreadSummary
import com.baestheorem.inbox.gmail.dateBucket
import com.baestheorem.inbox.gmail.formatWake
import com.baestheorem.inbox.gmail.initialsFor
import kotlinx.coroutines.launch

private sealed class ListEntry(val key: String) {
    class Header(val title: String) : ListEntry("h-$title")
    class Row(val t: ThreadSummary) : ListEntry("r-${t.id}")
    class Bundle(val b: BundleGroup) : ListEntry("b-${b.name}")
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MailList(
    store: MailStore,
    onOpen: (ThreadSummary) -> Unit,
    onSnooze: (ThreadSummary) -> Unit,
    onUnsubscribe: (ThreadSummary) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val mode by store.mode.collectAsState()
    val inbox by store.inbox.collectAsState()
    val items by store.listItems.collectAsState()
    val loading by store.loading.collectAsState()
    val error by store.errorText.collectAsState()
    val pinnedOnly by store.pinnedOnly.collectAsState()
    val expanded by store.expandedBundles.collectAsState()
    val swipes by SwipeConfig.slots.collectAsState()
    var refreshing by remember { mutableStateOf(false) }

    fun grouped(rows: List<ThreadSummary>): List<ListEntry> {
        val out = mutableListOf<ListEntry>()
        var cur: String? = null
        for (r in rows) {
            val b = dateBucket(r.ts)
            if (b != cur) {
                out.add(ListEntry.Header(b))
                cur = b
            }
            out.add(ListEntry.Row(r))
        }
        return out
    }

    // Mirrors paintList: Pinned, then bundles, then dated primary
    val entries: List<ListEntry> = when (mode) {
        is MailMode.Inbox -> buildList {
            if (inbox.pinned.isNotEmpty()) {
                add(ListEntry.Header("Pinned"))
                addAll(inbox.pinned.map { ListEntry.Row(it) })
            }
            if (!pinnedOnly) {
                addAll(inbox.bundles.map { ListEntry.Bundle(it) })
                addAll(grouped(inbox.primary))
            }
        }
        is MailMode.BundleFilter -> {
            val name = (mode as MailMode.BundleFilter).name
            var rows = inbox.bundles.firstOrNull { it.name == name }?.items ?: emptyList()
            if (pinnedOnly) rows = rows.filter { it.pinned }
            rows.map { ListEntry.Row(it) }
        }
        is MailMode.Snoozed -> {
            var rows = items
            if (pinnedOnly) rows = rows.filter { it.pinned }
            rows.map { ListEntry.Row(it) }
        }
        else -> {
            var rows = items
            if (pinnedOnly) rows = rows.filter { it.pinned }
            grouped(rows)
        }
    }

    val slots: List<SwipeActionKind> = when (mode) {
        is MailMode.Snoozed -> listOf(
            SwipeActionKind.TO_INBOX, SwipeActionKind.TO_INBOX,
            SwipeActionKind.SNOOZE, SwipeActionKind.SNOOZE,
        )
        is MailMode.Done -> listOf(
            SwipeActionKind.TO_INBOX, SwipeActionKind.TO_INBOX,
            SwipeActionKind.NONE, SwipeActionKind.NONE,
        )
        is MailMode.Sent -> List(4) { SwipeActionKind.NONE }
        else -> listOf(
            swipes[SwipeSlot.LEADING_SHORT] ?: SwipeSlot.LEADING_SHORT.defaultAction,
            swipes[SwipeSlot.LEADING_LONG] ?: SwipeSlot.LEADING_LONG.defaultAction,
            swipes[SwipeSlot.TRAILING_SHORT] ?: SwipeSlot.TRAILING_SHORT.defaultAction,
            swipes[SwipeSlot.TRAILING_LONG] ?: SwipeSlot.TRAILING_LONG.defaultAction,
        )
    }

    fun handle(kind: SwipeActionKind, t: ThreadSummary) {
        when (kind) {
            SwipeActionKind.DONE -> store.markDone(t)
            SwipeActionKind.SNOOZE -> onSnooze(t)
            SwipeActionKind.PIN -> store.togglePin(t)
            SwipeActionKind.SHARE -> store.share(t)
            SwipeActionKind.SPAM -> store.spam(t)
            SwipeActionKind.UNREAD -> store.toggleUnread(t)
            SwipeActionKind.TO_INBOX ->
                if (mode is MailMode.Snoozed) store.unsnooze(t) else store.restoreToInbox(t)
            SwipeActionKind.NONE -> Unit
        }
    }

    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = {
            refreshing = true
            scope.launch {
                store.refresh()
                refreshing = false
            }
        },
        modifier = Modifier.fillMaxSize().background(Theme.pageBg),
    ) {
        when {
            error != null && entries.isEmpty() -> ErrorState(error!!) {
                scope.launch { store.refresh() }
            }
            loading && entries.isEmpty() -> Box(
                Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator(color = Theme.blue) }
            entries.isEmpty() -> EmptyState(
                when (mode) {
                    is MailMode.Search -> "No results."
                    is MailMode.Inbox -> "You're all done."
                    else -> "Nothing here."
                }
            )
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(entries.size, key = { entries[it].key }) { index ->
                    when (val e = entries[index]) {
                        is ListEntry.Header -> Text(
                            e.title,
                            style = robotoStyle(12, FontWeight.Medium),
                            color = Theme.textSecondary,
                            modifier = Modifier.padding(start = 20.dp, top = 16.dp, bottom = 8.dp),
                        )
                        is ListEntry.Bundle -> BundleCard(
                            b = e.b,
                            expanded = e.b.name in expanded,
                            store = store,
                            slots = slots,
                            onToggle = {
                                store.expandedBundles.value =
                                    if (e.b.name in expanded) expanded - e.b.name else expanded + e.b.name
                            },
                            onOpen = onOpen,
                            onAction = { k, t -> handle(k, t) },
                            onUnsubscribe = onUnsubscribe,
                        )
                        is ListEntry.Row -> CardShell {
                            ThreadRow(
                                t = e.t,
                                store = store,
                                slots = slots,
                                showWake = mode is MailMode.Snoozed,
                                onOpen = onOpen,
                                onAction = { k -> handle(k, e.t) },
                                onUnsubscribe = onUnsubscribe,
                            )
                        }
                    }
                }
                if (store.nextPageToken != null && !pinnedOnly) {
                    item(key = "more") {
                        LaunchedEffect(Unit) { store.loadMore() }
                        Box(Modifier.fillMaxWidth().padding(vertical = 14.dp), Alignment.Center) {
                            CircularProgressIndicator(color = Theme.blue, modifier = Modifier.size(24.dp))
                        }
                    }
                }
                item(key = "tail") { Spacer(Modifier.height(88.dp)) }
            }
        }
    }
}

/** The rounded/bordered/shadowed card shell every list item sits in. */
@Composable
private fun CardShell(content: @Composable () -> Unit) {
    Box(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp)
            .shadow(1.dp, RoundedCornerShape(8.dp))
            .background(Theme.cardBg, RoundedCornerShape(8.dp))
            .border(1.dp, Theme.divider, RoundedCornerShape(8.dp))
    ) {
        content()
    }
}

@Composable
private fun ThreadRow(
    t: ThreadSummary,
    store: MailStore,
    slots: List<SwipeActionKind>,
    showWake: Boolean,
    inBundle: Boolean = false,
    onOpen: (ThreadSummary) -> Unit,
    onAction: (SwipeActionKind) -> Unit,
    onUnsubscribe: (ThreadSummary) -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }
    Box {
        SwipeableRow(
            leadingShort = slots[0],
            leadingLong = slots[1],
            trailingShort = slots[2],
            trailingLong = slots[3],
            onTap = { onOpen(t) },
            onLongPress = { menuOpen = true },
            perform = onAction,
        ) {
            CardRow(t, inBundle, showWake) { onUnsubscribe(t) }
        }
        RowMenu(
            open = menuOpen,
            onDismiss = { menuOpen = false },
            t = t,
            store = store,
            onSnoozeRequested = { onAction(SwipeActionKind.SNOOZE) },
        )
    }
}

@Composable
private fun RowMenu(
    open: Boolean,
    onDismiss: () -> Unit,
    t: ThreadSummary,
    store: MailStore,
    onSnoozeRequested: () -> Unit,
) {
    DropdownMenu(expanded = open, onDismissRequest = onDismiss) {
        MenuRow(if (t.pinned) "Unpin" else "Pin", "push_pin") {
            onDismiss()
            store.togglePin(t)
        }
        MenuRow("Snooze", "schedule") {
            onDismiss()
            onSnoozeRequested()
        }
        MenuRow("Share", "send") {
            onDismiss()
            store.share(t)
        }
        MenuRow(if (t.unread) "Mark read" else "Mark unread", "markunread") {
            onDismiss()
            store.toggleUnread(t)
        }
        if (t.unsub != null) {
            MenuRow("Unsubscribe", "unsubscribe") {
                onDismiss()
                store.unsubscribe(t.unsub, t.sender)
            }
        }
        MenuRow("Report spam", "report") {
            onDismiss()
            store.spam(t)
        }
        MenuRow("Open in Gmail", "open_in_new") {
            onDismiss()
            store.openUrl(t.permalink)
        }
    }
}

@Composable
fun CardRow(
    t: ThreadSummary,
    inBundle: Boolean = false,
    showWake: Boolean = false,
    onUnsubscribe: (() -> Unit)? = null,
) {
    Box(Modifier.background(if (t.unread) Theme.unreadBg else Theme.cardBg)) {
        if (t.unread) {
            Box(Modifier.width(4.dp).fillMaxSize().background(Theme.blue))
        }
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Box(
                Modifier.size(40.dp).background(Theme.blue, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    initialsFor(t.sender.removePrefix("To: ")),
                    style = robotoStyle(17, FontWeight.Medium),
                    color = Color.White,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    // Read mail recedes so unread mail pops
                    Text(
                        t.sender,
                        style = robotoStyle(14, if (t.unread) FontWeight.Bold else FontWeight.Normal),
                        color = if (t.unread) Theme.textPrimary else Theme.textSecondary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    if (t.pinned) {
                        Spacer(Modifier.width(6.dp))
                        MIcon("push_pin", size = 15, color = Theme.pinYellow)
                    }
                    if (t.unsub != null && onUnsubscribe != null) {
                        Spacer(Modifier.width(8.dp))
                        Text(
                            "Unsubscribe",
                            style = robotoStyle(12),
                            color = Theme.blue,
                            modifier = Modifier.clickable { onUnsubscribe() },
                        )
                    }
                    Spacer(Modifier.weight(1f))
                    if (t.unread) {
                        Box(Modifier.size(8.dp).background(Theme.blue, CircleShape))
                        Spacer(Modifier.width(6.dp))
                    }
                    Text(t.time, style = robotoStyle(12), color = Theme.textSecondary, maxLines = 1)
                }
                Text(
                    t.subject,
                    style = robotoStyle(14, if (t.unread) FontWeight.Medium else FontWeight.Normal),
                    color = if (t.unread) Theme.textPrimary else Theme.textSecondary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (t.snippet.isNotEmpty()) {
                    Text(
                        t.snippet,
                        style = robotoStyle(13),
                        color = if (t.unread) Theme.textPrimary else Theme.textSecondary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (showWake && t.wake != null) {
                    Row(
                        Modifier.padding(top = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        MIcon("schedule", size = 14, color = Theme.blue)
                        Spacer(Modifier.width(5.dp))
                        Text(
                            "Snoozed until ${formatWake(t.wake)}",
                            style = robotoStyle(12),
                            color = Theme.blue,
                        )
                    }
                }
                if (t.highlights.isNotEmpty() || t.attachments.isNotEmpty()) {
                    Row(
                        Modifier.padding(top = 6.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        for (c in t.highlights) Chip(c.icon, c.text)
                        for (a in t.attachments.take(2)) Chip("attach_file", a.filename)
                    }
                }
            }
        }
    }
}

/** Gray fill, hairline border, blue Material icon at 15px. */
@Composable
private fun Chip(icon: String, text: String) {
    Row(
        Modifier
            .background(Theme.chipBg, RoundedCornerShape(50))
            .border(1.dp, Theme.chipBorder, RoundedCornerShape(50))
            .padding(start = 7.dp, end = 10.dp, top = 3.dp, bottom = 3.dp)
            .widthIn(max = 175.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        MIcon(icon, size = 14, color = Theme.blue)
        Spacer(Modifier.width(5.dp))
        Text(
            text,
            style = robotoStyle(12),
            color = Theme.chipText,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** One rounded container holding the bundle head plus its members. */
@Composable
private fun BundleCard(
    b: BundleGroup,
    expanded: Boolean,
    store: MailStore,
    slots: List<SwipeActionKind>,
    onToggle: () -> Unit,
    onOpen: (ThreadSummary) -> Unit,
    onAction: (SwipeActionKind, ThreadSummary) -> Unit,
    onUnsubscribe: (ThreadSummary) -> Unit,
) {
    CardShell {
        Column {
            // The head swipes right to sweep every member to Done
            SwipeableRow(
                leadingShort = SwipeActionKind.DONE,
                leadingLong = SwipeActionKind.DONE,
                trailingShort = SwipeActionKind.NONE,
                trailingLong = SwipeActionKind.NONE,
                onTap = onToggle,
                perform = { store.sweep(b) },
            ) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .background(Theme.cardBg)
                        .padding(start = 16.dp, end = 6.dp, top = 8.dp, bottom = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        Modifier.size(36.dp).background(Color(b.color), CircleShape),
                        contentAlignment = Alignment.Center,
                    ) { MIcon(b.icon, size = 20, color = Color.White) }
                    Spacer(Modifier.width(14.dp))
                    Text(b.name, style = robotoStyle(14, FontWeight.Medium), color = Theme.textPrimary)
                    Spacer(Modifier.width(6.dp))
                    Text("${b.items.size}", style = robotoStyle(13), color = Theme.textSecondary)
                    if (!expanded) {
                        b.items.firstOrNull()?.let { first ->
                            Spacer(Modifier.width(8.dp))
                            Text(
                                "${first.sender}: ${first.subject}",
                                style = robotoStyle(13),
                                color = Theme.textSecondary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f),
                            )
                        }
                    } else {
                        Spacer(Modifier.weight(1f))
                    }
                    Box(
                        Modifier
                            .size(40.dp)
                            .clickable { store.sweep(b) },
                        contentAlignment = Alignment.Center,
                    ) { MIcon("done_all", size = 22, color = Theme.doneGreen) }
                }
            }
            if (expanded) {
                for (t in b.items) {
                    Box(Modifier.fillMaxWidth().height(1.dp).background(Color(0xFFEEEEEE)))
                    ThreadRow(
                        t = t,
                        store = store,
                        slots = slots,
                        showWake = false,
                        inBundle = true,
                        onOpen = onOpen,
                        onAction = { k -> onAction(k, t) },
                        onUnsubscribe = onUnsubscribe,
                    )
                }
            }
        }
    }
}

/** The sunny inbox-zero reward. */
@Composable
fun EmptyState(text: String) {
    Column(
        Modifier.fillMaxSize().background(Theme.pageBg),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        MIcon("wb_sunny", size = 72, color = Theme.sunYellow)
        Spacer(Modifier.height(14.dp))
        Text(text, style = robotoStyle(18), color = Theme.textSecondary)
    }
}

@Composable
private fun ErrorState(text: String, onRetry: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(text, style = robotoStyle(14), color = Theme.textSecondary)
        Spacer(Modifier.height(12.dp))
        Text(
            "Retry",
            style = robotoStyle(14, FontWeight.Medium),
            color = Theme.blue,
            modifier = Modifier.clickable(onClick = onRetry),
        )
    }
}
