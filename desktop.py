# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "flask",
#   "google-auth",
#   "google-auth-oauthlib",
#   "requests",
#   "pywebview",
#   "pyobjc-framework-Cocoa",
#   "pyobjc-framework-WebKit",
# ]
# ///
"""
Inbox desktop shell — runs the Flask server in-process and renders it in a native
macOS WKWebView window (via pywebview). No Chromium, far less RAM than a browser.

Deep links (inboxclone://thread/<id>) are delivered by the separate AppleScript
scheme handler, which POSTs /api/open_thread; the SPA picks it up over SSE.
"""
import threading
import time
import urllib.request
import webbrowser

import os
import sys

import webview
import app as inbox

URL = f"http://127.0.0.1:{inbox.PORT}/"
# Tee server logs to a file so we can see what the window requests (the .app
# launches detached, so stdout would otherwise go nowhere).
try:
    _logf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop.log"), "a", buffering=1)
    sys.stdout = sys.stderr = _logf
except Exception:
    pass


_default_browser_bid_cache = []

def _default_browser_bid():
    """Bundle id of the default http handler, so we can open URLs with it AND bring
    it to the foreground via `open -b` (a plain `open <url>` adds a background tab)."""
    if _default_browser_bid_cache:
        return _default_browser_bid_cache[0]
    bid = None
    try:
        import json as _json
        import subprocess
        p = os.path.expanduser("~/Library/Preferences/com.apple.LaunchServices/"
                               "com.apple.launchservices.secure.plist")
        out = subprocess.run(["plutil", "-convert", "json", "-o", "-", p],
                             capture_output=True, text=True).stdout
        for h in _json.loads(out).get("LSHandlers", []):
            if h.get("LSHandlerURLScheme") == "http":
                bid = h.get("LSHandlerRoleAll") or h.get("LSHandlerRoleViewer")
                break
    except Exception:
        bid = None
    _default_browser_bid_cache.append(bid)
    return bid


class Api:
    # Exposed to the page as window.pywebview.api.open_external(url)
    def open_external(self, url):
        # Use LaunchServices (`open`) rather than webbrowser.open: from inside this
        # pywebview app webbrowser falls back to `osascript open location`, which
        # silently fails without an Automation/TCC grant. `open -b <browser>` hands the
        # URL to the default browser AND activates it (plain `open <url>` would land in
        # a background tab the user never sees).
        import subprocess
        bid = _default_browser_bid()
        try:
            args = ["/usr/bin/open"] + (["-b", bid] if bid else []) + [url]
            subprocess.run(args, check=False)
        except Exception:
            try:
                subprocess.run(["/usr/bin/open", url], check=False)
            except Exception:
                pass
        return True

    # Exposed as window.pywebview.api.open_attachment(...). Download the Gmail
    # attachment and open it with its native app (Preview for PDFs/images), which
    # surfaces in the foreground — unlike a local URL buried in a background tab.
    def open_attachment(self, message_id, attachment_id, name="attachment", mime="application/octet-stream"):
        import base64, re, subprocess, tempfile
        try:
            data = inbox.gget(f"/messages/{message_id}/attachments/{attachment_id}")
            content = base64.urlsafe_b64decode(data.get("data", ""))
            safe = re.sub(r"[^\w.\- ]", "_", name).strip() or "attachment"
            path = os.path.join(tempfile.mkdtemp(prefix="inbox-att-"), safe)
            with open(path, "wb") as fh:
                fh.write(content)
            subprocess.run(["/usr/bin/open", path], check=False)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _server_up():
    try:
        urllib.request.urlopen(URL + "api/health", timeout=1)
        return True
    except Exception:
        return False


def main():
    # Singleton: if a server is already serving, don't start a second one.
    if _server_up():
        print("Inbox is already running.")
        return
    inbox.start_background()  # scheduler + auth (opens browser once on first run) + history poller
    threading.Thread(
        target=lambda: inbox.app.run(host="127.0.0.1", port=inbox.PORT,
                                     debug=False, threaded=True, use_reloader=False),
        daemon=True,
    ).start()
    for _ in range(160):  # wait up to ~40s for the server to come up
        if _server_up():
            break
        time.sleep(0.25)
    webview.create_window("Inbox", URL, js_api=Api(),
                          width=1180, height=820, min_size=(820, 600))
    webview.start()  # blocks on the native GUI loop until the window closes


if __name__ == "__main__":
    main()
