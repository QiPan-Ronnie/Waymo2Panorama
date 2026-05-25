"""
Heuristic ego-vehicle masks for AV2 ring cameras (WS1.2).

These masks tell the projection functions which source-image pixels belong to
the AV2 mounting hardware / vehicle body and should NOT be blended into the ERP.

Heuristic only — exact ROIs need empirical tuning against the rendered ERP
(T6 verify run on anchor 60). This is a sane, conservative starting point.

Background
----------
The 7 AV2 ring cams are bolted to a roof-rail assembly. At the wider vertical
FOV used by the cylinder projection (±45°) the sphere's asin() clip no longer
hides:
  - Top portion (all 7 cams): antenna / mounting plate / shroud reflections
    above the lens housing.
  - Bottom portion (ring_front_center): the hood / vehicle nose is visible in
    the lower rows of the portrait-orientation forward cam.
  - Bottom portion (ring_rear_left / ring_rear_right): the vehicle body /
    tailgate occludes the lower rows of the rear-quadrant cams.

We black out those rows with a uint8 mask (1 = keep, 0 = exclude). The
cylinder.py ego_mask plumbing (see render_camera_to_cylinder, step 7)
samples this mask via cv2.remap and multiplies it into the feather weight, so
excluded pixels contribute 0 weight in the multi-band blend.

ROI percentages (conservative — under-mask is better than over-mask):
  - All 7 cams: top 3% of rows.
  - ring_front_center: additionally bottom 5% of rows (hood).
  - ring_rear_left, ring_rear_right: additionally bottom 8% of rows (body).

These percentages should be revisited after the Colab verify run on anchor 60.
If residual hardware traces remain, bump the corresponding fraction up by 1-2
percentage points. If real-world content is being clipped, dial it back.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from waymo2panorama.data_io.av2_loader import RING_CAMS_7

# Cameras whose bottom edge is contaminated by the vehicle body.
_BODY_CAMS_BOTTOM_FRAC: dict[str, float] = {
    "ring_front_center": 0.05,   # hood / vehicle nose (portrait cam)
    "ring_rear_left": 0.08,      # rear body / tailgate
    "ring_rear_right": 0.08,     # rear body / tailgate
}

# Universal top fraction: every ring cam shows some mounting hardware up top
# once the vertical FOV opens to ±45° on the cylinder.
_TOP_FRAC_ALL: float = 0.03


def make_av2_ego_mask(
    cam_name: str,
    image_hw: tuple[int, int],
) -> np.ndarray | None:
    """Return a uint8 ego-vehicle mask for one AV2 ring camera.

    Args:
        cam_name:  AV2 ring camera name (e.g. ``"ring_front_center"``).
        image_hw:  ``(H, W)`` of the *source* image that will be projected.
                   The returned mask has exactly this shape, so callers MUST
                   pass the actual runtime image shape — do NOT hardcode
                   1550/2048 (front_center is portrait 2048x1550, the other six
                   are landscape 1550x2048).

    Returns:
        ``np.ndarray`` of shape ``(H, W)``, dtype ``uint8``:
            ``1`` = keep, ``0`` = exclude.
        ``None`` if ``cam_name`` is not in the AV2 ring-camera set
        (caller can skip mask wiring for that cam).
    """
    if cam_name not in RING_CAMS_7:
        return None

    h, w = image_hw
    mask = np.ones((h, w), dtype=np.uint8)

    # All cams: mask top fraction (hardware reflections / antenna above lens).
    top_rows = int(round(h * _TOP_FRAC_ALL))
    if top_rows > 0:
        mask[:top_rows, :] = 0

    # Per-cam bottom rows (hood / body).
    bot_frac = _BODY_CAMS_BOTTOM_FRAC.get(cam_name, 0.0)
    if bot_frac > 0.0:
        bot_rows = int(round(h * bot_frac))
        if bot_rows > 0:
            mask[h - bot_rows :, :] = 0

    return mask


def build_ego_masks(
    cam_names: Sequence[str],
    cam_image_shapes: dict[str, tuple[int, int]],
    enabled: bool = True,
) -> dict[str, np.ndarray | None]:
    """Build per-cam ego masks for a list of cameras.

    Thin wrapper around :func:`make_av2_ego_mask` that handles the
    "disabled" / "unknown cam" bookkeeping uniformly across drivers. This
    exists to remove duplication previously inlined in
    ``scripts/phase3/run_cylindrical_baseline.py`` and
    ``scripts/phase3/eval_cylindrical_cycle.py`` (drift risk flagged in code
    review, WS1.2 follow-up).

    Args:
        cam_names:        list of cam names to build masks for.
        cam_image_shapes: dict mapping cam name to ``(H_src, W_src)``. Cams
                          missing from this dict map to ``None`` in the
                          output (caller can then skip mask wiring).
        enabled:          if False, returns a dict with all ``None`` values
                          (the "mask disabled" A/B mode).

    Returns:
        dict mapping every name in ``cam_names`` to either a
        ``(H_src, W_src)`` uint8 mask or ``None``. ``None`` means "don't
        apply an ego mask for this cam" (either because masking is disabled,
        the cam isn't in :data:`RING_CAMS_7`, or its shape wasn't supplied).
    """
    if not enabled:
        return {cam: None for cam in cam_names}
    masks: dict[str, np.ndarray | None] = {}
    for cam in cam_names:
        if cam not in cam_image_shapes:
            masks[cam] = None
            continue
        masks[cam] = make_av2_ego_mask(cam, cam_image_shapes[cam])
    return masks


def make_waymo_ego_mask(
    cam_name: str,
    image_hw: tuple[int, int],
) -> np.ndarray | None:
    """Placeholder — Waymo Open Dataset hardware ROIs are TBD.

    A teammate should fill this in once we have representative Waymo frames
    to inspect empirically. The interface is identical to
    :func:`make_av2_ego_mask`: take the cam name + ``(H, W)``, return a uint8
    mask (or ``None`` for unknown cams).

    TODO: Inspect Waymo ring-cam images for hardware ROIs (antenna housings,
          mirror brackets, body panels) and fill in heuristic top/bottom row
          fractions analogous to :func:`make_av2_ego_mask`.

    Raises:
        NotImplementedError: always, until implemented.
    """
    raise NotImplementedError(
        "make_waymo_ego_mask is not yet implemented. "
        "Please inspect Waymo ring-cam images for hardware ROIs and fill in "
        "heuristic top/bottom row fractions analogous to make_av2_ego_mask."
    )
