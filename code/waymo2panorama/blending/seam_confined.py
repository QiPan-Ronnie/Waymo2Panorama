"""E1 — seam-confined multiband: L1 hard_select everywhere, multiband ONLY in the ~7 seam strips.

Isolates the PURE PHOTOMETRIC seam (cross-camera exposure/colour jump) from true parallax
doubling. The far field is BYTE-IDENTICAL to L1 hard_select (alpha == 0 outside the seam bands),
so it cannot warp; only a thin band around each camera-selection boundary is feathered toward the
multiband blend (which fixes the low-frequency colour jump). Near-field DOUBLING is NOT addressed
here (that is E2) — E1 measures how much of the seam artefact is purely photometric.

    out = base*(1 - alpha) + multiband*alpha
    alpha = cos ramp, 1 at a camera-label boundary -> 0 at +-band_half_width px, exactly 0 beyond.

Returns the composite plus the L1 base, the seam-band alpha, and the boundary mask for diagnostics
and for the relative-warp sanity check (far-field frac_warp must be ~0 by construction).
"""
from __future__ import annotations

import cv2
import numpy as np

from waymo2panorama.blending.multiband import (
    multiband_blend,
    _gaussian_pyramid,
    _laplacian_pyramid,
    _wrap_pad_h,
    _wrap_unpad_h,
)


def multiband_lowfreq_blend(slabs, weights, lowfreq_cutoff: int = 4, num_bands: int = 5,
                            wrap: bool = True):
    """Multiband variant that blends ONLY the low-frequency (coarse) bands across cameras.

    Fine/mid Laplacian levels (lvl < lowfreq_cutoff) use HARD one-hot (argmax) weights -> the
    high-frequency detail comes from a SINGLE camera per pixel -> NO doubling. Coarse levels
    (lvl >= lowfreq_cutoff) use SOFT cos^2 weights -> the colour/exposure step blends smoothly.
    This fixes the photometric seam WITHOUT reintroducing near-field parallax doubling.
    lowfreq_cutoff in [0, num_bands]: higher -> only the very coarsest bands blend (safer vs
    near-field doubling, weaker colour correction).
    """
    H, W = slabs[0].shape[:2]
    if wrap:
        base = max(W // 32, 16)
        step = 1 << num_bands
        pad_w = ((base + step - 1) // step) * step
    else:
        pad_w = 0
    slabs_p = [_wrap_pad_h(s, pad_w) for s in slabs]
    weights_p = [_wrap_pad_h(w, pad_w) for w in weights]
    wstack = np.stack(weights_p, axis=0).astype(np.float32)
    soft = wstack / np.maximum(wstack.sum(axis=0, keepdims=True), 1e-12)
    argmax = wstack.argmax(axis=0)
    hard = np.stack([(argmax == i).astype(np.float32) for i in range(len(slabs_p))], axis=0)

    laps = [_laplacian_pyramid(s, num_bands) for s in slabs_p]
    soft_pyr = [_gaussian_pyramid(soft[i], num_bands) for i in range(len(slabs_p))]
    hard_pyr = [_gaussian_pyramid(hard[i], num_bands) for i in range(len(slabs_p))]

    n_levels = num_bands + 1
    blended = []
    for lvl in range(n_levels):
        use_hard = lvl < lowfreq_cutoff  # fine/mid -> single-camera detail (no doubling)
        acc = np.zeros_like(laps[0][lvl], dtype=np.float32)
        wsm = np.zeros(laps[0][lvl].shape[:2], dtype=np.float32) + 1e-12
        for i in range(len(slabs_p)):
            w_i = hard_pyr[i][lvl] if use_hard else soft_pyr[i][lvl]
            acc += laps[i][lvl] * w_i[..., None]
            wsm += w_i
        blended.append(acc / wsm[..., None])
    out = blended[-1]
    for lvl in range(n_levels - 2, -1, -1):
        hl, wl = blended[lvl].shape[:2]
        out = cv2.pyrUp(out, dstsize=(wl, hl)) + blended[lvl]
    if pad_w > 0:
        out = _wrap_unpad_h(out, pad_w)
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


def _label_and_base(slabs, weights):
    """argmax camera-label map, validity mask, and the hard_select base (uint8)."""
    w_stack = np.stack(weights, axis=0)                 # (K, H, W)
    rgb_stack = np.stack(slabs, axis=0).astype(np.float32)  # (K, H, W, 3)
    argmax = w_stack.argmax(axis=0)                     # (H, W) in [0, K)
    valid = w_stack.max(axis=0) > 0                     # (H, W)
    picked = np.take_along_axis(rgb_stack, argmax[None, ..., None], axis=0)[0]
    base = np.where(valid[..., None], picked, 0).astype(np.uint8)
    return argmax, valid, base


def _seam_alpha(argmax, valid, band_half_width):
    """cos ramp peaking (=1) on camera-label boundaries, ->0 at band_half_width px, 0 beyond.

    Wrap-aware in u. Only counts boundaries between two genuinely-covered cameras (real seams),
    not the content/black FoV edge.
    """
    H, W = argmax.shape
    lab = argmax.astype(np.int32)
    boundary = np.zeros((H, W), bool)
    for ax, sh in ((1, 1), (1, -1), (0, 1), (0, -1)):
        nb = np.roll(lab, sh, axis=ax)
        nb_valid = np.roll(valid, sh, axis=ax)
        boundary |= (lab != nb) & valid & nb_valid
    pad = int(band_half_width) + 2
    src = ((~boundary).astype(np.uint8)) * 255
    src_p = np.concatenate([src[:, -pad:], src, src[:, :pad]], axis=1)  # wrap pad
    dist = cv2.distanceTransform(src_p, cv2.DIST_L2, 3)[:, pad:-pad]
    d = np.clip(dist / float(band_half_width), 0.0, 1.0)
    alpha = np.where(dist <= band_half_width, 0.5 * (1.0 + np.cos(np.pi * d)), 0.0)
    alpha = (alpha * valid).astype(np.float32)
    return alpha, boundary


def blend_seam_confined(slabs, weights, num_bands: int = 5, band_half_width: int = 64,
                        wrap: bool = True, lowfreq_cutoff=None):
    """Seam-confined blend. Returns dict(out, base, alpha, boundary, mb).

    out: uint8 ERP, == L1 hard_select everywhere except a feathered band around the ~7 seams,
         where it transitions to the blended image.
    lowfreq_cutoff=None  -> E1:   full multiband in the band (fixes photometric step BUT doubles
                                  near-field parallax).
    lowfreq_cutoff=int   -> E1.5: low-frequency-only blend (multiband_lowfreq_blend) — fixes the
                                  colour/exposure step with single-camera high-freq, NO doubling.
    """
    argmax, valid, base = _label_and_base(slabs, weights)
    if lowfreq_cutoff is None:
        mb = multiband_blend(slabs, weights, num_bands=num_bands, wrap=wrap)  # uint8 (E1)
    else:
        mb = multiband_lowfreq_blend(slabs, weights, lowfreq_cutoff=int(lowfreq_cutoff),
                                     num_bands=num_bands, wrap=wrap)  # uint8 (E1.5)
    alpha, boundary = _seam_alpha(argmax, valid, band_half_width)
    a = alpha[..., None]
    out = base.astype(np.float32) * (1.0 - a) + mb.astype(np.float32) * a
    out = np.clip(out, 0, 255).astype(np.uint8)
    # GUARANTEE byte-identical far field: force exact base where alpha == 0
    far = alpha == 0.0
    out[far] = base[far]
    return dict(out=out, base=base, alpha=alpha, boundary=boundary, mb=mb)


def confined_depth_rmap(slabs_l1, weights_l1, depth_erp, band_half_width: int = 64,
                        support_max_m: float = 500.0, far_m: float = 1e6,
                        near_max_m: float | None = None):
    """Build an N1 convergence_distance_m map that is the LiDAR ERP depth ONLY inside the seam
    strips where LiDAR supports it, and 'far' (-> rotation-only == L1) everywhere else.

    Feeding this to render_camera_to_erp (N1 mode) depth-corrects each camera ONLY in the strips
    on near/mid surfaces; far/sky and the whole far field stay rotation-only (== L1).
    Returns (r_map, strip_mask, alpha, boundary).
    """
    argmax, valid, _ = _label_and_base(slabs_l1, weights_l1)
    alpha, boundary = _seam_alpha(argmax, valid, band_half_width)
    strip = alpha > 0
    supported = depth_erp < support_max_m
    use = strip & supported
    if near_max_m is not None:
        # only depth-correct genuinely NEAR (large-parallax, dense-LiDAR) content; leave mid/far
        # at rotation-only (== L1) so sparse mid-range depth can't smear it.
        use = use & (depth_erp < near_max_m)
    r_map = np.where(use, depth_erp.astype(np.float64), float(far_m))
    return r_map, strip, alpha, boundary


def blend_seam_e2_depth(slabs_l1, weights_l1, slabs_n1, weights_n1, alpha,
                        num_bands: int = 5, wrap: bool = True, lowfreq_cutoff=None):
    """E2 seam fusion: blend the DEPTH-ALIGNED (N1) slabs inside the seam strips, L1 elsewhere.

    slabs_l1/weights_l1 : rotation-only renders -> the exact L1 hard_select base (far field).
    slabs_n1/weights_n1 : renders with the confined LiDAR depth r_map -> near/mid content in the
                          strips is reprojected to the ego centre so the overlapping cameras
                          AGREE (no parallax doubling). Outside strips these ~= L1 (r=far).
    alpha               : the seam-band feather (from confined_depth_rmap) — confines the edit.

    out = L1 base outside the band; inside, feather to the multiband blend of the ALIGNED N1
    slabs (aligned content -> merges instead of doubling). Far field byte-identical to L1.
    """
    _, _, base = _label_and_base(slabs_l1, weights_l1)
    if lowfreq_cutoff is None:
        mb_e2 = multiband_blend(slabs_n1, weights_n1, num_bands=num_bands, wrap=wrap)
    else:
        # low-freq-only blend on the aligned N1 slabs: near content is depth-aligned (merges),
        # non-corrected mid/far gets colour-only blend with single-cam high-freq (no doubling).
        mb_e2 = multiband_lowfreq_blend(slabs_n1, weights_n1, lowfreq_cutoff=int(lowfreq_cutoff),
                                        num_bands=num_bands, wrap=wrap)
    a = np.clip(alpha, 0, 1)[..., None]
    out = base.astype(np.float32) * (1.0 - a) + mb_e2.astype(np.float32) * a
    out = np.clip(out, 0, 255).astype(np.uint8)
    far = alpha == 0.0
    out[far] = base[far]
    return dict(out=out, base=base, mb_e2=mb_e2, alpha=alpha)
