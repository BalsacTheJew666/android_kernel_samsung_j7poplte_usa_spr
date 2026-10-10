#!/usr/bin/env python3
"""Run the actual CSID clock parser on a host with mocked OF/device APIs.

Extract the complete csid_device and msm_cam_clk_info declarations and the
patched msm_csid_get_clk_info body, rather than reimplementing its algorithm.
Device-tree names and rates for CSID0/1/2 come from the actual pinned DTS.
This is a memory/ownership regression test, not a camera or hardware test.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ACTIVE = Path("drivers/media/platform/msm/camera_v2_j7poplte")


def block(text, marker, trailing_semicolon=False):
    """Extract a complete C brace block, retaining its original declaration."""
    start = text.index(marker)
    opening = text.index("{", start)
    depth = 1
    cursor = opening + 1
    while depth:
        if text[cursor] == "{":
            depth += 1
        elif text[cursor] == "}":
            depth -= 1
        cursor += 1
    if trailing_semicolon:
        assert text[cursor] == ";"
        cursor += 1
    return text[start:cursor]


def generate(kernel):
    source = (kernel / ACTIVE / "sensor/csid/msm_csid.c").read_text()
    header = (kernel / ACTIVE / "sensor/csid/msm_csid.h").read_text()
    sdk = (kernel / ACTIVE / "include/soc/qcom/camera2.h").read_text()
    dts = (kernel / "arch/arm/boot/dts/qcom/msm8953-camera.dtsi").read_text()
    structure = block(header, "struct csid_device {", True)
    for member in ("csid_clk_info", "csid_clk_src_info"):
        declaration = rf"struct msm_cam_clk_info\s+{member}\[CSID_NUM_CLK_MAX\];"
        if not re.search(declaration, structure):
            raise ValueError(f"{member} is not an embedded per-device array")
        if re.search(rf"static\s+struct msm_cam_clk_info\s+{member}\b", source):
            raise ValueError(f"shared global {member} remains")

    fixtures = []
    for controller, address in enumerate(("1b30000", "1b30400", "1b30800")):
        node = block(dts, f"qcom,csid@{address}")
        names = re.findall(r'"([^"]+)"', re.search(
            r"clock-names\s*=([^;]+);", node).group(1))
        rates = [int(value) for value in re.search(
            r"qcom,clock-rates\s*=\s*<([^>]+)>", node).group(1).split()]
        if len(names) != len(rates) or names[4] != f"csi{controller}_phy_clk":
            raise ValueError(f"unexpected CSID{controller} DTS fixture")
        fixtures.append(
            "{ .names = { " + ", ".join(map(json.dumps, names)) + " }, "
            ".rates = { " + ", ".join(map(str, rates)) + " }, "
            f".count = {len(names)}, .fail_index = -1, "
            f'.full_name = "/soc/qcom,csid@{address}"' + " }")

    definitions = "\n".join((
        re.search(r"^#define CSID_NUM_CLK_MAX\s+\d+", header, re.M).group(0),
        re.search(r"^#define CSID_VERSION_V22\s+0x[0-9a-fA-F]+", source, re.M).group(0),
        re.search(r"^#define CSID_VERSION_V35_1\s+0x[0-9a-fA-F]+", source, re.M).group(0),
        block(sdk, "struct msm_cam_clk_info {", True),
        block(header, "enum msm_csid_state_t {", True),
        structure,
    ))
    diagnostics = block(source, "static void msm_csid_log_phy_clocks(")
    parser = block(source, "static int msm_csid_get_clk_info(")
    return PRELUDE + definitions + MOCK_APIS + diagnostics + "\n" + parser + "\n" + (
        "static struct device_node nodes[3] = {\n" + ",\n".join(fixtures) + "\n};\n"
    ) + TESTS


PRELUDE = r"""
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sched.h>
#define MAX_REGULATOR 16
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static void discard_log(const char *format, ...) __attribute__((format(printf, 1, 2)));
static void discard_log(const char *format, ...) { (void)format; }
#define CDBG(...) discard_log(__VA_ARGS__)
#define pr_err(...) discard_log(__VA_ARGS__)
#define pr_info(...) discard_log(__VA_ARGS__)
#define dev_err(device, ...) discard_log(__VA_ARGS__)
#define dev_info(device, ...) discard_log(__VA_ARGS__)
#define dev_dbg(device, ...) discard_log(__VA_ARGS__)
struct device_node {
    const char *names[17];
    uint32_t rates[17];
    int count, fail_index, rates_error;
    const char *full_name;
};
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; int id; };
struct msm_sd_subdev { int unused; };
struct mutex { int unused; };
struct completion { int unused; };
struct msm_camera_csid_testmode_parms { int unused; };
struct msm_camera_csid_params { int unused; };
#define __iomem
"""


MOCK_APIS = r"""
static int of_property_count_strings(struct device_node *node, const char *key)
{
    assert(!strcmp(key, "clock-names"));
    return node ? node->count : -ENOENT;
}
static int of_property_read_string_index(struct device_node *node,
        const char *key, int index, const char **result)
{
    assert(!strcmp(key, "clock-names"));
    if (!node || index < 0 || index >= node->count || index == node->fail_index)
        return -EINVAL;
    *result = node->names[index];
    sched_yield(); /* Force cross-controller interleaving in the threaded test. */
    return 0;
}
static int of_property_read_u32_array(struct device_node *node,
        const char *key, uint32_t *result, size_t count)
{
    assert(!strcmp(key, "qcom,clock-rates"));
    if (!node || node->rates_error || count > (size_t)node->count)
        return -EINVAL;
    memcpy(result, node->rates, count * sizeof(*result));
    return 0;
}
"""


TESTS = r"""
static struct platform_device devices[3];
static struct csid_device controllers[3];

static void check_controller(int id)
{
    struct csid_device *controller = &controllers[id];
    assert(controller->num_clk == (uint32_t)nodes[id].count);
    assert(controller->num_clk_src_info == 0);
    assert(controller->csid_clk_index == 3);
    assert(controller->csid_max_clk == nodes[id].rates[3]);
    for (unsigned i = 0; i < controller->num_clk; ++i) {
        assert(!strcmp(controller->csid_clk_info[i].clk_name, nodes[id].names[i]));
        assert(controller->csid_clk_info[i].clk_rate ==
            (nodes[id].rates[i] ? (long)nodes[id].rates[i] : -1L));
        assert(controller->csid_clk_info[i].delay == 0);
    }
    for (unsigned i = 0; i < CSID_NUM_CLK_MAX; ++i)
        assert(controller->csid_clk_src_info[i].clk_name == NULL);
}

static void initialize(int id)
{
    memset(&controllers[id], 0, sizeof(controllers[id]));
    controllers[id].pdev = &devices[id];
    controllers[id].hw_dts_version = CSID_VERSION_V35_1;
    assert(msm_csid_get_clk_info(&controllers[id], &devices[id]) == 0);
    check_controller(id);
}

static void *initialize_repeatedly(void *argument)
{
    int id = *(int *)argument;
    for (int iteration = 0; iteration < 1000; ++iteration)
        initialize(id);
    return NULL;
}

static void failure_case(struct device_node *node, uint32_t version, int expected)
{
    struct guarded {
        uint64_t before;
        struct csid_device controller;
        uint64_t after;
    } guarded = { .before = 0x1122334455667788ULL,
                  .after = 0x8877665544332211ULL };
    struct platform_device device = { .dev.of_node = node, .id = 0 };
    struct csid_device untouched[3];
    memcpy(untouched, controllers, sizeof(untouched));
    guarded.controller.pdev = &device;
    guarded.controller.hw_dts_version = version;
    assert(msm_csid_get_clk_info(&guarded.controller, &device) == expected);
    assert(guarded.before == 0x1122334455667788ULL);
    assert(guarded.after == 0x8877665544332211ULL);
    assert(!memcmp(untouched, controllers, sizeof(untouched)));
}

static void check_legacy_source_ownership(void)
{
    struct csid_device legacy[3] = {0};
    struct platform_device legacy_devices[3] = {0};
    struct device_node legacy_nodes[3];
    char source_names[3][2][32];
    for (int id = 0; id < 3; ++id) {
        legacy_nodes[id] = nodes[id];
        legacy_nodes[id].count = 8;
        legacy_nodes[id].names[5] = "csi_phy_src_clk";
        for (int source = 0; source < 2; ++source) {
            snprintf(source_names[id][source], sizeof(source_names[id][source]),
                "csi%d_phy_source%d", id, source);
            legacy_nodes[id].names[6 + source] = source_names[id][source];
            legacy_nodes[id].rates[6 + source] = 100 + id * 10 + source;
        }
        legacy_devices[id].dev.of_node = &legacy_nodes[id];
        legacy_devices[id].id = id;
        legacy[id].pdev = &legacy_devices[id];
        legacy[id].hw_dts_version = CSID_VERSION_V22;
        assert(msm_csid_get_clk_info(&legacy[id], &legacy_devices[id]) == 0);
        /* Preserve the driver's existing unused source slot zero. */
        for (int previous = 0; previous <= id; ++previous) {
            assert(legacy[previous].num_clk == 6);
            assert(legacy[previous].num_clk_src_info == 2);
            assert(legacy[previous].csid_clk_src_info[0].clk_name == NULL);
            for (int source = 0; source < 2; ++source) {
                assert(!strcmp(legacy[previous].csid_clk_src_info[source + 1].clk_name,
                    source_names[previous][source]));
                assert(legacy[previous].csid_clk_src_info[source + 1].clk_rate ==
                    100 + previous * 10 + source);
            }
        }
    }
    legacy_nodes[0].fail_index = 6;
    failure_case(&legacy_nodes[0], CSID_VERSION_V22, -EINVAL);
    puts("PASS: successful legacy source parsing retains separate source names/rates for three instances");
}

int main(void)
{
    for (int id = 0; id < 3; ++id) {
        devices[id].id = id;
        devices[id].dev.of_node = &nodes[id];
    }
    /* Test every probe order, validating already initialized devices each time. */
    const int orders[6][3] = {
        {0, 1, 2}, {0, 2, 1}, {1, 0, 2},
        {1, 2, 0}, {2, 0, 1}, {2, 1, 0}
    };
    for (unsigned order = 0; order < ARRAY_SIZE(orders); ++order) {
        for (int index = 0; index < 3; ++index) {
            initialize(orders[order][index]);
            for (int done = 0; done <= index; ++done)
                check_controller(orders[order][done]);
        }
    }
    for (int first = 0; first < 3; ++first)
        for (int second = first + 1; second < 3; ++second) {
            assert(controllers[first].csid_clk_info != controllers[second].csid_clk_info);
            assert(controllers[first].csid_clk_src_info != controllers[second].csid_clk_src_info);
        }
    /* Independently mutate both array types to prove there is no shared backing. */
    controllers[2].csid_clk_info[4].clk_name = "sentinel";
    controllers[2].csid_clk_src_info[0].clk_name = "sentinel-source";
    check_controller(0);
    check_controller(1);
    initialize(2);
    puts("PASS: all six CSID0/1/2 probe orders retain independent names and rates");

    pthread_t threads[3];
    int ids[3] = {0, 1, 2};
    for (int id = 0; id < 3; ++id)
        assert(pthread_create(&threads[id], NULL, initialize_repeatedly, &ids[id]) == 0);
    for (int id = 0; id < 3; ++id) {
        assert(pthread_join(threads[id], NULL) == 0);
        check_controller(id);
        printf("PASS: CSID%d node=%s PHY=%s main-array=%p source-array=%p\n",
            id, nodes[id].full_name, controllers[id].csid_clk_info[4].clk_name,
            (void *)controllers[id].csid_clk_info,
            (void *)controllers[id].csid_clk_src_info);
    }
    puts("PASS: 3,000 concurrent parser initializations retain controller ownership");

    struct device_node maximum = nodes[0];
    maximum.count = CSID_NUM_CLK_MAX;
    for (int index = nodes[0].count; index < maximum.count; ++index) {
        maximum.names[index] = "spare_clock";
        maximum.rates[index] = 0;
    }
    struct platform_device maximum_device = { .dev.of_node = &maximum };
    struct csid_device maximum_controller = { .pdev = &maximum_device,
        .hw_dts_version = CSID_VERSION_V35_1 };
    assert(msm_csid_get_clk_info(&maximum_controller, &maximum_device) == 0);
    assert(maximum_controller.num_clk == CSID_NUM_CLK_MAX);
    assert(!strcmp(maximum_controller.csid_clk_info[CSID_NUM_CLK_MAX - 1].clk_name,
        "spare_clock"));
    check_legacy_source_ownership();

    failure_case(NULL, CSID_VERSION_V35_1, -ENOENT);
    struct device_node malformed = nodes[0];
    malformed.count = -ENOENT;
    failure_case(&malformed, CSID_VERSION_V35_1, -ENOENT);
    malformed.count = 0;
    failure_case(&malformed, CSID_VERSION_V35_1, -EINVAL);
    malformed.count = CSID_NUM_CLK_MAX + 1;
    failure_case(&malformed, CSID_VERSION_V35_1, -EINVAL);
    for (int index = 0; index < nodes[0].count; ++index) {
        malformed = nodes[0];
        malformed.fail_index = index;
        failure_case(&malformed, CSID_VERSION_V35_1, -EINVAL);
    }
    malformed = nodes[0];
    malformed.rates_error = 1;
    failure_case(&malformed, CSID_VERSION_V35_1, -EINVAL);
    /* Exercise unsupported legacy split validation without changing its mapping. */
    malformed = nodes[0];
    failure_case(&malformed, CSID_VERSION_V22, -EINVAL);
    malformed.names[0] = "csi_phy_src_clk";
    failure_case(&malformed, CSID_VERSION_V22, -EINVAL);
    puts("PASS: absent/negative/zero/oversized DT counts and name/rate read failures are safe");
    puts("Host regression only; no CSID hardware, clock provider, or camera was exercised.");
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path,
                        default=Path(os.environ.get("KERNEL_SOURCE", "kernel")))
    args = parser.parse_args()
    source = generate(args.kernel.resolve(strict=True))
    compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
    with tempfile.TemporaryDirectory(prefix="j727p-csid-ownership-") as temporary:
        harness = Path(temporary) / "csid_clock_ownership.c"
        executable = Path(temporary) / "csid_clock_ownership"
        harness.write_text(source)
        command = compiler + ["-std=gnu11", "-Wall", "-Wextra", "-Wno-sign-compare",
                              "-Werror=implicit-function-declaration",
                              "-Werror=incompatible-pointer-types", "-pthread",
                              "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                              "-g", str(harness), "-o", str(executable)]
        print("Compiling extracted CSID declarations/parser with ASan and UBSan", flush=True)
        subprocess.run(command, check=True)
        environment = os.environ.copy()
        environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        subprocess.run([str(executable)], check=True, env=environment)


if __name__ == "__main__":
    main()
