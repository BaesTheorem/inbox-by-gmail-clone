package com.baestheorem.inbox.gmail

// Who a reply goes to. Port of reply_recipients in the Mac app's app.py: the reader
// loads one of two presets (Reply, Reply all), then the user can change every field.

data class Recipient(val name: String, val email: String) {
    val key: String get() = email.lowercase()
}

enum class RcptField(val label: String) { TO("To"), CC("Cc"), BCC("Bcc") }

data class ReplyRecipients(
    val to: List<Recipient> = emptyList(),
    val cc: List<Recipient> = emptyList(),
    val bcc: List<Recipient> = emptyList(),
) {
    val isEmpty: Boolean get() = to.isEmpty() && cc.isEmpty() && bcc.isEmpty()

    fun get(f: RcptField): List<Recipient> = when (f) {
        RcptField.TO -> to
        RcptField.CC -> cc
        RcptField.BCC -> bcc
    }

    /** Same people in the same fields, in any order. */
    fun matches(o: ReplyRecipients): Boolean {
        fun k(l: List<Recipient>) = l.map { it.key }.sorted()
        return k(to) == k(o.to) && k(cc) == k(o.cc) && k(bcc) == k(o.bcc)
    }

    fun remove(r: Recipient): ReplyRecipients = ReplyRecipients(
        to.filter { it.key != r.key }, cc.filter { it.key != r.key }, bcc.filter { it.key != r.key },
    )

    /** An address lives in one field only, so an address added to Cc leaves To. */
    fun add(r: Recipient, f: RcptField): ReplyRecipients {
        val base = remove(r)
        return when (f) {
            RcptField.TO -> base.copy(to = base.to + r)
            RcptField.CC -> base.copy(cc = base.cc + r)
            RcptField.BCC -> base.copy(bcc = base.bcc + r)
        }
    }
}

data class ReplyPresets(
    val reply: ReplyRecipients = ReplyRecipients(),
    val all: ReplyRecipients = ReplyRecipients(),
) {
    /** Reply all is offered only when it reaches a person that Reply does not. */
    val allDiffers: Boolean get() = !all.matches(reply)
}

private val emailRx = Regex("^[^\\s@<>,;\"]+@[^\\s@<>,;\"]+\\.[^\\s@<>,;\"]+$")

fun isEmailAddress(s: String): Boolean = emailRx.matches(s)

/**
 * One key per mailbox. Gmail ignores dots and a +tag in the local part, so
 * a.b+x@gmail.com and ab@gmail.com are the same inbox.
 */
fun mailboxKey(addr: String): String {
    val low = addr.trim().lowercase()
    val at = low.lastIndexOf('@')
    if (at < 0) return low
    var local = low.substring(0, at)
    var domain = low.substring(at + 1)
    if (domain == "gmail.com" || domain == "googlemail.com") {
        local = local.substringBefore('+').replace(".", "")
        domain = "gmail.com"
    }
    return "$local@$domain"
}

/**
 * Header values to recipients, in order, one per address. A comma inside a quoted
 * name ("Doe, Jane" <j@x.com>) or inside <> does not split the list.
 */
fun parseAddrList(vararg values: String): List<Recipient> {
    val out = mutableListOf<Recipient>()
    val seen = mutableSetOf<String>()
    for (value in values) {
        val parts = mutableListOf<String>()
        val cur = StringBuilder()
        var inQuote = false
        var inAngle = false
        for (ch in value) {
            if (ch == '"') inQuote = !inQuote
            else if (ch == '<' && !inQuote) inAngle = true
            else if (ch == '>' && !inQuote) inAngle = false
            if ((ch == ',' || ch == ';') && !inQuote && !inAngle) {
                parts.add(cur.toString()); cur.setLength(0)
            } else {
                cur.append(ch)
            }
        }
        parts.add(cur.toString())
        for (part in parts) {
            val p = part.trim()
            if (p.isEmpty()) continue
            val a = parseAddr(p)
            if (!a.email.contains("@") || !seen.add(a.email.lowercase())) continue
            out.add(Recipient(a.name.ifEmpty { a.email }, a.email))
        }
    }
    return out
}

/**
 * The rules are Gmail's:
 * - Reply goes to Reply-To when the sender set one, otherwise to From.
 * - Reply all adds the other To and Cc addresses as Cc.
 * - A reply to a message I sent goes to the people I sent it to. A reply to its
 *   From would go to me and to no other person.
 * - My own address (and its dotted and +tag forms) is never a recipient.
 */
fun computeReplyPresets(from: String, replyTo: String, to: String, cc: String, me: String): ReplyPresets {
    val mine = mailboxKey(me)
    fun notMe(l: List<Recipient>) = l.filter { mailboxKey(it.email) != mine }
    val sender = parseAddrList(from)
    val toList = notMe(parseAddrList(to))
    val ccList = notMe(parseAddrList(cc))
    if (sender.isNotEmpty() && mailboxKey(sender[0].email) == mine) {
        val primary = toList.ifEmpty { ccList }
        return ReplyPresets(
            ReplyRecipients(to = primary),
            ReplyRecipients(to = primary, cc = if (toList.isEmpty()) emptyList() else ccList),
        )
    }
    val primary = notMe(parseAddrList(replyTo)).ifEmpty { notMe(sender) }
    val taken = primary.map { it.key }.toSet()
    val others = (toList + ccList).filter { it.key !in taken }.distinctBy { it.key }
    if (primary.isEmpty()) {
        return ReplyPresets(ReplyRecipients(to = others.take(1)), ReplyRecipients(to = others))
    }
    return ReplyPresets(ReplyRecipients(to = primary), ReplyRecipients(to = primary, cc = others))
}
