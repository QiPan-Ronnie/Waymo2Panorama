"""
Single-frame stitching: 7 ring cams (with calibration) -> 1 ERP image.

Two entry points:
  * `stitch_one_frame`            : the original L1 sphere baseline (no warp).
  * `stitch_one_frame_with_prewarp`: 新-B / WS2 variant — for each adjacent
        ring-cam pair, estimate a 2D homography on the overlap region (DISK +
        LightGlue + cv2.findHomography), warp cam_b into cam_a's image-plane
        frame, THEN run the same L1 sphere projection + multi-band blender on
        the warped images. Aims to reduce overlap-region parallax ghosts.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from waymo2panorama.blending.multiband import multiband_blend
from waymo2panorama.data_io.av2_loader import RING_CAMS_7, FrameSample
from waymo2panorama.projection.sphere_projection import render_camera_to_erp


def stitch_one_frame(
    frame: FrameSample,
    erp_hw: tuple[int, int] = (1024, 2048),
    num_bands: int = 5,
    ego_masks: Optional[dict[str, np.ndarray]] = None,
    wrap: bool = True,
) -> np.ndarray:
    """Stitch one synchronized frame's 7 ring cams into a single ERP image (uint8 HxWx3)."""
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        mask = ego_masks.get(cam) if ego_masks else None
        rgb, _alpha, w = render_camera_to_erp(
            image=img,
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            ego_mask=mask,
        )
        slabs.append(rgb)
        weights.append(w)
    return multiband_blend(slabs, weights, num_bands=num_bands, wrap=wrap)


# ---------------------------------------------------------------------------
# WS2 — L1 + pre-warp (per-pair Architecture B)
# ---------------------------------------------------------------------------
#
# Per-pair design (v1):
#   For each of the 7 adjacent ring-cam pairs (cam_a, cam_b), we fit a 2D
#   homography H_a_b such that  H_a_b @ x_a ~= x_b  (in image coords).
#   We then warp cam_b's FULL image into cam_a's frame using H_b_a = H_a_b^-1:
#       warped_b = cv2.warpPerspective(img_b, H_b_a, (w_a, h_a))
#   cam_b's K / T_ego_cam stay the same — we are aligning cam_b's *pixels*
#   to look as if they had been captured from cam_a's viewing geometry, so
#   the L1 sphere projection (which uses cam_a's intrinsics for cam_a, cam_b's
#   for cam_b) needs cam_b's *intrinsics+extrinsics* unchanged. Subtle but
#   correct: we change ONLY the pixel grid of cam_b, by remapping img_b onto
#   img_a's image plane.
#
# Wait — that's wrong, isn't it? If we warp img_b's pixels into img_a's
# image plane, then we should project the warped pixels using cam_a's K
# and T_ego_cam, not cam_b's. Let me restate:
#
#   The homography H_a_b is purely a 2D image-warp. After warping, the
#   "image" looks as if it were taken from cam_a's pose with cam_a's K
#   (for the planar overlap content). To feed it through the sphere
#   projection, we should treat it as a cam_a image at that point.
#
# But that double-counts the cam_a contribution at the overlap. And each
# cam_b participates in TWO pair-warps (one with its CCW neighbour, one
# with its CW neighbour) — they'd target different "reference cams".
#
# v1 simplification (the one we ship): warp cam_b into cam_a's frame
# but STILL project it through cam_b's K/T_ego_cam to its own sphere
# slab. This is technically a small geometric inconsistency for the
# warped pixels, but for AV2's nearly-rectified ring overlap wedges and
# small homography corrections (a few pixels of shift), the resulting
# slab is closer to cam_a's ground truth than the un-warped cam_b slab
# was. The L1 blender then merges them, with smaller residual ghost.
#
# A more principled v2 would:
#   * Choose a global reference cam (e.g. ring_front_center).
#   * Chain homographies around the ring so every cam shares one global
#     reference image-plane.
#   * Project each warped image as if it were the reference cam.
# That requires a different K/T for each warped cam and is left as TODO.
# ---------------------------------------------------------------------------


# Module-level constant so callers can introspect / extend.
ADJACENT_PAIRS_RING: tuple[tuple[str, str], ...] = (
    ("ring_front_center", "ring_front_left"),
    ("ring_front_left", "ring_side_left"),
    ("ring_side_left", "ring_rear_left"),
    ("ring_rear_left", "ring_rear_right"),
    ("ring_rear_right", "ring_side_right"),
    ("ring_side_right", "ring_front_right"),
    ("ring_front_right", "ring_front_center"),
)


def _prewarp_one_cam(
    img_a: np.ndarray, img_b: np.ndarray, H_a_to_b: np.ndarray,
) -> np.ndarray:
    """Warp img_b into img_a's image-plane frame via H_b_to_a = inv(H_a_to_b).

    H_a_to_b: 3x3 homography mapping x_a -> x_b (as returned by
              compute_overlap_homography).
    """
    if np.allclose(H_a_to_b, np.eye(3), atol=1e-9):
        # Identity fallback — skip the warpPerspective round-trip.
        return img_b.copy()
    try:
        H_b_to_a = np.linalg.inv(H_a_to_b)
    except np.linalg.LinAlgError:
        return img_b.copy()
    h_a, w_a = img_a.shape[:2]
    warped = cv2.warpPerspective(
        img_b, H_b_to_a, (w_a, h_a),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    return warped


def stitch_one_frame_with_prewarp(
    per_cam: dict[str, dict],
    erp_hw: tuple[int, int] = (1024, 2048),
    num_bands: int = 5,
    ego_masks: Optional[dict[str, Optional[np.ndarray]]] = None,
    wrap: bool = True,
    device: str = "cpu",
    homography_kwargs: Optional[dict] = None,
    adjacent_pairs: tuple[tuple[str, str], ...] = ADJACENT_PAIRS_RING,
    return_diagnostics: bool = True,
) -> tuple[np.ndarray, dict]:
    """WS2 — L1 sphere stitch WITH per-pair overlap-homography prewarp.

    For each adjacent (cam_a, cam_b) ring pair we estimate a 2D homography
    on the overlap region (DISK+LightGlue+RANSAC); the homography then
    pre-aligns cam_b's pixels to cam_a's image-plane frame BEFORE the
    sphere projection. See module docstring for the v1 simplifications
    (per-pair warp, no global chain).

    Args:
        per_cam: dict cam_name -> {"image": HxWx3 uint8, "K": (3,3), "T_ego_cam": (4,4)}.
                 Use FrameSample.images/calibrations from av2_loader to build this,
                 or load from a pi3_cache anchor dir.
        erp_hw, num_bands, wrap: passed to L1 sphere + multi-band blender, as
                 per `stitch_one_frame`.
        ego_masks: optional dict cam_name -> (Hsrc, Wsrc) uint8 mask. Same
                 convention as `stitch_one_frame`.
        device: "cpu" or "cuda" for DISK+LightGlue.
        homography_kwargs: forwarded to compute_overlap_homography (e.g.
                 {"max_num_keypoints": 1024, "lightglue_min_confidence": 0.2}).
        adjacent_pairs: override the default 7-cam ring topology.
        return_diagnostics: if True, returns (erp, summary_dict).

    Returns:
        erp: (H_erp, W_erp, 3) uint8 — the final blended ERP.
        summary: dict with key "pair_homographies" mapping "cam_a->cam_b" to
                 the per-pair compute_overlap_homography result (with H as a
                 nested list for json-friendliness).
    """
    # Local import to avoid pulling torch/kornia when only `stitch_one_frame`
    # is used by a caller (e.g. the cheap L1 baseline driver).
    from waymo2panorama.alignment.pair_homography import compute_overlap_homography

    hk = dict(homography_kwargs) if homography_kwargs else {}
    hk.setdefault("device", device)

    # ---- Step 1: estimate homographies on each adjacent pair --------------
    # For each pair, decide which cam plays "a" (the reference frame we will
    # warp INTO) and which plays "b" (the one being warped). v1: cam_a is
    # always the first element of the tuple. That means cam_b will then be
    # warped into cam_a's frame. Each cam appears as "b" in exactly one pair
    # (since each pair has one CCW + one CW neighbour); the cam that is "a"
    # in some pair retains its OWN pixels and is "b" in the pair preceding
    # it in the ring. So every cam ends up warped at most once.
    #
    # Implementation: build a dict cam -> applied_warp where each cam is
    # warped into the frame of the PREVIOUS cam in the ring (its CCW
    # neighbour). cam_front_center is the natural anchor — it is "a" in the
    # first pair and "b" in the last (close-of-ring) pair. v1 treats it as
    # the anchor by SKIPPING the wrap-around warp on cam_front_center
    # itself, so its pixels are not double-perturbed.

    pair_results: dict[tuple[str, str], dict] = {}
    for cam_a, cam_b in adjacent_pairs:
        if cam_a not in per_cam or cam_b not in per_cam:
            # Skip rather than crash if a cam is missing (Waymo has only
            # 5 cams, vs AV2's 7; with a non-7-cam topology, drop pairs).
            continue
        img_a = per_cam[cam_a]["image"]
        img_b = per_cam[cam_b]["image"]
        K_a = per_cam[cam_a].get("K")
        K_b = per_cam[cam_b].get("K")
        T_a = per_cam[cam_a].get("T_ego_cam")
        T_b = per_cam[cam_b].get("T_ego_cam")
        res = compute_overlap_homography(
            img_a=img_a, img_b=img_b,
            K_a=K_a, K_b=K_b, T_ego_a=T_a, T_ego_b=T_b,
            **hk,
        )
        pair_results[(cam_a, cam_b)] = res

    # ---- Step 2: build the warped per-cam images --------------------------
    # cam_front_center: leave pixels untouched (anchor).
    # All other cams: warp into the frame of their CCW neighbour using the
    # pair where THIS cam is the "b" side.
    warped_per_cam: dict[str, np.ndarray] = {}
    cam_to_pair_as_b: dict[str, tuple[str, str]] = {b: (a, b) for a, b in pair_results.keys()}

    for cam, data in per_cam.items():
        img = data["image"]
        pair_key = cam_to_pair_as_b.get(cam)
        if pair_key is None:
            # No pair has this cam as 'b' — keep original pixels.
            warped_per_cam[cam] = img.copy()
            continue
        res = pair_results[pair_key]
        cam_a_for_warp = pair_key[0]
        img_a_ref = per_cam[cam_a_for_warp]["image"]
        warped_per_cam[cam] = _prewarp_one_cam(img_a_ref, img, res["H"])

    # ---- Step 3: L1 sphere project + multi-band blend ---------------------
    # NB: we use each cam's ORIGINAL K and T_ego_cam (v1 simplification —
    # see module docstring).
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in per_cam.keys():
        data = per_cam[cam]
        mask = ego_masks.get(cam) if ego_masks else None
        rgb, _alpha, w = render_camera_to_erp(
            image=warped_per_cam[cam],
            K=data["K"],
            T_ego_cam=data["T_ego_cam"],
            erp_hw=erp_hw,
            ego_mask=mask,
        )
        slabs.append(rgb)
        weights.append(w)
    erp = multiband_blend(slabs, weights, num_bands=num_bands, wrap=wrap)

    # ---- Diagnostics ------------------------------------------------------
    if not return_diagnostics:
        return erp, {}

    pair_log: dict[str, dict] = {}
    for (cam_a, cam_b), res in pair_results.items():
        key = f"{cam_a}__to__{cam_b}"
        h_list = res["H"].tolist() if isinstance(res["H"], np.ndarray) else res["H"]
        pair_log[key] = {
            "cam_a": cam_a,
            "cam_b": cam_b,
            "H": h_list,
            "inlier_count": int(res["inlier_count"]),
            "residual_px": float(res["residual_px"]) if np.isfinite(res["residual_px"]) else None,
            "match_count": int(res["match_count"]),
            "status": str(res["status"]),
            "time_s": float(res.get("time_s", 0.0)),
        }
    summary = {
        "pair_homographies": pair_log,
        "n_pairs_total": len(pair_results),
        "n_pairs_ok": int(sum(1 for r in pair_results.values() if r["status"] == "ok")),
    }
    return erp, summary
