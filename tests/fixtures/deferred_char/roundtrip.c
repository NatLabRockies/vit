/* Round-trip test for VIT's deferred-length CHARACTER view field.
 *
 * The C side is the C++ translation's position: it sees only the view struct.
 * Four cases, each a different wrong answer the design has to rule out.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    int size_avcMSG;
    int aviFAIL;
    int ErrStat;
    char* ErrMsg;
    int32_t n_ErrMsg;
    int32_t n_ErrMsg_cap;
} errlike_view_t;

/* 1. READ: report what the view says the current length and first bytes are. */
void c_read(errlike_view_t* v, int32_t* len, int32_t* cap, char* first, int32_t n)
{
    *len = v->n_ErrMsg;
    *cap = v->n_ErrMsg_cap;
    for (int32_t i = 0; i < n; i++)
        first[i] = v->ErrMsg ? v->ErrMsg[i] : '?';
}

/* 2. GROW: prefix the trimmed contents, the way ExtController's
 *    `ErrVar%ErrMsg = RoutineName//':'//TRIM(ErrVar%ErrMsg)` does. */
void c_grow(errlike_view_t* v, const char* prefix, int32_t plen)
{
    int32_t trimmed = v->n_ErrMsg;
    while (trimmed > 0 && v->ErrMsg[trimmed - 1] == ' ') trimmed--;

    int32_t want = plen + 1 + trimmed;
    if (want > v->n_ErrMsg_cap) { v->ErrStat = -99; return; }

    memmove(v->ErrMsg + plen + 1, v->ErrMsg, (size_t)trimmed);
    memcpy(v->ErrMsg, prefix, (size_t)plen);
    v->ErrMsg[plen] = ':';
    v->n_ErrMsg = want;
}

/* 3. SHRINK: assign a shorter string. */
void c_shrink(errlike_view_t* v, const char* s, int32_t n)
{
    memcpy(v->ErrMsg, s, (size_t)n);
    v->n_ErrMsg = n;
}

/* 4. OVERRUN: ask for more than the capacity. The reverse-copy must REFUSE
 *    this and leave the field alone -- not truncate it. */
void c_overrun(errlike_view_t* v)
{
    v->n_ErrMsg = v->n_ErrMsg_cap + 1;
}

/* 5. UNALLOCATED: the view must say so with a null pointer, which is the only
 *    value distinguishable from an allocated empty string. */
int c_is_null(errlike_view_t* v)
{
    return v->ErrMsg == NULL ? 1 : 0;
}
