#include "dslab.h"
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>
#include <direct.h>
#include <windows.h>

static int parse_num(const char *s, double *out, const char **endp) {
    char *end = NULL;
    double v = strtod(s, &end);
    if (end == s) return -1;
    *out = v;
    if (endp) *endp = end;
    return 0;
}

int dslab_parse_rate(const char *s, uint64_t *out) {
    double v = 0;
    const char *end = NULL;
    if (!s || parse_num(s, &v, &end) != 0 || v <= 0) return -1;
    while (*end && isspace((unsigned char)*end)) end++;
    double mul = 1.0;
    if (*end) {
        if (!strcasecmp(end, "hz")) mul = 1.0;
        else if (!strcasecmp(end, "khz")) mul = 1e3;
        else if (!strcasecmp(end, "mhz")) mul = 1e6;
        else if (!strcasecmp(end, "ghz")) mul = 1e9;
        else if (!strcasecmp(end, "k")) mul = 1e3;
        else if (!strcasecmp(end, "m")) mul = 1e6;
        else if (!strcasecmp(end, "g")) mul = 1e9;
        else return -1;
    }
    double r = v * mul;
    if (r < 1 || r > 2e9) return -1;
    *out = (uint64_t)(r + 0.5);
    return 0;
}

int dslab_parse_duration(const char *s, uint64_t *out_ms) {
    double v = 0;
    const char *end = NULL;
    if (!s || parse_num(s, &v, &end) != 0 || v <= 0) return -1;
    while (*end && isspace((unsigned char)*end)) end++;
    double mul = 1.0; /* ms */
    if (*end) {
        if (!strcasecmp(end, "ms")) mul = 1.0;
        else if (!strcasecmp(end, "us")) mul = 1e-3;
        else if (!strcasecmp(end, "ns")) mul = 1e-6;
        else if (!strcasecmp(end, "s")) mul = 1000.0;
        else if (!strcasecmp(end, "m")) mul = 60000.0;
        else return -1;
    }
    double ms = v * mul;
    if (ms < 0.000001 || ms > 3600000.0) return -1;
    uint64_t r = (uint64_t)(ms + 0.5);
    *out_ms = r ? r : 1;
    return 0;
}

int dslab_parse_channels(const char *s, int *indices, int *count,
                         char *err, size_t errlen) {
    *count = 0;
    if (!s || !*s) return 0;
    char *copy = strdup(s);
    if (!copy) {
        snprintf(err, errlen, "out of memory");
        return -1;
    }
    char *p = copy;
    while (p && *p) {
        while (*p == ',' || isspace((unsigned char)*p)) p++;
        if (!*p) break;
        char *comma = strchr(p, ',');
        if (comma) *comma = 0;
        char *tok = p;
        while (*tok && isspace((unsigned char)*tok)) tok++;
        char *e = tok + strlen(tok);
        while (e > tok && isspace((unsigned char)e[-1])) *--e = 0;

        int idx = -1;
        if ((tok[0] == 'D' || tok[0] == 'd') && isdigit((unsigned char)tok[1])) {
            idx = atoi(tok + 1);
        } else if (isdigit((unsigned char)tok[0])) {
            idx = atoi(tok);
        } else {
            snprintf(err, errlen, "bad channel '%s' (use D0..D15)", tok);
            free(copy);
            return -1;
        }
        if (idx < 0 || idx >= DSLAB_MAX_CHANNELS) {
            snprintf(err, errlen, "channel index %d out of range", idx);
            free(copy);
            return -1;
        }
        int dup = 0;
        for (int i = 0; i < *count; i++) if (indices[i] == idx) dup = 1;
        if (!dup) indices[(*count)++] = idx;
        if (comma) p = comma + 1;
        else break;
    }
    free(copy);
    return 0;
}

const char *dslab_res_dir(char *buf, size_t len) {
    const char *env = getenv("DSLAB_RES_DIR");
    if (env && *env) {
        snprintf(buf, len, "%s", env);
        return buf;
    }
    /* Portable layout: keep DSView resources under <exe dir>/res so the
     * bundle works on machines that do not have DSView installed. */
    char exe[1024];
    DWORD n = GetModuleFileNameA(NULL, exe, sizeof(exe));
    if (n > 0 && n < sizeof(exe)) {
        char *slash = strrchr(exe, '\\');
        if (slash) {
            *slash = 0;
            /* "res" is the portable bundle; the others find .deps/dsview
             * from dslab/build/ in a source checkout. */
            const char *rel[] = {
                "res",
                "../.deps/dsview/DSView/res",
                "../../.deps/dsview/DSView/res",
                "../../../.deps/dsview/DSView/res",
            };
            for (int i = 0; i < 4; i++) {
                char tmp[1200];
                snprintf(tmp, sizeof(tmp), "%s/%s", exe, rel[i]);
                struct stat st;
                if (stat(tmp, &st) == 0 && (st.st_mode & _S_IFDIR)) {
                    snprintf(buf, len, "%s", tmp);
                    return buf;
                }
            }
        }
    }
    const char *cands[] = {
        "C:\\Program Files\\DSView\\res",
        "C:\\Program Files (x86)\\DSView\\res",
    };
    for (int i = 0; i < 2; i++) {
        struct stat st;
        if (stat(cands[i], &st) == 0 && (st.st_mode & _S_IFDIR)) {
            snprintf(buf, len, "%s", cands[i]);
            return buf;
        }
    }
    buf[0] = 0;
    return buf;
}

char *dslab_make_run_dir(const char *out, char *buf, size_t len) {
    if (out && *out) {
        snprintf(buf, len, "%s", out);
    } else {
        time_t t = time(NULL);
        struct tm tmv;
        localtime_s(&tmv, &t);
        snprintf(buf, len, "runs/%04d%02d%02d-%02d%02d%02d",
                 tmv.tm_year + 1900, tmv.tm_mon + 1, tmv.tm_mday,
                 tmv.tm_hour, tmv.tm_min, tmv.tm_sec);
    }
    /* mkdir -p style for the parent chain */
    char tmp[1200];
    snprintf(tmp, sizeof(tmp), "%s", buf);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/' || *p == '\\') {
            char save = *p;
            *p = 0;
            _mkdir(tmp);
            *p = save;
        }
    }
    if (_mkdir(tmp) != 0) {
        struct stat st;
        if (stat(tmp, &st) != 0 || !(st.st_mode & _S_IFDIR)) return NULL;
    }
    return buf;
}
