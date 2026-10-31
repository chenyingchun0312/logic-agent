#include "dslab.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

typedef struct {
    GMutex lock;
    GCond cond;
    FILE *raw;
    uint64_t bytes;
    uint64_t sample_count;
    int enabled;
    int total_channels;
    bool saw_trigger;
    bool end;
    bool overflow;
    bool error;
    uint32_t trigger_real_pos;
    uint32_t trigger_status;
    char err[256];
} capture_state;

static capture_state *g_cap;

static void set_err(char *err, size_t errlen, const char *fmt, ...) {
    if (!err || errlen == 0) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err, errlen, fmt, ap);
    va_end(ap);
}

static void datafeed_cb(const struct sr_dev_inst *sdi, const struct sr_datafeed_packet *packet) {
    (void)sdi;
    capture_state *s = g_cap;
    if (!s || !packet) return;

    g_mutex_lock(&s->lock);
    switch (packet->type) {
    case SR_DF_LOGIC: {
        const struct sr_datafeed_logic *logic = packet->payload;
        if (logic && logic->data && logic->length && s->raw) {
            size_t n = fwrite(logic->data, 1, (size_t)logic->length, s->raw);
            s->bytes += n;
            s->sample_count += ((uint64_t)logic->length * 8) / (uint64_t)(s->enabled ? s->enabled : 1);
            if (n != logic->length) {
                s->error = true;
                snprintf(s->err, sizeof(s->err), "failed writing raw capture data");
            }
        }
        break;
    }
    case SR_DF_TRIGGER: {
        const struct ds_trigger_pos *tp = packet->payload;
        if (tp) {
            s->saw_trigger = true;
            s->trigger_real_pos = tp->real_pos;
            s->trigger_status = tp->status;
        }
        break;
    }
    case SR_DF_OVERFLOW:
        s->overflow = true;
        break;
    case SR_DF_END:
        s->end = true;
        g_cond_broadcast(&s->cond);
        break;
    default:
        break;
    }
    g_mutex_unlock(&s->lock);
}

static void event_cb(int event) {
    capture_state *s = g_cap;
    if (!s) return;
    g_mutex_lock(&s->lock);
    if (event == DS_EV_COLLECT_TASK_END) {
        s->end = true;
        g_cond_broadcast(&s->cond);
    } else if (event == DS_EV_COLLECT_TASK_END_BY_ERROR ||
               event == DS_EV_COLLECT_TASK_END_BY_DETACHED) {
        s->error = true;
        s->end = true;
        if (!s->err[0]) snprintf(s->err, sizeof(s->err), "capture ended with event %d", event);
        g_cond_broadcast(&s->cond);
    }
    g_mutex_unlock(&s->lock);
}

static uint64_t mono_ms(void) {
    return (uint64_t)g_get_monotonic_time() / 1000u;
}

int dslab_capture(dslab_capture_result *res, const char *raw_path,
                  char *err, size_t errlen) {
    memset(res, 0, sizeof(*res));
    if (!raw_path || !*raw_path) {
        set_err(err, errlen, "raw output path is required");
        return -1;
    }

    capture_state *s = calloc(1, sizeof(*s));
    if (!s) {
        set_err(err, errlen, "out of memory");
        return -1;
    }
    g_mutex_init(&s->lock);
    g_cond_init(&s->cond);
    s->raw = fopen(raw_path, "wb");
    if (!s->raw) {
        set_err(err, errlen, "cannot open raw output '%s'", raw_path);
        g_mutex_clear(&s->lock);
        g_cond_clear(&s->cond);
        free(s);
        return -1;
    }

    dslab_channel *chs = NULL;
    int nch = 0;
    if (dslab_channels_get(&chs, &nch, err, errlen) != 0) {
        fclose(s->raw);
        g_mutex_clear(&s->lock);
        g_cond_clear(&s->cond);
        free(s);
        return -1;
    }
    for (int i = 0; i < nch; i++) {
        if (chs[i].enabled) s->enabled++;
    }
    s->total_channels = nch;
    free(chs);
    if (s->enabled == 0) {
        set_err(err, errlen, "no channels enabled");
        fclose(s->raw);
        g_mutex_clear(&s->lock);
        g_cond_clear(&s->cond);
        free(s);
        return -1;
    }

    g_cap = s;
    ds_set_datafeed_callback(datafeed_cb);
    ds_set_event_callback(event_cb);

    int rc = ds_start_collect();
    if (rc != SR_OK) {
        set_err(err, errlen, "ds_start_collect failed: %s (%d)", sr_error_str(rc), rc);
        g_cap = NULL;
        fclose(s->raw);
        g_mutex_clear(&s->lock);
        g_cond_clear(&s->cond);
        free(s);
        return -1;
    }

    /* Buffer mode ends on its own; stream mode or a stuck trigger needs a stop.
       Allow the expected acquisition duration plus 30 s of slack. */
    uint64_t expect_ms = 0;
    if (res->sample_limit && res->sample_rate)
        expect_ms = res->sample_limit * 1000ull / res->sample_rate;
    uint64_t deadline = mono_ms() + 30000ull + expect_ms;

    g_mutex_lock(&s->lock);
    while (!s->end && !s->error) {
        if (mono_ms() >= deadline) {
            res->timed_out = true;
            break;
        }
        gint64 wake = g_get_monotonic_time() + 100 * G_TIME_SPAN_MILLISECOND;
        g_cond_wait_until(&s->cond, &s->lock, wake);
    }
    bool need_stop = !s->end;
    g_mutex_unlock(&s->lock);

    if (need_stop || ds_is_collecting())
        ds_stop_collect();

    bool had_error;
    g_mutex_lock(&s->lock);
    res->raw_bytes = s->bytes;
    res->sample_count = s->sample_count;
    res->enabled_channels = s->enabled;
    res->total_channels = s->total_channels;
    res->triggered = s->saw_trigger && ((s->trigger_status & 0x01) != 0);
    res->overflow = s->overflow;
    res->trigger_real_pos = s->trigger_real_pos;
    res->complete = !s->error && !res->timed_out;
    had_error = s->error;
    if (s->error && err && errlen)
        snprintf(err, errlen, "%s", s->err[0] ? s->err : "capture error");
    uint32_t trig_real = s->trigger_real_pos;
    g_mutex_unlock(&s->lock);

    if (res->triggered && s->enabled > 0)
        res->trigger_sample = (uint64_t)trig_real / (uint64_t)s->enabled;

    fclose(s->raw);
    g_cap = NULL;
    g_mutex_clear(&s->lock);
    g_cond_clear(&s->cond);
    free(s);
    return (had_error || res->timed_out) ? -1 : 0;
}

/* ---------- VCD export ---------- */

static uint8_t *read_all(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long n = ftell(f);
    if (n < 0) { fclose(f); return NULL; }
    rewind(f);
    uint8_t *p = malloc((size_t)n ? (size_t)n : 1);
    if (!p) { fclose(f); return NULL; }
    size_t got = fread(p, 1, (size_t)n, f);
    fclose(f);
    if (got != (size_t)n) { free(p); return NULL; }
    *len = got;
    return p;
}

int dslab_write_vcd(const char *raw_path, const char *vcd_path,
                    const dslab_channel *channels, int total_channels,
                    uint64_t sample_rate, uint64_t sample_count,
                    char *err, size_t errlen) {
    size_t raw_len = 0;
    uint8_t *raw = read_all(raw_path, &raw_len);
    if (!raw) {
        set_err(err, errlen, "cannot read raw '%s'", raw_path);
        return -1;
    }
    FILE *out = fopen(vcd_path, "w");
    if (!out) {
        free(raw);
        set_err(err, errlen, "cannot open VCD '%s'", vcd_path);
        return -1;
    }

    const dslab_channel *en_ch[DSLAB_MAX_CHANNELS];
    int en = 0;
    for (int i = 0; i < total_channels; i++) {
        if (channels[i].enabled && en < DSLAB_MAX_CHANNELS)
            en_ch[en++] = &channels[i];
    }
    if (en <= 0) {
        free(raw); fclose(out);
        set_err(err, errlen, "no enabled channels for VCD");
        return -1;
    }

    uint64_t frames = sample_count / 64ull;
    if (sample_count % 64ull) frames++;
    if (frames * (uint64_t)en * 8ull > raw_len)
        sample_count = (raw_len / ((uint64_t)en * 8ull)) * 64ull;

    time_t t = time(NULL);
    struct tm tmv;
    localtime_s(&tmv, &t);
    fprintf(out, "$date %04d-%02d-%02d %02d:%02d:%02d $end\n",
            tmv.tm_year + 1900, tmv.tm_mon + 1, tmv.tm_mday,
            tmv.tm_hour, tmv.tm_min, tmv.tm_sec);
    fprintf(out, "$version dslab 0.1 $end\n");
    fprintf(out, "$comment DSLogic Plus, %d/%d channels, %llu Hz $end\n",
            en, total_channels, (unsigned long long)sample_rate);
    fprintf(out, "$timescale 1ns $end\n");
    fprintf(out, "$scope module logic $end\n");
    for (int i = 0; i < en; i++)
        fprintf(out, "$var wire 1 %c %s $end\n", (char)('!' + i), en_ch[i]->name);
    fprintf(out, "$upscope $end\n$enddefinitions $end\n");

    if (sample_rate == 0) sample_rate = 1;
    const double ns_per_sample = 1e9 / (double)sample_rate;
    int prev[DSLAB_MAX_CHANNELS];
    for (int i = 0; i < en; i++) prev[i] = -1;
    uint64_t last_ts = ~0ull;
    fprintf(out, "#0\n");

    for (uint64_t s = 0; s < sample_count; s++) {
        uint64_t frame = s / 64ull;
        uint64_t bit = s % 64ull;
        bool changed = false;
        for (int c = 0; c < en; c++) {
            uint64_t chunk = frame * (uint64_t)en + (uint64_t)c;
            uint64_t off = chunk * 8ull + bit / 8ull;
            if (off >= raw_len) break;
            int v = (raw[off] >> (bit % 8ull)) & 1;
            if (v != prev[c]) {
                uint64_t ts = (uint64_t)llround((double)s * ns_per_sample);
                if (ts != last_ts) { fprintf(out, "#%llu\n", (unsigned long long)ts); last_ts = ts; }
                fprintf(out, "%d%c\n", v, (char)('!' + c));
                prev[c] = v;
            }
            changed = true;
        }
        if (!changed) break;
    }

    free(raw);
    fclose(out);
    return 0;
}
