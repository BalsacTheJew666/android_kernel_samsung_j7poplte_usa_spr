# Camera patch v2 build result — 2026-10-10

GitHub Actions **succeeded** for project commit
[`0782a249c69f322f1fa323999b45e658d5ae6cf6`](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/commit/0782a249c69f322f1fa323999b45e658d5ae6cf6)
on the existing `j727p-camera-build` branch. The local build also succeeded.

- [Successful workflow run 38016519342](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/38016519342)
- [Kernel, generated configuration, source diff, checksums and metadata](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/38016519342/artifacts/11656008512)
- [Complete build and verification logs, source diff, configuration and scripts](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/38016519342/artifacts/11656073509)
- [Previous successful v1 baseline run 37992808844](https://github.com/BalsacTheJew666/android_kernel_samsung_j7poplte_usa_spr/actions/runs/37992808844)

Artifacts expire on 2026-11-09 and require GitHub sign-in to download.

## Repair and audit

The active `camera_v2_j7poplte` CSID driver used static global clock-information
arrays. Later probes could replace CSID0's PHY name with `csi2_phy_clk`, leading
to `clk_get()` returning `-ENOENT` (`-2`). Both arrays are now embedded in each
`csid_device`. Initialization, enable, all three disable/unwind paths, and source
selection use that controller's storage. Clock order, requested rates,
zero-rate conversion to `-1`, and the common helper's reference handling are
preserved. Controller/node/requested-PHY diagnostics run at probe and enable.

The audit also addressed negative DT count handling, logging only after successful
string reads, legacy source-window bounds, and hardware-version initialization
before clock parsing. Probe now completes state/resource initialization before
subdevice publication. Registration failures remove the ordered-list entry and
unregister before freeing the entity, IRQ, memory reservation, regulator data,
and instance. IRQ handlers guard against a missing MMIO mapping.

The kernel diff contains exactly four files: the existing CSIPHY DTS repair and
active common clock diagnostics, plus active `msm_csid.c` and `msm_csid.h`.
The unrelated `camera_v2` driver, voltage values, and frequency values are
unchanged. The previous three CSIPHY seven-clock corrections remain intact.

## Validation

- Kernel source remains pinned to Galaxy-MSM8953 `lineage-16.0` commit
  `353d0e99d2e825e6517881c4df6011332aa75b31`.
- Android GCC 4.9 pins remain AArch64
  `961622e926a1b21382dba4dd9fe0e5fb3ee5ab7c` and ARM
  `cb7b3ac1b7fdb49474ff68761909934d1142f594`; both report
  `4.9.x 20150123`.
- All 20 patch tests passed locally and in Actions, including pristine and
  complete-v1 upgrades, idempotence, refusal of partial fixes or source drift,
  guarded clock consumers/cleanup, and rollback after an injected write failure.
- The host test compiles the actual repaired declarations, parser, and diagnostics
  with mocked OF APIs and actual target DTS fixtures under ASan and UBSan. All
  six probe orders and 3,000 concurrent initializations retain independent
  controller names/rates. Both main and source array storage are distinct.
  Malformed DT reads, array bounds, and controlled legacy source parsing pass.
- `j7popltespr_defconfig` and `Image.gz-dtb` compiled locally and in Actions.
  The active ARM64 CSID object and saved compiler command select
  `camera_v2_j7poplte`. Local DWARF places the two arrays at separate member
  offsets (488 and 872); `nm` finds neither as a global symbol.
- Image verification checks the gzip payload against the built ARM64 Image,
  the exact saved Kbuild concatenation, and all six Sprint revision DTBs.
  Each DTB retains all three seven-clock CSIPHY mappings and all three unchanged
  nine-clock CSID mappings:

| Controller | Actual node | PHY name | Provider reference |
| --- | --- | --- | --- |
| CSID0 | `/soc/qcom,csid@1b30000` | `csi0_phy_clk` | `clk_gcc_camss_csi0phy_clk` |
| CSID1 | `/soc/qcom,csid@1b30400` | `csi1_phy_clk` | `clk_gcc_camss_csi1phy_clk` |
| CSID2 | `/soc/qcom,csid@1b30800` | `csi2_phy_clk` | `clk_gcc_camss_csi2phy_clk` |

Python syntax, Bash syntax, actionlint 1.7.7, source whitespace checks, exact
patch comparison, and patch reverse-application checks passed. Actions completed
on its first attempt with no build failures or sanitizer findings. Its existing
warning kinds match the v1 baseline.

## Uploaded artifact verification

The Actions `Image.gz-dtb` contains **12,537,732 bytes**, with SHA-256:

```text
947ffd2ffaa4b1fff85c2df179938d03a91109cebbc5191537c380d1c94ee9de
```

Both downloaded ZIPs match GitHub's recorded archive digests. All four internal
`SHA256SUMS` entries pass. The image, configuration, source diff, and metadata
match between the two artifacts. Uploaded scripts and the applied source diff
match the committed project. The diagnostics ZIP contains all configuration,
compiler, patch, ownership, image-verification, dependency, and complete kernel
build logs, plus six decompiled DTBs and DTC logs. Download verification used
the GitHub connector, resolving the earlier workspace signed-download limitation.

## Remaining limitations

Compilation, host regression tests, and DT consistency do not prove that the
camera is fixed. Boot, actual probe/enable diagnostics, front/rear capture,
video, and repeated power cycles still need testing on the SM-J727P with the
LineageOS 16 camera stack. No device was flashed. The artifact is a raw compiled
kernel image, not a packaged flashable boot image.

Legacy CSID v2.2 source slot zero and the shared source-selection helper's
inherited reference/error behavior are preserved; the target uses v3.5.1.
Shared regulator metadata remains unchanged (the three target nodes specify the
same voltage), and the driver still has no platform remove callback. These and
the wider camera stack are outside this per-controller clock repair.
