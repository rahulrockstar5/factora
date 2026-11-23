"""Network lookups: read the post text and cross-check the claim.

Everything here uses only the standard library and fails soft. A lookup that
cannot run returns None, and service.py tells the user which source was skipped.
"""
import html
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; FactoraBot/1.0; +https://github.com/)"
TIMEOUT = 5
MAX_BYTES = 300_000


# ------------------------------------------------------------ safe fetching --

def is_public_host(host):
    """True only if every address the host resolves to is a public internet address."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        except ValueError:
            return False
    return True


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlparse(newurl)
        if p.scheme not in ("http", "https") or not is_public_host(p.hostname):
            raise urllib.error.URLError("redirect to a blocked address")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def http_get(url, trusted_api=False, timeout=TIMEOUT):
    """GET a URL and return text. User-supplied URLs must resolve to public addresses."""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("unsupported scheme")
    if not trusted_api and not is_public_host(p.hostname):
        raise ValueError("blocked address")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with _OPENER.open(req, timeout=timeout) as resp:
        data = resp.read(MAX_BYTES)
        charset = resp.headers.get_content_charset() or "utf-8"
    return data.decode(charset, "replace")


# ------------------------------------------------------------ post text ----

def _strip_tags(s):
    s = re.sub(r"(?i)<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _meta(page, *names):
    for name in names:
        for pat in (
            r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)["\']' % re.escape(name),
            r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(name),
        ):
            m = re.search(pat, page, re.I)
            if m and m.group(1).strip():
                return html.unescape(m.group(1)).strip()
    return ""


def _oembed(endpoint, url):
    q = urllib.parse.urlencode({"url": url, "format": "json", "omit_script": "1"})
    return json.loads(http_get(f"{endpoint}?{q}", trusted_api=True))


def fetch_post_text(url, platform):
    """Try to read the text of the post. Returns {"text", "author", "via"} or None."""
    try:
        if platform == "X" and re.search(r"/status/\d+", url):
            data = _oembed("https://publish.twitter.com/oembed", url)
            m = re.search(r"<p[^>]*>(.*?)</p>", data.get("html", ""), re.S)
            text = _strip_tags(m.group(1)) if m else ""
            if text:
                return {"text": text, "author": data.get("author_name"), "via": "X oEmbed"}
        elif platform == "YouTube":
            data = _oembed("https://www.youtube.com/oembed", url)
            if data.get("title"):
                return {"text": data["title"], "author": data.get("author_name"), "via": "YouTube oEmbed"}
        page = http_get(url)
        title = _meta(page, "og:title", "twitter:title")
        if not title:
            m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
            title = _strip_tags(m.group(1)) if m else ""
        desc = _meta(page, "og:description", "twitter:description", "description")
        text = " ".join(x for x in (title, desc) if x).strip()
        if len(text) >= 12:
            return {"text": text[:1500], "author": _meta(page, "og:site_name", "author") or None, "via": "page metadata"}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None
    return None


# --------------------------------------------------------- claim queries ----

STOP = set(
    "this that with from have will your what when where which their there about after before would could should "
    "were been being than then them they just into over also more some only very much many such like make made "
    "said says does did not for and the are was you all any can has had its out our who why how but".split()
)


def keywords(text, limit=8):
    seen, out = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9'-]{3,}", text or ""):
        lw = w.lower()
        if lw in STOP or lw in seen:
            continue
        seen.add(lw)
        out.append(lw)
        if len(out) == limit:
            break
    return out


# ------------------------------------------------------------ fact checks ---

FALSE_WORDS = (
    "false", "fake", "pants on fire", "misleading", "incorrect", "hoax", "fabricated", "scam", "satire",
    "no evidence", "unproven", "baseless", "manipulated", "altered", "misattributed", "not true", "untrue",
    "fiction", "wrong", "debunked",
)
TRUE_WORDS = ("true", "correct", "accurate", "authentic", "legit", "real")


def classify_rating(rating):
    r = (rating or "").lower()
    if any(w in r for w in FALSE_WORDS):
        return "false"
    if any(re.search(r"\b%s\b" % w, r) for w in TRUE_WORDS):
        return "true"
    return "unclear"


def google_fact_check(query):
    """Search Google's Fact Check Tools API. Needs FACTCHECK_API_KEY. Returns a dict or None."""
    key = os.environ.get("FACTCHECK_API_KEY")
    if not key or not query:
        return None
    q = urllib.parse.urlencode({"query": query, "pageSize": 6, "languageCode": "en", "key": key})
    try:
        data = json.loads(http_get("https://factchecktools.googleapis.com/v1alpha1/claims:search?" + q, trusted_api=True))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    reviews = []
    for c in data.get("claims", []):
        for r in c.get("claimReview", []):
            reviews.append({
                "claim": c.get("text", ""),
                "publisher": (r.get("publisher") or {}).get("name", ""),
                "rating": r.get("textualRating", ""),
                "url": r.get("url", ""),
                "verdict": classify_rating(r.get("textualRating", "")),
            })
    return {"reviews": reviews}


# --------------------------------------------------------------- news -------

def gdelt_search(query_words):
    """Search recent news with GDELT (free, no key). Returns a list of articles or None."""
    if len(query_words) < 2:
        return None
    q = urllib.parse.urlencode({
        "query": " ".join(query_words), "mode": "artlist", "format": "json",
        "maxrecords": 20, "timespan": "1month", "sort": "hybridrel",
    })
    try:
        raw = http_get("https://api.gdeltproject.org/api/v2/doc/doc?" + q, trusted_api=True)
        data = json.loads(raw)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    out = []
    for a in data.get("articles", []):
        out.append({"url": a.get("url", ""), "domain": (a.get("domain") or "").lower(), "title": a.get("title", "")})
    return out
