# Samsung SM-J727P camera kernel build

This build project compiles `j7popltespr_defconfig` and `Image.gz-dtb` for Samsung
SM-J727P (Sprint J7 Pop), LineageOS 16 / Android 9. It fetches
[Galaxy-MSM8953/android_kernel_samsung_msm8953](https://github.com/Galaxy-MSM8953/android_kernel_samsung_msm8953/tree/lineage-16.0)
at lineage-16.0 commit `353d0e99d2e825e6517881c4df6011332aa75b31`.

The project lives on the `j727p-camera-build` branch. Its source is the pinned
Galaxy-MSM8953 tree fetched into a separate build directory.

## Camera patch v2

The pinned active CSID driver originally kept `csid_clk_info` and
`csid_clk_src_info` in static global arrays. Probing CSID1 and CSID2 overwrote
CSID0's clock names, so CSID0 could request `csi2_phy_clk` from its own DT node
and fail with `-ENOENT` (`-2`). V2 embeds both fixed-size arrays in each
`csid_device`, alongside its existing clock handles, and uses that instance at
every initialization, enable, disable, reset unwind, and source-selection site.
The arrays share the device's allocation and lifetime; no extra allocation or
clock reference is introduced.

The initialization audit also keeps negative DT count errors signed, checks
string reads before logging, bounds the legacy source-selection window, and
sets hardware compatibility before parsing clocks. Probe publishes the subdevice
after its metadata, hardware state, and IRQ are ready. Failed registration
removes the ordered-list entry and releases the subdevice, media entity, IRQ,
memory reservation, and regulator data. IRQ handlers reject a missing MMIO base.
Existing per-instance ioctl mutexes continue to serialize runtime operations.

The unchanged CSID DT mappings are:

| Controller | Actual DT node | Requested PHY clock | GCC reference |
| --- | --- | --- | --- |
| CSID0 | `/soc/qcom,csid@1b30000` | `csi0_phy_clk` | `clk_gcc_camss_csi0phy_clk` |
| CSID1 | `/soc/qcom,csid@1b30400` | `csi1_phy_clk` | `clk_gcc_camss_csi1phy_clk` |
| CSID2 | `/soc/qcom,csid@1b30800` | `csi2_phy_clk` | `clk_gcc_camss_csi2phy_clk` |

Probe and enable diagnostics identify the controller, actual DT node, and
requested PHY clock. Clock order, DT rates, zero-rate handling, clock helper
reference counting, and enable/disable unwind remain intact. The unrelated
`camera_v2` driver is untouched.

## Existing CSIPHY correction and diagnostics

The three CSIPHY nodes at `1b34000`, `1b35000`, and `1b36000` have seven clock
references but originally six names and rates. The verified order is:

| Index | GCC reference (`N` is 0, 1, or 2) | Consumer name | Rate |
| --- | --- | --- | --- |
| 0 | `clk_gcc_camss_top_ahb_clk` | `camss_top_ahb_clk` | 0 |
| 1 | `clk_gcc_camss_ispif_ahb_clk` | `ispif_ahb_clk` | 61540000 |
| 2 | `clk_csiNphytimer_clk_src` | `csiphy_timer_src_clk` | 200000000 |
| 3 | `clk_gcc_camss_csiNphytimer_clk` | `csiphy_timer_clk` | 0 |
| 4 | `clk_camss_top_ahb_clk_src` | `camss_ahb_src` | 0 |
| 5 | `clk_gcc_camss_csiNphy_clk` | `csi_phy_clk` | 0 |
| 6 | `clk_gcc_camss_ahb_clk` | `camss_ahb_clk` | 0 |

The patch inserts `csi_phy_clk` at index 5 and its zero rate. All clock references
and existing rates stay in their original order. The zero rate preserves the
inherited branch rate under the camera driver's rate handling.

Evidence in the pinned source:

- `arch/arm/boot/dts/qcom/msm8917-camera.dtsi` has the same seven-clock sequence
  with the complete names and rates.
- `drivers/clk/msm/clock-gcc-8953.c` registers these clocks through `CLK_LIST`;
  `include/dt-bindings/clock/msm-clocks-8953.h` defines their IDs. The three CSI
  PHY gates are distinct from the final camera AHB gate.
- `msm_csiphy_get_clk_info()` pairs names and rates by index; `clk_get()` resolves
  the name to the clock reference's DT index.
- `arch/arm64/boot/dts/qcom` is a symlink to the ARM directory. Only the canonical
  ARM file needs an edit; the Sprint ARM64 board includes it.
- `CONFIG_SEC_J7POPLTE_USA_SPR=y` selects `camera_v2_j7poplte` in
  `drivers/media/platform/msm/Makefile`. The diagnostics patch changes that
  driver's `common/msm_camera_io_util.c`, reporting device, function, clock name,
  index/count, errno, and requested rate for the enable path. It preserves error
  returns and cleanup flow.

`scripts/patch_verify.py` validates ordering, provider IDs, precedent, the ARM64
alias, active driver selection, and the complete pinned CSID source repair
before writing. It upgrades pristine or fully repaired v1 inputs to v2, rejects
partial repairs and unexpected source, and is idempotent. Verify with:

```sh
python3 scripts/patch_verify.py --kernel /path/to/kernel --apply
python3 scripts/patch_verify.py --kernel /path/to/kernel
python3 scripts/test_patch_verify.py --kernel /path/to/kernel
python3 scripts/test_csid_clock_ownership.py --kernel /path/to/patched/kernel
```

The ownership test compiles the actual repaired clock parser with controlled
host-side DT APIs. It checks that all three controllers retain independent
metadata across probe orders and concurrent initialization, and exercises
malformed clock input under AddressSanitizer and UndefinedBehaviorSanitizer.
This test does not run the driver on hardware. Legacy v2.2 source slot zero and
the shared source-selection helper's inherited reference/error behavior are
preserved; SM-J727P uses CSID v3.5.1. Platform removal and the wider camera stack
are outside this repair.

## Build and artifacts

[The workflow](.github/workflows/j727p-camera-kernel.yml) runs on pushes to this
branch and supports `workflow_dispatch`. It uses Ubuntu 22.04 and pins both AOSP
Android GCC 4.9 toolchains to the Android 9 / pie-release commits:

- AArch64: `961622e926a1b21382dba4dd9fe0e5fb3ee5ab7c`
- ARM: `cb7b3ac1b7fdb49474ff68761909934d1142f594`

Both identify GCC `4.9.x 20150123`. Their Python wrappers are invoked explicitly
with Python 3. Both prefixes are provided; this ARM64 tree does not build a
32-bit vDSO. `HOST_EXTRACFLAGS=-fcommon` supports the bundled DTC's duplicate
`yylloc` definitions on modern host GCC while preserving upstream host flags.

After applying and verifying the patch, the build compares the generated diff
with `patches/j727p-camera.patch`, generates the device configuration, compiles,
and verifies the active CSID object, appended image, and compiled CSIPHY and
CSID clock mappings in all six Sprint revision device trees.

Successful builds upload `Image.gz-dtb`, the generated configuration, SHA-256
checksums, and source/toolchain metadata. A separate artifact always uploads
patches, scripts, configuration when available, and logs, including failed builds.
V2 artifact names start with `j727p-camera-v2-`. Artifacts are retained for
30 days. Artifact downloads require GitHub sign-in.

For local execution, install `build-essential bc bison flex libssl-dev libelf-dev
python3 git curl xz-utils zip` and run:

```sh
bash scripts/build_kernel.sh
```

Optional environment variables: `KERNEL_DIR`, `TOOLCHAIN_DIR`, `OUT_DIR`,
`LOG_DIR`, `ARTIFACT_DIR`, and `BUILD_JOBS`. Use absolute directory paths. The
build emits pinned source and toolchain information and uses the upstream commit
timestamp for the kernel build timestamp.

The output is a compiled kernel image, not a flashable boot image. No device is
flashed by this project. A successful build verifies compilation and device-tree
consistency; device boot and camera operation require testing on the SM-J727P.
