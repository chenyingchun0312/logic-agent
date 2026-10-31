#include "dslab.h"
#include <libusb.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

#define DS_VID 0x2A0E
#define DS_PLUS_PID 0x0020
#define FX3_BOOT_PID 0x0001

static void fw_err(char *err, size_t errlen, const char *fmt, ...) {
    if (!err || errlen == 0) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err, errlen, fmt, ap);
    va_end(ap);
}

static uint16_t rd16(const unsigned char *p) {
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static int find_descriptor(const unsigned char *buf, size_t len,
                           uint16_t *vid, uint16_t *pid, size_t *offset) {
    int found = -1;
    for (size_t i = 0; i + 18 <= len; i++) {
        if (buf[i] != 18 || buf[i + 1] != 1) continue;
        uint16_t v = rd16(buf + i + 8);
        uint16_t p = rd16(buf + i + 10);
        if (v != DS_VID) continue;
        if (found >= 0) return -2;
        found = 0;
        if (vid) *vid = v;
        if (pid) *pid = p;
        if (offset) *offset = i;
    }
    return found;
}

int dslab_firmware_probe(const char *path, dslab_firmware_info *info,
                         char *err, size_t errlen) {
    memset(info, 0, sizeof(*info));
    FILE *f = fopen(path, "rb");
    if (!f) {
        fw_err(err, errlen, "cannot open firmware '%s'", path);
        return -1;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        fw_err(err, errlen, "cannot seek firmware '%s'", path);
        return -1;
    }
    long end = ftell(f);
    if (end <= 0 || end > 4 * 1024 * 1024) {
        fclose(f);
        fw_err(err, errlen, "invalid firmware size %ld", end);
        return -1;
    }
    rewind(f);
    size_t len = (size_t)end;
    unsigned char *buf = malloc(len);
    if (!buf) {
        fclose(f);
        fw_err(err, errlen, "out of memory");
        return -1;
    }
    if (fread(buf, 1, len, f) != len) {
        free(buf);
        fclose(f);
        fw_err(err, errlen, "cannot read firmware '%s'", path);
        return -1;
    }
    fclose(f);

    size_t off = 0;
    uint16_t vid = 0, pid = 0;
    int rc = find_descriptor(buf, len, &vid, &pid, &off);
    free(buf);
    if (rc == -2) {
        fw_err(err, errlen, "firmware contains multiple DSLogic USB descriptors");
        return -1;
    }
    if (rc != 0) {
        fw_err(err, errlen, "no DSLogic USB device descriptor found in firmware");
        return -1;
    }
    snprintf(info->path, sizeof(info->path), "%s", path);
    info->size = (uint64_t)len;
    info->vid = vid;
    info->pid = pid;
    info->descriptor_offset = (uint64_t)off;
    info->is_dslogic_plus = (vid == DS_VID && pid == DS_PLUS_PID);
    return 0;
}

static const char *basename_of(const char *p) {
    if (!p) return "";
    const char *s = p;
    for (const char *q = p; *q; q++)
        if (*q == '/' || *q == '\\') s = q + 1;
    return s;
}

const char *dslab_find_firmware(const char *explicit_path, uint16_t pid,
                                char *buf, size_t len) {
    const char *name = pid == DS_PLUS_PID ? "DSLogicPlus.fw" : NULL;
    if (!name) return NULL;
    if (explicit_path && *explicit_path) {
        FILE *t = fopen(explicit_path, "rb");
        if (!t) return NULL;
        fclose(t);
        snprintf(buf, len, "%s", explicit_path);
        return buf;
    }
    const char *env = getenv("DSLAB_FIRMWARE");
    if (env && *env) {
        if (strcmp(basename_of(env), name) == 0) {
            FILE *t = fopen(env, "rb");
            if (t) {
                fclose(t);
                snprintf(buf, len, "%s", env);
                return buf;
            }
        }
    }
    static const char *dirs[] = {
        "C:\\Program Files\\DSView\\res",
        "C:\\Program Files (x86)\\DSView\\res",
        NULL
    };
    for (int i = 0; dirs[i]; i++) {
        snprintf(buf, len, "%s\\%s", dirs[i], name);
        FILE *t = fopen(buf, "rb");
        if (t) { fclose(t); return buf; }
    }
    return NULL;
}

typedef struct {
    libusb_device *dev;
    libusb_device_handle *hdl;
    uint8_t bus;
    uint8_t address;
    int interfaces;
    int config_value;
    int endpoints;
    int interface_class;
    int class_ok;
    int has_winusb_companion;
    char product[128];
    int bulk_out;
    int bulk_in;
    int dfu_no_ep;
} usb_target;

static int inspect_device(libusb_device *dev, usb_target *t) {
    struct libusb_device_descriptor d;
    if (libusb_get_device_descriptor(dev, &d) != 0) return 0;
    if (d.idVendor != DS_VID) return 0;
    memset(t, 0, sizeof(*t));
    t->dev = dev;
    t->bus = libusb_get_bus_number(dev);
    t->address = libusb_get_device_address(dev);
    struct libusb_config_descriptor *cfg = NULL;
    if (libusb_get_active_config_descriptor(dev, &cfg) == 0 && cfg) {
        t->interfaces = (int)cfg->bNumInterfaces;
        t->config_value = (int)cfg->bConfigurationValue;
        t->interface_class = cfg->interface[0].altsetting[0].bInterfaceClass;
        t->class_ok = (t->interface_class == LIBUSB_CLASS_VENDOR_SPEC ||
                       (t->interface_class == 0 && cfg->bNumInterfaces == 1 &&
                        cfg->interface[0].num_altsetting == 1));
        for (int i = 0; i < cfg->bNumInterfaces; i++) {
            const struct libusb_interface *itf = &cfg->interface[i];
            for (int a = 0; a < itf->num_altsetting; a++) {
                const struct libusb_interface_descriptor *id = &itf->altsetting[a];
                t->endpoints += id->bNumEndpoints;
                for (int e = 0; e < id->bNumEndpoints; e++) {
                    const unsigned char ep = id->endpoint[e].bEndpointAddress;
                    if ((ep & LIBUSB_ENDPOINT_DIR_MASK) == LIBUSB_ENDPOINT_IN)
                        t->bulk_in++;
                    else
                        t->bulk_out++;
                }
            }
        }
        libusb_free_config_descriptor(cfg);
    }
    return 1;
}

static int find_target(usb_target *out, libusb_device **list, ssize_t n,
                       uint16_t want_pid, char *err, size_t errlen) {
    int matches = 0;
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        if (libusb_get_device_descriptor(list[i], &d) != 0) continue;
        if (d.idVendor != DS_VID || d.idProduct != want_pid) continue;
        usb_target t;
        if (!inspect_device(list[i], &t)) continue;
        if (matches == 0) *out = t;
        matches++;
    }
    if (matches == 0) {
        fw_err(err, errlen, "USB device %04X:%04X not found", DS_VID, want_pid);
        return -1;
    }
    if (matches > 1) {
        fw_err(err, errlen, "multiple USB devices %04X:%04X found", DS_VID, want_pid);
        return -1;
    }
    return 0;
}

static int upload(libusb_device_handle *hdl, const unsigned char *buf, size_t len,
                  char *err, size_t errlen) {
    int rc = libusb_set_configuration(hdl, 1);
    if (rc < 0 && rc != LIBUSB_ERROR_BUSY) {
        fw_err(err, errlen, "set_configuration failed: %s", libusb_error_name(rc));
        return -1;
    }
    unsigned char one = 1;
    rc = libusb_control_transfer(hdl, LIBUSB_REQUEST_TYPE_VENDOR, 0xa0,
                                 0xe600, 0, &one, 1, 3000);
    if (rc < 0) {
        fw_err(err, errlen, "CPU reset on failed: %s", libusb_error_name(rc));
        return -1;
    }
    size_t off = 0;
    while (off < len) {
        size_t chunk = len - off;
        if (chunk > 4096) chunk = 4096;
        rc = libusb_control_transfer(hdl,
                 LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_ENDPOINT_OUT,
                 0xa0, (uint16_t)off, 0, (unsigned char *)(buf + off),
                 (uint16_t)chunk, 3000);
        if (rc < 0) {
            fw_err(err, errlen, "firmware upload failed at offset %zu: %s",
                   off, libusb_error_name(rc));
            return -1;
        }
        off += chunk;
    }
    unsigned char zero = 0;
    rc = libusb_control_transfer(hdl, LIBUSB_REQUEST_TYPE_VENDOR, 0xa0,
                                 0xe600, 0, &zero, 1, 3000);
    if (rc < 0) {
        fw_err(err, errlen, "CPU reset off failed: %s", libusb_error_name(rc));
        return -1;
    }
    return 0;
}

int dslab_firmware_usb_status(dslab_firmware_status *st, char *err, size_t errlen) {
    memset(st, 0, sizeof(*st));
    libusb_context *ctx = NULL;
    int rc = libusb_init(&ctx);
    if (rc != 0) {
        fw_err(err, errlen, "libusb_init failed: %s", libusb_error_name(rc));
        return -1;
    }
    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx, &list);
    if (n < 0) {
        libusb_exit(ctx);
        fw_err(err, errlen, "libusb_get_device_list failed: %s", libusb_error_name((int)n));
        return -1;
    }
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        if (libusb_get_device_descriptor(list[i], &d) != 0) continue;
        if (d.idVendor != DS_VID) continue;
        usb_target t;
        if (!inspect_device(list[i], &t)) continue;
        if (d.idProduct == DS_PLUS_PID) st->plus_present = true;
        if (d.idProduct == FX3_BOOT_PID) {
            st->boot_present = true;
            st->boot_bus = t.bus;
            st->boot_address = t.address;
            st->boot_interfaces = t.interfaces;
            st->boot_endpoints = t.endpoints;
            st->boot_bulk_in = t.bulk_in;
            st->boot_bulk_out = t.bulk_out;
        }
    }
    libusb_free_device_list(list, 1);
    libusb_exit(ctx);
    return 0;
}

int dslab_firmware_install(const char *path, uint16_t expect_pid,
                           dslab_firmware_install_result *out,
                           char *err, size_t errlen) {
    memset(out, 0, sizeof(*out));
    dslab_firmware_info info;
    if (dslab_firmware_probe(path, &info, err, errlen) != 0) return -1;
    out->file_pid = info.pid;
    out->file_vid = info.vid;
    out->file_size = info.size;
    if (info.pid != expect_pid) {
        fw_err(err, errlen,
               "refusing firmware: file targets PID %04X, expected %04X",
               info.pid, expect_pid);
        return -1;
    }

    FILE *f = fopen(path, "rb");
    if (!f) {
        fw_err(err, errlen, "cannot open firmware '%s'", path);
        return -1;
    }
    unsigned char *buf = malloc((size_t)info.size);
    if (!buf) {
        fclose(f);
        fw_err(err, errlen, "out of memory");
        return -1;
    }
    if (fread(buf, 1, (size_t)info.size, f) != (size_t)info.size) {
        free(buf);
        fclose(f);
        fw_err(err, errlen, "cannot read firmware '%s'", path);
        return -1;
    }
    fclose(f);

    libusb_context *ctx = NULL;
    int rc = libusb_init(&ctx);
    if (rc != 0) {
        free(buf);
        fw_err(err, errlen, "libusb_init failed: %s", libusb_error_name(rc));
        return -1;
    }
    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx, &list);
    if (n < 0) {
        free(buf);
        libusb_exit(ctx);
        fw_err(err, errlen, "libusb_get_device_list failed: %s", libusb_error_name((int)n));
        return -1;
    }

    usb_target running = {0}, boot = {0};
    int have_running = find_target(&running, list, n, expect_pid, err, errlen) == 0;
    if (have_running) {
        out->already_running = true;
        libusb_free_device_list(list, 1);
        libusb_exit(ctx);
        free(buf);
        return 0;
    }

    /* The Plus FX3 bootloader enumerates as PID 0001. Require the endpoint
       signature of the FX3 bootloader; a normal legacy DSLogic also uses
       PID 0001, so PID alone is not a safe discriminator. */
    if (find_target(&boot, list, n, FX3_BOOT_PID, err, errlen) != 0) {
        libusb_free_device_list(list, 1);
        libusb_exit(ctx);
        free(buf);
        return -1;
    }
    if (boot.endpoints != 2 || boot.interfaces != 1 || !boot.class_ok) {
        fw_err(err, errlen,
               "refusing PID 0001 device: endpoint/interface layout does not match FX3 bootloader");
        libusb_free_device_list(list, 1);
        libusb_exit(ctx);
        free(buf);
        return -1;
    }

    rc = libusb_open(boot.dev, &boot.hdl);
    if (rc != 0) {
        libusb_free_device_list(list, 1);
        libusb_exit(ctx);
        free(buf);
        fw_err(err, errlen, "cannot open bootloader: %s; install WinUSB for PID 0001",
               libusb_error_name(rc));
        return -1;
    }
#if !defined(__APPLE__)
    if (libusb_kernel_driver_active(boot.hdl, 0) == 1)
        libusb_detach_kernel_driver(boot.hdl, 0);
#endif
    int urc = upload(boot.hdl, buf, (size_t)info.size, err, errlen);
    libusb_close(boot.hdl);
    libusb_free_device_list(list, 1);
    free(buf);
    if (urc != 0) {
        libusb_exit(ctx);
        return -1;
    }

    out->uploaded = true;
    out->uploaded_bytes = info.size;
    out->reenumerated = false;
    for (int tries = 0; tries < 40; tries++) {
        libusb_device **again = NULL;
        ssize_t m = libusb_get_device_list(ctx, &again);
        if (m >= 0) {
            for (ssize_t i = 0; i < m; i++) {
                struct libusb_device_descriptor d;
                if (libusb_get_device_descriptor(again[i], &d) != 0) continue;
                if (d.idVendor == DS_VID && d.idProduct == expect_pid) {
                    out->reenumerated = true;
                    break;
                }
            }
            libusb_free_device_list(again, 1);
        }
        if (out->reenumerated) break;
        libusb_exit(ctx);
        libusb_init(&ctx);
        g_usleep(250000);
    }
    libusb_exit(ctx);
    if (!out->reenumerated) {
        fw_err(err, errlen,
               "firmware uploaded but PID %04X did not re-enumerate; replug USB",
               expect_pid);
        return -1;
    }
    return 0;
}
