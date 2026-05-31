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


class Api:
    # Exposed to the page as window.pywebview.api.open_external(url)
    def open_external(self, url):
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return True


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
