"""Contact autocomplete: a keystroke never waits on Gmail, and a refresh only reads new mail."""
# ruff: noqa: SLF001  (white-box tests: they set and read app's module-level contact state)
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


def _msg(ts, to, cc=""):
    headers = [{"name": "To", "value": to}]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    return {"internalDate": str(ts * 1000), "payload": {"headers": headers}}


class _Resp:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", str(tmp_path / "test.db"))
    app.migrate_db()
    monkeypatch.setattr(app, "_contacts", {"list": None, "at": 0.0, "running": False})
    monkeypatch.setattr(app, "CONTACTS_PACE", 0.0)
    return app


def _fake_gmail(monkeypatch, msgs, fail=None):
    """Serve msgs by id through app._request; `fail` maps an id to an HTTP status."""
    fetched = []

    def fake_request(method, path, tries=6, **kw):
        mid = path.rsplit("/", 1)[1]
        assert tries == 1, "the refresh must not retry (a retry storm is the bug)"
        if fail and mid in fail:
            raise app.GmailHTTPError(fail[mid], "rateLimitExceeded")
        fetched.append(mid)
        return _Resp(msgs[mid])

    monkeypatch.setattr(app, "_request", fake_request)
    return fetched


def _wait_idle(timeout=10):
    end = time.monotonic() + timeout
    while app._contacts["running"] and time.monotonic() < end:
        time.sleep(0.02)
    assert not app._contacts["running"], "refresh never finished"


def test_keystrokes_do_not_wait_for_a_slow_refresh(fresh, monkeypatch):
    conn = app.db()
    with conn:
        conn.execute("INSERT INTO contacts VALUES ('robin@example.com', 'robin@example.com', 'Robin', 5)")
    conn.close()
    calls = []

    def slow_list_ids(**kw):
        calls.append(kw)
        time.sleep(2.0)  # a Gmail fan-out that takes seconds
        return []

    monkeypatch.setattr(app, "list_ids", slow_list_ids)
    durations, found = [], {}

    def keystroke(q):
        t0 = time.monotonic()
        r = app.app.test_client().get("/api/contacts?q=" + q)
        durations.append(time.monotonic() - t0)
        found[q] = [c["email"] for c in r.get_json()["contacts"]]

    threads = [threading.Thread(target=keystroke, args=(q,))
               for q in ("ro", "rob", "robin", "zq", "zqx", "zqxw")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(durations) < 0.5, durations
    assert found == {"ro": ["robin@example.com"], "rob": ["robin@example.com"],
                     "robin": ["robin@example.com"], "zq": [], "zqx": [], "zqxw": []}
    _wait_idle()
    assert len(calls) == 1  # six keystrokes, one refresh


def test_refresh_reads_only_new_mail_and_keeps_the_newest_name(fresh, monkeypatch):
    msgs = {
        "m1": _msg(1000, "Jane Doe <jane@x.com>", "bob@y.com"),
        "m2": _msg(2000, '"Doe, Jane" <Jane@X.com>, Carl <carl@z.com>'),
        "m3": _msg(3000, "jane@x.com"),  # newest, but no display name
    }
    ids = ["m2", "m1"]
    fetched = _fake_gmail(monkeypatch, msgs)
    monkeypatch.setattr(app, "list_ids", lambda **kw: list(ids))

    app._refresh_contacts()
    got = {c["email"].lower(): c for c in app._contacts["list"]}
    assert got["jane@x.com"] == {"name": "Doe, Jane", "email": "Jane@X.com"}  # quoted comma kept
    assert got["carl@z.com"]["name"] == "Carl"
    assert got["bob@y.com"] == {"name": "", "email": "bob@y.com"}
    assert app._contacts["list"][-1]["email"] == "bob@y.com"  # oldest correspondent last
    assert sorted(fetched) == ["m1", "m2"]

    fetched.clear()
    app._refresh_contacts()
    assert fetched == []  # nothing new was sent

    ids[:0] = ["m3"]
    app._refresh_contacts()
    assert fetched == ["m3"]
    jane = next(c for c in app._contacts["list"] if c["email"].lower() == "jane@x.com")
    assert jane == {"name": "Doe, Jane", "email": "jane@x.com"}  # newest spelling, name kept
    assert app._contacts["list"][0] is jane


def test_rate_limit_stops_the_refresh_and_the_next_one_resumes(fresh, monkeypatch):
    msgs = {f"m{i}": _msg(1000 + i, f"p{i}@x.com") for i in range(1, 6)}
    ids = ["m5", "m4", "m3", "m2", "m1"]
    monkeypatch.setattr(app, "list_ids", lambda **kw: list(ids))
    fetched = _fake_gmail(monkeypatch, msgs, fail={"m3": 403})

    app._refresh_contacts()
    assert fetched == ["m5", "m4"]  # stopped at the refusal, no calls after it
    assert [c["email"] for c in app._contacts["list"]] == ["p5@x.com", "p4@x.com"]
    age = time.time() - app._contacts["at"]
    assert app.CONTACTS_TTL - 61 < age <= app.CONTACTS_TTL - 59  # next try in a minute

    fetched = _fake_gmail(monkeypatch, msgs)  # the limit has cleared
    app._refresh_contacts()
    assert fetched == ["m3", "m2", "m1"]  # resumes where it stopped
    assert len(app._contacts["list"]) == 5


def test_failed_refresh_retries_after_a_minute(fresh, monkeypatch):
    def broken(**kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(app, "list_ids", broken)
    app._refresh_contacts()
    age = time.time() - app._contacts["at"]
    assert app.CONTACTS_TTL - 61 < age <= app.CONTACTS_TTL - 59
    assert not app._contacts["running"]
