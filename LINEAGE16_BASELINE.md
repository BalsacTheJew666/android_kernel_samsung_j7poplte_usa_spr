# J727P LineageOS 16: pinned clean-source baseline

This is a non-flashing CI setup on a separate branch. **It is not KernelSU yet, and a produced Image.gz-dtb is NOT itself flashable.** The existing repository `master` (Linux 3.18.31) and camera build branch remain intact.

## Exact Linux 3.18.140 kernel tree

- Upstream: https://github.com/Galaxy-MSM8953/android_kernel_samsung_msm8953
- Pinned revision: `353d0e99d2e825e6517881c4df6011332aa75b31` (`lineage-16.0`).
- Pinned git submodule within this repository: `lineage16/kernel/samsung/msm8953`.
- Source Makefile: Linux `3.18.140`.
- Device defconfig: `arch/arm64/configs/j7popltespr_defconfig`.
- Kernel build target: `Image.gz-dtb`.
- Device tree: https://github.com/Galaxy-MSM8953/android_device_samsung_j7popltespr/tree/lineage-16.0
- ROM BoardConfig.mk expects `kernel/samsung/msm8953` and `j7popltespr_defconfig`; the `lineage16/` directory is only a namespace to avoid overwriting the old source.

## Exact previously successful GCC 4.9 tools

Recovered directly from `scripts/build_kernel.sh` on the existing `j727p-camera-build` branch:

- AOSP ARM64 GCC 4.9 URL: https://android.googlesource.com/platform/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9
- Revision: `961622e926a1b21382dba4dd9fe0e5fb3ee5ab7c` (`pie-release`).
- AOSP 32-bit ARM GCC 4.9 URL: https://android.googlesource.com/platform/prebuilts/gcc/linux-x86/arm/arm-linux-androideabi-4.9
- Revision: `cb7b3ac1b7fdb49474ff68761909934d1142f594` (`pie-release`).
- These are pinned Git submodules under `lineage16/prebuilts/gcc/linux-x86/`.

Cross compile prefix (relative to checkout):
`lineage16/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9/bin/aarch64-linux-android-`.

ARM32 prefix:
`lineage16/prebuilts/gcc/linux-x86/arm/arm-linux-androideabi-4.9/bin/arm-linux-androideabi-`.

The AOSP GCC binaries are invoked through their Python 3 wrappers using `CC="python3 ${CROSS_COMPILE}gcc"`. The already successful camera workflow additionally used `HOST_EXTRACFLAGS=-fcommon`, `KBUILD_BUILD_USER=github-actions`, `KBUILD_BUILD_HOST=j727p-camera`, and `KBUILD_BUILD_VERSION=2`; our baseline script mirrors these.

The old 3.18.31 `build_kernel.sh` expects a different relative compiler path and must not be used for this build.

## Camera modifications are already preserved

- Working CI script on existing branch: https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/blob/j727p-camera-build/scripts/build_kernel.sh
- Camera patch: https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/blob/j727p-camera-build/patches/j727p-camera.patch
- Successful camera-v2 build: https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/38016519342
- The camera-v2 publication workflow records extracted kernel SHA256 `75798965cdd8db5ab160029d823b0513e7513f3f808616420018010b0c9763a2`; this agrees with the actual kernel component extracted from the phone's verified boot backup.
- The `-dirty` suffix on the phone's running kernel is consistent with that camera workflow applying its source patch to the clean baseline before compilation. It is not evidence of *current* uncommitted changes on a laptop.
- This **baseline CI deliberately does NOT apply the camera patch**, so its output should not be flashed over the working phone's camera-v2 boot.

## Inspect local uncommitted changes

GitHub's hosted repository has committed objects; it cannot reveal a separate uncommitted `git diff` or untracked files on someone's laptop or phone. The pinned upstream kernel git commit itself is immutable, and CI refuses to build a dirty source checkout.

On Linux (not on the phone, which has little free space):

```bash
git clone -b j727p-lineage16-baseline-ci --recurse-submodules https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr.git j727p-baseline
cd j727p-baseline
bash scripts/check-local-changes.sh
```

If a checkout is dirty, preserve patches with `git diff --binary`, `git diff --cached --binary`, and copy untracked files. Do not reset or clean them before backing them up.

## CI artifacts / logs

- Workflow: `.github/workflows/j727p-lineage16-baseline.yml`
- Trigger: push to this branch or manual dispatch (depending on GitHub's default-branch workflow dispatch restrictions).
- Runner: Ubuntu 22.04.
- Script: `scripts/build-j727p-baseline.sh`.
- Logs: `artifacts/setup.log` and `artifacts/build.log`.
- On success: `artifacts/Image.gz-dtb`, `artifacts/kernel.config`, `artifacts/SHA256SUMS`, `artifacts/build-info.txt`.
- Logs and whatever diagnostics exist are uploaded even when a build fails.
- No ramdisk repacking, patched source, flashable zip, or root-manager replacement is performed.

Reference phone boot: Android boot header version 0; pagesize 2048; gzip kernel; appended DTBs; gzip ramdisk; Samsung SEANDROID marker; boot partition 33554432 bytes. Verified boot backup SHA256: `f81ff20675f58f40d521059428e234656f6d4e099c6c1d485893454709cf86f6`. Keep boot backups off GitHub because they can contain device-specific data.

**A green CI result proves compilation, not safe boot or KernelSU compatibility.** After baseline validation, next port KernelSU Next and reapply the known camera patch before any attempt to boot.
