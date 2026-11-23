#ifndef FACTORA_CORE_H
#define FACTORA_CORE_H

/* Bit flags set by factora_analyze_text(). */
#define FACTORA_F_URGENCY       1   /* "breaking", "forward this", "share now" ... */
#define FACTORA_F_SHOUTING      2   /* many capitals or exclamation marks */
#define FACTORA_F_MIRACLE       4   /* miracle cure claims */
#define FACTORA_F_PRIZE         8   /* free prizes, lotteries */
#define FACTORA_F_VAGUE        16   /* "experts say" with no named source */
#define FACTORA_F_OFFICIAL_NAME 32  /* names an official body (informational) */

typedef struct {
    int delta;     /* score change, 0 or negative */
    int flags;     /* FACTORA_F_* bits */
    int hits;      /* how many phrase lists or patterns matched */
    int length;    /* bytes scanned */
    int letters;   /* ASCII letters */
    int caps;      /* ASCII capital letters */
    int exclaims;  /* '!' characters */
} FactoraTextResult;

#ifdef __cplusplus
extern "C" {
#endif

int factora_analyze_text(const char *text, FactoraTextResult *out);
int factora_edit_distance(const char *a, const char *b);
int factora_version(void);

#ifdef __cplusplus
}
#endif

#endif
