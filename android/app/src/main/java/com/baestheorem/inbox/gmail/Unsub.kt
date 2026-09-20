package com.baestheorem.inbox.gmail

import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.URLDecoder
import java.net.UnknownHostException

// Unsubscribe parsing (RFC 2369 / RFC 8058) plus the body-buried fallback and
// the SSRF guard for sender-supplied one-click URLs. Port of app.py's
// parse_unsubscribe / find_body_unsubscribe / _is_safe_public_url.

private val uriRx = Regex("""<([^>]+)>""")
private val subjectQsRx = Regex("""subject=([^&]+)""")

fun parseUnsubscribe(headers: List<GHeader>): UnsubInfo? {
    val raw = headerValue(headers, "List-Unsubscribe")
    if (raw.isEmpty()) return null
    val oneClick = headerValue(headers, "List-Unsubscribe-Post")
        .lowercase().contains("one-click")
    var httpsUrl: String? = null
    var mailto: String? = null
    var mailtoSubject = "unsubscribe"
    for (m in uriRx.findAll(raw)) {
        val uri = m.groupValues[1].trim()
        val low = uri.lowercase()
        if (low.startsWith("http") && httpsUrl == null) {
            httpsUrl = uri
        } else if (low.startsWith("mailto:") && mailto == null) {
            val body = uri.substring("mailto:".length)
            val q = body.indexOf('?')
            if (q >= 0) {
                mailto = body.substring(0, q)
                val qs = body.substring(q + 1)
                subjectQsRx.find(qs)?.let {
                    mailtoSubject = try {
                        URLDecoder.decode(it.groupValues[1], "UTF-8")
                    } catch (e: IllegalArgumentException) {
                        it.groupValues[1]
                    }
                }
            } else {
                mailto = body
            }
        }
    }
    if (oneClick && httpsUrl != null && httpsUrl.lowercase().startsWith("https")) {
        return UnsubInfo(UnsubMethod.ONE_CLICK, httpsUrl, mailto, mailtoSubject, false)
    }
    if (mailto != null) {
        return UnsubInfo(UnsubMethod.MAILTO, httpsUrl, mailto, mailtoSubject, false)
    }
    if (httpsUrl != null) {
        return UnsubInfo(UnsubMethod.LINK, httpsUrl, null, mailtoSubject, false)
    }
    return null
}

// Phrases that mark an opt-out link, matched against the link text AND the words
// right around it, so a bare "Click here" anchor inside "click here if you do
// not wish to receive..." still gets caught.
private val unsubTextRx = Regex(
    """unsubscrib|opt[\s\-]?out|wish\s+to\s+receive|no\s+longer\s+(?:wish|want)|""" +
        """stop\s+receiving|stop\s+these\s+e-?mails|cancel\s+(?:your\s+)?subscription|""" +
        """manage\s+(?:your\s+)?(?:e-?mail\s+)?(?:preferences|subscriptions?)|""" +
        """e-?mail\s+preferences|manage\s+subscriptions?|remove\s+(?:me|from\s+(?:this\s+)?list)""",
    setOf(RegexOption.IGNORE_CASE),
)

private val anchorRx = Regex(
    """<a\b[^>]*?href=(["'])(.*?)\1[^>]*?>(.*?)</a>""",
    setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
)

// Last-resort opt-out for senders that omit the List-Unsubscribe header but bury
// the link in the body. Never auto-POSTed: body links always come back as LINK.
fun findBodyUnsubscribe(html: String): UnsubInfo? {
    if (html.isEmpty()) return null
    var fallback: UnsubInfo? = null
    for (m in anchorRx.findAll(html)) {
        val href = decodeEntities(m.groupValues[2]).trim()
        if (!href.lowercase().startsWith("http")) continue
        val inner = stripTags(m.groupValues[3])
        val hit = UnsubInfo(UnsubMethod.LINK, href, null, "unsubscribe", fromBody = true)
        if (unsubTextRx.containsMatchIn(inner)) return hit
        if (fallback == null) {
            val before = html.substring(maxOf(0, m.range.first - 200), m.range.first)
            val afterStart = minOf(html.length, m.range.last + 1)
            val after = html.substring(afterStart, minOf(html.length, afterStart + 200))
            val ctx = stripTags(before) + " " + inner + " " + stripTags(after)
            if (unsubTextRx.containsMatchIn(ctx)) fallback = hit
        }
    }
    return fallback
}

// Every resolved address must be public, or the caller falls back to opening
// the page in a browser instead of POSTing to it.
fun hostResolvesPublicOnly(host: String): Boolean {
    if (host.isEmpty()) return false
    val addrs = try {
        InetAddress.getAllByName(host)
    } catch (e: UnknownHostException) {
        return false
    }
    if (addrs.isEmpty()) return false
    for (a in addrs) {
        if (a.isAnyLocalAddress || a.isLoopbackAddress || a.isLinkLocalAddress ||
            a.isSiteLocalAddress || a.isMulticastAddress
        ) return false
        when (a) {
            is Inet4Address -> {
                val b = a.address
                val o1 = b[0].toInt() and 0xFF
                // 0.0.0.0/8, 10/8, 127/8 and 172.16/12 are already covered above
                // for most cases; re-check so a odd resolver answer cannot slip by.
                if (o1 == 0 || o1 == 10 || o1 == 127 || o1 >= 224) return false
            }
            is Inet6Address -> {
                val b = a.address
                // unique local fc00::/7
                if ((b[0].toInt() and 0xFE) == 0xFC) return false
                // v4-mapped ::ffff:a.b.c.d
                val v4mapped = (0..9).all { b[it].toInt() == 0 } &&
                    (b[10].toInt() and 0xFF) == 0xFF && (b[11].toInt() and 0xFF) == 0xFF
                if (v4mapped) {
                    val o1 = b[12].toInt() and 0xFF
                    val o2 = b[13].toInt() and 0xFF
                    if (o1 == 0 || o1 == 10 || o1 == 127 || o1 >= 224) return false
                    if (o1 == 172 && o2 in 16..31) return false
                    if (o1 == 192 && o2 == 168) return false
                    if (o1 == 169 && o2 == 254) return false
                }
            }
        }
    }
    return true
}
