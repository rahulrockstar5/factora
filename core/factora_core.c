/*
 * Factora core engine (C)
 *
 * Two fast, dependency-free routines that the Python backend calls through ctypes:
 *
 *   factora_analyze_text()   scans post text for hoax and scam patterns
 *   factora_edit_distance()  edit distance, used to spot lookalike domains
 *
 * Build:  make -C core        (produces core/libfactora.so)
 *
 * The Python package has a pure Python version of both routines with identical
 * results, so the site still works if this library is missing or cannot load.
 */
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#include "factora_core.h"

/* Phrase lists. Keep these identical to factora/engine.py. */
static const char *URGENCY[] = {
    "breaking", "urgent", "shocking", "you won't believe", "you wont believe",
    "they don't want you to know", "they dont want you to know",
    "doctors do not want", "doctors don't want", "before it's deleted",
    "before its deleted", "before it is deleted", "forward this", "forward to",
    "share this now", "share now", "share with everyone", "100% true",
    "100% real", "100% proof", "100% confirmed", NULL};

static const char *MIRACLE[] = {
    "cures cancer", "cure cancer", "cured cancer", "curing cancer",
    "cures diabetes", "cure diabetes", "cures covid", "cure covid",
    "cures aids", "cures hiv", "miracle cure", "instant cure",
    "instantly cure", NULL};

static const char *PRIZE[] = {
    "free recharge", "free gift", "free laptop", "free iphone", "free data",
    "lottery", "claim your prize", "claim your reward", "you have won",
    "you won a", NULL};

static const char *VAGUE[] = {
    "scientists say", "scientists said", "scientists confirm", "doctors say",
    "doctors said", "doctors confirm", "experts say", "experts said",
    "experts confirm", "officials say", "officials said", "sources say",
    "sources said", "sources confirm", "a source says", NULL};

static const char *OFFICIAL[] = {
    "pib", "rbi", "govt", "government", "ministry", "election commission",
    "supreme court", "isro", NULL};

static int contains_any(const char *hay, const char **needles, int *hits)
{
    int found = 0;
    for (int i = 0; needles[i]; i++) {
        if (strstr(hay, needles[i])) {
            found = 1;
            if (hits) (*hits)++;
        }
    }
    return found;
}

/* True when 'word' appears in 'hay' with non-letters (or an edge) on both sides. */
static int contains_word(const char *hay, const char *word)
{
    size_t wl = strlen(word);
    const char *p = hay;
    while ((p = strstr(p, word)) != NULL) {
        int left_ok = (p == hay) || !isalpha((unsigned char)p[-1]);
        int right_ok = !isalpha((unsigned char)p[wl]);
        if (left_ok && right_ok) return 1;
        p++;
    }
    return 0;
}

static int contains_any_word(const char *hay, const char **words)
{
    for (int i = 0; words[i]; i++)
        if (contains_word(hay, words[i])) return 1;
    return 0;
}

/* "according to X" with a capital letter after it, case-sensitive on the original text. */
static int has_named_attribution(const char *orig)
{
    const char *p = orig;
    while ((p = strstr(p, "according to ")) != NULL) {
        unsigned char c = (unsigned char)p[13];
        if (c >= 'A' && c <= 'Z') return 1;
        p++;
    }
    return 0;
}

int factora_analyze_text(const char *text, FactoraTextResult *out)
{
    if (!text || !out) return -1;
    memset(out, 0, sizeof(*out));

    size_t n = strlen(text);
    if (n > 20000) n = 20000;
    out->length = (int)n;
    if (n < 12) return 0; /* too short to judge */

    char *low = (char *)malloc(n + 1);
    char *orig = (char *)malloc(n + 1);
    if (!low || !orig) { free(low); free(orig); return -2; }
    memcpy(orig, text, n);
    orig[n] = '\0';
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)orig[i];
        low[i] = (c < 128) ? (char)tolower(c) : (char)c;
    }
    low[n] = '\0';

    int letters = 0, caps = 0, exclaims = 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)orig[i];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) {
            letters++;
            if (c >= 'A' && c <= 'Z') caps++;
        } else if (c == '!') {
            exclaims++;
        }
    }
    out->letters = letters;
    out->caps = caps;
    out->exclaims = exclaims;

    int delta = 0, hits = 0;

    if (contains_any(low, URGENCY, &hits)) { out->flags |= FACTORA_F_URGENCY; delta -= 14; }
    if (exclaims >= 3 || (letters >= 20 && caps * 10 > letters * 4)) {
        out->flags |= FACTORA_F_SHOUTING; delta -= 8; hits++;
    }
    if (contains_any(low, MIRACLE, &hits)) { out->flags |= FACTORA_F_MIRACLE; delta -= 14; }
    if (contains_any(low, PRIZE, &hits)) { out->flags |= FACTORA_F_PRIZE; delta -= 16; }
    if (contains_any(low, VAGUE, &hits) && !has_named_attribution(orig)) {
        out->flags |= FACTORA_F_VAGUE; delta -= 6;
    }
    if (contains_any_word(low, OFFICIAL)) out->flags |= FACTORA_F_OFFICIAL_NAME;

    out->delta = delta;
    out->hits = hits;
    free(low);
    free(orig);
    return 0;
}

/* Edit distance on ASCII bytes. Insert, delete, substitute and swapping two
 * neighbouring letters each cost 1 (so "reutres" is 1 away from "reuters").
 * Returns -1 if either string is longer than 128. */
int factora_edit_distance(const char *a, const char *b)
{
    if (!a || !b) return -1;
    size_t la = strlen(a), lb = strlen(b);
    if (la > 128 || lb > 128) return -1;
    int pp[129], prev[129], cur[129];
    for (size_t j = 0; j <= lb; j++) { prev[j] = (int)j; pp[j] = 0; }
    for (size_t i = 1; i <= la; i++) {
        cur[0] = (int)i;
        for (size_t j = 1; j <= lb; j++) {
            int cost = (a[i - 1] == b[j - 1]) ? 0 : 1;
            int best = prev[j] + 1;
            if (cur[j - 1] + 1 < best) best = cur[j - 1] + 1;
            if (prev[j - 1] + cost < best) best = prev[j - 1] + cost;
            if (i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1] && pp[j - 2] + 1 < best)
                best = pp[j - 2] + 1;
            cur[j] = best;
        }
        memcpy(pp, prev, (lb + 1) * sizeof(int));
        memcpy(prev, cur, (lb + 1) * sizeof(int));
    }
    return prev[lb];
}

int factora_version(void) { return 1; }
