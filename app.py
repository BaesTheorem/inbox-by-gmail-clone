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
import html as _htmlmod
import ipaddress
import json
import logging
import os
import re
import secrets
import queue
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import random
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("inbox")

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
_bundle_lock = threading.Lock()  # guards the _bundle_label_by_id dict across threads

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
        # Atomic 0600 write: create with restrictive perms from the start (no
        # world-readable window between open() and chmod), then rename into place.
        tmp = TOKEN_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(creds.to_json())
        os.replace(tmp, TOKEN_PATH)
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


def gput(path, json=None):
    return _request("PUT", path, json=json).json()


def gdelete(path):
    _request("DELETE", path)
    return {}


def gmail_permalink(thread_id):
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"


def _is_safe_public_url(url):
    """SSRF guard for sender-supplied URLs (one-click unsubscribe). Requires https
    and a hostname that resolves only to public, non-loopback addresses."""
    try:
        p = urlparse(url)
        if p.scheme != "https" or not p.hostname:
            return False
        # Resolve every A/AAAA record and reject if any is private/loopback/link-local.
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
        for *_, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return bool(infos)
    except Exception:
        return False


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


# Phrases that mark an opt-out link, matched against the link text AND the words right
# around it — so we catch both a plain "Unsubscribe" link and "Click here if you do not
# wish to receive emails from us" where only "Click here" is the anchor.
_UNSUB_RE = re.compile(
    r"unsubscrib|opt[\s\-]?out|wish\s+to\s+receive|no\s+longer\s+(?:wish|want)|"
    r"stop\s+receiving|stop\s+these\s+e-?mails|cancel\s+(?:your\s+)?subscription|"
    r"manage\s+(?:your\s+)?(?:e-?mail\s+)?(?:preferences|subscriptions?)|"
    r"e-?mail\s+preferences|manage\s+subscriptions?|remove\s+(?:me|from\s+(?:this\s+)?list)",
    re.I)


def find_body_unsubscribe(html):
    """Last-resort unsubscribe link for senders that omit the List-Unsubscribe header but
    bury the opt-out in the body. Scans each <a>: prefers a link whose own text says
    unsubscribe; otherwise falls back to one whose surrounding sentence does (the
    'Click here if you do not wish to receive…' pattern). Returns the same shape as
    parse_unsubscribe (always method 'link' — we never auto-POST a body link), or None."""
    if not html:
        return None
    def strip(s):
        return _htmlmod.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    fallback = None
    for m in re.finditer(r'<a\b[^>]*?href=(["\'])(.*?)\1[^>]*?>(.*?)</a>', html, re.I | re.S):
        href = _htmlmod.unescape(m.group(2)).strip()
        if not href.lower().startswith("http"):
            continue
        inner = strip(m.group(3))
        if _UNSUB_RE.search(inner):
            return {"available": True, "method": "link", "httpsUrl": href,
                    "mailto": None, "mailtoSubject": "unsubscribe", "source": "body"}
        # Context window on either side of the anchor catches the phrasing-around-a-bare-link case.
        ctx = strip(html[max(0, m.start() - 200):m.start()]) + " " + inner + " " + strip(html[m.end():m.end() + 200])
        if fallback is None and _UNSUB_RE.search(ctx):
            fallback = {"available": True, "method": "link", "httpsUrl": href,
                        "mailto": None, "mailtoSubject": "unsubscribe", "source": "body"}
    return fallback


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


def drive_link_from_html(html_body, filename=None):
    """Pull the Google Docs/Drive URL for an attached file out of the message HTML.

    A Drive file rides in the body as a chip whose <a href> points at the real
    document; the MIME attachment part carries no URL, so the body is the source.
    With several attachments, prefer the anchor whose visible text matches the
    filename; otherwise return the first Drive link found."""
    if not html_body:
        return None
    drive = []
    for _q, href, inner in re.findall(r'<a\b[^>]*?href=(["\'])(.*?)\1[^>]*?>(.*?)</a>',
                                      html_body, re.I | re.S):
        href = _htmlmod.unescape(href)
        if re.match(r'https://(?:docs|drive)\.google\.com/', href):
            text = _htmlmod.unescape(re.sub(r'<[^>]+>', '', inner)).strip()
            drive.append((href, text))
    if not drive:
        bare = re.findall(r'https://(?:docs|drive)\.google\.com/[^\s"\'<>\\]+', html_body)
        return _htmlmod.unescape(bare[0]) if bare else None
    if filename:
        stem = filename.rsplit('.', 1)[0].strip().lower()
        if stem:
            for href, text in drive:
                if stem in text.lower() or stem in href.lower():
                    return href
    return drive[0][0]


_DOC_KIND_BY_PATH = [
    ("/spreadsheets/", "sheet"), ("/presentation/", "slides"),
    ("/forms/", "form"), ("/document/", "doc"),
    ("/drive/folders/", "folder"), ("drive.google.com/file/", "file"),
]
_DOC_GENERIC_TITLE = {"doc": "Google Doc", "sheet": "Google Sheet", "slides": "Google Slides",
                      "form": "Google Form", "folder": "Drive folder", "file": "Drive file"}


def extract_doc_links(html_body):
    """Pull shared Google Docs/Drive links out of an email body so the reader can
    surface them as real cards (Gmail-style chips are client-side, so the raw email
    only carries plain <a> links). Deduped by URL; utility links are skipped."""
    if not html_body:
        return []
    out, seen = [], set()
    for _q, href, inner in re.findall(r'<a\b[^>]*?href=(["\'])(.*?)\1[^>]*?>(.*?)</a>',
                                      html_body, re.I | re.S):
        href = _htmlmod.unescape(href)
        if not re.match(r'https://(?:docs|drive)\.google\.com/', href):
            continue
        if "/drive/blockuser" in href or "accounts.google.com" in href:
            continue
        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        kind = next((k for frag, k in _DOC_KIND_BY_PATH if frag in href), "file")
        text = _htmlmod.unescape(re.sub(r'<[^>]+>', '', inner)).strip()
        if not text or text.lower().startswith("http"):
            text = _DOC_GENERIC_TITLE.get(kind, "Google Drive")
        out.append({"url": href, "title": text[:120], "kind": kind})
    return out


def _is_inline_part(part):
    inline, has_cid = False, False
    for h in part.get("headers", []) or []:
        n = h.get("name", "").lower()
        if n == "content-disposition":
            inline = h.get("value", "").lower().startswith("inline")
        elif n == "content-id":
            has_cid = True
    return inline or has_cid


# Inline images smaller than this are treated as decoration (logos/signatures/spacers).
_INLINE_IMG_MAX = 30000


def _collect_attachments(payload, hide_inline_images=False):
    """Walk the MIME tree for real attachments. Hides embedded decoration (small inline
    images, or any inline image in bulk/promo mail) but keeps photos and documents."""
    out = []
    seen = set()

    def walk(part):
        fn = part.get("filename") or ""
        body = part.get("body", {})
        if fn and body.get("attachmentId"):
            mime = part.get("mimeType", "application/octet-stream")
            inline_img = mime.startswith("image/") and _is_inline_part(part)
            decoration = inline_img and (hide_inline_images or (body.get("size") or 0) < _INLINE_IMG_MAX)
            if not decoration and fn not in seen:
                seen.add(fn)
                out.append({
                    "filename": fn,
                    "mimeType": mime,
                    "size": body.get("size", 0),
                    "attachmentId": body["attachmentId"],
                    "drive": mime.startswith("application/vnd.google-apps"),
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
    unsub = None
    bulk = False
    alias = None
    if outgoing:
        name, addr = _parse_addr(_header(headers, "To"))
        sender = "To: " + (name or "(no recipient)")
        bundle = None
    else:
        name, addr = _parse_addr(_header(headers, "From"))
        sender = name
        bundle = _bundle_for(labels, subject, name, snippet)
        unsub = parse_unsubscribe(headers)
        alias = _detect_alias(headers)
        # Bulk/promo mail: treat inline images as decoration (hide them as attachments)
        bulk = bool(unsub) or any(l in CATEGORY_BUNDLES for l in labels)
    atts = [dict(a, messageId=msg.get("id"))
            for a in _collect_attachments(msg.get("payload", {}), hide_inline_images=bulk)]
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
        "important": "IMPORTANT" in labels,
        "bundle": bundle,
        "highlights": [] if outgoing else compute_highlights(subject, snippet, bundle),
        "unsub": unsub,
        "alias": alias,
        "attachments": atts,
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
    with _bundle_lock:
        _bundle_label_by_id.clear()
        for l in labels:
            if l["name"].startswith(BUNDLE_LABEL_PREFIX):
                _bundle_label_by_id[l["id"]] = l["name"][len(BUNDLE_LABEL_PREFIX):]
        _bundle_labels_loaded = True


def ensure_bundle_label(bundle):
    lid = ensure_label(BUNDLE_LABEL_PREFIX + bundle)
    with _bundle_lock:
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
    if not outgoing:
        apply_body_unsub(summaries)  # body-buried opt-out from cache; scan misses in background
    summaries.sort(key=lambda r: int(r["ts"]), reverse=True)
    return summaries


def fetch_inbox(max_results=60):
    return summarize_ids(list_ids(label_ids=["INBOX"], max_results=max_results))


def fetch_thread(thread_id):
    thread = gget(f"/threads/{thread_id}", format="full")
    messages = []
    last_rfc_id = ""
    all_rfc_ids = []
    subject = ""
    unsub = None
    unsub_mid = None
    muted = False
    alias = None  # the addy alias this thread arrived to, if any (for reply-via-alias)
    draft_msg = None  # (message_id, html) of an unsent reply draft living in this thread
    for m in thread.get("messages", []):
        # An autosaved reply rides in the thread as a DRAFT message — don't render it as
        # a sent bubble; capture it so the reader can restore it into the reply box.
        if "DRAFT" in m.get("labelIds", []):
            _, dhtml = _decode_body(m.get("payload", {}))
            draft_msg = (m.get("id"), dhtml)
            continue
        headers = m.get("payload", {}).get("headers", [])
        name, email_addr = _parse_addr(_header(headers, "From"))
        subject = subject or _header(headers, "Subject")
        text, html = _decode_body(m.get("payload", {}))
        rfc_id = _header(headers, "Message-ID")
        if rfc_id:
            last_rfc_id = rfc_id
            all_rfc_ids.append(rfc_id)
        if alias is None:
            alias = _detect_alias(headers)
        u = parse_unsubscribe(headers)
        if u:
            unsub, unsub_mid = u, m.get("id")  # prefer the most recent message's list-unsubscribe
        messages.append({
            "id": m.get("id"),
            "sender": name,
            "senderEmail": email_addr,
            "to": _header(headers, "To"),
            "cc": _header(headers, "Cc"),   # needed for reply-all
            "date": _fmt_time(_header(headers, "Date")),
            "text": text,
            "html": html,
            "attachments": _collect_attachments(m.get("payload", {})),
            "docLinks": extract_doc_links(html),
            "unread": "UNREAD" in m.get("labelIds", []),
        })
    # No List-Unsubscribe header? Fall back to an opt-out link buried in the body, newest
    # message first — so senders that hide unsubscribe in the body still get the banner.
    if not unsub:
        for m in reversed(messages):
            bu = find_body_unsubscribe(m.get("html"))
            if bu:
                unsub, unsub_mid = bu, m.get("id")
                break
    reply_draft = None
    if draft_msg:
        try:
            for dr in gget("/drafts", maxResults=50).get("drafts", []):
                if dr.get("message", {}).get("id") == draft_msg[0]:
                    reply_draft = {"draftId": dr["id"], "html": draft_msg[1]}
                    break
        except Exception:
            pass
    # Pinned reflects the newest NON-draft message (drafts don't carry STARRED state).
    real = [m for m in thread.get("messages", []) if "DRAFT" not in m.get("labelIds", [])]
    return {
        "id": thread_id,
        "subject": subject or "(no subject)",
        "messages": messages,
        "rfcMessageId": last_rfc_id,
        "allRfcIds": all_rfc_ids,           # full ancestry for the References header
        "permalink": gmail_permalink(thread_id),
        "pinned": "STARRED" in ((real[-1] if real else {}).get("labelIds", [])),
        "muted": muted,
        "unsub": unsub,
        "unsubMessageId": unsub_mid,
        "alias": alias,
        "replyDraft": reply_draft,
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


def _build_raw_message(to, subject, body, cc=None, bcc=None, in_reply_to=None,
                       references=None, body_html=None, attachments=None):
    """Build an RFC 2822 message and return its base64url-encoded form.
    Shared by the immediate-send path and the Deferred Action Engine (send-later /
    undo-send), so message content is captured at the moment the user hit Send."""
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc            # Gmail strips Bcc from the delivered copy
    msg["Subject"] = subject
    if _user_email:
        msg["From"] = _user_email
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        # RFC 2822 wants the full ancestry; fall back to just the parent id.
        msg["References"] = references or in_reply_to
    msg.set_content(body or "")          # plain-text part (fallback)
    if body_html:
        msg.add_alternative(body_html, subtype="html")  # -> multipart/alternative
    for fname, mime, data in (attachments or []):
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                           filename=fname or "attachment")  # -> multipart/mixed
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _send_raw(raw, thread_id=None):
    send_body = {"raw": raw}
    if thread_id:
        send_body["threadId"] = thread_id
    return gpost("/messages/send", json=send_body)


def send_message(to, subject, body, thread_id=None, in_reply_to=None,
                 references=None, cc=None, bcc=None, body_html=None, attachments=None):
    raw = _build_raw_message(to, subject, body, cc=cc, bcc=bcc, in_reply_to=in_reply_to,
                             references=references, body_html=body_html, attachments=attachments)
    return _send_raw(raw, thread_id=thread_id)


# ----------------------------------------------------------------------------
# Persistence: snooze.db (WAL mode). Holds the Deferred Action Engine queue and
# the Settings Store. All writes are serialized through _DAE_LOCK; WAL lets reads
# run concurrently with the single writer.
# ----------------------------------------------------------------------------
_DAE_LOCK = threading.Lock()
DAE_TICK = 5            # seconds between scheduler ticks (catches the undo-send window)
_dae_wake = threading.Event()   # set to make the scheduler tick immediately


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate_db():
    """Create the DAE + settings tables and migrate the legacy `snoozed` table.
    Idempotent — safe to run on every startup and safe to re-run after a crash."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS deferred_actions (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL,
        thread_id TEXT, message_id TEXT,
        payload TEXT NOT NULL DEFAULT '{}',
        fire_at INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_da_fire ON deferred_actions (fire_at) WHERE status='pending'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_da_thread ON deferred_actions (thread_id) WHERE status='pending'")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    # Cache of the body-buried unsubscribe scan (per message): https_url='' means scanned,
    # no opt-out link found. Lets inbox list rows show the Unsubscribe banner without
    # re-downloading each body — the scan runs once, on arrival or first sight.
    conn.execute("""CREATE TABLE IF NOT EXISTS unsub_scan (
        message_id TEXT PRIMARY KEY,
        https_url  TEXT NOT NULL DEFAULT '',
        checked_at INTEGER NOT NULL
    )""")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "snoozed" in tables:
        now = int(time.time())
        for tid, wake_at in conn.execute("SELECT thread_id, wake_at FROM snoozed").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO deferred_actions "
                "(id, kind, thread_id, payload, fire_at, status, created_at, attempts) "
                "VALUES (?,?,?,?,?,?,?,0)",
                (secrets.token_hex(16), "snooze_wake", tid, "{}", int(wake_at),
                 "pending" if wake_at > now else "fired", now))
        conn.execute("DROP TABLE snoozed")
        log.info("migrated %s legacy snooze rows into deferred_actions", "all")
    conn.commit()
    conn.close()


# ---------- Settings Store ----------
SETTINGS_DEFAULTS = {
    "signature": "",
    "undo_send_window": 10,        # seconds; 0 disables undo-send
    "dark_mode": "auto",           # auto | light | dark
    "default_snooze": "later_today",
    "snippets": [],                # [{id, title, html}]
    "image_block": False,          # show remote images by default (toggle in Settings to block)
    "page_size": 50,
    "followup_default_days": 3,
}


def _get_setting(key, default=None):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row is None:
        return SETTINGS_DEFAULTS.get(key, default)
    try:
        return json.loads(row[0])
    except Exception:
        return row[0]


def _set_setting(key, value):
    with _DAE_LOCK:
        conn = db()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                     (key, json.dumps(value)))
        conn.commit()
        conn.close()


# ---------- Deferred Action Engine ----------
def _dae_enqueue(kind, fire_at, thread_id=None, message_id=None, payload=None):
    action_id = secrets.token_hex(16)
    with _DAE_LOCK:
        conn = db()
        conn.execute(
            "INSERT INTO deferred_actions "
            "(id, kind, thread_id, message_id, payload, fire_at, status, created_at, attempts) "
            "VALUES (?,?,?,?,?,?,?,?,0)",
            (action_id, kind, thread_id, message_id, json.dumps(payload or {}),
             int(fire_at), "pending", int(time.time())))
        conn.commit()
        conn.close()
    _dae_wake.set()
    return action_id


def _dae_cancel(action_id):
    with _DAE_LOCK:
        conn = db()
        n = conn.execute("UPDATE deferred_actions SET status='cancelled' "
                         "WHERE id=? AND status='pending'", (action_id,)).rowcount
        conn.commit()
        conn.close()
    return bool(n)


def _dae_cancel_kind_for_thread(kind, thread_id):
    """Cancel any pending action of `kind` for a thread (e.g. re-snooze supersedes)."""
    with _DAE_LOCK:
        conn = db()
        conn.execute("UPDATE deferred_actions SET status='cancelled' "
                     "WHERE kind=? AND thread_id=? AND status='pending'", (kind, thread_id))
        conn.commit()
        conn.close()


def _mark_action(action_id, status):
    with _DAE_LOCK:
        conn = db()
        conn.execute("UPDATE deferred_actions SET status=? WHERE id=?", (status, action_id))
        conn.commit()
        conn.close()


def _retry_or_fail(action_id, new_attempts, new_fire_at, max_attempts=5):
    with _DAE_LOCK:
        conn = db()
        if new_attempts >= max_attempts:
            conn.execute("UPDATE deferred_actions SET status='failed', attempts=? WHERE id=?",
                         (new_attempts, action_id))
        else:
            conn.execute("UPDATE deferred_actions SET attempts=?, fire_at=? WHERE id=?",
                         (new_attempts, new_fire_at, action_id))
        conn.commit()
        conn.close()


def snooze_thread(thread_id, wake_epoch):
    """Snooze a thread: archive it under the Snoozed label and queue a wake."""
    sid = ensure_label("Snoozed")
    modify_thread(thread_id, add=[sid], remove=["INBOX"])
    _dae_cancel_kind_for_thread("snooze_wake", thread_id)  # re-snooze supersedes
    _dae_enqueue("snooze_wake", wake_epoch, thread_id=thread_id)


def _handle_snooze_wake(thread_id, message_id, payload):
    sid = ensure_label("Snoozed")
    modify_thread(thread_id, add=["INBOX"], remove=[sid])
    SYNC["version"] += 1


def _handle_send(thread_id, message_id, payload):
    """Used for both send_scheduled and undo_send_commit — raw built at enqueue."""
    _send_raw(payload["raw_b64"], thread_id=thread_id)
    SYNC["version"] += 1


def _handle_followup_nudge(thread_id, message_id, payload):
    """Re-surface a sent thread to the inbox if no reply has arrived."""
    thread = gget(f"/threads/{thread_id}", format="metadata", metadataHeaders=["From"])
    sent_at = payload.get("sent_at_epoch", 0)
    has_reply = any(
        int(m.get("internalDate", 0)) / 1000 > sent_at
        and (_user_email or "") not in (_header(m.get("payload", {}).get("headers", []), "From") or "")
        for m in thread.get("messages", [])
    )
    if not has_reply:
        modify_thread(thread_id, add=["INBOX"])
        SYNC["version"] += 1


_DAE_HANDLERS = {
    "snooze_wake": _handle_snooze_wake,
    "send_scheduled": _handle_send,
    "undo_send_commit": _handle_send,
    "followup_nudge": _handle_followup_nudge,
}


def _dae_tick():
    now = int(time.time())
    with _DAE_LOCK:
        conn = db()
        due = conn.execute(
            "SELECT id, kind, thread_id, message_id, payload, attempts "
            "FROM deferred_actions WHERE status='pending' AND fire_at <= ? "
            "ORDER BY fire_at LIMIT 20", (now,)).fetchall()
        conn.close()
    for action_id, kind, thread_id, message_id, payload_json, attempts in due:
        handler = _DAE_HANDLERS.get(kind)
        if not handler:
            log.warning("DAE: unknown kind %r (action %s)", kind, action_id)
            _mark_action(action_id, "failed")
            continue
        try:
            handler(thread_id, message_id, json.loads(payload_json or "{}"))
            _mark_action(action_id, "fired")
        except Exception as exc:
            backoff = min(3600, 60 * (2 ** attempts))
            log.exception("DAE: %s %s failed (attempt %d): %s", kind, action_id, attempts + 1, exc)
            _retry_or_fail(action_id, attempts + 1, now + backoff)


def _scheduler_loop():
    """The Deferred Action Engine: poll due actions every DAE_TICK seconds."""
    while True:
        try:
            _dae_tick()
        except Exception:
            log.exception("DAE: tick crashed")
        _dae_wake.wait(timeout=DAE_TICK)
        _dae_wake.clear()


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
                        # Scan newly-arrived inbox mail for body-buried unsubscribe links now,
                        # so the banner is ready in the list row by the time it's viewed.
                        added = [ma["message"]["id"]
                                 for h in resp["history"]
                                 for ma in h.get("messagesAdded", [])
                                 if "INBOX" in (ma.get("message", {}).get("labelIds") or [])]
                        if added:
                            enqueue_unsub_scan(added)
                    SYNC["history_id"] = resp.get("historyId", SYNC["history_id"])
                except GmailHTTPError as he:
                    # 404 = startHistoryId too old; reset cursor and force a refresh
                    if he.status == 404:
                        SYNC["history_id"] = gget("/profile").get("historyId")
                        SYNC["version"] += 1
                    else:
                        raise
        except Exception:
            log.exception("history poll failed")
        time.sleep(POLL_INTERVAL)


# ----------------------------------------------------------------------------
# Body-unsubscribe scan cache
# Senders that omit the List-Unsubscribe header (KaraFun et al.) bury the opt-out in
# the body. List rows are fetched WITHOUT body bytes (cheap), so we can't scan them
# inline. Instead we scan each message's body once — on arrival (history poller) or the
# first time a list row is built — on a background worker, and cache the result so the
# Unsubscribe banner can show in the inbox list without re-downloading bodies.
# ----------------------------------------------------------------------------
# Body-only field mask: text parts down the same nesting depth as the list mask, no headers.
_BODY_FIELDS = ("payload(mimeType,body/data,parts(mimeType,body/data,"
                "parts(mimeType,body/data,parts(mimeType,body/data))))")
_unsub_q = queue.Queue()
_unsub_queued = set()           # ids already queued this run, so we don't re-enqueue dupes
_unsub_queued_lock = threading.Lock()


def unsub_cache_get(ids):
    """Return {message_id: https_url} for the scanned ids (url '' = none found)."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    out, conn = {}, db()
    try:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for mid, url in conn.execute(
                    f"SELECT message_id, https_url FROM unsub_scan WHERE message_id IN ({ph})",
                    chunk).fetchall():
                out[mid] = url
    finally:
        conn.close()
    return out


def unsub_cache_put(mid, url):
    conn = db()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO unsub_scan (message_id, https_url, checked_at) "
                         "VALUES (?,?,?)", (mid, url or "", int(time.time())))
    finally:
        conn.close()


def enqueue_unsub_scan(ids):
    """Queue message ids for a one-time body scan (skips ids already queued this run)."""
    with _unsub_queued_lock:
        fresh = [i for i in ids if i and i not in _unsub_queued]
        _unsub_queued.update(fresh)
    for i in fresh:
        _unsub_q.put(i)


def _unsub_scan_loop():
    found_pending = False
    while True:
        mid = _unsub_q.get()
        try:
            # Skip if a prior run already cached it (queue can outlive the cache check).
            if mid in unsub_cache_get([mid]):
                continue
            full = gget(f"/messages/{mid}", format="full", fields=_BODY_FIELDS)
            _t, html = _decode_body(full.get("payload", {}))
            info = find_body_unsubscribe(html)
            unsub_cache_put(mid, info["httpsUrl"] if info else "")
            if info:
                found_pending = True
        except Exception:
            log.exception("unsub body scan failed for %s", mid)
        finally:
            _unsub_q.task_done()
        # Coalesce UI refreshes: nudge the browser once a backfill batch drains, not per hit.
        if found_pending and _unsub_q.empty():
            SYNC["version"] += 1
            found_pending = False
        time.sleep(0.05)  # gentle on the Gmail per-second quota


def apply_body_unsub(rows):
    """For list rows with no header-based unsubscribe, fill in a body-buried opt-out from
    the cache, and enqueue a background scan for any not yet cached (shows next refresh)."""
    need = [r for r in rows if not r.get("unsub") and r.get("messageId")]
    if not need:
        return
    cached = unsub_cache_get([r["messageId"] for r in need])
    missing = []
    for r in need:
        mid = r["messageId"]
        if mid in cached:
            url = cached[mid]
            if url:
                r["unsub"] = {"available": True, "method": "link", "httpsUrl": url,
                              "mailto": None, "mailtoSubject": "unsubscribe", "source": "body"}
        else:
            missing.append(mid)
    if missing:
        enqueue_unsub_scan(missing)


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
    """Authoritative inbox counts straight from Gmail's INBOX label (no message fetch).

    Use the THREAD totals, not message totals: the list renders one row per
    conversation, so a thread with replies must count once. messagesTotal counts
    every message and inflates the sidebar past the number of visible rows."""
    info = gget("/labels/INBOX")
    return info.get("threadsTotal"), info.get("threadsUnread")


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
    # Smart-sort: float Gmail-flagged IMPORTANT + unread mail to the top, preserving
    # recency order within each group. A non-AI "priority inbox" honoring local/calm.
    primary.sort(key=lambda r: 0 if (r.get("important") and r.get("unread")) else 1)
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
        waked = dict(conn.execute(
            "SELECT thread_id, fire_at FROM deferred_actions "
            "WHERE kind='snooze_wake' AND status='pending'").fetchall())
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


@app.route("/api/save_draft", methods=["POST"])
def api_save_draft():
    """Create or update a Gmail draft. Called repeatedly by the compose autosave, so
    after the first save it reuses the returned draftId to update in place (no dupes)."""
    d = request.json or {}
    to, subject = d.get("to", ""), d.get("subject", "")
    body_text, body_html = d.get("body", ""), d.get("html")
    cc, bcc = d.get("cc"), d.get("bcc")
    thread_id = d.get("threadId") or None
    in_reply_to, references = d.get("inReplyTo") or None, d.get("references") or None
    draft_id = d.get("draftId") or None
    try:
        raw = _build_raw_message(to, subject, body_text, cc=cc, bcc=bcc,
                                 in_reply_to=in_reply_to, references=references,
                                 body_html=body_html)
        message = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        body = {"message": message}
        res = gput(f"/drafts/{draft_id}", json=body) if draft_id else gpost("/drafts", json=body)
        return jsonify({"ok": True, "draftId": res.get("id")})
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


def _bulk_modify(ids, add=None, remove=None):
    """Apply the same label change to many threads concurrently (one HTTP round trip
    from the client, parallel Gmail calls server-side). Used by category sweep."""
    def _one(tid):
        try:
            modify_thread(tid, add=add, remove=remove)
            return True
        except Exception:
            log.exception("bulk modify failed for %s", tid)
            return False
    if not ids:
        return 0, 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_one, ids))
    return sum(results), results.count(False)


@app.route("/api/bulk_done", methods=["POST"])
def api_bulk_done():
    ids = (request.json or {}).get("threadIds") or []
    done, failed = _bulk_modify(ids, remove=["INBOX"])
    return jsonify({"ok": True, "done": done, "failed": failed})


@app.route("/api/bulk_undo_done", methods=["POST"])
def api_bulk_undo_done():
    ids = (request.json or {}).get("threadIds") or []
    _bulk_modify(ids, add=["INBOX"])
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


@app.route("/api/unsnooze", methods=["POST"])
def api_unsnooze():
    tid = request.json["threadId"]
    _dae_cancel_kind_for_thread("snooze_wake", tid)
    sid = ensure_label("Snoozed")
    modify_thread(tid, add=["INBOX"], remove=[sid])
    return jsonify({"ok": True})


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


def _addy_encode_to(addr_str, alias):
    """Rewrite a comma-separated recipient list into addy's send-from-alias form so
    the outbound mail goes through addy and the contact only ever sees the alias:
    `aliasLocal+contactLocal=contactDomain@aliasDomain`. The message is still sent
    from the verified Gmail recipient, which is what addy requires."""
    if not addr_str or not alias or "@" not in alias:
        return addr_str
    a_local, a_domain = alias.rsplit("@", 1)
    out = []
    for part in addr_str.split(","):
        _, email_addr = _parse_addr(part.strip())
        if not email_addr or "@" not in email_addr:
            continue
        r_local, r_domain = email_addr.rsplit("@", 1)
        out.append("%s+%s=%s@%s" % (a_local, r_local, r_domain, a_domain))
    return ", ".join(out)


@app.route("/api/send", methods=["POST"])
def api_send():
    ctype = request.content_type or ""
    if ctype.startswith("multipart/form-data"):
        f = request.form
        to, subject = f.get("to", ""), f.get("subject", "")
        cc, bcc = f.get("cc") or None, f.get("bcc") or None
        body_text, body_html = f.get("text", ""), (f.get("html") or None)
        thread_id, in_reply_to = f.get("threadId") or None, f.get("inReplyTo") or None
        references = f.get("references") or None
        draft_id = f.get("draftId") or None
        send_at = f.get("sendAt") or None
        via_alias = f.get("viaAlias") or None
        attachments = [(fl.filename, fl.mimetype, fl.read()) for fl in request.files.getlist("attachments")]
    else:
        d = request.json or {}
        to, subject = d.get("to", ""), d.get("subject", "")
        cc, bcc = d.get("cc"), d.get("bcc")
        body_text, body_html = d.get("body", ""), d.get("html")
        thread_id, in_reply_to = d.get("threadId"), d.get("inReplyTo")
        references = d.get("references")
        draft_id, attachments = d.get("draftId"), []
        send_at = d.get("sendAt")
        via_alias = d.get("viaAlias")
    # Send through an alias: encode every recipient so addy relays it as the alias
    # and the contact never sees the real Gmail address.
    if via_alias:
        to = _addy_encode_to(to, via_alias)
        if cc:
            cc = _addy_encode_to(cc, via_alias)
        if bcc:
            bcc = _addy_encode_to(bcc, via_alias)
    try:
        raw = _build_raw_message(to, subject, body_text, cc=cc, bcc=bcc,
                                 in_reply_to=in_reply_to, references=references,
                                 body_html=body_html, attachments=attachments)
        payload = {"raw_b64": raw, "to": to, "subject": subject}
        now = int(time.time())

        def _drop_draft():
            if draft_id:
                try:
                    gdelete(f"/drafts/{draft_id}")
                except Exception:
                    log.warning("could not delete draft %s", draft_id)

        # 1. Scheduled send (explicit future time)
        try:
            send_at = int(send_at) if send_at else 0
        except (TypeError, ValueError):
            send_at = 0
        if send_at and send_at > now + 5:
            aid = _dae_enqueue("send_scheduled", send_at, thread_id=thread_id, payload=payload)
            _drop_draft()
            return jsonify({"ok": True, "scheduled": True, "actionId": aid, "sendAt": send_at})

        # 2. Undo-send window (hold N seconds, then commit unless cancelled)
        undo_window = int(_get_setting("undo_send_window", 10) or 0)
        if undo_window > 0:
            aid = _dae_enqueue("undo_send_commit", now + undo_window,
                               thread_id=thread_id, payload=payload)
            _drop_draft()
            return jsonify({"ok": True, "actionId": aid, "undoWindow": undo_window})

        # 3. Immediate send
        _send_raw(raw, thread_id=thread_id)
        _drop_draft()
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("send failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/cancel_send", methods=["POST"])
def api_cancel_send():
    action_id = (request.json or {}).get("actionId")
    if not action_id:
        return jsonify({"error": "missing actionId"}), 400
    cancelled = _dae_cancel(action_id)
    return jsonify({"ok": True, "cancelled": cancelled})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    conn = db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    out = dict(SETTINGS_DEFAULTS)
    for k, v in rows:
        try:
            out[k] = json.loads(v)
        except Exception:
            out[k] = v
    return jsonify(out)


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    for key, value in (request.json or {}).items():
        if key in SETTINGS_DEFAULTS:
            _set_setting(key, value)
    return jsonify({"ok": True})


# Contact autocomplete — mined from sent-mail headers (covered by gmail.modify; no
# extra scope). Cached so typing doesn't refetch on every keystroke.
_contacts_cache = {"at": 0, "list": []}
_contacts_lock = threading.Lock()


def _rebuild_contacts():
    ids = list_ids(label_ids=["SENT"], max_results=200)
    seen, contacts = set(), []
    with ThreadPoolExecutor(max_workers=8) as ex:
        msgs = list(ex.map(_get_meta, ids))
    for m in msgs:
        if not m:
            continue
        headers = m.get("payload", {}).get("headers", [])
        for field in ("To", "Cc"):
            for addr in (_header(headers, field) or "").split(","):
                name, email_addr = _parse_addr(addr)
                key = email_addr.lower()
                if "@" in key and key not in seen:
                    seen.add(key)
                    contacts.append({"name": name, "email": email_addr})
    return contacts


@app.route("/api/contacts")
def api_contacts():
    q = (request.args.get("q") or "").strip().lower()
    now = int(time.time())
    with _contacts_lock:
        if now - _contacts_cache["at"] > 900 or not _contacts_cache["list"]:
            try:
                _contacts_cache["list"] = _rebuild_contacts()
                _contacts_cache["at"] = now
            except Exception:
                log.exception("contact rebuild failed")
        contacts = _contacts_cache["list"]
    if q:
        contacts = [c for c in contacts
                    if q in c["email"].lower() or q in (c["name"] or "").lower()]
    return jsonify({"contacts": contacts[:8]})


@app.route("/api/mute", methods=["POST"])
def api_mute():
    d = request.json or {}
    tid = d.get("threadId")
    if not tid:
        return jsonify({"error": "missing threadId"}), 400
    try:
        muted_id = ensure_label("Muted")
        if d.get("muted", True):
            modify_thread(tid, add=[muted_id], remove=["INBOX"])
        else:
            modify_thread(tid, remove=[muted_id])
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("mute failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/mark", methods=["POST"])
def api_mark():
    d = request.json or {}
    tid = d.get("threadId")
    if not tid:
        return jsonify({"error": "missing threadId"}), 400
    try:
        if d.get("read", True):
            modify_thread(tid, remove=["UNREAD"])
        else:
            modify_thread(tid, add=["UNREAD"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/followup", methods=["POST"])
def api_followup():
    """Queue a 'remind me if no reply' nudge on a sent thread."""
    d = request.json or {}
    tid = d.get("threadId")
    if not tid:
        return jsonify({"error": "missing threadId"}), 400
    days = int(d.get("days") or _get_setting("followup_default_days", 3))
    # Dedup: one pending nudge per thread
    conn = db()
    existing = conn.execute(
        "SELECT id FROM deferred_actions WHERE kind='followup_nudge' "
        "AND thread_id=? AND status='pending'", (tid,)).fetchone()
    conn.close()
    if existing:
        return jsonify({"ok": True, "actionId": existing[0], "deduped": True})
    now = int(time.time())
    aid = _dae_enqueue("followup_nudge", now + days * 86400, thread_id=tid,
                       message_id=d.get("messageId"),
                       payload={"sent_at_epoch": now})
    return jsonify({"ok": True, "actionId": aid, "days": days})


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
            # No List-Unsubscribe header — fall back to an opt-out link buried in the body.
            full = gget(f"/messages/{mid}", format="full")
            _t, _h = _decode_body(full.get("payload", {}))
            info = find_body_unsubscribe(_h)
            if not info:
                return jsonify({"error": "no unsubscribe link found"}), 400

        if info["method"] == "one-click":
            if not _is_safe_public_url(info["httpsUrl"]):
                # Refuse to POST to a private/loopback target; let the user finish in-browser.
                return jsonify({"ok": False, "method": "one-click",
                                "fallbackUrl": info["httpsUrl"], "error": "unsafe URL"})
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


@app.route("/api/doc_links", methods=["POST"])
def api_doc_links():
    """Extract shared Google Docs/Drive links for a batch of messages so the inbox can
    show them as preview chips on the card. Called lazily after the list renders (the
    list fetch skips bodies for speed). fields=payload pulls the body tree only — no
    attachment bytes, no headers."""
    mids = (request.json or {}).get("messageIds") or []
    mids = [m for m in mids if m][:80]
    def one(mid):
        try:
            msg = gget(f"/messages/{mid}", format="full", fields="payload")
            _, html = _decode_body(msg.get("payload", {}))
            return mid, extract_doc_links(html)
        except Exception:
            return mid, []
    out = {}
    if mids:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for mid, links in ex.map(one, mids):
                out[mid] = links
    return jsonify(out)


@app.route("/api/drive_link")
def api_drive_link():
    """Resolve a Drive/Docs attachment chip to its real document URL on demand.

    The attachment metadata has no URL, so we fetch the message body and pull the
    chip's link from it. Falls back to opening the Gmail message (so the user still
    reaches the working chip) when no link can be found."""
    mid = request.args.get("messageId")
    if not mid:
        return jsonify({"error": "missing messageId"}), 400
    filename = request.args.get("filename")
    tid = request.args.get("threadId")
    url = None
    try:
        msg = gget(f"/messages/{mid}", format="full")
        _, html_body = _decode_body(msg.get("payload", {}))
        url = drive_link_from_html(html_body, filename)
    except Exception as e:
        logging.warning("drive_link resolve failed for %s: %s", mid, e)
    if not url:
        url = gmail_permalink(tid) if tid else "https://drive.google.com/"
    return jsonify({"url": url})


@app.route("/api/undo_things", methods=["POST"])
def api_undo_things():
    """Undo a 'Send to Things 3': trash the to-do(s) carrying this thread's backlink.

    The created task's notes always contain the unique inboxclone://thread/<id>
    backlink, so we match on that and delete via Things' AppleScript dictionary
    (delete moves the item to Things' Trash, a clean undo)."""
    tid = (request.json or {}).get("threadId", "")
    if not tid:
        return jsonify({"error": "missing threadId"}), 400
    token = f"inboxclone://thread/{tid}"
    script = (
        'tell application "Things3"\n'
        f'  set matches to to dos whose notes contains "{token}"\n'
        '  set n to count of matches\n'
        '  repeat with t in matches\n'
        '    delete t\n'
        '  end repeat\n'
        '  return n\n'
        'end tell'
    )
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return jsonify({"error": (out.stderr or "osascript failed").strip()}), 500
        return jsonify({"ok": True, "removed": (out.stdout or "").strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------- addy.io aliases (disposable email) ----------
# Key lives in the harness disposable-email/secrets.json (canonical home, gitignored)
# so the alias.py CLI and this app share one credential. Falls back to a local
# credentials/addy.json if the harness path isn't present.
ADDY_BASE = "https://app.addy.io/api/v1"
_ADDY_SECRET_PATHS = [
    os.path.expanduser("~/Documents/Exobrain harness/disposable-email/secrets.json"),
    os.path.join(CRED_DIR, "addy.json"),
]


def _addy_cfg():
    for p in _ADDY_SECRET_PATHS:
        if os.path.exists(p):
            try:
                c = json.load(open(p))
                if c.get("addy_api_key"):
                    return c
            except Exception:
                pass
    return None


def _addy_req(method, path, body=None):
    """Call the addy.io API. Returns (status_code, parsed_json_or_None)."""
    cfg = _addy_cfg()
    if not cfg:
        return 0, {"error": "addy not configured"}
    headers = {
        "Authorization": "Bearer " + cfg["addy_api_key"],
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    r = requests.request(method, ADDY_BASE + path, headers=headers,
                         json=body, timeout=20)
    data = None
    if r.text:
        try:
            data = r.json()
        except Exception:
            data = None
    return r.status_code, data


def _addy_alias_row(a):
    """Trim addy's verbose alias object to what the UI needs."""
    return {
        "id": a.get("id"),
        "email": a.get("email"),
        "active": a.get("active"),
        "description": a.get("description") or "",
        "forwarded": a.get("emails_forwarded", 0),
        "blocked": a.get("emails_blocked", 0),
        "replied": a.get("emails_replied", 0),
        "sent": a.get("emails_sent", 0),
        "created": (a.get("created_at") or "")[:10],
        "last_forwarded": a.get("last_forwarded"),
    }


# --- Tier 2: tag inbound mail with which alias it arrived to ---
_ADDY_ALIAS_CACHE = {"ts": 0.0, "map": {}}
_ADDY_CACHE_LOCK = threading.Lock()
_ADDY_CACHE_TTL = 60  # seconds; alias set changes rarely, refetch is cheap
_ADDR_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Headers addy/Gmail may carry the original alias recipient in, most specific first.
_ALIAS_HEADERS = ("X-AnonAddy-Original-Recipient", "X-Original-To", "Delivered-To",
                  "X-Forwarded-To", "To", "Cc")


def _alias_map():
    """email_lower -> {id, active, description}, cached briefly. Empty if addy off."""
    if not _addy_cfg():
        return {}
    now = time.time()
    with _ADDY_CACHE_LOCK:
        if now - _ADDY_ALIAS_CACHE["ts"] < _ADDY_CACHE_TTL and _ADDY_ALIAS_CACHE["map"]:
            return _ADDY_ALIAS_CACHE["map"]
    sc, data = _addy_req("GET", "/aliases")
    if sc != 200 or not data:
        return _ADDY_ALIAS_CACHE["map"]  # serve stale on transient error
    m = {}
    for a in data.get("data", []):
        if a.get("email"):
            m[a["email"].lower()] = {"id": a.get("id"), "active": a.get("active"),
                                     "description": a.get("description") or ""}
    with _ADDY_CACHE_LOCK:
        _ADDY_ALIAS_CACHE["ts"] = now
        _ADDY_ALIAS_CACHE["map"] = m
    return m


def _addy_cache_bust():
    with _ADDY_CACHE_LOCK:
        _ADDY_ALIAS_CACHE["ts"] = 0.0


def _detect_alias(headers):
    """If an inbound message arrived to one of the user's addy aliases, return
    {alias, id, active, description}; else None. Matches recipient headers against
    the known alias set, so it's domain-agnostic (custom shared domains work too)."""
    m = _alias_map()
    if not m:
        return None
    for hname in _ALIAS_HEADERS:
        val = _header(headers, hname)
        if not val:
            continue
        for addr in _ADDR_RE.findall(val):
            hit = m.get(addr.lower())
            if hit:
                return {"alias": addr.lower(), "id": hit["id"],
                        "active": hit["active"], "description": hit["description"]}
    return None


@app.route("/api/aliases")
def api_aliases():
    """List addy.io aliases + account quota (free plan caps active shared aliases)."""
    if not _addy_cfg():
        return jsonify({"configured": False})
    sc, data = _addy_req("GET", "/aliases")
    if sc != 200 or not data:
        return jsonify({"configured": True, "error": "list failed (%s)" % sc,
                        "aliases": []}), 200
    rows = [_addy_alias_row(a) for a in data.get("data", [])]
    rows.sort(key=lambda r: r["created"], reverse=True)
    acc_sc, acc = _addy_req("GET", "/account-details")
    quota = {}
    if acc_sc == 200 and acc:
        d = acc["data"]
        quota = {
            "active": d.get("active_shared_domain_alias_count", 0),
            "limit": d.get("active_shared_domain_alias_limit", 0),
            "domain": d.get("default_alias_domain", "anonaddy.me"),
            "recipient_count": d.get("recipient_count", 0),
            "bandwidth": d.get("bandwidth", 0),
            "bandwidth_limit": d.get("bandwidth_limit", 0),
            "plan": d.get("subscription", ""),
        }
    return jsonify({"configured": True, "aliases": rows, "quota": quota})


@app.route("/api/aliases", methods=["POST"])
def api_alias_create():
    """Mint a new alias. Body: {description}. Forwards to the addy default recipient."""
    cfg = _addy_cfg()
    if not cfg:
        return jsonify({"error": "addy not configured"}), 400
    desc = (request.json or {}).get("description", "").strip()
    body = {"domain": cfg.get("addy_domain", "anonaddy.me"), "description": desc}
    sc, data = _addy_req("POST", "/aliases", body)
    if sc not in (200, 201) or not data:
        msg = (data or {}).get("message") or "create failed (%s)" % sc
        return jsonify({"error": msg}), 400
    _addy_cache_bust()
    return jsonify({"ok": True, "alias": _addy_alias_row(data["data"])})


@app.route("/api/alias_toggle", methods=["POST"])
def api_alias_toggle():
    """Activate/deactivate an alias. Body: {id, active}. Deactivated aliases bounce mail
    and free a slot against the free-plan active cap."""
    b = request.json or {}
    aid, active = b.get("id"), b.get("active")
    if not aid:
        return jsonify({"error": "missing id"}), 400
    if active:
        sc, _ = _addy_req("POST", "/active-aliases", {"id": aid})
        ok = sc in (200, 201)
    else:
        sc, _ = _addy_req("DELETE", "/active-aliases/" + aid)
        ok = sc in (200, 204)
    if ok:
        _addy_cache_bust()
    return (jsonify({"ok": True}) if ok
            else (jsonify({"error": "toggle failed (%s)" % sc}), 400))


@app.route("/api/alias_delete", methods=["POST"])
def api_alias_delete():
    """Delete an alias. Body: {id, forget}. forget=true permanently removes it
    (frees the slot AND lets the local part be reused); otherwise a soft delete
    that bounces future mail but keeps stats."""
    b = request.json or {}
    aid = b.get("id")
    if not aid:
        return jsonify({"error": "missing id"}), 400
    if b.get("forget"):
        sc, _ = _addy_req("DELETE", "/aliases/" + aid + "/forget")
    else:
        sc, _ = _addy_req("DELETE", "/aliases/" + aid)
    if sc in (200, 204):
        _addy_cache_bust()
    return (jsonify({"ok": True}) if sc in (200, 204)
            else (jsonify({"error": "delete failed (%s)" % sc}), 400))


def start_background():
    """Migrate the DB, start the Deferred Action Engine, trigger auth, poll history."""
    migrate_db()
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    # Trigger auth before serving so the consent flow opens cleanly on first run
    try:
        get_service()
    except Exception as e:
        print(f"[auth] deferred: {e}")
    threading.Thread(target=_history_loop, daemon=True).start()
    threading.Thread(target=_unsub_scan_loop, daemon=True).start()


def run_server():
    start_background()
    # threaded=True is required: SSE connections are long-lived and must not block other requests
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    run_server()
