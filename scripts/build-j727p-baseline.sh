#!/usr/bin/env bash
# Read-only for source checkouts. Compiles out of tree on Linux CI; does not flash.
set -Eeuo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -n "$(printenv GITHUB_WORKSPACE || :)" ]; then
  WORKSPACE=$(printenv GITHUB_WORKSPACE)
else
  WORKSPACE=$(dirname "$REPO_ROOT")
fi

SOURCE="$REPO_ROOT/lineage16/kernel/samsung/msm8953"
TC="$REPO_ROOT/lineage16/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9"
CROSS="$TC/bin/aarch64-linux-android-"
OUT="$WORKSPACE/j727p-build-out"
ARTIFACTS="$WORKSPACE/artifacts"
SOURCE_SHA=353d0e99d2e825e6517881c4df6011332aa75b31
TOOLCHAIN_SHA=a61b4b9ea2a5098dbf113999526978aec683753b

mkdir -p "$OUT" "$ARTIFACTS"
echo "===== Exact source and compiler ====="
test -f "$SOURCE/Makefile" || { echo "Missing kernel submodule. Run: git submodule update --init --recursive"; exit 2; }
test -x "${CROSS}gcc" || { echo "Missing executable GCC 4.9 submodule compiler"; exit 2; }

test "$(git -C "$SOURCE" rev-parse HEAD)" = "$SOURCE_SHA" || { echo "Kernel source SHA mismatch"; exit 2; }
test "$(git -C "$TC" rev-parse HEAD)" = "$TOOLCHAIN_SHA" || { echo "Toolchain SHA mismatch"; exit 2; }

if [ -n "$(git -C "$SOURCE" status --porcelain --untracked-files=normal)" ]; then
  echo "STOP: source checkout contains local changes:"
  git -C "$SOURCE" status --short
  exit 2
fi
if [ -n "$(git -C "$TC" status --porcelain --untracked-files=normal)" ]; then
  echo "STOP: toolchain checkout contains local changes:"
  git -C "$TC" status --short
  exit 2
fi

printf 'Kernel source SHA: %s\nToolchain SHA: %s\n' "$SOURCE_SHA" "$TOOLCHAIN_SHA" | tee "$ARTIFACTS/source-revisions.txt"
head -3 "$SOURCE/Makefile" | tee -a "$ARTIFACTS/source-revisions.txt"
"${CROSS}gcc" --version | head -1 | tee "$ARTIFACTS/compiler-version.txt"
echo "Host build compiler: $(gcc --version | head -1)"
echo "Host make: $(make --version | head -1)"

echo "===== Produce baseline defconfig ====="
make -C "$SOURCE" O="$OUT" ARCH=arm64 CROSS_COMPILE="$CROSS" j7popltespr_defconfig
cp "$OUT/.config" "$ARTIFACTS/j7popltespr.config"

echo "===== Compile Image.gz-dtb; no KernelSU patches ====="
make -C "$SOURCE" O="$OUT" ARCH=arm64 CROSS_COMPILE="$CROSS" -j2 Image.gz-dtb

IMAGE="$OUT/arch/arm64/boot/Image.gz-dtb"
test -s "$IMAGE"
cp "$IMAGE" "$ARTIFACTS/J727P-baseline-Image.gz-dtb"
sha256sum "$ARTIFACTS/J727P-baseline-Image.gz-dtb" | tee "$ARTIFACTS/SHA256SUMS"
echo "BASELINE BUILD SUCCESS (not a flashable boot image)"
