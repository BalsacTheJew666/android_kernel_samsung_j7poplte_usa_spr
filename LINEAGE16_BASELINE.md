# J727P LineageOS 16: Pinned Baseline

This branch is a separate development/CI scaffold. **It is not a KernelSU build, a modified boot image, or a flashable zip.** The original repository master branch remains Linux 3.18.31 and is not overwritten.

## Verified source tree

- Device: Samsung Galaxy J7 Perx SM-J727P, codename `j7popltespr`, ARM64 MSM8953.
- Upstream kernel: https://github.com/Galaxy-MSM8953/android_kernel_samsung_msm8953
- Exact pinned kernel SHA: `353d0e99d2e825e6517881c4df6011332aa75b31`
- Upstream branch: `lineage-16.0`
- Submodule path in this repository: `lineage16/kernel/samsung/msm8953`
- Kernel Linux release in Makefile: 3.18.140
- Defconfig: `arch/arm64/configs/j7popltespr_defconfig`
- Build output: `arch/arm64/boot/Image.gz-dtb`
- Matching device configuration: https://github.com/Galaxy-MSM8953/android_device_samsung_j7popltespr/tree/lineage-16.0
- ROM BoardConfig.mk uses `TARGET_KERNEL_SOURCE := kernel/samsung/msm8953` and `TARGET_KERNEL_CONFIG := j7popltespr_defconfig`.

## Cross compiler

- Toolchain: https://github.com/LineageOS/android_prebuilts_gcc_linux-x86_aarch64_aarch64-linux-android-4.9
- Revision: `a61b4b9ea2a5098dbf113999526978aec683753b` (`lineage-17.1` branch when pinned)
- Submodule: `lineage16/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9`
- Compiler executable relative to this repository:
  `lineage16/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9/bin/aarch64-linux-android-gcc`
- Compiler prefix: same path ending in `/bin/aarch64-linux-android-`.

**The exact toolchain used by the historical 2019 `github-actions@j727p-camera` build is not known.** This pinned GCC 4.9 checkout is a reproducible candidate, not proof of a binary-identical rebuild.

The older Linux 3.18.31 `build_kernel.sh` in the repository's master branch refers to `../PLATFORM/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9/bin`; do not use that older kernel configuration to build the LineageOS 16 baseline.

## Local setup / worktree audit

Clone this branch on a Linux PC or build runner, not on the phone with less than 1 GB of free storage:

```bash
git clone -b j727p-lineage16-baseline-ci --recurse-submodules https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr.git j727p-baseline
cd j727p-baseline
bash scripts/check-local-changes.sh
```

The checked-out submodule source must be **clean**, including no staged or unstaged changes, before calling this a clean baseline. A GitHub hosted remote can show committed SHA and submodule pins but cannot reveal uncommitted files in someone else's PC checkout.

The phone reports a running kernel release `3.18.140-g353d0e99-dirty`. The `-dirty` suffix indicates that the source worktree used for that build was not clean; the actual uncommitted changes are not available from the released commit SHA or extracted kernel. In particular, camera/torch changes need separate identification before flashing a replacement.

## CI/build

Workflow: `.github/workflows/j727p-lineage16-baseline.yml`.

Trigger by pushing to `j727p-lineage16-baseline-ci`; an optional manual dispatch is defined. The runner uses Ubuntu 22.04, checks out both pinned submodules, runs `scripts/build-j727p-baseline.sh`, captures `artifacts/build.log`, and uploads logs and any generated config/kernel images to GitHub Actions.

The script stops if submodule SHAs do not match or either submodule is dirty. It does **not** make the resulting kernel bootable on its own.

On a Linux PC with dependencies and initialized submodules, run `bash scripts/build-j727p-baseline.sh`. Generated files go outside this repository to `../j727p-build-out` and `../artifacts`.

Known device boot reference: Android boot header v0, page size 2048, ramdisk gzip, appended 1.4 MB DTB, Samsung SEANDROID footer, partition size 33554432. Original phone boot backup SHA-256: `f81ff20675f58f40d521059428e234656f6d4e099c6c1d485893454709cf86f6`. **Never put the backup in the public GitHub repository**; it may contain device-specific data.

No kernel modification, root-manager migration, or on-device flashing is performed by CI.
