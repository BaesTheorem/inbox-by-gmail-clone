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

WebKit needs an AppKit main loop to service its main queue. Inside the desktop shell
(desktop.py -> pywebview) that loop is already running and the page is driven
in-process. Anywhere else (the launchd server, bare `python app.py`) drive() runs this
file as a helper process, `python unsub_webdriver.py --drive URL EMAIL`, which starts
its own loop with no Dock icon and prints the result as one JSON line. available() is
False only when no interpreter on hand can import WebKit.
"""
import ipaddress
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
from urllib.parse import urlparse

log = logging.getLogger("inbox")

# The driver script, kept byte-identical to UnsubWebDriver.swift and UnsubWebDriver.kt.
# Two placeholders are substituted per round: __SUBMITTED__ and __EMAIL__.
DRIVER_JS = r"""
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

_LOAD_SETTLE = 1.2      # seconds after didFinish before the scripts have drawn the page
_AFTER_CLICK = 2.5      # seconds after a click, for the XHR or the navigation it kicked off
_MAX_ROUNDS = 6        # opt out, save, "are you sure?", then read the result
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


def _loop_running():
    """pyobjc present here and an AppKit main loop running to service the main queue."""
    try:
        import WebKit  # noqa: F401
        from AppKit import NSApplication
    except Exception:
        return False
    try:
        return bool(NSApplication.sharedApplication().isRunning())
    except Exception:
        return False


_UNSET = object()
_helper_cmd = _UNSET


def _helper():
    """Command that runs this file as a helper process with its own run loop, or None.
    Prefers an interpreter that already imports WebKit (this one, then the repo's
    .venv, which the desktop shell uses); falls back to `uv run --script`, which
    installs the pyobjc frameworks from the header on first use. Memoized."""
    global _helper_cmd
    if _helper_cmd is not _UNSET:
        return _helper_cmd
    here = os.path.dirname(os.path.abspath(__file__))
    me = os.path.abspath(__file__)
    _helper_cmd = None
    for py in (sys.executable, os.path.join(here, ".venv", "bin", "python")):
        if not (py and os.path.exists(py)):
            continue
        try:
            r = subprocess.run([py, "-c", "import WebKit, AppKit"],
                               capture_output=True, timeout=30)
        except Exception:
            continue
        if r.returncode == 0:
            _helper_cmd = [py, me]
            return _helper_cmd
    uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    if os.path.exists(uv):
        _helper_cmd = [uv, "run", "--script", me]
    return _helper_cmd


def available():
    """True when a WebKit engine can be driven from here, in-process or in a helper."""
    return _loop_running() or _helper() is not None


def drive(url, email=None):
    """Load url in an offscreen WKWebView and press through the opt-out.

    Returns {ok, confirmed, steps, error}. Never raises: every failure degrades to
    ok=False so the caller can move to the next rung."""
    if _loop_running():
        return _drive_here(url, email)
    return _drive_in_helper(url, email)


def _drive_in_helper(url, email):
    out = {"ok": False, "confirmed": False, "steps": [], "error": None}
    cmd = _helper()
    if not cmd:
        out["error"] = "no interpreter with WebKit"
        return out
    try:
        r = subprocess.run(cmd + ["--drive", url, email or ""], capture_output=True,
                           text=True, timeout=_TOTAL_BUDGET + 30)
    except subprocess.TimeoutExpired:
        out["error"] = "helper timed out"
        return out
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        res = json.loads(lines[-1])
        assert isinstance(res, dict) and "steps" in res
        return res
    except Exception:
        out["error"] = f"helper failed: {(r.stderr or '').strip()[-300:] or 'no output'}"
        return out


def _helper_main(url, email, html=None):
    """Entry point of the helper process: bring up an AppKit loop that shows nothing,
    drive the page on a worker thread, print the result, exit."""
    from AppKit import NSApplication, NSApplicationActivationPolicyProhibited
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)  # no Dock icon, no focus

    def work():
        for _ in range(200):
            if app.isRunning():
                break
            _sleep(0.05)
        try:
            res = _drive_here(url, email, html=html)
        except Exception as e:
            res = {"ok": False, "confirmed": False, "steps": [], "error": str(e)}
        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()
        os._exit(0)

    threading.Thread(target=work, daemon=True).start()
    app.run()


def _drive_here(url, email=None, html=None):
    """html, when given, is loaded in place of url with no network: the tests drive
    the script against fixture pages this way."""
    out = {"ok": False, "confirmed": False, "steps": [], "error": None}
    if not _loop_running():
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
            # HTTP resolver does: http(s) only, resolving to public addresses only.
            try:
                u = str(action.request().URL().absoluteString() or "")
                p = urlparse(u)
                # about: covers the blank document a fresh web view starts on.
                ok = (p.scheme == "about"
                      or (p.scheme in ("https", "http") and bool(p.hostname)
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
        if html is not None:
            wv.loadHTMLString_baseURL_(html, None)
        else:
            wv.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    nav = _Nav.alloc().init()
    deadline = threading.Event()
    timer = threading.Timer(_TOTAL_BUDGET, deadline.set)
    timer.daemon = True
    timer.start()
    try:
        on_main(build)
        # didFinish waits for every subresource, and one stalled tracker holds it
        # back long after the page itself drew. So a slow finish is not a failure:
        # go on and let the script judge what is in the document.
        state["loaded"].wait(timeout=12)
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
                if not submitted:
                    # Static wording with the opt-out in a script that fired on load:
                    # give its request time to land before the view is torn down.
                    _sleep(_AFTER_CLICK)
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


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Drive an unsubscribe page in a WebKit engine.")
    ap.add_argument("--drive", nargs=2, metavar=("URL", "EMAIL"),
                    help="load URL, press through the opt-out, print one JSON line")
    ap.add_argument("--drive-html", metavar="FILE",
                    help="the same walk over a local HTML file, for the tests")
    args = ap.parse_args()
    if args.drive_html:
        with open(args.drive_html, encoding="utf-8") as fh:
            _helper_main("about:blank", None, html=fh.read())
    elif args.drive:
        _helper_main(args.drive[0], args.drive[1] or None)
    else:
        ap.print_help()
