#!/usr/bin/env bash
# Reproducible source and compiler selection for Samsung SM-J727P / LOS 16,
# camera patch v2 (per-controller CSID clocks and seven-clock CSIPHY DT).
set -euo pipefail

PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
KERNEL_SHA=353d0e99d2e825e6517881c4df6011332aa75b31
AARCH64_SHA=961622e926a1b21382dba4dd9fe0e5fb3ee5ab7c
ARM_SHA=cb7b3ac1b7fdb49474ff68761909934d1142f594
KERNEL_DIR=${KERNEL_DIR:-"$PROJECT_DIR/kernel"}
TOOLCHAIN_DIR=${TOOLCHAIN_DIR:-"$PROJECT_DIR/toolchains"}
OUT_DIR=${OUT_DIR:-"$PROJECT_DIR/out"}
LOG_DIR=${LOG_DIR:-"$PROJECT_DIR/logs"}
ARTIFACT_DIR=${ARTIFACT_DIR:-"$PROJECT_DIR/artifacts"}
mkdir -p "$LOG_DIR" "$ARTIFACT_DIR" "$TOOLCHAIN_DIR"
exec > >(tee "$LOG_DIR/build-project.log") 2>&1

collect() {
  local result=$?
  trap - EXIT
  if [[ -f "$OUT_DIR/.config" ]]; then
    cp "$OUT_DIR/.config" "$ARTIFACT_DIR/kernel.config"
  fi
  if [[ -f "$LOG_DIR/applied-camera.patch" ]]; then
    cp "$LOG_DIR/applied-camera.patch" "$ARTIFACT_DIR/j727p-camera.patch"
  fi
  if [[ -d "$OUT_DIR/verification" ]]; then
    cp -r "$OUT_DIR/verification" "$LOG_DIR/"
  fi
  printf 'exit_status=%s\n' "$result" >> "$ARTIFACT_DIR/build-info.txt"
  if [[ $result -eq 0 ]]; then
    (cd "$ARTIFACT_DIR" && sha256sum Image.gz-dtb kernel.config j727p-camera.patch build-info.txt > SHA256SUMS)
  fi
  exit "$result"
}
trap collect EXIT

clone_pinned() {
  local url=$1 branch=$2 revision=$3 destination=$4
  if [[ ! -d "$destination/.git" ]]; then
    git clone --depth 1 --branch "$branch" "$url" "$destination"
  fi
  if [[ $(git -C "$destination" rev-parse HEAD) != "$revision" ]]; then
    git -C "$destination" fetch --depth 1 origin "$revision"
    git -C "$destination" checkout --detach "$revision"
  fi
  test "$(git -C "$destination" rev-parse HEAD)" = "$revision"
}

clone_pinned https://github.com/Galaxy-MSM8953/android_kernel_samsung_msm8953.git \
  lineage-16.0 "$KERNEL_SHA" "$KERNEL_DIR"
clone_pinned https://android.googlesource.com/platform/prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9 \
  pie-release "$AARCH64_SHA" "$TOOLCHAIN_DIR/aarch64"
clone_pinned https://android.googlesource.com/platform/prebuilts/gcc/linux-x86/arm/arm-linux-androideabi-4.9 \
  pie-release "$ARM_SHA" "$TOOLCHAIN_DIR/arm"

export ARCH=arm64 SUBARCH=arm64
export CROSS_COMPILE="$TOOLCHAIN_DIR/aarch64/bin/aarch64-linux-android-"
export CROSS_COMPILE_ARM32="$TOOLCHAIN_DIR/arm/bin/arm-linux-androideabi-"
export CROSS_COMPILE_COMPAT="$CROSS_COMPILE_ARM32"
export PATH="$TOOLCHAIN_DIR/aarch64/bin:$TOOLCHAIN_DIR/arm/bin:$PATH"
export KBUILD_BUILD_USER=github-actions KBUILD_BUILD_HOST=j727p-camera
export KBUILD_BUILD_VERSION=2
# Set this in the environment so subdirectory Makefiles can append include paths.
export HOST_EXTRACFLAGS=-fcommon
KBUILD_BUILD_TIMESTAMP=$(git -C "$KERNEL_DIR" show -s --format=%cI "$KERNEL_SHA")
export KBUILD_BUILD_TIMESTAMP

# Invoke the AOSP Python wrapper explicitly: no /usr/bin/python or Python 2 needed.
python3 "${CROSS_COMPILE}gcc" --version | tee "$LOG_DIR/gcc-aarch64.log"
python3 "${CROSS_COMPILE_ARM32}gcc" --version | tee "$LOG_DIR/gcc-arm.log"
python3 "${CROSS_COMPILE}gcc" --version | head -1 | grep -F '4.9'
python3 "${CROSS_COMPILE_ARM32}gcc" --version | head -1 | grep -F '4.9'
{
  printf 'device=SM-J727P\ncodename=j7popltespr\nandroid=LineageOS 16 / Android 9\n'
  printf 'camera_patch_version=2\nbaseline_successful_workflow_run=37992808844\n'
  printf 'kernel_source=%s\naarch64_toolchain=%s\narm_toolchain=%s\n' \
    "$KERNEL_SHA" "$AARCH64_SHA" "$ARM_SHA"
  printf 'project_commit=%s\n' "$(git -C "$PROJECT_DIR" rev-parse HEAD)"
  printf 'camera_patch_sha256=%s\n' "$(sha256sum "$PROJECT_DIR/patches/j727p-camera.patch" | cut -d ' ' -f 1)"
  printf 'timestamp=%s\n' "$KBUILD_BUILD_TIMESTAMP"
  printf 'host_extra_cflags=-fcommon\n'
} > "$ARTIFACT_DIR/build-info.txt"

python3 "$PROJECT_DIR/scripts/test_patch_verify.py" --kernel "$KERNEL_DIR" \
  2>&1 | tee "$LOG_DIR/patch-tests.log"
python3 "$PROJECT_DIR/scripts/patch_verify.py" --kernel "$KERNEL_DIR" --apply \
  2>&1 | tee "$LOG_DIR/patch-apply.log"
python3 "$PROJECT_DIR/scripts/patch_verify.py" --kernel "$KERNEL_DIR" \
  2>&1 | tee "$LOG_DIR/patch-verify.log"
python3 "$PROJECT_DIR/scripts/test_csid_clock_ownership.py" --kernel "$KERNEL_DIR" \
  2>&1 | tee "$LOG_DIR/csid-clock-ownership.log"
git -C "$KERNEL_DIR" diff --check
git -C "$KERNEL_DIR" diff --binary --full-index -- \
  arch/arm/boot/dts/qcom/msm8953-camera.dtsi \
  drivers/media/platform/msm/camera_v2_j7poplte/common/msm_camera_io_util.c \
  drivers/media/platform/msm/camera_v2_j7poplte/sensor/csid/msm_csid.c \
  drivers/media/platform/msm/camera_v2_j7poplte/sensor/csid/msm_csid.h \
  > "$LOG_DIR/applied-camera.patch"
cmp "$PROJECT_DIR/patches/j727p-camera.patch" "$LOG_DIR/applied-camera.patch"

make_args=(-C "$KERNEL_DIR" O="$OUT_DIR" ARCH=arm64 \
  CROSS_COMPILE="$CROSS_COMPILE" CC="python3 ${CROSS_COMPILE}gcc")
make "${make_args[@]}" j7popltespr_defconfig 2>&1 | tee "$LOG_DIR/defconfig.log"
for setting in CONFIG_ARM64=y CONFIG_ARCH_MSM8953=y CONFIG_SEC_J7POPLTE_USA_SPR=y \
  CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE=y; do
  grep -Fx "$setting" "$OUT_DIR/.config"
done
make "${make_args[@]}" -j"${BUILD_JOBS:-$(nproc)}" Image.gz-dtb \
  2>&1 | tee "$LOG_DIR/kernel-build.log"

test -s "$OUT_DIR/arch/arm64/boot/Image.gz-dtb"
gzip -t "$OUT_DIR/arch/arm64/boot/Image.gz"
python3 "$PROJECT_DIR/scripts/verify_image.py" --kernel "$KERNEL_DIR" --out "$OUT_DIR" \
  2>&1 | tee "$LOG_DIR/image-verify.log"
cp "$OUT_DIR/arch/arm64/boot/Image.gz-dtb" "$ARTIFACT_DIR/Image.gz-dtb"
cp "$OUT_DIR/.config" "$ARTIFACT_DIR/kernel.config"
echo 'Built camera patch v2 Image.gz-dtb. Device boot and camera operation have not been tested.'
