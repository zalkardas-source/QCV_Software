"""Profile-URL import for LinkedIn, Xing, and Freelancermap.

Pragmatic approach: fetch the URL anonymously; if the response is too short
(login wall / Cloudflare block), the caller falls back to user-pasted text.
Either way, the final text is handed off to the existing LLM CV extractor.
"""
from urllib.parse import urlparse
import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Realistic browser headers — many sites reject obvious bot requests.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}

# Below this many extracted characters, the fetch is considered a failure
# (login wall, JS-only page, or block page).
MIN_USEFUL_TEXT_LEN = 400

# Known source-URL patterns. Detected purely for metadata / UI hints.
_SOURCE_PATTERNS: dict[str, re.Pattern] = {
    "linkedin": re.compile(r"linkedin\.com/(in|pub)/", re.I),
    "xing": re.compile(r"xing\.com/profile/", re.I),
    "freelancermap": re.compile(r"freelancermap\.(de|com|at)/", re.I),
}


def detect_source(url: str) -> str | None:
    """Returns 'linkedin' / 'xing' / 'freelancermap' or None."""
    if not url:
        return None
    for name, pattern in _SOURCE_PATTERNS.items():
        if pattern.search(url):
            return name
    return None


def is_valid_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def extract_text(html: str) -> str:
    """Strips boilerplate (nav, scripts, footers) and returns clean text."""
    soup = BeautifulSoup(html, "html.parser")

    # Drop elements that never contain CV content
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg", "iframe"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse whitespace
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def fetch_url(url: str, timeout: int = 15) -> str:
    """Fetches a URL and returns extracted text.

    Raises ValueError with a descriptive message on common failures so the
    caller can show the user a useful error.
    """
    if not is_valid_http_url(url):
        raise ValueError("Invalid URL — must start with http:// or https://")

    try:
        response = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.exceptions.Timeout:
        raise ValueError("Timeout — the page took too long to load")
    except requests.exceptions.ConnectionError:
        raise ValueError("Connection failed — check the URL or your network")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Network error: {e}")

    if response.status_code == 403 or response.status_code == 401:
        raise ValueError(
            "The page blocked the request (likely a login wall). "
            "Open the profile in your browser, copy the text, and paste it instead."
        )
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code} — page not accessible")

    text = extract_text(response.text)
    if len(text) < MIN_USEFUL_TEXT_LEN:
        raise ValueError(
            "Too little content extracted — the page is likely behind a login "
            "wall or rendered via JavaScript. Please paste the profile text instead."
        )
    return text
