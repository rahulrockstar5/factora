"""Factora scoring engine.

Link analysis and score assembly are written in Python. Text scanning and the
edit-distance routine live in C (core/factora_core.c) and are loaded through
ctypes. If the shared library is missing or cannot load (for example on a
platform it was not built for), the same routines run in pure Python with
identical results, so the site keeps working.
"""
import ctypes
import os
import re
from urllib.parse import urlparse

# ---------------------------------------------------------------- C library --

HERE = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.normpath(os.path.join(HERE, "..", "core"))


class _TextResult(ctypes.Structure):
    _fields_ = [
        ("delta", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("hits", ctypes.c_int),
        ("length", ctypes.c_int),
        ("letters", ctypes.c_int),
        ("caps", ctypes.c_int),
        ("exclaims", ctypes.c_int),
    ]


def _load_c():
    if os.environ.get("FACTORA_FORCE_PYTHON") == "1":
        return None
    for name in ("libfactora.so", "libfactora.dylib", "factora.dll"):
        path = os.path.join(CORE_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            lib = ctypes.CDLL(path)
            lib.factora_analyze_text.argtypes = [ctypes.c_char_p, ctypes.POINTER(_TextResult)]
            lib.factora_analyze_text.restype = ctypes.c_int
            lib.factora_edit_distance.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
            lib.factora_edit_distance.restype = ctypes.c_int
            lib.factora_version.restype = ctypes.c_int
            lib.factora_version()
            return lib
        except (OSError, AttributeError):
            continue
    return None


_LIB = _load_c()


def engine_name():
    return "c" if _LIB else "python"


# Flag bits, same values as core/factora_core.h
F_URGENCY, F_SHOUTING, F_MIRACLE, F_PRIZE, F_VAGUE, F_OFFICIAL_NAME = 1, 2, 4, 8, 16, 32

# Phrase lists. Keep these identical to core/factora_core.c.
URGENCY = [
    "breaking", "urgent", "shocking", "you won't believe", "you wont believe",
    "they don't want you to know", "they dont want you to know",
    "doctors do not want", "doctors don't want", "before it's deleted",
    "before its deleted", "before it is deleted", "forward this", "forward to",
    "share this now", "share now", "share with everyone", "100% true",
    "100% real", "100% proof", "100% confirmed",
]
MIRACLE = [
    "cures cancer", "cure cancer", "cured cancer", "curing cancer",
    "cures diabetes", "cure diabetes", "cures covid", "cure covid",
    "cures aids", "cures hiv", "miracle cure", "instant cure", "instantly cure",
]
PRIZE = [
    "free recharge", "free gift", "free laptop", "free iphone", "free data",
    "lottery", "claim your prize", "claim your reward", "you have won", "you won a",
]
VAGUE = [
    "scientists say", "scientists said", "scientists confirm", "doctors say",
    "doctors said", "doctors confirm", "experts say", "experts said",
    "experts confirm", "officials say", "officials said", "sources say",
    "sources said", "sources confirm", "a source says",
]
OFFICIAL_WORDS = [
    "pib", "rbi", "govt", "government", "ministry", "election commission",
    "supreme court", "isro",
]


def _any(low, needles):
    """Return how many needles occur in low (bytes)."""
    return sum(1 for n in needles if n.encode() in low)


def _any_word(low, words):
    for w in words:
        if re.search(rb"(?<![A-Za-z])" + re.escape(w.encode()) + rb"(?![A-Za-z])", low):
            return True
    return False


def _py_analyze(raw):
    b = raw[:20000]
    res = {"delta": 0, "flags": 0, "hits": 0, "length": len(b), "letters": 0, "caps": 0, "exclaims": 0}
    if len(b) < 12:
        return res
    low = b.lower()  # bytes.lower() only touches ASCII, like the C code
    letters = caps = exclaims = 0
    for c in b:
        if 65 <= c <= 90:
            letters += 1
            caps += 1
        elif 97 <= c <= 122:
            letters += 1
        elif c == 33:
            exclaims += 1
    res.update(letters=letters, caps=caps, exclaims=exclaims)
    delta = hits = 0

    n = _any(low, URGENCY)
    hits += n
    if n:
        res["flags"] |= F_URGENCY
        delta -= 14
    if exclaims >= 3 or (letters >= 20 and caps * 10 > letters * 4):
        res["flags"] |= F_SHOUTING
        delta -= 8
        hits += 1
    n = _any(low, MIRACLE)
    hits += n
    if n:
        res["flags"] |= F_MIRACLE
        delta -= 14
    n = _any(low, PRIZE)
    hits += n
    if n:
        res["flags"] |= F_PRIZE
        delta -= 16
    n = _any(low, VAGUE)
    hits += n
    if n and not re.search(rb"according to [A-Z]", b):
        res["flags"] |= F_VAGUE
        delta -= 6
    if _any_word(low, OFFICIAL_WORDS):
        res["flags"] |= F_OFFICIAL_NAME
    res["delta"] = delta
    res["hits"] = hits
    return res


def analyze_text(text):
    """Scan post text. Returns a dict with delta, flags, hits, length, letters, caps, exclaims."""
    raw = (text or "").encode("utf-8", "ignore")
    if _LIB:
        out = _TextResult()
        if _LIB.factora_analyze_text(raw, ctypes.byref(out)) == 0:
            return {f[0]: getattr(out, f[0]) for f in _TextResult._fields_}
    return _py_analyze(raw)


def _py_edit_distance(a, b):
    """Insert, delete, substitute or swap two neighbours, each cost 1. Same as the C version."""
    if len(a) > 128 or len(b) > 128:
        return -1
    pp = [0] * (len(b) + 1)
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            best = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                best = min(best, pp[j - 2] + 1)
            cur[j] = best
        pp, prev = prev, cur
    return prev[len(b)]


def edit_distance(a, b):
    try:
        ab, bb = a.encode("ascii"), b.encode("ascii")
    except UnicodeEncodeError:
        return -1
    if _LIB:
        return _LIB.factora_edit_distance(ab, bb)
    return _py_edit_distance(a, b)


# ------------------------------------------------------------ link analysis --

OFFICIAL_DOMAINS = ["gov.in", "nic.in", "rbi.org.in", "who.int", "un.org", "europa.eu", "gov.uk", "nhs.uk"]
NEWS_DOMAINS = [
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "ptinews.com", "thehindu.com",
    "indianexpress.com", "hindustantimes.com", "ndtv.com", "timesofindia.indiatimes.com",
    "indiatoday.in", "theguardian.com", "nytimes.com", "washingtonpost.com", "aljazeera.com",
    "dw.com", "livemint.com", "business-standard.com", "economictimes.indiatimes.com",
    "thewire.in", "scroll.in", "news18.com", "deccanherald.com", "telegraphindia.com",
]
FACT_DOMAINS = [
    "altnews.in", "boomlive.in", "factcheck.org", "snopes.com", "factly.in", "newschecker.in",
    "vishvasnews.com", "fullfact.org", "politifact.com", "factcheck.afp.com",
]
SHORTENERS = [
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "cutt.ly", "rb.gy",
    "shorturl.at", "buff.ly", "lnkd.in",
]
BAD_TLDS = {"xyz", "top", "click", "buzz", "icu", "rest", "gq", "tk", "ml", "cf", "ga", "cam", "sbs"}
SOCIAL = [
    ("x.com", "X"), ("twitter.com", "X"), ("facebook.com", "Facebook"), ("fb.com", "Facebook"),
    ("fb.watch", "Facebook"), ("instagram.com", "Instagram"), ("youtube.com", "YouTube"),
    ("youtu.be", "YouTube"), ("t.me", "Telegram"), ("telegram.me", "Telegram"),
    ("wa.me", "WhatsApp"), ("whatsapp.com", "WhatsApp"), ("reddit.com", "Reddit"),
    ("linkedin.com", "LinkedIn"), ("threads.net", "Threads"), ("threads.com", "Threads"),
    ("tiktok.com", "TikTok"), ("sharechat.com", "ShareChat"),
]
OFFICIAL_HANDLES = {
    "pibfactcheck", "pib_india", "mib_india", "rbi", "who", "un", "mohfw_india",
    "ecisveep", "cdcgov", "nasa", "isro", "ddnewslive", "airnewsalerts",
}
BRANDS = ["bbc", "reuters", "cnn", "ndtv", "aajtak", "indiatoday", "apnews", "thehindu", "timesofindia", "hindustantimes", "pib"]
SKIP_PATH = {
    "i", "home", "explore", "share", "p", "reel", "reels", "watch", "groups", "story.php",
    "permalink.php", "photo", "photos", "posts", "hashtag", "search", "intent", "status",
    "in", "company", "feed", "pulse", "video", "videos",
}
TRUSTED_FOR_LOOKALIKE = [d for d in NEWS_DOMAINS + OFFICIAL_DOMAINS + FACT_DOMAINS if len(d) >= 8]


def in_list(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def parse_link(raw):
    s = (raw or "").strip()
    if not s or re.search(r"\s", s):
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        s = "https://" + s
    try:
        u = urlparse(s)
        host = u.hostname
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not host or "." not in host or host.endswith("."):
        return None
    return u


def account_of(platform, u):
    parts = [p for p in u.path.split("/") if p]
    first = parts[0] if parts else ""
    if platform == "YouTube":
        if first.startswith("@"):
            return first
        if first in ("c", "channel", "user") and len(parts) > 1:
            return parts[1]
        return None
    if platform == "Reddit":
        return f"{first}/{parts[1]}" if first in ("r", "u", "user") and len(parts) > 1 else None
    if platform == "WhatsApp":
        return None
    if first and first.lower() not in SKIP_PATH:
        return first if first.startswith("@") else "@" + first
    return None


def lookalike_of(host):
    """Return the trusted domain this host imitates, or None."""
    if in_list(host, NEWS_DOMAINS + OFFICIAL_DOMAINS + FACT_DOMAINS):
        return None
    for t in TRUSTED_FOR_LOOKALIKE:
        d = edit_distance(host, t)
        if d < 1:
            continue
        if d == 1 or (d == 2 and len(t) >= 12):
            return t
    return None


def analyze_link(raw):
    u = parse_link(raw)
    if not u:
        return None
    host = re.sub(r"^(www|m|mobile)\.", "", u.hostname.lower())
    flags = []
    score = 50

    def add(kind, text, delta):
        nonlocal score
        flags.append({"type": kind, "text": text})
        score += delta

    platform = next((name for dom, name in SOCIAL if host == dom or host.endswith("." + dom)), None)
    account = account_of(platform, u) if platform else None
    known = None

    if u.scheme == "http":
        add("warn", "Link uses plain http, not https.", -5)
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        add("bad", "Link points to a raw IP address, not a named site.", -20)
    if in_list(host, SHORTENERS):
        add("warn", "Shortened link. The real destination is hidden.", -6)
    tld = host.rsplit(".", 1)[-1]
    if tld in BAD_TLDS:
        add("bad", f"Domain ending (.{tld}) is common on throwaway sites.", -18)
    if host.count("-") >= 3:
        add("warn", "Domain name is stuffed with hyphens.", -8)

    if in_list(host, OFFICIAL_DOMAINS) or host.endswith(".gov"):
        known = "official"
        add("good", "Link goes to an official or government domain.", 38)
    elif in_list(host, NEWS_DOMAINS):
        known = "news"
        add("good", "Link goes to an established news outlet.", 26)
    elif in_list(host, FACT_DOMAINS):
        known = "factcheck"
        add("good", "Link goes to a fact-checking organization. Its verdict is on the page.", 20)
    elif platform:
        handle = account.lstrip("@").lower() if account else ""
        if handle and handle in OFFICIAL_HANDLES:
            known = "official"
            add("good", f"Account is a known official source ({account}).", 35)
        else:
            who = f" from {account}" if account else ""
            add("warn", f"Post is on {platform}{who}, which Factora cannot confirm as official.", -4)
    else:
        twin = lookalike_of(host)
        spoof = next((b for b in BRANDS if b in host), None)
        if twin:
            add("bad", f"Domain looks like a copy of {twin}.", -22)
        elif spoof:
            add("bad", f"Site name imitates a trusted outlet ({spoof}) but is not its real domain.", -22)
        elif re.search(r"(^|[.-])gov([.-]|$)", host):
            add("bad", 'Name says "gov" but the domain is not a government one.', -18)
        else:
            add("warn", "Site is not one Factora recognizes as a trusted source.", -4)

    return {
        "url": u.geturl(), "host": host, "scheme": u.scheme, "platform": platform or "Web page",
        "account": account, "score": score, "flags": flags, "known": known,
    }


TEXT_FLAG_MESSAGES = [
    (F_URGENCY, "bad", "Wording pushes urgency or sharing, a common sign of a hoax."),
    (F_SHOUTING, "warn", "Heavy use of capitals or exclamation marks."),
    (F_MIRACLE, "bad", "Makes a miracle cure claim."),
    (F_PRIZE, "bad", "Offers free prizes or money, a common scam pattern."),
    (F_VAGUE, "warn", 'Cites "experts" or "sources" without naming them.'),
]


def score_text(text, known):
    """Return (delta, flags[]) for post text."""
    r = analyze_text(text)
    flags = [{"type": k, "text": msg} for bit, k, msg in TEXT_FLAG_MESSAGES if r["flags"] & bit]
    delta = r["delta"]
    if (r["flags"] & F_OFFICIAL_NAME) and not known:
        flags.append({"type": "warn", "text": "Names an official body, but the link does not come from it."})
        delta -= 4
    return delta, flags, r


def clamp_score(s):
    return max(5, min(95, int(round(s))))


def tone_of(score):
    return "good" if score >= 70 else "warn" if score >= 40 else "bad"
