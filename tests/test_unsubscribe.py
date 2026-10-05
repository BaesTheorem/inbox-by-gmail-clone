"""Unsubscribe ladder rules: the SSRF guard's scheme handling, the scripted-page
detector, and the resolver's refusal to believe static wording on a page whose
opt-out runs in a script. Fixtures are synthetic, shaped like the pages that
calibrated them (a click tracker that 302s to a FullRail-style page)."""

import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

SCRIPTED_DONE = """<html><body><p><b>Unsubscribe successful.</b></p>
<p>Your email {emailAddress} has been successfully unsubscribed from this notification.</p>
</body><script>
  var http = new XMLHttpRequest();
  http.open('POST', 'https://api.example.com/api/unsubscribe/task-unsubscribe', true);
  http.setRequestHeader('Content-type', 'application/json');
  http.send(JSON.stringify({customerId: '1', taskId: '2'}));
</script></html>"""

PLAIN_DONE = """<html><body><h1>You have been unsubscribed.</h1>
<script>window.dataLayer = window.dataLayer || []; dataLayer.push({event: 'unsub'});</script>
</body></html>"""


class SafeUrl(unittest.TestCase):
    def test_http_rejected_by_default(self):
        with mock.patch.object(app.socket, "getaddrinfo", return_value=PUBLIC):
            self.assertFalse(app._is_safe_public_url("http://tracker.example.com/ls/click?upn=x"))
            self.assertTrue(app._is_safe_public_url("https://tracker.example.com/ls/click?upn=x"))

    def test_http_admitted_on_request(self):
        with mock.patch.object(app.socket, "getaddrinfo", return_value=PUBLIC):
            self.assertTrue(app._is_safe_public_url("http://tracker.example.com/x", allow_http=True))

    def test_private_host_rejected_even_over_http(self):
        with mock.patch.object(app.socket, "getaddrinfo", return_value=PRIVATE):
            self.assertFalse(app._is_safe_public_url("http://intranet.example.com/x", allow_http=True))
            self.assertFalse(app._is_safe_public_url("https://intranet.example.com/x"))

    def test_other_schemes_rejected(self):
        for u in ("ftp://example.com/x", "file:///etc/hosts", "javascript:alert(1)", "mailto:a@b.c"):
            self.assertFalse(app._is_safe_public_url(u, allow_http=True))


class ScriptedPage(unittest.TestCase):
    def test_xhr_post_detected(self):
        self.assertTrue(app._opt_out_runs_in_script(SCRIPTED_DONE))

    def test_fetch_post_detected(self):
        html = "<script>fetch('/api/unsub', {method: 'POST', body: '{}'})</script>"
        self.assertTrue(app._opt_out_runs_in_script(html))

    def test_analytics_only_ignored(self):
        self.assertFalse(app._opt_out_runs_in_script(PLAIN_DONE))
        self.assertFalse(app._opt_out_runs_in_script("<script>fetch('/pixel.gif')</script>"))
        self.assertFalse(app._opt_out_runs_in_script(""))


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


class Resolver(unittest.TestCase):
    def _resolve(self, html):
        def fake_fetch(session, method, url, data=None, max_hops=8):
            return _Resp(html), "https://admin.example.com/unsubscribe.html?taskId=2"
        with mock.patch.object(app, "_safe_fetch", side_effect=fake_fetch):
            return app.resolve_unsubscribe_link("http://tracker.example.com/ls/click?upn=x",
                                                email="me@example.com")

    def test_plain_done_page_is_confirmed(self):
        res = self._resolve(PLAIN_DONE)
        self.assertTrue(res["ok"])
        self.assertTrue(res["confirmed"])
        self.assertEqual(res["steps"], ["confirmed"])

    def test_scripted_done_page_is_not_believed(self):
        res = self._resolve(SCRIPTED_DONE)
        self.assertFalse(res["ok"])
        self.assertFalse(res["confirmed"])
        self.assertEqual(res["steps"], ["scripted"])
        self.assertIn("script", res["error"])


if __name__ == "__main__":
    unittest.main()
