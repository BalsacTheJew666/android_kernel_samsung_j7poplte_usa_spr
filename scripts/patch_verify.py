#!/usr/bin/env python3
"""Fail-closed v2 CSIPHY and per-controller CSID repair for the pinned kernel.

--apply verifies every input and prepares every edit before writing any file.
It accepts the pristine pinned tree or the complete v1 repair, and upgrades
either to v2. Partial repairs or source drift are rejected. Without --apply the
tree must already contain the complete v2 fix.
Only Python's standard library is needed. No compilation or flashing occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile


DTS = Path("arch/arm/boot/dts/qcom/msm8953-camera.dtsi")
ARM64_DTS = Path("arch/arm64/boot/dts/qcom/msm8953-camera.dtsi")
PRECEDENT = Path("arch/arm/boot/dts/qcom/msm8917-camera.dtsi")
PROVIDER = Path("drivers/clk/msm/clock-gcc-8953.c")
BINDING = Path("include/dt-bindings/clock/msm-clocks-8953.h")
CLK_MACRO = Path("include/linux/clk/msm-clk-provider.h")
DEFCONFIG = Path("arch/arm64/configs/j7popltespr_defconfig")
CAMERA_MAKEFILE = Path("drivers/media/platform/msm/Makefile")
DIAGNOSTICS = Path(
    "drivers/media/platform/msm/camera_v2_j7poplte/common/msm_camera_io_util.c"
)
CSID_C = Path("drivers/media/platform/msm/camera_v2_j7poplte/sensor/csid/msm_csid.c")
CSID_H = CSID_C.with_suffix(".h")
INPUTS = (DTS, PRECEDENT, PROVIDER, BINDING, CLK_MACRO, DEFCONFIG,
          CAMERA_MAKEFILE, DIAGNOSTICS, CSID_C, CSID_H)
ADDRESSES = ("1b34000", "1b35000", "1b36000")
CSID_ADDRESSES = ("1b30000", "1b30400", "1b30800")
CSID_RATES = (0, 61540000, 0, 310000000, 0, 0, 0, 0, 0)
FIXED_NAMES = ("camss_top_ahb_clk", "ispif_ahb_clk", "csiphy_timer_src_clk",
               "csiphy_timer_clk", "camss_ahb_src", "csi_phy_clk", "camss_ahb_clk")
OLD_NAMES = FIXED_NAMES[:5] + FIXED_NAMES[6:]
FIXED_RATES = (0, 61540000, 200000000, 0, 0, 0, 0)
OLD_RATES = FIXED_RATES[:-1]
OLD_NAMES_TEXT = '"camss_ahb_src", "camss_ahb_clk";'
FIXED_NAMES_TEXT = ('"camss_ahb_src", "csi_phy_clk",\n'
                    '\t\t\t"camss_ahb_clk";')
OLD_RATES_TEXT = "qcom,clock-rates = <0 61540000 200000000 0 0 0>;"
FIXED_RATES_TEXT = "qcom,clock-rates = <0 61540000 200000000 0 0 0 0>;"

# Entire failure branches are matched so the original continue, errno assignment,
# and unwind jump are checked and retained. The only differences are log calls.
DIAGNOSTIC_EDITS = (
    (
        '\t\t\tif (IS_ERR(mux_clk)) {\n'
        '\t\t\t\tpr_err("%s get failed\\n",\n'
        '\t\t\t\t\t clk_info[i].clk_name);\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
        '\t\t\tif (IS_ERR(mux_clk)) {\n'
        '\t\t\t\tdev_err(dev,\n'
        '\t\t\t\t\t"%s: clk_get clock[%d/%d] name=%s failed: %ld\\n",\n'
        '\t\t\t\t\t__func__, i, num_clk, clk_info[i].clk_name,\n'
        '\t\t\t\t\tPTR_ERR(mux_clk));\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
    ),
    (
        '\t\t\tif (IS_ERR(src_clk)) {\n'
        '\t\t\t\tpr_err("%s get failed\\n",\n'
        '\t\t\t\t\tclk_src_info[i].clk_name);\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
        '\t\t\tif (IS_ERR(src_clk)) {\n'
        '\t\t\t\tdev_err(dev,\n'
        '\t\t\t\t\t"%s: clk_get clock[%d/%d] name=%s failed: %ld\\n",\n'
        '\t\t\t\t\t__func__, i, num_clk, clk_src_info[i].clk_name,\n'
        '\t\t\t\t\tPTR_ERR(src_clk));\n'
        '\t\t\t\tcontinue;\n\t\t\t}',
    ),
    (
        '\t\t\tif (IS_ERR(clk_ptr[i])) {\n'
        '\t\t\t\tpr_err("%s get failed\\n", clk_info[i].clk_name);\n'
        '\t\t\t\trc = PTR_ERR(clk_ptr[i]);\n'
        '\t\t\t\tgoto cam_clk_get_err;\n\t\t\t}',
        '\t\t\tif (IS_ERR(clk_ptr[i])) {\n'
        '\t\t\t\tdev_err(dev,\n'
        '\t\t\t\t\t"%s: clk_get clock[%d/%d] name=%s "\n'
        '\t\t\t\t\t"requested_rate=%ld failed: %ld\\n",\n'
        '\t\t\t\t\t__func__, i, num_clk, clk_info[i].clk_name,\n'
        '\t\t\t\t\tclk_info[i].clk_rate, PTR_ERR(clk_ptr[i]));\n'
        '\t\t\t\trc = PTR_ERR(clk_ptr[i]);\n'
        '\t\t\t\tgoto cam_clk_get_err;\n\t\t\t}',
    ),
)


# The active CSID driver is pinned byte-for-byte: prerequisite and reviewed v2
# fingerprints make arbitrary source changes fail closed. Context edits keep the
# repair deterministic and reversible for the tests; no unrelated driver is read.
CSID_SIGNATURES = {
    CSID_C: {"pristine": "bdc7fa36adf6acd538aff14127fe2bd2a0e035cf4af8dbd5af1d51cca8dabda1",
              "fixed": "80ff74aa038fd0c7ce4e2cd901c9adb7fce7a0247539f591ed94add2264e95a6"},
    CSID_H: {"pristine": "9bdb6a5f74c3d54357b861d65f527b1a1a9fbc8d2df8a78eb1f58d8691604e77",
              "fixed": "7bcab43f538dab352c259ddede711096c35bdeb13b86abd55fdde1cae5a43fd0"},
}

CSID_EDITS = {
    CSID_C: (
        (r"""
#undef CDBG
#define CDBG(fmt, args...) pr_debug(fmt, ##args)

static struct msm_cam_clk_info csid_clk_info[CSID_NUM_CLK_MAX];
static struct msm_cam_clk_info csid_clk_src_info[CSID_NUM_CLK_MAX];

static struct camera_vreg_t csid_vreg_info[] = {
	{"qcom,mipi-csi-vdd", 0, 0, 12000},
""", r"""
#undef CDBG
#define CDBG(fmt, args...) pr_debug(fmt, ##args)

static struct camera_vreg_t csid_vreg_info[] = {
	{"qcom,mipi-csi-vdd", 0, 0, 12000},
"""),
        (r"""	uint32_t count = 0, dt = 0;
	struct csid_device *csid_dev = data;

	if (!csid_dev) {
		pr_err("%s:%d csid_dev NULL\n", __func__, __LINE__);
		return IRQ_HANDLED;
	}
	irq = msm_camera_io_r(csid_dev->base +
""", r"""	uint32_t count = 0, dt = 0;
	struct csid_device *csid_dev = data;

	if (!csid_dev || !csid_dev->base) {
		pr_err("%s:%d csid_dev or base NULL\n", __func__, __LINE__);
		return IRQ_HANDLED;
	}
	irq = msm_camera_io_r(csid_dev->base +
"""),
        (r"""	uint32_t irq;
	struct csid_device *csid_dev = data;

	if (!csid_dev) {
		pr_err("%s:%d csid_dev NULL\n", __func__, __LINE__);
		return IRQ_HANDLED;
	}
	irq = msm_camera_io_r(csid_dev->base +
""", r"""	uint32_t irq;
	struct csid_device *csid_dev = data;

	if (!csid_dev || !csid_dev->base) {
		pr_err("%s:%d csid_dev or base NULL\n", __func__, __LINE__);
		return IRQ_HANDLED;
	}
	irq = msm_camera_io_r(csid_dev->base +
"""),
        (r"""	ret = msm_csid_irq(csid_dev->irq->start, csid_dev);
	*handled = TRUE;
	return 0;
}

static int msm_csid_init(struct csid_device *csid_dev, uint32_t *csid_version)
""", r"""	ret = msm_csid_irq(csid_dev->irq->start, csid_dev);
	*handled = TRUE;
	return 0;
}

/* Log the names used for clk_get(), after validated DT reads. */
static void msm_csid_log_phy_clocks(struct csid_device *csid_dev,
	const char *stage)
{
	uint32_t i;
	const char *name;
	struct device_node *of_node = csid_dev->pdev->dev.of_node;

	for (i = 0; i < csid_dev->num_clk; i++) {
		name = csid_dev->csid_clk_info[i].clk_name;
		if (name && strstr(name, "phy"))
			pr_info("CSID%d node=%s %s PHY clock[%u]=%s\n",
				csid_dev->pdev->id, of_node->full_name, stage,
				i, name);
	}
	for (i = 0; i < csid_dev->num_clk_src_info; i++) {
		name = csid_dev->csid_clk_src_info[i].clk_name;
		if (name && strstr(name, "phy"))
			pr_info("CSID%d node=%s %s PHY source[%u]=%s\n",
				csid_dev->pdev->id, of_node->full_name, stage,
				i, name);
	}
}

static int msm_csid_init(struct csid_device *csid_dev, uint32_t *csid_version)
"""),
        (r"""		goto csid_vreg_enable_failed;
	}

	if (csid_dev->ctrl_reg->csid_reg.csid_version == CSID_VERSION_V22)
		msm_cam_clk_sel_src(&csid_dev->pdev->dev,
			&csid_clk_info[3], csid_clk_src_info,
			csid_dev->num_clk_src_info);

	rc = msm_cam_clk_enable(&csid_dev->pdev->dev,
			csid_clk_info, csid_dev->csid_clk,
			csid_dev->num_clk, 1);
	if (rc < 0) {
		pr_err("%s:%d clock enable failed\n",
			 __func__, __LINE__);
		goto clk_enable_failed;
	}
	CDBG("%s:%d called\n", __func__, __LINE__);
""", r"""		goto csid_vreg_enable_failed;
	}

	msm_csid_log_phy_clocks(csid_dev, "init requesting");
	if (csid_dev->ctrl_reg->csid_reg.csid_version == CSID_VERSION_V22)
		msm_cam_clk_sel_src(&csid_dev->pdev->dev,
			&csid_dev->csid_clk_info[3], csid_dev->csid_clk_src_info,
			csid_dev->num_clk_src_info);

	rc = msm_cam_clk_enable(&csid_dev->pdev->dev,
			csid_dev->csid_clk_info, csid_dev->csid_clk,
			csid_dev->num_clk, 1);
	if (rc < 0) {
		pr_err("CSID%d node=%s clock enable failed rc=%d\n",
			csid_dev->pdev->id,
			csid_dev->pdev->dev.of_node->full_name, rc);
		goto clk_enable_failed;
	}
	CDBG("%s:%d called\n", __func__, __LINE__);
"""),
        (r"""
msm_csid_reset_fail:
	disable_irq(csid_dev->irq->start);
	msm_cam_clk_enable(&csid_dev->pdev->dev, csid_clk_info,
		csid_dev->csid_clk, csid_dev->num_clk, 0);
clk_enable_failed:
	if (csid_dev->ctrl_reg->csid_reg.csid_version < CSID_VERSION_V22) {
""", r"""
msm_csid_reset_fail:
	disable_irq(csid_dev->irq->start);
	msm_cam_clk_enable(&csid_dev->pdev->dev, csid_dev->csid_clk_info,
		csid_dev->csid_clk, csid_dev->num_clk, 0);
clk_enable_failed:
	if (csid_dev->ctrl_reg->csid_reg.csid_version < CSID_VERSION_V22) {
"""),
        (r"""	disable_irq(csid_dev->irq->start);

	if (csid_dev->hw_version == CSID_VERSION_V20) {
		msm_cam_clk_enable(&csid_dev->pdev->dev, csid_clk_info,
			csid_dev->csid_clk, csid_dev->num_clk, 0);

		msm_camera_enable_vreg(&csid_dev->pdev->dev,
""", r"""	disable_irq(csid_dev->irq->start);

	if (csid_dev->hw_version == CSID_VERSION_V20) {
		msm_cam_clk_enable(&csid_dev->pdev->dev, csid_dev->csid_clk_info,
			csid_dev->csid_clk, csid_dev->num_clk, 0);

		msm_camera_enable_vreg(&csid_dev->pdev->dev,
"""),
        (r"""			NULL, 0, &csid_dev->csi_vdd, 0);
	} else {
		msm_cam_clk_enable(&csid_dev->pdev->dev,
			csid_clk_info,
			csid_dev->csid_clk,
			csid_dev->num_clk, 0);

""", r"""			NULL, 0, &csid_dev->csi_vdd, 0);
	} else {
		msm_cam_clk_enable(&csid_dev->pdev->dev,
			csid_dev->csid_clk_info,
			csid_dev->csid_clk,
			csid_dev->num_clk, 0);

"""),
        (r"""static int msm_csid_get_clk_info(struct csid_device *csid_dev,
	struct platform_device *pdev)
{
	uint32_t count;
	uint32_t cnt = 0;
	int i, rc;
	int ii = 0;
	uint32_t rates[CSID_NUM_CLK_MAX];
""", r"""static int msm_csid_get_clk_info(struct csid_device *csid_dev,
	struct platform_device *pdev)
{
	int count;
	int cnt = 0;
	int i, rc;
	int ii = 0;
	uint32_t rates[CSID_NUM_CLK_MAX];
"""),
        (r"""	of_node = pdev->dev.of_node;

	count = of_property_count_strings(of_node, "clock-names");
	csid_dev->num_clk = count;

	CDBG("%s: count = %d\n", __func__, count);
	if (count == 0) {
		pr_err("%s: no clocks found in device tree, count=%d",
			__func__, count);
		return -EINVAL;
	}

	if (count > CSID_NUM_CLK_MAX) {
""", r"""	of_node = pdev->dev.of_node;

	count = of_property_count_strings(of_node, "clock-names");
	CDBG("%s: count = %d\n", __func__, count);
	if (count <= 0) {
		pr_err("%s: no clocks found in device tree, count=%d\n",
			__func__, count);
		return count < 0 ? count : -EINVAL;
	}

	if (count > CSID_NUM_CLK_MAX) {
"""),
        (r"""			count, CSID_NUM_CLK_MAX);
		return -EINVAL;
	}

	if (csid_dev->hw_dts_version == CSID_VERSION_V22) {
		cnt = count;
""", r"""			count, CSID_NUM_CLK_MAX);
		return -EINVAL;
	}
	csid_dev->num_clk = count;

	if (csid_dev->hw_dts_version == CSID_VERSION_V22) {
		cnt = count;
"""),
        (r"""			count++;
			rc = of_property_read_string_index(of_node,
				"clock-names", i, &clock_name);
			CDBG("%s: clock_names[%d] = %s\n", __func__,
				i, clock_name);
			if (rc < 0) {
				pr_err("%s:%d, failed\n", __func__, __LINE__);
				return rc;
			}
			if (strcmp(clock_name, "csi_phy_src_clk") == 0)
				break;
		}
		csid_dev->num_clk = count;
	}

	for (i = 0; i < count; i++) {
		rc = of_property_read_string_index(of_node, "clock-names",
				i, &(csid_clk_info[i].clk_name));
		CDBG("%s: clock-names[%d] = %s\n", __func__,
			i, csid_clk_info[i].clk_name);
		if (rc < 0) {
			pr_err("%s:%d, failed\n", __func__, __LINE__);
			return rc;
		}
	}
	rc = of_property_read_u32_array(of_node, "qcom,clock-rates",
		rates, count);
""", r"""			count++;
			rc = of_property_read_string_index(of_node,
				"clock-names", i, &clock_name);
			if (rc < 0) {
				pr_err("%s:%d, failed\n", __func__, __LINE__);
				return rc;
			}
			CDBG("%s: clock_names[%d] = %s\n", __func__,
				i, clock_name);
			if (strcmp(clock_name, "csi_phy_src_clk") == 0)
				break;
		}
		/* Keep the legacy source slot zero and mux offset of three. */
		if (i == cnt || count < 3 || cnt - count > count - 3) {
			pr_err("%s: invalid v2.2 clock/source layout\n", __func__);
			return -EINVAL;
		}
		csid_dev->num_clk = count;
	}

	for (i = 0; i < count; i++) {
		rc = of_property_read_string_index(of_node, "clock-names",
				i, &(csid_dev->csid_clk_info[i].clk_name));
		if (rc < 0) {
			pr_err("%s:%d, failed\n", __func__, __LINE__);
			return rc;
		}
		CDBG("%s: clock-names[%d] = %s\n", __func__,
			i, csid_dev->csid_clk_info[i].clk_name);
	}
	rc = of_property_read_u32_array(of_node, "qcom,clock-rates",
		rates, count);
"""),
        (r"""		return rc;
	}
	for (i = 0; i < count; i++) {
		csid_clk_info[i].clk_rate = (rates[i] == 0) ?
			(long)-1 : rates[i];
		if (!strcmp(csid_clk_info[i].clk_name, "csi_src_clk")) {
			CDBG("%s:%d, copy csi_src_clk",
				__func__, __LINE__);
			csid_dev->csid_max_clk = rates[i];
			csid_dev->csid_clk_index = i;
		}
		CDBG("%s: clk_rate[%d] = %ld\n", __func__, i,
			csid_clk_info[i].clk_rate);
	}

	if (csid_dev->hw_dts_version == CSID_VERSION_V22) {
""", r"""		return rc;
	}
	for (i = 0; i < count; i++) {
		csid_dev->csid_clk_info[i].clk_rate = (rates[i] == 0) ?
			(long)-1 : rates[i];
		if (!strcmp(csid_dev->csid_clk_info[i].clk_name, "csi_src_clk")) {
			CDBG("%s:%d, copy csi_src_clk",
				__func__, __LINE__);
			csid_dev->csid_max_clk = rates[i];
			csid_dev->csid_clk_index = i;
		}
		CDBG("%s: clk_rate[%d] = %ld\n", __func__, i,
			csid_dev->csid_clk_info[i].clk_rate);
	}

	if (csid_dev->hw_dts_version == CSID_VERSION_V22) {
"""),
        (r"""			ii++;
			rc = of_property_read_string_index(of_node,
				"clock-names", i,
				&(csid_clk_src_info[ii].clk_name));
			CDBG("%s: clock-names[%d] = %s\n", __func__,
				ii, csid_clk_src_info[ii].clk_name);
			if (rc < 0) {
				pr_err("%s:%d, failed\n", __func__, __LINE__);
				return rc;
			}
		}
		ii = 0;
		rc = of_property_read_u32_array(of_node, "qcom,clock-rates",
""", r"""			ii++;
			rc = of_property_read_string_index(of_node,
				"clock-names", i,
				&(csid_dev->csid_clk_src_info[ii].clk_name));
			if (rc < 0) {
				pr_err("%s:%d, failed\n", __func__, __LINE__);
				return rc;
			}
			CDBG("%s: clock-names[%d] = %s\n", __func__,
				ii, csid_dev->csid_clk_src_info[ii].clk_name);
		}
		ii = 0;
		rc = of_property_read_u32_array(of_node, "qcom,clock-rates",
"""),
        (r"""		}
		for (i = count; i < cnt; i++) {
			ii++;
			csid_clk_src_info[ii].clk_rate = rates[i];
			CDBG("%s: clk_rate[%d] = %ld\n", __func__, ii,
			csid_clk_src_info[ii].clk_rate);
		}
	}
	return 0;
}

""", r"""		}
		for (i = count; i < cnt; i++) {
			ii++;
			csid_dev->csid_clk_src_info[ii].clk_rate = rates[i];
			CDBG("%s: clk_rate[%d] = %ld\n", __func__, ii,
			csid_dev->csid_clk_src_info[ii].clk_rate);
		}
	}
	msm_csid_log_phy_clocks(csid_dev, "probe configured");
	return 0;
}

"""),
        (r"""
	v4l2_subdev_init(&new_csid_dev->msm_sd.sd, &msm_csid_subdev_ops);
	v4l2_set_subdevdata(&new_csid_dev->msm_sd.sd, new_csid_dev);
	platform_set_drvdata(pdev, &new_csid_dev->msm_sd.sd);
	mutex_init(&new_csid_dev->mutex);

	if (pdev->dev.of_node) {
		rc = of_property_read_u32((&pdev->dev)->of_node,
""", r"""
	v4l2_subdev_init(&new_csid_dev->msm_sd.sd, &msm_csid_subdev_ops);
	v4l2_set_subdevdata(&new_csid_dev->msm_sd.sd, new_csid_dev);
	new_csid_dev->pdev = pdev;
	mutex_init(&new_csid_dev->mutex);
	INIT_LIST_HEAD(&new_csid_dev->msm_sd.list);
	init_completion(&new_csid_dev->reset_complete);
	if (!pdev->dev.of_node) {
		rc = -EINVAL;
		goto csid_no_resource;
	}

	if (pdev->dev.of_node) {
		rc = of_property_read_u32((&pdev->dev)->of_node,
"""),
        (r"""
		csid_vreg_info[0].min_voltage = csi_vdd_voltage;
		csid_vreg_info[0].max_voltage = csi_vdd_voltage;
	}

	rc = msm_csid_get_clk_info(new_csid_dev, pdev);
	if (rc < 0) {
		pr_err("%s: msm_csid_get_clk_info() failed", __func__);
		rc = -EFAULT;
		goto csid_no_resource;
	}

	rc = msm_camera_get_dt_vreg_data(pdev->dev.of_node,
		&(new_csid_dev->csid_vreg), &(new_csid_dev->regulator_count));
	if (rc < 0) {
		pr_err("%s: get vreg data from dtsi fail\n", __func__);
		rc = -EFAULT;
		goto csid_no_resource;
	}

	if ((new_csid_dev->regulator_count < 0) ||
		(new_csid_dev->regulator_count > MAX_REGULATOR)) {
		pr_err("%s: invalid reg count = %d, max is %d\n", __func__,
			new_csid_dev->regulator_count, MAX_REGULATOR);
		rc = -EFAULT;
		goto csid_no_resource;
	}

	new_csid_dev->mem = platform_get_resource_byname(pdev,
					IORESOURCE_MEM, "csid");
	if (!new_csid_dev->mem) {
		pr_err("%s: no mem resource?\n", __func__);
		rc = -ENODEV;
		goto csid_invalid_vreg_data;
	}
	new_csid_dev->irq = platform_get_resource_byname(pdev,
					IORESOURCE_IRQ, "csid");
	if (!new_csid_dev->irq) {
		pr_err("%s: no irq resource?\n", __func__);
		rc = -ENODEV;
		goto csid_invalid_vreg_data;
	}
	new_csid_dev->io = request_mem_region(new_csid_dev->mem->start,
		resource_size(new_csid_dev->mem), pdev->name);
	if (!new_csid_dev->io) {
		pr_err("%s: no valid mem region\n", __func__);
		rc = -EBUSY;
		goto csid_invalid_vreg_data;
	}

	new_csid_dev->pdev = pdev;
	new_csid_dev->msm_sd.sd.internal_ops = &msm_csid_internal_ops;
	new_csid_dev->msm_sd.sd.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	snprintf(new_csid_dev->msm_sd.sd.name,
			ARRAY_SIZE(new_csid_dev->msm_sd.sd.name), "msm_csid");
	media_entity_init(&new_csid_dev->msm_sd.sd.entity, 0, NULL, 0);
	new_csid_dev->msm_sd.sd.entity.type = MEDIA_ENT_T_V4L2_SUBDEV;
	new_csid_dev->msm_sd.sd.entity.group_id = MSM_CAMERA_SUBDEV_CSID;
	new_csid_dev->msm_sd.close_seq = MSM_SD_CLOSE_2ND_CATEGORY | 0x5;
	msm_sd_register(&new_csid_dev->msm_sd);

#ifdef CONFIG_COMPAT
	msm_cam_copy_v4l2_subdev_fops(&msm_csid_v4l2_subdev_fops);
	msm_csid_v4l2_subdev_fops.compat_ioctl32 = msm_csid_subdev_fops_ioctl32;
	new_csid_dev->msm_sd.sd.devnode->fops = &msm_csid_v4l2_subdev_fops;
#endif

	rc = request_irq(new_csid_dev->irq->start, msm_csid_irq,
		IRQF_TRIGGER_RISING, "csid", new_csid_dev);
	if (rc < 0) {
		release_mem_region(new_csid_dev->mem->start,
			resource_size(new_csid_dev->mem));
		pr_err("%s: irq request fail\n", __func__);
		rc = -EBUSY;
		goto csid_invalid_vreg_data;
	}
	disable_irq(new_csid_dev->irq->start);
	if (rc < 0) {
		release_mem_region(new_csid_dev->mem->start,
			resource_size(new_csid_dev->mem));
		pr_err("%s Error registering irq ", __func__);
		rc = -EBUSY;
		goto csid_invalid_vreg_data;
	}

	if (of_device_is_compatible(new_csid_dev->pdev->dev.of_node,
""", r"""
		csid_vreg_info[0].min_voltage = csi_vdd_voltage;
		csid_vreg_info[0].max_voltage = csi_vdd_voltage;
	}

	if (of_device_is_compatible(new_csid_dev->pdev->dev.of_node,
"""),
        (r"""		pr_err("%s:%d, invalid hw version : 0x%x", __func__, __LINE__,
			new_csid_dev->hw_dts_version);
		rc = -EINVAL;
		goto csid_invalid_vreg_data;
	}

	new_csid_dev->csid_state = CSID_POWER_DOWN;
	return 0;

csid_invalid_vreg_data:
	kfree(new_csid_dev->csid_vreg);
csid_no_resource:
""", r"""		pr_err("%s:%d, invalid hw version : 0x%x", __func__, __LINE__,
			new_csid_dev->hw_dts_version);
		rc = -EINVAL;
		goto csid_no_resource;
	}

	rc = msm_csid_get_clk_info(new_csid_dev, pdev);
	if (rc < 0) {
		pr_err("CSID%d node=%s clock metadata failed rc=%d\n",
			pdev->id, pdev->dev.of_node->full_name, rc);
		goto csid_no_resource;
	}

	rc = msm_camera_get_dt_vreg_data(pdev->dev.of_node,
		&(new_csid_dev->csid_vreg), &(new_csid_dev->regulator_count));
	if (rc < 0) {
		pr_err("%s: get vreg data from dtsi fail\n", __func__);
		/* The DT helper already frees this allocation on error. */
		new_csid_dev->csid_vreg = NULL;
		goto csid_no_resource;
	}

	if ((new_csid_dev->regulator_count < 0) ||
		(new_csid_dev->regulator_count > MAX_REGULATOR)) {
		pr_err("%s: invalid reg count = %d, max is %d\n", __func__,
			new_csid_dev->regulator_count, MAX_REGULATOR);
		rc = -EFAULT;
		goto csid_invalid_vreg_data;
	}

	new_csid_dev->mem = platform_get_resource_byname(pdev,
					IORESOURCE_MEM, "csid");
	if (!new_csid_dev->mem) {
		pr_err("%s: no mem resource?\n", __func__);
		rc = -ENODEV;
		goto csid_invalid_vreg_data;
	}
	new_csid_dev->irq = platform_get_resource_byname(pdev,
					IORESOURCE_IRQ, "csid");
	if (!new_csid_dev->irq) {
		pr_err("%s: no irq resource?\n", __func__);
		rc = -ENODEV;
		goto csid_invalid_vreg_data;
	}
	new_csid_dev->io = request_mem_region(new_csid_dev->mem->start,
		resource_size(new_csid_dev->mem), pdev->name);
	if (!new_csid_dev->io) {
		pr_err("%s: no valid mem region\n", __func__);
		rc = -EBUSY;
		goto csid_invalid_vreg_data;
	}

	rc = request_irq(new_csid_dev->irq->start, msm_csid_irq,
		IRQF_TRIGGER_RISING, "csid", new_csid_dev);
	if (rc < 0) {
		pr_err("%s: irq request fail\n", __func__);
		goto csid_release_mem;
	}
	disable_irq(new_csid_dev->irq->start);

	new_csid_dev->csid_state = CSID_POWER_DOWN;
	new_csid_dev->msm_sd.sd.internal_ops = &msm_csid_internal_ops;
	new_csid_dev->msm_sd.sd.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	snprintf(new_csid_dev->msm_sd.sd.name,
			ARRAY_SIZE(new_csid_dev->msm_sd.sd.name), "msm_csid");
	rc = media_entity_init(&new_csid_dev->msm_sd.sd.entity, 0, NULL, 0);
	if (rc < 0)
		goto csid_free_irq;
	new_csid_dev->msm_sd.sd.entity.type = MEDIA_ENT_T_V4L2_SUBDEV;
	new_csid_dev->msm_sd.sd.entity.group_id = MSM_CAMERA_SUBDEV_CSID;
	new_csid_dev->msm_sd.close_seq = MSM_SD_CLOSE_2ND_CATEGORY | 0x5;
	rc = msm_sd_register(&new_csid_dev->msm_sd);
	if (rc < 0) {
		/* msm_sd_register adds its ordered-list entry before registration. */
		list_del_init(&new_csid_dev->msm_sd.list);
		msm_sd_unregister(&new_csid_dev->msm_sd);
		goto csid_entity_cleanup;
	}

#ifdef CONFIG_COMPAT
	msm_cam_copy_v4l2_subdev_fops(&msm_csid_v4l2_subdev_fops);
	msm_csid_v4l2_subdev_fops.compat_ioctl32 = msm_csid_subdev_fops_ioctl32;
	new_csid_dev->msm_sd.sd.devnode->fops = &msm_csid_v4l2_subdev_fops;
#endif

	platform_set_drvdata(pdev, &new_csid_dev->msm_sd.sd);
	return 0;

csid_entity_cleanup:
	media_entity_cleanup(&new_csid_dev->msm_sd.sd.entity);
csid_free_irq:
	free_irq(new_csid_dev->irq->start, new_csid_dev);
csid_release_mem:
	release_mem_region(new_csid_dev->mem->start,
		resource_size(new_csid_dev->mem));
csid_invalid_vreg_data:
	kfree(new_csid_dev->csid_vreg);
csid_no_resource:
"""),
    ),
    CSID_H: (
        (r"""#include <linux/platform_device.h>
#include <media/v4l2-subdev.h>
#include "../../include/media/msm_cam_sensor.h"
#include "msm_sd.h"

#define CSID_NUM_CLK_MAX  16
""", r"""#include <linux/platform_device.h>
#include <media/v4l2-subdev.h>
#include "../../include/media/msm_cam_sensor.h"
#include "../../include/soc/qcom/camera2.h"
#include "msm_sd.h"

#define CSID_NUM_CLK_MAX  16
"""),
        (r"""	uint32_t num_clk;
	uint32_t num_clk_src_info;
	struct regulator *reg_ptr;
	struct clk *csid_clk[CSID_NUM_CLK_MAX];
	uint32_t csid_clk_index;
	uint32_t csid_max_clk;
""", r"""	uint32_t num_clk;
	uint32_t num_clk_src_info;
	struct regulator *reg_ptr;
	/* Clock metadata belongs to this controller, not the last probe. */
	struct msm_cam_clk_info csid_clk_info[CSID_NUM_CLK_MAX];
	struct msm_cam_clk_info csid_clk_src_info[CSID_NUM_CLK_MAX];
	struct clk *csid_clk[CSID_NUM_CLK_MAX];
	uint32_t csid_clk_index;
	uint32_t csid_max_clk;
"""),
    ),
}


class VerificationError(Exception):
    """Input does not match the independently established repair prerequisites."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def without_comments(text: str) -> str:
    # This source's relevant properties have no comment markers in strings.
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


def refs(index: int) -> tuple[str, ...]:
    return ("clk_gcc_camss_top_ahb_clk", "clk_gcc_camss_ispif_ahb_clk",
            f"clk_csi{index}phytimer_clk_src",
            f"clk_gcc_camss_csi{index}phytimer_clk", "clk_camss_top_ahb_clk_src",
            f"clk_gcc_camss_csi{index}phy_clk", "clk_gcc_camss_ahb_clk")


def csid_refs(index: int) -> tuple[str, ...]:
    return ("clk_gcc_camss_top_ahb_clk", "clk_gcc_camss_ispif_ahb_clk",
            f"clk_gcc_camss_csi{index}_ahb_clk", f"clk_csi{index}_clk_src",
            f"clk_gcc_camss_csi{index}phy_clk", f"clk_gcc_camss_csi{index}_clk",
            f"clk_gcc_camss_csi{index}pix_clk", f"clk_gcc_camss_csi{index}rdi_clk",
            "clk_gcc_camss_ahb_clk")


def csid_names(index: int) -> tuple[str, ...]:
    return ("camss_top_ahb_clk", "ispif_ahb_clk", "csi_ahb_clk", "csi_src_clk",
            f"csi{index}_phy_clk", "csi_clk", "csi_pix_clk", "csi_rdi_clk",
            "camss_ahb_clk")


def csid_nodes(text: str, label: str):
    matches = list(re.finditer(
        r"\bqcom,csid@([0-9a-f]+)\s*\{(?P<body>[^{}]*)\};", text
    ))
    require(len(re.findall(r"\bqcom,csid@", text)) == len(matches),
            f"{label}: unrecognized CSID node syntax")
    require(tuple(m.group(1) for m in matches) == CSID_ADDRESSES,
            f"{label}: unexpected CSID nodes/order")
    return matches


def csiphy_nodes(text: str, expected: tuple[str, ...], label: str):
    # Refuse unexpected nested syntax rather than guessing where a node ends.
    matches = list(re.finditer(
        r"\bqcom,csiphy@([0-9a-f]+)\s*\{(?P<body>[^{}]*)\};", text
    ))
    require(len(re.findall(r"\bqcom,csiphy@", text)) == len(matches),
            f"{label}: unrecognized CSIPHY node syntax")
    require(tuple(m.group(1) for m in matches) == expected,
            f"{label}: unexpected CSIPHY nodes/order")
    return matches


def property_value(body: str, prop: str, label: str) -> str:
    values = re.findall(r"(?m)^\s*" + re.escape(prop) + r"\s*=\s*([^;]*);", body)
    require(len(values) == 1, f"{label}: expected exactly one {prop} property")
    return values[0].strip()


def string_array(value: str, label: str) -> tuple[str, ...]:
    require(re.fullmatch(r'"[^"\\]*"(?:\s*,\s*"[^"\\]*")*', value) is not None,
            f"{label}: invalid string array")
    return tuple(re.findall(r'"([^"\\]*)"', value))


def clock_refs(body: str, label: str) -> tuple[str, ...]:
    value = property_value(body, "clocks", label)
    cell = r"<&clock_gcc\s+(clk_[a-zA-Z0-9_]+)>"
    require(re.fullmatch(cell + r"(?:\s*,\s*" + cell + r")*", value) is not None,
            f"{label}: unrecognized clock phandle/cell syntax")
    return tuple(re.findall(cell, value))


def clock_rates(body: str, label: str) -> tuple[int, ...]:
    value = property_value(body, "qcom,clock-rates", label)
    require(re.fullmatch(r"<\s*\d+(?:\s+\d+)*\s*>", value) is not None,
            f"{label}: unrecognized clock-rate syntax")
    return tuple(int(v) for v in re.findall(r"\d+", value))


def validate_node(body: str, index: int, label: str, precedent: bool) -> str:
    require(clock_refs(body, label) == refs(index),
            f"{label}: clock reference order differs; refusing repair")
    require(property_value(body, "cell-index", label) == f"<{index}>",
            f"{label}: unexpected cell-index")
    compatibility = string_array(property_value(body, "compatible", label), label)
    version = "qcom,csiphy-v3.4.2" if precedent else "qcom,csiphy-v3.5"
    require(compatibility == (version, "qcom,csiphy"),
            f"{label}: unexpected CSIPHY compatibility")
    names = string_array(property_value(body, "clock-names", label), label)
    rates = clock_rates(body, label)
    if (names, rates) == (FIXED_NAMES, FIXED_RATES):
        return "fixed"
    if not precedent and (names, rates) == (OLD_NAMES, OLD_RATES):
        return "pristine"
    raise VerificationError(f"{label}: unexpected or mixed clock names/rates")


def validate_evidence(texts: dict[Path, str]) -> None:
    config = texts[DEFCONFIG]
    for option in ("CONFIG_ARM64", "CONFIG_ARCH_MSM8953", "CONFIG_MSMB_CAMERA",
                   "CONFIG_SEC_J7POPLTE_USA_SPR"):
        require(re.findall(r"^" + option + r"=(.*)$", config, re.M) == ["y"],
                f"{DEFCONFIG}: expected {option}=y")
    selection = ("ifneq ($(CONFIG_SEC_J7POPLTE_USA_SPR),y)\n"
                 "    obj-$(CONFIG_MSMB_CAMERA) += camera_v2/\n"
                 "else\n"
                 "    obj-$(CONFIG_MSMB_CAMERA) += camera_v2_j7poplte/\n"
                 "endif")
    require(texts[CAMERA_MAKEFILE].count(selection) == 1,
            f"{CAMERA_MAKEFILE}: active camera driver selection changed")
    macro = "#define CLK_LIST(_c) { .clk = &(&_c)->c, .of_idx = clk_##_c }"
    require(texts[CLK_MACRO].count(macro) == 1,
            f"{CLK_MACRO}: CLK_LIST binding-ID mapping changed")
    require("#include <dt-bindings/clock/msm-clocks-8953.h>" in texts[PROVIDER],
            f"{PROVIDER}: expected binding include")
    lookup = re.findall(r"static struct clk_lookup msm_clocks_lookup\[\] = \{(.*?)\n\};",
                        texts[PROVIDER], re.S)
    require(len(lookup) == 1, f"{PROVIDER}: expected GCC provider lookup table")
    ids = set()
    all_refs = {r for index in range(3) for r in refs(index) + csid_refs(index)}
    for ref in sorted(all_refs):
        defines = re.findall(r"^#define\s+" + re.escape(ref) + r"\s+(0x[0-9a-fA-F]+)\s*$",
                             texts[BINDING], re.M)
        require(len(defines) == 1, f"{BINDING}: missing/duplicate definition {ref}")
        clock_id = int(defines[0], 16)
        require(clock_id not in ids, f"{BINDING}: duplicate clock ID {ref}")
        ids.add(clock_id)
        require(len(re.findall(r"\bCLK_LIST\(\s*" + re.escape(ref[4:]) + r"\s*\)",
                               lookup[0])) == 1,
                f"{PROVIDER}: missing/duplicate provider mapping for {ref}")
    for index, match in enumerate(csiphy_nodes(texts[PRECEDENT], ADDRESSES[:2],
                                             str(PRECEDENT))):
        validate_node(match.group("body"), index,
                      f"{PRECEDENT}:csiphy@{ADDRESSES[index]}", precedent=True)
    for index, match in enumerate(csid_nodes(texts[DTS], str(DTS))):
        label = f"{DTS}:csid@{CSID_ADDRESSES[index]}"
        body = match.group("body")
        require(property_value(body, "cell-index", label) == f"<{index}>",
                f"{label}: unexpected controller ID")
        require(property_value(body, "reg", label) == f"<0x{CSID_ADDRESSES[index]} 0x400>",
                f"{label}: unexpected controller address")
        require(string_array(property_value(body, "compatible", label), label) ==
                ("qcom,csid-v3.5.1", "qcom,csid"),
                f"{label}: unexpected compatibility")
        require(clock_refs(body, label) == csid_refs(index),
                f"{label}: CSID clock reference order differs")
        require(string_array(property_value(body, "clock-names", label), label) ==
                csid_names(index), f"{label}: CSID clock names/order differ")
        require(clock_rates(body, label) == CSID_RATES,
                f"{label}: CSID clock rates differ")


def source_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_csid(texts: dict[Path, str]) -> str:
    states = []
    for relative, signatures in CSID_SIGNATURES.items():
        fingerprint = source_digest(texts[relative])
        matches = [state for state, expected in signatures.items() if fingerprint == expected]
        require(len(matches) == 1,
                f"{relative}: source differs from pinned pristine or reviewed v2 driver")
        states.extend(matches)
    require(len(set(states)) == 1,
            "Mixed CSID header/implementation generation; refusing to modify any file")
    if states[0] == "pristine":
        return "pristine"

    # Independent semantic assertions make the fingerprint contract reviewable:
    # inline storage cannot alias another controller; every metadata consumer is
    # qualified, including legacy source selection and all three disable paths.
    source = without_comments(texts[CSID_C])
    header = without_comments(texts[CSID_H])
    require('#include "../../include/soc/qcom/camera2.h"' in header,
            "CSID header must include the complete clock-information type")
    device = re.search(r"struct csid_device\s*\{([^{}]*)\};", header, re.S)
    require(device is not None, "Missing csid_device structure")
    for name in ("csid_clk_info", "csid_clk_src_info"):
        declaration = f"struct msm_cam_clk_info {name}[CSID_NUM_CLK_MAX];"
        require(device.group(1).count(declaration) == 1,
                f"csid_device must own an inline {name} array")
        mentions = list(re.finditer(r"\b" + name + r"\b", source))
        require(bool(mentions) and all(
            source[max(0, match.start() - len("csid_dev->")):match.start()] == "csid_dev->"
            for match in mentions), f"CSID has a shared or unqualified use of {name}")
    calls = re.findall(r"\bmsm_cam_clk_enable\([^;]*;", source)
    require(len(calls) == 4 and all("csid_dev->csid_clk_info" in call for call in calls),
            "CSID enable/disable must use each controller's clock information")
    require(sum("csid_dev->num_clk, 1" in call for call in calls) == 1 and
            sum("csid_dev->num_clk, 0" in call for call in calls) == 3,
            "CSID enable/disable count or reference-counting behavior changed")
    require(source.count("&csid_dev->csid_clk_info[3], csid_dev->csid_clk_src_info,") == 1,
            "CSID source selection must preserve the legacy main-array offset")
    for required in (
        "int count;", "int cnt = 0;", "if (count <= 0)",
        "return count < 0 ? count : -EINVAL;", "if (count > CSID_NUM_CLK_MAX)",
        "if (i == cnt || count < 3 || cnt - count > count - 3)",
        'msm_csid_log_phy_clocks(csid_dev, "probe configured");',
        'msm_csid_log_phy_clocks(csid_dev, "init requesting");',
        "csid_dev->pdev->id, of_node->full_name, stage,",
        "csid_dev->csid_clk_info[i].clk_rate = (rates[i] == 0) ?",
        "(long)-1 : rates[i];", "csid_dev->csid_clk_src_info[ii].clk_rate = rates[i];",
        "INIT_LIST_HEAD(&new_csid_dev->msm_sd.list);",
        "list_del_init(&new_csid_dev->msm_sd.list);",
        "msm_sd_unregister(&new_csid_dev->msm_sd);",
        "csid_entity_cleanup:\n\tmedia_entity_cleanup(&new_csid_dev->msm_sd.sd.entity);",
        "csid_free_irq:\n\tfree_irq(new_csid_dev->irq->start, new_csid_dev);",
        "csid_release_mem:\n\trelease_mem_region(new_csid_dev->mem->start,",
    ):
        require(required in source, f"CSID invariant missing: {required}")
    require(source.count("if (!csid_dev || !csid_dev->base)") == 2,
            "CSID IRQ handlers must reject unavailable register mappings")
    require(source.index("new_csid_dev->hw_dts_version = CSID_VERSION_V22;") <
            source.index("rc = msm_csid_get_clk_info(new_csid_dev, pdev);"),
            "CSID compatibility must be known before interpreting source clocks")
    require(source.index("new_csid_dev->csid_state = CSID_POWER_DOWN;") <
            source.index("rc = msm_sd_register(&new_csid_dev->msm_sd);"),
            "CSID must finish initialization before publishing the subdevice")
    require(source.count("platform_set_drvdata(pdev, &new_csid_dev->msm_sd.sd);") == 1 and
            source.index("rc = msm_sd_register(&new_csid_dev->msm_sd);") <
            source.index("platform_set_drvdata(pdev, &new_csid_dev->msm_sd.sd);"),
            "CSID platform data must be published only after successful registration")
    return "fixed"


def preflight(kernel: Path) -> tuple[dict[Path, str], dict[Path, str], str]:
    kernel = kernel.resolve(strict=True)
    require((kernel / ARM64_DTS).resolve(strict=True) ==
            (kernel / DTS).resolve(strict=True),
            "ARM64 qcom DTS path must resolve to the canonical ARM source")
    texts = {}
    for relative in INPUTS:
        path = kernel / relative
        try:
            path.resolve(strict=True).relative_to(kernel)
        except ValueError as error:
            raise VerificationError(f"{relative}: input resolves outside the kernel tree") from error
        texts[relative] = path.read_bytes().decode("utf-8")
    clean = {p: without_comments(t) for p, t in texts.items()}
    validate_evidence(clean)
    states = []
    for index, match in enumerate(csiphy_nodes(clean[DTS], ADDRESSES, str(DTS))):
        states.append(validate_node(match.group("body"), index,
                      f"{DTS}:csiphy@{ADDRESSES[index]}", precedent=False))
    camera = texts[DIAGNOSTICS]
    require(len(re.findall(r"\bclk_get\s*\(", without_comments(camera))) == 3,
            f"{DIAGNOSTICS}: expected exactly three clk_get call sites")
    for lookup in ("mux_clk = clk_get(dev, clk_info[i].clk_name);",
                   "src_clk = clk_get(dev, clk_src_info[i].clk_name);",
                   "clk_ptr[i] = clk_get(dev, clk_info[i].clk_name);"):
        require(camera.count(lookup) == 1,
                f"{DIAGNOSTICS}: unexpected clk_get lookup assignment")
    for index, (old, fixed) in enumerate(DIAGNOSTIC_EDITS):
        counts = (camera.count(old), camera.count(fixed))
        require(counts in ((1, 0), (0, 1)),
                f"{DIAGNOSTICS}: unexpected clk_get failure branch {index}")
        states.append("pristine" if counts == (1, 0) else "fixed")
    require(len(set(states)) == 1,
            "Mixed partial v1 repair detected; refusing to modify any file")
    previous_state = states[0]
    csid_state = validate_csid(texts)
    require(previous_state != "pristine" or csid_state == "pristine",
            "CSID v2 repair without complete v1 repair; refusing to modify any file")
    state = ("fixed" if csid_state == "fixed" else
             "v1" if previous_state == "fixed" else "pristine")
    changes = {}
    if previous_state == "pristine":
        updated_dts = texts[DTS]
        # Limit substitution to the three independently verified CSIPHY bodies.
        for match in reversed(csiphy_nodes(updated_dts, ADDRESSES, str(DTS))):
            body = match.group("body")
            require(body.count(OLD_NAMES_TEXT) == 1 and body.count(OLD_RATES_TEXT) == 1,
                    "CSIPHY property formatting drift; refusing repair")
            patched = body.replace(OLD_NAMES_TEXT, FIXED_NAMES_TEXT).replace(
                OLD_RATES_TEXT, FIXED_RATES_TEXT)
            updated_dts = (updated_dts[:match.start("body")] + patched +
                           updated_dts[match.end("body"):])
        for old, fixed in DIAGNOSTIC_EDITS:
            camera = camera.replace(old, fixed)
        changes = {DTS: updated_dts, DIAGNOSTICS: camera}
    if csid_state == "pristine":
        for relative, edits in CSID_EDITS.items():
            updated = texts[relative]
            for old, fixed in edits:
                require(updated.count(old) == 1,
                        f"{relative}: CSID edit context changed; refusing repair")
                updated = updated.replace(old, fixed, 1)
            require(source_digest(updated) == CSID_SIGNATURES[relative]["fixed"],
                    f"{relative}: prepared CSID repair differs from verified v2 source")
            changes[relative] = updated
        validate_csid({**texts, **changes})
    return texts, changes, state


def commit_changes(kernel: Path, originals: dict[Path, str], changes: dict[Path, str]):
    # All semantic checks finish before this function. Stage adjacent temporary
    # files, recheck originals, and restore replaced files if a write fails.
    staged = {}
    replaced = []
    try:
        for relative, updated in changes.items():
            path = kernel / relative
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            staged[relative] = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, path.stat().st_mode)
        for relative in changes:
            require((kernel / relative).read_bytes().decode("utf-8") == originals[relative],
                    f"{relative}: input changed after preflight")
        for relative, temporary in staged.items():
            os.replace(temporary, kernel / relative)
            replaced.append(relative)
    except BaseException:
        for relative in replaced:
            with (kernel / relative).open("w", encoding="utf-8", newline="") as handle:
                handle.write(originals[relative])
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def run(kernel: Path, apply: bool = False) -> str:
    kernel = kernel.resolve(strict=True)
    originals, changes, state = preflight(kernel)
    if apply and changes:
        commit_changes(kernel, originals, changes)
        _, remaining, state = preflight(kernel)
        require(state == "fixed" and not remaining, "Post-write verification failed")
        return "applied"
    require(state == "fixed", f"Tree is {state}; use --apply to apply the verified v2 repair")
    return "already-fixed" if apply else "verified"


def print_report(result: str) -> None:
    print(f"Result: {result}; camera patch v2 verified")
    print("Three CSIPHY nodes retain seven refs/names/rates each")
    print(f"Canonical DTS: {DTS}; ARM64 DTS alias verified")
    print("Evidence: GCC CLK_LIST + binding IDs + MSM8917 seven-clock precedent verified")
    print("Selection: CONFIG_SEC_J7POPLTE_USA_SPR=y selects camera_v2_j7poplte")
    print("Clock positions (zero-based; reference / name / requested rate):")
    for index in range(3):
        print(f"  csiphy@{ADDRESSES[index]}:")
        for position, (ref, name, rate) in enumerate(zip(refs(index), FIXED_NAMES, FIXED_RATES)):
            print(f"    {position}: {ref} / {name} / {rate}")
    print(f"Diagnostics: {DIAGNOSTICS}; 3 device/function/index/name/errno logs verified")
    print("Enable failure includes requested rate; errno assignment and unwind are preserved")
    print("CSID clocks (zero-based; reference / name / requested DT rate):")
    for index in range(3):
        print(f"  CSID{index} /soc/qcom,csid@{CSID_ADDRESSES[index]}:")
        for position, (ref, name, rate) in enumerate(zip(
                csid_refs(index), csid_names(index), CSID_RATES)):
            print(f"    {position}: {ref} / {name} / {rate}")
    print("CSID ownership: two inline arrays per csid_device; no shared clock-information arrays")
    print("Pinned source fingerprints verify every initialization, enable, disable, source-selection,")
    print("bounds, failure-unwind, and controller/node/requested-PHY diagnostic change")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True, help="Kernel checkout root")
    parser.add_argument("--apply", action="store_true", help="Apply only after complete preflight")
    args = parser.parse_args()
    try:
        result = run(args.kernel, args.apply)
    except (VerificationError, OSError, UnicodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
