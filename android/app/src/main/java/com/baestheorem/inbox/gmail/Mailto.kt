package com.baestheorem.inbox.gmail

import java.net.URLDecoder
import java.util.UUID

/**
 * Compose fields from an RFC 6068 mailto: URL, so Inbox can be the default mail
 * app. Port of app.py parse_mailto: addresses in the path and in to= headers join,
 * header names are case-insensitive, "+" stays literal (spaces arrive as %20), and
 * unknown headers (In-Reply-To and others) are dropped.
 */
data class MailtoDraft(
    val to: String = "",
    val cc: String = "",
    val bcc: String = "",
    val subject: String = "",
    val body: String = "",
    val id: String = UUID.randomUUID().toString(),
)

private fun pctDecode(s: String): String =
    // URLDecoder turns "+" into a space; RFC 6068 keeps it literal
    try { URLDecoder.decode(s.replace("+", "%2B"), "UTF-8") } catch (e: IllegalArgumentException) { s }

private fun joinAddrs(parts: List<String>): String =
    parts.flatMap { it.split(",") }.map { it.trim() }.filter { it.isNotEmpty() }
        .distinct().joinToString(", ")

fun parseMailto(raw: String?): MailtoDraft? {
    val url = raw?.trim() ?: return null
    if (!url.lowercase().startsWith("mailto:")) return null
    val rest = url.substring("mailto:".length)
    val path = rest.substringBefore("?")
    val query = if ('?' in rest) rest.substringAfter("?") else ""
    val addrs = mutableMapOf("to" to mutableListOf<String>(), "cc" to mutableListOf(), "bcc" to mutableListOf())
    var subject = ""
    var body = ""
    if (path.isNotEmpty()) addrs.getValue("to").add(pctDecode(path))
    for (pair in query.split("&")) {
        if (pair.isEmpty()) continue
        val key = pctDecode(pair.substringBefore("=")).lowercase()
        val value = if ('=' in pair) pctDecode(pair.substringAfter("=")) else ""
        when (key) {
            "to", "cc", "bcc" -> if (value.isNotEmpty()) addrs.getValue(key).add(value)
            "subject" -> if (subject.isEmpty()) subject = value
            "body" -> if (body.isEmpty()) body = value
        }
    }
    return MailtoDraft(
        to = joinAddrs(addrs.getValue("to")),
        cc = joinAddrs(addrs.getValue("cc")),
        bcc = joinAddrs(addrs.getValue("bcc")),
        subject = subject,
        body = body,
    )
}

/** Adds the Intent.EXTRA_* fields some apps send beside (or instead of) the URI. */
fun MailtoDraft.withExtras(
    to: Array<String>?, cc: Array<String>?, bcc: Array<String>?,
    subject: String?, text: String?,
): MailtoDraft = copy(
    to = joinAddrs(listOf(this.to) + (to?.toList() ?: emptyList())),
    cc = joinAddrs(listOf(this.cc) + (cc?.toList() ?: emptyList())),
    bcc = joinAddrs(listOf(this.bcc) + (bcc?.toList() ?: emptyList())),
    subject = this.subject.ifEmpty { subject.orEmpty() },
    body = this.body.ifEmpty { text.orEmpty() },
)
