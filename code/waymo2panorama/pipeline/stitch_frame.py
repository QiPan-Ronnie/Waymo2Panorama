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
from waymo2panorama.blending.hard_hdr_of import blend_hard_hdr_of
from waymo2panorama.blending.seam_local_align import blend_seam_local_align
from waymo2panorama.blending.seam_routing import blend_seam_routing
from waymo2panorama.blending.region_coherent_seam import (
    blend_region_coherent_hard,
    blend_region_coherent_seam,
)
from waymo2panorama.blending.seam_confined import blend_seam_confined
from waymo2panorama.data_io.av2_loader import RING_CAMS_7, FrameSample
from waymo2panorama.projection.sphere_projection import render_camera_to_erp


def stitch_one_frame(
    frame: FrameSample,
    erp_hw: tuple[int, int] = (1024, 2048),
    num_bands: int = 5,
    ego_masks: Optional[dict[str, np.ndarray]] = None,
    wrap: bool = True,
    convergence_distance_m: float | np.ndarray | None = None,
    blend_mode: str = "multiband",
) -> np.ndarray:
    """Stitch one synchronized frame's 7 ring cams into a single ERP image (uint8 HxWx3).

    `convergence_distance_m` is forwarded to render_camera_to_erp. None (default)
    runs the legacy L1 baseline (cam at ego origin). Float r > 0 or per-pixel
    (H,W) array enables N1 cam-translation-aware finite-radius projection.

    `blend_mode` selects the final blend:
      - "multiband" (default, backward-compat) — cos² feather + multi-band blend.
        Smooth seams but produces doubled-feature ghost in overlap on near-field
        objects (e.g. BMW car body doubled at front-right seam).
      - "hard_hdr_of" — 3-layer basic-CV pipeline (see hard_hdr_of.py):
          L1 hard_select  → kills view-mixing ghost
          L2 joint HDR    → equalizes cross-cam exposure
          L3 OF chain warp → corrects spatial parallax at seams
        ~50s/anchor at 2048x4096 vs ~6s for multiband, but ghost-free.
      - "hard_hdr"       — same as above without L3 OF (~20s/anchor).
      - "hard_localalign" — LPAM-inspired seam-local translation alignment,
        no HDR. Keeps hard_select's one-camera-per-pixel contract.
      - "hard_hdr_localalign" — centered Y-only HDR + seam-local alignment.
      - "hard_seamroute" — visibility-aware DP seam routing, no HDR/OF/warp.
    """
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
            convergence_distance_m=convergence_distance_m,
        )
        slabs.append(rgb)
        weights.append(w)

    if blend_mode == "multiband":
        return multiband_blend(slabs, weights, num_bands=num_bands, wrap=wrap)
    elif blend_mode == "hard_hdr_of":
        return blend_hard_hdr_of(slabs, weights, apply_of=True)
    elif blend_mode == "hard_hdr":
        return blend_hard_hdr_of(slabs, weights, apply_of=False)
    elif blend_mode == "hard_localalign":
        return blend_seam_local_align(slabs, weights, apply_hdr_pre=False)
    elif blend_mode == "hard_hdr_localalign":
        return blend_seam_local_align(slabs, weights, apply_hdr_pre=True)
    elif blend_mode == "hard_seamroute":
        return blend_seam_routing(slabs, weights)
    elif blend_mode == "hard_regioncoherent":
        return blend_region_coherent_seam(slabs, weights)
    elif blend_mode == "hard_componentcoherent":
        return blend_region_coherent_hard(slabs, weights)
    elif blend_mode == "hard_seamconfined":
        # E1: L1 hard_select everywhere, multiband confined to the ~7 seam strips only.
        return blend_seam_confined(slabs, weights, num_bands=num_bands, wrap=wrap)["out"]
    else:
        raise ValueError(
            f"blend_mode={blend_mode!r} not recognized. "
            "Use 'multiband', 'hard_hdr', 'hard_hdr_of', "
            "'hard_localalign', 'hard_hdr_localalign', 'hard_seamroute', "
            "'hard_regioncoherent', or 'hard_componentcoherent'."
        )


# ---------------------------------------------------------------------------
# WS2 — L1 + pre-warp (Architecture B with global chain-to-reference)
# ---------------------------------------------------------------------------
#
# Algorithm (per anchor):
#   1. For each of the 7 adjacent ring-cam pairs (cam_a, cam_b), fit a 2D
#      homography H_{a->b} on the overlap region (DISK + LightGlue + RANSAC).
#   2. For each cam c != reference_cam, compose pair homographies along the
#      SHORTEST ring path from c to reference_cam:
#          H_{c->ref} = H_n @ ... @ H_2 @ H_1
#      (see alignment/pair_homography.ring_path_homography).
#   3. For each cam c != reference_cam, warp its image:
#          warped_c = cv2.warpPerspective(img_c, H_{c->ref}, (w_ref, h_ref))
#      The warped image lives in reference_cam's image plane.
#   4. Pass warped images (+ each cam's ORIGINAL K / T_ego_cam) to the L1
#      sphere projection. Each cam still projects to its OWN spherical
#      slab — the prewarp just made the overlap region pixel-aligned.
#   5. Multi-band blend the slabs to produce the final ERP.
#
# Geometric consistency note:
#   v1 keeps each cam's ORIGINAL K + T_ego_cam after warping the pixels.
#   This is a small geometric inconsistency (the warped pixels "look like"
#   reference_cam's view of the overlap, but we project them through
#   cam_c's pinhole). For AV2's nearly-rectified ring overlap wedges and
#   small chain homographies (a few pixels of shift on planar content at
#   >30m), the resulting slab is meaningfully closer to the reference than
#   the un-warped slab — that's where the +PSNR comes from. A v2 would
#   reproject the warped pixels through reference_cam's K + T_ego_cam,
#   which requires per-cam virtual extrinsics; left as future work.
#
# Graceful degradation:
#   If ALL pair homographies fall back to identity (status != "ok"), the
#   composed chain is identity for every cam — so the prewarp pipeline
#   produces the SAME ERP as plain `stitch_one_frame`. The summary dict's
#   "n_pairs_ok" field is 0 in that case; callers should warn and / or
#   skip the prewarp entirely (the run_l1_orb_hybrid.py driver logs this).
# ---------------------------------------------------------------------------


def stitch_one_frame_with_prewarp(
    per_cam: dict[str, dict],
    erp_hw: tuple[int, int] = (1024, 2048),
    num_bands: int = 5,
    ego_masks: Optional[dict[str, Optional[np.ndarray]]] = None,
    wrap: bool = True,
    device: str = "cpu",
    homography_kwargs: Optional[dict] = None,
    reference_cam: str = "ring_front_center",
    adjacent_pairs: Optional[list[tuple[str, str]]] = None,
    ring_order: Optional[list[str]] = None,
    return_diagnostics: bool = True,
    warp_model: str = "rotation_only",
    min_warp_coverage_frac: float = 0.10,
    convergence_distance_m: float | np.ndarray | None = None,
) -> tuple[np.ndarray, dict]:
    """WS2 — L1 sphere stitch WITH chain-warp overlap-homography prewarp.

    Algorithm (Architecture B, global chain-to-reference):
      1. For each adjacent ring pair, fit a 2D homography (DISK+LightGlue+RANSAC).
      2. For each cam != reference_cam, compose homographies along the shortest
         ring path to reference_cam.
      3. Warp each non-reference cam's image with cv2.warpPerspective using
         the composed H.
      4. L1 sphere project each (warped) cam onto the ERP canvas using each
         cam's ORIGINAL K + T_ego_cam (v1 simplification — see module docstring).
      5. Multi-band blend.

    If ALL pair homographies fall back to identity (e.g. no DISK matches on
    any pair), the chain warps are all identity and the output matches
    plain `stitch_one_frame` (graceful degradation). The summary dict
    reports n_pairs_ok=0 in that case.

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
        reference_cam: global anchor cam — its pixels are NOT warped; every
                 other cam is warped into reference_cam's image-plane frame.
                 Default = "ring_front_center" (most forward-facing, fewest
                 chain hops on average).
        adjacent_pairs: override the default 7-cam ring topology.
        ring_order: override the cam ordering used for shortest-path lookup.
        return_diagnostics: if True, returns (erp, summary_dict).

    Returns:
        erp: (H_erp, W_erp, 3) uint8 — the final blended ERP.
        summary: dict with keys:
            "pair_homographies": dict "cam_a__to__cam_b" -> per-pair stats
            "chain_warps": dict cam_name -> chain H + n_hops + status
            "n_pairs_total" / "n_pairs_ok": pair-level success counts
            "runtime": dict feature_match / warp / project / blend phase times
            "reference_cam": the anchor used for the chain
    """
    # Local import to avoid pulling torch/kornia when only `stitch_one_frame`
    # is used by a caller (e.g. the cheap L1 baseline driver).
    from waymo2panorama.alignment.pair_homography import (
        ADJACENT_PAIRS as _DEFAULT_ADJACENT_PAIRS,
        RING_ORDER as _DEFAULT_RING_ORDER,
        compute_overlap_homography,
        ring_path_homography,
        validate_warp_corners,
    )
    import time as _time

    hk = dict(homography_kwargs) if homography_kwargs else {}
    hk.setdefault("device", device)
    hk.setdefault("warp_model", warp_model)

    pairs = adjacent_pairs if adjacent_pairs is not None else _DEFAULT_ADJACENT_PAIRS
    ring = ring_order if ring_order is not None else _DEFAULT_RING_ORDER

    if reference_cam not in per_cam:
        raise KeyError(
            f"reference_cam={reference_cam!r} not in per_cam (keys={sorted(per_cam.keys())})"
        )

    # ---- Step 1: estimate homographies on each adjacent pair --------------
    t_fm0 = _time.time()
    pair_results: dict[tuple[str, str], dict] = {}
    for cam_a, cam_b in pairs:
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
    t_fm_s = _time.time() - t_fm0

    # ---- Step 2: build chain homography for each non-reference cam --------
    # Map pair_results -> {(a,b): H} for ring_path_homography.
    pair_H: dict[tuple[str, str], np.ndarray] = {
        k: v["H"] for k, v in pair_results.items()
    }
    chain_log: dict[str, dict] = {}
    chain_warps: dict[str, np.ndarray] = {}
    n_chain_safety_fallbacks = 0
    for cam in per_cam.keys():
        if cam == reference_cam:
            chain_warps[cam] = np.eye(3, dtype=np.float64)
            chain_log[cam] = {
                "n_hops": 0, "is_identity": True,
                "safety_fallback": False, "post_warp_coverage_frac": 1.0,
            }
            continue
        H_chain = ring_path_homography(
            cam_target=cam,
            cam_reference=reference_cam,
            pair_homographies=pair_H,
            ring_order=ring,
        )
        # SAFETY VALVE (v2): adjacent ring cams point in different directions, so
        # geometrically-correct pair warps DO map full-image corners far off the
        # receiving cam's canvas — corner-projection check would over-trigger.
        # Instead we run the warp, count how many output pixels are non-zero,
        # and only fall back if coverage collapsed below the threshold (which
        # IS the v1 NEG mode: rear cams' 3-hop perspective chain pushes all
        # image content off-canvas -> all-black slab -> ERP rear region empty).
        img = per_cam[cam]["image"]
        h_src, w_src = img.shape[:2]
        warped_probe = cv2.warpPerspective(
            np.ones((h_src, w_src), dtype=np.uint8),
            H_chain, (w_src, h_src),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        post_warp_coverage = float(warped_probe.mean())
        # If less than `min_warp_coverage_frac` fraction of the canvas is
        # non-zero after warp -> warp pushed image off; fall back to identity.
        # Default 0.5 means "require at least 50% of the canvas to be covered
        # by warped image content". For pure rotation between adjacent ring cams,
        # this is easily satisfied (~80-95% coverage typical).
        safety_fallback = post_warp_coverage < min_warp_coverage_frac
        if safety_fallback:
            H_chain = np.eye(3, dtype=np.float64)
            n_chain_safety_fallbacks += 1
        chain_warps[cam] = H_chain
        i = ring.index(cam)
        j = ring.index(reference_cam)
        n = len(ring)
        n_hops = min((j - i) % n, (i - j) % n)
        chain_log[cam] = {
            "n_hops": int(n_hops),
            "is_identity": bool(np.allclose(H_chain, np.eye(3), atol=1e-9)),
            "safety_fallback": bool(safety_fallback),
            "post_warp_coverage_frac": post_warp_coverage,
        }

    # ---- Step 3: warp each non-reference cam ------------------------------
    t_warp0 = _time.time()
    warped_per_cam: dict[str, np.ndarray] = {}
    for cam, data in per_cam.items():
        img = data["image"]
        H_chain = chain_warps[cam]
        if cam == reference_cam or np.allclose(H_chain, np.eye(3), atol=1e-9):
            warped_per_cam[cam] = img.copy()
            continue
        h_src, w_src = img.shape[:2]
        # We warp into a canvas the same size as cam's own image; this keeps
        # the L1 sphere projection (which uses cam's own K) sampling the
        # warped image correctly. The reference cam's image-plane geometry
        # is approximately the same size as cam's own (ring cams all share
        # ~504x504 letterboxed dimensions on AV2 / Pi3 cache).
        warped = cv2.warpPerspective(
            img, H_chain, (w_src, h_src),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        warped_per_cam[cam] = warped
    t_warp_s = _time.time() - t_warp0

    # ---- Step 4: L1 sphere project + multi-band blend ---------------------
    # NB: each cam still uses its ORIGINAL K and T_ego_cam (v1 simplification).
    # convergence_distance_m forwarded so prewarp + N1 can be combined.
    t_proj0 = _time.time()
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
            convergence_distance_m=convergence_distance_m,
        )
        slabs.append(rgb)
        weights.append(w)
    t_proj_s = _time.time() - t_proj0

    t_blend0 = _time.time()
    erp = multiband_blend(slabs, weights, num_bands=num_bands, wrap=wrap)
    t_blend_s = _time.time() - t_blend0

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
    # Serializable chain log (H as list of lists for JSON).
    chain_log_serial = {}
    for cam, info in chain_log.items():
        H = chain_warps[cam]
        chain_log_serial[cam] = {
            "H": H.tolist(),
            "n_hops": info["n_hops"],
            "is_identity": info["is_identity"],
            "safety_fallback": info.get("safety_fallback", False),
            "post_warp_coverage_frac": info.get("post_warp_coverage_frac", 1.0),
        }

    summary = {
        "pair_homographies": pair_log,
        "chain_warps": chain_log_serial,
        "n_pairs_total": len(pair_results),
        "n_pairs_ok": int(sum(1 for r in pair_results.values() if r["status"] == "ok")),
        "reference_cam": reference_cam,
        "warp_model": warp_model,
        "min_warp_coverage_frac": float(min_warp_coverage_frac),
        "n_chain_safety_fallbacks": int(n_chain_safety_fallbacks),
        "runtime": {
            "feature_match_s": round(t_fm_s, 3),
            "warp_s": round(t_warp_s, 3),
            "project_s": round(t_proj_s, 3),
            "blend_s": round(t_blend_s, 3),
        },
    }
    return erp, summary
