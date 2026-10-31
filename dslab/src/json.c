#include "dslab.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

static void ensure(dslab_buf *b, size_t extra) {
    if (b->len + extra + 1 <= b->cap) return;
    size_t cap = b->cap ? b->cap : 4096;
    while (cap < b->len + extra + 1) cap *= 2;
    char *p = realloc(b->data, cap);
    if (!p) abort();
    b->data = p;
    b->cap = cap;
}

void json_init(dslab_buf *b) {
    memset(b, 0, sizeof(*b));
    ensure(b, 1);
    b->data[0] = 0;
}

void json_free(dslab_buf *b) {
    free(b->data);
    memset(b, 0, sizeof(*b));
}

void json_raw(dslab_buf *b, const char *s) {
    size_t n = strlen(s);
    ensure(b, n);
    memcpy(b->data + b->len, s, n);
    b->len += n;
    b->data[b->len] = 0;
}

void json_str(dslab_buf *b, const char *s) {
    if (!s) { json_raw(b, "null"); return; }
    ensure(b, strlen(s) * 6 + 2);
    char *p = b->data + b->len;
    *p++ = '"';
    for (const unsigned char *c = (const unsigned char *)s; *c; c++) {
        switch (*c) {
        case '"': *p++ = '\\'; *p++ = '"'; break;
        case '\\': *p++ = '\\'; *p++ = '\\'; break;
        case '\n': *p++ = '\\'; *p++ = 'n'; break;
        case '\r': *p++ = '\\'; *p++ = 'r'; break;
        case '\t': *p++ = '\\'; *p++ = 't'; break;
        default:
            if (*c < 0x20) { p += sprintf(p, "\\u%04x", *c); }
            else *p++ = (char)*c;
        }
    }
    *p++ = '"';
    b->len = (size_t)(p - b->data);
    b->data[b->len] = 0;
}

void json_kv_str(dslab_buf *b, const char *k, const char *v) {
    json_str(b, k); json_raw(b, ":"); json_str(b, v);
}
void json_kv_u64(dslab_buf *b, const char *k, uint64_t v) {
    char tmp[64]; snprintf(tmp, sizeof(tmp), "%llu", (unsigned long long)v);
    json_str(b, k); json_raw(b, ":"); json_raw(b, tmp);
}
void json_kv_i64(dslab_buf *b, const char *k, int64_t v) {
    char tmp[64]; snprintf(tmp, sizeof(tmp), "%lld", (long long)v);
    json_str(b, k); json_raw(b, ":"); json_raw(b, tmp);
}
void json_kv_bool(dslab_buf *b, const char *k, bool v) {
    json_str(b, k); json_raw(b, ":"); json_raw(b, v ? "true" : "false");
}
void json_kv_double(dslab_buf *b, const char *k, double v) {
    char tmp[64]; snprintf(tmp, sizeof(tmp), "%.6f", v);
    json_str(b, k); json_raw(b, ":"); json_raw(b, tmp);
}
