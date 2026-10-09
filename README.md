# Samsung SM-J727P camera kernel build

This build project compiles `j7popltespr_defconfig` and `Image.gz-dtb` for Samsung
SM-J727P (Sprint J7 Pop), LineageOS 16 / Android 9. It fetches
[Galaxy-MSM8953/android_kernel_samsung_msm8953](https://github.com/Galaxy-MSM8953/android_kernel_samsung_msm8953/tree/lineage-16.0)
at lineage-16.0 commit `353d0e99d2e825e6517881c4df6011332aa75b31`.

The project lives on the `j727p-camera-build` branch. Its source is the pinned
Galaxy-MSM8953 tree fetched into a separate build directory.

## Clock correction and diagnostics

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
alias, and active driver selection before writing. It accepts the pristine or
exactly patched state, rejects unexpected source, and is idempotent. Verify with:

```sh
python3 scripts/patch_verify.py --kernel /path/to/kernel --apply
python3 scripts/patch_verify.py --kernel /path/to/kernel
python3 scripts/test_patch_verify.py --kernel /path/to/kernel
```

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
and verifies the appended image and compiled CSIPHY device trees.

Successful builds upload `Image.gz-dtb`, the generated configuration, SHA-256
checksums, and source/toolchain metadata. A separate artifact always uploads
patches, scripts, configuration when available, and logs, including failed builds.
Artifacts are retained for 30 days. Artifact downloads require GitHub sign-in.

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
