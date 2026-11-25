package com.baestheorem.inbox.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.baestheorem.inbox.data.Contact

/**
 * The contact list under a recipient field: name, then address, one row per contact,
 * same rows as the Mac app's #acDropdown. The caller decides which query to show.
 */
@Composable
fun ContactSuggestions(contacts: List<Contact>, onPick: (Contact) -> Unit) {
    if (contacts.isEmpty()) return
    Column(Modifier.fillMaxWidth().background(Theme.chipBg.copy(alpha = 0.5f))) {
        for (c in contacts) {
            Row(
                Modifier.fillMaxWidth().height(44.dp).clickable { onPick(c) }.padding(horizontal = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    c.label, style = robotoStyle(15), color = Theme.textPrimary,
                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                if (c.name.isNotEmpty()) {
                    Spacer(Modifier.width(8.dp))
                    Text(
                        c.email, style = robotoStyle(13), color = Theme.textSecondary,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Box(Modifier.fillMaxWidth().padding(start = 16.dp).height(1.dp).background(Theme.divider))
        }
    }
}

/** The text after the last comma, which is the address being typed. */
fun lastAddressToken(s: String): String = s.substringAfterLast(',').trim()

/** Replace the token being typed with the picked address and open the next one. */
fun replaceLastAddressToken(s: String, email: String): String {
    val parts = s.split(',').map { it.trim() }.toMutableList()
    parts[parts.size - 1] = email
    return parts.filter { it.isNotEmpty() }.joinToString(", ") + ", "
}
