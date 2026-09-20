package com.baestheorem.inbox

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.baestheorem.inbox.auth.AuthStore
import com.baestheorem.inbox.data.MailStore
import com.baestheorem.inbox.data.Prefs
import com.baestheorem.inbox.data.SnoozeStore
import com.baestheorem.inbox.ui.RootScreen
import com.baestheorem.inbox.ui.SetupWizard
import com.baestheorem.inbox.ui.Theme
import com.baestheorem.inbox.work.NewMailWorker
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private var pendingThreadId: String? = null
    private var store: MailStore? = null

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        pendingThreadId = threadIdFrom(intent)
        setContent {
            MaterialTheme {
                Box(Modifier.fillMaxSize().background(Theme.pageBg)) {
                    App(
                        onStoreReady = { s ->
                            store = s
                            pendingThreadId?.let {
                                s.deepLinkThreadId.value = it
                                pendingThreadId = null
                            }
                        },
                        onSignedIn = { askForNotifications() },
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val tid = threadIdFrom(intent) ?: return
        val s = store
        if (s != null) s.deepLinkThreadId.value = tid else pendingThreadId = tid
    }

    override fun onResume() {
        super.onResume()
        if (!AuthStore.isSignedIn) return
        lifecycleScope.launch {
            val woken = SnoozeStore.processDue()
            val s = store ?: return@launch
            if (woken > 0) s.refresh()
        }
    }

    /** inboxclone://thread/<id> reopens the exact thread; a bare link just raises the app. */
    private fun threadIdFrom(intent: Intent?): String? {
        val data = intent?.data ?: return null
        if (data.scheme != "inboxclone" || data.host != "thread") return null
        return data.pathSegments.firstOrNull()?.takeIf { it.isNotEmpty() }
    }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}

@Composable
private fun App(onStoreReady: (MailStore) -> Unit, onSignedIn: () -> Unit) {
    var signedIn by remember { mutableStateOf(AuthStore.isSignedIn) }
    if (!signedIn) {
        SetupWizard(onDone = { signedIn = true })
        return
    }
    val store: MailStore = viewModel()
    val expired by store.authExpired.collectAsState()
    val context = androidx.compose.ui.platform.LocalContext.current

    LaunchedEffect(Unit) {
        onStoreReady(store)
        onSignedIn()
        if (Prefs.notifyNewMail) NewMailWorker.schedule(context)
        store.bootstrap()
    }

    if (expired) {
        // The refresh token died (revoked, or the 7-day Testing expiry); the
        // wizard puts it back without touching the client credentials.
        SetupWizard(onDone = { })
        return
    }

    RootScreen(store = store, onSignedOut = { signedIn = AuthStore.isSignedIn })
}
