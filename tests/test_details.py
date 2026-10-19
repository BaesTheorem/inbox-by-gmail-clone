"""message_details: the rows of Gmail's "show details" popup."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

H = lambda **kw: [{"name": k.replace("_", "-"), "value": v} for k, v in kw.items()]  # noqa: E731


def _list_mail():
    return [
        {"name": "Return-Path", "value": "<jobalerts-noreply@bounce.example.com>"},
        {"name": "Received", "value": "from maild.example.com by mx.google.com with ESMTPS id x "
                                      "(version=TLS1_3 cipher=TLS_AES_256_GCM_SHA384 bits=256/256)"},
        {"name": "Authentication-Results", "value":
            "mx.google.com; dkim=pass header.i=@maild.example.com header.s=a header.b=Q; "
            "dkim=pass header.i=@example.com header.s=b header.b=i; spf=pass (google.com: domain "
            "of x@bounce.example.com designates 1.2.3.4) smtp.mailfrom=x@bounce.example.com; dmarc=pass"},
        {"name": "From", "value": "Example Alerts <alerts@example.com>"},
        {"name": "Reply-To", "value": "help@example.com"},
        {"name": "To", "value": "Me <me@gmail.com>"},
        {"name": "Date", "value": "Mon, 19 Oct 2026 21:03:58 +0000 (UTC)"},
        {"name": "Subject", "value": "Hello"},
    ]


def test_inbound_rows():
    d = app.message_details(_list_mail())
    assert d["from"] == "Example Alerts <alerts@example.com>"
    assert d["replyTo"] == "help@example.com"
    assert d["mailedBy"] == "bounce.example.com"
    assert d["signedBy"] == "example.com"  # the From-aligned signer wins over the first
    assert d["security"] == "Standard encryption (TLS)"
    assert d["subject"] == "Hello"
    assert "2026" in d["date"]
    assert "cc" not in d and "bcc" not in d


def test_google_internal_hop_is_tls():
    hs = _list_mail()
    hs[1] = {"name": "Received", "value": "from mail-sor-f41.google.com by mx.google.com with SMTPS id a "
                                          "(Google Transport Security)"}
    assert app.message_details(hs)["security"] == "Standard encryption (TLS)"


def test_sent_copy_has_no_auth_rows():
    d = app.message_details(H(From="Me <me@gmail.com>", To="a@b.com", Bcc="c@d.com",
                              Subject="s", Date="Mon, 19 Oct 2026 17:28:02 -0500"))
    assert set(d) == {"from", "to", "bcc", "subject", "date"}


def test_plain_received_is_not_tls():
    hs = [h for h in _list_mail() if h["name"] != "Received"]
    hs.append({"name": "Received", "value": "from x by mx.google.com with SMTP id q"})
    assert "security" not in app.message_details(hs)
