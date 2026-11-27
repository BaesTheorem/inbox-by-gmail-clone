package com.baestheorem.inbox.data

import android.content.Context
import android.util.Log
import com.baestheorem.inbox.gmail.GmailClient
import com.baestheorem.inbox.gmail.GmailException
import com.baestheorem.inbox.gmail.Recipient
import com.baestheorem.inbox.gmail.headerValue
import com.baestheorem.inbox.gmail.isEmailAddress
import com.baestheorem.inbox.gmail.parseAddrList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException

/** One address this account has sent mail to. `name` is "" when no message carried one. */
@Serializable
data class Contact(val email: String, val name: String = "", val lastSent: Long = 0) {
    val id: String get() = email.lowercase()
    val label: String get() = name.ifEmpty { email }
}

/**
 * Contact autocomplete, mined from the To and Cc of sent mail. Port of
 * _refresh_contacts in the Mac app's app.py: one row per address this account has
 * sent mail to, newest correspondent first. The list lives in a JSON file and in
 * memory, so a keystroke only reads it. A stale list refreshes in a background
 * coroutine that reads only the sent messages no earlier refresh read, one paced
 * fetch at a time, and stops at the first refusal. A send made on this phone goes
 * into the list at once (noteSent), so the person is suggested before the next scan.
 */
object ContactStore {
    private const val TAG = "ContactStore"
    const val TTL_MS = 900_000L      // a refresh stays fresh this long
    const val SCAN = 200             // newest sent messages each refresh looks at
    // Between fetches: a get costs ~50 of Gmail's 6,000 units/min per user, shared with the other apps.
    // At 200 ms a first scan of 200 messages asked for ~15,000 units a minute.
    const val PACE_MS = 1500L
    const val MIN_QUERY = 2          // characters before the list shows

    private val json = Json { ignoreUnknownKeys = true }
    private lateinit var file: File
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Serializable
    private data class Store(
        val contacts: MutableList<Contact> = mutableListOf(),
        val sources: MutableSet<String> = mutableSetOf(),   // sent message ids already read
    )

    private var store = Store()
    private var refreshedAt = 0L
    private var refreshing = false

    private val _contacts = MutableStateFlow<List<Contact>>(emptyList())
    val contacts: StateFlow<List<Contact>> = _contacts

    fun init(context: Context) {
        if (::file.isInitialized) return
        file = File(context.applicationContext.filesDir, "contacts.json")
        store = try {
            if (file.exists()) json.decodeFromString(Store.serializer(), file.readText()) else Store()
        } catch (e: Exception) {
            Store()
        }
        _contacts.value = store.contacts.toList()
    }

    @Synchronized
    private fun save() {
        store.contacts.sortByDescending { it.lastSent }
        _contacts.value = store.contacts.toList()
        try {
            file.writeText(json.encodeToString(Store.serializer(), store))
        } catch (e: IOException) {
            Log.w(TAG, "could not persist contacts", e)
        }
    }

    /**
     * The contacts whose name or address contains the query, newest first. `excluding`
     * holds lowercased addresses already chosen (and the account's own), which stay out.
     */
    fun suggestions(query: String, excluding: Set<String> = emptySet(), limit: Int = 8): List<Contact> {
        val q = query.trim().lowercase()
        if (q.length < MIN_QUERY) return emptyList()
        return _contacts.value.asSequence()
            .filter { it.id !in excluding }
            .filter { q in it.id || q in it.name.lowercase() }
            .take(limit)
            .toList()
    }

    // The newest message sets the spelling and the name (an empty name never replaces a
    // known one); lastSent keeps the maximum.
    @Synchronized
    private fun upsert(name: String, email: String, sent: Long) {
        val key = email.lowercase()
        val clean = if (name.lowercase() == key) "" else name
        val i = store.contacts.indexOfFirst { it.id == key }
        if (i < 0) {
            store.contacts.add(Contact(email, clean, sent))
            return
        }
        val c = store.contacts[i]
        store.contacts[i] = c.copy(
            email = if (sent >= c.lastSent) email else c.email,
            name = if (clean.isNotEmpty() && (sent >= c.lastSent || c.name.isEmpty())) clean else c.name,
            lastSent = maxOf(c.lastSent, sent),
        )
    }

    fun noteSent(recipients: List<Recipient>) {
        val now = System.currentTimeMillis() / 1000
        for (r in recipients) if (isEmailAddress(r.email)) upsert(r.name, r.email, now)
        save()
    }

    /** Start a background refresh when the list is stale and no refresh runs. */
    @Synchronized
    fun refreshIfStale(force: Boolean = false) {
        if (refreshing) return
        if (!force && System.currentTimeMillis() - refreshedAt < TTL_MS) return
        refreshing = true
        scope.launch { refresh() }
    }

    private suspend fun refresh() {
        val t0 = System.currentTimeMillis()
        var ok = false
        try {
            val ids = GmailClient.listMessages(listOf("SENT"), maxResults = SCAN).messages.map { it.id }
            val todo = synchronized(this) { ids.filter { it !in store.sources } }
            var read = 0
            var stopped: String? = null
            for ((n, mid) in todo.withIndex()) {
                if (n > 0) delay(PACE_MS)
                val msg = try {
                    GmailClient.recipientHeaders(mid)
                } catch (e: GmailException) {
                    if (e.code == 404) continue  // deleted since the list call
                    stopped = "HTTP ${e.code}"   // rate limit or Gmail trouble: yield to the app
                    break
                } catch (e: IOException) {
                    stopped = e.javaClass.simpleName
                    break
                }
                val sent = (msg.internalDate?.toLongOrNull() ?: 0L) / 1000
                val headers = msg.payload?.headers ?: emptyList()
                for (r in parseAddrList(headerValue(headers, "To"), headerValue(headers, "Cc"))) {
                    if (isEmailAddress(r.email)) upsert(r.name, r.email, sent)
                }
                synchronized(this) { store.sources.add(mid) }
                read++
            }
            // Only the newest SCAN ids are ever checked again, so older source rows are
            // dead weight.
            synchronized(this) { store.sources.retainAll(ids.toSet()) }
            save()
            ok = stopped == null
            val note = if (stopped != null) " (stopped early on $stopped)" else ""
            Log.i(TAG, "contacts refreshed: read $read of ${todo.size} new sent messages in " +
                "${System.currentTimeMillis() - t0}ms$note, ${_contacts.value.size} contacts")
        } catch (e: Exception) {
            Log.w(TAG, "contact refresh failed", e)
        } finally {
            synchronized(this) {
                refreshing = false
                // After a failure, try again in a minute instead of after the full TTL.
                refreshedAt = if (ok) System.currentTimeMillis() else System.currentTimeMillis() - (TTL_MS - 60_000L)
            }
        }
    }
}
