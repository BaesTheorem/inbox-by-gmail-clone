package com.baestheorem.inbox.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.baestheorem.inbox.data.ContactStore
import com.baestheorem.inbox.gmail.RcptField
import com.baestheorem.inbox.gmail.Recipient
import com.baestheorem.inbox.gmail.ReplyPresets
import com.baestheorem.inbox.gmail.ReplyRecipients
import com.baestheorem.inbox.gmail.isEmailAddress
import com.baestheorem.inbox.gmail.parseAddr

private val badRed = Color(0xFFD93025)

/** The To / Cc / Bcc editor for a reply. Opens from the recipients line of the reply bar. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReplyRecipientsSheet(
    recipients: ReplyRecipients,
    presets: ReplyPresets,
    onChange: (ReplyRecipients) -> Unit,
    me: String = "",   // the account's address, never suggested
    onDismiss: () -> Unit,
) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val typed = remember { mutableStateMapOf<RcptField, String>() }
    val bad = remember { mutableStateMapOf<RcptField, Boolean>() }
    val contacts by ContactStore.contacts.collectAsState()

    // Suggestions for the address being typed in a field. People already in any field
    // and the account itself stay out.
    fun suggestions(f: RcptField): List<com.baestheorem.inbox.data.Contact> {
        val taken = (recipients.to + recipients.cc + recipients.bcc).map { it.key }.toMutableSet()
        taken.add(me.lowercase())
        return ContactStore.suggestions(lastAddressToken(typed[f] ?: ""), taken)
    }

    // Typed text becomes recipients. Text that is not an address stays, in red.
    fun commit(f: RcptField, current: ReplyRecipients): ReplyRecipients {
        var next = current
        val rest = mutableListOf<String>()
        for (part in (typed[f] ?: "").split(',', ';', '\n')) {
            val p = part.trim()
            if (p.isEmpty()) continue
            val a = parseAddr(p)
            if (isEmailAddress(a.email)) next = next.add(Recipient(a.name.ifEmpty { a.email }, a.email), f)
            else rest.add(p)
        }
        typed[f] = rest.joinToString(", ")
        bad[f] = rest.isNotEmpty()
        return next
    }

    fun close() {
        var next = recipients
        for (f in RcptField.entries) next = commit(f, next)
        if (next != recipients) onChange(next)
        onDismiss()
    }

    ModalBottomSheet(onDismissRequest = { close() }, sheetState = state, containerColor = Theme.cardBg) {
        Column(
            Modifier.fillMaxWidth().navigationBarsPadding().imePadding()
                .verticalScroll(rememberScrollState()).padding(bottom = 12.dp)
        ) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp).height(44.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Recipients", style = robotoStyle(16, FontWeight.Medium), color = Theme.textPrimary)
                Spacer(Modifier.weight(1f))
                Text(
                    "Done", style = robotoStyle(15, FontWeight.Medium), color = Theme.blue,
                    modifier = Modifier.clickable { close() }.padding(8.dp),
                )
            }
            Row(Modifier.padding(horizontal = 20.dp, vertical = 8.dp)) {
                PresetButton("reply", "Reply", recipients.matches(presets.reply)) {
                    typed.clear(); bad.clear(); onChange(presets.reply)
                }
                if (presets.allDiffers) {
                    Spacer(Modifier.width(8.dp))
                    PresetButton("reply_all", "Reply all", recipients.matches(presets.all)) {
                        typed.clear(); bad.clear(); onChange(presets.all)
                    }
                }
            }
            for (f in RcptField.entries) {
                Text(
                    f.label, style = robotoStyle(12, FontWeight.Medium), color = Theme.textSecondary,
                    modifier = Modifier.padding(start = 20.dp, top = 10.dp),
                )
                for (r in recipients.get(f)) {
                    RecipientRow(r, f, onMove = { onChange(recipients.add(r, it)) }, onRemove = { onChange(recipients.remove(r)) })
                }
                Box(Modifier.fillMaxWidth().height(44.dp).padding(horizontal = 20.dp), Alignment.CenterStart) {
                    val text = typed[f] ?: ""
                    BasicTextField(
                        value = text,
                        onValueChange = { typed[f] = it; bad[f] = false; ContactStore.refreshIfStale() },
                        singleLine = true,
                        textStyle = robotoStyle(15).copy(color = if (bad[f] == true) badRed else Theme.textPrimary),
                        cursorBrush = SolidColor(Theme.blue),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = {
                            val next = commit(f, recipients)
                            if (next != recipients) onChange(next)
                        }),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    if (text.isEmpty()) Text("Add an address", style = robotoStyle(15), color = Theme.textFaint)
                }
                // A pick becomes a chip; addresses typed before it in the same field do too.
                ContactSuggestions(remember(typed[f], contacts, recipients) { suggestions(f) }) { c ->
                    typed[f] = (typed[f] ?: "").substringBeforeLast(',', "")
                    val next = commit(f, recipients).add(Recipient(c.label, c.email), f)
                    onChange(next)
                }
                Box(Modifier.fillMaxWidth().height(1.dp).background(Theme.divider))
            }
        }
    }
}

@Composable
private fun PresetButton(icon: String, title: String, on: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(8.dp)
    Row(
        Modifier
            .height(36.dp)
            .background(if (on) Theme.blue.copy(alpha = 0.12f) else Color.Transparent, shape)
            .border(1.dp, if (on) Theme.blue else Theme.divider, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        MIcon(icon, size = 18, color = if (on) Theme.blue else Theme.textSecondary)
        Spacer(Modifier.width(6.dp))
        Text(title, style = robotoStyle(14, FontWeight.Medium), color = if (on) Theme.blue else Theme.textSecondary)
    }
}

@Composable
private fun RecipientRow(r: Recipient, field: RcptField, onMove: (RcptField) -> Unit, onRemove: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    Row(
        Modifier.fillMaxWidth().height(48.dp).padding(start = 20.dp, end = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(r.name, style = robotoStyle(15), color = Theme.textPrimary, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (r.name != r.email) {
                Text(r.email, style = robotoStyle(12), color = Theme.textSecondary, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        Box {
            Box(Modifier.size(40.dp).clickable { menu = true }, Alignment.Center) { MIcon("more_vert", size = 20) }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                for (g in RcptField.entries) {
                    if (g == field) continue
                    DropdownMenuItem(
                        text = { Text("Move to ${g.label}", style = robotoStyle(14)) },
                        onClick = { menu = false; onMove(g) },
                    )
                }
            }
        }
        Box(Modifier.size(40.dp).clickable(onClickLabel = "Remove ${r.email}", onClick = onRemove), Alignment.Center) {
            MIcon("close", size = 20)
        }
    }
}
