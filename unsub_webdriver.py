# /// script
# requires-python = ">=3.10"
# dependencies = ["pyobjc-framework-Cocoa", "pyobjc-framework-WebKit"]
# ///
"""Last automatic rung of the unsubscribe ladder: pages that only exist in JavaScript.

resolve_unsubscribe_link in app.py reads raw HTML, so a single-page opt-out (a React
shell that fetches a token and renders a confirm button) looks empty to it. This drives
a real WebKit engine at the page instead: load it, let the scripts run, tick the opt-out
option, type the address into the "which address?" box, press the confirm control, and
read the result. Same driver script as the iOS and Android apps, so all three behave the
same on the same page.

Only usable inside the desktop shell, which runs an AppKit main loop (desktop.py ->
pywebview). Bare `python app.py` has no run loop, so available() returns False and the
ladder falls through to the mailto route and then the browser, exactly as before.
"""
import ipaddress
import logging
import socket
import threading
from urllib.parse import urlparse

log = logging.getLogger("inbox")

# The driver script, kept byte-identical to UnsubWebDriver.swift and UnsubWebDriver.kt.
# Two placeholders are substituted per round: __SUBMITTED__ and __EMAIL__.
DRIVER_JS = r"""
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

_LOAD_SETTLE = 1.2      # seconds after didFinish before the scripts have drawn the page
_AFTER_CLICK = 2.5      # seconds after a click, for the XHR or the navigation it kicked off
_MAX_ROUNDS = 4
_TOTAL_BUDGET = 30.0

_host_ok_memo = {}


def _host_ok(host, port):
    """Same public-only rule as app.py's _is_safe_public_url, memoized because the
    navigation policy handler runs on the main thread."""
    key = (host, port)
    if key in _host_ok_memo:
        return _host_ok_memo[key]
    ok = False
    try:
        infos = socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)
        ok = bool(infos) and all(
            not (ip.is_private or ip.is_loopback or ip.is_link_local
                 or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
            for ip in (ipaddress.ip_address(sa[0]) for *_, sa in infos))
    except Exception:
        ok = False
    _host_ok_memo[key] = ok
    return ok


def available():
    """True when a WebKit engine can actually be driven from here: pyobjc present and an
    AppKit main loop running to service the main queue."""
    try:
        import WebKit  # noqa: F401
        from AppKit import NSApplication
    except Exception:
        return False
    try:
        return bool(NSApplication.sharedApplication().isRunning())
    except Exception:
        return False


def drive(url, email=None):
    """Load url in an offscreen WKWebView and press through the opt-out.

    Returns {ok, confirmed, steps, error}. Never raises: every failure degrades to
    ok=False so the caller can move to the next rung."""
    out = {"ok": False, "confirmed": False, "steps": [], "error": None}
    if not available():
        out["error"] = "no AppKit run loop"
        return out
    try:
        from AppKit import NSApplication  # noqa: F401
        from Foundation import (
            NSURL,
            NSMakeRect,
            NSObject,
            NSOperationQueue,
            NSURLRequest,
        )
        from WebKit import (
            WKNavigationActionPolicyAllow,
            WKNavigationActionPolicyCancel,
            WKWebsiteDataStore,
            WKWebView,
            WKWebViewConfiguration,
        )
    except Exception as e:
        out["error"] = f"pyobjc unavailable: {e}"
        return out

    state = {"loaded": threading.Event(), "js": None, "jsdone": threading.Event(),
             "webview": None, "error": None}

    class _Nav(NSObject):
        def webView_didFinishNavigation_(self, webview, nav):
            state["loaded"].set()

        def webView_didFailNavigation_withError_(self, webview, nav, err):
            state["error"] = str(err.localizedDescription())
            state["loaded"].set()

        def webView_didFailProvisionalNavigation_withError_(self, webview, nav, err):
            state["error"] = str(err.localizedDescription())
            state["loaded"].set()

        def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler):
            # Every hop is the sender's to choose, so re-check it the same way the
            # HTTP resolver does: https only, resolving to public addresses only.
            try:
                u = str(action.request().URL().absoluteString() or "")
                p = urlparse(u)
                # about: covers the blank document a fresh web view starts on.
                ok = (p.scheme == "about"
                      or (p.scheme == "https" and bool(p.hostname)
                          and _host_ok(p.hostname, p.port)))
            except Exception:
                ok = False
            handler(WKNavigationActionPolicyAllow if ok else WKNavigationActionPolicyCancel)

    def on_main(fn):
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)

    def build():
        cfg = WKWebViewConfiguration.alloc().init()
        # Non-persistent: the sender's cookies die with this page walk.
        cfg.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
        wv = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 1024, 768), cfg)
        wv.setNavigationDelegate_(nav)
        state["webview"] = wv
        wv.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    nav = _Nav.alloc().init()
    deadline = threading.Event()
    timer = threading.Timer(_TOTAL_BUDGET, deadline.set)
    timer.daemon = True
    timer.start()
    try:
        on_main(build)
        if not state["loaded"].wait(timeout=12):
            out["error"] = "page never loaded"
            return out
        if state["error"]:
            out["error"] = state["error"]
            return out

        submitted = False
        for _round in range(_MAX_ROUNDS):
            if deadline.is_set():
                out["error"] = "timed out"
                break
            _sleep(_LOAD_SETTLE)
            verdict = _eval(on_main, state,
                            _script(submitted, email), deadline)
            if verdict is None:
                out["error"] = out["error"] or "script failed"
                break
            if verdict == "done":
                out["steps"].append("js:confirmed")
                out.update(ok=True, confirmed=True)
                break
            if verdict.startswith("clicked:"):
                out["steps"].append("js:" + verdict)
                submitted = True
                state["loaded"].clear()
                _sleep(_AFTER_CLICK)
                continue
            out["steps"].append("js:nothing")
            break
        out["ok"] = out["confirmed"] or (submitted and not out["error"])
    except Exception as e:
        out["error"] = str(e)
    finally:
        timer.cancel()
        wv = state.get("webview")
        if wv is not None:
            def teardown():
                # pyobjc blocks must return void; a lambda returning the call's value
                # raises on the main thread and takes the app down with it.
                wv.stopLoading()
                wv.setNavigationDelegate_(None)
            on_main(teardown)
    return out


def _script(submitted, email):
    return (DRIVER_JS
            .replace("__SUBMITTED__", "true" if submitted else "false")
            .replace("__EMAIL__", _js_string(email or "")))


def _js_string(s):
    import json
    return json.dumps(s)


def _sleep(seconds):
    threading.Event().wait(seconds)


def _eval(on_main, state, script, deadline):
    """Run script in the page and return its string result, or None."""
    state["js"] = None
    state["jsdone"].clear()

    def go():
        wv = state.get("webview")
        if wv is None:
            state["jsdone"].set()
            return

        def done(result, error):
            state["js"] = None if result is None else str(result)
            state["jsdone"].set()

        wv.evaluateJavaScript_completionHandler_(script, done)

    on_main(go)
    if not state["jsdone"].wait(timeout=10) or deadline.is_set():
        return None
    return state["js"]
