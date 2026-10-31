#include "dslab.h"
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <ctype.h>

static char g_err[512];
static int g_inited = 0;

static void set_err(char *err, size_t errlen, const char *fmt, ...) {
    if (!err || errlen == 0) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err, errlen, fmt, ap);
    va_end(ap);
}

int dslab_lib_init(const char *res_dir, const char *user_dir, char *err, size_t errlen) {
    if (g_inited) return 0;
    if (!res_dir || !*res_dir) {
        set_err(err, errlen, "firmware resource directory is required");
        return -1;
    }
    ds_set_firmware_resource_dir(res_dir);
    if (user_dir && *user_dir) ds_set_user_data_dir(user_dir);
    int rc = ds_lib_init();
    if (rc != SR_OK) {
        set_err(err, errlen, "ds_lib_init failed: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    g_inited = 1;
    return 0;
}

void dslab_lib_exit(void) {
    if (g_inited) {
        ds_lib_exit();
        g_inited = 0;
    }
}

/* Same filter as dslab_scan(): unnamed entries and the Demo Device are hidden. */
static int is_real_device(const struct ds_device_base_info *d) {
    return d->name[0] && strncmp(d->name, "Demo Device", 11) != 0;
}

/* Library-list index of our index-th real device, or -1. */
static int library_index_of(int index) {
    struct ds_device_base_info *list = NULL;
    int n = 0;
    if (ds_get_device_list(&list, &n) != SR_OK) return -1;
    int found = -1;
    for (int i = 0, real = 0; i < n; i++) {
        if (!is_real_device(&list[i])) continue;
        if (real++ == index) { found = i; break; }
    }
    free(list);
    return found;
}

int dslab_scan(dslab_device **out, int *count, char *err, size_t errlen) {
    *out = NULL;
    *count = 0;
    struct ds_device_base_info *list = NULL;
    int n = 0;
    int rc = ds_get_device_list(&list, &n);
    if (rc != SR_OK) {
        set_err(err, errlen, "ds_get_device_list failed: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    if (n <= 0) return 0;
    dslab_device *arr = calloc((size_t)n, sizeof(*arr));
    if (!arr) {
        free(list);
        set_err(err, errlen, "out of memory");
        return -1;
    }
    int kept = 0;
    for (int i = 0; i < n; i++) {
        if (!is_real_device(&list[i])) continue;
        arr[kept].handle = list[i].handle;
        snprintf(arr[kept].name, sizeof(arr[kept].name), "%s", list[i].name);
        arr[kept].dev_type = -1;
        arr[kept].is_usb = strstr(list[i].name, "DSLogic") != NULL;
        arr[kept].driver[0] = 0;
        kept++;
    }
    free(list);
    if (kept == 0) {
        free(arr);
        return 0;
    }
    *out = arr;
    *count = kept;
    return 0;
}

int dslab_open(int index, dslab_device *dev, char *err, size_t errlen) {
    int rc = SR_ERR;
    dslab_device *list = NULL;
    int n = 0;
    if (dslab_scan(&list, &n, err, errlen) != 0) return -1;
    if (n == 0) {
        free(list);
        set_err(err, errlen, "no real DSLogic device found (demo devices are ignored)");
        return -1;
    }
    if (index < 0) index = 0;
    if (index >= n) {
        free(list);
        set_err(err, errlen, "device index %d out of range (%d real devices)", index, n);
        return -1;
    }
    char selected_name[160];
    snprintf(selected_name, sizeof(selected_name), "%s", list[index].name);
    free(list);
    /* Our index skips the built-in Demo Device, libsigrok4DSL's does not
     * (its list is typically [Demo Device, DSLogic ...]). Map our index to
     * the library's by walking the library list in the same order and
     * counting only the devices dslab_scan() keeps. Activating by library
     * index avoids holding a handle copied out of ds_get_device_list(). */
    int lib_index = library_index_of(index);
    if (lib_index < 0) {
        set_err(err, errlen, "device %d (\"%s\") disappeared before it could be opened",
                index, selected_name);
        return -1;
    }
    rc = ds_active_device_by_index(lib_index);
    if (rc != SR_OK) {
        set_err(err, errlen, "ds_active_device_by_index(%d) failed: %s (%d), last_error=%d",
                lib_index, sr_error_str(rc), rc, ds_get_last_error());
        return -1;
    }
    struct ds_device_full_info info;
    memset(&info, 0, sizeof(info));
    rc = ds_get_actived_device_info(&info);
    if (rc != SR_OK) {
        set_err(err, errlen, "ds_get_actived_device_info failed: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    if (strcmp(info.name, selected_name) != 0) {
        set_err(err, errlen, "activated unexpected device \"%s\" (wanted \"%s\")",
                info.name, selected_name);
        return -1;
    }
    if (dev) {
        dev->handle = info.handle;
        snprintf(dev->name, sizeof(dev->name), "%s", info.name);
        snprintf(dev->driver, sizeof(dev->driver), "%s", info.driver_name);
        dev->dev_type = info.dev_type;
        dev->is_usb = (info.dev_type == DEV_TYPE_USB);
    }
    return 0;
}

static int get_u64(int key, uint64_t *v) {
    GVariant *g = NULL;
    int rc = ds_get_actived_device_config(NULL, NULL, key, &g);
    if (rc != SR_OK || !g) return -1;
    *v = g_variant_get_uint64(g);
    g_variant_unref(g);
    return 0;
}
static int get_i16(int key, int *v) {
    GVariant *g = NULL;
    int rc = ds_get_actived_device_config(NULL, NULL, key, &g);
    if (rc != SR_OK || !g) return -1;
    *v = g_variant_get_int16(g);
    g_variant_unref(g);
    return 0;
}
static int get_bool(int key, bool *v) {
    GVariant *g = NULL;
    int rc = ds_get_actived_device_config(NULL, NULL, key, &g);
    if (rc != SR_OK || !g) return -1;
    *v = g_variant_get_boolean(g);
    g_variant_unref(g);
    return 0;
}
static int set_u64(int key, uint64_t v) {
    GVariant *g = g_variant_new_uint64(v);
    return ds_set_actived_device_config(NULL, NULL, key, g);
}
static int set_i16(int key, int v) {
    GVariant *g = g_variant_new_int16((int16_t)v);
    return ds_set_actived_device_config(NULL, NULL, key, g);
}
static int set_bool(int key, bool v) {
    GVariant *g = g_variant_new_boolean(v);
    return ds_set_actived_device_config(NULL, NULL, key, g);
}

int dslab_caps_get(dslab_caps *caps, char *err, size_t errlen) {
    memset(caps, 0, sizeof(*caps));
    struct ds_device_full_info info;
    memset(&info, 0, sizeof(info));
    if (ds_get_actived_device_info(&info) == SR_OK) {
        snprintf(caps->model, sizeof(caps->model), "%s", info.name);
        snprintf(caps->driver, sizeof(caps->driver), "%s", info.driver_name);
    }
    int v = 0;
    uint64_t u = 0;
    if (get_i16(SR_CONF_TOTAL_CH_NUM, &v) == 0) caps->total_channels = v;
    if (get_i16(SR_CONF_CHANNEL_MODE, &v) == 0) caps->channel_mode = v;
    if (get_bool(SR_CONF_STREAM, &caps->stream) != 0) caps->stream = false;
    if (get_u64(SR_CONF_SAMPLERATE, &u) == 0) caps->sample_rate = u;
    if (get_u64(SR_CONF_LIMIT_SAMPLES, &u) == 0) caps->sample_limit = u;
    if (get_u64(SR_CONF_HW_DEPTH, &u) == 0) caps->hw_depth = u;
    if (get_i16(SR_CONF_THRESHOLD, &v) == 0) caps->threshold = v;
    GVariant *g = NULL;
    if (ds_get_actived_device_config(NULL, NULL, SR_CONF_VTH, &g) == SR_OK && g) {
        caps->vth = g_variant_get_double(g);
        g_variant_unref(g);
    }
    if (caps->total_channels == 0) {
        set_err(err, errlen, "device did not report a channel count");
        return -1;
    }
    return 0;
}

int dslab_channels_get(dslab_channel **out, int *count, char *err, size_t errlen) {
    GSList *channels = ds_get_actived_device_channels();
    int n = 0;
    for (GSList *l = channels; l; l = l->next) n++;
    if (n == 0) {
        set_err(err, errlen, "device did not report any channels");
        return -1;
    }
    dslab_channel *arr = calloc((size_t)n, sizeof(*arr));
    if (!arr) {
        set_err(err, errlen, "out of memory");
        return -1;
    }
    int i = 0;
    for (GSList *l = channels; l; l = l->next) {
        struct sr_channel *ch = l->data;
        arr[i].index = ch->index;
        arr[i].enabled = ch->enabled;
        snprintf(arr[i].name, sizeof(arr[i].name), "%s", ch->name ? ch->name : "");
        i++;
    }
    *out = arr;
    *count = n;
    return 0;
}

int dslab_set_samplerate(uint64_t rate, char *err, size_t errlen) {
    int rc = set_u64(SR_CONF_SAMPLERATE, rate);
    if (rc != SR_OK) {
        set_err(err, errlen, "set samplerate failed: %s (%d), last_error=%d",
                sr_error_str(rc), rc, ds_get_last_error());
        return -1;
    }
    return 0;
}

int dslab_set_limit(uint64_t samples, char *err, size_t errlen) {
    int rc = set_u64(SR_CONF_LIMIT_SAMPLES, samples);
    if (rc != SR_OK) {
        set_err(err, errlen, "set sample limit failed: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    return 0;
}

int dslab_set_operation(bool stream, char *err, size_t errlen) {
    return dslab_set_operation_mode(stream ? LO_OP_STREAM : LO_OP_BUFFER, err, errlen);
}

int dslab_set_operation_mode(int mode, char *err, size_t errlen) {
    int rc = set_i16(SR_CONF_OPERATION_MODE, mode);
    if (rc != SR_OK) {
        set_err(err, errlen, "set operation mode %d failed: %s (%d)", mode,
                sr_error_str(rc), rc);
        return -1;
    }
    return 0;
}

int dslab_set_device_mode(int mode, char *err, size_t errlen) {
    int rc = set_i16(SR_CONF_DEVICE_MODE, mode);
    if (rc != SR_OK) {
        set_err(err, errlen, "set device mode %d failed: %s (%d)", mode,
                sr_error_str(rc), rc);
        return -1;
    }
    return 0;
}

int dslab_set_channel_mode(int mode, char *err, size_t errlen) {
    int rc = set_i16(SR_CONF_CHANNEL_MODE, mode);
    if (rc != SR_OK) {
        set_err(err, errlen, "set channel mode failed: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    return 0;
}

int dslab_enable_channel(int index, bool enable, char *err, size_t errlen) {
    int rc = ds_enable_device_channel_index(index, enable ? TRUE : FALSE);
    if (rc != SR_OK) {
        set_err(err, errlen, "enable channel %d failed: %s (%d)", index, sr_error_str(rc), rc);
        return -1;
    }
    return 0;
}


int dslab_rates_get(uint64_t **out, int *count, char *err, size_t errlen) {
    *out = NULL;
    *count = 0;
    GVariant *g = NULL;
    int rc = ds_get_actived_device_config_list(NULL, SR_CONF_SAMPLERATE, &g);
    if (rc != SR_OK || !g) {
        set_err(err, errlen, "cannot query samplerates: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    GVariant *arr = g_variant_lookup_value(g, "samplerates", G_VARIANT_TYPE("at"));
    if (!arr) {
        g_variant_unref(g);
        set_err(err, errlen, "samplerates missing from config list");
        return -1;
    }
    gsize n = g_variant_n_children(arr);
    uint64_t *rates = calloc(n ? n : 1, sizeof(uint64_t));
    if (!rates) {
        g_variant_unref(arr);
        g_variant_unref(g);
        set_err(err, errlen, "out of memory");
        return -1;
    }
    for (gsize i = 0; i < n; i++) {
        GVariant *item = g_variant_get_child_value(arr, i);
        rates[i] = g_variant_get_uint64(item);
        g_variant_unref(item);
    }
    g_variant_unref(arr);
    g_variant_unref(g);
    *out = rates;
    *count = (int)n;
    return 0;
}

int dslab_set_vth(double vth, char *err, size_t errlen) {
    GVariant *g = g_variant_new_double(vth);
    int rc = ds_set_actived_device_config(NULL, NULL, SR_CONF_VTH, g);
    if (rc != SR_OK) {
        set_err(err, errlen, "set VTH failed: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    return 0;
}

int dslab_set_threshold(int level, char *err, size_t errlen) {
    int rc = set_i16(SR_CONF_THRESHOLD, level);
    if (rc != SR_OK) {
        set_err(err, errlen, "set threshold failed: %s (%d)", sr_error_str(rc), rc);
        return -1;
    }
    return 0;
}

/* spec is a comma separated list of Dn=EDGE, e.g. D0=R,D3=F.
   EDGE is R(rising), F(falling), C(change), 1, 0. */
int dslab_set_trigger(const char *spec, int position_pct, char *err, size_t errlen) {
    ds_trigger_reset();
    if (!spec || !*spec) return 0;
    char *copy = strdup(spec);
    if (!copy) { set_err(err, errlen, "out of memory"); return -1; }
    char *save = NULL;
    int any = 0;
    for (char *tok = strtok_s(copy, ",", &save); tok; tok = strtok_s(NULL, ",", &save)) {
        while (*tok == ' ' || *tok == '\t') tok++;
        int idx = -1;
        char code = 0;
        if ((tok[0] == 'D' || tok[0] == 'd') && tok[1]) {
            char *end = NULL;
            idx = (int)strtol(tok + 1, &end, 10);
            if (end && *end == '=') code = end[1];
        } else {
            char *eq = strchr(tok, '=');
            if (eq) {
                idx = (int)strtol(tok, NULL, 10);
                code = eq[1];
            }
        }
        if (idx < 0 || idx >= DSLAB_MAX_CHANNELS) {
            set_err(err, errlen, "bad trigger token '%s'", tok);
            free(copy);
            return -1;
        }
        code = (char)toupper((unsigned char)code);
        if (code != 'R' && code != 'F' && code != 'C' && code != '1' && code != '0') {
            set_err(err, errlen, "bad trigger edge in '%s' (use R,F,C,1,0)", tok);
            free(copy);
            return -1;
        }
        ds_trigger_probe_set((uint16_t)idx, (unsigned char)code, 'X');
        any = 1;
    }
    free(copy);
    if (any) {
        ds_trigger_set_mode(SIMPLE_TRIGGER);
        ds_trigger_set_stage(0);
        ds_trigger_set_pos((uint16_t)position_pct);
        ds_trigger_set_en(1);
    }
    return 0;
}
