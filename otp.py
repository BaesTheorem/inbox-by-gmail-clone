"""One-time code detection for emailed 2FA codes.

Pure functions, no Gmail calls, so the same rules can be exercised by tests and
ported line for line to the iPhone app (ios/Sources/OTPDetect.swift). Keep the
two in sync when a rule changes.

The problem has two halves. First, is this message a code delivery at all? A
meeting invite carries a "Passcode:", a receipt carries a "Confirmation Code",
an FAQ mentions "your verification code" without containing one. Second, which
token is the code? Codes are 4 to 8 digits, or 5 to 8 upper-case letters and
digits, sitting next to the words that announce them. Everything else that
looks like a code (card last-fours, phone numbers, years, meeting passcodes,
promo codes, order numbers, URL fragments) is excluded by its surroundings.
"""

import html as _html
import re

# Words that announce a code. The subject or the first stretch of the body must
# contain one of these before any token is considered.
_INTENT = re.compile(
    r"(?:(?:verification|verify|one[- ]?time|login|log[- ]?in|sign[- ]?in|security|"
    r"authentication|activation|registration|confirmation|access|2fa|two[- ]factor|"
    r"sudo|temporary|pin)\s+(?:pass)?code)"
    r"|\bpasscode\b|\botp\b|\bone[- ]?time\s+pass(?:word|code)\b"
    r"|\byour\s+code\b|\bcode\s+(?:is|below)\b|\b(?:enter|use)\s+(?:the|this)\s+(?:following\s+)?code\b"
    r"|\bcode:\s*[A-Z0-9]",
    re.IGNORECASE,
)

# Subjects that are never code deliveries, whatever the body says.
_NEGATIVE_SUBJECT = re.compile(
    r"\b(?:invitation|invite|meeting|teleconference|interview|webinar|receipt|"
    r"invoice|order|purchase|shipped|tracking|unread messages|newsletter)\b",
    re.IGNORECASE,
)

# Candidate tokens. Digits 4 to 8 long, or upper-case alphanumerics 5 to 8 long.
# Must stand alone: whitespace, start/end, or light punctuation on both sides.
_TOKEN = re.compile(
    r"(?<![\w#$/=?&.+*-])"
    r"(?:(?P<digits>\d{4,8})|(?P<grouped>\d{3}[ -]\d{3})|(?P<alnum>[A-Z0-9]{5,8}))"
    r"(?![\w/=?&%-])(?!\.\w)"
)

# Words right before a token that mark it as something other than a code.
_BAD_BEFORE = re.compile(
    r"(?:card|ending|ends?\s+in|last|ext|order|ref(?:erence)?|invoice|zip|suite|apt|"
    r"account|acct|number|no\.?|id|member|ticket|case|confirmation\s+code|"
    r"reference\s+code|promo|coupon|discount|save|\$|usd|#|at|from|to|dial|call|"
    r"meeting\s+id|ip|version|v)\s*:?\s*$",
    re.IGNORECASE,
)

# Upper-case tokens with no digit are accepted only when they do not read as a
# word or acronym we expect in mail.
_COMMON_UPPER = {
    "HTTPS", "HTTP", "HTML", "EMAIL", "PHONE", "LOGIN", "ERROR", "THANKS", "HELLO",
    "PLEASE", "TEAMS", "ZOOM", "UTC", "GMT", "EST", "CST", "MST", "PST", "EDT",
    "CDT", "MDT", "PDT", "USA", "MYHR", "MYCHART", "ESPN", "DICE", "GITHUB",
    "AMAZON", "APPLE", "GOOGLE", "PAYPAL", "VENMO", "SONY", "JAGEX", "PLAUD",
    "STOP", "RESET", "VERIFY", "CLICK", "CODE", "ENTER", "TODAY", "NEVER",
}

_TAG_STRIP = re.compile(r"<(script|style|head)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[\s‌​͏﻿ ]+")

# Senders whose display name is just plumbing; fall back to the domain.
_NOISE_NAME = re.compile(r"^(?:no[ -]?reply|noreply|do-?not-?reply|notifications?|info|support|"
                         r"alerts?|mailer|team|hello|news)$", re.IGNORECASE)


def html_to_text(s):
    """Flatten HTML (or already-plain text) to one line of visible text."""
    if not s:
        return ""
    if "<" in s and ">" in s:
        s = _TAG_STRIP.sub(" ", s)
        s = re.sub(r"<br\s*/?>|</(?:p|div|td|tr|li|h\d)>", " ", s, flags=re.IGNORECASE)
        s = _TAGS.sub(" ", s)
    s = _html.unescape(s)
    return _WS.sub(" ", s).strip()


def registrable_domain(addr):
    """'no-reply@mydisney.espn.com' -> 'espn.com'. Two labels, three for the common
    country second-levels (co.uk, com.au ...)."""
    host = (addr or "").rsplit("@", 1)[-1].lower().strip(" >")
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if parts[-2] in ("co", "com", "org", "net", "gov", "ac", "edu") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def service_name(from_header):
    """A short label for the banner: the sender's display name, minus plumbing
    ('via', 'no-reply'), else the domain's second-level label capitalised."""
    m = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', from_header or "")
    name, addr = (m.group(1).strip(), m.group(2).strip()) if m else ("", (from_header or "").strip())
    name = re.sub(r"\s+(?:via|from)\s+.*$", "", name, flags=re.IGNORECASE).strip(" '\"")
    name = re.sub(r"\s*'.*$", "", name).strip()  # Jagex 'no-reply at ...'
    if name and not _NOISE_NAME.match(name) and "@" not in name:
        return name[:40]
    dom = registrable_domain(addr)
    label = dom.split(".")[0] if dom else "Unknown"
    return label.capitalize()


def _is_year(tok):
    return len(tok) == 4 and tok.isdigit() and 1900 <= int(tok) <= 2100


def _candidates(text, intent_positions):
    """Yield (score, token) for every plausible code token in text."""
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        kind = m.lastgroup
        before = text[max(0, m.start() - 24):m.start()]
        if _BAD_BEFORE.search(before):
            continue
        if re.search(r"\d[\s()+-]*$", before):
            continue  # tail of a phone number or a spaced meeting id
        if kind == "digits" and _is_year(tok):
            continue
        if kind == "grouped":
            # "123 456" only counts right after the announcing words
            if not re.search(r"code\b.{0,8}$", before, re.IGNORECASE):
                continue
            tok = re.sub(r"[ -]", "", tok)
        if kind == "alnum":
            if tok.isdigit():
                continue  # the digits branch would have caught it
            if not any(c.isdigit() for c in tok):
                if tok in _COMMON_UPPER or len(tok) > 6:
                    continue
                # pure letters are accepted only within reach of an intent phrase
                if not any(abs(m.start() - p) < 120 for p in intent_positions):
                    continue
        # Meeting invites: a passcode that follows a meeting id is not ours
        if re.search(r"meeting\s+id", text[max(0, m.start() - 160):m.start()], re.IGNORECASE):
            continue
        # Score: distance to the nearest intent phrase, closer is better; a
        # token right after the phrase beats one before it; longer wins ties.
        dist = min((abs(m.start() - p) for p in intent_positions), default=10_000)
        if dist > 250:
            continue  # a code sits next to the words that announce it
        after = any(0 <= m.start() - p < 160 for p in intent_positions)
        score = dist - (40 if after else 0) - len(tok)
        yield score, tok


def extract_code(subject, body, from_header=""):
    """Return {'code', 'service', 'domain'} when the message delivers a one-time
    code, else None. `body` can be HTML or plain text."""
    subject = html_to_text(subject or "")
    text = html_to_text(body or "")
    head = text[:4000]
    if _NEGATIVE_SUBJECT.search(subject) and not _INTENT.search(subject):
        return None
    subj_intents = [m.start() for m in _INTENT.finditer(subject)]
    body_intents = [m.start() for m in _INTENT.finditer(head)]
    if not subj_intents and not body_intents:
        return None
    # The subject is the strongest signal: "482913 is your X code", "Login code: 6502"
    best = None
    if subj_intents:
        for score, tok in _candidates(subject, subj_intents):
            if best is None or score < best[0]:
                best = (score - 1000, tok)
    if best is None:
        intents = body_intents or [0]
        for score, tok in _candidates(head, intents):
            if best is None or score < best[0]:
                best = (score, tok)
    if best is None:
        return None
    addr = re.search(r"<([^>]+)>", from_header or "")
    addr = addr.group(1) if addr else (from_header or "")
    return {"code": best[1], "service": service_name(from_header), "domain": registrable_domain(addr)}
