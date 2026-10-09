#!/usr/bin/env python3
"""Fail-closed CSIPHY clock repair for the pinned SM-J727P kernel tree.

--apply first verifies every input and prepares every edit, then writes the two
changed files. Without --apply the tree must already contain the complete fix.
Only Python's standard library is needed. No compilation or flashing occurs.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile


DTS = Path("arch/arm/boot/dts/qcom/msm8953-camera.dtsi")
ARM64_DTS = Path("arch/arm64/boot/dts/qcom/msm8953-camera.dtsi")
PRECEDENT = Path("arch/arm/boot/dts/qcom/msm8917-camera.dtsi")
PROVIDER = Path("drivers/clk/msm/clock-gcc-8953.c")
BINDING = Path("include/dt-bindings/clock/msm-clocks-8953.h")
CLK_MACRO = Path("include/linux/clk/msm-clk-provider.h")
DEFCONFIG = Path("arch/arm64/configs/j7popltespr_defconfig")
CAMERA_MAKEFILE = Path("drivers/media/platform/msm/Makefile")
DIAGNOSTICS = Path(
    "drivers/media/platform/msm/camera_v2_j7poplte/common/msm_camera_io_util.c"
)
INPUTS = (DTS, PRECEDENT, PROVIDER, BINDING, CLK_MACRO, DEFCONFIG,
          CAMERA_MAKEFILE, DIAGNOSTICS)
ADDRESSES = ("1b34000", "1b35000", "1b36000")
FIXED_NAMES = ("camss_top_ahb_clk", "ispif_ahb_clk", "csiphy_timer_src_clk",
               "csiphy_timer_clk", "camss_ahb_src", "csi_phy_clk", "camss_ahb_clk")
OLD_NAMES = FIXED_NAMES[:5] + FIXED_NAMES[6:]
FIXED_RATES = (0, 61540000, 200000000, 0, 0, 0, 0)
OLD_RATES = FIXED_RATES[:-1]
OLD_NAMES_TEXT = '"camss_ahb_src", "camss_ahb_clk";'
FIXED_NAMES_TEXT = ('"camss_ahb_src", "csi_phy_clk",\n'
                    '\t\t\t"camss_ahb_clk";')
OLD_RATES_TEXT = "qcom,clock-rates = <0 61540000 200000000 0 0 0>;"
FIXED_RATES_TEXT = "qcom,clock-rates = <0 61540000 200000000 0 0 0 0>;"

# Entire failure branches are matched so the original continue, errno assignment,
# and unwind jump are checked and retained. The only differences are log calls.
DIAGNOSTIC_EDITS = (
    (
        '\t\t\tif (IS_ERR(mux_clk)) {\n'
        '\t\t\t\tpr_err("%s get failed\\n",\n'
        '\t\t\t\t\t clk_info[i].clk_name);\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
        '\t\t\tif (IS_ERR(mux_clk)) {\n'
        '\t\t\t\tdev_err(dev,\n'
        '\t\t\t\t\t"%s: clk_get clock[%d/%d] name=%s failed: %ld\\n",\n'
        '\t\t\t\t\t__func__, i, num_clk, clk_info[i].clk_name,\n'
        '\t\t\t\t\tPTR_ERR(mux_clk));\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
    ),
    (
        '\t\t\tif (IS_ERR(src_clk)) {\n'
        '\t\t\t\tpr_err("%s get failed\\n",\n'
        '\t\t\t\t\tclk_src_info[i].clk_name);\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
        '\t\t\tif (IS_ERR(src_clk)) {\n'
        '\t\t\t\tdev_err(dev,\n'
        '\t\t\t\t\t"%s: clk_get clock[%d/%d] name=%s failed: %ld\\n",\n'
        '\t\t\t\t\t__func__, i, num_clk, clk_src_info[i].clk_name,\n'
        '\t\t\t\t\tPTR_ERR(src_clk));\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
    ),
    (
        '\t\t\tif (IS_ERR(clk_ptr[i])) {\n'
        '\t\t\t\tpr_err("%s get failed\\n", clk_info[i].clk_name);\n'
        '\t\t\t\trc = PTR_ERR(clk_ptr[i]);\n'
        '\t\t\t\tgoto cam_clk_get_err;\n\t\t\t}',
        '\t\t\tif (IS_ERR(clk_ptr[i])) {\n'
        '\t\t\t\tdev_err(dev,\n'
        '\t\t\t\t\t"%s: clk_get clock[%d/%d] name=%s "\n'
        '\t\t\t\t\t"requested_rate=%ld failed: %ld\\n",\n'
        '\t\t\t\t\t__func__, i, num_clk, clk_info[i].clk_name,\n'
        '\t\t\t\t\tclk_info[i].clk_rate, PTR_ERR(clk_ptr[i]));\n'
        '\t\t\t\trc = PTR_ERR(clk_ptr[i]);\n'
        '\t\t\t\tgoto cam_clk_get_err;\n\t\t\t}',
    ),
)


class VerificationError(Exception):
    """Input does not match the independently established repair prerequisites."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def without_comments(text: str) -> str:
    # This source's relevant properties have no comment markers in strings.
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


def refs(index: int) -> tuple[str, ...]:
    return ("clk_gcc_camss_top_ahb_clk", "clk_gcc_camss_ispif_ahb_clk",
            f"clk_csi{index}phytimer_clk_src",
            f"clk_gcc_camss_csi{index}phytimer_clk", "clk_camss_top_ahb_clk_src",
            f"clk_gcc_camss_csi{index}phy_clk", "clk_gcc_camss_ahb_clk")


def csiphy_nodes(text: str, expected: tuple[str, ...], label: str):
    # Refuse unexpected nested syntax rather than guessing where a node ends.
    matches = list(re.finditer(
        r"\bqcom,csiphy@([0-9a-f]+)\s*\{(?P<body>[^{}]*)\};", text
    ))
    require(len(re.findall(r"\bqcom,csiphy@", text)) == len(matches),
            f"{label}: unrecognized CSIPHY node syntax")
    require(tuple(m.group(1) for m in matches) == expected,
            f"{label}: unexpected CSIPHY nodes/order")
    return matches


def property_value(body: str, prop: str, label: str) -> str:
    values = re.findall(r"(?m)^\s*" + re.escape(prop) + r"\s*=\s*([^;]*);", body)
    require(len(values) == 1, f"{label}: expected exactly one {prop} property")
    return values[0].strip()


def string_array(value: str, label: str) -> tuple[str, ...]:
    require(re.fullmatch(r'"[^"\\]*"(?:\s*,\s*"[^"\\]*")*', value) is not None,
            f"{label}: invalid string array")
    return tuple(re.findall(r'"([^"\\]*)"', value))


def clock_refs(body: str, label: str) -> tuple[str, ...]:
    value = property_value(body, "clocks", label)
    cell = r"<&clock_gcc\s+(clk_[a-zA-Z0-9_]+)>"
    require(re.fullmatch(cell + r"(?:\s*,\s*" + cell + r")*", value) is not None,
            f"{label}: unrecognized clock phandle/cell syntax")
    return tuple(re.findall(cell, value))


def clock_rates(body: str, label: str) -> tuple[int, ...]:
    value = property_value(body, "qcom,clock-rates", label)
    require(re.fullmatch(r"<\s*\d+(?:\s+\d+)*\s*>", value) is not None,
            f"{label}: unrecognized clock-rate syntax")
    return tuple(int(v) for v in re.findall(r"\d+", value))


def validate_node(body: str, index: int, label: str, precedent: bool) -> str:
    require(clock_refs(body, label) == refs(index),
            f"{label}: clock reference order differs; refusing repair")
    require(property_value(body, "cell-index", label) == f"<{index}>",
            f"{label}: unexpected cell-index")
    compatibility = string_array(property_value(body, "compatible", label), label)
    version = "qcom,csiphy-v3.4.2" if precedent else "qcom,csiphy-v3.5"
    require(compatibility == (version, "qcom,csiphy"),
            f"{label}: unexpected CSIPHY compatibility")
    names = string_array(property_value(body, "clock-names", label), label)
    rates = clock_rates(body, label)
    if (names, rates) == (FIXED_NAMES, FIXED_RATES):
        return "fixed"
    if not precedent and (names, rates) == (OLD_NAMES, OLD_RATES):
        return "pristine"
    raise VerificationError(f"{label}: unexpected or mixed clock names/rates")


def validate_evidence(texts: dict[Path, str]) -> None:
    config = texts[DEFCONFIG]
    for option in ("CONFIG_ARM64", "CONFIG_ARCH_MSM8953", "CONFIG_MSMB_CAMERA",
                   "CONFIG_SEC_J7POPLTE_USA_SPR"):
        require(re.findall(r"^" + option + r"=(.*)$", config, re.M) == ["y"],
                f"{DEFCONFIG}: expected {option}=y")
    selection = ("ifneq ($(CONFIG_SEC_J7POPLTE_USA_SPR),y)\n"
                 "    obj-$(CONFIG_MSMB_CAMERA) += camera_v2/\n"
                 "else\n"
                 "    obj-$(CONFIG_MSMB_CAMERA) += camera_v2_j7poplte/\n"
                 "endif")
    require(texts[CAMERA_MAKEFILE].count(selection) == 1,
            f"{CAMERA_MAKEFILE}: active camera driver selection changed")
    macro = "#define CLK_LIST(_c) { .clk = &(&_c)->c, .of_idx = clk_##_c }"
    require(texts[CLK_MACRO].count(macro) == 1,
            f"{CLK_MACRO}: CLK_LIST binding-ID mapping changed")
    require("#include <dt-bindings/clock/msm-clocks-8953.h>" in texts[PROVIDER],
            f"{PROVIDER}: expected binding include")
    lookup = re.findall(r"static struct clk_lookup msm_clocks_lookup\[\] = \{(.*?)\n\};",
                        texts[PROVIDER], re.S)
    require(len(lookup) == 1, f"{PROVIDER}: expected GCC provider lookup table")
    ids = set()
    for ref in sorted({r for index in range(3) for r in refs(index)}):
        defines = re.findall(r"^#define\s+" + re.escape(ref) + r"\s+(0x[0-9a-fA-F]+)\s*$",
                             texts[BINDING], re.M)
        require(len(defines) == 1, f"{BINDING}: missing/duplicate definition {ref}")
        clock_id = int(defines[0], 16)
        require(clock_id not in ids, f"{BINDING}: duplicate clock ID {ref}")
        ids.add(clock_id)
        require(len(re.findall(r"\bCLK_LIST\(\s*" + re.escape(ref[4:]) + r"\s*\)",
                               lookup[0])) == 1,
                f"{PROVIDER}: missing/duplicate provider mapping for {ref}")
    for index, match in enumerate(csiphy_nodes(texts[PRECEDENT], ADDRESSES[:2],
                                             str(PRECEDENT))):
        validate_node(match.group("body"), index,
                      f"{PRECEDENT}:csiphy@{ADDRESSES[index]}", precedent=True)


def preflight(kernel: Path) -> tuple[dict[Path, str], dict[Path, str], str]:
    kernel = kernel.resolve(strict=True)
    require((kernel / ARM64_DTS).resolve(strict=True) ==
            (kernel / DTS).resolve(strict=True),
            "ARM64 qcom DTS path must resolve to the canonical ARM source")
    texts = {}
    for relative in INPUTS:
        path = kernel / relative
        try:
            path.resolve(strict=True).relative_to(kernel)
        except ValueError as error:
            raise VerificationError(f"{relative}: input resolves outside the kernel tree") from error
        texts[relative] = path.read_text(encoding="utf-8")
    clean = {p: without_comments(t) for p, t in texts.items()}
    validate_evidence(clean)
    states = []
    for index, match in enumerate(csiphy_nodes(clean[DTS], ADDRESSES, str(DTS))):
        states.append(validate_node(match.group("body"), index,
                      f"{DTS}:csiphy@{ADDRESSES[index]}", precedent=False))
    camera = texts[DIAGNOSTICS]
    require(len(re.findall(r"\bclk_get\s*\(", without_comments(camera))) == 3,
            f"{DIAGNOSTICS}: expected exactly three clk_get call sites")
    for lookup in ("mux_clk = clk_get(dev, clk_info[i].clk_name);",
                   "src_clk = clk_get(dev, clk_src_info[i].clk_name);",
                   "clk_ptr[i] = clk_get(dev, clk_info[i].clk_name);"):
        require(camera.count(lookup) == 1,
                f"{DIAGNOSTICS}: unexpected clk_get lookup assignment")
    for index, (old, fixed) in enumerate(DIAGNOSTIC_EDITS):
        counts = (camera.count(old), camera.count(fixed))
        require(counts in ((1, 0), (0, 1)),
                f"{DIAGNOSTICS}: unexpected clk_get failure branch {index}")
        states.append("pristine" if counts == (1, 0) else "fixed")
    require(len(set(states)) == 1,
            "Mixed partial repair detected; refusing to guess or modify any file")
    state = states[0]
    changes = {}
    if state == "pristine":
        updated_dts = texts[DTS]
        # Limit substitution to the three independently verified CSIPHY bodies.
        for match in reversed(csiphy_nodes(updated_dts, ADDRESSES, str(DTS))):
            body = match.group("body")
            require(body.count(OLD_NAMES_TEXT) == 1 and body.count(OLD_RATES_TEXT) == 1,
                    "CSIPHY property formatting drift; refusing repair")
            patched = body.replace(OLD_NAMES_TEXT, FIXED_NAMES_TEXT).replace(
                OLD_RATES_TEXT, FIXED_RATES_TEXT)
            updated_dts = (updated_dts[:match.start("body")] + patched +
                           updated_dts[match.end("body"):])
        for old, fixed in DIAGNOSTIC_EDITS:
            camera = camera.replace(old, fixed)
        changes = {DTS: updated_dts, DIAGNOSTICS: camera}
    return texts, changes, state


def commit_changes(kernel: Path, originals: dict[Path, str], changes: dict[Path, str]):
    # All semantic checks finish before this function. Stage adjacent temporary
    # files, recheck originals, and restore replaced files if a write fails.
    staged = {}
    replaced = []
    try:
        for relative, updated in changes.items():
            path = kernel / relative
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            staged[relative] = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, path.stat().st_mode)
        for relative in changes:
            require((kernel / relative).read_text(encoding="utf-8") == originals[relative],
                    f"{relative}: input changed after preflight")
        for relative, temporary in staged.items():
            os.replace(temporary, kernel / relative)
            replaced.append(relative)
    except BaseException:
        for relative in replaced:
            with (kernel / relative).open("w", encoding="utf-8", newline="") as handle:
                handle.write(originals[relative])
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def run(kernel: Path, apply: bool = False) -> str:
    kernel = kernel.resolve(strict=True)
    originals, changes, state = preflight(kernel)
    if apply and changes:
        commit_changes(kernel, originals, changes)
        _, remaining, state = preflight(kernel)
        require(state == "fixed" and not remaining, "Post-write verification failed")
        return "applied"
    require(state == "fixed", "Tree is pristine; use --apply to apply the verified repair")
    return "already-fixed" if apply else "verified"


def print_report(result: str) -> None:
    print(f"Result: {result}; three CSIPHY nodes, seven refs/names/rates each")
    print(f"Canonical DTS: {DTS}; ARM64 DTS alias verified")
    print("Evidence: GCC CLK_LIST + binding IDs + MSM8917 seven-clock precedent verified")
    print("Selection: CONFIG_SEC_J7POPLTE_USA_SPR=y selects camera_v2_j7poplte")
    print("Clock positions (zero-based; reference / name / requested rate):")
    for index in range(3):
        print(f"  csiphy@{ADDRESSES[index]}:")
        for position, (ref, name, rate) in enumerate(zip(refs(index), FIXED_NAMES, FIXED_RATES)):
            print(f"    {position}: {ref} / {name} / {rate}")
    print(f"Diagnostics: {DIAGNOSTICS}; 3 device/function/index/name/errno logs verified")
    print("Enable failure includes requested rate; errno assignment and unwind are preserved")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True, help="Kernel checkout root")
    parser.add_argument("--apply", action="store_true", help="Apply only after complete preflight")
    args = parser.parse_args()
    try:
        result = run(args.kernel, args.apply)
    except (VerificationError, OSError, UnicodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
