"""Quota budget and the persistent list-row cache (Gmail charges ~50 units per get)."""

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app


@pytest.fixture
def quota(monkeypatch):
    monkeypatch.setattr(app, "_quota_log", app.deque())
    monkeypatch.setattr(app, "_quota_hold", {"until": 0.0})
    return app


def test_costs():
    assert app._quota_cost("GET", "/messages/abc") == 50
    assert app._quota_cost("GET", "/drafts/abc") == 50
    assert app._quota_cost("GET", "/threads/abc") == 100
    assert app._quota_cost("GET", "/messages") == 5
    assert app._quota_cost("GET", "/history") == 5


def test_foreground_waits_for_the_window_to_roll(quota, monkeypatch):
    monkeypatch.setattr(app, "QUOTA_FG_BUDGET", 100)
    quota._quota_log.append((time.monotonic() - 59.7, 100))   # expires in 0.3 s
    t0 = time.monotonic()
    quota._quota_take(50)
    assert 0.2 < time.monotonic() - t0 < 2.0


def test_background_yields_before_foreground(quota, monkeypatch):
    monkeypatch.setattr(app, "QUOTA_FG_BUDGET", 1000)
    monkeypatch.setattr(app, "QUOTA_BG_BUDGET", 100)
    quota._quota_log.append((time.monotonic(), 100))
    t0 = time.monotonic()
    quota._quota_take(50)                       # foreground: room under 1000
    assert time.monotonic() - t0 < 0.1

    done = threading.Event()

    def bg():
        app._bg.on = True
        app._quota_take(50)
        done.set()

    threading.Thread(target=bg, daemon=True).start()
    assert not done.wait(0.5), "background must wait while the minute is over its budget"


def test_quota_refusal_holds_every_thread(quota):
    quota._quota_exceeded(0.3)
    t0 = time.monotonic()
    quota._quota_take(5)
    assert time.monotonic() - t0 >= 0.25


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", str(tmp_path / "test.db"))
    app.migrate_db()
    app._meta_cache.clear()
    app._meta_tids.clear()
    return app


def _store(a, mid, tid):
    a._meta_db_put(mid, {"id": mid, "threadId": tid, "labelIds": ["INBOX"]})


def test_restore_keeps_unchanged_and_evicts_moved(cache, monkeypatch):
    _store(cache, "m1", "t1")
    _store(cache, "m2", "t2")
    cache._set_setting("meta_history_id", "100")
    monkeypatch.setattr(app, "gget", lambda path, **p: {
        "historyId": "150",
        "history": [{"labelRemoved": [{"message": {"id": "m2"}, "labelIds": ["INBOX"]}]}]})
    assert cache._meta_restore() == "150"
    assert "m1" in cache._meta_cache and "m2" not in cache._meta_cache
    conn = cache.db()
    left = {r[0] for r in conn.execute("SELECT message_id FROM meta_cache")}
    conn.close()
    assert left == {"m1"}


def test_restore_clears_when_anchor_is_too_old(cache, monkeypatch):
    _store(cache, "m1", "t1")
    cache._set_setting("meta_history_id", "100")

    def gone(path, **p):
        raise app.GmailHTTPError(404, "historyId too old")

    monkeypatch.setattr(app, "gget", gone)
    assert cache._meta_restore() is None
    assert not cache._meta_cache
    conn = cache.db()
    assert conn.execute("SELECT COUNT(*) FROM meta_cache").fetchone()[0] == 0
    conn.close()


def test_put_and_evict_write_through(cache):
    cache._meta_put("m9", {"id": "m9", "threadId": "t9"})
    conn = cache.db()
    assert json.loads(conn.execute("SELECT body FROM meta_cache").fetchone()[0])["id"] == "m9"
    conn.close()
    cache._meta_evict_thread("t9")
    conn = cache.db()
    assert conn.execute("SELECT COUNT(*) FROM meta_cache").fetchone()[0] == 0
    conn.close()
