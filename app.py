# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "flask",
#   "google-auth",
#   "google-auth-oauthlib",
#   "requests",
# ]
# ///
"""
Inbox — a Google Inbox clone over the Gmail API.

Triage model mirrors Inbox-by-Gmail semantics on top of native Gmail labels:
  Done   = remove the INBOX label (archive)
  Pin    = STARRED label
  Snooze = remove INBOX + add "Snoozed" label; a background scheduler re-adds
           INBOX when the snooze time arrives (state in snooze.db)
  Bundles = native Gmail categories (Promotions/Social/Updates/Forums) plus a
           light keyword classifier for Travel / Purchases / Finance.

Send-to-Things builds a things:///add deep link whose notes carry a backlink
that reopens the exact thread in this app (inboxclone://thread/<id>), with a
Gmail web permalink as the fallback.
"""
import base64
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import random
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CRED_DIR = os.path.join(HERE, "credentials")
CLIENT_SECRET = os.path.join(CRED_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(CRED_DIR, "token.json")
DB_PATH = os.path.join(HERE, "snooze.db")
PORT = 5008

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",         # read, label, archive
    "https://www.googleapis.com/auth/gmail.send",           # send / reply
    "https://www.googleapis.com/auth/gmail.settings.basic",  # create filters (re-label)
]

CATEGORY_BUNDLES = {
    "CATEGORY_PROMOTIONS": "Promos",
    "CATEGORY_SOCIAL": "Social",
    "CATEGORY_UPDATES": "Updates",
    "CATEGORY_FORUMS": "Forums",
}
# Bundle display order + icons (Material icon ligatures rendered on the client)
BUNDLE_META = {
    "Travel": {"icon": "flight", "color": "#9C27B0"},
    "Purchases": {"icon": "local_offer", "color": "#795548"},
    "Finance": {"icon": "attach_money", "color": "#679F38"},
    "Social": {"icon": "people", "color": "#DB4437"},
    "Updates": {"icon": "notifications", "color": "#FF6839"},
    "Promos": {"icon": "loyalty", "color": "#00BCD4"},
    "Forums": {"icon": "forum", "color": "#3F51B5"},
}
BUNDLE_ORDER = ["Travel", "Purchases", "Finance", "Promos", "Social", "Updates", "Forums"]
VALID_BUNDLES = set(BUNDLE_ORDER) | {"Primary"}

# User-applied re-label overrides live in Gmail labels named "Bundle/<name>".
# They win over the keyword heuristic and native categories in _bundle_for.
BUNDLE_LABEL_PREFIX = "Bundle/"
_bundle_label_by_id = {}      # Gmail labelId -> bundle name
_bundle_labels_loaded = False

# Light keyword classifiers for the Inbox-only bundles Gmail has no native label for
TRAVEL_RE = re.compile(r"\b(flight|itinerary|boarding|reservation|hotel|booking|check-?in|airline|trip|departure|gate)\b", re.I)
PURCHASE_RE = re.compile(r"\b(order|shipped|delivery|tracking|receipt|your package|dispatched|invoice|out for delivery)\b", re.I)
FINANCE_RE = re.compile(r"\b(statement|payment|invoice|balance|transaction|deposit|bank|credit card|autopay|bill is)\b", re.I)

# Highlight chips — lightweight heuristic stand-in for Inbox's schema.org/ML highlights
PRICE_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
TRACK_RE = re.compile(r"\b(out for delivery|arriving today|arriving|delivered|has shipped|shipped|on its way|tracking)\b", re.I)
FLIGHT_HL_RE = re.compile(r"\b(flight|boarding|departs?|gate|check[- ]?in)\b", re.I)
ORDER_RE = re.compile(r"\border\s*#?\s*[\w-]{3,}", re.I)


def compute_highlights(subject, snippet, bundle):
    blob = f"{subject} {snippet}"
    chips = []
    if bundle == "Travel" or FLIGHT_HL_RE.search(blob):
        chips.append({"icon": "flight", "text": "Travel"})
    tm = TRACK_RE.search(blob)
    if tm:
        chips.append({"icon": "local_shipping", "text": tm.group(0).title()})
    om = ORDER_RE.search(blob)
    if om:
        chips.append({"icon": "receipt_long", "text": om.group(0).strip()[:22]})
    pm = PRICE_RE.search(blob)
    if pm:
        chips.append({"icon": "payments", "text": pm.group(0).replace(" ", "")})
    seen, out = set(), []
    for c in chips:
        if c["icon"] in seen:
            continue
        seen.add(c["icon"])
        out.append(c)
    return out[:2]


app = Flask(__name__)
# Don't let the (WKWebView/browser) client cache static JS/CSS — otherwise it can
# run stale code after an update. Combined with the ?v= query, this keeps it fresh.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
_user_email = None

# ----------------------------------------------------------------------------
# Gmail REST client (raw HTTPS via google-auth's AuthorizedSession — no
# google-api-python-client / httplib2 / discovery doc, which keeps RAM low).
# requests/urllib3 is thread-safe for issuing requests, so one session is shared.
# ----------------------------------------------------------------------------
BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_session = None
_session_lock = threading.Lock()
_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "backendError"}


class GmailHTTPError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body or ""
        super().__init__(f"Gmail {status}: {self.body[:200]}")


def _load_or_consent_creds():
    creds = None
    granted = []
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        try:
            granted = json.load(open(TOKEN_PATH)).get("scopes") or []
        except Exception:
            granted = []
    # Re-consent if a new scope was added since the token was minted (read granted
    # scopes from the file — creds.scopes just echoes what we passed in).
    have_scopes = bool(creds) and set(SCOPES).issubset(set(granted))
    if not (creds and creds.valid and have_scopes):
        if creds and creds.expired and creds.refresh_token and have_scopes:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        os.chmod(TOKEN_PATH, 0o600)
    return creds


def get_session():
    global _session, _user_email
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            _session = AuthorizedSession(_load_or_consent_creds())
    if _user_email is None:
        try:
            _user_email = gget("/profile").get("emailAddress")
        except Exception:
            pass
    return _session


def get_service():  # back-compat: ensures auth is ready and returns the session
    return get_session()


def _is_rate(resp):
    try:
        reasons = {e.get("reason") for e in resp.json().get("error", {}).get("errors", [])}
        if reasons & _RATE_REASONS:
            return True
    except Exception:
        pass
    return "rateLimitExceeded" in resp.text or "Quota exceeded" in resp.text


def _request(method, path, tries=6, **kw):
    s = get_session()
    url = BASE + path
    for attempt in range(tries):
        try:
            r = s.request(method, url, timeout=30, **kw)
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(min(32.0, 2 ** attempt) + random.uniform(0, 0.5))
            continue
        if r.status_code in (429, 500, 502, 503, 504) or (r.status_code == 403 and _is_rate(r)):
            if attempt == tries - 1:
                raise GmailHTTPError(r.status_code, r.text)
            time.sleep(min(32.0, 2 ** attempt) + random.uniform(0, 0.5))
            continue
        if r.status_code >= 400:
            raise GmailHTTPError(r.status_code, r.text)
        return r


def gget(path, **params):
    return _request("GET", path, params=(params or None)).json()


def gpost(path, json=None):
    return _request("POST", path, json=json).json()


def gdelete(path):
    _request("DELETE", path)
    return {}


def gmail_permalink(thread_id):
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


# ----------------------------------------------------------------------------
# Message parsing helpers
# ----------------------------------------------------------------------------
def _header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _parse_addr(value):
    # "Jane Doe <jane@x.com>" -> ("Jane Doe", "jane@x.com")
    m = re.match(r"\s*\"?([^\"<]*)\"?\s*<([^>]+)>", value or "")
    if m:
        name = m.group(1).strip() or m.group(2).strip()
        return name, m.group(2).strip()
    return (value or "").strip(), (value or "").strip()


def _fmt_time(date_str):
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        now = datetime.now().astimezone()
        if local.date() == now.date():
            return local.strftime("%-I:%M %p")
        if (now.date() - local.date()).days == 1:
            return "Yesterday"
        if local.year == now.year:
            return local.strftime("%b %-d")
        return local.strftime("%b %-d, %Y")
    except Exception:
        return ""


def _bundle_for(labels, subject, sender, snippet):
    # 1. User re-label override (Bundle/<name>) wins over everything
    for lbl in labels:
        if lbl in _bundle_label_by_id:
            b = _bundle_label_by_id[lbl]
            return None if b == "Primary" else b
    # 2. Keyword heuristic
    blob = f"{subject} {sender} {snippet}"
    if TRAVEL_RE.search(blob):
        return "Travel"
    if PURCHASE_RE.search(blob):
        return "Purchases"
    if FINANCE_RE.search(blob):
        return "Finance"
    # 3. Native Gmail category
    for lbl in labels:
        if lbl in CATEGORY_BUNDLES:
            return CATEGORY_BUNDLES[lbl]
    return None  # Primary / main list


def parse_unsubscribe(headers):
    """Parse List-Unsubscribe / List-Unsubscribe-Post (RFC 2369 / RFC 8058)."""
    raw = _header(headers, "List-Unsubscribe")
    if not raw:
        return None
    one_click = "one-click" in _header(headers, "List-Unsubscribe-Post").lower()
    https_url, mailto, mailto_subject = None, None, "unsubscribe"
    for uri in re.findall(r"<([^>]+)>", raw):
        uri = uri.strip()
        low = uri.lower()
        if low.startswith("http") and not https_url:
            https_url = uri
        elif low.startswith("mailto:") and not mailto:
            body = uri[len("mailto:"):]
            if "?" in body:
                mailto, qs = body.split("?", 1)
                sm = re.search(r"subject=([^&]+)", qs, re.I)
                if sm:
                    from urllib.parse import unquote
                    mailto_subject = unquote(sm.group(1))
            else:
                mailto = body
    if one_click and https_url and https_url.lower().startswith("https"):
        method = "one-click"
    elif mailto:
        method = "mailto"
    elif https_url:
        method = "link"
    else:
        return None
    return {"available": True, "method": method, "httpsUrl": https_url,
            "mailto": mailto, "mailtoSubject": mailto_subject}


def _decode_body(payload):
    """Return (text, html) walking the MIME tree."""
    text, html = "", ""

    def walk(part):
        nonlocal text, html
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data and not text:
            text = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        elif mime == "text/html" and data:
            html = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return text, html


def _part_disposition(part):
    for h in part.get("headers", []) or []:
        if h.get("name", "").lower() == "content-disposition":
            return h.get("value", "").lower()
    return ""


def _collect_attachments(payload):
    """Walk the MIME tree and return real attachments (named, non-inline parts)."""
    out = []

    def walk(part):
        fn = part.get("filename") or ""
        body = part.get("body", {})
        # Skip inline parts (e.g. logos/signature images embedded in the body).
        if fn and body.get("attachmentId") and not _part_disposition(part).startswith("inline"):
            mime = part.get("mimeType", "application/octet-stream")
            out.append({
                "filename": fn,
                "mimeType": mime,
                "size": body.get("size", 0),
                "attachmentId": body["attachmentId"],
                "drive": mime.startswith("application/vnd.google-apps"),  # open in Drive, not downloadable
            })
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return out


def thread_summary(msg, outgoing=False):
    """Build a list-row summary from a metadata message resource.
    outgoing=True (Sent) shows the recipient ('To: …') instead of the sender."""
    headers = msg.get("payload", {}).get("headers", [])
    subject = _header(headers, "Subject") or "(no subject)"
    labels = msg.get("labelIds", [])
    snippet = msg.get("snippet", "")
    if outgoing:
        name, addr = _parse_addr(_header(headers, "To"))
        sender = "To: " + (name or "(no recipient)")
        bundle = None
    else:
        name, addr = _parse_addr(_header(headers, "From"))
        sender = name
        bundle = _bundle_for(labels, subject, name, snippet)
    return {
        "id": msg.get("threadId"),
        "messageId": msg.get("id"),
        "sender": sender,
        "senderEmail": addr,
        "subject": subject,
        "snippet": snippet,
        "time": _fmt_time(_header(headers, "Date")),
        "ts": msg.get("internalDate", "0"),
        "unread": "UNREAD" in labels,
        "pinned": "STARRED" in labels,
        "bundle": bundle,
        "highlights": [] if outgoing else compute_highlights(subject, snippet, bundle),
        "unsub": None if outgoing else parse_unsubscribe(headers),
        "attachments": [dict(a, messageId=msg.get("id")) for a in _collect_attachments(msg.get("payload", {}))],
        "permalink": gmail_permalink(msg.get("threadId")),
    }


# ----------------------------------------------------------------------------
# Gmail operations
# ----------------------------------------------------------------------------
def list_ids(q=None, label_ids=None, max_results=60, page_token=None, return_token=False):
    params = {"maxResults": max_results}
    if q:
        params["q"] = q
    if label_ids:
        params["labelIds"] = label_ids
    if page_token:
        params["pageToken"] = page_token
    resp = gget("/messages", **params)
    ids = [m["id"] for m in resp.get("messages", [])]
    if return_token:
        return ids, resp.get("nextPageToken")
    return ids


def load_bundle_labels(force=False):
    """Cache the id->name map for our Bundle/<name> override labels."""
    global _bundle_labels_loaded
    if _bundle_labels_loaded and not force:
        return
    labels = gget("/labels").get("labels", [])
    _bundle_label_by_id.clear()
    for l in labels:
        if l["name"].startswith(BUNDLE_LABEL_PREFIX):
            _bundle_label_by_id[l["id"]] = l["name"][len(BUNDLE_LABEL_PREFIX):]
    _bundle_labels_loaded = True


def ensure_bundle_label(bundle):
    lid = ensure_label(BUNDLE_LABEL_PREFIX + bundle)
    _bundle_label_by_id[lid] = bundle
    return lid


def ensure_filter(sender, label_id):
    """Create a Gmail filter so future mail from sender gets label_id. Returns True if created."""
    existing = gget("/settings/filters").get("filter", [])
    for f in existing:
        crit = f.get("criteria", {})
        act = f.get("action", {})
        if crit.get("from", "").lower() == sender.lower() and label_id in (act.get("addLabelIds") or []):
            return False
    body = {"criteria": {"from": sender}, "action": {"addLabelIds": [label_id]}}
    gpost("/settings/filters", json=body)
    return True


# format=full but a fields mask that returns headers + the attachment part tree
# WITHOUT the body bytes — so list rows get attachments cheaply (no big body download).
_PART = "partId,mimeType,filename,headers,body/attachmentId,body/size"
_LIST_FIELDS = ("id,threadId,internalDate,labelIds,snippet,"
                f"payload(mimeType,headers,parts({_PART},parts({_PART},parts({_PART}))))")


def _get_meta(mid):
    try:
        return gget(f"/messages/{mid}", format="full", fields=_LIST_FIELDS)
    except Exception:
        return None  # message vanished (deleted/moved) — drop it


def summarize_ids(ids, outgoing=False):
    if not ids:
        return []
    if not outgoing:
        load_bundle_labels()
    # Concurrent fetches (requests/urllib3 is thread-safe). gget retries rate-limits
    # internally; a small worker cap keeps us under the per-second quota.
    with ThreadPoolExecutor(max_workers=8) as ex:
        msgs = list(ex.map(_get_meta, ids))

    # newest message per thread
    by_thread = {}
    for msg in msgs:
        if not msg:
            continue
        tid = msg.get("threadId")
        prev = by_thread.get(tid)
        if not prev or int(msg.get("internalDate", 0)) > int(prev.get("internalDate", 0)):
            by_thread[tid] = msg
    summaries = [thread_summary(m, outgoing=outgoing) for m in by_thread.values()]
    summaries.sort(key=lambda r: int(r["ts"]), reverse=True)
    return summaries


def fetch_inbox(max_results=60):
    return summarize_ids(list_ids(label_ids=["INBOX"], max_results=max_results))


def fetch_thread(thread_id):
    thread = gget(f"/threads/{thread_id}", format="full")
    messages = []
    last_rfc_id = ""
    subject = ""
    unsub = None
    unsub_mid = None
    for m in thread.get("messages", []):
        headers = m.get("payload", {}).get("headers", [])
        name, email_addr = _parse_addr(_header(headers, "From"))
        subject = subject or _header(headers, "Subject")
        text, html = _decode_body(m.get("payload", {}))
        last_rfc_id = _header(headers, "Message-ID") or last_rfc_id
        u = parse_unsubscribe(headers)
        if u:
            unsub, unsub_mid = u, m.get("id")  # prefer the most recent message's list-unsubscribe
        messages.append({
            "id": m.get("id"),
            "sender": name,
            "senderEmail": email_addr,
            "to": _header(headers, "To"),
            "date": _fmt_time(_header(headers, "Date")),
            "text": text,
            "html": html,
            "attachments": _collect_attachments(m.get("payload", {})),
            "unread": "UNREAD" in m.get("labelIds", []),
        })
    return {
        "id": thread_id,
        "subject": subject or "(no subject)",
        "messages": messages,
        "rfcMessageId": last_rfc_id,
        "permalink": gmail_permalink(thread_id),
        "pinned": "STARRED" in (thread.get("messages", [{}])[-1].get("labelIds", [])),
        "unsub": unsub,
        "unsubMessageId": unsub_mid,
    }


def modify_thread(thread_id, add=None, remove=None):
    body = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove
    return gpost(f"/threads/{thread_id}/modify", json=body)


def ensure_label(name):
    labels = gget("/labels").get("labels", [])
    for l in labels:
        if l["name"] == name:
            return l["id"]
    created = gpost("/labels", json={"name": name, "labelListVisibility": "labelShow",
                                     "messageListVisibility": "show"})
    return created["id"]


def mark_read(thread_id):
    try:
        modify_thread(thread_id, remove=["UNREAD"])
    except Exception:
        pass


def send_message(to, subject, body, thread_id=None, in_reply_to=None,
                 body_html=None, attachments=None):
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    if _user_email:
        msg["From"] = _user_email
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body or "")          # plain-text part (fallback)
    if body_html:
        msg.add_alternative(body_html, subtype="html")  # -> multipart/alternative
    for fname, mime, data in (attachments or []):
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=fname or "attachment")  # -> multipart/mixed
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    send_body = {"raw": raw}
    if thread_id:
        send_body["threadId"] = thread_id
    return gpost("/messages/send", json=send_body)


# ----------------------------------------------------------------------------
# Snooze scheduler
# ----------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS snoozed (thread_id TEXT PRIMARY KEY, wake_at INTEGER)")
    return conn


def snooze_thread(thread_id, wake_epoch):
    ensure_label("Snoozed")
    modify_thread(thread_id, add=[ensure_label("Snoozed")], remove=["INBOX"])
    conn = db()
    conn.execute("INSERT OR REPLACE INTO snoozed VALUES (?,?)", (thread_id, int(wake_epoch)))
    conn.commit()
    conn.close()


def _scheduler_loop():
    while True:
        try:
            now = int(time.time())
            conn = db()
            due = conn.execute("SELECT thread_id FROM snoozed WHERE wake_at <= ?", (now,)).fetchall()
            for (tid,) in due:
                try:
                    snoozed_id = ensure_label("Snoozed")
                    modify_thread(tid, add=["INBOX"], remove=[snoozed_id])
                except Exception:
                    pass
                conn.execute("DELETE FROM snoozed WHERE thread_id=?", (tid,))
            conn.commit()
            conn.close()
        except Exception:
            pass
        time.sleep(60)


# ----------------------------------------------------------------------------
# Live sync — poll Gmail's History API for deltas, push to browsers via SSE
# ----------------------------------------------------------------------------
POLL_INTERVAL = 8  # seconds
SYNC = {"version": 0, "history_id": None}
_focus = {"tid": None, "v": 0}  # deep-link "open this thread" signal, pushed over SSE


def _history_loop():
    while True:
        try:
            if SYNC["history_id"] is None:
                SYNC["history_id"] = gget("/profile").get("historyId")
            else:
                try:
                    resp = gget("/history", startHistoryId=SYNC["history_id"],
                                historyTypes=["messageAdded", "labelAdded", "labelRemoved"])
                    if resp.get("history"):
                        SYNC["version"] += 1  # something changed -> tell the browser
                    SYNC["history_id"] = resp.get("historyId", SYNC["history_id"])
                except GmailHTTPError as he:
                    # 404 = startHistoryId too old; reset cursor and force a refresh
                    if he.status == 404:
                        SYNC["history_id"] = gget("/profile").get("historyId")
                        SYNC["version"] += 1
                    else:
                        raise
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)


# ----------------------------------------------------------------------------
# Snooze preset resolution (Inbox-style)
# ----------------------------------------------------------------------------
def resolve_snooze(preset):
    from datetime import timedelta
    now = datetime.now().astimezone()
    if preset == "later_today":
        target = now + timedelta(hours=3)
    elif preset == "tomorrow":
        target = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    elif preset == "this_weekend":
        days = (5 - now.weekday()) % 7  # Saturday
        days = days or 7
        target = (now + timedelta(days=days)).replace(hour=8, minute=0, second=0, microsecond=0)
    elif preset == "next_week":
        days = (7 - now.weekday()) % 7 or 7
        target = (now + timedelta(days=days)).replace(hour=8, minute=0, second=0, microsecond=0)
    elif preset == "someday":
        import random
        target = (now + timedelta(days=random.randint(30, 90))).replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        target = now + timedelta(hours=3)
    return int(target.timestamp())


# ----------------------------------------------------------------------------
# Routes — pages
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", open_thread="")


@app.route("/thread/<thread_id>")
def open_thread_route(thread_id):
    """Deep-link target. If the thread can't be served, fall back to Gmail."""
    gmail_fallback = request.args.get("gmail") or gmail_permalink(thread_id)
    try:
        get_service()
        fetch_thread(thread_id)  # verify it exists / is reachable
    except Exception:
        return redirect(gmail_fallback)
    return render_template("index.html", open_thread=thread_id)


# ----------------------------------------------------------------------------
# Routes — API
# ----------------------------------------------------------------------------
@app.route("/api/health")
def api_health():
    authed = os.path.exists(TOKEN_PATH)
    return jsonify({"ok": True, "authed": authed, "email": _user_email})


@app.route("/api/stream")
def api_stream():
    """Server-Sent Events: emit on mailbox change (version) or a deep-link focus request."""
    def gen():
        last = SYNC["version"]
        last_focus = _focus["v"]
        yield "retry: 4000\n\n"
        while True:
            if _focus["v"] != last_focus:
                last_focus = _focus["v"]
                yield f"data: {json.dumps({'focus': _focus['tid']})}\n\n"
            elif SYNC["version"] != last:
                last = SYNC["version"]
                yield f"data: {json.dumps({'version': last})}\n\n"
            else:
                yield ": ping\n\n"  # heartbeat keeps the connection open
            time.sleep(1)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


@app.route("/api/open_thread", methods=["POST"])
def api_open_thread():
    """A deep link asks the running window to surface a thread (pushed via SSE)."""
    _focus["tid"] = (request.json or {}).get("threadId")
    _focus["v"] += 1
    return jsonify({"ok": True})


def _limit_arg(default=50):
    # Per-page size. Capped at 100 so a single fetch stays a small quota burst;
    # depth comes from pagination (pageToken), not from an ever-growing window.
    try:
        return min(max(int(request.args.get("limit", default)), 1), 100)
    except (TypeError, ValueError):
        return default


def inbox_totals():
    """Authoritative inbox counts straight from Gmail's INBOX label (no message fetch)."""
    info = gget("/labels/INBOX")
    return info.get("messagesTotal"), info.get("messagesUnread")


@app.route("/api/inbox")
def api_inbox():
    limit = _limit_arg()
    token = request.args.get("pageToken")
    try:
        ids, next_token = list_ids(label_ids=["INBOX"], max_results=limit,
                                   page_token=token, return_token=True)
        rows = summarize_ids(ids)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    try:
        total, unread = inbox_totals()
    except Exception:
        total, unread = None, None
    pinned = [r for r in rows if r["pinned"]]
    primary = [r for r in rows if not r["bundle"] and not r["pinned"]]
    bundles = {}
    for r in rows:
        if r["pinned"]:
            continue
        if r["bundle"]:
            bundles.setdefault(r["bundle"], []).append(r)
    bundle_list = []
    for name in BUNDLE_ORDER:
        if name in bundles:
            bundle_list.append({
                "name": name,
                "icon": BUNDLE_META[name]["icon"],
                "color": BUNDLE_META[name]["color"],
                "count": len(bundles[name]),
                "items": bundles[name],
            })
    return jsonify({"pinned": pinned, "primary": primary, "bundles": bundle_list,
                    "email": _user_email, "limit": limit, "fetched": len(ids),
                    "nextPageToken": next_token, "inboxTotal": total, "inboxUnread": unread})


@app.route("/api/snoozed")
def api_snoozed():
    limit = _limit_arg()
    token = request.args.get("pageToken")
    try:
        sid = ensure_label("Snoozed")
        ids, next_token = list_ids(label_ids=[sid], max_results=limit,
                                   page_token=token, return_token=True)
        rows = summarize_ids(ids)
        conn = db()
        waked = dict(conn.execute("SELECT thread_id, wake_at FROM snoozed").fetchall())
        conn.close()
        for r in rows:
            w = waked.get(r["id"])
            if w:
                r["wake"] = "Snoozed until " + datetime.fromtimestamp(w).astimezone().strftime("%b %-d, %-I:%M %p")
        return jsonify({"items": rows, "email": _user_email, "limit": limit,
                        "fetched": len(ids), "nextPageToken": next_token})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/done_list")
def api_done_list():
    limit = _limit_arg()
    token = request.args.get("pageToken")
    try:
        ids, next_token = list_ids(q="-in:inbox -in:sent -in:draft -in:trash -in:spam -in:chats",
                                   max_results=limit, page_token=token, return_token=True)
        rows = summarize_ids(ids)
        return jsonify({"items": rows, "email": _user_email, "limit": limit,
                        "fetched": len(ids), "nextPageToken": next_token})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sent")
def api_sent():
    limit = _limit_arg()
    token = request.args.get("pageToken")
    try:
        ids, next_token = list_ids(label_ids=["SENT"], max_results=limit,
                                   page_token=token, return_token=True)
        rows = summarize_ids(ids, outgoing=True)
        return jsonify({"items": rows, "email": _user_email, "limit": limit,
                        "fetched": len(ids), "nextPageToken": next_token})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"items": []})
    limit = _limit_arg()
    token = request.args.get("pageToken")
    try:
        ids, next_token = list_ids(q=q, max_results=limit, page_token=token, return_token=True)
        rows = summarize_ids(ids)
        return jsonify({"items": rows, "email": _user_email, "query": q, "limit": limit,
                        "fetched": len(ids), "nextPageToken": next_token})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/drafts")
def api_drafts():
    limit = _limit_arg()
    try:
        drafts = gget("/drafts", maxResults=limit).get("drafts", [])
        mid_to_did, ids = {}, []
        for dr in drafts:
            mid = (dr.get("message") or {}).get("id")
            if mid:
                mid_to_did[mid] = dr["id"]
                ids.append(mid)
        with ThreadPoolExecutor(max_workers=8) as ex:
            fetched = list(ex.map(_get_meta, ids))
        items = []
        for mid, msg in zip(ids, fetched):
            if not msg:
                continue
            headers = msg.get("payload", {}).get("headers", [])
            to_name, _ = _parse_addr(_header(headers, "To"))
            items.append({
                "draftId": mid_to_did[mid], "messageId": mid, "draft": True,
                "sender": ("To: " + (to_name or "(no recipient)")),
                "subject": _header(headers, "Subject") or "(no subject)",
                "snippet": msg.get("snippet", ""),
                "time": _fmt_time(_header(headers, "Date")),
                "ts": msg.get("internalDate", "0"),
            })
        items.sort(key=lambda r: int(r["ts"]), reverse=True)
        return jsonify({"items": items, "email": _user_email, "fetched": len(items), "nextPageToken": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/draft/<draft_id>")
def api_draft(draft_id):
    try:
        dr = gget(f"/drafts/{draft_id}", format="full")
        payload = dr.get("message", {}).get("payload", {})
        headers = payload.get("headers", [])
        text, html = _decode_body(payload)
        body = text or re.sub(r"<[^>]+>", "", html or "")
        return jsonify({"draftId": draft_id, "to": _header(headers, "To"),
                        "subject": _header(headers, "Subject"), "body": body, "bodyHtml": html or None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/discard_draft", methods=["POST"])
def api_discard_draft():
    try:
        gdelete(f"/drafts/{request.json['draftId']}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/thread/<thread_id>")
def api_thread(thread_id):
    try:
        data = fetch_thread(thread_id)
        mark_read(thread_id)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/attachment/<message_id>/<attachment_id>")
def api_attachment(message_id, attachment_id):
    """Stream a Gmail attachment (decoded from base64url) so the browser can open/save it."""
    try:
        data = gget(f"/messages/{message_id}/attachments/{attachment_id}")
        content = base64.urlsafe_b64decode(data.get("data", ""))
        name = (request.args.get("name") or "attachment").replace('"', "")
        mime = request.args.get("mime") or "application/octet-stream"
        # Preview images/PDFs in the browser; download everything else.
        disp = "inline" if (mime.startswith("image/") or mime == "application/pdf") else "attachment"
        return Response(content, mimetype=mime,
                        headers={"Content-Disposition": f'{disp}; filename="{name}"'})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/done", methods=["POST"])
def api_done():
    tid = request.json["threadId"]
    modify_thread(tid, remove=["INBOX"])
    return jsonify({"ok": True})


@app.route("/api/undo_done", methods=["POST"])
def api_undo_done():
    tid = request.json["threadId"]
    modify_thread(tid, add=["INBOX"])
    return jsonify({"ok": True})


@app.route("/api/pin", methods=["POST"])
def api_pin():
    tid = request.json["threadId"]
    if request.json.get("pinned"):
        modify_thread(tid, add=["STARRED"])
    else:
        modify_thread(tid, remove=["STARRED"])
    return jsonify({"ok": True})


@app.route("/api/snooze", methods=["POST"])
def api_snooze():
    tid = request.json["threadId"]
    preset = request.json.get("preset", "later_today")
    wake = request.json.get("epoch") or resolve_snooze(preset)
    snooze_thread(tid, wake)
    return jsonify({"ok": True, "wake": wake})


@app.route("/api/relabel", methods=["POST"])
def api_relabel():
    d = request.json or {}
    tid = d.get("threadId")
    bundle = d.get("bundle")
    sender = (d.get("sender") or "").strip()
    apply_future = bool(d.get("applyFuture"))
    if not tid or bundle not in VALID_BUNDLES:
        return jsonify({"error": "bad request"}), 400
    try:
        target_id = ensure_bundle_label(bundle)
        # move: add the target Bundle/* label, strip any other Bundle/* labels
        remove = [lid for lid in _bundle_label_by_id if lid != target_id]
        modify_thread(tid, add=[target_id], remove=remove or None)
        created_filter = False
        if apply_future and sender:
            created_filter = ensure_filter(sender, target_id)
        return jsonify({"ok": True, "bundle": bundle, "filtered": created_filter})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/send", methods=["POST"])
def api_send():
    ctype = request.content_type or ""
    if ctype.startswith("multipart/form-data"):
        f = request.form
        to, subject = f.get("to", ""), f.get("subject", "")
        body_text, body_html = f.get("text", ""), (f.get("html") or None)
        thread_id, in_reply_to = f.get("threadId") or None, f.get("inReplyTo") or None
        draft_id = f.get("draftId") or None
        attachments = [(fl.filename, fl.mimetype, fl.read()) for fl in request.files.getlist("attachments")]
    else:
        d = request.json or {}
        to, subject = d.get("to", ""), d.get("subject", "")
        body_text, body_html = d.get("body", ""), d.get("html")
        thread_id, in_reply_to = d.get("threadId"), d.get("inReplyTo")
        draft_id, attachments = d.get("draftId"), []
    try:
        send_message(to, subject, body_text, thread_id=thread_id, in_reply_to=in_reply_to,
                     body_html=body_html, attachments=attachments)
        if draft_id:
            try:
                gdelete(f"/drafts/{draft_id}")
            except Exception:
                pass
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unsubscribe", methods=["POST"])
def api_unsubscribe():
    """Re-read the message's headers server-side and act per RFC 2369 / 8058."""
    import urllib.request
    mid = (request.json or {}).get("messageId")
    tid = (request.json or {}).get("threadId")
    try:
        if not mid and tid:
            th = gget(f"/threads/{tid}", format="metadata",
                      metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post"])
            msgs = th.get("messages", [])
            mid = msgs[-1]["id"] if msgs else None
        if not mid:
            return jsonify({"error": "no message id"}), 400
        msg = gget(f"/messages/{mid}", format="metadata",
                   metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post", "From", "Subject"])
        info = parse_unsubscribe(msg.get("payload", {}).get("headers", []))
        if not info:
            return jsonify({"error": "no List-Unsubscribe header"}), 400

        if info["method"] == "one-click":
            req = urllib.request.Request(
                info["httpsUrl"], data=b"List-Unsubscribe=One-Click",
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "User-Agent": "InboxClone/1.0"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return jsonify({"ok": True, "method": "one-click", "status": r.status})
            except Exception as e:
                # Sender's endpoint failed — let the user finish in the browser
                return jsonify({"ok": False, "method": "one-click",
                                "fallbackUrl": info["httpsUrl"], "error": str(e)})
        if info["method"] == "mailto":
            send_message(info["mailto"], info["mailtoSubject"] or "unsubscribe",
                         "Please unsubscribe this address from your mailing list.")
            return jsonify({"ok": True, "method": "mailto", "to": info["mailto"]})
        # link only — open in the browser
        return jsonify({"ok": True, "method": "link", "url": info["httpsUrl"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/to_things", methods=["POST"])
def api_to_things():
    d = request.json
    tid = d["threadId"]
    title = d.get("title", "(email)")
    sender = d.get("sender", "")
    snippet = d.get("snippet", "")
    permalink = gmail_permalink(tid)
    backlink = f"inboxclone://thread/{tid}?gmail={quote(permalink, safe='')}"
    notes = f"{backlink}\n\nGmail (fallback): {permalink}\n\nFrom: {sender}\n{snippet}"
    things = f"things:///add?title={quote(title)}&notes={quote(notes)}"
    try:
        subprocess.run(["open", things], check=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e), "url": things}), 500


def start_background():
    """Start the snooze scheduler, trigger auth, and start the history poller."""
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    # Trigger auth before serving so the consent flow opens cleanly on first run
    try:
        get_service()
    except Exception as e:
        print(f"[auth] deferred: {e}")
    threading.Thread(target=_history_loop, daemon=True).start()


def run_server():
    start_background()
    # threaded=True is required: SSE connections are long-lived and must not block other requests
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    run_server()
