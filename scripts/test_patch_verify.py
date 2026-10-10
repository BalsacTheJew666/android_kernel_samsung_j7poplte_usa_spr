#!/usr/bin/env python3
"""Exercise patch preflight and edits with isolated copies of real source files."""

import argparse
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

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
        if state in ("fixed", "v1"):
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
        if state == "fixed":
            for relative, edits in patch.CSID_EDITS.items():
                path = self.kernel / relative
                text = path.read_text()
                for old, fixed in reversed(edits):
                    self.assertEqual(text.count(fixed), 1)
                    text = text.replace(fixed, old, 1)
                path.write_text(text)
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
        self.assertEqual(changed, {patch.DTS, patch.DIAGNOSTICS, patch.CSID_C, patch.CSID_H})
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

    def mutate(self, relative, old, new):
        path = self.kernel / relative
        text = path.read_text()
        self.assertIn(old, text, "Test mutation must affect a real code path")
        path.write_text(text.replace(old, new, 1))

    def apply_v1(self):
        self.mutate(patch.DTS, patch.OLD_NAMES_TEXT, patch.FIXED_NAMES_TEXT)
        self.mutate(patch.DTS, patch.OLD_NAMES_TEXT, patch.FIXED_NAMES_TEXT)
        self.mutate(patch.DTS, patch.OLD_NAMES_TEXT, patch.FIXED_NAMES_TEXT)
        for _ in range(3):
            self.mutate(patch.DTS, patch.OLD_RATES_TEXT, patch.FIXED_RATES_TEXT)
        for old, fixed in patch.DIAGNOSTIC_EDITS:
            self.mutate(patch.DIAGNOSTICS, old, fixed)

    def test_complete_v1_upgrade_preserves_previous_repair(self):
        self.apply_v1()
        self.assertEqual(patch.preflight(self.kernel)[2], "v1")
        before = self.snapshot()
        with self.assertRaises(patch.VerificationError):
            patch.run(self.kernel)
        self.assertEqual(patch.run(self.kernel, apply=True), "applied")
        after = self.snapshot()
        self.assertEqual({p for p in before if before[p] != after[p]},
                         {patch.CSID_C, patch.CSID_H})
        self.assertEqual(patch.run(self.kernel), "verified")

    def test_csid_clock_names_refs_rates_and_cell_index_drift_refused(self):
        original = (self.kernel / patch.DTS).read_text()
        mutations = (
            ('"csi0_phy_clk"', '"csi2_phy_clk"'),
            ('<&clock_gcc clk_gcc_camss_csi0pix_clk>',
             '<&clock_gcc clk_gcc_camss_csi1pix_clk>'),
            ('qcom,clock-rates = <0 61540000 0 310000000 0 0 0 0 0>;',
             'qcom,clock-rates = <0 61540000 0 300000000 0 0 0 0 0>;'),
            ('reg = <0x1b30000 0x400>;', 'reg = <0x1b30400 0x400>;'),
            ('qcom,csid@1b30000  {\n\t\tstatus = "ok";\n\t\tcell-index = <0>;',
             'qcom,csid@1b30000  {\n\t\tstatus = "ok";\n\t\tcell-index = <2>;'),
        )
        for old, new in mutations:
            with self.subTest(mutation=old):
                (self.kernel / patch.DTS).write_text(original)
                self.mutate(patch.DTS, old, new)
                self.assert_refused_without_writes()

    def test_csid_header_and_c_partial_generation_refused(self):
        _, changes, _ = patch.preflight(self.kernel)
        (self.kernel / patch.CSID_H).write_text(changes[patch.CSID_H])
        self.assert_refused_without_writes()

    def test_v2_csid_without_v1_refused(self):
        _, changes, _ = patch.preflight(self.kernel)
        for relative in (patch.CSID_C, patch.CSID_H):
            (self.kernel / relative).write_text(changes[relative])
        self.assert_refused_without_writes()

    def test_pristine_csid_drift_refused_before_other_edits(self):
        self.mutate(patch.CSID_C,
                    'static struct msm_cam_clk_info csid_clk_info[CSID_NUM_CLK_MAX];',
                    'static struct msm_cam_clk_info csid_clk_info[CSID_NUM_CLK_MAX + 1];')
        self.assert_refused_without_writes()

    def test_shared_global_csid_arrays_cannot_pass_v2(self):
        patch.run(self.kernel, apply=True)
        with (self.kernel / patch.CSID_C).open("a") as handle:
            handle.write("\nstatic struct msm_cam_clk_info csid_clk_info[CSID_NUM_CLK_MAX];\n"
                         "static struct msm_cam_clk_info csid_clk_src_info[CSID_NUM_CLK_MAX];\n")
        self.assert_refused_without_writes()

    def test_pointer_ownership_or_aliased_arrays_cannot_pass_v2(self):
        patch.run(self.kernel, apply=True)
        original = (self.kernel / patch.CSID_H).read_text()
        for name in ("csid_clk_info", "csid_clk_src_info"):
            with self.subTest(member=name):
                (self.kernel / patch.CSID_H).write_text(original)
                self.mutate(patch.CSID_H,
                            f"struct msm_cam_clk_info {name}[CSID_NUM_CLK_MAX];",
                            f"struct msm_cam_clk_info *{name};")
                self.assert_refused_without_writes()

    def test_every_enable_disable_and_source_selection_use_is_guarded(self):
        patch.run(self.kernel, apply=True)
        original = (self.kernel / patch.CSID_C).read_text()
        calls = list(re.finditer(r"\bmsm_cam_clk_(?:enable|sel_src)\([^;]*;", original))
        self.assertEqual(len(calls), 5)
        self.assertEqual(sum("msm_cam_clk_enable" in m.group() for m in calls), 4)
        for number, call in enumerate(calls):
            for member in ("csid_clk_info", "csid_clk_src_info"):
                qualified = "csid_dev->" + member
                if qualified not in call.group():
                    continue
                with self.subTest(call=number, member=member):
                    bad_call = call.group().replace(qualified, member, 1)
                    (self.kernel / patch.CSID_C).write_text(
                        original[:call.start()] + bad_call + original[call.end():])
                    self.assert_refused_without_writes()

    def test_probe_or_enable_diagnostics_cannot_name_the_wrong_controller(self):
        patch.run(self.kernel, apply=True)
        original = (self.kernel / patch.CSID_C).read_text()
        for old, new in (("of_node->full_name", "of_node->name"),
                         ("csid_dev->pdev->id", "2"),
                         ('msm_csid_log_phy_clocks(csid_dev, "init requesting");',
                          'msm_csid_log_phy_clocks(csid_dev, "disabled");')):
            with self.subTest(mutation=old):
                (self.kernel / patch.CSID_C).write_text(original)
                self.mutate(patch.CSID_C, old, new)
                self.assert_refused_without_writes()

    def test_csid_bounds_failure_unwind_and_publication_are_guarded(self):
        patch.run(self.kernel, apply=True)
        original = (self.kernel / patch.CSID_C).read_text()
        mutations = (
            ("int count;", "uint32_t count;"),
            ("if (count <= 0)", "if (count == 0)"),
            ("if (count > CSID_NUM_CLK_MAX)", "if (count > CSID_NUM_CLK_MAX + 1)"),
            ("i == cnt || count < 3 || cnt - count > count - 3", "i == cnt"),
            ("goto clk_enable_failed;", "return rc;"),
            ("free_irq(new_csid_dev->irq->start, new_csid_dev);", "/* missing IRQ cleanup */"),
            ("media_entity_cleanup(&new_csid_dev->msm_sd.sd.entity);", "/* missing entity cleanup */"),
            ("list_del_init(&new_csid_dev->msm_sd.list);", "/* stale ordered-list entry */"),
            ("new_csid_dev->csid_vreg = NULL;", "/* dangling freed DT allocation */"),
            ("if (!csid_dev || !csid_dev->base)", "if (!csid_dev)"),
            ("new_csid_dev->csid_state = CSID_POWER_DOWN;",
             "platform_set_drvdata(pdev, &new_csid_dev->msm_sd.sd);\n"
             "\tnew_csid_dev->csid_state = CSID_POWER_DOWN;"),
            ("(long)-1 : rates[i];", "(long)0 : rates[i];"),
        )
        for old, new in mutations:
            with self.subTest(mutation=old):
                (self.kernel / patch.CSID_C).write_text(original)
                self.mutate(patch.CSID_C, old, new)
                self.assert_refused_without_writes()

    def test_pinned_csid_fingerprints_reject_line_ending_drift(self):
        path = self.kernel / patch.CSID_C
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assert_refused_without_writes()

    def test_write_failure_restores_every_original(self):
        before = self.snapshot()
        real_replace = patch.os.replace
        calls = 0

        def fail_second_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Simulated staged-write failure")
            return real_replace(source, destination)

        with mock.patch.object(patch.os, "replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "Simulated staged-write failure"):
                patch.run(self.kernel, apply=True)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.kernel.rglob(".msm*")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, default=Path(os.environ.get("KERNEL_SOURCE", "kernel")),
                        help="Real pristine or completely patched kernel checkout")
    args, unittest_args = parser.parse_known_args()
    SOURCE = args.kernel.resolve(strict=True)
    unittest.main(argv=[sys.argv[0]] + unittest_args, verbosity=2)
