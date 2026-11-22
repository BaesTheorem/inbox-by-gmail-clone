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

    private const val MAX_ROUNDS = 6
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
                        if (scheme != "https" && scheme != "http") return blocked
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
                        // Static wording with the opt-out in a script that fired on load:
                        // give its request time to land before the view is torn down.
                        if (!submitted) delay(AFTER_CLICK_MS)
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
  var DONE = /you(?:'ve| have) been (?:successfully )?(?:unsubscribed|removed|opted[\s\-]?out)|(?:has|have) been (?:successfully )?(?:unsubscribed|removed from)|(?:successfully|now) (?:unsubscribed|opted[\s\-]?out)|unsubscri(?:be|ption)[^.]{0,20}(?:was )?(?:success|complete|confirmed)|you(?:'re| are) (?:now )?(?:unsubscribed|opted[\s\-]?out)|you will no longer receive|no longer (?:be )?subscribed|removed from (?:our|the|this|that) (?:mailing |e-?mail |distribution )?list|(?:subscription|preferences) (?:has|have) been (?:successfully )?(?:cancell?ed|updated|saved|removed)|opt[\s\-]?out (?:is )?(?:complete|successful|confirmed)/i;
  var CONFIRM = /unsubscrib|opt[\s\-]?out|remove\s+(?:me|my|this)|yes[,!\s]|confirm|stop\s+(?:receiving|all|these)|no\s+longer\s+(?:wish|want)|(?:update|save)\s+(?:my\s+)?(?:e-?mail\s+)?preferences/i;
  var AVOID = /keep me|stay subscribed|remain subscribed|resubscribe|go back|cancel|no,? (?:thanks|keep)|sign in|log in|create account|privacy|terms|contact us/i;
  var OPTOUT = /unsub|opt[\s\-]?out|remove|stop|\bnone\b|no[\s_\-]?e-?mail/i;
  // Wording that names the opt-out itself, as opposed to "save" or "confirm".
  var DIRECT = /unsubscrib|opt[\s\-]?out|remove\s+(?:me|my|this)|stop\s+(?:receiving|all|these)/i;
  var SEMANTIC = 'button, input[type=submit], input[type=button], a[href], [role=button]';

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
  // A greyed-out control: the attribute, the ARIA state, or a "disable(d)" class word.
  function disabled(el) {
    return !!el.disabled || el.getAttribute('aria-disabled') === 'true' ||
           /(?:^|[\s_\-])disabled?(?![a-z])/i.test(String(el.getAttribute('class') || ''));
  }
  function wanted(el) {
    var t = label(el);
    return !!t && shown(el) && !disabled(el) && CONFIRM.test(t) && !AVOID.test(t);
  }

  var text = (document.body && document.body.innerText) || '';
  var controls = [].slice.call(document.querySelectorAll(SEMANTIC)).filter(wanted);

  // Pages built from a component kit often draw a button as a bare div or span with
  // a click handler and nothing semantic on it (Alibaba's preference page does). The
  // pointer cursor is the tell. Short text only, the innermost match only, and never
  // inside a label, where a click would untick the box ticked below.
  var bare = [].slice.call(document.querySelectorAll('body *')).filter(function (e) {
    if ((e.textContent || '').length > 80) return false;
    if (e.closest(SEMANTIC + ', label, select, textarea, script, style')) return false;
    if (e.querySelector(SEMANTIC + ', input, select, textarea')) return false;
    if (!wanted(e) || label(e).length > 40) return false;
    return window.getComputedStyle(e).cursor === 'pointer';
  });
  bare = bare.filter(function (e) {
    return !bare.some(function (o) { return o !== e && e.contains(o); });
  });
  controls = controls.concat(bare);

  // What this walk already pressed on this page, so a page that wants several presses
  // in a row (opt out, then save, then "are you sure?") moves forward, not in a circle.
  var KEY = '__inboxUnsubPressed';
  var pressed = [];
  try { pressed = JSON.parse(window.sessionStorage.getItem(KEY) || '[]'); }
  catch (e) { pressed = window[KEY] || []; }
  function mark(el) { return location.pathname + '|' + label(el).slice(0, 60); }
  function remember(el) {
    pressed.push(mark(el));
    window[KEY] = pressed;
    try { window.sessionStorage.setItem(KEY, JSON.stringify(pressed)); } catch (e) {}
  }

  // A real button beats a bare element, and both beat a footer link that happens to
  // say "unsubscribe". Within a kind: not yet pressed first, then the control that
  // names the opt-out before one that only saves or confirms, then page order.
  function kind(e) {
    if (e.tagName === 'A') return 2;
    return bare.indexOf(e) >= 0 ? 1 : 0;
  }
  var order = controls.map(function (e, i) {
    return { el: e, key: [kind(e) === 2 ? 1 : 0, pressed.indexOf(mark(e)) >= 0 ? 1 : 0,
                          DIRECT.test(label(e)) ? 0 : 1, kind(e), i] };
  });
  order.sort(function (a, b) {
    for (var k = 0; k < a.key.length; k++) {
      if (a.key[k] !== b.key[k]) return a.key[k] - b.key[k];
    }
    return 0;
  });
  controls = order.map(function (o) { return o.el; });

  // A result that shows only for a moment (a toast, a dialog that shuts itself) can
  // come and go between two rounds, so the page is watched from the first press on.
  var SEEN = '__inboxUnsubSeenDone';
  function watch() {
    if (window[SEEN] !== undefined || !window.MutationObserver || !document.body) return;
    window[SEEN] = false;
    var due = null;
    var mo = new MutationObserver(function () {
      if (due) return;
      due = setTimeout(function () {
        due = null;
        if (DONE.test(document.body.innerText || '')) { window[SEEN] = true; mo.disconnect(); }
      }, 100);
    });
    mo.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true });
  }

  // Same rule as the HTML resolver: the done wording only counts once something has
  // been pressed, or when there is nothing left to press.
  if (window[SEEN] === true) return 'done';
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
  remember(controls[0]);
  watch();
  controls[0].click();
  return 'clicked:' + chosen;
})()
"""
}
