cmake_minimum_required(VERSION 3.20)
project(dslab C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

find_package(PkgConfig REQUIRED)
pkg_check_modules(GLIB REQUIRED glib-2.0)
pkg_check_modules(LIBUSB REQUIRED libusb-1.0)
pkg_check_modules(ZLIB REQUIRED zlib)
find_package(Threads REQUIRED)
add_compile_definitions(_WIN32_WINNT=0x0601)

# Fetched and patched by scripts/bootstrap.ps1 (pinned in deps.json).
set(DSVIEW_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.deps/dsview" CACHE PATH "DSView v1.3.2 source root")
if(NOT EXISTS "${DSVIEW_ROOT}/libsigrok4DSL/lib_main.c")
    message(FATAL_ERROR "DSView source not found at ${DSVIEW_ROOT}; run scripts/bootstrap.ps1")
endif()
file(GLOB SIGROK_SOURCES
    "${DSVIEW_ROOT}/libsigrok4DSL/*.c"
    "${DSVIEW_ROOT}/libsigrok4DSL/hardware/DSL/*.c"
    "${DSVIEW_ROOT}/libsigrok4DSL/hardware/common/*.c"
    "${DSVIEW_ROOT}/libsigrok4DSL/hardware/demo/*.c"
    "${DSVIEW_ROOT}/libsigrok4DSL/input/*.c"
    "${DSVIEW_ROOT}/libsigrok4DSL/output/*.c"
    "${DSVIEW_ROOT}/common/log/xlog.c"
    "${DSVIEW_ROOT}/common/minizip/zip.c"
    "${DSVIEW_ROOT}/common/minizip/unzip.c"
    "${DSVIEW_ROOT}/common/minizip/ioapi.c"
)
add_library(sigrok4dsl STATIC ${SIGROK_SOURCES})
target_include_directories(sigrok4dsl PUBLIC
    "${DSVIEW_ROOT}/libsigrok4DSL"
    "${DSVIEW_ROOT}/common"
    "${DSVIEW_ROOT}/common/log"
    "${DSVIEW_ROOT}/common/minizip"
    "${GLIB_INCLUDE_DIRS}"
    "${LIBUSB_INCLUDE_DIRS}"
)
target_link_libraries(sigrok4dsl PUBLIC
    ${GLIB_LIBRARIES} ${LIBUSB_LIBRARIES} ${ZLIB_LIBRARIES} Threads::Threads)

add_executable(dslab src/main.c src/device.c src/capture.c src/json.c src/config.c src/firmware.c)
target_include_directories(dslab PRIVATE include "${DSVIEW_ROOT}/libsigrok4DSL" "${GLIB_INCLUDE_DIRS}")
target_link_libraries(dslab PRIVATE sigrok4dsl)
if(WIN32)
    target_link_options(dslab PRIVATE -static-libgcc)
    # Keep MinGW imports resolvable; runtime DLLs are bundled beside the exe.
endif()
