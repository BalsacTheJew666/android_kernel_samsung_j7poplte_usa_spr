#!/usr/bin/env bash
# Baseline only (no camera patches, no KernelSU). No flashing or boot image modifications.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORKSPACE=$(printenv GITHUB_WORKSPACE || :)
if [ -z "$WORKSPACE" ]; then WORKSPACE=$(dirname "$ROOT"); fi

KERNEL="$ROOT/lineage16/kernel/samsung/msm8953"
TC_ROOT="$ROOT/lineage16/prebuilts/gcc/linux-x86"
AARCH64="$TC_ROOT/aarch64/aarch64-linux-android-4.9"
ARM32="$TC_ROOT/arm/arm-linux-androideabi-4.9"
KERNEL_SHA=353d0e99d2e825e6517881c4df6011332aa75b31
AARCH64_SHA=961622e926a1b21382dba4dd9fe0e5fb3ee5ab7c
ARM32_SHA=cb7b3ac1b7fdb49474ff68761909934d1142f594
OUT="$WORKSPACE/j727p-build-out"
ARTIFACTS="$WORKSPACE/artifacts"
mkdir -p "$OUT" "$ARTIFACTS"

echo "===== Require exact clean kernel / GCC 4.9 commits ====="
for pair in "$KERNEL:$KERNEL_SHA" "$AARCH64:$AARCH64_SHA" "$ARM32:$ARM32_SHA"; do
  directory=$(printf '%s' "$pair" | cut -d: -f1)
  revision=$(printf '%s' "$pair" | cut -d: -f2)
  test -e "$directory/.git" || { echo "Submodule not initialized: $directory"; exit 2; }
  actual=$(git -C "$directory" rev-parse HEAD)
  test "$actual" = "$revision" || { echo "Revision mismatch: $directory $actual"; exit 2; }
  if [ -n "$(git -C "$directory" status --porcelain --untracked-files=normal)" ]; then
    echo "Dirty submodule: $directory"
    git -C "$directory" status --short
    exit 2
  fi
done

export ARCH=arm64 SUBARCH=arm64
export CROSS_COMPILE="$AARCH64/bin/aarch64-linux-android-"
export CROSS_COMPILE_ARM32="$ARM32/bin/arm-linux-androideabi-"
export CROSS_COMPILE_COMPAT="$CROSS_COMPILE_ARM32"
export PATH="$AARCH64/bin:$ARM32/bin:$PATH"
export KBUILD_BUILD_USER=github-actions KBUILD_BUILD_HOST=j727p-camera
export KBUILD_BUILD_VERSION=2
export HOST_EXTRACFLAGS=-fcommon
KBUILD_BUILD_TIMESTAMP=$(git -C "$KERNEL" show -s --format=%cI "$KERNEL_SHA")
export KBUILD_BUILD_TIMESTAMP

test -f "${CROSS_COMPILE}gcc" || { echo "Missing AOSP GCC wrapper"; exit 2; }
test -f "${CROSS_COMPILE_ARM32}gcc" || { echo "Missing AOSP ARM GCC wrapper"; exit 2; }
python3 "${CROSS_COMPILE}gcc" --version | tee "$ARTIFACTS/gcc-aarch64-version.txt"
python3 "${CROSS_COMPILE_ARM32}gcc" --version | tee "$ARTIFACTS/gcc-arm-version.txt"
python3 "${CROSS_COMPILE}gcc" --version | head -1 | grep -F '4.9'
python3 "${CROSS_COMPILE_ARM32}gcc" --version | head -1 | grep -F '4.9'

{
  echo "kernel_sha=$KERNEL_SHA"
  echo "aarch64_toolchain_sha=$AARCH64_SHA"
  echo "arm32_toolchain_sha=$ARM32_SHA"
  echo "kernel_patch=NONE (clean baseline)"
  echo "timestamp=$KBUILD_BUILD_TIMESTAMP"
  echo "source_config=arch/arm64/configs/j7popltespr_defconfig"
  echo "running_device_kernel=3.18.140-g353d0e99-dirty (not a clean-source match)"
} > "$ARTIFACTS/build-info.txt"

make_args=(-C "$KERNEL" O="$OUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" CC="python3 ${CROSS_COMPILE}gcc")
echo "===== Generate j7popltespr defconfig ====="
make "${make_args[@]}" j7popltespr_defconfig
cp "$OUT/.config" "$ARTIFACTS/kernel.config"
for setting in CONFIG_ARM64=y CONFIG_ARCH_MSM8953=y CONFIG_SEC_J7POPLTE_USA_SPR=y CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE=y; do
  grep -Fx "$setting" "$OUT/.config"
done

echo "===== Compile clean LineageOS 16 kernel ====="
make "${make_args[@]}" -j2 Image.gz-dtb
IMAGE="$OUT/arch/arm64/boot/Image.gz-dtb"
test -s "$IMAGE"
gzip -t "$OUT/arch/arm64/boot/Image.gz"
cp "$IMAGE" "$ARTIFACTS/Image.gz-dtb"
(cd "$ARTIFACTS" && sha256sum Image.gz-dtb kernel.config > SHA256SUMS)
echo "CLEAN BASELINE KERNEL BUILT — Not a boot image; do not flash."
