# Build result — 2026-10-09

GitHub Actions **succeeded** for project commit
`431a963ad96395d33a6dea4826557572db0ff4bc`.

- [Executed workflow run](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/37992808844)
- [Workflow source at the built commit](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/blob/431a963ad96395d33a6dea4826557572db0ff4bc/.github/workflows/j727p-camera-kernel.yml)
- [Kernel, configuration, patch, checksums and metadata](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/37992808844/artifacts/11646675290)
- [Patch, scripts, configuration and complete logs](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/37992808844/artifacts/11645549551)

Artifacts expire on 2026-11-08 and require GitHub sign-in to download.

## Validation

- Source pinned to Galaxy-MSM8953 `lineage-16.0` commit
  `353d0e99d2e825e6517881c4df6011332aa75b31`.
- Both pinned Android GCC 4.9 toolchains ran successfully.
- Eight patch tests passed locally and in Actions, including refusal of changed
  reference ordering, partial fixes, changed providers, and altered error handling.
- Python syntax, `bash -n`, `actionlint` 1.7.7, source whitespace checks, and patch
  reverse-application checks passed.
- `j7popltespr_defconfig` and `Image.gz-dtb` compiled successfully locally and in
  Actions. The active Sprint `msm_camera_io_util.c` compiled.
- Actions verified the gzip payload against the built ARM64 Image, the exact
  Kbuild concatenation, six Sprint revision DTBs, and all three CSIPHY mappings
  in each compiled DTB. Native DTC accepted all six; decompiled trees and logs
  are in the diagnostics artifact.
- The Actions image contains 12,537,970 bytes. Its build log records the image
  SHA-256; the kernel artifact contains `SHA256SUMS` for the image, generated
  configuration, source patch, and build metadata.

An extra download-side checksum check could not be completed because the cloud
workspace download proxy returned HTTP 403. GitHub confirmed both successful
artifact uploads, and the image verification completed on the Actions runner
before upload.

## Build issues investigated

The bundled DTC's duplicate `yylloc` definitions fail under modern host GCC's
default `-fno-common`; an isolated compilation reproduced this and `-fcommon`
resolved it. The first local kernel build then exposed a missing `classmap.h`:
passing `HOST_EXTRACFLAGS` on make's command line suppressed a subdirectory's
SELinux include-path additions. Exporting the flag through the environment
preserves those additions. This correction was included before the successful
Actions run. The Actions build itself passed on its first run.

New-fork creation was unavailable to the connected GitHub integration. The
complete build project was committed to the isolated `j727p-camera-build` branch
of the existing Samsung kernel repository. Its build script fetches and patches
the specified Galaxy-MSM8953 source.

No device was flashed. The artifact is a raw compiled kernel image. Device boot
and camera operation on the SM-J727P remain untested.
