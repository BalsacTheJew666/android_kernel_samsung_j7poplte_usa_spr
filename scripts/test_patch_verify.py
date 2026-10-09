#!/usr/bin/env python3
"""Exercise patch preflight and edits with isolated copies of real source files."""

import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import patch_verify as patch


SOURCE = None


class PatchVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="j727p-patch-test-")
        self.addCleanup(self.temporary.cleanup)
        self.kernel = Path(self.temporary.name)
        # Copy just the real evidence and affected source files, retaining the
        # architecture's actual shared DTS layout instead of copying it twice.
        for relative in patch.INPUTS:
            destination = self.kernel / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / relative, destination)
        arm64 = self.kernel / "arch/arm64/boot/dts"
        arm64.mkdir(parents=True)
        (arm64 / "qcom").symlink_to("../../../arm/boot/dts/qcom", target_is_directory=True)
        # Tests accept either the original or a fully repaired checkout as a
        # source. Derive pristine test fixtures using the known inverse edits.
        _, _, state = patch.preflight(self.kernel)
        if state == "fixed":
            dts = self.kernel / patch.DTS
            text = dts.read_text()
            text = text.replace(patch.FIXED_NAMES_TEXT, patch.OLD_NAMES_TEXT)
            text = text.replace(patch.FIXED_RATES_TEXT, patch.OLD_RATES_TEXT)
            dts.write_text(text)
            camera = self.kernel / patch.DIAGNOSTICS
            text = camera.read_text()
            for old, fixed in patch.DIAGNOSTIC_EDITS:
                text = text.replace(fixed, old)
            camera.write_text(text)
        self.assertEqual(patch.preflight(self.kernel)[2], "pristine")

    def snapshot(self):
        return {relative: (self.kernel / relative).read_bytes() for relative in patch.INPUTS}

    def assert_refused_without_writes(self):
        before = self.snapshot()
        with self.assertRaises(patch.VerificationError):
            patch.run(self.kernel, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_pristine_apply_and_idempotence(self):
        before = self.snapshot()
        with self.assertRaises(patch.VerificationError):
            patch.run(self.kernel)
        self.assertEqual(patch.run(self.kernel, apply=True), "applied")
        self.assertEqual(patch.run(self.kernel), "verified")
        after = self.snapshot()
        changed = {p for p in before if before[p] != after[p]}
        self.assertEqual(changed, {patch.DTS, patch.DIAGNOSTICS})
        self.assertEqual(patch.run(self.kernel, apply=True), "already-fixed")
        self.assertEqual(after, self.snapshot())
        self.assertEqual((self.kernel / patch.ARM64_DTS).read_bytes(), after[patch.DTS])

    def test_clock_order_drift_refused_before_any_write(self):
        path = self.kernel / patch.DTS
        text = path.read_text().replace(
            "<&clock_gcc clk_gcc_camss_csi1phy_clk>",
            "<&clock_gcc clk_gcc_camss_csi2phy_clk>", 1)
        path.write_text(text)
        self.assert_refused_without_writes()

    def test_partial_repair_refused_before_any_write(self):
        path = self.kernel / patch.DTS
        text = path.read_text().replace(patch.OLD_NAMES_TEXT, patch.FIXED_NAMES_TEXT, 1)
        text = text.replace(patch.OLD_RATES_TEXT, patch.FIXED_RATES_TEXT, 1)
        path.write_text(text)
        self.assert_refused_without_writes()

    def test_provider_evidence_drift_refused_before_any_write(self):
        path = self.kernel / patch.PROVIDER
        path.write_text(path.read_text().replace("CLK_LIST(gcc_camss_csi2phy_clk)",
                                                "CLK_LIST(gcc_camss_csi2pix_clk)", 1))
        self.assert_refused_without_writes()

    def test_precedent_drift_refused_before_any_write(self):
        path = self.kernel / patch.PRECEDENT
        path.write_text(path.read_text().replace('"csi_phy_clk"', '"incorrect_phy_clk"', 1))
        self.assert_refused_without_writes()

    def test_active_camera_selection_drift_refused(self):
        path = self.kernel / patch.DEFCONFIG
        path.write_text(path.read_text().replace("CONFIG_SEC_J7POPLTE_USA_SPR=y",
                                                "CONFIG_SEC_J7POPLTE_USA_SPR=n", 1))
        self.assert_refused_without_writes()

    def test_bad_diagnostics_cannot_pass_fixed_verification(self):
        patch.run(self.kernel, apply=True)
        path = self.kernel / patch.DIAGNOSTICS
        path.write_text(path.read_text().replace("PTR_ERR(src_clk));", "PTR_ERR(mux_clk));", 1))
        with self.assertRaises(patch.VerificationError):
            patch.run(self.kernel)
        self.assert_refused_without_writes()

    def test_changed_unwind_refused_before_any_write(self):
        path = self.kernel / patch.DIAGNOSTICS
        path.write_text(path.read_text().replace("goto cam_clk_get_err;",
                                                "goto cam_clk_set_err;", 1))
        self.assert_refused_without_writes()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, default=Path(os.environ.get("KERNEL_SOURCE", "kernel")),
                        help="Real pristine or completely patched kernel checkout")
    args, unittest_args = parser.parse_known_args()
    SOURCE = args.kernel.resolve(strict=True)
    unittest.main(argv=[sys.argv[0]] + unittest_args, verbosity=2)
