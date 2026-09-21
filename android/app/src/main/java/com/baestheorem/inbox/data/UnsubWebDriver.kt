package com.baestheorem.inbox.data

import android.annotation.SuppressLint
import android.content.Context
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import com.baestheorem.inbox.gmail.hostResolvesPublicOnly
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.ByteArrayInputStream

// Last automatic rung of the unsubscribe ladder: pages that only exist in JavaScript.
//
// UnsubResolver reads raw HTML, so a single-page opt-out (a React shell that fetches a
// token and then renders a confirm button) looks empty to it. This drives a real browser
// engine at the page instead: load it, let the scripts run, tick the opt-out option, type
// the address into the "which address?" box, press the confirm control, and read the
// result. Same driver script as the Mac and iOS apps, byte for byte, so all three behave
// the same way on the same page.

data class UnsubWebOutcome(
    val ok: Boolean = false,
    val confirmed: Boolean = false,
    val steps: List<String> = emptyList(),
    val error: String? = null,
)

object UnsubWebDriver {

    private const val MAX_ROUNDS = 4
    private const val LOAD_SETTLE_MS = 1_200L    // scripts finish drawing
    private const val AFTER_CLICK_MS = 2_500L    // the XHR or nav a click starts

    private val hostOk = mutableMapOf<String, Boolean>()
    private val blocked
        get() = WebResourceResponse("text/plain", "utf-8", ByteArrayInputStream(ByteArray(0)))

    @SuppressLint("SetJavaScriptEnabled")
    suspend fun drive(ctx: Context, url: String, email: String?): UnsubWebOutcome =
        withContext(Dispatchers.Main) {
            val steps = mutableListOf<String>()
            var error: String? = null
            var submitted = false
            var confirmed = false
            val finished = CompletableDeferred<Unit>()
            var web: WebView? = null
            try {
                val wv = WebView(ctx)
                web = wv
                wv.settings.javaScriptEnabled = true
                wv.settings.domStorageEnabled = true
                wv.settings.userAgentString = com.baestheorem.inbox.gmail.UnsubResolver.BROWSER_UA
                // Detached from any window, so nothing lays out unless we say how big it is;
                // the driver script decides what is clickable from layout.
                wv.layout(0, 0, 1080, 1920)
                wv.webViewClient = object : WebViewClient() {
                    override fun onPageFinished(view: WebView, u: String) {
                        if (!finished.isCompleted) finished.complete(Unit)
                    }

                    // Runs off the UI thread, which is where the public-only check belongs:
                    // it resolves DNS. Every hop and every subresource is the sender's to
                    // choose, so all of them go through it.
                    override fun shouldInterceptRequest(
                        view: WebView,
                        request: WebResourceRequest,
                    ): WebResourceResponse? {
                        val u = request.url
                        val scheme = u.scheme?.lowercase()
                        if (scheme == "about" || scheme == "data") return null
                        if (scheme != "https") return blocked
                        val host = u.host ?: return blocked
                        val ok = synchronized(hostOk) {
                            hostOk.getOrPut(host) { hostResolvesPublicOnly(host) }
                        }
                        return if (ok) null else blocked
                    }
                }
                wv.loadUrl(url)
                withTimeoutOrNull(12_000L) { finished.await() }

                for (round in 0 until MAX_ROUNDS) {
                    delay(LOAD_SETTLE_MS)
                    val verdict = evaluate(wv, script(submitted, email))
                    if (verdict == null) {
                        error = error ?: "script failed"
                        break
                    }
                    if (verdict == "done") {
                        steps += "js:confirmed"
                        confirmed = true
                        break
                    }
                    if (verdict.startsWith("clicked:")) {
                        steps += "js:" + verdict
                        submitted = true
                        delay(AFTER_CLICK_MS)
                        continue
                    }
                    steps += "js:nothing"
                    break
                }
            } catch (e: Exception) {
                error = e.message ?: e.toString()
            } finally {
                web?.stopLoading()
                web?.destroy()
            }
            UnsubWebOutcome(
                ok = confirmed || (submitted && error == null),
                confirmed = confirmed,
                steps = steps,
                error = error,
            )
        }

    /** evaluateJavascript hands back a JSON literal, so "done" arrives with its quotes. */
    private suspend fun evaluate(wv: WebView, js: String): String? {
        val result = CompletableDeferred<String?>()
        wv.evaluateJavascript(js) { raw -> result.complete(raw) }
        val raw = withTimeoutOrNull(10_000L) { result.await() } ?: return null
        if (raw == "null" || raw.isEmpty()) return null
        if (raw.length >= 2 && raw.startsWith("\"") && raw.endsWith("\"")) {
            return raw.substring(1, raw.length - 1)
                .replace("\\\"", "\"").replace("\\n", "\n")
                .replace("\\r", "\r").replace("\\\\", "\\")
        }
        return raw
    }

    private fun script(submitted: Boolean, email: String?): String =
        DRIVER_JS
            .replace("__SUBMITTED__", if (submitted) "true" else "false")
            .replace("__EMAIL__", jsString(email ?: ""))

    private fun jsString(s: String): String {
        val sb = StringBuilder("\"")
        for (c in s) {
            when {
                c == '"' -> sb.append("\\\"")
                c == '\\' -> sb.append("\\\\")
                c == '\n' -> sb.append("\\n")
                c == '\r' -> sb.append("\\r")
                c.code < 0x20 -> sb.append(String.format("\\u%04x", c.code))
                else -> sb.append(c)
            }
        }
        return sb.append('"').toString()
    }

    // Kept byte-identical to unsub_webdriver.py's DRIVER_JS and UnsubWebDriver.swift's.
    // Two placeholders are substituted per round: __SUBMITTED__ and __EMAIL__.
    const val DRIVER_JS = """
(function () {
  var SUBMITTED = __SUBMITTED__;
  var EMAIL = __EMAIL__;
  var DONE = /you(?:'ve| have) been (?:successfully )?(?:unsubscribed|removed|opted[\s\-]?out)|(?:has|have) been (?:successfully )?(?:unsubscribed|removed from)|(?:successfully|now) (?:unsubscribed|opted[\s\-]?out)|unsubscri(?:be|ption)[^.]{0,20}(?:was )?(?:success|complete|confirmed)|you(?:'re| are) (?:now )?(?:unsubscribed|opted[\s\-]?out)|you will no longer receive|no longer (?:be )?subscribed|removed from (?:our|the|this|that) (?:mailing |e-?mail |distribution )?list|opt[\s\-]?out (?:is )?(?:complete|successful|confirmed)/i;
  var CONFIRM = /unsubscrib|opt[\s\-]?out|remove\s+(?:me|my|this)|yes[,!\s]|confirm|stop\s+(?:receiving|all|these)|no\s+longer\s+(?:wish|want)|(?:update|save)\s+(?:my\s+)?(?:e-?mail\s+)?preferences/i;
  var AVOID = /keep me|stay subscribed|remain subscribed|resubscribe|go back|cancel|no,? (?:thanks|keep)|sign in|log in|create account|privacy|terms|contact us/i;
  var OPTOUT = /unsub|opt[\s\-]?out|remove|stop|\bnone\b|no[\s_\-]?e-?mail/i;

  // Layout-based, so it still works in an offscreen web view that never paints.
  function shown(el) {
    if (!el) return false;
    if (el.offsetParent !== null) return true;
    var s = window.getComputedStyle(el);
    return s && s.position === 'fixed' && s.display !== 'none' && s.visibility !== 'hidden';
  }
  function label(el) {
    return ((el.innerText || el.textContent || '') + ' ' + (el.value || '') + ' ' +
            (el.getAttribute('aria-label') || '') + ' ' +
            (el.getAttribute('title') || '')).replace(/\s+/g, ' ').trim();
  }

  var text = (document.body && document.body.innerText) || '';
  var controls = [].slice.call(document.querySelectorAll(
      'button, input[type=submit], input[type=button], a[href], [role=button]'))
    .filter(function (e) {
      var t = label(e);
      return t && shown(e) && CONFIRM.test(t) && !AVOID.test(t);
    });
  // A real button beats a footer link that happens to say "unsubscribe".
  controls.sort(function (a, b) {
    var rank = function (e) { return e.tagName === 'A' ? 1 : 0; };
    return rank(a) - rank(b);
  });

  // Same rule as the HTML resolver: the done wording only counts once something has
  // been pressed, or when there is nothing left to press.
  if (DONE.test(text) && (SUBMITTED || controls.length === 0)) return 'done';

  [].slice.call(document.querySelectorAll('input[type=email], input[type=text]'))
    .forEach(function (i) {
      if (i.value || !EMAIL) return;
      if (!/e-?mail|addr/i.test(i.name + ' ' + i.id + ' ' + (i.placeholder || ''))) return;
      i.value = EMAIL;
      i.dispatchEvent(new Event('input', { bubbles: true }));
      i.dispatchEvent(new Event('change', { bubbles: true }));
    });
  [].slice.call(document.querySelectorAll('input[type=radio], input[type=checkbox]'))
    .forEach(function (i) {
      if (i.checked || !shown(i)) return;
      var near = i.closest('label') || i.parentElement || i;
      if (OPTOUT.test(i.value + ' ' + i.name + ' ' + i.id + ' ' + label(near))) i.click();
    });

  if (!controls.length) return 'nothing';
  var chosen = label(controls[0]).slice(0, 60);
  controls[0].click();
  return 'clicked:' + chosen;
})()
"""
}
