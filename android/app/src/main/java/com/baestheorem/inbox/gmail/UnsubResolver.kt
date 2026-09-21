package com.baestheorem.inbox.gmail

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.FormBody
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException

// Port of app.py's unsubscribe resolver.
//
// Senders that skip RFC 8058 one-click hand you a link that lands on a confirmation
// page, usually after a redirect or two: "click here to confirm", a reason dropdown,
// a box asking which address to remove. This walks that page the way a person would,
// inside the app, so the opt-out finishes without a browser ever opening. The browser
// is the last resort, not the first.

data class UnsubResolution(
    val ok: Boolean = false,
    val confirmed: Boolean = false,   // the sender said in words that the address is off
    val steps: List<String> = emptyList(),
    val finalUrl: String? = null,
    val error: String? = null,
)

object UnsubResolver {

    const val BROWSER_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"

    private val I = setOf(RegexOption.IGNORE_CASE)
    private val IS = setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)

    // Wording that means the opt-out already went through. Deliberately narrow: a
    // confirmation page says "unsubscribe" too, and "you are about to unsubscribe"
    // must not read as done.
    private val doneRx = Regex(
        listOf(
            """you(?:'ve| have) been (?:successfully )?(?:unsubscribed|removed|opted[\s\-]?out)""",
            """(?:has|have) been (?:successfully )?(?:unsubscribed|removed from)""",
            """(?:successfully|now) (?:unsubscribed|opted[\s\-]?out)""",
            """unsubscri(?:be|ption)[^.]{0,20}(?:was )?(?:success|complete|confirmed)""",
            """you(?:'re| are) (?:now )?(?:unsubscribed|opted[\s\-]?out)""",
            """you will no longer receive""",
            """no longer (?:be )?subscribed""",
            """removed from (?:our|the|this|that) (?:mailing |e-?mail |distribution )?list""",
            """(?:subscription|e-?mail preferences|preferences) (?:has|have) been """ +
                """(?:cancell?ed|updated|saved|removed)""",
            """opt[\s\-]?out (?:is )?(?:complete|successful|confirmed)""",
        ).joinToString("|"),
        I,
    )

    // Wording on a button, link, or form that means "this control finishes the opt-out".
    private val confirmRx = Regex(
        listOf(
            """unsubscrib""", """opt[\s\-]?out""", """remove\s+(?:me|my|this)""", """yes[,!\s]""",
            """confirm""", """stop\s+(?:receiving|all|these)""", """no\s+longer\s+(?:wish|want)""",
            """(?:update|save)\s+(?:my\s+)?(?:e-?mail\s+)?preferences""",
        ).joinToString("|"),
        I,
    )

    // Forms never to submit, even on a page that is otherwise an opt-out page.
    private val formSkipRx =
        Regex("""search|log[\s\-]?in|sign[\s\-]?in|signup|register|donate|(?<!un)subscribe""", I)
    private val emailFieldRx = Regex("""e-?mail|addr""", I)

    // Values/ids marking the "take me off everything" option. Deliberately excludes a
    // bare "all": on a preference radio, value="all" is the opposite of an opt-out.
    private val optOutValueRx = Regex("""unsub|opt[\s\-]?out|remove|stop|\bnone\b|no[\s_\-]?e-?mail""", I)

    private val formRx = Regex("""<form\b([^>]*)>(.*?)(?:</form>|$)""", IS)
    private val inputRx = Regex("""<input\b([^>]*)>""", IS)
    private val buttonRx = Regex("""<button\b([^>]*)>(.*?)(?:</button>|$)""", IS)
    private val selectRx = Regex("""<select\b([^>]*)>(.*?)(?:</select>|$)""", IS)
    private val optionRx = Regex("""<option\b([^>]*)>(.*?)(?:</option>|(?=<option)|$)""", IS)
    private val textareaRx = Regex("""<textarea\b([^>]*)>""", IS)
    private val anchorRx = Regex("""<a\b[^>]*?href=(["'])(.*?)\1[^>]*?>(.*?)</a>""", IS)
    private val metaRefreshRx = Regex(
        """<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["'][^"']*?url\s*=\s*([^"'>;]+)""", IS)
    private val jsRedirectRx = Regex(
        """(?:window\.|document\.)?location(?:\.href|\.replace\()?\s*(?:=|\()\s*["']([^"']+)["']""", IS)
    private val scriptRx = Regex("""<(script|style)\b.*?</\1>""", IS)
    private val attrRx = Regex("""([a-zA-Z_:][-\w:.]*)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")

    private val redirectCodes = setOf(301, 302, 303, 307, 308)

    // MARK: Entry points

    /** Drive a sender's unsubscribe page to completion. Blocking; call it off the main thread. */
    fun resolve(startUrl: String, email: String?, maxRounds: Int = 4): UnsubResolution {
        val start = startUrl.toHttpUrlOrNull() ?: return UnsubResolution(error = "bad url")
        val steps = mutableListOf<String>()
        val seen = mutableSetOf<String>()
        val client = client()
        var method = "GET"
        var payload: List<Pair<String, String>>? = null
        var target = start
        var submitted = false
        var error: String? = null
        var finalUrl = startUrl
        try {
            for (round in 0 until maxRounds) {
                if (method == "GET" && target.toString() in seen) break
                seen.add(target.toString())
                val f = safeFetch(client, method, target, payload)
                finalUrl = f.url.toString()
                val text = visibleText(f.page)
                val form = pickUnsubForm(scrapeForms(f.page), text)
                // "You will no longer receive email" is also what a confirmation page
                // promises. Trust the wording only once we have submitted something, or
                // when there is no form left to submit.
                if (doneRx.containsMatchIn(text) && (submitted || form == null)) {
                    steps += "confirmed"
                    return UnsubResolution(true, true, steps, finalUrl, null)
                }
                if (f.status >= 400) {
                    error = "HTTP ${f.status}"
                    break
                }
                val hop = findClientRedirect(f.page, f.url)
                if (hop != null && hop.toString() !in seen) {
                    steps += "redirect"
                    method = "GET"; payload = null; target = hop
                    continue
                }
                if (form != null) {
                    val action = if (form.action.isEmpty()) f.url else (f.url.resolve(form.action) ?: f.url)
                    val p = formPayload(form, email)
                    steps += "form"
                    submitted = true
                    if (form.method == "post") {
                        method = "POST"; payload = p; target = action
                    } else {
                        val b = action.newBuilder()
                        for ((k, v) in p) b.addQueryParameter(k, v)
                        method = "GET"; payload = null; target = b.build()
                    }
                    continue
                }
                val link = findConfirmLink(f.page, f.url)
                if (link != null && link.toString() !in seen) {
                    steps += "confirm-link"
                    submitted = true
                    method = "GET"; payload = null; target = link
                    continue
                }
                break
            }
        } catch (e: IOException) {
            error = e.message ?: "network error"
        }
        return UnsubResolution(submitted && error == null, false, steps, finalUrl, error)
    }

    /**
     * RFC 8058 one-click POST. The body comes back too, so an *unadvertised* attempt can
     * be judged on the sender's own wording instead of on a bare 2xx. Blocking.
     */
    fun oneClickPost(url: String): Triple<Boolean, String, String> {
        val u = url.toHttpUrlOrNull() ?: return Triple(false, "bad url", "")
        if (!isSafePublicUrl(u)) return Triple(false, "unsafe URL", "")
        return try {
            val f = safeFetch(client(), "POST", u, listOf("List-Unsubscribe" to "One-Click"))
            if (f.status in 200..299) Triple(true, "${f.status}", f.page)
            else Triple(false, "HTTP ${f.status}", f.page)
        } catch (e: IOException) {
            Triple(false, e.message ?: "network error", "")
        }
    }

    /** True when the page's own wording says the opt-out went through. */
    fun saysDone(html: String): Boolean = doneRx.containsMatchIn(visibleText(html))

    // MARK: HTTP

    private class Fetched(val page: String, val status: Int, val url: HttpUrl)

    /** Senders set a session cookie on the landing page and check it on the form POST. */
    private class MemoryCookieJar : CookieJar {
        private val store = mutableMapOf<String, MutableList<Cookie>>()
        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            val bucket = store.getOrPut(url.host) { mutableListOf() }
            for (c in cookies) {
                bucket.removeAll { it.name == c.name && it.path == c.path }
                bucket.add(c)
            }
        }
        override fun loadForRequest(url: HttpUrl): List<Cookie> =
            store[url.host]?.filter { it.matches(url) } ?: emptyList()
    }

    private fun client(): OkHttpClient = Net.client.newBuilder()
        .followRedirects(false)
        .followSslRedirects(false)
        .cookieJar(MemoryCookieJar())
        .build()

    /** Like an auto-following client, but SSRF-checks every hop: each Location is the sender's. */
    private fun safeFetch(
        client: OkHttpClient,
        method: String,
        start: HttpUrl,
        payload: List<Pair<String, String>>?,
        maxHops: Int = 8,
    ): Fetched {
        var url = start
        var verb = method
        var body = payload
        for (hop in 0 until maxHops) {
            if (!isSafePublicUrl(url)) throw IOException("unsafe target: ${url.host}")
            val builder = Request.Builder().url(url)
                .header("User-Agent", BROWSER_UA)
                .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                .header("Accept-Language", "en-US,en;q=0.9")
            val req = if (verb == "POST") {
                val fb = FormBody.Builder()
                for ((k, v) in body.orEmpty()) fb.add(k, v)
                builder.post(fb.build()).build()
            } else {
                builder.get().build()
            }
            val resp = client.newCall(req).execute()
            val code = resp.code
            val location = resp.header("Location")
            if (code in redirectCodes && location != null) {
                resp.close()
                url = url.resolve(location) ?: throw IOException("bad redirect: $location")
                if (code == 301 || code == 302 || code == 303) {
                    verb = "GET"; body = null
                }
                continue
            }
            resp.use {
                val ctype = it.header("Content-Type")?.lowercase() ?: "text/html"
                val page = if (ctype.contains("html") || ctype.contains("text/plain")) {
                    it.peekBody(400_000L).string()
                } else {
                    ""
                }
                return Fetched(page, code, url)
            }
        }
        throw IOException("too many redirects")
    }

    private fun isSafePublicUrl(url: HttpUrl): Boolean =
        url.scheme.lowercase() == "https" && hostResolvesPublicOnly(url.host)

    // MARK: Form model

    data class SelectOption(
        val value: String,
        val text: String,
        val explicit: Boolean,
        val selected: Boolean,
    )

    data class FormField(
        val kind: String,                 // input | select | textarea | button
        val type: String = "text",
        val name: String = "",
        val value: String = "",
        val ident: String = "",           // id + class, for spotting the opt-out control
        val label: String = "",           // a <button>'s own text
        val checked: Boolean = false,
        val options: List<SelectOption> = emptyList(),
    )

    data class ScrapedForm(
        val action: String,
        val method: String,
        val ident: String,
        val text: String,
        val fields: List<FormField>,
    )

    // MARK: Scraping

    fun scrapeForms(html: String): List<ScrapedForm> = formRx.findAll(html).map { m ->
        val a = parseAttrs(m.groupValues[1])
        val inner = m.groupValues[2]
        ScrapedForm(
            action = a["action"] ?: "",
            method = (a["method"] ?: "get").trim().lowercase(),
            ident = listOfNotNull(a["id"], a["name"], a["class"]).joinToString(" "),
            text = visibleText(inner),
            fields = scrapeFields(inner),
        )
    }.toList()

    private fun scrapeFields(inner: String): List<FormField> {
        val fields = mutableListOf<FormField>()
        for (m in inputRx.findAll(inner)) {
            val a = parseAttrs(m.groupValues[1])
            fields += FormField(
                kind = "input",
                type = (a["type"] ?: "text").trim().lowercase(),
                name = a["name"] ?: "",
                value = a["value"] ?: "",
                ident = listOfNotNull(a["id"], a["class"]).joinToString(" "),
                checked = a.containsKey("checked"),
            )
        }
        for (m in buttonRx.findAll(inner)) {
            val a = parseAttrs(m.groupValues[1])
            fields += FormField(
                kind = "button",
                type = (a["type"] ?: "submit").trim().lowercase(),
                name = a["name"] ?: "",
                value = a["value"] ?: "",
                label = visibleText(m.groupValues[2]),
            )
        }
        for (m in selectRx.findAll(inner)) {
            val a = parseAttrs(m.groupValues[1])
            val options = optionRx.findAll(m.groupValues[2]).map { om ->
                val oa = parseAttrs(om.groupValues[1])
                SelectOption(
                    value = oa["value"] ?: "",
                    text = visibleText(om.groupValues[2]).trim(),
                    explicit = oa.containsKey("value"),
                    selected = oa.containsKey("selected"),
                )
            }.toList()
            fields += FormField(kind = "select", name = a["name"] ?: "", options = options)
        }
        for (m in textareaRx.findAll(inner)) {
            fields += FormField(kind = "textarea", name = parseAttrs(m.groupValues[1])["name"] ?: "")
        }
        return fields
    }

    private fun parseAttrs(s: String): Map<String, String> {
        val out = LinkedHashMap<String, String>()
        for (m in attrRx.findAll(s)) {
            val key = m.groupValues[1].lowercase()
            if (out.containsKey(key)) continue
            val v = (2..4).firstNotNullOfOrNull { m.groups[it]?.value } ?: ""
            out[key] = decodeEntities(v)
        }
        return out
    }

    // MARK: Picking and filling

    /**
     * The form that finishes the opt-out, or null. Refuses login forms outright and only
     * guesses at an unlabeled form when it is the page's only one and the page itself is
     * clearly an opt-out page.
     */
    fun pickUnsubForm(forms: List<ScrapedForm>, pageText: String): ScrapedForm? {
        val usable = mutableListOf<ScrapedForm>()
        for (form in forms) {
            if (form.fields.any { it.kind == "input" && it.type == "password" }) continue
            val ident = form.action + " " + form.ident
            val blob = (listOf(ident, form.text) + form.fields.map { it.value + " " + it.label })
                .joinToString(" ")
            if (confirmRx.containsMatchIn(blob)) return form
            if (!formSkipRx.containsMatchIn(ident)) usable += form
        }
        if (usable.size == 1 && confirmRx.containsMatchIn(pageText)) return usable[0]
        return null
    }

    /**
     * Fill a confirmation form the way a person would: keep the hidden tokens, tick the
     * opt-out option, answer the "which address?" box, press the confirm button.
     */
    fun formPayload(form: ScrapedForm, email: String?): List<Pair<String, String>> {
        val data = mutableListOf<Pair<String, String>>()
        val submits = mutableListOf<FormField>()
        val radios = LinkedHashMap<String, MutableList<FormField>>()

        for (f in form.fields) {
            when (f.kind) {
                "select" -> {
                    val opt = f.options.firstOrNull { it.selected } ?: run {
                        val opts = f.options.filter { it.value.isNotEmpty() || it.text.isNotEmpty() }
                        opts.firstOrNull { optOutValueRx.containsMatchIn(it.value + " " + it.text) }
                            ?: opts.firstOrNull { it.value.isNotBlank() }
                            ?: opts.firstOrNull()
                    }
                    if (f.name.isNotEmpty() && opt != null) {
                        val v = if (opt.explicit) opt.value
                        else opt.value.ifEmpty { opt.text }
                        data += f.name to v
                    }
                }
                "textarea" -> if (f.name.isNotEmpty()) data += f.name to ""
                "button" -> if (f.type == "submit") submits += f
                else -> {
                    if (f.type == "submit" || f.type == "image") {
                        submits += f
                    } else if (f.type in setOf("button", "reset", "file") || f.name.isEmpty()) {
                        // nothing to send
                    } else if (f.type == "radio") {
                        radios.getOrPut(f.name) { mutableListOf() }.add(f)
                    } else if (f.type == "checkbox") {
                        if (f.checked || optOutValueRx.containsMatchIn("${f.name} ${f.value} ${f.ident}")) {
                            data += f.name to f.value.ifEmpty { "on" }
                        }
                    } else if (f.type == "email" ||
                        (f.type in setOf("text", "hidden") && f.value.isEmpty() &&
                            emailFieldRx.containsMatchIn(f.name + " " + f.ident))
                    ) {
                        data += f.name to f.value.ifEmpty { email ?: "" }
                    } else {
                        data += f.name to f.value
                    }
                }
            }
        }
        for ((name, group) in radios) {
            val pick = group.firstOrNull { it.checked }
                ?: group.firstOrNull { optOutValueRx.containsMatchIn("${it.value} ${it.ident}") }
                ?: group.first()
            data += name to pick.value.ifEmpty { "on" }
        }
        if (submits.isNotEmpty()) {
            val pick = submits.firstOrNull { confirmRx.containsMatchIn("${it.value} ${it.label}") }
                ?: submits[0]
            if (pick.name.isNotEmpty()) data += pick.name to pick.value
        }
        return data
    }

    /** A plain <a> that finishes the job, for pages that use a link instead of a form. */
    fun findConfirmLink(html: String, base: HttpUrl): HttpUrl? {
        for (m in anchorRx.findAll(html)) {
            val text = visibleText(m.groupValues[3]).trim()
            if (text.isEmpty() || !confirmRx.containsMatchIn(text)) continue
            val href = decodeEntities(m.groupValues[2]).trim()
            val u = base.resolve(href) ?: continue
            if (u.scheme.lowercase() == "https") return u
        }
        return null
    }

    /** meta-refresh and window.location hops: the redirects a plain HTTP client misses. */
    fun findClientRedirect(html: String, base: HttpUrl): HttpUrl? {
        val m = metaRefreshRx.find(html) ?: jsRedirectRx.find(html) ?: return null
        val u = base.resolve(decodeEntities(m.groupValues[1].trim())) ?: return null
        return if (u.scheme.lowercase() == "https") u else null
    }

    fun visibleText(html: String): String = stripTags(scriptRx.replace(html, " "))
}
