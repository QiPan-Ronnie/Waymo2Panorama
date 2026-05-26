"""
T4 v3 hypothesis verification — proves the code path responds to asymmetric reweight.

The v1/v2 NEG result raised the question: is `apply_option_b_reweight` + `multiband_blend`
fundamentally unable to change the output, OR was the v2 differential just too weak?

Tests here cover three regimes on a synthetic 7-cam ring stitch:

  1. UNIFORM (all cams get same mask)   -> output unchanged (the v1 NEG behavior).
  2. v2 SIMULATED (cam_a and cam_b get THE SAME mask in their overlap, others zero)
     -> output ~unchanged (the v2 NEG behavior we observed on Colab).
  3. EXTREME ASYMMETRIC (cam_0 mask=1, others=0) -> output dominated by cam_0,
     differs significantly from baseline. **This is the v3 success criterion.**
  4. MODERATE ASYMMETRIC (cam_0=.8, cam_1=.2, others 0) -> output differs.

If (3) and (4) both PASS, code path is fine and v3 just needs asymmetric masks.
If they fail, there's a deeper bug and reweight approach is dead regardless.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.pipeline.option_b_reweight import apply_option_b_reweight  # noqa: E402


def _band_center_col(W: int, n_cams: int, i: int) -> int:
    """Cam i's center column in the synthetic ring layout."""
    return int(round((i / n_cams) * W))


def _synthetic_ring_cam_scene(
    h_erp: int = 128, w_erp: int = 256, n_cams: int = 7,
) -> tuple[list[np.ndarray], list[np.ndarray], list[str]]:
    """Build a synthetic 7-cam ERP-style stitching input.

    Each cam contributes a solid color (HSV-hue-spaced) and a wrapped-Gaussian
    feathered weight centered at theta=2*pi*i/n. Adjacent cams overlap heavily —
    same overlap geometry as ring-cam L1 in practice.
    """
    cam_names = [f"cam_{i}" for i in range(n_cams)]
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    u_axis = np.arange(w_erp, dtype=np.float32)

    for i in range(n_cams):
        hue = (i / n_cams) * 360.0
        c = 1.0
        x = 1.0 - abs((hue / 60.0) % 2 - 1)
        if   hue < 60:   r, g, b = c, x, 0.0
        elif hue < 120:  r, g, b = x, c, 0.0
        elif hue < 180:  r, g, b = 0.0, c, x
        elif hue < 240:  r, g, b = 0.0, x, c
        elif hue < 300:  r, g, b = x, 0.0, c
        else:            r, g, b = c, 0.0, x
        color = np.array([r * 255, g * 255, b * 255], dtype=np.float32)

        slab = np.zeros((h_erp, w_erp, 3), dtype=np.float32)
        slab[:] = color
        slabs.append(slab)

        center_u = (i / n_cams) * w_erp
        du = np.minimum(np.abs(u_axis - center_u),
                        w_erp - np.abs(u_axis - center_u))
        sigma = w_erp / (2.0 * n_cams)
        w_u = np.exp(-(du * du) / (2 * sigma * sigma)).astype(np.float32)
        w = np.tile(w_u[None, :], (h_erp, 1))
        weights.append(w)

    return slabs, weights, cam_names


# ---------------------------------------------------------------------------
# Hypothesis tests
# ---------------------------------------------------------------------------


def test_uniform_mask_does_not_change_output():
    """v1 NEG reproducer: same mask applied to all cams -> multiband normalize cancels.

    This is the ROOT CAUSE we hit. Documenting it as a passing test pins the
    expected behavior down (so if the cancel ever stops working we notice).
    """
    slabs, weights, cam_names = _synthetic_ring_cam_scene()
    h, w = slabs[0].shape[:2]

    baseline = multiband_blend(slabs, weights, num_bands=3, wrap=True)

    uniform_mask = np.ones((h, w), dtype=np.float32) * 0.5
    boosted_w = apply_option_b_reweight(weights, uniform_mask, alpha=10.0)
    boosted_blend = multiband_blend(slabs, boosted_w, num_bands=3, wrap=True)

    diff = np.abs(baseline.astype(np.int32) - boosted_blend.astype(np.int32))
    print(
        f"[t4-v3-hyp uniform] diff: max={diff.max()} mean={diff.mean():.3f}"
    )
    # Theoretical cancel is exact; allow tiny pyramid-quantization slack.
    assert int(diff.max()) <= 2, (
        f"v1 NEG hypothesis violated: uniform mask CHANGED output by up to "
        f"{int(diff.max())} levels — multiband normalize is NOT a perfect cancel here."
    )


def test_v2_simulated_identical_pair_masks_barely_change_output():
    """Simulate the v2 NEG bug: cam_0 and cam_1 splatted with IDENTICAL points.

    In their pair-only overlap, both masks are equal -> cancel survives. Other
    cams in the synthetic ring have cos^2 tails into this region, so a tiny
    non-zero diff is OK (this is exactly why anchor 0 had PSNR=111 dB not inf).
    """
    slabs, weights, cam_names = _synthetic_ring_cam_scene()
    h, w = slabs[0].shape[:2]

    baseline = multiband_blend(slabs, weights, num_bands=3, wrap=True)

    masks_dict = {c: np.zeros((h, w), dtype=np.float32) for c in cam_names}
    # The bug: same vertical band shared between cam_0 and cam_1 with identical values
    center0 = _band_center_col(w, len(cam_names), 0)
    center1 = _band_center_col(w, len(cam_names), 1)
    band_lo, band_hi = min(center0, center1), max(center0, center1)
    shared_mask = np.zeros((h, w), dtype=np.float32)
    shared_mask[:, band_lo:band_hi + 1] = 0.8
    masks_dict["cam_0"] = shared_mask.copy()
    masks_dict["cam_1"] = shared_mask.copy()  # IDENTICAL — this is the v2 bug

    weights_dict = {c: weights[i] for i, c in enumerate(cam_names)}
    boosted_dict = apply_option_b_reweight(weights_dict, masks_dict, alpha=2.0)
    boosted_w = [boosted_dict[c] for c in cam_names]
    boosted_blend = multiband_blend(slabs, boosted_w, num_bands=3, wrap=True)

    diff = np.abs(baseline.astype(np.int32) - boosted_blend.astype(np.int32))
    print(
        f"[t4-v3-hyp v2-sim] identical pair masks: "
        f"max={diff.max()} mean={diff.mean():.3f}"
    )
    # In pair-only zones the boost cancels; other cams' cos^2 tails permit a small
    # change. Should be MUCH smaller than the asymmetric test below.
    assert int(diff.max()) <= 30


def test_extreme_asymmetric_changes_output_significantly():
    """v3 hypothesis SUCCESS criterion: per-cam differential mask actually changes output.

    cam_0 mask=1 everywhere, all other cams' masks=0, alpha=10. Cam_0 should
    dominate the blend. If this passes, the apply_option_b_reweight + multiband_blend
    pipeline IS capable of producing differential output — v1/v2 NEG was because the
    masks weren't asymmetric enough.

    If this FAILS, there's a deep bug and reweight approach is dead.
    """
    slabs, weights, cam_names = _synthetic_ring_cam_scene()
    h, w = slabs[0].shape[:2]

    baseline = multiband_blend(slabs, weights, num_bands=3, wrap=True)

    masks_dict = {c: np.zeros((h, w), dtype=np.float32) for c in cam_names}
    masks_dict["cam_0"] = np.ones((h, w), dtype=np.float32)
    weights_dict = {c: weights[i] for i, c in enumerate(cam_names)}

    boosted_dict = apply_option_b_reweight(weights_dict, masks_dict, alpha=10.0)
    boosted_w = [boosted_dict[c] for c in cam_names]
    boosted_blend = multiband_blend(slabs, boosted_w, num_bands=3, wrap=True)

    diff = np.abs(baseline.astype(np.int32) - boosted_blend.astype(np.int32))
    frac_changed = float((diff.max(axis=-1) > 5).mean())
    print(
        f"[t4-v3-hyp asym EXTREME] cam_0=1 others=0 alpha=10: "
        f"max={diff.max()} mean={diff.mean():.3f} frac_changed_>5lvl={frac_changed:.4f}"
    )

    assert int(diff.max()) >= 30, (
        f"HYPOTHESIS FAILED: extreme asymmetric mask only changed output by "
        f"max {int(diff.max())} levels. apply_option_b_reweight + multiband_blend "
        "do not produce differential output — reweight approach is structurally dead."
    )
    assert frac_changed >= 0.05, (
        f"HYPOTHESIS PARTIAL FAIL: only {frac_changed:.2%} of pixels changed by >5 levels"
    )


def test_moderate_asymmetric_changes_output():
    """Realistic-magnitude v3 test: alpha=1, two adjacent cams with DIFFERENT masks.

    cam_0=0.8, cam_1=0.2 — what winner-take-all would roughly produce. Confirms
    even modest asymmetry produces visible output diff.
    """
    slabs, weights, cam_names = _synthetic_ring_cam_scene()
    h, w = slabs[0].shape[:2]

    baseline = multiband_blend(slabs, weights, num_bands=3, wrap=True)

    masks_dict = {c: np.zeros((h, w), dtype=np.float32) for c in cam_names}
    masks_dict["cam_0"] = np.ones((h, w), dtype=np.float32) * 0.8
    masks_dict["cam_1"] = np.ones((h, w), dtype=np.float32) * 0.2
    weights_dict = {c: weights[i] for i, c in enumerate(cam_names)}

    boosted_dict = apply_option_b_reweight(weights_dict, masks_dict, alpha=1.0)
    boosted_w = [boosted_dict[c] for c in cam_names]
    boosted_blend = multiband_blend(slabs, boosted_w, num_bands=3, wrap=True)

    diff = np.abs(baseline.astype(np.int32) - boosted_blend.astype(np.int32))
    print(
        f"[t4-v3-hyp asym MODERATE] cam_0=.8 cam_1=.2 alpha=1: "
        f"max={diff.max()} mean={diff.mean():.3f}"
    )
    assert int(diff.max()) >= 5, (
        f"Moderate asym (alpha=1) only changed output by {int(diff.max())} levels"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
