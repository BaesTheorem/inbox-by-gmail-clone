package com.baestheorem.inbox.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.baestheorem.inbox.gmail.SnoozePreset
import com.baestheorem.inbox.gmail.formatWake
import java.util.Calendar

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SnoozeSheet(onDismiss: () -> Unit, onPick: (Long, String) -> Unit) {
    val context = LocalContext.current
    val state = rememberModalBottomSheetState()
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = state,
        containerColor = Theme.cardBg,
    ) {
        Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(bottom = 12.dp)) {
            Text(
                "SNOOZE UNTIL…",
                style = robotoStyle(12, FontWeight.Medium),
                color = Theme.textSecondary,
                modifier = Modifier.padding(horizontal = 20.dp, vertical = 14.dp),
            )
            for (p in SnoozePreset.entries) {
                val at = p.resolve()
                Row(
                    Modifier
                        .fillMaxWidth()
                        .height(46.dp)
                        .clickable {
                            onPick(at, if (p == SnoozePreset.SOMEDAY) "someday" else formatWake(at))
                            onDismiss()
                        }
                        .padding(horizontal = 20.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(Modifier.width(26.dp)) { MIcon(p.icon, size = 20) }
                    Spacer(Modifier.width(16.dp))
                    Text(p.label, style = robotoStyle(15), color = Theme.textPrimary)
                    Spacer(Modifier.weight(1f))
                    if (p != SnoozePreset.SOMEDAY) {
                        Text(formatWake(at), style = robotoStyle(13), color = Theme.textSecondary)
                    }
                }
            }
            Row(
                Modifier
                    .fillMaxWidth()
                    .height(46.dp)
                    .clickable {
                        val now = Calendar.getInstance()
                        DatePickerDialog(
                            context,
                            { _, year, month, day ->
                                TimePickerDialog(
                                    context,
                                    { _, hour, minute ->
                                        val c = Calendar.getInstance().apply {
                                            set(year, month, day, hour, minute, 0)
                                            set(Calendar.MILLISECOND, 0)
                                        }
                                        onPick(c.timeInMillis, formatWake(c.timeInMillis))
                                        onDismiss()
                                    },
                                    now.get(Calendar.HOUR_OF_DAY), 0, false,
                                ).show()
                            },
                            now.get(Calendar.YEAR), now.get(Calendar.MONTH), now.get(Calendar.DAY_OF_MONTH),
                        ).apply { datePicker.minDate = System.currentTimeMillis() - 1000 }.show()
                    }
                    .padding(horizontal = 20.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(Modifier.width(26.dp)) { MIcon("edit_calendar", size = 20) }
                Spacer(Modifier.width(16.dp))
                Text("Pick date & time", style = robotoStyle(15), color = Theme.textPrimary)
            }
        }
    }
}
