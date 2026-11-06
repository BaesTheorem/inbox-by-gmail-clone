"""parse_mailto: RFC 6068 mailto: URLs from the default-mail-app handler."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import parse_mailto  # noqa: E402


def test_plain_address():
    assert parse_mailto("mailto:a@example.com")["to"] == "a@example.com"


def test_headers_case_and_encoding():
    f = parse_mailto("mailto:a@example.com?Subject=Hi%20there&body=line1%0D%0Aa+b&CC=c@example.com")
    assert f["subject"] == "Hi there"
    assert f["body"] == "line1\r\na+b"   # "+" stays literal
    assert f["cc"] == "c@example.com"


def test_to_header_joins_path_and_dedupes():
    f = parse_mailto("mailto:a@example.com,b@example.com?to=b@example.com&to=d@example.com")
    assert f["to"] == "a@example.com, b@example.com, d@example.com"


def test_encoded_path_and_empty_path():
    assert parse_mailto("mailto:a%40example.com")["to"] == "a@example.com"
    assert parse_mailto("mailto:?subject=x")["to"] == ""


def test_not_mailto():
    assert parse_mailto("https://example.com") is None
