#ifndef DSLAB_H
#define DSLAB_H

#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>

#include <glib.h>
#include "libsigrok.h"

#define DSLAB_MAX_CHANNELS 32

typedef struct {
    char path[900];
    uint64_t size;
    uint16_t vid;
    uint16_t pid;
    uint64_t descriptor_offset;
    bool is_dslogic_plus;
} dslab_firmware_info;

typedef struct {
    bool plus_present;
    bool boot_present;
    int boot_bus;
    int boot_address;
    int boot_interfaces;
    int boot_config_value;
    int boot_interface_class;
    char boot_product[128];
    int boot_endpoints;
    int boot_bulk_in;
    int boot_bulk_out;
} dslab_firmware_status;

typedef struct {
    uint16_t file_vid;
    uint16_t file_pid;
    uint64_t file_size;
    bool already_running;
    bool uploaded;
    uint64_t uploaded_bytes;
    bool reenumerated;
} dslab_firmware_install_result;

int dslab_firmware_probe(const char *path, dslab_firmware_info *info,
                         char *err, size_t errlen);
int dslab_firmware_usb_status(dslab_firmware_status *st, char *err, size_t errlen);
int dslab_firmware_install(const char *path, uint16_t expect_pid,
                           dslab_firmware_install_result *out,
                           char *err, size_t errlen);
const char *dslab_find_firmware(const char *explicit_path, uint16_t pid,
                                char *buf, size_t len);

typedef struct {
    uint64_t handle;
    char name[160];
    char driver[32];
    int dev_type;
    bool is_usb;
} dslab_device;

typedef struct {
    char model[160];
    char driver[32];
    char firmware[64];
    int total_channels;
    int channel_mode;
    bool stream;
    uint64_t sample_rate;
    uint64_t sample_limit;
    uint64_t hw_depth;
    double vth;
    int threshold;
} dslab_caps;

typedef struct {
    bool enabled;
    int index;
    char name[32];
} dslab_channel;

typedef struct {
    char *data;
    size_t len;
    size_t cap;
} dslab_buf;

void json_init(dslab_buf *b);
void json_free(dslab_buf *b);
void json_raw(dslab_buf *b, const char *s);
void json_str(dslab_buf *b, const char *s);
void json_kv_str(dslab_buf *b, const char *k, const char *v);
void json_kv_u64(dslab_buf *b, const char *k, uint64_t v);
void json_kv_i64(dslab_buf *b, const char *k, int64_t v);
void json_kv_bool(dslab_buf *b, const char *k, bool v);
void json_kv_double(dslab_buf *b, const char *k, double v);

int dslab_lib_init(const char *res_dir, const char *user_dir, char *err, size_t errlen);
void dslab_lib_exit(void);
int dslab_scan(dslab_device **out, int *count, char *err, size_t errlen);
int dslab_open(int index, dslab_device *dev, char *err, size_t errlen);
int dslab_caps_get(dslab_caps *caps, char *err, size_t errlen);
int dslab_channels_get(dslab_channel **out, int *count, char *err, size_t errlen);
int dslab_rates_get(uint64_t **out, int *count, char *err, size_t errlen);
int dslab_set_samplerate(uint64_t rate, char *err, size_t errlen);
int dslab_set_limit(uint64_t samples, char *err, size_t errlen);
int dslab_set_device_mode(int mode, char *err, size_t errlen);
int dslab_set_operation(bool stream, char *err, size_t errlen);
int dslab_set_operation_mode(int mode, char *err, size_t errlen);
int dslab_set_channel_mode(int mode, char *err, size_t errlen);
int dslab_enable_channel(int index, bool enable, char *err, size_t errlen);
int dslab_set_vth(double vth, char *err, size_t errlen);
int dslab_set_threshold(int level, char *err, size_t errlen);
int dslab_set_trigger(const char *spec, int position_pct, char *err, size_t errlen);

typedef struct {
    uint64_t sample_rate;
    uint64_t sample_limit;
    uint64_t raw_bytes;
    uint64_t sample_count;
    int enabled_channels;
    int total_channels;
    bool triggered;
    bool complete;
    bool overflow;
    bool timed_out;
    uint32_t trigger_real_pos;
    uint64_t trigger_sample;
    char trigger_spec[128];
} dslab_capture_result;

int dslab_capture(dslab_capture_result *res, const char *raw_path,
                  char *err, size_t errlen);

int dslab_write_vcd(const char *raw_path, const char *vcd_path,
                    const dslab_channel *channels, int total_channels,
                    uint64_t sample_rate, uint64_t sample_count,
                    char *err, size_t errlen);

/* config.c */
int dslab_parse_rate(const char *s, uint64_t *out);
int dslab_parse_duration(const char *s, uint64_t *out_ms);
int dslab_parse_channels(const char *s, int *indices, int *count,
                         char *err, size_t errlen);
const char *dslab_res_dir(char *buf, size_t len);
char *dslab_make_run_dir(const char *out, char *buf, size_t len);

#endif
