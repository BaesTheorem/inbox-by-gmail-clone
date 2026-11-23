"""reply_recipients: the Reply and Reply-all presets the reader shows as chips."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

ME = "me@gmail.com"


def H(**kw):
    return [{"name": k.replace("_", "-"), "value": v} for k, v in kw.items()]


def emails(lst):
    return [a["email"] for a in lst]


def test_reply_goes_to_sender_and_all_adds_the_others_as_cc():
    r = app.reply_recipients(H(From="Ann <ann@x.com>", To="Me <me@gmail.com>, bob@x.com",
                               Cc="Cy <cy@x.com>"), ME)
    assert emails(r["reply"]["to"]) == ["ann@x.com"] and r["reply"]["cc"] == []
    assert emails(r["all"]["to"]) == ["ann@x.com"]
    assert emails(r["all"]["cc"]) == ["bob@x.com", "cy@x.com"]
    assert r["reply"]["to"][0]["name"] == "Ann"


def test_reply_to_header_wins_over_from():
    r = app.reply_recipients(H(From="List <list@x.com>", Reply_To="ann@x.com", To=ME), ME)
    assert emails(r["reply"]["to"]) == ["ann@x.com"]
    assert emails(r["all"]["to"]) == ["ann@x.com"] and r["all"]["cc"] == []


def test_my_own_message_goes_to_the_people_i_wrote_to():
    r = app.reply_recipients(H(From="Me <ME@gmail.com>", To="ann@x.com, bob@x.com", Cc="cy@x.com"), ME)
    assert emails(r["reply"]["to"]) == ["ann@x.com", "bob@x.com"]
    assert emails(r["all"]["to"]) == ["ann@x.com", "bob@x.com"]
    assert emails(r["all"]["cc"]) == ["cy@x.com"]


def test_name_with_a_comma_is_one_address():
    r = app.reply_recipients(H(From="ann@x.com", To='"Doe, Bob" <bob@x.com>, me@gmail.com'), ME)
    assert emails(r["all"]["cc"]) == ["bob@x.com"]
    assert r["all"]["cc"][0]["name"] == "Doe, Bob"


def test_alias_and_duplicates_are_not_recipients():
    r = app.reply_recipients(H(From="ann@x.com", To="shop@alias.example, Ann <ANN@x.com>, bob@x.com"),
                             ME, ["shop@alias.example"])
    assert emails(r["all"]["to"]) == ["ann@x.com"]
    assert emails(r["all"]["cc"]) == ["bob@x.com"]


def test_no_headers_gives_empty_presets():
    r = app.reply_recipients([], ME)
    assert r == {"reply": {"to": [], "cc": []}, "all": {"to": [], "cc": []}}


def test_message_without_to_still_builds():
    import base64
    raw = base64.urlsafe_b64decode(app._build_raw_message("", "s", "b", cc="ann@x.com"))
    assert b"Cc: ann@x.com" in raw and b"\nTo:" not in raw


def test_dotted_and_tagged_forms_of_my_gmail_address_are_me():
    r = app.reply_recipients(H(From="ann@x.com", To="m.e+shop@gmail.com, bob@x.com", Cc="ME@googlemail.com"), ME)
    assert emails(r["all"]["cc"]) == ["bob@x.com"]
    r = app.reply_recipients(H(From="m.e@gmail.com", To="ann@x.com"), ME)
    assert emails(r["reply"]["to"]) == ["ann@x.com"]
