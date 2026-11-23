"""Run with:  python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from factora import engine, service  # noqa: E402

SAMPLES = [
    "BREAKING!!! Govt will shut down ALL ATMs for 7 days from tomorrow. Withdraw your money NOW. Forward this to everyone before it is deleted!!!",
    "Doctors do not want you to know this. Hot lemon water cures cancer in 3 days. 100% proof. Share now!",
    "Scientists say the new policy will change everything. The PIB has not commented.",
    "Scientists say it, according to Reuters, but only in part.",
    "Win a free iPhone in this lottery. Claim your prize today.",
    "The council met on Tuesday to discuss the new bridge budget and agreed to review it in March.",
    "short",
    "",
    "Ünïcode text with émojis 😀 and ASCII URGENT words, share with everyone!",
]


class TextParity(unittest.TestCase):
    """The C routine and the pure Python fallback must give the same answers."""

    def test_c_matches_python(self):
        if engine.engine_name() != "c":
            self.skipTest("libfactora.so not built. Run: make -C core")
        for s in SAMPLES:
            with self.subTest(text=s[:40]):
                self.assertEqual(engine.analyze_text(s), engine._py_analyze(s.encode("utf-8")))

    def test_edit_distance_parity(self):
        if engine.engine_name() != "c":
            self.skipTest("libfactora.so not built")
        pairs = [("reuters.com", "reutres.com"), ("reutres", "reuters"), ("ab", "ba"), ("bbc.com", "bbc.com"), ("", "abc"), ("kitten", "sitting")]
        for a, b in pairs:
            self.assertEqual(engine.edit_distance(a, b), engine._py_edit_distance(a, b))

    def test_known_values(self):
        r = engine._py_analyze(SAMPLES[0].encode())
        self.assertTrue(r["flags"] & engine.F_URGENCY)
        self.assertTrue(r["flags"] & engine.F_SHOUTING)
        self.assertLess(r["delta"], -20)
        self.assertEqual(engine._py_analyze(SAMPLES[5].encode())["delta"], 0)
        self.assertEqual(engine._py_edit_distance("kitten", "sitting"), 3)
        self.assertEqual(engine._py_edit_distance("reutres", "reuters"), 1)


class LinkAnalysis(unittest.TestCase):
    def test_invalid(self):
        self.assertIsNone(engine.analyze_link("not a link"))
        self.assertIsNone(engine.analyze_link("ftp://example.com/x"))

    def test_official_account(self):
        l = engine.analyze_link("https://x.com/PIBFactCheck")
        self.assertEqual(l["known"], "official")
        self.assertGreaterEqual(l["score"], 80)

    def test_news_domain(self):
        self.assertEqual(engine.analyze_link("https://www.reuters.com/world/x")["known"], "news")

    def test_lookalike(self):
        l = engine.analyze_link("https://reutres.com/story")
        self.assertTrue(any("copy of reuters.com" in f["text"] for f in l["flags"]))
        self.assertLess(l["score"], 40)

    def test_throwaway_domain(self):
        self.assertLess(engine.analyze_link("https://bbc-news-live-update.xyz/a")["score"], 20)


class Service(unittest.TestCase):
    def setUp(self):
        # No network in tests.
        patches = [
            mock.patch("factora.sources.fetch_post_text", return_value=None),
            mock.patch("factora.sources.google_fact_check", return_value=None),
            mock.patch("factora.sources.gdelt_search", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_hoax_text_scores_low(self):
        r = service.check("https://www.facebook.com/groups/1/posts/2", SAMPLES[0])
        self.assertLess(r["score"], 25)
        self.assertEqual(set(r["checks"]), set(service.SOURCE_KEYS))

    def test_link_only_is_unclear_and_low_confidence(self):
        r = service.check("https://x.com/someuser/status/123", "")
        self.assertEqual(r["conf"], "low")
        self.assertTrue(30 <= r["score"] <= 60)
        self.assertFalse(r["text_found"])

    def test_invalid_link(self):
        self.assertEqual(service.check("hello", "")["error"], "invalid_link")

    def test_fact_check_contradiction_lowers_score(self):
        fake = {"reviews": [{"claim": "x", "publisher": "Alt News", "rating": "False", "url": "", "verdict": "false"}]}
        with mock.patch("factora.sources.google_fact_check", return_value=fake):
            r = service.check("https://x.com/u/status/9", "The ministry will ban all cash withdrawals next week, officials said.")
        self.assertEqual(r["checks"]["factcheck"]["status"], "contradicts")
        self.assertLess(r["score"], 25)

    def test_news_coverage_raises_score(self):
        arts = [{"url": "u", "domain": "reuters.com", "title": "t"}, {"url": "u", "domain": "bbc.com", "title": "t"}]
        with mock.patch("factora.sources.gdelt_search", return_value=arts):
            r = service.check("https://x.com/u/status/9", "The central bank raised interest rates by half a percent on Friday.")
        self.assertEqual(r["checks"]["news"]["status"], "supports")


class SafeFetch(unittest.TestCase):
    def test_private_addresses_blocked(self):
        from factora import sources
        for h in ("127.0.0.1", "localhost", "10.0.0.5", "169.254.169.254", "192.168.1.1"):
            self.assertFalse(sources.is_public_host(h), h)
        with self.assertRaises(ValueError):
            sources.http_get("http://127.0.0.1:8000/")


if __name__ == "__main__":
    unittest.main()
