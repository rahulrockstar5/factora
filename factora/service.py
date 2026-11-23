"""Ties link analysis, the C text scanner and the outside sources into one result."""
import re
from concurrent.futures import ThreadPoolExecutor

from . import engine, sources

SOURCE_KEYS = ["official", "government", "news", "factcheck", "research"]


def _unverified(note):
    return {"status": "unverified", "note": note}


def extract_claims(text, limit=3):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    scored = []
    for p in parts:
        p = p.strip(" \t\"'")
        if not 25 <= len(p) <= 220:
            continue
        s = 0
        if re.search(r"\d", p):
            s += 2
        if re.search(r"\b(will|is|are|was|were|has|have|says?|confirmed|announced|banned|shut|cure[sd]?)\b", p, re.I):
            s += 1
        scored.append((s, p))
    scored.sort(key=lambda x: -x[0])  # stable: keeps original order for ties
    return [p for _, p in scored[:limit]]


def _domain_in(domain, domains):
    return any(domain == d or domain.endswith("." + d) for d in domains)


def check(url, text=""):
    link = engine.analyze_link(url)
    if not link:
        return {"error": "invalid_link", "message": "That does not look like a link. Include the full address."}

    notices = []
    text = (text or "").strip()[:4000]
    post_text, author, text_via = text, None, ("you" if text else None)

    if not text:
        got = sources.fetch_post_text(link["url"], link["platform"])
        if got:
            post_text, author, text_via = got["text"], got.get("author"), got["via"]
        else:
            notices.append("Factora could not read the post text from this link. Paste the text for a sharper result.")

    score = link["score"]
    flags = list(link["flags"])
    known = link["known"]

    t_delta, t_flags, t_raw = engine.score_text(post_text, known)
    score += t_delta
    flags += t_flags

    claims = extract_claims(post_text)
    words = sources.keywords(post_text)
    has_text = len(post_text) >= 12

    fact = news = None
    if has_text:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_fact = pool.submit(sources.google_fact_check, " ".join(words) or post_text[:200])
            f_news = pool.submit(sources.gdelt_search, words)
            fact, news = f_fact.result(), f_news.result()

    checks = {k: _unverified("Nothing found for this source.") for k in SOURCE_KEYS}
    checks["research"] = _unverified("Research and journal sources are not searched yet.")

    # Official accounts and government bodies
    if known == "official":
        checks["official"] = {"status": "supports", "note": "The link is on an official domain or account."}
    elif link["platform"] != "Web page":
        checks["official"] = _unverified("The account is not on Factora's list of official accounts.")
    if known == "official" and (link["host"].endswith(".gov") or "gov" in link["host"].split(".")):
        checks["government"] = {"status": "supports", "note": "The link is on a government domain."}
    elif known == "official":
        checks["government"] = {"status": "supports", "note": "The link is on an official body's channel."}

    # News
    if known == "news":
        checks["news"] = {"status": "supports", "note": "The link is on an established news outlet."}
    elif news is None and has_text:
        checks["news"] = _unverified("News search did not respond, so coverage could not be checked.")
        notices.append("News search (GDELT) did not respond, so news coverage was not checked.")
    elif news is not None:
        trusted = [a for a in news if _domain_in(a["domain"], engine.NEWS_DOMAINS)]
        gov = [a for a in news if _domain_in(a["domain"], engine.OFFICIAL_DOMAINS) or a["domain"].endswith(".gov")]
        outlets = sorted({a["domain"] for a in trusted})
        if len(outlets) >= 2:
            score += 10
            checks["news"] = {"status": "supports", "note": "Recent coverage found on " + ", ".join(outlets[:3]) + "."}
        elif len(outlets) == 1:
            score += 5
            checks["news"] = {"status": "supports", "note": f"Recent coverage found on {outlets[0]}. One outlet only."}
        else:
            checks["news"] = _unverified("No trusted outlet was found reporting this in the last month.")
        if gov and checks["government"]["status"] != "supports":
            score += 5
            checks["government"] = {"status": "supports", "note": "A government site appears in recent coverage: " + gov[0]["domain"] + "."}

    # Fact-checkers
    if known == "factcheck":
        checks["factcheck"] = {"status": "supports", "note": "The link is on a fact-checking site. Read its verdict on the claim."}
    elif has_text and fact is None:
        import os
        if not os.environ.get("FACTCHECK_API_KEY"):
            notices.append("Google Fact Check was skipped because FACTCHECK_API_KEY is not set on the server.")
            checks["factcheck"] = _unverified("Fact-check search is switched off on this server.")
        else:
            notices.append("Google Fact Check did not respond.")
            checks["factcheck"] = _unverified("Fact-check search did not respond.")
    elif fact is not None:
        rv = fact["reviews"]
        bad = [r for r in rv if r["verdict"] == "false"]
        good = [r for r in rv if r["verdict"] == "true"]
        if bad and len(bad) >= len(good):
            score -= 35
            r = bad[0]
            checks["factcheck"] = {"status": "contradicts", "note": f'{r["publisher"] or "A fact-checker"} rated a similar claim "{r["rating"]}".'}
        elif good:
            score += 20
            r = good[0]
            checks["factcheck"] = {"status": "supports", "note": f'{r["publisher"] or "A fact-checker"} rated a similar claim "{r["rating"]}".'}
        else:
            checks["factcheck"] = _unverified("No published fact-check matched this claim.")

    score = engine.clamp_score(score)
    tone = engine.tone_of(score)

    if known or (fact and fact["reviews"]):
        conf = "high"
    elif has_text:
        conf = "medium"
    else:
        conf = "low"

    headline = {
        "good": "Signals point to this being real.",
        "warn": "Not enough evidence to call it real or fake.",
        "bad": "Several signs match fake or misleading posts.",
    }[tone]
    if not has_text and not known:
        summary = "Only the link was available. Add the post text to get a firmer result."
    elif tone == "bad":
        summary = "Do not share this until an official source or a fact-checker confirms it."
    elif tone == "good":
        summary = "Nothing contradicts it and the sources support it. Still read the original before sharing."
    else:
        summary = "Some signals are missing or mixed. Check the sources below before sharing."

    if tone == "good":
        nxt = ["Open the original page and read it in full.", "Check the date so an old story is not being passed off as new."]
    else:
        nxt = [
            "Search the claim on PIB Fact Check, Alt News or BOOM.",
            "Open the account profile and look at its age, followers and past posts.",
            "Do not forward it until an official source confirms it.",
        ]

    return {
        "score": score, "conf": conf, "headline": headline, "summary": summary,
        "platform": link["platform"], "account": author or link["account"], "host": link["host"],
        "claims": claims, "checks": checks, "flags": flags[:8], "next": nxt,
        "text_found": has_text, "post_text": post_text[:300] if text_via and text_via != "you" else "",
        "text_via": text_via, "notices": notices, "engine": engine.engine_name(),
    }
