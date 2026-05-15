"""Unit tests for profile-URL import."""
from unittest.mock import patch, MagicMock

import pytest
import requests

from backend.scraping import (
    detect_source,
    is_valid_http_url,
    extract_text,
    fetch_url,
    MIN_USEFUL_TEXT_LEN,
)


# ── detect_source ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.linkedin.com/in/john-doe-12345/", "linkedin"),
    ("https://linkedin.com/pub/john-doe", "linkedin"),
    ("https://www.xing.com/profile/John_Doe", "xing"),
    ("https://www.freelancermap.de/profile/john-doe", "freelancermap"),
    ("https://www.freelancermap.com/some-page", "freelancermap"),
    ("https://www.freelancermap.at/page", "freelancermap"),
    ("https://example.com/foo", None),
    ("", None),
])
def test_detect_source(url, expected):
    assert detect_source(url) == expected


# ── is_valid_http_url ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://example.com", True),
    ("http://example.com/path", True),
    ("ftp://example.com", False),
    ("not a url", False),
    ("javascript:alert(1)", False),
    ("", False),
])
def test_is_valid_http_url(url, expected):
    assert is_valid_http_url(url) is expected


# ── extract_text ─────────────────────────────────────────────────────────────

def test_extract_text_removes_scripts_and_styles():
    html = """
    <html><head><style>body{color:red}</style></head>
    <body>
        <script>var x = 1;</script>
        <nav>Menu</nav>
        <p>John Doe — Senior Developer</p>
        <footer>(c) 2025</footer>
    </body></html>
    """
    text = extract_text(html)
    assert "John Doe" in text
    assert "var x" not in text
    assert "body{color:red}" not in text
    assert "Menu" not in text
    assert "(c) 2025" not in text


def test_extract_text_collapses_whitespace():
    html = "<p>Line one</p>\n\n\n   <p>Line two</p>"
    text = extract_text(html)
    assert text == "Line one\nLine two"


# ── fetch_url ────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, text: str) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    return r


def test_fetch_url_rejects_invalid_url():
    with pytest.raises(ValueError, match="Invalid URL"):
        fetch_url("not a url")


def test_fetch_url_success():
    long_text_html = "<html><body>" + ("<p>This is a CV paragraph with content.</p>" * 30) + "</body></html>"
    with patch("backend.scraping.requests.get", return_value=_mock_response(200, long_text_html)):
        result = fetch_url("https://example.com/cv")
    assert "This is a CV paragraph" in result


def test_fetch_url_403_login_wall():
    with patch("backend.scraping.requests.get", return_value=_mock_response(403, "")):
        with pytest.raises(ValueError, match="login wall"):
            fetch_url("https://linkedin.com/in/foo")


def test_fetch_url_500_error():
    with patch("backend.scraping.requests.get", return_value=_mock_response(500, "")):
        with pytest.raises(ValueError, match="HTTP 500"):
            fetch_url("https://example.com")


def test_fetch_url_timeout():
    with patch("backend.scraping.requests.get", side_effect=requests.exceptions.Timeout):
        with pytest.raises(ValueError, match="Timeout"):
            fetch_url("https://example.com")


def test_fetch_url_connection_error():
    with patch("backend.scraping.requests.get", side_effect=requests.exceptions.ConnectionError):
        with pytest.raises(ValueError, match="Connection failed"):
            fetch_url("https://example.com")


def test_fetch_url_too_short_content():
    """Login walls often return very short body — should hint at paste fallback."""
    short_html = "<html><body><p>Please log in</p></body></html>"
    with patch("backend.scraping.requests.get", return_value=_mock_response(200, short_html)):
        with pytest.raises(ValueError, match="paste"):
            fetch_url("https://linkedin.com/in/foo")


def test_min_useful_text_constant_reasonable():
    """Sanity check — threshold should be enough to skip login walls but not real CVs."""
    assert 100 <= MIN_USEFUL_TEXT_LEN <= 2000
