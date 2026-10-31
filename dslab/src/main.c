#include "dslab.h"
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define EXIT_USAGE 2
#define EXIT_HW 3

static void usage(void) {
    fprintf(stderr,
        "dslab - DSLogic Plus command line capture\n\n"
        "Usage:\n"
        "  dslab doctor [--json]\n"
        "  dslab devices [--json]\n"
        "  dslab capabilities [--json]\n"
        "  dslab capture once --channels D0[,D1...] [options] [--json]\n"
        "  dslab firmware status [--json]\n"
        "  dslab firmware install --file PATH --pid 0020 [--json]\n\n"
        "Capture options:\n"
        "  --rate RATE          sample rate, e.g. 1M, 10MHz, 1000000\n"
        "  --duration TIME      capture duration, e.g. 10ms, 1s\n"
        "  --samples N          explicit sample limit (overrides duration)\n"
        "  --operation MODE     buffer (default), stream, or internal self-test\n"
        "  --stream             stream mode instead of buffer mode\n"
        "  --mode N             channel mode id (0..3 stream, 21..23 buffer)\n"
        "  --trigger SPEC       e.g. D0=R  or  D0=R,D3=1\n"
        "  --trigger-pos PCT    trigger position in percent (default 0)\n"
        "  --vth VOLTS          threshold voltage, e.g. 1.8\n"
        "  --out DIR            output directory (default runs/<timestamp>)\n"
        "  --res DIR            DSView resource directory with firmware\n"
        "  --timeout MS         hard process timeout in milliseconds\n"
        "  --json               machine readable output on stdout\n"
        "  --verbose            show DSView driver diagnostics on stderr\n");
}

static const char *arg_value(int argc, char **argv, const char *name) {
    for (int i = 0; i < argc - 1; i++)
        if (!strcmp(argv[i], name)) return argv[i + 1];
    return NULL;
}
static int has_flag(int argc, char **argv, const char *name) {
    for (int i = 0; i < argc; i++) if (!strcmp(argv[i], name)) return 1;
    return 0;
}

static void emit_error(bool json, const char *code, const char *msg) {
    if (json) {
        dslab_buf b;
        json_init(&b);
        json_raw(&b, "{\"ok\":false,");
        json_kv_str(&b, "error", code);
        json_raw(&b, ",");
        json_kv_str(&b, "message", msg);
        json_raw(&b, "}\r\n");
        fwrite(b.data, 1, b.len, stdout);
        fflush(stdout);
        json_free(&b);
    } else {
        fprintf(stderr, "dslab: %s\n", msg);
    }
}

static int option_takes_value(const char *name) {
    static const char *const valued[] = {
        "--res", "--channels", "--rate", "--duration", "--samples",
        "--operation", "--mode", "--trigger", "--trigger-pos", "--vth",
        "--out", "--timeout", "--file",
    };
    for (size_t i = 0; i < sizeof(valued) / sizeof(valued[0]); i++)
        if (!strcmp(name, valued[i])) return 1;
    return 0;
}

static int option_known(const char *name) {
    static const char *const flags[] = {"--json", "--verbose", "--stream", "--help"};
    for (size_t i = 0; i < sizeof(flags) / sizeof(flags[0]); i++)
        if (!strcmp(name, flags[i])) return 1;
    return option_takes_value(name);
}

static int validate_options(int argc, char **argv, bool json) {
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "--", 2) != 0) continue;
        if (!option_known(argv[i])) {
            char msg[256];
            snprintf(msg, sizeof(msg), "unknown option '%s' (use --rate for sample rate)", argv[i]);
            emit_error(json, "usage", msg);
            return -1;
        }
        if (option_takes_value(argv[i])) {
            if (i + 1 >= argc || !strncmp(argv[i + 1], "--", 2)) {
                char msg[256];
                snprintf(msg, sizeof(msg), "option '%s' needs a value", argv[i]);
                emit_error(json, "usage", msg);
                return -1;
            }
            i++;
        }
    }
    return 0;
}

static int boot_context(int argc, char **argv, bool json, char *err, size_t errlen) {
    static char res[900];
    const char *p = arg_value(argc, argv, "--res");
    if (p && *p) snprintf(res, sizeof(res), "%s", p);
    else dslab_res_dir(res, sizeof(res));
    if (!res[0]) {
        snprintf(err, errlen, "DSView resource directory not found; pass --res or set DSLAB_RES_DIR");
        return -1;
    }
    if (dslab_lib_init(res, NULL, err, errlen) != 0) return -1;
    return 0;
}

static int cmd_doctor(int argc, char **argv, bool json) {
    char err[512] = {0};
    static char res[900];
    const char *p = arg_value(argc, argv, "--res");
    if (p && *p) snprintf(res, sizeof(res), "%s", p);
    else dslab_res_dir(res, sizeof(res));

    dslab_buf b;
    json_init(&b);
    json_raw(&b, "{");
    if (!res[0]) {
        json_kv_bool(&b, "ok", false);
        json_raw(&b, ",");
        json_kv_str(&b, "error", "res_dir_not_found");
        json_raw(&b, "}\r\n");
        if (json) { fwrite(b.data, 1, b.len, stdout); fflush(stdout); }
        else fprintf(stderr, "dslab: DSView resource dir not found\n");
        json_free(&b);
        return EXIT_HW;
    }
    bool inited = dslab_lib_init(res, NULL, err, sizeof(err)) == 0;
    json_kv_bool(&b, "lib_init", inited);
    json_raw(&b, ",");
    json_kv_str(&b, "resource_dir", res);
    int ndev = 0;
    dslab_device *devs = NULL;
    bool scanned = false;
    if (inited) {
        scanned = dslab_scan(&devs, &ndev, err, sizeof(err)) == 0;
        json_raw(&b, ",");
        json_kv_bool(&b, "device_scan", scanned);
        json_raw(&b, ",");
        json_kv_i64(&b, "device_count", ndev);
    }
    dslab_caps caps;
    bool have_caps = false;
    if (ndev > 0 && dslab_open(-1, NULL, err, sizeof(err)) == 0) {
        if (dslab_caps_get(&caps, err, sizeof(err)) == 0) {
            have_caps = true;
            json_raw(&b, ",");
            json_kv_str(&b, "model", caps.model);
            json_raw(&b, ",");
            json_kv_str(&b, "driver", caps.driver);
            json_raw(&b, ",");
            json_kv_i64(&b, "total_channels", caps.total_channels);
            json_raw(&b, ",");
            json_kv_u64(&b, "hw_depth", caps.hw_depth);
        }
    }
    bool ok = inited && scanned && ndev > 0 && have_caps;
    json_raw(&b, ",");
    json_kv_bool(&b, "ok", ok);
    if (err[0]) { json_raw(&b, ","); json_kv_str(&b, "message", err); }
    json_raw(&b, "}\r\n");
    if (json) { fwrite(b.data, 1, b.len, stdout); fflush(stdout); }
    else {
        printf("resource_dir : %s\n", res);
        printf("lib_init     : %s\n", inited ? "ok" : "failed");
        printf("device_scan  : %s\n", scanned ? "ok" : "failed");
        printf("device_count : %d\n", ndev);
        if (have_caps) {
            printf("model        : %s\n", caps.model);
            printf("channels     : %d\n", caps.total_channels);
        }
        if (err[0]) printf("message      : %s\n", err);
    }
    json_free(&b);
    free(devs);
    return ok ? 0 : EXIT_HW;
}

static int cmd_devices(int argc, char **argv, bool json) {
    char err[512] = {0};
    if (boot_context(argc, argv, json, err, sizeof(err)) != 0) {
        emit_error(json, "init_failed", err);
        return EXIT_HW;
    }
    dslab_device *devs = NULL;
    int n = 0;
    if (dslab_scan(&devs, &n, err, sizeof(err)) != 0) {
        emit_error(json, "scan_failed", err);
        return EXIT_HW;
    }
    dslab_buf b;
    json_init(&b);
    json_raw(&b, "{\"ok\":true,\"devices\":[");
    for (int i = 0; i < n; i++) {
        if (i) json_raw(&b, ",");
        json_raw(&b, "{");
        json_kv_i64(&b, "index", i);
        json_raw(&b, ",");
        json_kv_str(&b, "name", devs[i].name);
        json_raw(&b, ",");
        json_kv_u64(&b, "handle", devs[i].handle);
        json_raw(&b, "}");
    }
    json_raw(&b, "]}\r\n");
    if (json) { fwrite(b.data, 1, b.len, stdout); fflush(stdout); }
    else {
        printf("%d device(s)\n", n);
        for (int i = 0; i < n; i++) printf("  [%d] %s\n", i, devs[i].name);
    }
    json_free(&b);
    free(devs);
    return n > 0 ? 0 : EXIT_HW;
}

static int cmd_capabilities(int argc, char **argv, bool json) {
    char err[512] = {0};
    if (boot_context(argc, argv, json, err, sizeof(err)) != 0) {
        emit_error(json, "init_failed", err);
        return EXIT_HW;
    }
    dslab_device *devs = NULL;
    int n = 0;
    if (dslab_scan(&devs, &n, err, sizeof(err)) != 0 || n == 0) {
        emit_error(json, "no_device", err[0] ? err : "no DSLogic device found");
        return EXIT_HW;
    }
    free(devs);
    if (dslab_open(-1, NULL, err, sizeof(err)) != 0) {
        emit_error(json, "open_failed", err);
        return EXIT_HW;
    }
    dslab_caps caps;
    if (dslab_caps_get(&caps, err, sizeof(err)) != 0) {
        emit_error(json, "caps_failed", err);
        return EXIT_HW;
    }
    dslab_channel *chs = NULL;
    int nch = 0;
    dslab_channels_get(&chs, &nch, err, sizeof(err));
    uint64_t *rates = NULL;
    int nrates = 0;
    dslab_rates_get(&rates, &nrates, err, sizeof(err));

    dslab_buf b;
    json_init(&b);
    json_raw(&b, "{");
    json_kv_str(&b, "model", caps.model);
    json_raw(&b, ",");
    json_kv_str(&b, "driver", caps.driver);
    json_raw(&b, ",");
    json_kv_i64(&b, "total_channels", caps.total_channels);
    json_raw(&b, ",");
    json_kv_u64(&b, "hw_depth_bytes", caps.hw_depth);
    json_raw(&b, ",");
    json_kv_u64(&b, "current_sample_rate", caps.sample_rate);
    json_raw(&b, ",");
    json_kv_u64(&b, "current_sample_limit", caps.sample_limit);
    json_raw(&b, ",");
    json_kv_bool(&b, "stream", caps.stream);
    json_raw(&b, ",");
    json_kv_double(&b, "vth", caps.vth);
    json_raw(&b, ",\"channels\":[");
    for (int i = 0; i < nch; i++) {
        if (i) json_raw(&b, ",");
        json_raw(&b, "{");
        json_kv_i64(&b, "index", chs[i].index);
        json_raw(&b, ",");
        json_kv_str(&b, "name", chs[i].name);
        json_raw(&b, ",");
        json_kv_bool(&b, "enabled", chs[i].enabled);
        json_raw(&b, "}");
    }
    json_raw(&b, "],\"samplerates\":[");
    for (int i = 0; i < nrates; i++) {
        if (i) json_raw(&b, ",");
        char tmp[32]; snprintf(tmp, sizeof(tmp), "%llu", (unsigned long long)rates[i]);
        json_raw(&b, tmp);
    }
    json_raw(&b, "]}\r\n");
    if (json) { fwrite(b.data, 1, b.len, stdout); fflush(stdout); }
    else {
        printf("model         : %s\n", caps.model);
        printf("channels      : %d\n", caps.total_channels);
        printf("sample rate   : %llu\n", (unsigned long long)caps.sample_rate);
        printf("sample limit  : %llu\n", (unsigned long long)caps.sample_limit);
        printf("hw depth      : %llu bytes\n", (unsigned long long)caps.hw_depth);
        printf("samplerates   : %d values\n", nrates);
    }
    json_free(&b);
    free(chs);
    free(rates);
    return 0;
}

static int cmd_capture(int argc, char **argv, bool json) {
    char err[512] = {0};
    const char *chspec = arg_value(argc, argv, "--channels");
    if (!chspec) {
        emit_error(json, "usage", "--channels is required");
        return EXIT_USAGE;
    }
    if (boot_context(argc, argv, json, err, sizeof(err)) != 0) {
        emit_error(json, "init_failed", err);
        return EXIT_HW;
    }
    dslab_device *devs = NULL;
    int n = 0;
    if (dslab_scan(&devs, &n, err, sizeof(err)) != 0) {
        emit_error(json, "scan_failed", err);
        return EXIT_HW;
    }
    free(devs);
    if (n == 0) {
        emit_error(json, "no_device", "no DSLogic device found");
        return EXIT_HW;
    }
    if (dslab_open(-1, NULL, err, sizeof(err)) != 0) {
        emit_error(json, "open_failed", err);
        return EXIT_HW;
    }

    if (dslab_set_device_mode(LOGIC, err, sizeof(err)) != 0) {
        emit_error(json, "config_failed", err);
        return EXIT_HW;
    }
    bool stream = has_flag(argc, argv, "--stream");
    const char *operation_s = arg_value(argc, argv, "--operation");
    int operation = stream ? LO_OP_STREAM : LO_OP_BUFFER;
    if (operation_s) {
        if (!strcmp(operation_s, "buffer")) operation = LO_OP_BUFFER;
        else if (!strcmp(operation_s, "stream")) operation = LO_OP_STREAM;
        else if (!strcmp(operation_s, "internal")) operation = LO_OP_INTEST;
        else {
            emit_error(json, "usage", "--operation must be buffer, stream, or internal");
            return EXIT_USAGE;
        }
    }
    if (dslab_set_operation_mode(operation, err, sizeof(err)) != 0) {
        emit_error(json, "config_failed", err);
        return EXIT_HW;
    }
    const char *mode_s = arg_value(argc, argv, "--mode");
    if (mode_s) {
        if (dslab_set_channel_mode(atoi(mode_s), err, sizeof(err)) != 0) {
            emit_error(json, "config_failed", err);
            return EXIT_HW;
        }
    }
    const char *vth_s = arg_value(argc, argv, "--vth");
    if (vth_s) {
        if (dslab_set_vth(atof(vth_s), err, sizeof(err)) != 0) {
            emit_error(json, "config_failed", err);
            return EXIT_HW;
        }
    }

    int indices[DSLAB_MAX_CHANNELS];
    int count = 0;
    if (dslab_parse_channels(chspec, indices, &count, err, sizeof(err)) != 0 || count == 0) {
        emit_error(json, "usage", err[0] ? err : "no channels selected");
        return EXIT_USAGE;
    }
    dslab_channel *chs = NULL;
    int nch = 0;
    if (dslab_channels_get(&chs, &nch, err, sizeof(err)) != 0) {
        emit_error(json, "caps_failed", err);
        return EXIT_HW;
    }
    for (int i = 0; i < nch; i++) {
        bool want = false;
        for (int k = 0; k < count; k++) if (indices[k] == chs[i].index) want = true;
        if (dslab_enable_channel(chs[i].index, want, err, sizeof(err)) != 0) {
            free(chs);
            emit_error(json, "config_failed", err);
            return EXIT_HW;
        }
        chs[i].enabled = want;
    }

    uint64_t rate = 0;
    const char *rate_s = arg_value(argc, argv, "--rate");
    if (rate_s && dslab_parse_rate(rate_s, &rate) != 0) {
        free(chs);
        emit_error(json, "usage", "invalid --rate");
        return EXIT_USAGE;
    }
    if (rate == 0) {
        dslab_caps caps;
        if (dslab_caps_get(&caps, err, sizeof(err)) != 0) {
            free(chs);
            emit_error(json, "caps_failed", err);
            return EXIT_HW;
        }
        rate = caps.sample_rate;
    }
    if (dslab_set_samplerate(rate, err, sizeof(err)) != 0) {
        free(chs);
        emit_error(json, "config_failed", err);
        return EXIT_HW;
    }

    uint64_t samples = 0;
    const char *samples_s = arg_value(argc, argv, "--samples");
    const char *dur_s = arg_value(argc, argv, "--duration");
    if (samples_s) samples = strtoull(samples_s, NULL, 0);
    else if (dur_s) {
        uint64_t ms = 0;
        if (dslab_parse_duration(dur_s, &ms) != 0) {
            free(chs);
            emit_error(json, "usage", "invalid --duration");
            return EXIT_USAGE;
        }
        samples = (rate * ms) / 1000ull;
    }
    if (samples == 0) samples = rate / 1000ull;
    if (samples < 64) samples = 64;
    if (dslab_set_limit(samples, err, sizeof(err)) != 0) {
        free(chs);
        emit_error(json, "config_failed", err);
        return EXIT_HW;
    }

    const char *trig = arg_value(argc, argv, "--trigger");
    int pos = 0;
    const char *pos_s = arg_value(argc, argv, "--trigger-pos");
    if (pos_s) pos = atoi(pos_s);
    if (pos < 0) pos = 0;
    if (pos > 90) pos = 90;
    if (dslab_set_trigger(trig, pos, err, sizeof(err)) != 0) {
        free(chs);
        emit_error(json, "config_failed", err);
        return EXIT_HW;
    }

    dslab_caps caps;
    if (dslab_caps_get(&caps, err, sizeof(err)) != 0) {
        free(chs);
        emit_error(json, "caps_failed", err);
        return EXIT_HW;
    }

    char dir[900];
    const char *out_s = arg_value(argc, argv, "--out");
    if (out_s && !*out_s) out_s = NULL;
    if (!dslab_make_run_dir(out_s, dir, sizeof(dir))) {
        free(chs);
        emit_error(json, "io_error", "cannot create output directory");
        return EXIT_HW;
    }
    char raw[1200], vcd[1200], meta[1200];
    snprintf(raw, sizeof(raw), "%s/capture.raw", dir);
    snprintf(vcd, sizeof(vcd), "%s/capture.vcd", dir);
    snprintf(meta, sizeof(meta), "%s/meta.json", dir);

    dslab_capture_result res;
    memset(&res, 0, sizeof(res));
    /* Use the configured request, not the pre-configuration capabilities
     * default, for timeout math and metadata. The post-capture readback
     * remains authoritative for the actual device rate. */
    res.sample_rate = rate;
    res.sample_limit = samples;
    res.enabled_channels = count;
    res.total_channels = nch;
    if (trig) snprintf(res.trigger_spec, sizeof(res.trigger_spec), "%s", trig);

    struct timespec t0, t1;
    timespec_get(&t0, TIME_UTC);
    int crc = dslab_capture(&res, raw, err, sizeof(err));
    timespec_get(&t1, TIME_UTC);
    double elapsed = (double)(t1.tv_sec - t0.tv_sec) + (double)(t1.tv_nsec - t0.tv_nsec) / 1e9;

    int vcd_rc = -1;
    if (res.raw_bytes > 0) {
        dslab_caps actual;
        uint64_t actual_rate = rate;
        if (dslab_caps_get(&actual, err, sizeof(err)) == 0 && actual.sample_rate > 0) {
            actual_rate = actual.sample_rate;
            caps = actual;
        }
        res.sample_rate = actual_rate;
        res.sample_limit = caps.sample_limit;
        vcd_rc = dslab_write_vcd(raw, vcd, chs, nch, actual_rate,
                                 res.sample_count, err, sizeof(err));
    }

    dslab_buf b;
    json_init(&b);
    json_raw(&b, "{");
    json_kv_bool(&b, "ok", crc == 0);
    json_raw(&b, ",");
    json_kv_str(&b, "run_dir", dir);
    json_raw(&b, ",");
    json_kv_str(&b, "raw_path", raw);
    json_raw(&b, ",");
    json_kv_str(&b, "vcd_path", vcd_rc == 0 ? vcd : "");
    json_raw(&b, ",");
    json_kv_u64(&b, "sample_rate", caps.sample_rate);
    json_raw(&b, ",");
    json_kv_u64(&b, "sample_limit", caps.sample_limit);
    json_raw(&b, ",");
    json_kv_u64(&b, "sample_count", res.sample_count);
    json_raw(&b, ",");
    json_kv_u64(&b, "raw_bytes", res.raw_bytes);
    json_raw(&b, ",");
    json_kv_i64(&b, "enabled_channels", res.enabled_channels);
    json_raw(&b, ",");
    json_kv_bool(&b, "triggered", res.triggered);
    json_raw(&b, ",");
    json_kv_bool(&b, "overflow", res.overflow);
    json_raw(&b, ",");
    json_kv_bool(&b, "timed_out", res.timed_out);
    json_raw(&b, ",");
    json_kv_u64(&b, "trigger_sample", res.trigger_sample);
    json_raw(&b, ",");
    json_kv_double(&b, "elapsed_s", elapsed);
    if (err[0]) { json_raw(&b, ","); json_kv_str(&b, "message", err); }
    json_raw(&b, "}\r\n");

    FILE *mf = fopen(meta, "w");
    if (mf) { fputs(b.data, mf); fclose(mf); }

    if (json) { fwrite(b.data, 1, b.len, stdout); fflush(stdout); }
    else {
        printf("ok=%s  samples=%llu  bytes=%llu  elapsed=%.3fs\n",
               crc == 0 ? "true" : "false",
               (unsigned long long)res.sample_count,
               (unsigned long long)res.raw_bytes, elapsed);
        printf("raw=%s\n", raw);
        if (vcd_rc == 0) printf("vcd=%s\n", vcd);
        if (err[0]) printf("error=%s\n", err);
    }
    json_free(&b);
    free(chs);
    return crc == 0 ? 0 : EXIT_HW;
}


static int cmd_firmware_status(bool json) {
    char err[512] = {0};
    dslab_firmware_status st;
    if (dslab_firmware_usb_status(&st, err, sizeof(err)) != 0) {
        emit_error(json, "usb_error", err);
        return EXIT_HW;
    }
    dslab_buf b;
    json_init(&b);
    json_raw(&b, "{");
    json_kv_bool(&b, "ok", true);
    json_raw(&b, ",");
    json_kv_bool(&b, "plus_present", st.plus_present);
    json_raw(&b, ",");
    json_kv_bool(&b, "bootloader_present", st.boot_present);
    json_raw(&b, ",");
    json_kv_i64(&b, "bootloader_bus", st.boot_bus);
    json_raw(&b, ",");
    json_kv_i64(&b, "bootloader_address", st.boot_address);
    json_raw(&b, ",");
    json_kv_i64(&b, "bootloader_endpoints", st.boot_endpoints);
    json_raw(&b, ",");
    json_kv_i64(&b, "bootloader_interfaces", st.boot_interfaces);
    json_raw(&b, ",");
    json_kv_i64(&b, "bootloader_config", st.boot_config_value);
    json_raw(&b, ",");
    json_kv_i64(&b, "bootloader_interface_class", st.boot_interface_class);
    json_raw(&b, ",");
    json_kv_str(&b, "bootloader_product", st.boot_product);
    json_raw(&b, "}\r\n");
    if (json) { fwrite(b.data, 1, b.len, stdout); fflush(stdout); }
    else {
        printf("DSLogic Plus (2A0E:0020): %s\n", st.plus_present ? "present" : "absent");
        printf("FX3 bootloader (2A0E:0001): %s", st.boot_present ? "present" : "absent");
        if (st.boot_present)
            printf("  bus=%d address=%d endpoints=%d", st.boot_bus, st.boot_address,
                   st.boot_endpoints);
        printf("\n");
    }
    json_free(&b);
    return 0;
}

static int cmd_firmware_install(int argc, char **argv, bool json) {
    char err[512] = {0};
    const char *path = arg_value(argc, argv, "--file");
    char found[900] = {0};
    if (!dslab_find_firmware(path, 0x0020, found, sizeof(found))) {
        emit_error(json, "firmware_not_found",
                   "DSLogicPlus.fw not found; pass --file or set DSLAB_FIRMWARE");
        return EXIT_HW;
    }
    dslab_firmware_info info;
    if (dslab_firmware_probe(found, &info, err, sizeof(err)) != 0) {
        emit_error(json, "invalid_firmware", err);
        return EXIT_HW;
    }
    dslab_firmware_install_result out;
    int rc = dslab_firmware_install(found, 0x0020, &out, err, sizeof(err));
    dslab_buf b;
    json_init(&b);
    json_raw(&b, "{");
    json_kv_bool(&b, "ok", rc == 0);
    json_raw(&b, ",");
    json_kv_str(&b, "file", found);
    json_raw(&b, ",");
    json_kv_u64(&b, "file_size", info.size);
    json_raw(&b, ",");
    json_kv_str(&b, "file_target", "2A0E:0020");
    json_raw(&b, ",");
    json_kv_bool(&b, "already_running", out.already_running);
    json_raw(&b, ",");
    json_kv_bool(&b, "uploaded", out.uploaded);
    json_raw(&b, ",");
    json_kv_bool(&b, "reenumerated", out.reenumerated);
    if (rc != 0 || err[0]) {
        json_raw(&b, ",");
        json_kv_str(&b, "message", err);
    }
    json_raw(&b, "}\r\n");
    if (json) { fwrite(b.data, 1, b.len, stdout); fflush(stdout); }
    else {
        if (out.already_running) printf("already running as 2A0E:0020\n");
        else if (rc == 0) printf("firmware installed; device re-enumerated as 2A0E:0020\n");
        else fprintf(stderr, "dslab: %s\n", err);
    }
    json_free(&b);
    return rc == 0 ? 0 : EXIT_HW;
}

int main(int argc, char **argv) {
    if (argc < 2) { usage(); return EXIT_USAGE; }
    bool json = has_flag(argc, argv, "--json");
    bool verbose = has_flag(argc, argv, "--verbose");
    if (validate_options(argc, argv, json) != 0) return EXIT_USAGE;
    if (has_flag(argc, argv, "--help")) { usage(); return 0; }
    if (!verbose) ds_log_level(0);
    const char *cmd = argv[1];
    int rc;
    if (!strcmp(cmd, "doctor")) rc = cmd_doctor(argc, argv, json);
    else if (!strcmp(cmd, "devices")) rc = cmd_devices(argc, argv, json);
    else if (!strcmp(cmd, "capabilities")) rc = cmd_capabilities(argc, argv, json);
    else if (!strcmp(cmd, "firmware")) {
        if (argc < 3) { emit_error(json, "usage", "use: dslab firmware status|install"); return EXIT_USAGE; }
        if (!strcmp(argv[2], "status")) rc = cmd_firmware_status(json);
        else if (!strcmp(argv[2], "install")) rc = cmd_firmware_install(argc, argv, json);
        else { emit_error(json, "usage", "use: dslab firmware status|install"); return EXIT_USAGE; }
    }
    else if (!strcmp(cmd, "capture")) {
        if (argc < 3 || strcmp(argv[2], "once")) {
            emit_error(json, "usage", "use: dslab capture once ...");
            return EXIT_USAGE;
        }
        rc = cmd_capture(argc, argv, json);
    } else {
        usage();
        return EXIT_USAGE;
    }
    dslab_lib_exit();
    return rc;
}
