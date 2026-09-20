package com.baestheorem.inbox.ui

import com.baestheorem.inbox.gmail.ATTACH_HOST
import com.baestheorem.inbox.gmail.ThreadDetail
import com.baestheorem.inbox.gmail.fmtBytes
import com.baestheorem.inbox.gmail.htmlEscape
import com.baestheorem.inbox.gmail.parseAddr
import com.baestheorem.inbox.gmail.pct

// One WebView renders the whole thread as Inbox-styled message cards. Mirrors
// static/style.css: .msg cards, the quote-toggle pill, .attach-chip.

private const val PAPERCLIP = """<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#3367D6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>"""

private const val CSS = """
body{margin:0;padding:12px 12px 16px;background:#F2F2F2;color:#212121;
  font-family:Roboto,sans-serif;font-size:14px;line-height:1.5;
  -webkit-text-size-adjust:100%;word-break:break-word}
.msg{background:#fff;border:1px solid #DDDDDD;border-radius:8px;padding:16px;margin-bottom:12px;
  box-shadow:0 1px 2px rgba(0,0,0,.10),0 1px 3px rgba(0,0,0,.08);overflow-x:auto}
.hdr{display:flex;justify-content:space-between;align-items:baseline;gap:8px;
  color:#5F6368;font-size:13px;margin-bottom:8px}
.sender{font-weight:500;font-size:13px;color:#212121}
.date{white-space:nowrap}
.to{color:#5F6368;font-size:12px;margin:-4px 0 10px}
.body img{max-width:100%;height:auto}
.body table{max-width:100%}
.body a{color:#3367D6}
.body blockquote{border-left:2px solid #DDD;margin:8px 0;padding-left:10px;color:#5F6368}
.qtoggle{border:0;background:#DDDDDD;border-radius:12px;padding:1px 10px;
  color:#5F6368;font-size:13px;line-height:1.7;margin-top:6px;
  font-weight:700;letter-spacing:1.5px}
.quoted{margin-top:8px;padding-top:8px;border-top:1px solid #EEE}
.atts{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid #DDDDDD}
.att{display:inline-flex;align-items:center;gap:6px;background:#F1F3F4;border:1px solid #E0E0E0;
  border-radius:14px;padding:4px 10px;color:#3c4043;text-decoration:none;font-size:12px}
.att small{color:#80868b}
"""

// Split on commas outside quotes so '"Doe, Jane" <j@x>' stays one recipient.
private val rcptRx = Regex("""(?:"[^"]*"|[^,])+""")

private fun shortRecipients(to: String): String =
    rcptRx.findAll(to)
        .map { parseAddr(it.value).name }
        .filter { it.isNotEmpty() }
        .joinToString(", ")
        .take(80)

fun buildThreadHtml(d: ThreadDetail): String {
    val cards = StringBuilder()
    d.messages.forEachIndexed { i, m ->
        val bodyHtml = if (m.html.isEmpty()) {
            "<div style=\"white-space:pre-wrap\">${htmlEscape(m.text)}</div>"
        } else {
            m.html
        }
        val quotedBlock = m.quotedHtml?.let { q ->
            """<button class="qtoggle" onclick="tg('q$i')">&#8226;&#8226;&#8226;</button>""" +
                """<div class="quoted" id="q$i" style="display:none">$q</div>"""
        } ?: ""
        val atts = if (m.attachments.isEmpty()) "" else {
            val links = m.attachments.joinToString("") { a ->
                "<a class=\"att\" href=\"https://$ATTACH_HOST/${a.messageId}/${a.attachmentId}" +
                    "?name=${pct(a.filename)}&mime=${pct(a.mimeType)}&size=${a.size}\">" +
                    "$PAPERCLIP ${htmlEscape(a.filename)} <small>${fmtBytes(a.size)}</small></a>"
            }
            "<div class=\"atts\">$links</div>"
        }
        val rcpt = listOf("to" to m.to, "cc" to m.cc, "bcc" to m.bcc)
            .map { it.first to shortRecipients(it.second) }
            .filter { it.second.isNotEmpty() }
            .joinToString(", ") { "${it.first} ${it.second}" }
        val toLine = if (rcpt.isEmpty()) "" else "<div class=\"to\">${htmlEscape(rcpt)}</div>"
        cards.append(
            """
            <div class="msg">
              <div class="hdr"><span class="sender">${htmlEscape(m.sender)}</span><span class="date">${htmlEscape(m.date)}</span></div>
              $toLine
              <div class="body">$bodyHtml</div>
              $quotedBlock
              $atts
            </div>
            """.trimIndent()
        )
    }
    return """
        <!doctype html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>$CSS</style></head><body>$cards
        <script>function tg(id){var e=document.getElementById(id);
        e.style.display=e.style.display==='none'?'block':'none';}</script>
        </body></html>
    """.trimIndent()
}
