package com.baestheorem.inbox.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.baestheorem.inbox.data.MailStore
import kotlinx.coroutines.launch
import java.io.IOException

@Composable
fun ComposeScreen(store: MailStore, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var to by remember { mutableStateOf("") }
    var subject by remember { mutableStateOf("") }
    var body by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    val canSend = to.contains("@") && !sending

    Column(Modifier.fillMaxSize().background(Theme.cardBg).statusBarsPadding().imePadding()) {
        Row(
            Modifier.fillMaxWidth().height(52.dp).padding(horizontal = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(40.dp).clickable(onClick = onClose), Alignment.Center) {
                MIcon("close", size = 20)
            }
            Text("New message", style = robotoStyle(16), color = Theme.textPrimary)
            Spacer(Modifier.weight(1f))
            Box(
                Modifier.size(40.dp).clickable(enabled = canSend) {
                    sending = true
                    scope.launch {
                        try {
                            store.sendNew(to, subject, body)
                            store.showSnack("Sent")
                            onClose()
                        } catch (e: IOException) {
                            store.showSnack("Send failed")
                            sending = false
                        }
                    }
                },
                Alignment.Center,
            ) {
                if (sending) CircularProgressIndicator(color = Theme.blue, modifier = Modifier.size(20.dp))
                else MIcon("send", size = 20, color = if (canSend) Theme.blue else Theme.textFaint)
            }
        }
        Divider()
        Field("To", to, { to = it }, KeyboardType.Email)
        Divider()
        Field("Subject", subject, { subject = it }, KeyboardType.Text)
        Divider()
        Box(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp)) {
            BasicTextField(
                value = body,
                onValueChange = { body = it },
                textStyle = robotoStyle(15).copy(color = Theme.textPrimary),
                cursorBrush = SolidColor(Theme.blue),
                modifier = Modifier.fillMaxSize(),
            )
            if (body.isEmpty()) {
                Text("Compose email…", style = robotoStyle(15), color = Theme.textFaint)
            }
        }
    }
}

@Composable
private fun Field(label: String, value: String, onValue: (String) -> Unit, type: KeyboardType) {
    Box(Modifier.fillMaxWidth().height(46.dp).padding(horizontal = 16.dp), Alignment.CenterStart) {
        BasicTextField(
            value = value,
            onValueChange = onValue,
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = type),
            textStyle = robotoStyle(15).copy(color = Theme.textPrimary),
            cursorBrush = SolidColor(Theme.blue),
            modifier = Modifier.fillMaxWidth(),
        )
        if (value.isEmpty()) Text(label, style = robotoStyle(15), color = Theme.textFaint)
    }
}

@Composable
fun Divider() {
    Box(Modifier.fillMaxWidth().height(1.dp).background(Theme.divider))
}
