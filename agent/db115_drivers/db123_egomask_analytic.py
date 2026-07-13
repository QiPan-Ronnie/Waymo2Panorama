# -*- coding: utf-8 -*-
"""DB-123: analytic per-camera ego-body mask for scene-band ego removal.

Generates the EGO_IMG_MASK npz consumed by db89_ghost_recovery's composite
projection (DB-123 v2 hook): source pixels whose camera ray intersects the
ego BODY box are rejected in the main 7-camera projection, so the hood /
trunk reflections never enter the band. With all cameras rejected, those
ERP pixels stay black — the band's honest "Cosmos will paint this" domain —
and the mask twin (comp.sum>=12) follows automatically.

Verdict trail (2026-07-11, log cd22abca):
  v1 ERP-domain geometric gate (egoproj & no-LiDAR-support): NEG — hood
     pixels get pasted at GROUND depth whose direction has real LiDAR
     support; it blacked real road and kept the hood.
  v2 temporal-variance image mask (db118_egomask v3): NEG for the hood —
     the hood is SPECULAR; its reflection content moves with the scene, so
     the variance gate never fires (all-zero masks).
  v3 analytic two-box (body+cabin): rear cams over-masked 0.99 — the cabin
     box (z-top above the camera height) swallowed every rearward ray. AV2
     ego frame origin is the REAR AXLE center (z=0 at ~0.35 m AGL), NOT the
     ground; cameras sit on the front pod at ego-z 1.40.
  v4/v5 single body slab: hood/trunk hugged, BUT the panorama still showed
     bright-blue arcs at the band's rear azimuths. Ablation pinned them to
     rear-camera MAIN-projection pixels; zooming the rear images revealed
     the real occluder: the ROOF ASSEMBLY + rear glass (a specular plate
     reflecting the sky) fills ~40-45% of the rear images' bottom — far
     larger than the trunk-only slab.
  v6 FINAL: body slab + roof box capped BELOW the pod cameras
     (z<=1.30 < 1.40). Over-masks a wedge of far road in the rear/side
     views — ACCEPTED BY DESIGN: the cost asymmetry is one-sided (over-
     masking only widens the band's black Cosmos domain a few tens of px
     at its most grazing-distorted edge; under-masking leaks ego pixels,
     which is the failure the task forbids). Masks: fc 0.208 / fl 0.042 /
     fr 0.044 / rl 0.442 / rr 0.447 / sl 0.273 / sr 0.270.

The boxes are constants of the AV2 fleet vehicle (same car all logs); the
mask depends only on per-log calibration, costs milliseconds, and needs no
image data.

API:
    masks = build_ego_masks(log_dir)          # {cam: bool (h//4, w//4)}
    save_ego_mask_npz(log_dir, out_npz)
"""
import numpy as np
import cv2
import pyarrow.feather as feather
from scipy.spatial.transform import Rotation as R

# ego frame: origin at rear-axle center, z=0 at axle height (~0.35 m AGL).
# box 1: bumper-to-bumper body slab up to hood/trunk top (boundary-overlay
#        verified: hugs the hood in front_center and the trunk arc in rear).
# box 2: roof assembly + rear glass, capped strictly below the pod cameras
#        (cams at ego-z 1.40) so no camera sits inside a box.
# v8-fine (2026-07-11 sweep verdict roofY078_z110, user over-mask complaint):
# body front tightened 3.95->3.60 (front_center line hugs the hood), roof box
# y +-0.78 / z-top 1.10 hugs the roof-glare upper edge in the rear cams,
# dilate 5. Over-mask recovered -61% (128k -> 49.8k px) with zero ego leak.
EGO_BOXES = [
    (np.array([-1.25, -1.05, -0.45]), np.array([3.60, 1.05, 0.60])),
    (np.array([-1.00, -0.78, 0.60]), np.array([1.30, 0.78, 1.10])),
]
MASK_DILATE = 5          # quarter-res px (~20 full-res) specular-bloom margin
MASK_DILATE_REAR = 5
NEAR_LIMIT_M = 8.0       # ignore box grazing beyond this ray distance


def build_ego_masks(log_dir):
    ext = feather.read_feather(str(log_dir) + "/calibration/egovehicle_SE3_sensor.feather")
    intr = feather.read_feather(str(log_dir) + "/calibration/intrinsics.feather")
    cams = sorted(c for c in intr["sensor_name"].tolist() if str(c).startswith("ring_"))
    masks = {}
    for c in cams:
        ri = intr[intr.sensor_name == c].iloc[0]
        re_ = ext[ext.sensor_name == c].iloc[0]
        K = np.array([[ri.fx_px, 0, ri.cx_px], [0, ri.fy_px, ri.cy_px], [0, 0, 1.0]])
        Rc = R.from_quat([re_.qx, re_.qy, re_.qz, re_.qw]).as_matrix()
        tc = np.array([re_.tx_m, re_.ty_m, re_.tz_m])
        hh, ww = int(ri.height_px), int(ri.width_px)
        h4, w4 = hh // 4, ww // 4
        uu, vv = np.meshgrid((np.arange(w4) * 4 + 2).astype(np.float64),
                             (np.arange(h4) * 4 + 2).astype(np.float64))
        d = np.stack([(uu - K[0, 2]) / K[0, 0],
                      (vv - K[1, 2]) / K[1, 1],
                      np.ones_like(uu)], -1) @ Rc.T
        m = np.zeros((h4, w4), bool)
        for a, b in EGO_BOXES:
            with np.errstate(divide="ignore", invalid="ignore"):
                t1 = (a[None, None, :] - tc[None, None, :]) / d
                t2 = (b[None, None, :] - tc[None, None, :]) / d
            tmin = np.nanmax(np.minimum(t1, t2), -1)
            tmax = np.nanmin(np.maximum(t1, t2), -1)
            m |= (tmax >= np.maximum(tmin, 0.0)) & (tmax > 0) & (tmin < NEAR_LIMIT_M)
        dk = MASK_DILATE_REAR if str(c).startswith("ring_rear") else MASK_DILATE
        m = cv2.dilate(m.astype(np.uint8), np.ones((dk, dk), np.uint8)) > 0
        masks[str(c)] = m
    return masks


def save_ego_mask_npz(log_dir, out_npz):
    masks = build_ego_masks(log_dir)
    np.savez_compressed(out_npz, **masks)
    return {c: round(float(m.mean()), 4) for c, m in masks.items()}
