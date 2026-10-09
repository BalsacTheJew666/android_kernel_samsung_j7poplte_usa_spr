#!/usr/bin/env python3
"""Verify the actual ARM64 Image.gz-dtb and compiled SM-J727P clock mappings.

Uses the saved Kbuild cat command, not a sorted directory listing: this kernel's
empty CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE_NAMES makes ordering depend on find.
FDT parsing and clock-provider resolution use only the Python standard library.
"""

import argparse
import gzip
import hashlib
import re
import shlex
import struct
import subprocess
import sys
import zlib
from pathlib import Path


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def cells(value):
    require(len(value) % 4 == 0, "property is not a whole number of FDT cells")
    return list(struct.unpack(">" + "I" * (len(value) // 4), value))


def strings(value):
    require(value.endswith(b"\0"), "string property is not NUL-terminated")
    return value[:-1].decode("utf-8").split("\0")


def one_cell(properties, name):
    value = cells(properties[name])
    require(len(value) == 1, f"{name} must contain exactly one cell")
    return value[0]


def parse_fdt(blob):
    """Parse a version-17 flattened tree while checking all region boundaries."""
    require(len(blob) >= 40, "truncated FDT header")
    (magic, total, structure, string_table, reservations, version, compatible,
     _boot_cpu, string_size, structure_size) = struct.unpack_from(">10I", blob)
    require(magic == 0xD00DFEED, "bad FDT magic")
    require(total == len(blob), f"FDT totalsize {total} differs from file size {len(blob)}")
    require(version == 17 and compatible <= 17, "unsupported FDT header version")
    require(structure % 4 == 0 and reservations % 8 == 0, "unaligned FDT region")
    regions = [(0, 40, "header")]
    for start, size, label in ((structure, structure_size, "structure"),
                               (string_table, string_size, "strings")):
        require(start >= 40 and start + size <= total, f"out-of-bounds FDT {label}")
        regions.append((start, start + size, label))
    require(reservations >= 40, "invalid FDT reservation offset")
    end_reservations = reservations
    while True:
        require(end_reservations + 16 <= total, "unterminated FDT reservation map")
        address, size = struct.unpack_from(">QQ", blob, end_reservations)
        end_reservations += 16
        if address == size == 0:
            break
    regions.append((reservations, end_reservations, "reservations"))
    regions.sort()
    for previous, current in zip(regions, regions[1:]):
        require(previous[1] <= current[0], f"overlapping FDT {previous[2]}/{current[2]}")

    table = blob[string_table:string_table + string_size]
    limit = structure + structure_size
    cursor = structure
    nodes, stack = {}, []
    finished = False
    while cursor < limit:
        require(cursor + 4 <= limit, "truncated FDT token")
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:  # FDT_BEGIN_NODE
            end = blob.find(b"\0", cursor, limit)
            require(end >= cursor, "unterminated FDT node name")
            name = blob[cursor:end].decode("utf-8")
            require("/" not in name, "slash in FDT node name")
            if not stack:
                require(not nodes and not name, "FDT must have one unnamed root")
                path = "/"
            else:
                require(bool(name), "empty child node name")
                path = stack[-1].rstrip("/") + "/" + name
            require(path not in nodes, f"duplicate FDT node {path}")
            nodes[path] = {}
            stack.append(path)
            cursor = (end + 4) & ~3
            require(cursor <= limit, "FDT node alignment exceeds structure")
        elif token == 2:  # FDT_END_NODE
            require(bool(stack), "unmatched FDT_END_NODE")
            stack.pop()
        elif token == 3:  # FDT_PROP
            require(bool(stack) and cursor + 8 <= limit, "FDT property outside a node")
            length, name_offset = struct.unpack_from(">II", blob, cursor)
            cursor += 8
            require(name_offset < string_size, "FDT property name outside strings")
            end = table.find(b"\0", name_offset)
            require(end >= name_offset, "unterminated FDT property name")
            name = table[name_offset:end].decode("utf-8")
            require(name and name not in nodes[stack[-1]], f"duplicate/empty property {name}")
            require(cursor + length <= limit, f"truncated FDT property {name}")
            nodes[stack[-1]][name] = blob[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
            require(cursor <= limit, "FDT property alignment exceeds structure")
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            require(nodes and not stack, "FDT_END before root is closed")
            require(not any(blob[cursor:limit]), "nonzero data after FDT_END")
            finished = True
            break
        else:
            raise VerificationError(f"unknown FDT token {token:#x}")
    require(finished, "missing FDT_END")
    return nodes


def clock_constants(kernel):
    header = kernel / "include/dt-bindings/clock/msm-clocks-8953.h"
    return {name: int(number, 0) for name, number in re.findall(
        r"^#define\s+(clk_\w+)\s+(0x[0-9a-fA-F]+|[0-9]+)\s*$",
        header.read_text(), re.MULTILINE)}


def verify_csiphy(nodes, constants):
    phandles = {}
    for path, properties in nodes.items():
        handles = [one_cell(properties, name) for name in ("phandle", "linux,phandle")
                   if name in properties]
        if handles:
            require(len(set(handles)) == 1, f"inconsistent phandles in {path}")
            handle = handles[0]
            require(handle not in (0, 0xFFFFFFFF) and handle not in phandles,
                    f"invalid/duplicate phandle {handle:#x}")
            phandles[handle] = (path, properties)
    csiphy = [(path, properties) for path, properties in nodes.items()
              if "compatible" in properties
              and "qcom,csiphy" in strings(properties["compatible"])]
    require(len(csiphy) == 3, f"expected three CSIPHY nodes, found {len(csiphy)}")
    indices = set()
    expected_names = ["camss_top_ahb_clk", "ispif_ahb_clk", "csiphy_timer_src_clk",
                      "csiphy_timer_clk", "camss_ahb_src", "csi_phy_clk", "camss_ahb_clk"]
    for path, properties in csiphy:
        index = one_cell(properties, "cell-index")
        require(index in (0, 1, 2) and index not in indices, f"invalid CSIPHY index at {path}")
        indices.add(index)
        names = strings(properties["clock-names"])
        rates = cells(properties["qcom,clock-rates"])
        require(names == expected_names, f"CSIPHY{index} clock names/order differ: {names}")
        require(rates == [0, 61540000, 200000000, 0, 0, 0, 0],
                f"CSIPHY{index} clock rates/order differ: {rates}")
        raw = cells(properties["clocks"])
        specifiers = []
        cursor = 0
        while cursor < len(raw):
            handle = raw[cursor]
            require(handle in phandles, f"CSIPHY{index} unresolved clock provider {handle:#x}")
            provider_path, provider = phandles[handle]
            count = one_cell(provider, "#clock-cells")
            require(count == 1, f"unexpected clock provider cell count at {provider_path}")
            require(cursor + 1 + count <= len(raw), f"truncated CSIPHY{index} clock specifier")
            require("qcom,gcc-8953" in strings(provider["compatible"]),
                    f"unexpected CSIPHY{index} provider {provider_path}")
            specifiers.append(raw[cursor + 1])
            cursor += 1 + count
        identifiers = ["clk_gcc_camss_top_ahb_clk", "clk_gcc_camss_ispif_ahb_clk",
                       f"clk_csi{index}phytimer_clk_src", f"clk_gcc_camss_csi{index}phytimer_clk",
                       "clk_camss_top_ahb_clk_src", f"clk_gcc_camss_csi{index}phy_clk",
                       "clk_gcc_camss_ahb_clk"]
        require(specifiers == [constants[name] for name in identifiers],
                f"CSIPHY{index} compiled clock IDs/order differ: {specifiers}")
        print(f"  CSIPHY{index}: 7 references/names/rates; index 5 csi_phy_clk -> "
              f"{identifiers[5]} ({specifiers[5]:#x}); index 6 camss_ahb_clk -> "
              f"{identifiers[6]} ({specifiers[6]:#x})")


def kbuild_inputs(out):
    command_file = out / "arch/arm64/boot/.Image.gz-dtb.cmd"
    assignments = re.findall(r"^cmd_\S+\s*:=\s*(.+)$", command_file.read_text(), re.MULTILINE)
    require(len(assignments) == 1, "expected one saved Image.gz-dtb Kbuild command")
    match = re.match(r"\(\s*cat\s+(.+?)\s*>\s*([^;]+?)\s*\)\s*\|\|", assignments[0])
    require(match is not None, "unexpected Kbuild Image.gz-dtb command; refusing to guess order")
    inputs = [(out / name).resolve() for name in shlex.split(match[1])]
    targets = shlex.split(match[2])
    require(len(targets) == 1 and (out / targets[0]).resolve()
            == out / "arch/arm64/boot/Image.gz-dtb", "unexpected cat output path")
    require(len(inputs) == 7, f"expected Image.gz plus six DTBs, found {len(inputs)} inputs")
    require(inputs[0] == out / "arch/arm64/boot/Image.gz", "first Kbuild input is not Image.gz")
    expected = {f"msm8953-sec-j7poplte_usa_spr-r{i:02}.dtb" for i in range(6)}
    require({path.name for path in inputs[1:]} == expected,
            "Kbuild inputs are not exactly the six Sprint revision DTBs")
    return inputs


def verify(kernel, out):
    configuration = (out / ".config").read_text().splitlines()
    for expected in ("CONFIG_ARM64=y", "CONFIG_ARCH_MSM8953=y",
                     "CONFIG_SEC_J7POPLTE_USA_SPR=y", "CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE=y"):
        require(expected in configuration, f"missing required build setting: {expected}")
    inputs = kbuild_inputs(out)
    image_file = out / "arch/arm64/boot/Image.gz-dtb"
    image = image_file.read_bytes()
    require(bool(image), "empty Image.gz-dtb")
    compressed = inputs[0].read_bytes()
    uncompressed = gzip.decompress(compressed)  # Validates gzip length and CRC.
    require(uncompressed == (out / "arch/arm64/boot/Image").read_bytes(),
            "Image.gz does not decompress to the built Image")
    require(uncompressed[56:60] == b"ARM\x64", "missing ARM64 Image header magic")
    require(image[:len(compressed)] == compressed, "Image.gz-dtb has an incorrect gzip prefix")
    constants = clock_constants(kernel)
    dtc = out / "scripts/dtc/dtc"
    require(dtc.is_file(), "built native scripts/dtc/dtc is missing")
    verification_dir = out / "verification"
    verification_dir.mkdir(exist_ok=True)
    offset = len(compressed)
    for position, path in enumerate(inputs[1:], 1):
        blob = path.read_bytes()
        require(image[offset:offset + len(blob)] == blob,
                f"appended DTB {position} does not match Kbuild input {path.name}")
        print(f"DTB {position}: {path.name}; offset={offset}; totalsize={len(blob)}")
        nodes = parse_fdt(blob)
        model = strings(nodes["/"]["model"])
        require(model == ["Samsung Electronics. j7poplte_usa_spr"],
                f"incorrect device model in {path.name}: {model}")
        verify_csiphy(nodes, constants)
        result = subprocess.run([str(dtc), "-I", "dtb", "-O", "dts", "-o",
                                 str(verification_dir / (path.stem + ".dts")), str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        (verification_dir / (path.stem + ".dtc.log")).write_bytes(result.stdout + result.stderr)
        require(result.returncode == 0, f"native dtc rejected {path.name}; see out/verification")
        offset += len(blob)
    require(offset == len(image), f"extra/truncated appended data: consumed {offset}, image {len(image)}")
    print(f"Verified exact Kbuild concatenation: gzip Image + 6 complete Sprint DTBs ({len(image)} bytes)")
    print(f"SHA256 {hashlib.sha256(image).hexdigest()}  Image.gz-dtb")
    print("This verifies the build artifact; device boot and camera operation remain untested.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path, help="kernel source checkout")
    parser.add_argument("--out", required=True, type=Path, help="Kbuild output directory")
    arguments = parser.parse_args()
    try:
        verify(arguments.kernel.resolve(), arguments.out.resolve())
    except (VerificationError, OSError, KeyError, UnicodeError, struct.error, EOFError,
            zlib.error) as error:
        print(f"Image verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
