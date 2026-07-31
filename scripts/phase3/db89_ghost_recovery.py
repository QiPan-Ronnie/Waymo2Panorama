"""DB-89: ghost-zone temporal recovery — the hardened v7 GENERAL algorithm (L4 for YOLO only).

Five evidence-driven rules, zero scene parameters:
  1. STATIC world <- EMC render (per-camera exposure-time ego poses).
  2. OBJECT BODY <- ONE camera c_own at ONE exposure time, chosen by EVIDENCE
     COMPLETENESS first (mask not truncated by its image border = that camera saw the
     WHOLE object; splitting a straddler across two exposure times necessarily tears
     it open by object_speed * dt at the FOV boundary), Voronoi dominance among
     equals. Identity matching is ONE-TO-ONE (greedy by IoU; a camera instance is
     evidence for exactly one object). Extent = matched mask under TOPOLOGICAL
     CLOSURE (binary_fill_holes: parameter-free, boundary-preserving); uniform
     object-distance projection.
  3. SECONDARY BODY with OBJECT-MOTION SHUTTER COMPENSATION (OMC, the object-side
     symmetric piece to DB-86's ego EMC): when no camera sees the whole object, the
     split across exposure times is unavoidable — measure the object's ERP
     displacement between the two exposures from the masks themselves (binary
     alignment inside the overlap strip both cameras see) and shift the secondary
     camera's contribution to c_own's exposure-time position before compositing.
     Without OMC the halves tear open by object_speed * dt at the FOV boundary;
     without secondary body at all, temporal fill erases the truncated half.
  4. GHOST ZONE (other cameras' unshifted copies, minus ALL cameras' body evidence)
     <- temporal recovery as the LAST RESORT: only where NO camera cleanly sees the
     background at anchor time (all views poisoned, true mutual disocclusion), under
     a TRIPLE gate: object provably departed (|dframe|>=3) AND padded-box-free
     sightline at that frame AND LiDAR-evidenced background depth. Where a clean
     camera exists, RULE 2 renders the true anchor-time background instead.
  5. Gate fails -> keep the EMC pixel. No depth overwrites anywhere.
Sanity asserts closing DB-88 v7's infra failure: (a) skip cameras with |cam_ts-anchor|>=60ms;
(b) skip per-object box regions wider than 2x their expected angular size.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db89_ghost_recovery"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db89_ghost_recovery"
RESULT = REMOTE_OUT + "/DB89_remote_result.json"

CASE_NAMES = ["02a00399_a000_bmw", "9f871fb4_a030_downtown", "fbee355f_a030_crowd",
              "0bae3b5e_a030_clean", "2c652f9e_a030_highway"]


def remote_py() -> str:
    code = r'''
import hashlib, json, math, pathlib, subprocess, sys, time, traceback
import warnings as _w
_w.warn = lambda *a, **k: None  # DB115-PRO fix#1 (2026-07-10): band-off cap is all-NaN -> nanmedian fired 2.28M RuntimeWarns = 233s of a 241s/frame (a filterwarnings-ignore still pays ~10us/call; the no-op is the measured config). Byte-identical output verified (md5, a145).
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
GROUND_MODE = "fill"   # "fill"=STAGE-4 nadir reconstruction; "off"=middle-only base stitch (skip ground outpaint entirely -> BLACK nadir, like the Fable-5 board). ("mask" gray branch is deprecated/dead.) "funnel"=DB-109 Stage-1 diagnostic: runs fill + dumps a per-pixel gate-funnel npy (_funnel_cls.npy) + counts in OUT["funnel"]; no main-path change.
ANNOTATION_POLICY = "composite"  # Explicit pixel-ownership contract. "composite" permits annotation/YOLO recovery; "raw_sensor" preserves synchronized sensor pixels and cannot paste an annotation-lagged second body. Scene-band drivers must choose raw_sensor explicitly; this is deliberately independent of GROUND_MODE.
SEAM_OBJDEPTH = False   # DB-103 isolation test (default OFF, never ships): force close-object ERP regions to their box depth before scene-band reproject, to isolate the near-car seam-shear cause (depth-field smoothing vs occlusion). Does NOT touch the Fable-5 core when False.
SEAM_MASK_FILL = False  # DB-104 robust mask (default OFF, gated): fill ENCLOSED holes (windows) in each YOLO object mask via binary_fill_holes (NOT dilation -> cannot inflate the boundary or merge instances, so it does NOT reintroduce the v7 giant-instance bug). A complete object body also gives the flow-morph more registration signal. Off = pure Fable-5 mask.
SEAM_FLOWMORPH = True   # DB-103 fix (SHIPPED 2026-06-19, validated: a309 shear gone 32->8.6px, crowd a50 helped, clean seams byte-identical, 6-frame temporal stable): when the view-morph ECC-AFFINE residual is large (close-object depth-varying parallax), replace the affine displacement with dense Farneback optical flow INSIDE the object body. GATED on max_reg_px>8 -> fires ONLY on the rare near-object-break seams, never touches the well-registered ones (clean frames byte-identical). Pristine core in _baseline_fable5/. Set False to revert to pure affine.
GAIN_PER_CHANNEL = False   # DB-208 (2026-07-30, user: "修复后...感觉颜色发紫"): solve ONE exposure gain per camera on luminance, instead of three independent per-channel gains. Rationale, from what the co-visible evidence can and cannot support: an EXPOSURE difference is a common offset across all three channels and can be estimated from any surface; a WHITE-BALANCE difference is an R/B offset relative to G and estimating it needs a NEUTRAL reference, which co-visible LiDAR points cannot provide — they land on whatever colour the scene happens to have, so the three-channel solve fits that colour, not the AWB. Measured: on a healthy frame the per-channel solution's R/B ratio is 0.974-1.043 across all seven cameras (i.e. it finds no white-balance difference to correct — those three degrees of freedom buy nothing), while on the poisoned frame it swings to 0.898 (side_right) and 0.917 (rear_right), which is exactly the magenta cast the user saw: R +9.5% and B +16.8% over the raw sensor while G stayed at +0.9%. Per-channel therefore never fits real AWB, only noise. True = pre-DB-208 behaviour.
GAIN_PRIOR_W = 0.05   # DB-203b/214: weight (relative to the pair's own sample count) of the "no relative exposure difference" prior. DB-214 applies it continuously to the unexplained fraction (1-max(rho,0)^2), rather than only after a threshold rejects an edge. Dropping an unmeasurable edge is not neutral: the remaining graph then infers the pair around a 6-hop path and can accumulate more error (00a6ffc1 a099 front_right|side_right: 22.0 -> 38.0). 0 disables this honest fallback.
GAIN_STRENGTH = 1.0   # DB-184b REVERTED to 1.0 by DB-198 (2026-07-30). History, because the trap is instructive: on 00a6ffc1 the gain solution overshoots badly (fr_0037 front_right|side_right seam step: 70.5 at full gain vs 12.8 with NO gain — the solve is simply wrong on that log), and 0.5 looked like a clean Pareto win there (seam 8.75->6.92 on fr_0037, 8.00->6.21 on fr_0032, plus tonal deviation from the recorded sensor colour 12.5%->6.1%). It does NOT generalise: on three unseen crowded logs (1842383a / e453f164 / 280269f9) 0.5 made the seams WORSE on all three (5.50->7.33, 9.08->14.17, 6.33->7.67) — there the solve is right and halving it just under-corrects, leaving visible territory blocks (s2 front_left|front_center 8.3->20.7). So 00a6ffc1 is an outlier whose gain SOLVE is broken, not evidence that the gain is globally too strong; the real fix is robustifying solve_gains_for (reject co-visible pairs contaminated by flare / view-dependent BRDF), not a global scale. Set 0.5 only to reproduce the DB-184b experiment. ALSO NOTE: the seam numbers quoted in the DB-184b commit came from a measurement that fixed each seam at one COLUMN and only sampled rows where that column was a boundary — a territory boundary is a CURVE, so most rows went unmeasured; DB-198 walks the boundary row by row and is the number to trust.
COLOR_DIAG = False   # DB-215 diagnostic only: compare both cameras at the SAME 3D rays along their real curved ownership boundary. Dumps raw/corrected luminance, chroma, and camera-coordinate spatial residuals plus the per-log territory map. It never changes rendered pixels.
DEPTH_SEAMRAMP_DEG = 10.546875  # DB-214 resolution-invariant form of DB-184's validated 60 px at W=2048 (60*360/2048). Fine inverse depth is retained only near multi-camera overlap; single-camera interiors use the smooth field so glyph strokes cannot inherit LiDAR-depth gradients. Angular distance wraps at the ERP meridian.
SEAM_SINGLE_SOURCE = False  # DB-105 (diagnostic-validated on a309): when c_own sees the object COMPLETE and a secondary contributes only a small grazing sliver (mask << c_own area), DROP the secondary body-fill + SKIP the view-morph -> pure single-source. The near-car seam's CAUSE is the morph FUSING a complete car (side_left 1610 LiDAR pts) with a 149-pt grazing sliver (front_left). Gated, default OFF; pristine core in _baseline_fable5/.
GROUND_RESID = "plate"  # DB-108 (AUDIT 2026-06-22): how the evidence-INSUFFICIENT nadir (spread>30 or no source) is filled. "plate"=DB-99 gray DC plate (DEFAULT, honest-but-gray). "inpaint"=video-era NS-inpaint (cv2.INPAINT_NS extends real edges into the blind cap) -> ground-FEEL (the ground_video_v1 look; blurry/白团 on bare asphalt). COMBO (audit-verified, recovers ground-feel + keeps near car) = "inpaint" + the DB-106 boundary. Gated, default unchanged (gray).
GROUND_TORCH = False  # DB115-PRO fix#3 (2026-07-10): GPU-batched STAGE-4 source-selection scan (same math as the CPU loop, ~25f x 7cam x 800k pts -> torch; fill 226s/frame bulk). False = untouched CPU path. Needs CUDA; silently falls back when unavailable.
BAND_TORCH = False  # DB115-PRO fix#5 (2026-07-10): GPU-batched scene-band composite core — the 2M-px depth reprojection X=C+Zd*DIRS, the 7-camera projection/poison/bperp pass and the source-choice argmin run in torch float64 (same math, same float32 outputs). False = untouched CPU path; silently falls back without CUDA. Backup of the pre-fix file: _backup_db115pro/db89_ghost_recovery_20260710_v6_pre_bandtorch.py
MOVING_GATE = True  # DB-109 Stage-1b (diagnostic, default True = shipped behavior): STAGE-4 ground-source moving-object occlusion gate. Set False to isolate whether a309's 94% gate3 is OVER-AGGRESSIVE box-occlusion (real recovers when off) vs GENUINE car blocking (newly-admitted sources read as car-body -> spread>30, real stays low).
MOVING_SCALE = 1.3  # DB-109 Stage-1c: moving-box inflation factor (default 1.3 = shipped). 1.0 = precise box. The 1.3x inflation + whole-grazing-ray test over-blocks ~76% of good ground sources on traffic frames (a309 5.6%->81.9% when off); shrinking toward 1.0 recovers them, the spread gate backstops genuine car-body.
WORLDBEV_WIN = (0, 92)  # DB-109 B1 (GROUND_MODE="worldbev"): FIXED anchor window [lo,hi] the world ground map is built over. Fixed (NOT anchor-relative) so neighbouring target anchors sample the SAME map -> temporal-coherence test. Driver sets it per scene.
WORLDBEV_FILL = ""  # DB-109 coherence test: path to a FLUX-filled world-BEV png; if set, worldbev OVERRIDES the built map with it so every target frame samples the SAME generated map ("generate once + sample" = temporal coherence by construction). Empty = build normally.
WORLDBEV_CENTER = ""  # DB-123 cascade: "x,y" city metres; pins the map grid origin so a WORLDBEV_FILL map built at one anchor stays registered when sampled from neighbouring anchors. Empty = anchor-centred (unchanged).
CAP_ONLY = False  # DB-126: fill/wbev renders whose BAND pixels are unused by the cascade composite skip YOLO seg + OMC object matching (morph/view-morph collapse via empty morph_jobs); the cap pipeline (cast/low-pass/resid fallback) is untouched. Default False (all shipped paths unchanged).
CAP_LIMIT_TMPL = ""  # DB-126: printf-style glob template (anchor as %03d) for an external mask ANDed into the nadir cap — band frames only need the egozone strip, skipping 75-85% of the candidate scan. Empty = full cap (unchanged).
CAP_REF_TMPL = ""  # DB-126: printf-style glob template for an external band segcomposite used as the cast-correction truth ring when CAP_ONLY leaves comp black (self-reference would disable the cast fix). Empty = comp ring (unchanged).
WORLDBEV_SHARD = ""  # DB-131: "i,k" — this build only processes source frames _wfis[i::k]; combined with WORLDBEV_DUMP, K parallel shard workers replace the single-process map build (its 4-15min was the production critical path). Empty = full build (unchanged).
WORLDBEV_DUMP = ""  # DB-131: npz path; after the source-selection+sampling loops, dump (chosen, score, col) raw slot state for the shard-merge. Empty = no dump (unchanged).
WORLDBEV_LOAD = ""  # DB-131: npz path; SKIP both build loops and load merged (chosen, score, col) instead — the native post-processing (gain/median/tier/Telea) then runs unchanged, so the merge path re-uses the tuned pipeline instead of re-implementing it. Empty = build normally (unchanged).
COHERENT = False  # DB-109 B-coherence (fill variant, gated, default off): keep the per-pixel cap reprojection (the 81.9% MOVING_GATE=False path) but make the SOURCE PICK a deterministic function of the WORLD ground point (FIXED window + egod-closest-to-sweet) so neighbouring anchors agree on the same world point -> temporal coherence WITHOUT the world-grid's discretisation/accumulation loss. Use with MOVING_GATE=False; nvalid>=2 guard against single-source car-body.
COHERENT_WIN = (0, 92)  # fixed candidate window for COHERENT (driver sets per scene)
COHERENT_SWEET = 22.0   # egod sweet-spot (m): the source whose ground-distance is closest to this is picked, deterministically per world point (20-28 m is the inner-cap grazing window)
COHERENT_PICK = "sweet"  # DB-109 Evidence-A: per-world-point source PICK among the egod-sweet slots. "sweet"=slot-0 egod-closest single source (= the spread-19.8 "格子"/quilt, current default); "agree"=argmin distance-to-median (BEST-AGREEING source — also deterministic per world point in a FIXED window, so it kills the quilt by selecting AGREEING colours while staying temporally coherent). Evidence-A: sweet=spread~19.8 mediocre; the pristine old patches were spread~1.6 (argmin-to-median). EYE-test vs sweet before shipping (prior loop-r1 "argmin=blurred" was over egod-NEAR not egod-SWEET slots).
SELFOCC = True  # DB-109 Lever-1 (user non-generative probe): apply the two-box ego SELF-OCCLUSION gate in the STAGE-4 fill source loop. Default True (shipped). False = let grazing-over-own-body views through -> test whether they recover REAL road (gate too conservative) or hood sky-reflection (gate correct).
SELFOCC_DEEP_R = 0.0  # DB-109 LOCAL self-occ radius (m). 0 = global (shipped). >0 = apply the hood self-occ gate ONLY to cap points within R metres of the ego (the deep centre that is genuinely hood-only) and KEEP mid-field grazing views of REAL lanes. Resolves the "self-occ ON deletes lanes / OFF keeps the car-head" tradeoff. Use with SELFOCC=True.
FAITH_MASK = False  # DB-109 A (faithful-base + generative fill): also export {run}_faithfill_mask.png = 255 where the nadir is NOT faithful-real (cap abstained-to-plate OR foreground-occluded) = the region the downstream temporally-coherent generative fill (Cosmos/DiT, DB-14) must paint. Gated; default off (shipped output unchanged).
HOOD_TO_MASK = False  # DB-114 ROOT FIX (no FLUX; fable5 NS-inpaint): the ego hood/rig is rendered into comp (non-black), so DB-106's resid_m=comp-black-only misses it -> hood returns. Restore the video-era egoproj->resid union (then GROUND_RESID="inpaint" NS-fills the hood, exactly like ground_video_v1) BUT keep DB-106's near-car protection via LiDAR support: the hood is the SELF body (egoproj with NO LiDAR return = large Zsupport); a real near-car returns LiDAR (small Zsupport). resid_m |= egoproj & (Zsupport > HOOD_SUPPORT_PX). Use with GROUND_RESID="inpaint" + GROUND_MODE="fill". Gated; default off (shipped unchanged).
HOOD_SUPPORT_PX = 12.0  # px: a lower-nadir egoproj pixel whose nearest LiDAR return is farther than this (= no real surface there = ego hood/rig) gets filled; real near-cars return LiDAR (small dist) and stay protected (DB-106).
EGO_BLACK = False  # DB-123: scene-band ego-body removal. In GROUND_MODE="off" (band frames) the ego hood/body pixels (egoproj two-box gate & no-LiDAR-support, same discriminator as DB-114 HOOD_TO_MASK) are BLACKED OUT instead of kept — black is the band's honest "Cosmos will paint this" domain, and the mask twin (comp.sum>=12) follows automatically. Real near-cars overlapping the hood keep their LiDAR support and are protected. Default False (shipped band output unchanged); driver v6 sets True.
EGO_BLACK_DILATE = 9  # px: dilation margin around the ego-body mask (catches grazing z-error smear at the hood boundary that the 2-box gate misses, cf. EGO_IMG_MASK note).
EGO_IMG_MASK = ""  # DB-118 E-ego (fable5): npz of per-ring-cam quarter-res bool masks (temporal-variance static + bottom-edge-connected = ego BODY in the image). extract rejects samples whose (px,py) lands on the body — the 2-box geometric gate cannot catch grazing z-error slides into hood/trunk pixels.
EMC_RENDER = True  # DB-118 speed #1a: the emc A/B render + board are display-only; batch/video mode sets False (segcomposite byte-identical either way)
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"),
         ("9f871fb4-3b8e-34b3-9161-ed961e71a6da:30:downtown", "9f871fb4_a030_downtown"),
         ("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "fbee355f_a030_crowd"),
         ("0bae3b5e-417d-3b03-abaa-806b433233b8:30:clean", "0bae3b5e_a030_clean"),
         ("2c652f9e-8db8-3572-aa49-fae1344a875b:30:highway", "2c652f9e_a030_highway")]
WINDOW = 10; DMIN, DMAX = 1.5, 80.0; STATIC_DISP_M = 0.5; SAT_LO, SAT_HI = 10, 245
OBJ_MAX_DIST = 40.0; IOU_MIN = 0.30
SEG_CLASSES = {1, 2, 3, 5, 7, 0}   # bicycle, car, motorcycle, bus, truck, person (COCO)
OUT = {"phase": "db89_ghost_recovery", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"segmentation_ownership_only": True, "generation": False}}

sys.path.insert(0, "/content/waymo2panorama/scripts/phase3"); sys.path.insert(0, "/content/waymo2panorama/code")
from db214_artifact_primitives import (angular_overlap_weight, annotation_enabled,
    load_ego_pose_interpolators, ownership_boundary_indices,
    pair_evidence_weights, photometric_pair_residual_stats, solve_gain_components,
    validate_renderer_capabilities)
from db226_luma_response import (
    RAW_PAIR_SCHEMA_VERSION, collect_pair_samples, fixed_brightness_profile)


def save_rgb(path, arr):
    import cv2
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(arr, 0, 255).astype("uint8"), cv2.COLOR_RGB2BGR))


def erp_dirs():
    u = np.arange(W); v = np.arange(H); uu, vv = np.meshgrid(u, v)
    theta = np.pi - (uu + 0.5) / W * 2 * np.pi; phi = np.pi / 2 - (vv + 0.5) / H * np.pi
    cph = np.cos(phi)
    return np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], -1).astype(np.float64)


DIRS = erp_dirs()


def load_all(case_spec):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader
    import pandas as pd
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    validate_renderer_capabilities(log_dir, GROUND_MODE)
    loader = AV2RingLoader(log_dir); ring_cams = list(loader.cameras()); all_ts = loader.anchor_timestamps_ns()
    ts = all_ts[anchor_idx]; frame = loader.load_synced_frame(ts)
    cte, tri = load_ego_pose_interpolators(log_dir)
    ann_path = log_dir / "annotations.feather"
    ann = pd.read_feather(ann_path) if annotation_enabled(ANNOTATION_POLICY, ann_path.exists()) else None
    cam_ts = {}
    for cam in ring_cams:
        files = sorted(int(p.stem) for p in (log_dir / "sensors" / "cameras" / cam).glob("*.jpg"))
        arr = np.asarray(files, np.int64)
        cam_ts[cam] = int(arr[np.argmin(np.abs(arr - ts))])
    return loader, log_dir, all_ts, anchor_idx, ts, frame, ring_cams, cte, tri, ann, cam_ts


def moving_tracks(ann, t_lo, t_hi):
    if ann is None or "track_uuid" not in ann.columns: return set()
    sub = ann[(ann["timestamp_ns"] >= t_lo) & (ann["timestamp_ns"] <= t_hi)]
    mv = set()
    for uid, g in sub.groupby("track_uuid"):
        if "category" in g.columns and str(g["category"].iloc[0]).upper() not in {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}: continue
        c = g[["tx_m", "ty_m", "tz_m"]].to_numpy(float)
        if len(c) >= 2 and float(np.linalg.norm(c.max(0) - c.min(0))) > STATIC_DISP_M: mv.add(uid)
    return mv


def boxes_at(ann, ts, moving):
    from scipy.spatial.transform import Rotation
    if ann is None: return []
    tss = ann["timestamp_ns"].to_numpy(np.int64); nt = np.unique(tss)[np.argmin(np.abs(np.unique(tss) - ts))]
    out = []
    for _, r in ann[ann["timestamp_ns"] == nt].iterrows():
        if r["track_uuid"] not in moving: continue
        out.append((np.array([r["tx_m"], r["ty_m"], r["tz_m"]], float), np.array([r["length_m"], r["width_m"], r["height_m"]], float),
                    Rotation.from_quat([r["qx"], r["qy"], r["qz"], r["qw"]]).as_matrix()))
    return out


def remove_dyn(pts, boxes, pad=0.3):
    # (DB115-PRO: an einsum batch over boxes was tried and ROLLED BACK — the B x N x 3
    # temporary is a memory-allocation storm on box-heavy urban scenes, net slower.)
    if not boxes or len(pts) == 0: return np.ones(len(pts), bool)
    keep = np.ones(len(pts), bool)
    for c, sz, Rb in boxes:
        loc = (pts - c) @ Rb; half = sz / 2 + pad
        keep &= ~((np.abs(loc[:, 0]) < half[0]) & (np.abs(loc[:, 1]) < half[1]) & (np.abs(loc[:, 2]) < half[2]))
    return keep


_SWEEP_CACHE = {}
_DYN_CACHE = {}
def accumulate_lidar(log_dir, anchor_ts, cte, tri, ann):
    # DB115-PRO fix#4: per-sweep feather IO + city transform are ANCHOR-INDEPENDENT
    # (city frame) -> cache them across the batch (adjacent anchors share ~90% of
    # their sweep windows). Only the anchor-dependent parts (moving set -> dynamic
    # removal, city->anchor transform) run per call. Same output.
    import pandas as pd
    sweeps = sorted((log_dir / "sensors" / "lidar").glob("*.feather"))
    if not sweeps:
        return np.zeros((0, 3), dtype=np.float64), cte(anchor_ts), set()
    stss = np.array([int(p.stem) for p in sweeps], np.int64); ai = int(np.argmin(np.abs(stss - anchor_ts)))
    t_lo, t_hi = int(stss[max(0, ai - WINDOW)]), int(stss[min(len(stss) - 1, ai + WINDOW)])
    moving = moving_tracks(ann, t_lo, t_hi)
    Ra, ta = cte(anchor_ts); acc = []
    for si in range(max(0, ai - WINDOW), min(len(sweeps), ai + WINDOW + 1)):
        sts = int(stss[si])
        ent = _SWEEP_CACHE.get(sts)
        if ent is None:
            df = pd.read_feather(sweeps[si]); xyz = df[["x", "y", "z"]].to_numpy(np.float64)
            off = df["offset_ns"].to_numpy(np.int64) if "offset_ns" in df.columns else np.zeros(len(df), np.int64)
            Rsw, _ = cte(sts); city = (Rsw @ xyz.T).T + tri((sts + off).astype(np.int64))
            if len(_SWEEP_CACHE) > 80: _SWEEP_CACHE.pop(next(iter(_SWEEP_CACHE)))
            ent = (xyz, city); _SWEEP_CACHE[sts] = ent
        xyz, city = ent
        # keep-mask cache: boxes_at depends only on (sts, moving); adjacent anchors
        # share the moving set almost always -> the dynamic-removal mask is reused
        # verbatim (exact equivalence).
        _dk = (sts, tuple(sorted(moving)))
        keep = _DYN_CACHE.get(_dk)
        if keep is None:
            keep = remove_dyn(xyz, boxes_at(ann, sts, moving))
            if len(_DYN_CACHE) > 400: _DYN_CACHE.pop(next(iter(_DYN_CACHE)))
            _DYN_CACHE[_dk] = keep
        acc.append((city[keep] - ta) @ Ra)
    return np.concatenate(acc, 0) if acc else np.zeros((0, 3)), (Ra, ta), moving


def bilinear(img, px, py):
    x0 = np.floor(px).astype(np.int64); y0 = np.floor(py).astype(np.int64)
    fx = (px - x0)[:, None]; fy = (py - y0)[:, None]
    hh, ww = img.shape[:2]
    x0c = np.clip(x0, 0, ww - 2); y0c = np.clip(y0, 0, hh - 2)
    a = img[y0c, x0c].astype(np.float64); b = img[y0c, x0c + 1].astype(np.float64)
    c = img[y0c + 1, x0c].astype(np.float64); d = img[y0c + 1, x0c + 1].astype(np.float64)
    return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy


def solve_gains_for(frame, ring_cams, lidar, C):
    sub = lidar[np.random.RandomState(0).choice(len(lidar), min(len(lidar), 150000), replace=False)]
    Q = sub - C[None, :]; n = np.linalg.norm(Q, axis=1)
    sub = sub[(n > DMIN) & (n < DMAX)]
    obs = []
    for cam in ring_cams:
        cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
        Tci = np.linalg.inv(T); img = frame.images[cam]; hh, ww = img.shape[:2]
        Xc = (Tci[:3, :3] @ sub.T).T + Tci[:3, 3]; z = Xc[:, 2]
        px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
        ok = (z > 0.5) & (px >= 2) & (px < ww - 2) & (py >= 2) & (py < hh - 2)
        rgb = np.zeros((len(sub), 3)); rgb[ok] = bilinear(img, px[ok], py[ok])
        obs.append((ok & (rgb.min(1) > SAT_LO) & (rgb.max(1) < SAT_HI), rgb))
    nc = len(ring_cams)
    # DB-214: validity is a confidence, not a threshold. Positive rho^2 is the
    # explained-variation fraction for the log-domain constant-offset model. The
    # complementary weight goes to the zero-difference prior. Thus 0.298 and 0.330
    # behave continuously while the poisoned rho=0.029 edge is prior-dominated.
    pair_rho = {}
    for i in range(nc):
        for j in range(i + 1, nc):
            both = obs[i][0] & obs[j][0]
            if both.sum() < 50: continue
            li = np.log(np.maximum(obs[i][1][both].mean(1), 1.0))
            lj = np.log(np.maximum(obs[j][1][both].mean(1), 1.0))
            rho = None if li.std() < 1e-3 or lj.std() < 1e-3 else float(np.corrcoef(li, lj)[0, 1])
            pair_rho[(i, j)] = rho
            _wm, _wp, _conf = pair_evidence_weights(rho, int(both.sum()), GAIN_PRIOR_W)
            if _conf < 0.25:
                print("GAIN_PAIR_CONF %s|%s rho=%s conf=%.4f evidence=%.1f prior=%.1f n=%d" %
                      (ring_cams[i], ring_cams[j], "flat" if rho is None else "%.3f" % rho,
                       _conf, _wm, _wp, int(both.sum())), flush=True)
    gains = np.zeros((nc, 3))
    for ch in ([0, 1, 2] if GAIN_PER_CHANNEL else [None]):
        A = np.zeros((nc, nc)); b = np.zeros(nc)
        pair_edges = set()
        for i in range(nc):
            for j in range(i + 1, nc):
                both = obs[i][0] & obs[j][0]
                if both.sum() < 50: continue
                if ch is None:   # DB-208: exposure only, estimated on luminance
                    li = np.log(np.maximum(obs[i][1][both].mean(1), 1.0)); lj = np.log(np.maximum(obs[j][1][both].mean(1), 1.0))
                else:
                    li = np.log(np.maximum(obs[i][1][both, ch], 1.0)); lj = np.log(np.maximum(obs[j][1][both, ch], 1.0))
                wgt, wp, _ = pair_evidence_weights(pair_rho[(i, j)], int(both.sum()), GAIN_PRIOR_W)
                if wgt + wp > 0:
                    pair_edges.add((i, j))
                if wp > 0:
                    A[i, i] += wp; A[j, j] += wp; A[i, j] -= wp; A[j, i] -= wp
                if wgt > 0:
                    dm = float(np.median(lj - li))
                    A[i, i] += wgt; A[j, j] += wgt; A[i, j] -= wgt; A[j, i] -= wgt
                    b[i] += wgt * dm; b[j] -= wgt * dm
        # Each connected component has its own additive gauge.  A single global
        # ones-matrix only fixes a connected graph; sparse nuScenes overlap left
        # isolated cameras and made this system singular.  No cross-component
        # evidence means identity relative offset, not an invented bridge.
        c = solve_gain_components(A, b, pair_edges)
        if ch is None:
            gains[:, 0] = gains[:, 1] = gains[:, 2] = c - c.mean()
        else:
            gains[:, ch] = c - c.mean()
    return gains


def depth_field(lidar, C):
    from scipy.ndimage import distance_transform_edt
    Q = lidar - C[None, :]; n = np.linalg.norm(Q, axis=1)
    m = (n > DMIN) & (n < DMAX); Qm = Q[m]; nm = n[m]
    d = Qm / nm[:, None]
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    ui = np.clip(np.round((np.pi - theta) / (2 * np.pi) * W - 0.5).astype(np.int64), 0, W - 1)
    vi = np.clip(np.round((np.pi / 2 - phi) / np.pi * H - 0.5).astype(np.int64), 0, H - 1)
    Z = np.zeros((H, W), np.float32)
    order = np.argsort(-nm); flat = vi * W + ui; zf = Z.reshape(-1); zf[flat[order]] = nm[order].astype(np.float32)
    valid = Z > 0
    dist_px, inds = distance_transform_edt(~valid, return_distances=True, return_indices=True)
    Zf = Z[inds[0], inds[1]].astype(np.float32)
    # nearest-neighbour is a 1-SAMPLE depth estimator: on thin/sparse structures
    # (poles, glass mullions) it flips per pixel between foreground and background
    # returns, and every flip times the camera baseline becomes a sampling jump =
    # GRAIN (user-confirmed vs the L1 baseline, present already in the EMC base).
    # A neighbourhood MEDIAN keeps true depth edges but kills the bimodal jitter.
    import cv2 as _cvd
    Zf = _cvd.medianBlur(Zf, 5)
    dz = DIRS[:, :, 2]
    plane_t = np.where(dz < -0.05, (-C[2] - 0.33) / np.minimum(dz, -1e-3), np.inf).astype(np.float32)
    use_plane = (dist_px > 12) & np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX)
    Zf = np.where(use_plane, plane_t, Zf)
    # DEPTH-EVIDENCE GATING (rule 8): per-pixel reprojection is only legal where the
    # depth evidence is trustworthy. On specular/transmissive surfaces (glass facades:
    # LiDAR punches through or mirror-bounces -> WHOLE-PATCH garbage, not salt noise)
    # and at discontinuity edges, per-pixel depth shatters the render. There the
    # region degrades to a LARGE-SCALE robust depth (the L1-style locally-flat render:
    # coherent even if a few px displaced — coherence over absolute position).
    # Trust = close LiDAR support AND agreement with the large-scale median.
    small = _cvd.resize(Zf, (W // 8, H // 8), interpolation=_cvd.INTER_NEAREST)
    Zsmooth = _cvd.resize(_cvd.medianBlur(small, 5), (W, H), interpolation=_cvd.INTER_LINEAR)
    conf = (dist_px <= 4) & (np.abs(Zf - Zsmooth) < 0.05 * Zsmooth)
    Zf = np.where(conf, Zf, Zsmooth)
    return np.where(Zf <= 0, 200.0, Zf), dist_px.astype(np.float32)


_TPA_CACHE = {}
def track_pose_at(ann, uid, t_query, cte, anchor_R, anchor_t):
    # DB115-PRO fix#2 (2026-07-10): per-uid cache. The track's CITY-frame trajectory is
    # anchor-independent (anchor_R/t applied after lookup), so the pandas scan + per-row
    # slerp is paid once per track, not per query (was 20s/frame; byte-identical, md5 a145).
    from scipy.spatial.transform import Rotation
    ent = _TPA_CACHE.get(uid)
    if ent is None:
        g = ann[ann["track_uuid"] == uid].sort_values("timestamp_ns")
        tss = g["timestamp_ns"].to_numpy(np.int64)
        if len(tss) == 0:
            _TPA_CACHE[uid] = False
            return None
        q = g[["qx", "qy", "qz", "qw"]].to_numpy(float)
        ce = g[["tx_m", "ty_m", "tz_m"]].to_numpy(float)
        Rbs = Rotation.from_quat(q).as_matrix()
        centers_city = np.empty((len(tss), 3))
        Rs_city = np.empty((len(tss), 3, 3))
        for _i in range(len(tss)):
            Re, te = cte(int(tss[_i]))
            centers_city[_i] = Re @ ce[_i] + te
            Rs_city[_i] = Re @ Rbs[_i]
        sizes = g[["length_m", "width_m", "height_m"]].to_numpy(float)
        ent = (tss, centers_city, Rs_city, sizes)
        _TPA_CACHE[uid] = ent
    if ent is False:
        return None
    tss, centers_city, Rs_city, sizes = ent
    t_rel = (tss - tss[0]).astype(np.float64)
    tq = float(np.clip(t_query - tss[0], t_rel.min(), t_rel.max()))
    c_q = np.array([np.interp(tq, t_rel, centers_city[:, i]) for i in range(3)])
    ni = int(np.argmin(np.abs(t_rel - tq)))
    R_q = Rs_city[ni]
    sz = sizes[ni]
    c_a = anchor_R.T @ (c_q - anchor_t)
    R_a = anchor_R.T @ R_q
    return c_a, sz, R_a


def ray_obb_region(c, sz, Rb, C, pad=1.0):
    """Flat ERP indices whose centroid ray hits the box, + entry depth.
    SANITY (assert b): returns empty if the region is wider than 2x the box's expected
    angular size (guards against far-interpolated poses exploding the region)."""
    half = sz / 2 * pad
    dist = float(np.linalg.norm(c - C))
    if dist < 0.5: return np.zeros(0, np.int64), np.zeros(0, np.float32)
    expected_w_px = float(np.linalg.norm(sz)) / dist * (W / (2 * np.pi)) * 1.2
    corners = np.array([[sx * half[0], sy * half[1], sz_ * half[2]]
                        for sx in (-1, 1) for sy in (-1, 1) for sz_ in (-1, 1)])
    P = (corners @ Rb.T) + c[None, :]
    Q = P - C[None, :]
    n = np.linalg.norm(Q, axis=1); d = Q / n[:, None]
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    u = (np.pi - theta) / (2 * np.pi) * W - 0.5
    v = (np.pi / 2 - phi) / np.pi * H - 0.5
    v0 = max(int(np.floor(v.min())) - 1, 0); v1 = min(int(np.ceil(v.max())) + 1, H - 1)
    us = np.sort(u % W)
    gaps = np.diff(np.concatenate([us, us[:1] + W]))
    gi = int(np.argmax(gaps))
    u_start = us[(gi + 1) % len(us)]
    width = (us[gi] - u_start) % W if len(us) > 1 else 0
    if width > 2 * expected_w_px:                      # assert (b)
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
    cols = (np.arange(int(np.floor(u_start)) - 1, int(np.floor(u_start)) + int(np.ceil(width)) + 2)) % W
    rows = np.arange(v0, v1 + 1)
    if len(cols) == 0 or len(rows) == 0: return np.zeros(0, np.int64), np.zeros(0, np.float32)
    sub = DIRS[np.ix_(rows, cols)].reshape(-1, 3)
    o_loc = Rb.T @ (C - c)
    d_loc = sub @ Rb
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / d_loc
        t1 = (-half[None, :] - o_loc[None, :]) * inv
        t2 = (half[None, :] - o_loc[None, :]) * inv
    tmin = np.nanmax(np.minimum(t1, t2), axis=1)
    tmax = np.nanmin(np.maximum(t1, t2), axis=1)
    hit = (tmax >= np.maximum(tmin, 0.0)) & (tmax > 0)
    tent = np.where(tmin > 0, tmin, tmax).astype(np.float32)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    flat = (rr.reshape(-1) * W + cc.reshape(-1))
    return flat[hit], tent[hit]


def box_img_bbox(c_a, sz, R_a, K, Rc, tc, hh, ww):
    """Project box corners into a camera (EMC pose); return (x0,y0,x1,y1) or None."""
    half = sz / 2
    corners = np.array([[sx * half[0], sy * half[1], sz_ * half[2]]
                        for sx in (-1, 1) for sy in (-1, 1) for sz_ in (-1, 1)])
    P = (corners @ R_a.T) + c_a[None, :]
    Xc = (Rc.T @ (P - tc[None, :]).T).T
    if (Xc[:, 2] <= 0.2).all(): return None
    vis = Xc[:, 2] > 0.2
    px = K[0, 0] * Xc[vis, 0] / Xc[vis, 2] + K[0, 2]
    py = K[1, 1] * Xc[vis, 1] / Xc[vis, 2] + K[1, 2]
    x0, x1 = float(px.min()), float(px.max()); y0, y1 = float(py.min()), float(py.max())
    if x1 < 0 or x0 > ww or y1 < 0 or y0 > hh: return None
    return (max(x0, 0), max(y0, 0), min(x1, ww), min(y1, hh))


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1]); ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(ix1 - ix0, 0), max(iy1 - iy0, 0)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(ua, 1e-6)


def run_case(case_spec, run_name):
    from PIL import Image, ImageDraw, ImageFont
    import cv2
    loader, log_dir, all_ts, anchor_idx, ts, frame, ring_cams, cte, tri, ann, cam_ts = load_all(case_spec)
    Ra, ta = cte(ts)
    lidar, _, moving = accumulate_lidar(log_dir, ts, cte, tri, ann)
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    C = cents.mean(axis=0)
    # GRACEFUL NO-LiDAR DEGRADATION (evidence-insufficiency fallbacks): without LiDAR
    # the gains stay identity, depth degrades to ground-plane + far shell, and the
    # LiDAR-gated temporal fill disarms itself (Zsupport=inf -> sup_ok empty).
    if len(lidar) < 1000:
        gains = np.zeros((len(ring_cams), 3))
        dz0 = DIRS[:, :, 2]
        Zd = np.where(dz0 < -0.05, np.clip((-C[2] - 0.33) / np.minimum(dz0, -1e-3), DMIN, DMAX), 100.0).astype(np.float32)
        Zsupport = np.full((H, W), 1e9, np.float32)
    else:
        gains = solve_gains_for(frame, ring_cams, lidar, C) * GAIN_STRENGTH   # DB-184b: the LS solution overshoots ~2x
        Zd, Zsupport = depth_field(lidar, C)
    if SEAM_OBJDEPTH and ann is not None and "track_uuid" in ann.columns:
        # DB-103 isolation test: force CLOSE-object ERP regions to the object's OWN box
        # depth (not the smoothed depth field) BEFORE the scene-band reprojection, to test
        # whether the near-car seam shear (front_left/side_left max_reg_px=32) comes from
        # depth_field smoothing the car's depth at the car/background discontinuity.
        _au = set(ann["track_uuid"].unique()); _nov = 0
        for _bc, _bsz, _bR in boxes_at(ann, ts, _au):
            if np.linalg.norm(_bc - C) > 12.0: continue
            _reg, _dent = ray_obb_region(_bc, _bsz, _bR, C, pad=1.0)
            if len(_reg):
                Zd.reshape(-1)[_reg] = _dent.astype(np.float32); _nov += len(_reg)
        print("SEAM_OBJDEPTH overrode", _nov, "ERP px with object-box depth", flush=True)
    poses_emc = []
    for cam in ring_cams:
        T = np.asarray(frame.calibrations[cam].T_ego_cam, float)
        Ri, ti_ = cte(cam_ts[cam])
        poses_emc.append((Ra.T @ Ri @ T[:3, :3], Ra.T @ (Ri @ T[:3, 3] + ti_ - ta)))
    cals = [(np.asarray(frame.calibrations[c].K, float), frame.images[c].shape[:2]) for c in ring_cams]
    if DEPTH_SEAMRAMP_DEG > 0:   # DB-184/214: a camera's OWN territory needs no parallax correction
        import cv2 as _cvr
        _nv = np.zeros((H, W), np.int16); _dfr = DIRS.reshape(-1, 3)
        for _i in range(len(ring_cams)):   # far-field visibility: depth-free, calibration only
            _Rc, _tcp = poses_emc[_i]; _Kc, (_hhc, _wwc) = cals[_i]
            _dcam = _dfr @ _Rc; _zc = _dcam[:, 2]
            _pxc = _Kc[0, 0] * _dcam[:, 0] / np.maximum(_zc, 1e-6) + _Kc[0, 2]
            _pyc = _Kc[1, 1] * _dcam[:, 1] / np.maximum(_zc, 1e-6) + _Kc[1, 2]
            _nv += ((_zc > 0.05) & (_pxc >= 1) & (_pxc < _wwc - 1) & (_pyc >= 1) & (_pyc < _hhc - 1)).reshape(H, W)
        _wf = angular_overlap_weight(_nv >= 2, math.radians(float(DEPTH_SEAMRAMP_DEG)))
        _Z32 = Zd.astype(np.float32)
        _sm = _cvr.resize(_Z32, (W // 8, H // 8), interpolation=_cvr.INTER_NEAREST)
        _Zc = _cvr.resize(_cvr.medianBlur(_sm, 5), (W, H), interpolation=_cvr.INTER_LINEAR)
        # inverse-depth blend: parallax is linear in 1/Z, so this is the domain where a
        # ramp neither creates a step at the ramp ends nor bends straight structures.
        Zd = (1.0 / np.maximum(_wf / np.maximum(_Z32, 1e-3) + (1.0 - _wf) / np.maximum(_Zc, 1e-3), 1e-6)).astype(np.float32)
        print("DEPTH_SEAMRAMP overlap=%.2f%% ramp_deg=%.4f mean_w=%.3f" %
              (100.0 * float((_nv >= 2).mean()), DEPTH_SEAMRAMP_DEG, float(_wf.mean())), flush=True)
    # ---- YOLO segmentation on all 7 native images ----
    if not CAP_ONLY:   # DB-126: seg feeds OMC/body/poison — all band-content machinery
        from ultralytics import YOLO
        model = YOLO("yolov8x-seg.pt")
    seg_masks = []   # per camera: full-res bool mask of ALL seg instances (cls in SEG_CLASSES)
    seg_insts = []   # per camera: list of (bbox, mask_lowres, shape)
    for ci, cam in enumerate(ring_cams):
        img = frame.images[cam]
        if CAP_ONLY:   # DB-126: empty per-camera seg -> OMC/poison/body all no-op downstream
            seg_masks.append(np.zeros(img.shape[:2], bool)); seg_insts.append([]); continue
        res = model.predict(img, imgsz=1280, conf=0.25, verbose=False, device=0)[0]
        hh, ww = img.shape[:2]
        full = np.zeros((hh, ww), bool)
        insts = []
        if res.masks is not None:
            for k in range(len(res.boxes)):
                if int(res.boxes.cls[k]) not in SEG_CLASSES: continue
                m = res.masks.data[k].cpu().numpy()
                m = cv2.resize(m, (ww, hh), interpolation=cv2.INTER_NEAREST) > 0.5
                # raw masks: detail gaps (mirrors/pillars/glass) are covered by the mask-UNION-
                # own-time-box rule downstream — no morphology needed (and morphology inflated
                # a mis-matched giant instance into a 530k-px body in the v7 forensics).
                if SEAM_MASK_FILL:   # DB-104: fill ENCLOSED holes (windows) only — NOT dilation, so
                    from scipy.ndimage import binary_fill_holes as _bfh   # the boundary can't inflate
                    m = _bfh(m)
                bb = res.boxes.xyxy[k].cpu().numpy().tolist()
                insts.append((bb, m))
                full |= m
        seg_masks.append(full)
        seg_insts.append(insts)
    # ---- per moving object: per-camera matched instance masks (MOVING ONLY) + choose c_own ----
    # poison masks must contain ONLY moving objects: a static car is consistent in every camera
    # and must not invalidate anyone (v1 used the full YOLO union -> 24% of the image got filled).
    # ---- ALL-IMAGE-EVIDENCE ARCHITECTURE (audit conclusion) ----
    # The AV2 box 3D position is ~4 m off on fast tracks (audit: box projects 100 px away
    # from where the camera actually imaged the car; label-time recalibration is killed by
    # track-boundary clipping at anchor 0). Therefore: boxes do IDENTITY matching only;
    # ALL spatial placement comes from image evidence (per-camera instance masks).
    # assert (a): a camera whose nearest image timestamp is far from the anchor would make
    # track_pose_at interpolate the box to a far position (the DB-88 v7 smear) — skip it.
    cam_valid = [abs(cam_ts[cam] - ts) < 60_000_000 for cam in ring_cams]
    # pass 1: candidate (object, camera, instance) matches
    obj_meta = []   # per moving uid passing the time gate: {"uid", "per_cam_pose"}
    cand = []       # (iou, obj_idx, ci, mi)
    for uid in (sorted(moving) if not CAP_ONLY else []):   # DB-126: OMC is band-content machinery
        g = ann[ann["track_uuid"] == uid]
        nt = g["timestamp_ns"].to_numpy(np.int64)
        if np.abs(nt - ts).min() > 150_000_000: continue
        per_cam_pose = {}
        for ci, cam in enumerate(ring_cams):
            if not cam_valid[ci]: continue
            pose = track_pose_at(ann, uid, cam_ts[cam], cte, Ra, ta)
            if pose is None: continue
            c_a, sz, R_a = pose
            dist = float(np.linalg.norm(c_a - C))
            if dist > OBJ_MAX_DIST or dist < 1.0: continue
            K, (hh, ww) = cals[ci]
            Rc, tc = poses_emc[ci]
            bb = box_img_bbox(c_a, sz, R_a, K, Rc, tc, hh, ww)
            if bb is None: continue
            Xc = Rc.T @ (c_a - tc)
            if Xc[2] <= 0.3: continue
            per_cam_pose[ci] = (c_a, sz, R_a, dist)
            box_area = max((bb[2] - bb[0]) * (bb[3] - bb[1]), 1.0)
            for k, (sbb, sm) in enumerate(seg_insts[ci]):
                v = iou(bb, sbb)
                if v < IOU_MIN: continue
                ratio = float(sm.sum()) / box_area
                if not (0.25 <= ratio <= 4.0): continue   # evidence sanity: reject giant/tiny instances
                cand.append((v, len(obj_meta), ci, k))
        obj_meta.append({"uid": uid, "per_cam_pose": per_cam_pose})
    # pass 2: ONE-TO-ONE greedy by IoU — a camera instance is evidence for exactly ONE
    # object. An instance claimed by >=2 tracks is a MERGED BLOB (adjacent vehicles
    # fused into one mask; identity unresolvable): ambiguous evidence may VETO
    # (poison — conservative, keeps contaminated backgrounds out) but cannot ASSERT
    # (a fused silhouette painted at one object's uniform distance drags the
    # neighbour's pixels onto it — seen on the 6.1 m X3).
    poison_masks = [np.zeros(cals[ci][1], bool) for ci in range(len(ring_cams))]
    claims = {}
    for v, oidx, ci, k in cand:
        claims.setdefault((ci, k), set()).add(oidx)
    ambiguous = {key for key, s in claims.items() if len(s) >= 2}
    for ci, k in ambiguous:
        poison_masks[ci] |= seg_insts[ci][k][1]
    cand.sort(key=lambda t: -t[0])
    taken_inst = set(); taken_slot = set()
    assign = {}   # (obj_idx, ci) -> instance index
    for v, oidx, ci, k in cand:
        if (ci, k) in ambiguous: continue
        if (ci, k) in taken_inst or (oidx, ci) in taken_slot: continue
        taken_inst.add((ci, k)); taken_slot.add((oidx, ci))
        assign[(oidx, ci)] = k
    # pass 3: c_own choice — EVIDENCE COMPLETENESS first (a mask not truncated by its
    # image border means this camera saw the WHOLE object: single-time render, no seam;
    # splitting a straddler across two exposure times necessarily tears it open by
    # object_speed * dt at the FOV boundary), Voronoi dominance among equals.
    n_handled, n_unmatched = 0, 0
    objects = []
    for oidx, meta in enumerate(obj_meta):
        per_cam_mask = {}   # IMAGE evidence per camera (label-position-independent)
        best = None   # (key, ci, mask, dist, complete, area)
        cands_ci = []   # (ci, m, dist, complete, area) — for the DB-105 dominant-coverage flip
        for ci in sorted(meta["per_cam_pose"]):
            k = assign.get((oidx, ci))
            if k is None: continue
            m = seg_insts[ci][k][1]
            c_a, sz, R_a, dist = meta["per_cam_pose"][ci]
            poison_masks[ci] |= m   # this camera sees THIS moving object here
            per_cam_mask[ci] = (m, dist)
            # completeness margin: 1% of the image dimension — YOLO mask edges are ragged,
            # a truncated mask can stop a few px short of the border (seen: x_min=4 on a
            # nose cut off at x=0). False-incomplete is mild (falls back to the split
            # path); false-complete tears the object. Asymmetric costs -> conservative.
            mh, mw = m.shape; mgy, mgx = max(4, mh // 100), max(4, mw // 100)
            complete = not (m[:mgy, :].any() or m[-mgy:, :].any() or m[:, :mgx].any() or m[:, -mgx:].any())
            tc = poses_emc[ci][1]
            dvec = (c_a - C) / max(dist, 1e-6)
            cvec = tc - C
            along = float(dvec @ cvec)
            neg_bperp = -math.sqrt(max(float(cvec @ cvec) - along * along, 0.0))
            area_ci = int(m.sum())
            cands_ci.append((ci, m, dist, complete, area_ci))
            key = (1 if complete else 0, neg_bperp)
            if best is None or key > best[0]:
                best = (key, ci, m, dist, complete, area_ci)
        # DB-105: dominant-coverage flip — completeness mis-ranks a VERY close object (the grazing
        # SLIVER is "complete"; the whole-object camera is "incomplete"). If ONE camera sees the
        # object MUCH more than the completeness-winner (>2.5x mask area), it is the true single-
        # source owner -> flip c_own to it. Fires ONLY on a real dominant (a309 side_left ~10.8x);
        # a genuinely cross-camera object (crowd RAM, comparable areas) is UNCHANGED -> still morphs.
        if SEAM_SINGLE_SOURCE and best is not None and cands_ci:
            dom = max(cands_ci, key=lambda t: t[4])
            if dom[0] != best[1] and dom[4] > 2.5 * max(best[5], 1):
                best = ((1 if dom[3] else 0, 0.0), dom[0], dom[1], dom[2], dom[3], dom[4])
        if best is None:
            n_unmatched += 1
            continue
        n_handled += 1
        objects.append({"ci": best[1], "mask": best[2], "dist": best[3],
                        "per_cam_mask": dict(per_cam_mask),
                        "complete": bool(best[4]), "own_area": int(best[5])})
    # ---- composite ----
    # base EMC render with per-pixel chosen-cam + projections retained
    X = C[None, None, :] + Zd[:, :, None].astype(np.float64) * DIRS
    Xf = X.reshape(-1, 3)
    _bt = False
    if BAND_TORCH:
        try:
            import torch as _th
            _bt = _th.cuda.is_available()
        except Exception:
            _bt = False
    # DB-123 v2: per-camera image-domain ego-body mask (db118_egomask npz, quarter-res)
    # rejects hood/body SOURCE pixels in the main composite projection. The DB-114
    # geometric gate cannot work here: hood pixels get pasted at GROUND depth, whose
    # direction has real LiDAR support (v1 NEG — it blacked real road, kept the hood).
    _EIMC = None
    if EGO_IMG_MASK:
        _eimz_c = np.load(EGO_IMG_MASK)
        _EIMC = [(_eimz_c[c_] if c_ in _eimz_c.files else None) for c_ in ring_cams]
    _ego_rej = np.zeros(len(Xf), bool) if _EIMC is not None else None  # DB-123 C: pixels blacked BECAUSE of the ego mask
    proj = []
    if _bt:
        # DB115-PRO fix#5: same math as the CPU loop below, batched on GPU in float64
        # (float32 outputs bit-match the CPU path within dtype rounding).
        _dev = "cuda"
        Xf_t = _th.as_tensor(Xf, dtype=_th.float64, device=_dev)
        df64 = DIRS.reshape(-1, 3)
        df_t = _th.as_tensor(df64, dtype=_th.float64, device=_dev)
        for ci, cam in enumerate(ring_cams):
            K, (hh, ww) = cals[ci]
            Rc, tc = poses_emc[ci]
            Rc_t = _th.as_tensor(Rc, dtype=_th.float64, device=_dev)
            tc_t = _th.as_tensor(tc, dtype=_th.float64, device=_dev)
            Xc_t = (Xf_t - tc_t[None, :]) @ Rc_t          # == (Rc.T @ (Xf-tc).T).T
            z_t = Xc_t[:, 2]
            zc_t = _th.clamp(z_t, min=1e-6)
            px_t = (K[0, 0] * Xc_t[:, 0] / zc_t + K[0, 2]).float()
            py_t = (K[1, 1] * Xc_t[:, 1] / zc_t + K[1, 2]).float()
            ok_t = (z_t > 0.1) & (px_t >= 1) & (px_t < ww - 1) & (py_t >= 1) & (py_t < hh - 1)
            if _EIMC is not None and _EIMC[ci] is not None:   # DB-123: ego-body source pixels are no source at all
                _em = _EIMC[ci]
                _em_t = _th.as_tensor(_em.astype(np.uint8), device=_dev)
                _exi = _th.clamp((px_t / 4).long(), 0, _em.shape[1] - 1)
                _eyi = _th.clamp((py_t / 4).long(), 0, _em.shape[0] - 1)
                _okb_t = ok_t.clone()
                ok_t = ok_t & ~(_em_t[_eyi, _exi] > 0)
                _ego_rej |= (_okb_t & ~ok_t).cpu().numpy()
            pis_t = _th.zeros(Xf_t.shape[0], dtype=_th.bool, device=_dev)
            if poison_masks[ci].any():
                pm_t = _th.as_tensor(poison_masks[ci].astype(np.uint8), device=_dev)
                xi_t = _th.clamp(px_t.long(), 0, ww - 1)
                yi_t = _th.clamp(py_t.long(), 0, hh - 1)
                pis_t = ok_t & (pm_t[yi_t, xi_t] > 0)
            cvec = tc - C
            along_t = df_t @ _th.as_tensor(cvec, dtype=_th.float64, device=_dev)
            bperp_t = _th.sqrt(_th.clamp(float(cvec @ cvec) - along_t * along_t, min=0.0)).float()
            proj.append({"px": px_t.cpu().numpy(), "py": py_t.cpu().numpy(),
                         "ok": ok_t.cpu().numpy(), "poison": pis_t.cpu().numpy(),
                         "bperp": bperp_t.cpu().numpy()})
        del Xf_t, df_t
    else:
        for ci, cam in enumerate(ring_cams):
            K, (hh, ww) = cals[ci]
            Rc, tc = poses_emc[ci]
            Xc = (Rc.T @ (Xf - tc[None, :]).T).T
            z = Xc[:, 2]
            px = (K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]).astype(np.float32)
            py = (K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]).astype(np.float32)
            ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
            if _EIMC is not None and _EIMC[ci] is not None:   # DB-123: ego-body source pixels are no source at all
                _em = _EIMC[ci]
                _exi = np.clip((px / 4).astype(np.int64), 0, _em.shape[1] - 1)
                _eyi = np.clip((py / 4).astype(np.int64), 0, _em.shape[0] - 1)
                _okb = ok.copy()
                ok &= ~_em[_eyi, _exi]
                _ego_rej |= (_okb & ~ok)
            # poisoned: projection lands inside this camera's MOVING-object mask (matched instances only)
            pis = np.zeros(len(Xf), bool)
            sel = np.nonzero(ok)[0]
            if sel.size and poison_masks[ci].any():
                xi = np.clip(px[sel].astype(np.int64), 0, ww - 1); yi = np.clip(py[sel].astype(np.int64), 0, hh - 1)
                pis[sel] = poison_masks[ci][yi, xi]
            cvec = tc - C; df = DIRS.reshape(-1, 3); along = df @ cvec
            bperp = np.sqrt(np.maximum(float(cvec @ cvec) - along * along, 0.0)).astype(np.float32)
            proj.append({"px": px, "py": py, "ok": ok, "poison": pis, "bperp": bperp})
    # RULE 2 source choice: valid = ok & ~poison; pick min bperp; record needs_fill where none
    bestscore = np.full(len(Xf), np.inf, np.float32)
    bestcam = np.full(len(Xf), -1, np.int8)
    for ci in range(len(ring_cams)):
        p = proj[ci]
        sc = np.where(p["ok"] & ~p["poison"], p["bperp"], np.inf)
        upd = sc < bestscore
        bestscore[upd] = sc[upd]; bestcam[upd] = ci
    needs_fill = (bestcam < 0)
    # also keep a fallback cam (ok, even if poisoned) for pixels nothing can see
    fbscore = np.full(len(Xf), np.inf, np.float32)
    fbcam = np.full(len(Xf), -1, np.int8)
    for ci in range(len(ring_cams)):
        p = proj[ci]
        sc = np.where(p["ok"], p["bperp"], np.inf)
        upd = sc < fbscore
        fbscore[upd] = sc[upd]; fbcam[upd] = ci
    # RULE 1: object-body rays — evidence UNION (mask reprojection OR own-time box hit),
    # single camera, uniform object distance. Plus collect the GHOST ZONE (this object's
    # position at every camera's exposure time).
    body_cam = np.full(len(Xf), -1, np.int8)
    body_px = np.zeros(len(Xf), np.float32); body_py = np.zeros(len(Xf), np.float32)
    ghost_zone = np.zeros(len(Xf), bool)
    n_secondary = 0
    omc = []   # per (object, camera-pair) measured shutter displacement
    morph_jobs = []   # straddle objects: (ci_own, ci_sec, d_own, d_sec, shift, body_flat)
    df = DIRS.reshape(-1, 3)
    from scipy.ndimage import binary_fill_holes
    def close_region(flat_bool):
        """Topological closure (parameter-free, boundary-preserving): a hole strictly
        enclosed by an object's silhouette at uniform distance IS the object — YOLO
        masks lose thin dark structures (A-pillars) and the ghost ledger would
        otherwise temporally fill real background INTO the car. Roll by the region's
        circular mean to respect the ERP u-wrap before hole-filling."""
        ib2 = flat_bool.reshape(H, W)
        cols = np.nonzero(ib2.any(0))[0]
        if cols.size == 0: return flat_bool
        ang = cols / W * 2 * np.pi
        cmean = math.atan2(float(np.sin(ang).mean()), float(np.cos(ang).mean())) % (2 * np.pi)
        shift = W // 2 - int(round(cmean / (2 * np.pi) * W))
        # DB115-PRO fix#4: scipy binary_fill_holes (2s/frame over ~17 calls) -> cv2
        # floodFill from a guaranteed-outside padded corner; unreached background =
        # enclosed holes (same 4-connectivity definition).
        r_ = np.roll(ib2, shift, axis=1)
        invp = np.pad((~r_).astype(np.uint8), 1, constant_values=1)  # pad ring IS background
        _ffm = np.zeros((invp.shape[0] + 2, invp.shape[1] + 2), np.uint8)
        import cv2 as _cvf
        _cvf.floodFill(invp, _ffm, (0, 0), 2)                        # flood reachable background
        ib2 = r_ | (invp[1:-1, 1:-1] == 1)                           # unreached background = holes
        return np.roll(ib2, -shift, axis=1).reshape(-1)
    import cv2 as _cv
    gimgs = [np.clip(frame.images[cam].astype(np.float32) * np.exp(gains[ci_]).astype(np.float32)[None, None, :], 0, 255).astype(np.uint8)
             for ci_, cam in enumerate(ring_cams)]
    color_diag_report = None
    if COLOR_DIAG:
        _palette = np.asarray([[230, 75, 75], [60, 180, 75], [255, 225, 25],
                               [0, 130, 200], [245, 130, 48], [145, 30, 180],
                               [70, 240, 240], [240, 50, 230]], np.uint8)
        _terr = np.zeros((H * W, 3), np.uint8)
        for _ci in range(len(ring_cams)):
            _terr[bestcam == _ci] = _palette[_ci % len(_palette)]
        save_rgb(REMOTE_OUT / f"{run_name}_territory.png", _terr.reshape(H, W, 3))
        _pair_reports = []
        _sample_arrays = {}
        _depth_flat = Zd.reshape(-1)
        for _pair_number, ((_ci, _cj), _idx0) in enumerate(
                ownership_boundary_indices(bestcam.reshape(H, W)).items()):
            _prefix = f"pair_{_pair_number:03d}"
            _boundary_n = int(len(_idx0))
            if len(_idx0) > 50000:
                _idx0 = _idx0[np.linspace(0, len(_idx0) - 1, 50000, dtype=np.int64)]
            _pi, _pj = proj[_ci], proj[_cj]
            _geometry_valid = _pi["ok"][_idx0] & _pj["ok"][_idx0]
            _unpoisoned = (_geometry_valid & ~_pi["poison"][_idx0] &
                           ~_pj["poison"][_idx0])
            _idx_all = _idx0[_unpoisoned]
            _raw_i_all = bilinear(frame.images[ring_cams[_ci]],
                                  _pi["px"][_idx_all], _pi["py"][_idx_all])
            _raw_j_all = bilinear(frame.images[ring_cams[_cj]],
                                  _pj["px"][_idx_all], _pj["py"][_idx_all])
            _hi, _wi = cals[_ci][1]; _hj, _wj = cals[_cj][1]
            _xy_i_all = np.column_stack([_pi["px"][_idx_all] / max(_wi - 1, 1),
                                         _pi["py"][_idx_all] / max(_hi - 1, 1)])
            _xy_j_all = np.column_stack([_pj["px"][_idx_all] / max(_wj - 1, 1),
                                         _pj["py"][_idx_all] / max(_hj - 1, 1)])
            _points = Xf[_idx_all]
            _view_i = _points - poses_emc[_ci][1][None, :]
            _view_j = _points - poses_emc[_cj][1][None, :]
            _view_den = np.maximum(
                np.linalg.norm(_view_i, axis=1) * np.linalg.norm(_view_j, axis=1), 1e-12)
            _view_cos = np.sum(_view_i * _view_j, axis=1) / _view_den
            _parallax_deg = np.degrees(np.arccos(np.clip(_view_cos, -1.0, 1.0)))
            _samples = collect_pair_samples(
                rgb_a=_raw_i_all, rgb_b=_raw_j_all, erp_flat_index=_idx_all,
                xy_a=_xy_i_all, xy_b=_xy_j_all, depth_m=_depth_flat[_idx_all],
                parallax_deg=_parallax_deg)
            _sample_arrays[_prefix + "__rgb_a"] = _samples.rgb_a
            _sample_arrays[_prefix + "__rgb_b"] = _samples.rgb_b
            _sample_arrays[_prefix + "__erp_flat_index"] = _samples.erp_flat_index
            _sample_arrays[_prefix + "__xy_a"] = _samples.xy_a
            _sample_arrays[_prefix + "__xy_b"] = _samples.xy_b
            _sample_arrays[_prefix + "__depth_m"] = _samples.depth_m
            _sample_arrays[_prefix + "__parallax_deg"] = _samples.parallax_deg

            _gain_i = np.asarray(gains[_ci], dtype=np.float64)
            _gain_j = np.asarray(gains[_cj], dtype=np.float64)
            if (_gain_i.shape != (3,) or _gain_j.shape != (3,) or
                    not np.isfinite(_gain_i).all() or not np.isfinite(_gain_j).all() or
                    not np.allclose(_gain_i, _gain_i[0], rtol=0.0, atol=1e-12) or
                    not np.allclose(_gain_j, _gain_j[0], rtol=0.0, atol=1e-12)):
                raise ValueError("COLOR_DIAG fixed profile requires one finite scalar RGB gain")
            _fixed_profile = fixed_brightness_profile(
                _samples, gain_log_a=float(_gain_i[0]), gain_log_b=float(_gain_j[0]),
                sat_lo=float(SAT_LO), sat_hi=float(SAT_HI))
            _unsat = ((_raw_i_all.min(1) > SAT_LO) & (_raw_i_all.max(1) < SAT_HI) &
                      (_raw_j_all.min(1) > SAT_LO) & (_raw_j_all.max(1) < SAT_HI))
            _stats = {}
            if int(_unsat.sum()) >= 32:
                _raw_i = _raw_i_all[_unsat]; _raw_j = _raw_j_all[_unsat]
                _xy_i = _xy_i_all[_unsat]; _xy_j = _xy_j_all[_unsat]
                _stats = photometric_pair_residual_stats(
                    _raw_i, _raw_j, gains[_ci], gains[_cj], xy_a=_xy_i, xy_b=_xy_j)
            _stats.update({"sample_prefix": _prefix,
                           "camera_pair": [ring_cams[_ci], ring_cams[_cj]],
                           "boundary_n": _boundary_n,
                           "geometry_valid_n": int(_geometry_valid.sum()),
                           "unpoisoned_n": int(_unpoisoned.sum()),
                           "unsaturated_n": int(_unsat.sum()),
                           "emitted_n": int(len(_samples.rgb_a)),
                           "fixed_brightness_profile": _fixed_profile,
                           "boundary_pixels": _boundary_n,
                           "same_point_valid_before_saturation": int(_unpoisoned.sum())})
            _pair_reports.append(_stats)
        _sample_path = REMOTE_OUT / f"{run_name}_color_diag_samples.npz"
        np.savez_compressed(_sample_path, **_sample_arrays)
        _sample_sha256 = hashlib.sha256(_sample_path.read_bytes()).hexdigest()
        color_diag_report = {
            "schema_version": RAW_PAIR_SCHEMA_VERSION,
            "measurement": "same_3d_ray_at_curved_ownership_boundary",
            "dataset": "av2", "log_id": log_dir.name,
            "anchor_index": int(anchor_idx), "anchor_timestamp_ns": int(ts),
            "camera_order": list(ring_cams),
            "luma_definition": "mean_rgb_code_value",
            "input_encoding": "av2_jpeg_rgb_uint8_bilinear_float64",
            "gain_applied_to_npz": False,
            "sat_lo": float(SAT_LO), "sat_hi": float(SAT_HI),
            "max_samples_per_pair": 50000,
            "sampling": "deterministic_linspace",
            "sample_npz": _sample_path.name, "sample_sha256": _sample_sha256,
            "render_gain_log_rgb": gains.tolist(), "pairs": _pair_reports}
        (REMOTE_OUT / f"{run_name}_color_diag.json").write_text(
            json.dumps(color_diag_report, indent=1), encoding="utf-8")

    def sample_cam_patch(ci_s, dist_s, rows_s, cols_s, shift=(0, 0)):
        """ERP patch (rows x cols grid at uniform distance) rendered from one camera.
        shift=(dv,du): sample the source at y-shift (OMC: content appears moved +shift)."""
        rr, cc = np.meshgrid(rows_s, cols_s, indexing="ij")
        rr2 = np.clip(rr - shift[0], 0, H - 1)
        cc2 = (cc - shift[1]) % W
        dirs_p = DIRS[rr2, cc2]
        Xp = (C[None, None, :] + dist_s * dirs_p).reshape(-1, 3)
        K_, (hh_, ww_) = cals[ci_s]
        Rc_, tc_ = poses_emc[ci_s]
        Xc_ = (Xp - tc_[None, :]) @ Rc_
        z_ = Xc_[:, 2]
        px_ = (K_[0, 0] * Xc_[:, 0] / np.maximum(z_, 1e-6) + K_[0, 2]).astype(np.float32)
        py_ = (K_[1, 1] * Xc_[:, 1] / np.maximum(z_, 1e-6) + K_[1, 2]).astype(np.float32)
        valid = (z_ > 0.1) & (px_ >= 1) & (px_ < ww_ - 1) & (py_ >= 1) & (py_ < hh_ - 1)
        col = np.zeros((len(z_), 3), np.float32)
        if valid.any():
            col[valid] = bilinear(gimgs[ci_s], px_[valid], py_[valid]).astype(np.float32)
        return col.reshape(rr.shape + (3,)), valid.reshape(rr.shape)
    if _bt:
        _df_t = _th.as_tensor(df, dtype=_th.float64, device="cuda")
        _C_t = _th.as_tensor(C, dtype=_th.float64, device="cuda")
    def _obj_proj(ci_o, dist_o):
        # DB115-PRO fix#5 phase B: per-object full-pano projection (2M x 3, 5-8x per
        # frame) — same math on GPU when BAND_TORCH, numpy otherwise.
        K_o, (hh_o, ww_o) = cals[ci_o]; Rc_o, tc_o = poses_emc[ci_o]
        if _bt:
            Xo_t = _C_t[None, :] + dist_o * _df_t
            Xc_t = (Xo_t - _th.as_tensor(tc_o, dtype=_th.float64, device="cuda")[None, :]) \
                @ _th.as_tensor(Rc_o, dtype=_th.float64, device="cuda")
            z_t = Xc_t[:, 2]
            zc_t = _th.clamp(z_t, min=1e-6)
            px_t = (K_o[0, 0] * Xc_t[:, 0] / zc_t + K_o[0, 2]).float()
            py_t = (K_o[1, 1] * Xc_t[:, 1] / zc_t + K_o[1, 2]).float()
            ok_t = (z_t > 0.1) & (px_t >= 1) & (px_t < ww_o - 1) & (py_t >= 1) & (py_t < hh_o - 1)
            return px_t.cpu().numpy(), py_t.cpu().numpy(), ok_t.cpu().numpy(), z_t.cpu().numpy()
        Xobj_ = C[None, :] + dist_o * df
        Xc_ = (Rc_o.T @ (Xobj_ - tc_o[None, :]).T).T
        z_ = Xc_[:, 2]
        px_ = (K_o[0, 0] * Xc_[:, 0] / np.maximum(z_, 1e-6) + K_o[0, 2]).astype(np.float32)
        py_ = (K_o[1, 1] * Xc_[:, 1] / np.maximum(z_, 1e-6) + K_o[1, 2]).astype(np.float32)
        ok_ = (z_ > 0.1) & (px_ >= 1) & (px_ < ww_o - 1) & (py_ >= 1) & (py_ < hh_o - 1)
        return px_, py_, ok_, z_
    for ob in objects:
        ci = ob["ci"]; K, (hh, ww) = cals[ci]; Rc, tc = poses_emc[ci]
        px, py, ok, z = _obj_proj(ci, ob["dist"])
        sel = np.nonzero(ok)[0]
        xi = np.clip(px[sel].astype(np.int64), 0, ww - 1); yi = np.clip(py[sel].astype(np.int64), 0, hh - 1)
        inbody = np.zeros(len(Xf), bool)
        inbody[sel] = ob["mask"][yi, xi]
        inbody = close_region(inbody) & ok   # & ok: hole pixels must still project into c_own
        body_cam[inbody] = ci
        body_px[inbody] = px[inbody]; body_py[inbody] = py[inbody]
        # SECONDARY BODY with OBJECT-MOTION SHUTTER COMPENSATION (OMC) + GHOST LEDGER.
        # Each camera's mask covers only the PART of a boundary-straddling object it
        # sees, AND imaged it at a DIFFERENT exposure time — naively butting the two
        # halves tears the object open by object_speed * dt at the FOV boundary.
        # OMC is the object-side symmetric piece to DB-86's EMC: measure the object's
        # ERP displacement between the two exposures FROM THE MASKS THEMSELVES (the
        # overlap strip both cameras see images the same physical part twice), shift
        # the secondary camera's contribution to c_own's exposure-time position, THEN
        # composite. The secondary camera's UNSHIFTED copy becomes ghost (-> temporal
        # background recovery). Zero scene parameters: the shift is measured per
        # object per camera pair from image evidence alone.
        # own_cover = where c_own's evidence is AUTHORITATIVE. Negative evidence
        # (absence of mask = "background here") is unreliable within the ragged border
        # margin of c_own's own image (seen: a truncated mask starting at x=4 left a
        # 4-column "background" strip at the FOV edge that temporal fill painted with
        # the real background INSIDE the car). Positive evidence (inbody) keeps the
        # full 1-px bounds; the authority region shrinks by the same 1% margin as the
        # completeness test.
        mgy2, mgx2 = max(4, hh // 100), max(4, ww // 100)
        own_cover = (z > 0.1) & (px >= mgx2) & (px < ww - mgx2) & (py >= mgy2) & (py < hh - mgy2)
        obj_body = inbody.copy()
        best_sec = None   # (n_px, ci2, d2, (dv,du))
        others = [c2 for c2 in ob.get("per_cam_mask", {}) if c2 != ci]
        others.sort(key=lambda c2: abs(cam_ts[ring_cams[c2]] - cam_ts[ring_cams[ci]]))
        for ci2 in others:
            m2, d2 = ob["per_cam_mask"][ci2]
            K2, (hh2, ww2) = cals[ci2]; Rc2, tc2 = poses_emc[ci2]
            px2, py2, ok2, z2 = _obj_proj(ci2, d2)
            s2 = np.nonzero(ok2)[0]
            xi2 = np.clip(px2[s2].astype(np.int64), 0, ww2 - 1); yi2 = np.clip(py2[s2].astype(np.int64), 0, hh2 - 1)
            rep2f = np.zeros(len(Xf), bool)
            rep2f[s2[m2[yi2, xi2]]] = True   # where THIS camera's copy of the object sits in the ERP
            # the DONOR's positive evidence inside its own border margin is rectification
            # junk (black border columns get pulled in by mask raggedness) — same 1%
            # border rule as c_own's negative evidence, applied symmetrically.
            mgy2c, mgx2c = max(4, hh2 // 100), max(4, ww2 // 100)
            ok2m = (z2 > 0.1) & (px2 >= mgx2c) & (px2 < ww2 - mgx2c) & (py2 >= mgy2c) & (py2 < hh2 - mgy2c)
            rep2b = close_region(rep2f) & ok2m
            # OMC shift estimate: binary alignment of the two masks inside the overlap strip
            strip = own_cover & ok2
            A2 = (inbody & strip).reshape(H, W); B2 = (rep2b & strip).reshape(H, W)
            du_best, dv_best, sc_best, ncc_best = 0, 0, -1.0, -9.0
            if min(int(A2.sum()), int(B2.sum())) >= 50:   # evidence-sufficiency gate
                yy, xx = np.nonzero(A2 | B2)
                y0c = max(0, int(yy.min()) - 12); y1c = min(H, int(yy.max()) + 13)
                x0c = max(0, int(xx.min()) - 70); x1c = min(W, int(xx.max()) + 71)
                Ac = A2[y0c:y1c, x0c:x1c]; Bc = B2[y0c:y1c, x0c:x1c]
                for dv2 in range(-8, 9, 2):
                    for du2 in range(-60, 61, 2):
                        Bs = np.roll(np.roll(Bc, dv2, axis=0), du2, axis=1)
                        sc = float((Ac & Bs).sum()) / max(float((Ac | Bs).sum()), 1.0)
                        if sc > sc_best: sc_best, du_best, dv_best = sc, du2, dv2
                for dv2 in range(dv_best - 1, dv_best + 2):
                    for du2 in range(du_best - 1, du_best + 2):
                        Bs = np.roll(np.roll(Bc, dv2, axis=0), du2, axis=1)
                        sc = float((Ac & Bs).sum()) / max(float((Ac | Bs).sum()), 1.0)
                        if sc > sc_best: sc_best, du_best, dv_best = sc, du2, dv2
                # ECC IMAGE refinement: mask-IoU is blind to ~15 px shifts (coarse ragged
                # silhouettes barely change IoU), while the physics budget says
                # object_speed * dt / dist is exactly that order (17.7 m/s * 35 ms at
                # 13 m ~ 15 px). Estimate pure translation on the RENDERED GRAYS inside
                # the masks; arbitrate candidates (coarse, +ecc, -ecc) by masked NCC so
                # a sign mistake cannot slip through.
                rows_e = np.arange(y0c, y1c); cols_e = np.arange(x0c, x1c)
                Ae, Aev = sample_cam_patch(ci, ob["dist"], rows_e, cols_e)
                Be, Bev = sample_cam_patch(ci2, d2, rows_e, cols_e)
                gAe = Ae.mean(2).astype(np.float32) / 255.0
                gBe = Be.mean(2).astype(np.float32) / 255.0
                def ncc_at(du_c, dv_c):
                    Bs2 = np.roll(np.roll(gBe, dv_c, 0), du_c, 1)
                    Ms2 = np.roll(np.roll(Bc & Bev, dv_c, 0), du_c, 1) & Ac & Aev
                    if Ms2.sum() < 50: return -2.0
                    a_ = gAe[Ms2] - gAe[Ms2].mean(); b_ = Bs2[Ms2] - Bs2[Ms2].mean()
                    return float((a_ * b_).sum() / max(np.sqrt((a_ * a_).sum() * (b_ * b_).sum()), 1e-9))
                cand = [(du_best, dv_best)]
                Mt = np.eye(2, 3, dtype=np.float32); Mt[0, 2] = -du_best; Mt[1, 2] = -dv_best
                try:
                    _cce, Mt = _cv.findTransformECC(gAe, gBe, Mt, _cv.MOTION_TRANSLATION,
                                                    (_cv.TERM_CRITERIA_EPS | _cv.TERM_CRITERIA_COUNT, 80, 1e-5),
                                                    ((Ac | Bc) & Aev & Bev).astype(np.uint8) * 255, 3)
                    cand += [(int(round(-Mt[0, 2])), int(round(-Mt[1, 2]))),
                             (int(round(Mt[0, 2])), int(round(Mt[1, 2])))]
                except _cv.error:
                    pass
                scored = sorted(((ncc_at(dc, vc), dc, vc) for dc, vc in cand), reverse=True)
                ncc_best, du_best, dv_best = scored[0]
            omc.append({"cam_pair": [ring_cams[ci], ring_cams[ci2]], "du": int(du_best),
                        "dv": int(dv_best), "score": round(sc_best, 3),
                        "ncc": round(float(ncc_best) if isinstance(ncc_best, float) else -9.0, 3)})
            # secondary body = the OMC-shifted copy, wherever no body evidence exists yet.
            # POSITIVE evidence (the NCC-verified shifted copy says car) outranks
            # NEGATIVE evidence (c_own's mask gap says background) — same hierarchy as
            # the border-margin rule; without it a shift opens a sliver between the
            # halves that temporal fill paints with true (dark) background.
            shifted = np.roll(np.roll(rep2b.reshape(H, W), dv_best, axis=0), du_best, axis=1).reshape(-1)
            ti = np.nonzero(shifted & (body_cam < 0))[0]
            sv = ti // W - dv_best; su = (ti % W - du_best) % W
            keep = (sv >= 0) & (sv < H)
            ti = ti[keep]; si = sv[keep] * W + su[keep]
            if ti.size:
                body_cam[ti] = ci2
                body_px[ti] = px2[si]; body_py[ti] = py2[si]
                n_secondary += int(ti.size)
                obj_body[ti] = True
                if best_sec is None or ti.size > best_sec[0]:
                    best_sec = (int(ti.size), ci2, d2, (dv_best, du_best))
            # ghost: this camera's UNSHIFTED copy anywhere not claimed as body — but a
            # ghost only EXISTS when the measured displacement exceeds the measurement
            # quantisation (~2 px): at zero displacement the "displaced copy" IS the
            # body, and rep2-minus-body is pure mask-edge noise (filling it paints
            # shadowless road over the contact shadow). Anchor-time cameras already
            # render those pixels correctly.
            if abs(du_best) > 2 or abs(dv_best) > 2:
                ghost_zone |= rep2b & (body_cam < 0)
        # Temporal recovery must NEVER target the INTERIOR of a solid object's
        # silhouette closure (mask notches between the halves would get painted with
        # true dark background INSIDE the car) — interior holes fall back to RULE 2/EMC.
        ghost_zone &= ~close_region(obj_body)
        if best_sec is not None and best_sec[0] >= 50:
            sec_area = int(ob["per_cam_mask"].get(best_sec[1], (np.zeros((1, 1), bool),))[0].sum())
            if SEAM_SINGLE_SOURCE and sec_area < 0.4 * max(ob.get("own_area", 1), 1):
                pass   # DB-105: secondary is a grazing sliver -> KEEP its disocclusion fill (the leading edge c_own genuinely can't see) but SKIP the morph. Fusing a complete c_own body with a sliver IS the shear; no fusion -> no shear, and the sliver still patches the few px c_own lacks.
            else:
                morph_jobs.append((ci, best_sec[1], ob["dist"], best_sec[2], best_sec[3], obj_body))
    # assemble image
    out = np.zeros((len(Xf), 3), np.uint8)
    for ci, cam in enumerate(ring_cams):
        img = frame.images[cam]
        gimg = np.clip(img.astype(np.float32) * np.exp(gains[ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
        p = proj[ci]
        sel = np.nonzero((bestcam == ci) & (body_cam < 0))[0]
        if sel.size:
            out[sel] = np.clip(bilinear(gimg, p["px"][sel], p["py"][sel]), 0, 255).astype(np.uint8)
        selb = np.nonzero(body_cam == ci)[0]
        if selb.size:
            out[selb] = np.clip(bilinear(gimg, body_px[selb], body_py[selb]), 0, 255).astype(np.uint8)
        self_fb = np.nonzero(needs_fill & (fbcam == ci) & (body_cam < 0))[0]
        if self_fb.size:
            out[self_fb] = np.clip(bilinear(gimg, p["px"][self_fb], p["py"][self_fb]), 0, 255).astype(np.uint8)
    # GHOST-ZONE TEMPORAL RECOVERY (rule 3): the other cameras' displaced copies live in
    # ghost_zone \ body — recover the occluded background from time. Fill is gated on
    # LiDAR-evidenced background depth; everything else falls back to EMC (rule 4).
    n_filled = 0
    sup_ok = (Zsupport.reshape(-1) <= 4.0)
    # TEMPORAL RECOVERY IS THE LAST RESORT: only where NO camera cleanly sees the
    # background at anchor time (needs_fill = all views poisoned, true mutual
    # disocclusion). Where a clean camera exists, RULE 2 already renders the true
    # anchor-time background (e.g. the shadowed road under the car — a fill from
    # another TIME paints shadowless road and breaks the contact shadow).
    zone_flat = np.nonzero(ghost_zone & needs_fill & (body_cam < 0) & sup_ok)[0]
    leftover = np.nonzero(needs_fill & (body_cam < 0) & ~(ghost_zone & sup_ok))[0]
    if leftover.size:
        for ci, cam in enumerate(ring_cams):
            img = frame.images[cam]
            gimg = np.clip(img.astype(np.float32) * np.exp(gains[ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
            p = proj[ci]
            sel = leftover[(fbcam[leftover] == ci)]
            if sel.size:
                out[sel] = np.clip(bilinear(gimg, p["px"][sel], p["py"][sel]), 0, 255).astype(np.uint8)
    if zone_flat.size:
        zdirs = df[zone_flat]
        Zv = Zd.reshape(-1)[zone_flat].astype(np.float64)
        Xz = C[None, :] + Zv[:, None] * zdirs
        X_city = (Ra @ Xz.T).T + ta
        ai = int(anchor_idx)
        # TEMPORAL CONSENSUS: keep the 3 best independent (frame, camera) sources per
        # pixel and fill with their per-channel MEDIAN. The sightline gate tests
        # LAGGED boxes (the dataset's ~0.2 s annotation lag), so a single source can
        # leak a moving object (the user's green protrusion above the Porsche roof);
        # an outlier among 3 independent times is voted out. Zero thresholds.
        chosen = np.full((3, zone_flat.size), -1, np.int32)
        chosen_bp = np.full((3, zone_flat.size), np.inf)
        def seg_blocked2(o, Xq, boxes_q):
            outb = np.zeros(len(Xq), bool)
            for c2, sz2, R2 in boxes_q:
                half2 = sz2 / 2 * 1.05
                o_loc = R2.T @ (o - c2)
                d_loc = (Xq - o[None, :]) @ R2
                with np.errstate(divide="ignore", invalid="ignore"):
                    inv = 1.0 / d_loc
                    t1 = (-half2[None, :] - o_loc[None, :]) * inv
                    t2 = (half2[None, :] - o_loc[None, :]) * inv
                tmin = np.nanmax(np.minimum(t1, t2), axis=1)
                tmax = np.nanmin(np.maximum(t1, t2), axis=1)
                outb |= (tmax >= np.maximum(tmin, 0.0)) & (tmin < 0.97) & (tmin > 0.02)
            return outb
        for fi in range(max(0, ai - 10), min(len(all_ts) - 1, ai + 10) + 1):
            if abs(fi - ai) < 3: continue   # gate: object provably departed
            tsf = int(all_ts[fi])
            Rf, tf = cte(tsf)
            Xq = (X_city - tf[None, :]) @ Rf
            fboxes = [(c2, sz2 * 1.3, R2) for (c2, sz2, R2) in boxes_at(ann, tsf, moving)]   # padded
            for ci2, cam in enumerate(ring_cams):
                K2, (hh2, ww2) = cals[ci2]
                T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float)
                Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2)
                if not okq.any(): continue
                blocked = np.zeros(zone_flat.size, bool)
                blocked[okq] = seg_blocked2(T2[:3, 3], Xq[okq], fboxes)
                visq = okq & ~blocked
                if not visq.any(): continue
                cam_city = Rf @ T2[:3, 3] + tf
                cam_anchor = Ra.T @ (cam_city - ta)
                cvec2 = cam_anchor - C
                along2 = zdirs @ cvec2
                bp2 = np.sqrt(np.maximum(float(cvec2 @ cvec2) - along2 * along2, 0.0))
                code_new = fi * 10 + ci2
                c0 = visq & (bp2 < chosen_bp[0])
                c1 = visq & ~c0 & (bp2 < chosen_bp[1])
                c2 = visq & ~c0 & ~c1 & (bp2 < chosen_bp[2])
                # slot insertion (best three by b_perp)
                chosen[2][c0] = chosen[1][c0]; chosen_bp[2][c0] = chosen_bp[1][c0]
                chosen[1][c0] = chosen[0][c0]; chosen_bp[1][c0] = chosen_bp[0][c0]
                chosen[0][c0] = code_new; chosen_bp[0][c0] = bp2[c0]
                chosen[2][c1] = chosen[1][c1]; chosen_bp[2][c1] = chosen_bp[1][c1]
                chosen[1][c1] = code_new; chosen_bp[1][c1] = bp2[c1]
                chosen[2][c2] = code_new; chosen_bp[2][c2] = bp2[c2]
        colbuf = np.full((3, zone_flat.size, 3), np.nan, np.float32)
        frame_cache = {}
        for slot in range(3):
            for code in np.unique(chosen[slot][chosen[slot] >= 0]):
                fi, ci2 = int(code) // 10, int(code) % 10
                sel = chosen[slot] == code
                if int(all_ts[fi]) not in frame_cache:
                    frame_cache[int(all_ts[fi])] = loader.load_synced_frame(int(all_ts[fi]))
                fr2 = frame_cache[int(all_ts[fi])]
                Rf, tf = cte(int(all_ts[fi]))
                Xq = (X_city[sel] - tf[None, :]) @ Rf
                K2, _s2 = cals[ci2]
                T2 = np.asarray(frame.calibrations[ring_cams[ci2]].T_ego_cam, float)
                Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                img2 = fr2.images[ring_cams[ci2]]
                g2 = np.exp(gains[ci2])[None, :]
                colbuf[slot][sel] = np.clip(bilinear(img2, px2, py2) * g2, 0, 255).astype(np.float32)
        have = ~np.isnan(colbuf[:, :, 0])
        anyv = have.any(0)
        if anyv.any():
            pre_fill = out[zone_flat].copy()
            med = np.nanmedian(colbuf[:, anyv], axis=0)
            out[zone_flat[anyv]] = np.clip(med, 0, 255).astype(np.uint8)
            n_filled += int(anyv.sum())
            # NEIGHBOURHOOD-CONSISTENCY ABSTAIN (rule 8 for time): specular content is
            # view-dependent — a fill sourced from one camera's future frames can be
            # "true background" yet clash with the surrounding render from another
            # viewpoint (the green reflection blob beside the Porsche nose). A filled
            # blob whose colour departs from its surrounding ring by far more than the
            # ring's own spread is unverifiable -> abstain back to the EMC pixel.
            from scipy.ndimage import label as _lbl, binary_dilation as _bdl, distance_transform_edt as _edt2
            ffm = np.zeros(len(Xf), bool); ffm[zone_flat[anyv]] = True
            ffm2 = ffm.reshape(H, W)
            labarr, nlab = _lbl(ffm2)
            out2v = out.reshape(H, W, 3)
            n_abstained = 0
            for li in range(1, nlab + 1):
                blob = labarr == li
                ring = _bdl(blob, iterations=4) & ~ffm2 & (body_cam.reshape(H, W) < 0)
                if int(ring.sum()) < 30: continue
                bpx = out2v[blob].astype(np.float32)
                rpx = out2v[ring].astype(np.float32)
                bmed = np.median(bpx, 0); rmed = np.median(rpx, 0)
                rmad = np.median(np.abs(rpx - rmed[None, :]), 0).mean() + 4.0
                if float(np.abs(bmed - rmed).mean()) > 5.0 * rmad:
                    # content of a different CLASS entirely -> abstain to the EMC pixel
                    sel_b = blob.reshape(-1)[zone_flat]
                    out[zone_flat[sel_b]] = pre_fill[sel_b]
                    n_abstained += int(sel_b.sum())
                    continue
                # PHOTOMETRIC ALIGNMENT (seamless-cloning lite, Perez'03): the fill is
                # content from another time/viewpoint — geometrically right but lit
                # differently (and the object's cast shadow, absent from all evidence,
                # falls on this band at anchor time). Match the blob's per-channel
                # colour statistics to its surrounding ring so structure is kept and
                # the ambient light (incl. shadow) transfers. Zero scene parameters.
                # PER-PIXEL boundary-driven offset (harmonic-lite Poisson approx):
                # each fill pixel takes the photometric offset of its NEAREST ring
                # pixels, smoothed. Global blob shifts cannot serve heterogeneous
                # rings (shadow on one side, sunlit kerb on the other): locally, the
                # under-car pixels inherit the shadow falloff, the outer pixels stay
                # sunlit, and wherever the fill already matches its surroundings the
                # offset is ~0 so no foreign tint is introduced.
                ys_b, xs_b = np.nonzero(blob)
                y0b, y1b = max(0, int(ys_b.min()) - 12), min(H, int(ys_b.max()) + 13)
                x0b, x1b = max(0, int(xs_b.min()) - 12), min(W, int(xs_b.max()) + 13)
                bl = blob[y0b:y1b, x0b:x1b]; rg = ring[y0b:y1b, x0b:x1b]
                patch = out2v[y0b:y1b, x0b:x1b].astype(np.float32)
                if rg.any() and bl.any():
                    _di, idx_in = _edt2(~bl, return_distances=True, return_indices=True)
                    offs = np.zeros(bl.shape + (3,), np.float32)
                    offs[rg] = patch[rg] - patch[idx_in[0][rg], idx_in[1][rg]]
                    _dr, idx_rg = _edt2(~rg, return_distances=True, return_indices=True)
                    field = _cv.GaussianBlur(offs[idx_rg[0], idx_rg[1]], (0, 0), 3.0)
                    patch[bl] = np.clip(patch[bl] + field[bl], 0, 255)
                    out2v[y0b:y1b, x0b:x1b] = patch.astype(np.uint8)
            n_filled -= n_abstained
    # ---- STAGE 3.5: VIEW-MORPH the straddle seam (Surround360/Megastereo-style) ----
    # A hard butt-joint between two cameras' halves of one object leaves a 1-2 px
    # registration step + a photometric step that the eye integrates as DOUBLING
    # (user-confirmed at 16x: roofline/sill/shoulder lines all step at the seam).
    # Selection answers WHO/WHERE (evidence calculus); interpolation answers HOW to
    # transition: ECC-affine registration (rigid object, small view change) + an
    # alpha-ramp Beier-Neely morph across the evidence-bounded overlap strip.
    out2 = out.reshape(H, W, 3)
    morph_report = []
    for ci_o, ci_s, d_o, d_s, shift_s, body_flat in morph_jobs:
        m2d = body_flat.reshape(H, W)
        rows_any = np.nonzero(m2d.any(1))[0]; cols_any = np.nonzero(m2d.any(0))[0]
        if rows_any.size == 0 or cols_any.size > W // 2: continue   # skip wrap/degenerate
        v0o, v1o = int(rows_any.min()), int(rows_any.max())
        u0o, u1o = int(cols_any.min()), int(cols_any.max())
        rows_p = np.arange(max(0, v0o - 8), min(H, v1o + 9))
        cols_p = np.arange(u0o - 8, u1o + 9)   # may exceed [0,W); helper wraps
        A_patch, A_val = sample_cam_patch(ci_o, d_o, rows_p, cols_p)
        B_patch, B_val = sample_cam_patch(ci_s, d_s, rows_p, cols_p, shift_s)
        # invalid patch pixels are literal zeros — bilinear remap across the validity
        # edge would bleed BLACK into the blend. Cross-fill so black never exists.
        A_patch[~A_val] = B_patch[~A_val]
        B_patch[~B_val] = A_patch[~B_val]
        body_p = m2d[np.ix_(rows_p, cols_p % W)]
        # overlap strip: columns where BOTH cameras cover the object's rows
        colA = (A_val & body_p).sum(0); colB = (B_val & body_p).sum(0); colN = body_p.sum(0)
        both = (colN > 0) & (colA >= 0.9 * colN) & (colB >= 0.9 * colN)
        bi = np.nonzero(both)[0]
        if bi.size < 4: continue
        # B-pure end = the side where A loses coverage beyond the strip
        left_A = colA[:bi[0]].sum(); right_A = colA[bi[-1] + 1:].sum()
        b_side_left = left_A <= right_A
        # clamp strip to 32 cols hugging the B side
        if bi.size > 32: bi = bi[:32] if b_side_left else bi[-32:]
        # ECC affine registration B->A on the strip (gray, masked)
        gA = _cv.cvtColor(A_patch.astype(np.uint8), _cv.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        gB = _cv.cvtColor(B_patch.astype(np.uint8), _cv.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        mask_ecc = np.zeros(body_p.shape, np.uint8)
        mask_ecc[:, bi] = (body_p[:, bi] & A_val[:, bi] & B_val[:, bi]).astype(np.uint8) * 255
        M = np.eye(2, 3, dtype=np.float32)
        cc_ecc = 0.0
        try:
            cc_ecc, M = _cv.findTransformECC(gA, gB, M, _cv.MOTION_AFFINE,
                                             (_cv.TERM_CRITERIA_EPS | _cv.TERM_CRITERIA_COUNT, 60, 1e-5),
                                             mask_ecc, 3)
        except _cv.error:
            M = np.eye(2, 3, dtype=np.float32)   # fallback: pure cross-fade
        # alpha ramp across the strip (1.0 at the B-pure end)
        alpha_col = np.zeros(body_p.shape[1], np.float32)
        ramp = np.linspace(1.0, 0.0, bi.size, dtype=np.float32)
        alpha_col[bi] = ramp if b_side_left else ramp[::-1]
        if b_side_left: alpha_col[:bi[0]] = 1.0
        else: alpha_col[bi[-1] + 1:] = 1.0
        # Beier-Neely with the affine displacement field d(y) = M·y - y
        yy, xx = np.meshgrid(np.arange(body_p.shape[0], dtype=np.float32),
                             np.arange(body_p.shape[1], dtype=np.float32), indexing="ij")
        dx = M[0, 0] * xx + M[0, 1] * yy + M[0, 2] - xx
        dy = M[1, 0] * xx + M[1, 1] * yy + M[1, 2] - yy
        if SEAM_FLOWMORPH and (mask_ecc > 0).any() and float(np.hypot(dx, dy)[mask_ecc > 0].max()) > 8.0:
            # DB-103: a CLOSE straddling object's parallax is depth-varying (non-affine);
            # the single ECC-affine shears it. Inside the OBJECT body (where both cameras
            # see the SAME surface in the overlap), use dense optical flow (A->B) instead
            # of the affine displacement. Gated on the affine residual so well-registered
            # seams are untouched; flow only overrides where it is sane (magnitude-clamped).
            _gA8 = (np.clip(gA, 0, 1) * 255).astype(np.uint8)
            _gB8 = (np.clip(gB, 0, 1) * 255).astype(np.uint8)
            _fl = _cv.calcOpticalFlowFarneback(_gA8, _gB8, None, 0.5, 4, 25, 5, 7, 1.5, 0)
            _use = (body_p & A_val & B_val) & (np.hypot(_fl[:, :, 0], _fl[:, :, 1]) < 80.0)
            dx = np.where(_use, _fl[:, :, 0], dx).astype(np.float32)
            dy = np.where(_use, _fl[:, :, 1], dy).astype(np.float32)
        al = np.broadcast_to(alpha_col[None, :], body_p.shape).astype(np.float32)
        A_w = _cv.remap(A_patch, xx + al * dx, yy + al * dy, _cv.INTER_LINEAR, borderMode=_cv.BORDER_REPLICATE)
        B_w = _cv.remap(B_patch, xx - (1 - al) * dx, yy - (1 - al) * dy, _cv.INTER_LINEAR, borderMode=_cv.BORDER_REPLICATE)
        Av_w = _cv.remap(A_val.astype(np.uint8), xx + al * dx, yy + al * dy, _cv.INTER_NEAREST) > 0
        Bv_w = _cv.remap(B_val.astype(np.uint8), xx - (1 - al) * dx, yy - (1 - al) * dy, _cv.INTER_NEAREST) > 0
        # CONTENT seam (Photomontage-style): geometry is interpolated by the alpha ramp
        # above (continuous), but CONTENT must be winner-take-all where the two views
        # disagree — glass reflections are VIEW-DEPENDENT (the mirrored storefront's
        # parallax follows the reflected source's depth, not the body's), so any
        # alpha-blend double-exposes them. A min-difference DP seam picks the switch
        # path through whatever agrees (paint, pillars); 2 px feather.
        diff_ab = np.abs(A_w.astype(np.float32) - B_w.astype(np.float32)).sum(2)
        cost = np.where(body_p & Av_w & Bv_w, diff_ab, 0.0)
        lo_c, hi_c = int(bi[0]), int(bi[-1])
        ncol = hi_c - lo_c + 1
        nrow = body_p.shape[0]
        BIG = np.float32(1e9)
        D = np.zeros((nrow, ncol), np.float32)
        back = np.zeros((nrow, ncol), np.int32)
        D[0] = cost[0, lo_c:hi_c + 1]
        for r_ in range(1, nrow):
            prev = D[r_ - 1]
            s_l = np.concatenate([[BIG], prev[:-1]])
            s_r = np.concatenate([prev[1:], [BIG]])
            stacked = np.stack([s_l, prev, s_r])
            arg = stacked.argmin(0)
            D[r_] = cost[r_, lo_c:hi_c + 1] + stacked[arg, np.arange(ncol)]
            back[r_] = arg - 1
        s_col = np.zeros(nrow, np.int32)
        s_col[-1] = int(D[-1].argmin())
        for r_ in range(nrow - 2, -1, -1):
            s_col[r_] = s_col[r_ + 1] + back[r_ + 1, s_col[r_ + 1]]
        seam_x = (lo_c + s_col)[:, None].astype(np.float32)
        xs_g = np.arange(body_p.shape[1], dtype=np.float32)[None, :]
        if b_side_left:
            w_cont = np.clip((seam_x + 2 - xs_g) / 4.0, 0, 1)
        else:
            w_cont = np.clip((xs_g - seam_x + 2) / 4.0, 0, 1)
        w_a = (1 - w_cont) * Av_w; w_b = w_cont * Bv_w
        den = np.maximum(w_a + w_b, 1e-6)
        blend = (A_w * w_a[:, :, None] + B_w * w_b[:, :, None]) / den[:, :, None]
        write = body_p & ((Av_w | Bv_w))
        write[:, ~((alpha_col > 0) & (alpha_col < 1))] &= False   # only strip interior
        tgt_r = rows_p[:, None] * np.ones_like(cols_p)[None, :]
        tgt_c = np.ones_like(rows_p)[:, None] * (cols_p % W)[None, :]
        out2[tgt_r[write], tgt_c[write]] = np.clip(blend[write], 0, 255).astype(np.uint8)
        seam_diff = diff_ab[np.arange(nrow), lo_c + s_col]
        morph_report.append({"cam_pair": [ring_cams[ci_o], ring_cams[ci_s]],
                             "strip_cols": int(bi.size), "ecc_cc": round(float(cc_ecc), 3),
                             "max_reg_px": round(float(np.hypot(dx, dy)[mask_ecc > 0].max()) if (mask_ecc > 0).any() else 0.0, 2),
                             "seam_diff_med": round(float(np.median(seam_diff[body_p[np.arange(nrow), lo_c + s_col]])) if body_p[np.arange(nrow), lo_c + s_col].any() else 0.0, 1),
                             "n_px": int(write.sum())})
    comp = out.reshape(H, W, 3)
    # CHROMA-FRINGE SUPPRESSION (final polish): the source cameras carry purple
    # fringing on high-contrast edges (native-confirmed). Desaturate only pixels in
    # the magenta band (Cr>136 AND Cb>136 in YCrCb) toward neutral chroma, keeping
    # luminance untouched. Verified surgical: ~0.5% of pixels change; genuinely
    # purple content (the locustprojects sign) survives.
    _ycc = _cv.cvtColor(comp, _cv.COLOR_RGB2YCrCb).astype(np.float32)
    _fr = _cv.GaussianBlur(((_ycc[:, :, 1] > 136) & (_ycc[:, :, 2] > 136)).astype(np.float32), (5, 5), 0)
    _w = np.clip(_fr * 1.5, 0, 1) * 0.75
    _ycc[:, :, 1] = _ycc[:, :, 1] * (1 - _w) + 128 * _w
    _ycc[:, :, 2] = _ycc[:, :, 2] * (1 - _w) + 128 * _w
    comp = _cv.cvtColor(np.clip(_ycc, 0, 255).astype(np.uint8), _cv.COLOR_YCrCb2RGB)
    # ---- STAGE 4: GROUND TEMPORAL FILL (deterministic, real pixels) ----
    # The nadir cap and the ego-occluded zone are a REPROJECTION problem, not a
    # generation problem: the road under/around the ego was fully visible to the
    # cameras seconds before/after. Ego zone = rays intersecting the ego 3D box
    # (slab test; the hood occludes ground out to ~5-8 m, footprint alone misses it).
    # Sources gated by ego-distance 5-28 m (no ego shadow/body) and lagged-box
    # occlusion; candidates = WHOLE-LOG geometry search (ego displacement 5-58 m,
    # displacement-stratified), never a time window — a stationary ego (red light)
    # defeats any fixed window; 6-source median VALIDATES, the nearest-to-median single source
    # RENDERS (blending smears misaligned markings). Residual (never-visible) px
    # get small-area diffusion inpaint from the surrounding real road.
    def gseg_blocked(o, Xq_, boxes_q):
        outb = np.zeros(len(Xq_), bool)
        for c2_, sz2_, R2_ in boxes_q:
            half2 = sz2_ / 2 * 1.05
            o_loc = R2_.T @ (o - c2_)
            d_loc = (Xq_ - o[None, :]) @ R2_
            with np.errstate(divide="ignore", invalid="ignore"):
                inv_ = 1.0 / d_loc
                t1_ = (-half2[None, :] - o_loc[None, :]) * inv_
                t2_ = (half2[None, :] - o_loc[None, :]) * inv_
            tmin_ = np.nanmax(np.minimum(t1_, t2_), axis=1)
            tmax_ = np.nanmin(np.maximum(t1_, t2_), axis=1)
            outb |= (tmax_ >= np.maximum(tmin_, 0.0)) & (tmin_ < 0.97) & (tmin_ > 0.02)
        return outb
    df3 = DIRS.reshape(-1, 3)
    dzf = df3[:, 2]
    bmin_e = np.array([-2.2, -1.6, -C[2] - 0.33])
    bmax_e = np.array([4.6, 1.6, -0.35])
    with np.errstate(divide="ignore", invalid="ignore"):
        invd = 1.0 / df3
        ta_e = bmin_e[None, :] * invd
        tb_e = bmax_e[None, :] * invd
    tmin_e = np.nanmax(np.minimum(ta_e, tb_e), axis=1)
    tmax_e = np.nanmin(np.maximum(ta_e, tb_e), axis=1)
    egoproj = (tmax_e >= np.maximum(tmin_e, 0.0)) & (tmax_e > 0) & (dzf < -0.02)
    # DB-106 (user-found ground/scene-band boundary bug): ground must fill ONLY where the
    # scene band rendered NOTHING (comp black). Do NOT union egoproj: a NEAR car's lower body
    # has rays that pass through the ego box (egoproj=True) yet comp there is the REAL car —
    # unioning egoproj let ground (footprint-shadow + bev/plate) OVERWRITE the real car's lower
    # body ("ground eats the car"). egoproj's genuine blind region (under-hood/under-ego) is
    # comp-black and already included by the sum<12 term, so nothing real is lost.
    blackg = (comp.astype(np.int32).sum(2) < 12)
    capg = blackg.copy()
    capg[:H // 2] = False
    _capfull = capg.copy()   # DB-101: full unseen nadir cap (before the target-gate prunes capg) — for middle-only mask mode
    if CAP_LIMIT_TMPL:   # DB-126: cascade band frames only need the egozone strip of the cap
        import glob as _gl
        _clg = sorted(_gl.glob(CAP_LIMIT_TMPL % int(anchor_idx)))
        if _clg:
            capg &= (_cv.imread(_clg[0], 0) > 127)
            print("CAP_LIMIT %s -> %d px" % (_clg[0].rsplit("/", 1)[-1], int(capg.sum())), flush=True)
    flat_g = np.nonzero(capg.reshape(-1))[0]
    dirs_g = df3[flat_g]
    okd = dirs_g[:, 2] < -0.08
    flat_g = flat_g[okd]; dirs_g = dirs_g[okd]
    t_g = (-C[2] - 0.33) / dirs_g[:, 2]
    keepn = (t_g > 0) & (t_g < 30.0)
    flat_g = flat_g[keepn]; dirs_g = dirs_g[keepn]; t_g = t_g[keepn]
    Xg = C[None, :] + t_g[:, None] * dirs_g
    # GROUND HEIGHT FROM LiDAR, not a flat plane (DB-98): the flat-plane assumption is
    # wrong at curbs/slopes, and at grazing angles a small height error becomes a
    # metre-scale horizontal sampling error -> every source samples a DIFFERENT real
    # surface -> they disagree -> the per-pixel pick jumps -> radial black streaks.
    # March each cap ray onto the measured LiDAR ground surface so every source samples
    # the SAME real-world point -> agreement -> real texture. General (any scene, uses
    # the LiDAR we already have, zero scene params); falls back to the plane where no
    # LiDAR is nearby. (The residual softness at the near-nadir-behind pole is the
    # genuine evidence limit — extreme grazing + ERP pole undersampling — left honest.)
    from scipy.spatial import cKDTree as _CKD
    _gpts = lidar[(lidar[:, 2] > -0.33 - 0.5) & (lidar[:, 2] < -0.33 + 2.5)]   # ground + curb band
    if len(_gpts) > 200 and GROUND_MODE != "off":   # DB-118 speed #1b: the 3-iter march only matters when the cap gets filled
        _tr = _CKD(_gpts[:, :2])
        for _it in range(3):
            _dd, _ii = _tr.query(Xg[:, :2], k=1)
            _gz = np.where(_dd < 1.2, _gpts[_ii, 2], -0.33)
            _t = np.clip((_gz - C[2]) / dirs_g[:, 2], 0.1, 40.0)
            Xg = C[None, :] + _t[:, None] * dirs_g
    Xg_city = (Ra @ Xg.T).T + ta
    # ---- DB-101 TARGET-side visibility gate (object FOOTPRINT) ----
    # A cap ground cell directly UNDER an annotated object (any tracked box footprint:
    # parked OR moving vehicle, etc.) is not clear road -> render an honest contact-shadow
    # there instead of fake road climbing over the car ("car eaten by the road"). Use the
    # object FOOTPRINT, NOT the ray-occlusion shadow from C (that abstains the whole ground
    # BEHIND the car -> giant dark blob), and NOT a LiDAR-tall test (it fires on building
    # walls -> false road-shadow near buildings). Buildings/walls are NOT annotated, so a
    # box-footprint gate leaves road next to them as road. General, zero scene params.
    occ_t = np.zeros(len(Xg), bool)
    _allu = set(ann["track_uuid"].unique()) if (ann is not None and "track_uuid" in ann.columns) else set()
    for _c, _sz, _R in boxes_at(ann, ts, _allu):
        _loc = (Xg - _c) @ _R; _hf = _sz / 2.0
        occ_t |= (np.abs(_loc[:, 0]) < _hf[0] + 0.3) & (np.abs(_loc[:, 1]) < _hf[1] + 0.3)
    fg_occ = np.zeros(H * W, bool)
    if occ_t.any():
        _drop = flat_g[occ_t]; fg_occ[_drop] = True
        capg.reshape(-1)[_drop] = False
        _kv = ~occ_t
        flat_g, dirs_g, t_g, Xg, Xg_city = flat_g[_kv], dirs_g[_kv], t_g[_kv], Xg[_kv], Xg_city[_kv]
    fg_occ = fg_occ.reshape(H, W)
    NSLOT = 6
    chosen_g = np.full((NSLOT, len(flat_g)), -1, np.int64)
    score_g = np.full((NSLOT, len(flat_g)), np.inf)
    ai_g = int(anchor_idx)
    # CANDIDATES: displacement-BUCKETED, time-nearest WITHIN each bucket. Physics:
    # all 7 ring cameras sit in one front-roof pod, so a source ego self-occludes
    # ground 0-20 m behind itself (ray must clear its own trunk) and 0-9 m ahead
    # (hood) — the INNER cap is only ever visible from sources 20-28 m away, while
    # the outer ring prefers near ones. A pure time-nearest list misses the 20-28 m
    # band entirely (v3f: 4% coverage); a pure displacement-stratified list drags
    # in +-15 s frames whose auto-exposure drifted (v3: lavender wash). Buckets of
    # 5 m over the eligible 5-58 m range (58 = 28 + 30 m point reach), 3 frames per
    # bucket nearest in TIME = every viewing geometry present, freshest exposure
    # available for each. Whole-log search (a fixed window yields ZERO eligible
    # frames when the ego idles at a light — downtown 9.5 s stationary).
    if GROUND_MODE == "off":   # DB-118 speed #1b: no fill -> no candidate scan (the whole-log cte loop is pure waste here)
        disp_g = None
        cand_fis = []
    else:
        disp_g = np.array([np.linalg.norm(cte(int(t_))[1] - ta) for t_ in all_ts])
        fis_all = np.arange(len(all_ts))
        elig_g = (np.abs(fis_all - ai_g) >= 5) & (disp_g > 5.0) & (disp_g < 58.0)
        cand_fis = []
        for b0_ in np.arange(5.0, 58.0, 5.0):
            inb_ = np.where(elig_g & (disp_g >= b0_) & (disp_g < b0_ + 5.0))[0]
            cand_fis.extend(int(x_) for x_ in inb_[np.argsort(np.abs(inb_ - ai_g))][:3])
        cand_fis = sorted(set(cand_fis))
    if COHERENT:   # DB-109 B-coherence: FIXED window candidates (NOT anchor-relative) so neighbouring anchors share them
        _clo, _chi = COHERENT_WIN
        cand_fis = [int(t_) for t_ in range(max(0, _clo), min(len(all_ts) - 1, _chi) + 1) if abs(t_ - ai_g) >= 5]
    if GROUND_MODE == "off": cand_fis = []   # middle-only base stitch: NO ground outpaint -> nadir stays black
    # EMC FOR GROUND SOURCES: each ring camera fires up to +-22.5 ms off the sync
    # timestamp; at source-frame speeds (highway: >10 m/s) the SYNC pose is ~0.3 m
    # wrong along travel, so 6 slots land the same stripe at 6 offsets and the
    # per-pixel median pick interleaves them into a smeared multi-ghost band. Use
    # each camera's OWN capture-time pose (same principle as the scene-band EMC).
    cam_ts_arr = {ci2: np.array([int(p_.stem) for p_ in loader._image_paths[cam]], np.int64)
                  for ci2, cam in enumerate(ring_cams)}
    if GROUND_MODE == "probe":   # DB-109 breakthrough diag (user): for a few BAD ground points, back-project into EVERY candidate source cam and dump the crop each candidate actually captured + realness (grazing/egod/occlusion). Answers "are frames B,C,D,E,F's captures of this ground REAL?". Uses the SHIPPED bucketed cand_fis (the real pipeline).
        _gze = -C[2] - 0.33
        _tg = [("fwd_8m", 8.0, 0.0), ("rear_8m", -8.0, 0.0), ("rear_left", -6.0, 4.0),
               ("left_5m", 0.0, 5.0), ("right_5m", 0.0, -5.0), ("fwd_left", 6.0, 4.0)]
        _ebx = [(C + (a_ + b_) / 2.0, b_ - a_, np.eye(3)) for a_, b_ in
                ((np.array([-2.2, -1.6, _gze]), np.array([4.6, 1.6, -C[2] + 0.67])),
                 (np.array([-1.7, -1.6, _gze]), np.array([1.0, 1.6, -0.35])))]
        _pc = {}; _pj = {"anchor": int(anchor_idx), "n_cand": len(cand_fis), "targets": []}
        for _tn, _tx, _ty in _tg:
            _Xc = (Ra @ np.array([_tx, _ty, _gze])) + ta
            _cands = []
            for _fi in cand_fis:
                _tsf = int(all_ts[_fi])
                _fb = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, _tsf, moving)]
                if _tsf not in _pc:
                    _pc[_tsf] = loader.load_synced_frame(_tsf)
                _fr = _pc[_tsf]
                for _ci, _cam in enumerate(ring_cams):
                    _cts = cam_ts_arr[_ci]; _Rf, _tf = cte(int(_cts[np.argmin(np.abs(_cts - _tsf))]))
                    _eg = float(np.linalg.norm(_Xc - _tf)); _Xq = (_Xc - _tf) @ _Rf
                    _K, (_hh, _ww) = cals[_ci]
                    _T = np.asarray(frame.calibrations[_cam].T_ego_cam, float); _Tc = np.linalg.inv(_T)
                    _Xcc = _Tc[:3, :3] @ _Xq + _Tc[:3, 3]; _z = float(_Xcc[2])
                    if _z <= 0.5: continue
                    _px = _K[0, 0] * _Xcc[0] / _z + _K[0, 2]; _py = _K[1, 1] * _Xcc[1] / _z + _K[1, 2]
                    if not (6 <= _px < _ww - 6 and 6 <= _py < _hh - 6): continue
                    _hz = float(np.linalg.norm((_Xc - _tf)[:2])); _g2 = float(np.degrees(np.arctan2(max(_tf[2] - _Xc[2], 0.0), max(_hz, 1e-3))))
                    _so = bool(gseg_blocked(_T[:3, 3], _Xq[None, :], _ebx)[0])
                    _mv = bool(gseg_blocked(_T[:3, 3], _Xq[None, :], _fb)[0]) if _fb else False
                    _ix, _iy = int(round(_px)), int(round(_py)); _R = 56
                    _cr = _fr.images[_cam][max(0, _iy - _R):_iy + _R, max(0, _ix - _R):_ix + _R]
                    _cb = np.ascontiguousarray(_cr[:, :, ::-1]) if _cr.size else _cr
                    _cands.append((_g2, _eg, bool(_so or _mv), _cb, int(_fi), int(_fi - anchor_idx), bool(_so), bool(_mv)))
            _cands.sort(key=lambda d: (-d[0], abs(d[5])))
            _tiles = []
            for (_g, _e, _occ, _cb, _f, _dt, _s, _m) in _cands[:8]:
                if _cb is None or _cb.size == 0: continue
                _t = _cv.resize(_cb, (120, 120)); _col = (0, 0, 255) if _occ else (0, 200, 0)
                _cv.rectangle(_t, (0, 0), (120, 17), (0, 0, 0), -1)
                _cv.putText(_t, "g%.0f e%.0f%s" % (_g, _e, "X" if _occ else ""), (2, 13), _cv.FONT_HERSHEY_SIMPLEX, 0.38, _col, 1)
                _tiles.append(_t)
            if _tiles:
                _row = np.hstack(_tiles); _hd = np.zeros((18, _row.shape[1], 3), np.uint8)
                _cv.putText(_hd, "%s ego(%.0f,%.0f)  in-view=%d  (g=grazing deg, e=egod m, X=occluded)" % (_tn, _tx, _ty, len(_cands)), (4, 13), _cv.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
                _cv.imwrite(str(REMOTE_OUT / (run_name + "_probeboard_" + _tn + ".png")), np.vstack([_hd, _row]))
            _pj["targets"].append({"name": _tn, "xy_ego": [_tx, _ty], "n_inview": len(_cands),
                                   "top": [{"fi": d[4], "dt": d[5], "graze": round(d[0], 1), "egod": round(d[1], 1), "selfocc": d[6], "moving_occ": d[7]} for d in _cands[:12]]})
        (REMOTE_OUT / (run_name + "_probe.json")).write_text(json.dumps(_pj, indent=1), encoding="utf-8")
        print("PROBE", run_name, {t["name"]: (t["n_inview"], sum(1 for x in t["top"] if not (x["selfocc"] or x["moving_occ"]))) for t in _pj["targets"]}, flush=True)
        return {"case": run_name, "probe": True}
    if GROUND_MODE == "trace":   # DB-109 (user "open the black box"): for a few ego-relative ground points, show EXACTLY which (frame,cam,pixel) fills each — the winner's full source image with the landmark boxed, plus every other candidate's capture and why it lost (self-occ / car / not-picked). Self-contained: reproduces the shipped egod-gate + NSLOT=6 egod-rank + best-agree pick.
        _gze = -C[2] - 0.33
        _ebx = [(C + (a_ + b_) / 2.0, b_ - a_, np.eye(3)) for a_, b_ in
                ((np.array([-2.2, -1.6, _gze]), np.array([4.6, 1.6, -C[2] + 0.67])),
                 (np.array([-1.7, -1.6, _gze]), np.array([1.0, 1.6, -0.35])))]
        _tg = [("left6", 0.0, 6.0), ("right6", 0.0, -6.0), ("rear6", -6.0, 0.0), ("fwd8", 8.0, 0.0), ("fwd3_deep", 3.0, 0.0), ("rearleft", -5.0, 5.0)]
        _pc = {}; _tj = {"anchor": int(anchor_idx), "n_cand": len(cand_fis), "NSLOT": 6, "points": []}; _boards = []
        for _tn, _tx, _ty in _tg:
            _Xc = (Ra @ np.array([_tx, _ty, _gze])) + ta; _all = []
            for _fi in cand_fis:
                _tsf = int(all_ts[_fi])
                _fb = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, _tsf, moving)]
                if _tsf not in _pc: _pc[_tsf] = loader.load_synced_frame(_tsf)
                _fr = _pc[_tsf]
                for _ci, _cam in enumerate(ring_cams):
                    _cts = cam_ts_arr[_ci]; _Rf, _tf = cte(int(_cts[np.argmin(np.abs(_cts - _tsf))]))
                    _eg = float(np.linalg.norm(_Xc - _tf)); _Xq = (_Xc - _tf) @ _Rf
                    _K, (_hh, _ww) = cals[_ci]
                    _T = np.asarray(frame.calibrations[_cam].T_ego_cam, float); _Tc = np.linalg.inv(_T)
                    _Xcc = _Tc[:3, :3] @ _Xq + _Tc[:3, 3]; _z = float(_Xcc[2])
                    if _z <= 0.5: continue
                    _px = _K[0, 0] * _Xcc[0] / _z + _K[0, 2]; _py = _K[1, 1] * _Xcc[1] / _z + _K[1, 2]
                    if not (6 <= _px < _ww - 6 and 6 <= _py < _hh - 6): continue
                    if not (5.0 < _eg < 28.0): continue
                    _hz = float(np.linalg.norm((_Xc - _tf)[:2])); _g2 = float(np.degrees(np.arctan2(max(_tf[2] - _Xc[2], 0.0), max(_hz, 1e-3))))
                    _so = bool(gseg_blocked(_T[:3, 3], _Xq[None, :], _ebx)[0])
                    _mv = bool(gseg_blocked(_T[:3, 3], _Xq[None, :], _fb)[0]) if _fb else False
                    _ix, _iy = int(round(_px)), int(round(_py)); _RR = 56
                    _img = _fr.images[_cam]
                    _cr = _img[max(0, _iy - _RR):_iy + _RR, max(0, _ix - _RR):_ix + _RR]
                    _all.append({"fi": int(_fi), "ci": int(_ci), "cam": str(_cam), "dt": int(_fi - anchor_idx),
                                 "px": float(_px), "py": float(_py), "egod": _eg, "graze": _g2, "selfocc": _so, "moving": _mv,
                                 "col": _img[_iy, _ix].astype(np.float32), "crop": np.ascontiguousarray(_cr[:, :, ::-1]) if _cr.size else None, "full": _img})
            _passed = [d for d in _all if (not (SELFOCC and d["selfocc"])) and (not (MOVING_GATE and d["moving"]))]
            _passed.sort(key=lambda d: d["egod"]); _slots = _passed[:6]; _winner = None; _spread = None
            if _slots:
                _cols = np.array([d["col"] for d in _slots]); _med = np.median(_cols, axis=0)
                _dist = np.abs(_cols - _med[None, :]).sum(1); _winner = _slots[int(np.argmin(_dist))]; _spread = float(_dist.mean())
            _all.sort(key=lambda d: d["egod"]); _tiles = []
            for d in _all[:14]:
                if d["crop"] is None or d["crop"].size == 0: continue
                _t = _cv.resize(d["crop"], (110, 110))
                _gated = (SELFOCC and d["selfocc"]) or (MOVING_GATE and d["moving"])
                _isw = (_winner is not None and d["fi"] == _winner["fi"] and d["ci"] == _winner["ci"])
                _bx = (0, 200, 0) if _isw else ((0, 0, 230) if _gated else (150, 150, 150))
                _cv.rectangle(_t, (0, 0), (109, 109), _bx, 3 if _isw else 2); _cv.rectangle(_t, (0, 0), (110, 28), (0, 0, 0), -1)
                _tag = "WIN" if _isw else ("SELF-OCC" if (SELFOCC and d["selfocc"]) else ("CAR" if (MOVING_GATE and d["moving"]) else ""))
                _cv.putText(_t, "f%d c%d %s" % (d["fi"], d["ci"], _tag), (2, 11), _cv.FONT_HERSHEY_SIMPLEX, 0.32, _bx, 1)
                _cv.putText(_t, "e%.0f g%.0f" % (d["egod"], d["graze"]), (2, 24), _cv.FONT_HERSHEY_SIMPLEX, 0.32, (220, 220, 220), 1)
                _tiles.append(_t)
            _strip = np.hstack(_tiles) if _tiles else np.zeros((110, 110, 3), np.uint8)
            if _winner is not None:
                _wfull = np.ascontiguousarray(_winner["full"][:, :, ::-1]).copy(); _wx, _wy = int(round(_winner["px"])), int(round(_winner["py"]))
                _cv.rectangle(_wfull, (_wx - 56, _wy - 56), (_wx + 56, _wy + 56), (0, 200, 0), 4); _cv.circle(_wfull, (_wx, _wy), 5, (0, 0, 255), -1)
                _sc = 380.0 / _wfull.shape[1]; _wsm = _cv.resize(_wfull, (380, int(_wfull.shape[0] * _sc)))
                _zc = _winner["full"][max(0, _wy - 56):_wy + 56, max(0, _wx - 56):_wx + 56]; _zoom = _cv.resize(np.ascontiguousarray(_zc[:, :, ::-1]), (150, 150)) if _zc.size else np.zeros((150, 150, 3), np.uint8)
                _Hh = max(_wsm.shape[0], 150); _left = np.zeros((_Hh, _wsm.shape[1], 3), np.uint8); _left[:_wsm.shape[0]] = _wsm
                _right = np.zeros((_Hh, 150, 3), np.uint8); _right[:150] = _zoom; _top = np.hstack([_left, _right])
                _htxt = "%s ego(%.0f,%.0f)m | in-view=%d passed-gate=%d | WINNER=frame %d cam %d  egod=%.0fm graze=%.0fdeg spread=%.1f" % (_tn, _tx, _ty, len(_all), len(_passed), _winner["fi"], _winner["ci"], _winner["egod"], _winner["graze"], _spread or 0)
            else:
                _top = np.zeros((150, 530, 3), np.uint8)
                _htxt = "%s ego(%.0f,%.0f)m | in-view=%d passed-gate=0 -> NO CLEAN SOURCE (all self-occ/car-blocked) = nobody captured this cleanly" % (_tn, _tx, _ty, len(_all))
            _W = max(_top.shape[1], _strip.shape[1], 540)
            _hd = np.zeros((22, _W, 3), np.uint8); _cv.putText(_hd, _htxt, (4, 15), _cv.FONT_HERSHEY_SIMPLEX, 0.4, (0, 180, 255) if _winner is None else (255, 255, 255), 1)
            def _pad(im, w=_W): return im if im.shape[1] >= w else np.hstack([im, np.zeros((im.shape[0], w - im.shape[1], 3), np.uint8)])
            _board = np.vstack([_pad(_hd), _pad(_top), np.full((4, _W, 3), 60, np.uint8), _pad(_strip)])
            _cv.imwrite(str(REMOTE_OUT / (run_name + "_trace_" + _tn + ".png")), _board); _boards.append(_board)
            _tj["points"].append({"name": _tn, "ego_xy": [_tx, _ty], "n_inview": len(_all), "n_passed": len(_passed),
                                  "winner": (None if _winner is None else {"fi": _winner["fi"], "ci": _winner["ci"], "cam": _winner["cam"], "px": round(_winner["px"], 1), "py": round(_winner["py"], 1), "egod": round(_winner["egod"], 1), "graze": round(_winner["graze"], 1)}),
                                  "spread": _spread, "candidates": [{"fi": d["fi"], "ci": d["ci"], "dt": d["dt"], "egod": round(d["egod"], 1), "graze": round(d["graze"], 1), "selfocc": d["selfocc"], "moving": d["moving"]} for d in _all]})
        if _boards:
            _BW = max(b.shape[1] for b in _boards); _stk = []
            for b in _boards:
                _stk.append(b if b.shape[1] >= _BW else np.hstack([b, np.zeros((b.shape[0], _BW - b.shape[1], 3), np.uint8)])); _stk.append(np.full((8, _BW, 3), 120, np.uint8))
            _cv.imwrite(str(REMOTE_OUT / (run_name + "_trace_all.png")), np.vstack(_stk))
        (REMOTE_OUT / (run_name + "_trace.json")).write_text(json.dumps(_tj, indent=1, default=float), encoding="utf-8")
        print("TRACE", run_name, {p["name"]: (p["n_inview"], p["n_passed"], (p["winner"]["fi"] if p["winner"] else None)) for p in _tj["points"]}, flush=True)
        return {"case": run_name, "trace": True}
    if GROUND_MODE == "bevaudit":
        # ---- DB-102 NO-RENDER metric-domain audit ----
        # Build a LOCAL BEV ground grid around the ego, project each cell into the SAME
        # bucketed candidates+cams with the SAME gates (FOV, egod 5-28, moving-box, two-box
        # ego self-occ), and dump per-cell radial stats {nvalid, best_grazing, az_spread,
        # lum_std}. Answers "is the determinable 3-7 m annulus recoverable in BEV?" before
        # building the renderer — measure before build ([[feedback-isolate-input-variable]]).
        HALF, CELL = 9.0, 0.08
        _gx = np.arange(-HALF, HALF, CELL)
        _GX, _GY = np.meshgrid(_gx, _gx)
        cell_xy = np.stack([_GX.ravel(), _GY.ravel()], 1).astype(np.float64)
        rr = np.linalg.norm(cell_xy, axis=1)
        _kc = (rr >= 1.0) & (rr <= 8.0)
        cell_xy = cell_xy[_kc]; rr = rr[_kc]; NC = len(cell_xy)
        cz = np.full(NC, -0.33)
        if len(_gpts) > 200:
            from scipy.spatial import cKDTree as _CKD2
            _tr2 = _CKD2(_gpts[:, :2])
            _dd2, _ii2 = _tr2.query(cell_xy, k=1)
            cz = np.where(_dd2 < 1.2, _gpts[_ii2, 2], -0.33)
        Xcell = np.concatenate([cell_xy, cz[:, None]], 1)
        Xcell_city = (Ra @ Xcell.T).T + ta
        ncount = np.zeros(NC, np.int32)
        graze_max = np.full(NC, -1.0, np.float64)
        ssin = np.zeros(NC); scos = np.zeros(NC); sl = np.zeros(NC); sl2 = np.zeros(NC)
        _gc = {}
        for fi in cand_fis:
            tsf = int(all_ts[fi])
            fboxes = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, tsf, moving)]
            for ci2, cam in enumerate(ring_cams):
                cts_ = cam_ts_arr[ci2]
                Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
                egod = np.linalg.norm(Xcell_city - tf[None, :], axis=1)
                Xq = (Xcell_city - tf[None, :]) @ Rf
                K2, (hh2, ww2) = cals[ci2]
                T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float); Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2) & (egod > 5.0) & (egod < 28.0)
                if not okq.any(): continue
                blocked = np.zeros(NC, bool)
                if fboxes: blocked[okq] = gseg_blocked(T2[:3, 3], Xq[okq], fboxes)
                body_lo = np.array([-2.2, -1.6, -C[2] - 0.33]); body_hi = np.array([4.6, 1.6, -C[2] + 0.67])
                cab_lo = np.array([-1.7, -1.6, -C[2] - 0.33]); cab_hi = np.array([1.0, 1.6, -0.35])
                ego_boxes = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3)) for bn_, bx_ in ((body_lo, body_hi), (cab_lo, cab_hi))]
                selfocc = np.zeros(NC, bool); selfocc[okq] = gseg_blocked(T2[:3, 3], Xq[okq], ego_boxes)
                visq = okq & ~blocked & ~selfocc
                if not visq.any(): continue
                if tsf not in _gc: _gc[tsf] = loader.load_synced_frame(tsf)
                img2 = _gc[tsf].images[cam]
                col = bilinear(img2, px2, py2) * np.exp(gains[ci2])[None, :]
                lum = 0.299 * col[:, 0] + 0.587 * col[:, 1] + 0.114 * col[:, 2]
                horiz = np.linalg.norm((Xcell_city - tf[None, :])[:, :2], axis=1)
                graze = np.degrees(np.arctan2(np.maximum(tf[2] - Xcell_city[:, 2], 0.0), np.maximum(horiz, 1e-3)))
                az = np.arctan2(tf[1] - Xcell_city[:, 1], tf[0] - Xcell_city[:, 0])
                v = visq
                ncount += v
                graze_max = np.where(v & (graze > graze_max), graze, graze_max)
                ssin += np.where(v, np.sin(az), 0.0); scos += np.where(v, np.cos(az), 0.0)
                sl += np.where(v, lum, 0.0); sl2 += np.where(v, lum * lum, 0.0)
        n = np.maximum(ncount, 1)
        lum_std = np.sqrt(np.maximum(sl2 / n - (sl / n) ** 2, 0.0))
        az_R = np.sqrt(ssin ** 2 + scos ** 2) / n
        out = np.stack([cell_xy[:, 0], cell_xy[:, 1], rr, ncount.astype(np.float64),
                        graze_max, 1.0 - az_R, lum_std], 1).astype(np.float32)
        np.save(str(REMOTE_OUT / (run_name + "_bevaudit.npy")), out)
        print("BEVAUDIT", run_name, "NC", NC, "cols=x,y,rr,ncount,graze_max,az_spread,lum_std")
        cand_fis = []   # skip the normal per-pixel render loop
    bev_sel_px = bev_spread = bev_anyg = None
    if GROUND_MODE in ("bev", "bevdirect"):
        # ---- DB-102 metric-domain (BEV) ground reconstruction (bevdirect = DB-107: same metric selection, DIRECT ERP sampling) ----
        # Fuse the determinable annulus on a UNIFORM metric raster (no ERP pole
        # singularity, no per-pixel source jump) with the SAME gates, gate per-cell by
        # source agreement, then RESAMPLE into the cap. Audit (STEP 0) found coverage is
        # plentiful (nvalid 7-17) and the discriminator is AGREEMENT (lum_std: highway 2-3
        # =recoverable, bmw near-nadir 50 =genuine blind). Coherent raster => speckle gone
        # by construction; resampling makes the pole a smooth magnification, not noise.
        HALF, CELL = 12.0, 0.06   # ~24 m tile covers the whole cap ground (cap ~0-10 m); 160k cells < 900k cap px
        _bgx = np.arange(-HALF, HALF, CELL); BW = len(_bgx)
        _BGX, _BGY = np.meshgrid(_bgx, _bgx)               # xy indexing: [row=y, col=x]
        bev_xy = np.stack([_BGX.ravel(), _BGY.ravel()], 1).astype(np.float64)
        bev_z = np.full(len(bev_xy), -0.33)
        if len(_gpts) > 200:
            from scipy.spatial import cKDTree as _CKD3
            _tr3 = _CKD3(_gpts[:, :2]); _dd3, _ii3 = _tr3.query(bev_xy, k=1)
            bev_z = np.where(_dd3 < 1.2, _gpts[_ii3, 2], -0.33)
        Xb_city = (Ra @ np.concatenate([bev_xy, bev_z[:, None]], 1).T).T + ta
        NB = len(bev_xy); NS2 = 6
        bchosen = np.full((NS2, NB), -1, np.int64); bscore = np.full((NS2, NB), np.inf)
        for fi in cand_fis:
            tsf = int(all_ts[fi])
            fboxes = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, tsf, moving)]
            for ci2, cam in enumerate(ring_cams):
                cts_ = cam_ts_arr[ci2]; Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
                egod = np.linalg.norm(Xb_city - tf[None, :], axis=1)
                Xq = (Xb_city - tf[None, :]) @ Rf
                K2, (hh2, ww2) = cals[ci2]; T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float); Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2) & (egod > 5.0) & (egod < 28.0)
                if not okq.any(): continue
                blocked = np.zeros(NB, bool)
                if fboxes: blocked[okq] = gseg_blocked(T2[:3, 3], Xq[okq], fboxes)
                body_lo = np.array([-2.2, -1.6, -C[2] - 0.33]); body_hi = np.array([4.6, 1.6, -C[2] + 0.67])
                cab_lo = np.array([-1.7, -1.6, -C[2] - 0.33]); cab_hi = np.array([1.0, 1.6, -0.35])
                ego_boxes = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3)) for bn_, bx_ in ((body_lo, body_hi), (cab_lo, cab_hi))]
                selfocc = np.zeros(NB, bool); selfocc[okq] = gseg_blocked(T2[:3, 3], Xq[okq], ego_boxes)
                visq = okq & ~blocked & ~selfocc
                if not visq.any(): continue
                code_b = fi * 10 + ci2; sc = egod.copy(); rem = visq.copy()
                for s_ in range(NS2):
                    better = rem & (sc < bscore[s_])
                    if not better.any(): continue
                    for t_ in range(NS2 - 1, s_, -1):
                        bchosen[t_][better] = bchosen[t_ - 1][better]; bscore[t_][better] = bscore[t_ - 1][better]
                    bchosen[s_][better] = code_b; bscore[s_][better] = sc[better]; rem = rem & ~better
        bcol = np.full((NS2, NB, 3), np.nan, np.float32); _bc = {}
        for slot in range(NS2):
            for code in np.unique(bchosen[slot][bchosen[slot] >= 0]):
                fi, ci2 = int(code) // 10, int(code) % 10; sel = bchosen[slot] == code; tsf = int(all_ts[fi])
                if tsf not in _bc: _bc[tsf] = loader.load_synced_frame(tsf)
                fr2 = _bc[tsf]; Rf, tf = cte(int(fr2.timestamps_ns[ring_cams[ci2]]))
                Xq = (Xb_city[sel] - tf[None, :]) @ Rf
                K2, _s2 = cals[ci2]; T2 = np.asarray(frame.calibrations[ring_cams[ci2]].T_ego_cam, float); Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                bcol[slot][sel] = np.clip(bilinear(fr2.images[ring_cams[ci2]], px2, py2) * np.exp(gains[ci2])[None, :], 0, 255).astype(np.float32)
        bhave = ~np.isnan(bcol[:, :, 0]); bany = bhave.any(0)
        bmed = np.nanmedian(bcol, axis=0); bd = np.abs(bcol - bmed[None]).sum(2)
        _bn = np.maximum(bhave.sum(0), 1); bspread_c = np.where(bhave, bd, 0.0).sum(0) / _bn; bspread_c[~bany] = 1e9
        bd[~bhave] = np.inf; bpick = np.argmin(bd, axis=0); bev_rgb = bcol[bpick, np.arange(NB)]
        bev_rgb = np.where(np.isnan(bev_rgb), 0.0, bev_rgb).astype(np.float32)
        # RESAMPLE the BEV raster into the cap (flat_g) — bilinear colour on the ego grid,
        # nearest agreement/coverage (1e9 must not bleed). col=x, row=y (xy meshgrid).
        col_f = (Xg[:, 0] + HALF) / CELL; row_f = (Xg[:, 1] + HALF) / CELL
        i0 = np.clip(np.floor(col_f).astype(int), 0, BW - 2); j0 = np.clip(np.floor(row_f).astype(int), 0, BW - 2)
        fa = np.clip(col_f - i0, 0, 1)[:, None]; fb = np.clip(row_f - j0, 0, 1)[:, None]
        R3 = bev_rgb.reshape(BW, BW, 3)
        if FAITH_MASK:   # DB-109 A: save the UNDISTORTED top-down BEV ground raster + hole mask for BEV-domain generative inpaint (the ERP-pole distortion broke vanilla ERP inpaint; BEV is flat → SDXL-friendly, then reproject)
            _bf = (bany & (bspread_c <= 14.0)).reshape(BW, BW)
            save_rgb(REMOTE_OUT / f"{run_name}_bevraster.png", np.where(_bf[:, :, None], np.clip(R3, 0, 255).astype(np.uint8), 0))
            save_rgb(REMOTE_OUT / f"{run_name}_bevmask.png", np.dstack([((~_bf).astype(np.uint8) * 255)] * 3))
        bev_sel_px = (R3[j0, i0] * (1 - fa) * (1 - fb) + R3[j0, i0 + 1] * fa * (1 - fb)
                      + R3[j0 + 1, i0] * (1 - fa) * fb + R3[j0 + 1, i0 + 1] * fa * fb)
        ic = np.clip(np.round(col_f).astype(int), 0, BW - 1); jr = np.clip(np.round(row_f).astype(int), 0, BW - 1)
        bev_spread = bspread_c.reshape(BW, BW)[jr, ic]
        bev_anyg = bany.reshape(BW, BW)[jr, ic] & (np.linalg.norm(Xg[:, :2], axis=1) <= HALF - CELL)
        if GROUND_MODE == "bevdirect":
            # DB-107: keep bev's METRIC-CONSISTENT source choice (bchosen) but render by DIRECT ERP
            # sampling of that source per cap pixel — no raster round-trip. Kills fill's per-pixel
            # radial (neighbour cap pixels share a metric cell -> same source) AND bev's softness
            # (no source->raster->ERP double resample). Agreement gate reused from the raster.
            _icd = np.clip(np.round((Xg[:, 0] + HALF) / CELL).astype(int), 0, BW - 1)
            _jrd = np.clip(np.round((Xg[:, 1] + HALF) / CELL).astype(int), 0, BW - 1)
            cap_code = bchosen[0].reshape(BW, BW)[_jrd, _icd]
            cap_col = np.full((len(flat_g), 3), np.nan, np.float32); _bdc = {}
            for code in np.unique(cap_code[cap_code >= 0]):
                fi_, ci2_ = int(code) // 10, int(code) % 10; sel_ = cap_code == code; tsf_ = int(all_ts[fi_])
                if tsf_ not in _bdc: _bdc[tsf_] = loader.load_synced_frame(tsf_)
                fr2_ = _bdc[tsf_]; Rf_, tf_ = cte(int(fr2_.timestamps_ns[ring_cams[ci2_]]))
                Xq_ = (Xg_city[sel_] - tf_[None, :]) @ Rf_
                K2_, _s_ = cals[ci2_]; T2_ = np.asarray(frame.calibrations[ring_cams[ci2_]].T_ego_cam, float); Tci2_ = np.linalg.inv(T2_)
                Xc2_ = (Tci2_[:3, :3] @ Xq_.T).T + Tci2_[:3, 3]; z2_ = Xc2_[:, 2]
                px2_ = K2_[0, 0] * Xc2_[:, 0] / np.maximum(z2_, 1e-6) + K2_[0, 2]; py2_ = K2_[1, 1] * Xc2_[:, 1] / np.maximum(z2_, 1e-6) + K2_[1, 2]
                cap_col[sel_] = np.clip(bilinear(fr2_.images[ring_cams[ci2_]], px2_, py2_) * np.exp(gains[ci2_])[None, :], 0, 255).astype(np.float32)
            bev_sel_px = np.where(np.isnan(cap_col), 0.0, cap_col).astype(np.float32)
            bev_anyg = (cap_code >= 0) & (np.linalg.norm(Xg[:, :2], axis=1) <= HALF - CELL)
            bev_spread = bspread_c.reshape(BW, BW)[_jrd, _icd]
        _rad = np.linalg.norm(Xg[:, :2], axis=1)   # DB-102 diag: coverage/agreement by cap-pixel radius
        for _lo, _hi in [(0, 1), (1, 3), (3, 5), (5, 7), (7, 9), (9, 12), (12, 99)]:
            _mm = (_rad >= _lo) & (_rad < _hi)
            if _mm.any():
                print("BEVDIAG r%d-%d npx=%d anyg=%.2f spread_ok=%.2f rendered=%.2f" % (
                    _lo, _hi, int(_mm.sum()), float(bev_anyg[_mm].mean()),
                    float((bev_spread[_mm] <= 30).mean()),
                    float((bev_anyg[_mm] & (bev_spread[_mm] <= 30)).mean())), flush=True)
        cand_fis = []   # the per-pixel loop no-ops; bev_* override the pick below
    if GROUND_MODE == "extract":
        # ---- DB-118 (fable5): dump ALL gated per-source ground samples for the
        # joint alignment-colour-texture optimisation (inverse-problem paradigm).
        # No slots, no selection, no fusion — every observation that passes the
        # gates becomes evidence for the optimiser. Output: per-source (cell-id,
        # RGB) arrays + grid meta, saved to LOCAL disk (not Drive; ~300MB).
        _MHALF, _CW = 46.0, 0.05
        _mcx, _mcy = float(ta[0]), float(ta[1])
        _xmin, _ymin = _mcx - _MHALF, _mcy - _MHALF
        _GW = _GH = int(round(2.0 * _MHALF / _CW))
        _ge = lidar[(lidar[:, 2] > -0.93) & (lidar[:, 2] < 0.17)]
        _gc = (Ra @ _ge.T).T + ta[None, :]
        _gzplane = float(np.median(_gc[:, 2])) if len(_gc) > 200 else float(ta[2] - 0.33)
        _HCC = 8
        _HW = _GW // _HCC
        _hin = (_gc[:, 0] >= _xmin) & (_gc[:, 0] < _xmin + 2 * _MHALF) & (_gc[:, 1] >= _ymin) & (_gc[:, 1] < _ymin + 2 * _MHALF)
        _gci = _gc[_hin]
        _hcx = np.clip(((_gci[:, 0] - _xmin) / (_CW * _HCC)).astype(np.int64), 0, _HW - 1)
        _hcy = np.clip(((_gci[:, 1] - _ymin) / (_CW * _HCC)).astype(np.int64), 0, _HW - 1)
        _hid = _hcy * _HW + _hcx
        _hsum = np.bincount(_hid, weights=_gci[:, 2], minlength=_HW * _HW)
        _hcnt = np.bincount(_hid, minlength=_HW * _HW)
        _hz = np.where(_hcnt > 2, _hsum / np.maximum(_hcnt, 1), _gzplane).astype(np.float32).reshape(_HW, _HW)
        _hz = _cv.medianBlur(_hz, 5)
        _HZ = _cv.resize(_hz, (_GW, _GH), interpolation=_cv.INTER_LINEAR)
        _wz = _HZ.ravel()
        # DB-118 v2: static-obstacle cell filter — a cell whose LiDAR max-z rises >0.5m
        # above its ground surface is occupied by a building/pole/parked structure; rays
        # "landing" there sample facade colour, not road (the black-smoke artefact).
        _ge2 = lidar[(lidar[:, 2] > -0.93) & (lidar[:, 2] < 3.0)]
        _gc2 = (Ra @ _ge2.T).T + ta[None, :]
        _hin2 = (_gc2[:, 0] >= _xmin) & (_gc2[:, 0] < _xmin + 2 * _MHALF) & (_gc2[:, 1] >= _ymin) & (_gc2[:, 1] < _ymin + 2 * _MHALF)
        _gc2 = _gc2[_hin2]
        _hcx2 = np.clip(((_gc2[:, 0] - _xmin) / (_CW * _HCC)).astype(np.int64), 0, _HW - 1)
        _hcy2 = np.clip(((_gc2[:, 1] - _ymin) / (_CW * _HCC)).astype(np.int64), 0, _HW - 1)
        _hid2 = _hcy2 * _HW + _hcx2
        _hmax = np.full(_HW * _HW, -1e9, np.float32)
        np.maximum.at(_hmax, _hid2, _gc2[:, 2].astype(np.float32))
        _obst = (_hmax.reshape(_HW, _HW) - _hz) > 0.5
        _obst = _cv.dilate(_obst.astype(np.uint8), np.ones((3, 3), np.uint8), 1).astype(bool)
        _cellok = ~_cv.resize(_obst.astype(np.uint8), (_GW, _GH), interpolation=_cv.INTER_NEAREST).astype(bool).ravel()
        _gxc = (_xmin + (np.arange(_GW, dtype=np.float64) + 0.5) * _CW)
        _gyc = (_ymin + (np.arange(_GH, dtype=np.float64) + 0.5) * _CW)
        _dispA = np.array([np.linalg.norm(cte(int(t_))[1][:2] - np.array([_mcx, _mcy])) for t_ in all_ts])
        _reach = np.nonzero(_dispA < (_MHALF + 30.0))[0]
        _aidx = int(anchor_idx)
        _pickf = []
        for _bv in np.unique(np.floor(_dispA[_reach] / 5.0)):
            _inbf = _reach[np.floor(_dispA[_reach] / 5.0) == _bv]
            _pickf.extend(int(x_) for x_ in _inbf[np.argsort(np.abs(_inbf - _aidx))][:8])
        _wfis = sorted(sorted(set(_pickf), key=lambda i_: abs(i_ - _aidx))[:110])
        _eb = [(C + (a_ + b_) / 2.0, b_ - a_, np.eye(3)) for a_, b_ in (
            (np.array([-2.2, -1.6, -C[2] - 0.33]), np.array([4.6, 1.6, -C[2] + 0.67])),
            (np.array([-1.7, -1.6, -C[2] - 0.33]), np.array([1.0, 1.6, -0.35])))]
        _sids, _gis, _rgbs, _metas = [], [], [], []
        _EIM = None
        if EGO_IMG_MASK:
            _eimz = np.load(EGO_IMG_MASK)
            _EIM = [(_eimz[c_] if c_ in _eimz.files else None) for c_ in ring_cams]
            print("EGO_IMG_MASK loaded", [(_c.replace("ring_", ""), None if _m is None else round(float(_m.mean()), 3)) for _c, _m in zip(ring_cams, _EIM)], flush=True)
        _ex_cache = {}
        for _fi in _wfis:
            _tsf = int(all_ts[_fi])
            _fb = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, _tsf, moving)]
            _tfa = cte(_tsf)[1]
            _cxl = int(np.clip((_tfa[0] - 29.0 - _xmin) / _CW, 0, _GW - 1))
            _cxh = int(np.clip((_tfa[0] + 29.0 - _xmin) / _CW, 1, _GW))
            _cyl = int(np.clip((_tfa[1] - 29.0 - _ymin) / _CW, 0, _GH - 1))
            _cyh = int(np.clip((_tfa[1] + 29.0 - _ymin) / _CW, 1, _GH))
            if _cxh <= _cxl + 2 or _cyh <= _cyl + 2:
                continue
            _sub = (np.arange(_cyl, _cyh)[:, None] * _GW + np.arange(_cxl, _cxh)[None, :]).ravel()
            _sxx, _syy = np.meshgrid(_gxc[_cxl:_cxh], _gyc[_cyl:_cyh])
            _pxyz = np.stack([_sxx.ravel(), _syy.ravel(), _wz[_sub]], 1)
            if _tsf not in _ex_cache:
                _ex_cache.clear()
                _ex_cache[_tsf] = loader.load_synced_frame(_tsf)
            _fr = _ex_cache[_tsf]
            for _ci, _cam in enumerate(ring_cams):
                _cts = cam_ts_arr[_ci]
                _Rf, _tf = cte(int(_cts[np.argmin(np.abs(_cts - _tsf))]))
                _d2 = _pxyz - _tf[None, :]
                _egod = np.linalg.norm(_d2, axis=1)
                _Xq = _d2 @ _Rf
                _K, (_hh, _ww) = cals[_ci]
                _T = np.asarray(frame.calibrations[_cam].T_ego_cam, float)
                _Tc = np.linalg.inv(_T)
                _Xc = (_Tc[:3, :3] @ _Xq.T).T + _Tc[:3, 3]
                _z = _Xc[:, 2]
                _px = _K[0, 0] * _Xc[:, 0] / np.maximum(_z, 1e-6) + _K[0, 2]
                _py = _K[1, 1] * _Xc[:, 1] / np.maximum(_z, 1e-6) + _K[1, 2]
                _ok = (_z > 0.5) & (_px >= 2) & (_px < _ww - 2) & (_py >= 2) & (_py < _hh - 2) & (_egod > 2.5) & (_egod < 28.0)
                if not _ok.any():
                    continue
                if _fb:
                    _bl = np.zeros(len(_sub), bool)
                    _bl[_ok] = gseg_blocked(_T[:3, 3], _Xq[_ok], _fb)
                    _ok = _ok & ~_bl
                _so = np.zeros(len(_sub), bool)
                _so[_ok] = gseg_blocked(_T[:3, 3], _Xq[_ok], _eb)
                _ok = _ok & ~_so
                if _EIM is not None and _EIM[_ci] is not None:
                    _m4 = _EIM[_ci]
                    _ok = _ok & ~_m4[np.clip((_py * 0.25).astype(np.int64), 0, _m4.shape[0] - 1),
                                     np.clip((_px * 0.25).astype(np.int64), 0, _m4.shape[1] - 1)]
                if _ok.sum() < 500:
                    continue
                _col = np.clip(bilinear(_fr.images[_cam], _px[_ok], _py[_ok]) * np.exp(gains[_ci])[None, :], 0, 255).astype(np.uint8)
                _sid = len(_metas)
                _metas.append([int(_fi), int(_ci), int(_ok.sum())])
                _sids.append(np.full(int(_ok.sum()), _sid, np.int16))
                _gis.append(_sub[_ok].astype(np.int32))
                _rgbs.append(_col)
        np.savez_compressed("/content/db118_" + run_name + "_samples.npz",
                            sid=np.concatenate(_sids), gi=np.concatenate(_gis),
                            rgb=np.concatenate(_rgbs), meta=np.array(_metas, np.int64),
                            gw=np.int64(_GW), gh=np.int64(_GH), cellok=_cellok,
                            xmin=np.float64(_xmin), ymin=np.float64(_ymin), cw=np.float64(_CW))
        print("EXTRACT_DONE", run_name, "sources", len(_metas), "samples", sum(m[2] for m in _metas), flush=True)
        cand_fis = []
    if GROUND_MODE == "worldbev":
        # ---- DB-117 P0 (fable5, 2026-07-01): FAIR-version world-BEV ground map ----
        # Replaces the DB-109 B1 strawman (constant-plane Z ~1.4m BELOW the true road,
        # tight-cluster MEAN render, luminance-only per-cell norm, binary holes, fixed
        # window). Five upgrades, each mapping to a proven per-cap lesson:
        #  U1 LiDAR world HEIGHT map (grazing amplifies dz->dx 11-19x; a wrong plane
        #     => systematic cross-source misregistration = smear/quilt/hood hits)
        #  U2 per-SOURCE (frame,cam) 3-channel global gain solved against the map
        #     median (exposure+WB together; luminance norm cannot fix the WB purple)
        #  U3 SINGLE-SOURCE render, the agreeing cluster only VALIDATES
        #     (DB-88/v6 law: blending misaligned grazing sources smears markings)
        #  U4 evidence TIERS conf/low/hole; holes NS-inpainted in the FLAT BEV domain
        #     (the ERP-pole domain is what turns NS extension into comet-tail streaks)
        #  U5 window = every frame whose ego is within source reach of the map
        # selfocc is UNCONDITIONALLY ON (2026-06-25 hood-reflection fake-coverage
        # lesson); moving-box gate always on (the whole-log window dodges traffic);
        # EMC capture-time poses per camera (same as the per-cap path).
        _MHALF, _CW = 46.0, 0.05
        _mcx, _mcy = float(ta[0]), float(ta[1])
        if WORLDBEV_CENTER:
            _mcx, _mcy = (float(_v) for _v in WORLDBEV_CENTER.split(","))
        _xmin, _ymin = _mcx - _MHALF, _mcy - _MHALF
        _GW = _GH = int(round(2.0 * _MHALF / _CW))
        _NWC = _GW * _GH
        # U1: ground height map from the anchor's accumulated (dyn-removed) LiDAR.
        _ge = lidar[(lidar[:, 2] > -0.93) & (lidar[:, 2] < 0.17)]
        _gc = (Ra @ _ge.T).T + ta[None, :]
        _gzplane = float(np.median(_gc[:, 2])) if len(_gc) > 200 else float(ta[2] - 0.33)
        _HCC = 8
        _HW = _GW // _HCC
        _hin = (_gc[:, 0] >= _xmin) & (_gc[:, 0] < _xmin + 2 * _MHALF) & (_gc[:, 1] >= _ymin) & (_gc[:, 1] < _ymin + 2 * _MHALF)
        _gci = _gc[_hin]
        _hcx = np.clip(((_gci[:, 0] - _xmin) / (_CW * _HCC)).astype(np.int64), 0, _HW - 1)
        _hcy = np.clip(((_gci[:, 1] - _ymin) / (_CW * _HCC)).astype(np.int64), 0, _HW - 1)
        _hid = _hcy * _HW + _hcx
        _hsum = np.bincount(_hid, weights=_gci[:, 2], minlength=_HW * _HW)
        _hcnt = np.bincount(_hid, minlength=_HW * _HW)
        _hz = np.where(_hcnt > 2, _hsum / np.maximum(_hcnt, 1), _gzplane).astype(np.float32).reshape(_HW, _HW)
        _hz = _cv.medianBlur(_hz, 5)
        _HZ = _cv.resize(_hz, (_GW, _GH), interpolation=_cv.INTER_LINEAR)
        _wz = _HZ.ravel()
        _gxc = (_xmin + (np.arange(_GW, dtype=np.float64) + 0.5) * _CW)
        _gyc = (_ymin + (np.arange(_GH, dtype=np.float64) + 0.5) * _CW)
        # U5: whole-log reachable window (ego within 28m source reach of any map cell)
        # Budgeting must be ANCHOR-CENTRED: displacement buckets (every viewing
        # geometry present) x time-nearest-to-anchor within each bucket. A plain
        # [:N] cut keeps the log's EARLIEST frames and starves the anchor's own
        # surroundings (hw309 hole-east-of-anchor bug: slow log => all 319 frames
        # reachable => the cut dropped a220-318 entirely).
        _dispA = np.array([np.linalg.norm(cte(int(t_))[1][:2] - np.array([_mcx, _mcy])) for t_ in all_ts])
        _reach = np.nonzero(_dispA < (_MHALF + 30.0))[0]
        _aidx = int(anchor_idx)
        _pickf = []
        for _bv in np.unique(np.floor(_dispA[_reach] / 5.0)):
            _inbf = _reach[np.floor(_dispA[_reach] / 5.0) == _bv]
            _pickf.extend(int(x_) for x_ in _inbf[np.argsort(np.abs(_inbf - _aidx))][:8])
        _wfis = sorted(sorted(set(_pickf), key=lambda i_: abs(i_ - _aidx))[:110])
        if WORLDBEV_SHARD:   # DB-131: this worker builds only its interleaved share of the source frames
            _sh_i, _sh_k = (int(_v) for _v in WORLDBEV_SHARD.split(","))
            _wfis = _wfis[_sh_i::_sh_k]
        _NSW = 6
        _wchosen = np.full((_NSW, _NWC), -1, np.int64)
        _wscore = np.full((_NSW, _NWC), np.inf, np.float32)
        _eb = [(C + (a_ + b_) / 2.0, b_ - a_, np.eye(3)) for a_, b_ in (
            (np.array([-2.2, -1.6, -C[2] - 0.33]), np.array([4.6, 1.6, -C[2] + 0.67])),
            (np.array([-1.7, -1.6, -C[2] - 0.33]), np.array([1.0, 1.6, -0.35])))]
        for _fi in (_wfis if not (WORLDBEV_FILL or WORLDBEV_LOAD) else []):   # P1: skip the expensive build when a filled map overrides it; DB-131: or when merged shard state is loaded below
            _tsf = int(all_ts[_fi])
            _fb = [(c2_, sz2_ * 1.3, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, _tsf, moving)]
            _tfa = cte(_tsf)[1]
            _cxl = int(np.clip((_tfa[0] - 29.0 - _xmin) / _CW, 0, _GW - 1))
            _cxh = int(np.clip((_tfa[0] + 29.0 - _xmin) / _CW, 1, _GW))
            _cyl = int(np.clip((_tfa[1] - 29.0 - _ymin) / _CW, 0, _GH - 1))
            _cyh = int(np.clip((_tfa[1] + 29.0 - _ymin) / _CW, 1, _GH))
            if _cxh <= _cxl + 2 or _cyh <= _cyl + 2:
                continue
            _sub = (np.arange(_cyl, _cyh)[:, None] * _GW + np.arange(_cxl, _cxh)[None, :]).ravel()
            _sxx, _syy = np.meshgrid(_gxc[_cxl:_cxh], _gyc[_cyl:_cyh])
            _pxyz = np.stack([_sxx.ravel(), _syy.ravel(), _wz[_sub]], 1)
            for _ci, _cam in enumerate(ring_cams):
                _cts = cam_ts_arr[_ci]
                _Rf, _tf = cte(int(_cts[np.argmin(np.abs(_cts - _tsf))]))
                _d2 = _pxyz - _tf[None, :]
                _egod = np.linalg.norm(_d2, axis=1)
                _Xq = _d2 @ _Rf
                _K, (_hh, _ww) = cals[_ci]
                _T = np.asarray(frame.calibrations[_cam].T_ego_cam, float)
                _Tc = np.linalg.inv(_T)
                _Xc = (_Tc[:3, :3] @ _Xq.T).T + _Tc[:3, 3]
                _z = _Xc[:, 2]
                _px = _K[0, 0] * _Xc[:, 0] / np.maximum(_z, 1e-6) + _K[0, 2]
                _py = _K[1, 1] * _Xc[:, 1] / np.maximum(_z, 1e-6) + _K[1, 2]
                _ok = (_z > 0.5) & (_px >= 2) & (_px < _ww - 2) & (_py >= 2) & (_py < _hh - 2) & (_egod > 2.5) & (_egod < 28.0)
                if not _ok.any():
                    continue
                if _fb:
                    _bl = np.zeros(len(_sub), bool)
                    _bl[_ok] = gseg_blocked(_T[:3, 3], _Xq[_ok], _fb)
                    _ok = _ok & ~_bl
                _so = np.zeros(len(_sub), bool)
                _so[_ok] = gseg_blocked(_T[:3, 3], _Xq[_ok], _eb)
                _ok = _ok & ~_so
                if not _ok.any():
                    continue
                _code = _fi * 10 + _ci
                _sc = _egod.astype(np.float32)
                _rem = _ok.copy()
                for _s in range(_NSW):
                    _b = _rem & (_sc < _wscore[_s][_sub])
                    if not _b.any():
                        continue
                    _gi = _sub[_b]
                    for _tt in range(_NSW - 1, _s, -1):
                        _wchosen[_tt][_gi] = _wchosen[_tt - 1][_gi]
                        _wscore[_tt][_gi] = _wscore[_tt - 1][_gi]
                    _wchosen[_s][_gi] = _code
                    _wscore[_s][_gi] = _sc[_b]
                    _rem = _rem & ~_b
        _wcol = np.full((_NSW, _NWC, 3), np.nan, np.float32)
        _wcache = {}
        for _s in range(_NSW):
            for _code in np.unique(_wchosen[_s][_wchosen[_s] >= 0]):
                _fi, _ci = int(_code) // 10, int(_code) % 10
                _sel = _wchosen[_s] == _code
                _tsf = int(all_ts[_fi])
                if _tsf not in _wcache:
                    _wcache[_tsf] = loader.load_synced_frame(_tsf)
                _fr = _wcache[_tsf]
                _Rf, _tf = cte(int(_fr.timestamps_ns[ring_cams[_ci]]))
                _gid = np.nonzero(_sel)[0]
                _pw = np.stack([_gxc[_gid % _GW], _gyc[_gid // _GW], _wz[_gid]], 1)
                _Xq = (_pw - _tf[None, :]) @ _Rf
                _K, _ = cals[_ci]
                _T = np.asarray(frame.calibrations[ring_cams[_ci]].T_ego_cam, float)
                _Tc = np.linalg.inv(_T)
                _Xc = (_Tc[:3, :3] @ _Xq.T).T + _Tc[:3, 3]
                _z = _Xc[:, 2]
                _px = _K[0, 0] * _Xc[:, 0] / np.maximum(_z, 1e-6) + _K[0, 2]
                _py = _K[1, 1] * _Xc[:, 1] / np.maximum(_z, 1e-6) + _K[1, 2]
                _wcol[_s][_sel] = np.clip(bilinear(_fr.images[ring_cams[_ci]], _px, _py) * np.exp(gains[_ci])[None, :], 0, 255).astype(np.float32)
        _wcache.clear()
        if WORLDBEV_DUMP:   # DB-131 shard worker: dump raw slot state for the merge, before any post-processing
            np.savez_compressed(WORLDBEV_DUMP, chosen=_wchosen, score=_wscore, col=_wcol)
            print("WORLDBEV_DUMPED", WORLDBEV_DUMP, int((_wchosen[0] >= 0).sum()), flush=True)
        if WORLDBEV_LOAD:   # DB-131 merge consumer: adopt merged slot state; the tuned post-processing below runs unchanged
            _wz_npz = np.load(WORLDBEV_LOAD)
            _wchosen = _wz_npz["chosen"]
            _wscore = _wz_npz["score"]
            _wcol = _wz_npz["col"]
            print("WORLDBEV_LOADED", WORLDBEV_LOAD, flush=True)
        _wh = ~np.isnan(_wcol[:, :, 0])
        # U2: per-SOURCE 3-channel global gain vs the pre-gain map median (exposure+WB).
        _wmed0 = np.nanmedian(np.where(_wh[:, :, None], _wcol, np.nan), axis=0)
        _gains_src = {}
        for _code in [int(x) for x in np.unique(_wchosen[_wchosen >= 0])]:
            _m = (_wchosen == _code) & _wh
            if int(_m.sum()) < 800:
                continue
            _sm = _wcol[_m]
            _mm = np.broadcast_to(_wmed0[None], _wcol.shape)[_m]
            _r = np.nanmedian(_mm / np.maximum(_sm, 8.0), axis=0)
            _gv = np.clip(_r, 0.75, 1.35).astype(np.float32)
            _gains_src[_code] = [round(float(x), 4) for x in _gv]
            _wcol[_m] = np.clip(_wcol[_m] * _gv[None, :], 0, 255)
        # U3: cluster VALIDATES, single best-ranked source RENDERS.
        _wmed = np.nanmedian(np.where(_wh[:, :, None], _wcol, np.nan), axis=0)
        _wdd = np.abs(_wcol - _wmed[None]).sum(2)
        _wdd[~_wh] = np.inf
        _wnv = _wh.sum(0)
        _wspr = np.where(_wh, np.where(np.isfinite(_wdd), _wdd, 0.0), 0.0).sum(0) / np.maximum(_wnv, 1)
        _tight = _wh & (_wdd <= 24.0)
        _conf = (_wnv >= 2) & (_tight.sum(0) >= 2) & (_wspr <= 30.0)
        _anyv = _wnv >= 1
        _ft = np.argmax(_tight, axis=0)
        _fh = np.argmax(_wh, axis=0)
        _arw = np.arange(_NWC)
        _col_conf = _wcol[_ft, _arw]
        _col_low = _wcol[_fh, _arw]
        _tier = np.where(_conf, 2, np.where(_anyv, 1, 0)).astype(np.uint8)
        _wmap = np.where(_conf[:, None], _col_conf, np.where(_anyv[:, None], _col_low, 0.0))
        _mimg = np.clip(np.nan_to_num(_wmap), 0, 255).astype(np.uint8).reshape(_GH, _GW, 3)
        # U4: flat-domain TELEA inpaint of holes within 8m of covered ground. Telea in
        # the FLAT domain is isotropic nearest-boundary propagation (stable on large
        # holes); ERP-domain NS on a huge residual diverges into black + colour noise
        # (the hw309b right-rear artefact). Distant never-covered cells stay tier0.
        _t2d = _tier.reshape(_GH, _GW)
        _cov8 = (_t2d > 0).astype(np.uint8)
        _dst = _cv.distanceTransform(1 - _cov8, _cv.DIST_L2, 3)
        _inp = (_t2d == 0) & (_dst < 160.0)
        if _inp.any():
            _mimg = _cv.inpaint(_mimg, _inp.astype(np.uint8) * 255, 8, _cv.INPAINT_TELEA)
            _t2d[_inp] = 3   # tier3 = Telea-extended (structureless honest grey) = the GENERATIVE region for P1
        save_rgb(REMOTE_OUT / f"{run_name}_worldmap.png", _mimg)
        save_rgb(REMOTE_OUT / f"{run_name}_worldtier.png", np.dstack([_t2d * 85] * 3).astype(np.uint8))
        if WORLDBEV_FILL:   # DB-117 P1: OVERRIDE the map with a generated (FLUX-filled) one — every
            # frame samples the SAME generated map => holes filled ONCE, temporally shared.
            _fm = _cv.imread(WORLDBEV_FILL)[:, :, ::-1]
            if _fm.shape[:2] != (_GH, _GW):
                _fm = _cv.resize(_fm, (_GW, _GH), interpolation=_cv.INTER_LINEAR)
            _mimg = np.ascontiguousarray(_fm)
            _t2d = np.maximum(_t2d, 1)   # everything sampleable; conf stays 2, gen regions render as low
            print("WORLDBEV2 FILLED-OVERRIDE", WORLDBEV_FILL, flush=True)
        print("WORLDBEV2", run_name, "grid", _GW, "cw", _CW, "frames", len(_wfis),
              "tier2%%=%.1f tier1%%=%.1f hole%%=%.1f nsrc_gain=%d" % (
                  100.0 * float((_t2d == 2).mean()), 100.0 * float((_t2d == 1).mean()),
                  100.0 * float((_t2d == 0).mean()), len(_gains_src)), flush=True)
        # sample the shared world map at each cap ground point (Xg_city world XY)
        _Wm = _mimg.astype(np.float32)
        _cf = (Xg_city[:, 0] - _xmin) / _CW - 0.5
        _rf = (Xg_city[:, 1] - _ymin) / _CW - 0.5
        _i0 = np.clip(np.floor(_cf).astype(int), 0, _GW - 2)
        _j0 = np.clip(np.floor(_rf).astype(int), 0, _GH - 2)
        _fa = np.clip(_cf - _i0, 0, 1)[:, None]
        _fb2 = np.clip(_rf - _j0, 0, 1)[:, None]
        _cap = (_Wm[_j0, _i0] * (1 - _fa) * (1 - _fb2) + _Wm[_j0, _i0 + 1] * _fa * (1 - _fb2)
                + _Wm[_j0 + 1, _i0] * (1 - _fa) * _fb2 + _Wm[_j0 + 1, _i0 + 1] * _fa * _fb2)
        _ic = np.clip(np.round(_cf).astype(int), 0, _GW - 1)
        _jr = np.clip(np.round(_rf).astype(int), 0, _GH - 1)
        _tn = _t2d[_jr, _ic]
        _inb = (_cf >= 0) & (_cf < _GW - 1) & (_rf >= 0) & (_rf < _GH - 1)
        bev_sel_px = _cap.astype(np.float32)
        bev_anyg = _inb & (_tn >= 1)
        bev_spread = np.where(_inb & (_tn == 2), 0.0, np.where(_inb & (_tn >= 1), 20.0, 1e9))
        print("WBEVCAP2 %s ingrid%%=%.1f conf%%=%.1f low%%=%.1f | ncap=%d" % (
            run_name, 100.0 * float(_inb.mean()), 100.0 * float((_inb & (_tn == 2)).mean()),
            100.0 * float((_inb & (_tn == 1)).mean()), len(Xg_city)), flush=True)
        cand_fis = []
    _EIMF = None
    if EGO_IMG_MASK and cand_fis:   # DB-118 F1: the classic fill was the remaining ego-leak path (extract had the gate, fill did not — user's "hood shows through" ①②)
        _eimfz = np.load(EGO_IMG_MASK)
        _EIMF = [(_eimfz[c_] if c_ in _eimfz.files else None) for c_ in ring_cams]
        print("EGO_IMG_MASK(fill) loaded", flush=True)
    # DB115-PRO fix#3 (2026-07-10): GROUND_TORCH — GPU (torch) source-selection loop.
    # The candidate scan is ~25 frames x 7 cams of 800k-point numpy geometry (the
    # bulk of fill's 226s/frame) while the GPU idles. Same math, batched per frame;
    # slot ranking via a single topk over all (frame,cam) codes at the end (egod
    # scores are continuous floats -> tie order immaterial). CPU path untouched.
    _use_gt = False
    if GROUND_TORCH and cand_fis and len(flat_g):
        try:
            import torch as _th
            _dev = "cuda" if _th.cuda.is_available() else "cpu"
            _use_gt = _dev == "cuda"
        except Exception:
            _use_gt = False
    if _use_gt:
        _t0gt = time.time()
        _Xcity = _th.as_tensor(Xg_city, dtype=_th.float32, device=_dev)      # (N,3)
        _Ng = _Xcity.shape[0]
        _egoR2 = _th.as_tensor(np.linalg.norm(Xg_city[:, :2] - ta[None, :2], axis=1),
                               dtype=_th.float32, device=_dev)

        def _slab_t(o_np, Xq_t, boxes):
            ob = _th.zeros(Xq_t.shape[0], dtype=_th.bool, device=_dev)
            o_t = _th.as_tensor(o_np, dtype=_th.float32, device=_dev)
            for c2_, sz2_, R2_ in boxes:
                Rt = _th.as_tensor(R2_, dtype=_th.float32, device=_dev)
                ct = _th.as_tensor(c2_, dtype=_th.float32, device=_dev)
                half2 = _th.as_tensor(sz2_ / 2 * 1.05, dtype=_th.float32, device=_dev)
                o_loc = Rt.T @ (o_t - ct)
                d_loc = (Xq_t - o_t[None, :]) @ Rt
                inv_ = 1.0 / d_loc
                t1_ = (-half2[None, :] - o_loc[None, :]) * inv_
                t2_ = (half2[None, :] - o_loc[None, :]) * inv_
                lo = _th.minimum(t1_, t2_); hi = _th.maximum(t1_, t2_)
                lo = _th.nan_to_num(lo, nan=-1e30); hi = _th.nan_to_num(hi, nan=1e30)
                tmin_ = lo.max(dim=1).values; tmax_ = hi.min(dim=1).values
                ob |= (tmax_ >= _th.clamp(tmin_, min=0.0)) & (tmin_ < 0.97) & (tmin_ > 0.02)
            return ob

        body_lo = np.array([-2.2, -1.6, -C[2] - 0.33]); body_hi = np.array([4.6, 1.6, -C[2] + 0.67])
        cab_lo = np.array([-1.7, -1.6, -C[2] - 0.33]); cab_hi = np.array([1.0, 1.6, -0.35])
        ego_boxes = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3))
                     for bn_, bx_ in ((body_lo, body_hi), (cab_lo, cab_hi))]
        _codes = []; _scs = []
        for fi in cand_fis:
            tsf = int(all_ts[fi])
            fboxes = ([(c2_, sz2_ * MOVING_SCALE, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, tsf, moving)] if MOVING_GATE else [])
            for ci2, cam in enumerate(ring_cams):
                cts_ = cam_ts_arr[ci2]
                Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
                tf_t = _th.as_tensor(tf, dtype=_th.float32, device=_dev)
                Rf_t = _th.as_tensor(Rf, dtype=_th.float32, device=_dev)
                diff = _Xcity - tf_t[None, :]
                egod = diff.norm(dim=1)
                Xq_t = diff @ Rf_t
                K2, (hh2, ww2) = cals[ci2]
                T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float)
                Tci2 = np.linalg.inv(T2)
                Tr = _th.as_tensor(Tci2[:3, :3], dtype=_th.float32, device=_dev)
                Tt = _th.as_tensor(Tci2[:3, 3], dtype=_th.float32, device=_dev)
                Xc2 = Xq_t @ Tr.T + Tt[None, :]
                z2 = Xc2[:, 2]
                zc = _th.clamp(z2, min=1e-6)
                px2 = K2[0, 0] * Xc2[:, 0] / zc + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / zc + K2[1, 2]
                okq = ((z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2)
                       & (egod > 5.0) & (egod < 28.0))
                if _EIMF is not None and _EIMF[ci2] is not None:
                    _m4f = _th.as_tensor(_EIMF[ci2].astype(np.uint8), device=_dev)
                    _iy = _th.clamp((py2 * 0.25).long(), 0, _m4f.shape[0] - 1)
                    _ix = _th.clamp((px2 * 0.25).long(), 0, _m4f.shape[1] - 1)
                    okq &= _m4f[_iy, _ix] == 0
                if not bool(okq.any()):
                    continue
                blocked = _th.zeros(_Ng, dtype=_th.bool, device=_dev)
                if fboxes:
                    blocked = _slab_t(T2[:3, 3], Xq_t, fboxes)
                selfocc = _th.zeros(_Ng, dtype=_th.bool, device=_dev)
                if SELFOCC:
                    selfocc = _slab_t(T2[:3, 3], Xq_t, ego_boxes)
                    if SELFOCC_DEEP_R > 0:
                        selfocc &= _egoR2 < SELFOCC_DEEP_R
                visq = okq & ~blocked & ~selfocc
                if not bool(visq.any()):
                    continue
                sc = (egod - COHERENT_SWEET).abs() if COHERENT else egod
                _codes.append(fi * 10 + ci2)
                _scs.append(_th.where(visq, sc, _th.full_like(sc, float("inf"))))
        if _scs:
            _S = _th.stack(_scs)                                   # (n_codes, N)
            _k = min(NSLOT, _S.shape[0])
            _vals, _idx = _th.topk(_S, _k, dim=0, largest=False)   # ascending
            _codes_t = _th.as_tensor(_codes, dtype=_th.int64, device=_dev)
            _pickc = _codes_t[_idx]                                # (k, N)
            _valid = _th.isfinite(_vals)
            chosen_g[:_k] = _th.where(_valid, _pickc, _th.full_like(_pickc, -1)).cpu().numpy()
            score_g[:_k] = _th.where(_valid, _vals, _th.full_like(_vals, float("inf"))).cpu().numpy()
            del _S, _vals, _idx, _pickc, _valid
        _th.cuda.empty_cache()
        print("GROUND_TORCH scan %.1fs codes=%d" % (time.time() - _t0gt, len(_codes)), flush=True)
    for fi in ([] if _use_gt else cand_fis):
        tsf = int(all_ts[fi])
        fboxes = ([(c2_, sz2_ * MOVING_SCALE, R2_) for (c2_, sz2_, R2_) in boxes_at(ann, tsf, moving)] if MOVING_GATE else [])   # DB-109 Stage-1b/1c: MOVING_GATE off = isolation; MOVING_SCALE shrinks the box toward a precise gate (1.3=shipped, 1.0=precise)
        for ci2, cam in enumerate(ring_cams):
            cts_ = cam_ts_arr[ci2]
            Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
            egod = np.linalg.norm(Xg_city - tf[None, :], axis=1)
            Xq = (Xg_city - tf[None, :]) @ Rf
            K2, (hh2, ww2) = cals[ci2]
            T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float)
            Tci2 = np.linalg.inv(T2)
            Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
            px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
            py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
            okq = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2) & (egod > 5.0) & (egod < 28.0)
            if _EIMF is not None and _EIMF[ci2] is not None:
                _m4f = _EIMF[ci2]
                okq = okq & ~_m4f[np.clip((py2 * 0.25).astype(np.int64), 0, _m4f.shape[0] - 1),
                                  np.clip((px2 * 0.25).astype(np.int64), 0, _m4f.shape[1] - 1)]
            if not okq.any(): continue
            blocked = np.zeros(len(flat_g), bool)
            if fboxes:
                blocked[okq] = gseg_blocked(T2[:3, 3], Xq[okq], fboxes)
            # SOURCE-EGO SELF-OCCLUSION (proven by single-source isolation): rays
            # from a source camera to ground points ~5-9 m ahead graze the source's
            # OWN hood, so the sample is hood sky-reflection (bluish smears), not
            # road. egod is the wrong geometry (point distance, not ray clearance).
            # TWO-BOX ego model: a roof-height single box over the full length
            # blocks legal over-the-trunk rear views (downtown's only inner-cap
            # sources, 15-19.6 m, collapsed to 22% coverage) — the real vehicle is
            # cabin-high only mid-body; hood and trunk are ~1.0 m. Full-length low
            # box + cabin-height short box, gseg's internal 1.05 the only margin.
            body_lo = np.array([-2.2, -1.6, -C[2] - 0.33]); body_hi = np.array([4.6, 1.6, -C[2] + 0.67])
            cab_lo = np.array([-1.7, -1.6, -C[2] - 0.33]); cab_hi = np.array([1.0, 1.6, -0.35])
            ego_boxes = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3))
                         for bn_, bx_ in ((body_lo, body_hi), (cab_lo, cab_hi))]
            selfocc = np.zeros(len(flat_g), bool)
            if SELFOCC:   # DB-109 Lever-1: gate the self-occlusion test (default on=shipped)
                selfocc[okq] = gseg_blocked(T2[:3, 3], Xq[okq], ego_boxes)
                if SELFOCC_DEEP_R > 0:   # DB-109 LOCAL self-occ: drop hood-grazing views ONLY within R of the ego (deep centre = genuinely hood-only); KEEP mid-field grazing views of real lanes
                    selfocc &= (np.linalg.norm(Xg_city[:, :2] - ta[None, :2], axis=1) < SELFOCC_DEEP_R)
            visq = okq & ~blocked & ~selfocc
            if not visq.any(): continue
            code_g = fi * 10 + ci2
            sc = (np.abs(egod - COHERENT_SWEET) if COHERENT else egod.copy())   # DB-109 B-coherence: rank by closeness to the egod sweet-spot (a deterministic world-point function) instead of nearest
            rem = visq.copy()
            for s_ in range(NSLOT):
                better = rem & (sc < score_g[s_])
                if not better.any(): continue
                for t_ in range(NSLOT - 1, s_, -1):
                    chosen_g[t_][better] = chosen_g[t_ - 1][better]; score_g[t_][better] = score_g[t_ - 1][better]
                chosen_g[s_][better] = code_g; score_g[s_][better] = sc[better]
                rem = rem & ~better
    colg = np.full((NSLOT, len(flat_g), 3), np.nan, np.float32)
    gcache = {}
    for slot in range(NSLOT):
        for code in np.unique(chosen_g[slot][chosen_g[slot] >= 0]):
            fi, ci2 = int(code) // 10, int(code) % 10
            sel = chosen_g[slot] == code
            tsf = int(all_ts[fi])
            if tsf not in gcache:
                gcache[tsf] = loader.load_synced_frame(tsf)
            fr2 = gcache[tsf]
            Rf, tf = cte(int(fr2.timestamps_ns[ring_cams[ci2]]))   # capture-time pose (EMC)
            Xq = (Xg_city[sel] - tf[None, :]) @ Rf
            K2, _s2 = cals[ci2]
            T2 = np.asarray(frame.calibrations[ring_cams[ci2]].T_ego_cam, float)
            Tci2 = np.linalg.inv(T2)
            Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
            px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
            py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
            img2 = fr2.images[ring_cams[ci2]]
            g2 = np.exp(gains[ci2])[None, :]
            colg[slot][sel] = np.clip(bilinear(img2, px2, py2) * g2, 0, 255).astype(np.float32)
    haveg = ~np.isnan(colg[:, :, 0])
    anyg = haveg.any(0)
    if anyg.any():
        medg = np.nanmedian(colg, axis=0)
        dist_s = np.abs(colg - medg[None]).sum(2)
    else:   # DB-118 speed #1b: off mode / zero candidates — skip the 6x2M nanmedian (and its All-NaN warnings)
        medg = np.zeros((colg.shape[1], 3), np.float32)
        dist_s = np.full(colg.shape[:2], np.inf, np.float32)
    # SOURCE-AGREEMENT GATE (DB-98): the near-nadir-behind corners are seen only by
    # far, grazing sources that DISAGREE wildly (each grazing ray skims different
    # content); the per-pixel pick then jumps between them -> radial black streaks.
    # spread = mean abs deviation of the valid sources from their median; high spread
    # = views don't agree = unreliable. We render real pixels only where they AGREE
    # (spread small) and abstain elsewhere -> smooth fill. (Isolation-verified: the
    # spread map co-locates exactly with the streak wedges; nvalid/t_g gates did not.)
    _ns_count = np.maximum(haveg.sum(0), 1)
    spread = np.where(haveg, dist_s, 0.0).sum(0) / _ns_count
    spread[~anyg] = 1e9
    if GROUND_MODE == "funnel":   # DB-109 Stage-1: per-pixel GATE FUNNEL — split "no-source" into geometry-blind (N1=0, TRUE wall) vs rule-rejected (egod / self-occ / moving / spread). Diagnostic-only; runs ON TOP of the normal fill path, does NOT change fill/bev output.
        NF = len(flat_g)
        f_fov = np.zeros(NF, bool)        # N1: ray lands in SOME ring-cam FOV (egod ignored)
        f_noselfocc = np.zeros(NF, bool)  # N2: + not ego-self-occluded
        f_egod = np.zeros(NF, bool)       # N3: + egod in [5,28]
        _ebx = [(C + (bn_ + bx_) / 2.0, bx_ - bn_, np.eye(3))
                for bn_, bx_ in ((np.array([-2.2, -1.6, -C[2] - 0.33]), np.array([4.6, 1.6, -C[2] + 0.67])),
                                 (np.array([-1.7, -1.6, -C[2] - 0.33]), np.array([1.0, 1.6, -0.35])))]
        for fi in cand_fis:
            tsf = int(all_ts[fi])
            for ci2, cam in enumerate(ring_cams):
                cts_ = cam_ts_arr[ci2]
                Rf, tf = cte(int(cts_[np.argmin(np.abs(cts_ - tsf))]))
                egod_f = np.linalg.norm(Xg_city - tf[None, :], axis=1)
                Xq = (Xg_city - tf[None, :]) @ Rf
                K2, (hh2, ww2) = cals[ci2]
                T2 = np.asarray(frame.calibrations[cam].T_ego_cam, float)
                Tci2 = np.linalg.inv(T2)
                Xc2 = (Tci2[:3, :3] @ Xq.T).T + Tci2[:3, 3]; z2 = Xc2[:, 2]
                px2 = K2[0, 0] * Xc2[:, 0] / np.maximum(z2, 1e-6) + K2[0, 2]
                py2 = K2[1, 1] * Xc2[:, 1] / np.maximum(z2, 1e-6) + K2[1, 2]
                infov = (z2 > 0.5) & (px2 >= 2) & (px2 < ww2 - 2) & (py2 >= 2) & (py2 < hh2 - 2)
                if not infov.any(): continue
                so = np.zeros(NF, bool); so[infov] = gseg_blocked(T2[:3, 3], Xq[infov], _ebx)
                ok_egod = (egod_f > 5.0) & (egod_f < 28.0)
                f_fov |= infov
                f_noselfocc |= infov & ~so
                f_egod |= infov & ~so & ok_egod
        cls = np.zeros(NF, np.uint8)   # highest gate each blind pixel reaches (0=geom-blind ... 5=real)
        cls[f_fov] = 1; cls[f_noselfocc] = 2; cls[f_egod] = 3
        cls[anyg] = 4; cls[anyg & (spread <= 30.0)] = 5
        _cm = np.full(H * W, 255, np.uint8); _cm[flat_g] = cls   # 255 = not-a-cap pixel; 0..5 = highest gate the blind cap pixel reaches (0=geom-blind ... 5=real)
        np.save(str(REMOTE_OUT / (run_name + "_funnel_cls.npy")), _cm.reshape(H, W))
        _counts = {int(k): int((cls == k).sum()) for k in range(6)}
        _funnel = {
            "run": run_name, "n_blind": int(NF), "counts_by_gate": _counts,
            "pct": {int(k): round(100.0 * v / max(NF, 1), 1) for k, v in _counts.items()},
            "gate_legend": {0: "geometry-blind: no cam EVER saw it (N1=0, TRUE wall, generation-only)",
                            1: "FOV-hit but fully ego-self-occluded (killed at self-occ)",
                            2: "passed self-occ but egod out of [5,28] (RULE-REJECTED by the 28m cut)",
                            3: "passed self-occ+egod but did NOT become a written source (moving-box occluded OR lost the NSLOT egod-rank competition) — NOT purely moving; do NOT read cls3 as 'moving冤杀' (DB-113A trace proved hw mid-field is SELF-OCC-gated, not moving)",
                            4: "had source(s) but they disagree (spread>30)",
                            5: "REAL (written)"},
            "candidates": [{"fi": int(_f), "disp_m": round(float(disp_g[_f]), 1), "dt_frames": int(_f - ai_g)} for _f in cand_fis]}
        (REMOTE_OUT / (run_name + "_funnel_counts.json")).write_text(json.dumps(_funnel, indent=1), encoding="utf-8")
        print("FUNNEL", run_name, _funnel["pct"], flush=True)
    if GROUND_MODE == "diag":   # DATA EVIDENCE of the blind spot: per cap pixel -> #valid sources + nearest-source distance
        _nv = np.full(H * W, np.nan, np.float32); _nv[flat_g] = haveg.sum(0).astype(np.float32)
        _eg = np.full(H * W, np.nan, np.float32); _eg[flat_g] = np.where(np.isfinite(score_g[0]), score_g[0], np.nan).astype(np.float32)
        np.save(str(REMOTE_OUT / (run_name + "_diag_nvalid.npy")), _nv.reshape(H, W))
        np.save(str(REMOTE_OUT / (run_name + "_diag_nearestegod.npy")), _eg.reshape(H, W))
        _spd = np.full(H * W, np.nan, np.float32); _spd[flat_g] = np.asarray(spread, np.float32); np.save(str(REMOTE_OUT / (run_name + "_diag_spread.npy")), _spd.reshape(H, W))   # DB-108 AUDIT: per-cap spread map -> real-write(spread<=30) vs sources-disagree(>30) for the real-vs-inpaint overlay
    dist_s[~haveg] = np.inf
    pick = (np.zeros(len(flat_g), np.int64) if (COHERENT and COHERENT_PICK == "sweet") else np.argmin(dist_s, axis=0))   # DB-109 B-coherence: COHERENT "sweet"=slot 0 egod-sweet DETERMINISTIC single source (spread-19.8 quilt); "agree"=argmin-to-median (BEST-AGREEING, still world-deterministic in a fixed window -> kills 格子 + keeps coherence). non-COHERENT keeps the legacy per-anchor argmin.
    sel_px = colg[pick, np.arange(len(flat_g))]
    if GROUND_MODE == "diag":   # DB-109 A-evidence: per-pixel WINNING source-frame fi (decoded from the chosen slot) — proves whether the 格子/tiling is source-label fragmentation (-1 = no source)
        _wc = chosen_g[pick, np.arange(len(flat_g))]
        _lb = np.full(H * W, -1, np.int32); _lb[flat_g] = np.where(_wc >= 0, (_wc // 10).astype(np.int32), -1)
        np.save(str(REMOTE_OUT / (run_name + "_diag_label.npy")), _lb.reshape(H, W))
    if GROUND_MODE in ("bev", "bevdirect", "worldbev") and bev_sel_px is not None:   # DB-102/107 + DB-109 B1: metric-fused cap overrides the per-pixel pick
        anyg = bev_anyg; spread = bev_spread; sel_px = bev_sel_px.astype(np.float32)
    # GLOBAL cast correction to the anchor truth ring: the inner cap is only ever
    # visible at 4-6 deg grazing (front-pod rig blocks all steeper views), where
    # asphalt specularly reflects the SKY — sunny scene -> blue-lavender cast that
    # clashes with the steep-view road in the scene band directly above. ONE global
    # per-channel gain to the median of the anchor's own lowest scene-band rows:
    # no regional boundaries (per-region clipped gains quilted, tested NEG), the
    # within-fill texture untouched, only the cast removed.
    nonb_r = comp.astype(np.int32).sum(2) >= 12
    ring_px = []
    for u_ in range(0, W, 4):
        rs_ = np.nonzero(nonb_r[H // 2:, u_])[0]
        if len(rs_) >= 4:
            ring_px.append(comp[H // 2 + rs_[-10:], u_])
    if CAP_ONLY and CAP_REF_TMPL:   # DB-126: comp is black under CAP_ONLY -> the truth ring must come from the external band render (self-reference would null the cast fix)
        import glob as _gl2
        _crg = sorted(_gl2.glob(CAP_REF_TMPL % int(anchor_idx)))
        if _crg:
            _crimg = _cv.imread(_crg[0])[:, :, ::-1].astype(np.float64)
            _crnb = _crimg.sum(2) >= 12
            ring_px = []
            for u_ in range(0, W, 4):
                rs_ = np.nonzero(_crnb[H // 2:, u_])[0]
                if len(rs_) >= 4:
                    ring_px.append(_crimg[H // 2 + rs_[-10:], u_])
    if ring_px and anyg.any():
        # DB-117 U6 per-azimuth ring gain TESTED NEG (bmw14d, 2026-07-02): piecewise-
        # constant bin application banded the low-texture road (v3e's lesson in 1D)
        # and blew out the deep nadir; the residual BMW "purple" is the SOURCE ISP
        # tone (the band's own road is the same tint) => source-faithful, not a
        # defect. GLOBAL gain stays.
        ref_med = np.median(np.concatenate(ring_px).reshape(-1, 3).astype(np.float32), axis=0)
        fill_med = np.median(sel_px[anyg], axis=0)
        gn_glob = np.clip(ref_med / np.maximum(fill_med, 1.0), 0.7, 1.5)
        sel_px[anyg] = sel_px[anyg] * gn_glob[None, :]
    cflat = comp.reshape(-1, 3).copy()
    SPREAD_MAX = 30.0   # abstain where the sources disagree more than this (units: sum-abs-channel dev)
    _gm = anyg & (spread <= SPREAD_MAX)
    if COHERENT: _gm = _gm & (haveg.sum(0) >= 2)   # DB-109 B-coherence: nvalid>=2 guard (a single source could be car-body; need >=2 agreeing sources)
    cflat[flat_g[_gm]] = np.clip(sel_px[_gm], 0, 255).astype(np.uint8)
    comp = cflat.reshape(H, W, 3)
    # DB-99 nadir floor (replaces the NS-inpaint + heavy wv low-pass that produced the
    # 白团 swirl): the abstain/empty cap cells get a STRUCTURELESS per-anchor truth-ring
    # DC plate (reuse ref_med, the road tone just above the cap) — no invented low-freq
    # structure => no swirl, no radial NS streak. The agreeing REAL cap pixels keep the
    # SAME resolution-matched low-pass as before (kills grazing speckle). Honest:
    # real where evidence agrees, flat-honest where it does not. No NS, no grain, no
    # cross-anchor fusion. (Round-2 workflow DB-99; see agent/decision_briefs.md.)
    resid_m = (comp.astype(np.int32).sum(2) < 12)   # DB-106: residual fill = ONLY scene-band-black px (dropped the egoproj term — it painted plate-dark over the real near-car lower body)
    resid_m[:H // 2] = False
    resid_m &= ~fg_occ   # foreground-occluded handled separately (shadow), not as normal ground abstain
    if HOOD_TO_MASK and GROUND_MODE != "off":   # DB-114 ROOT FIX: ego hood = egoproj with NO LiDAR support (self body); a real near-car returns LiDAR (small Zsupport) -> protected. GROUND_RESID NS-inpaint then covers the hood, like the fable5 video.
        _self = egoproj.reshape(H, W) & (Zsupport > HOOD_SUPPORT_PX)
        _self[:H // 2] = False
        resid_m |= _self
    fillzone = capg | resid_m
    # truth-ring asphalt tone (per-anchor, view-dependent -> Fresnel-safe)
    plate_rgb = locals().get('ref_med', None)
    if plate_rgb is None:
        _low = comp[H // 2:].reshape(-1, 3).astype(np.float32); _low = _low[_low.sum(1) >= 12]
        plate_rgb = np.median(_low, axis=0) if len(_low) else np.float32([60, 60, 60])
    plate_rgb = np.asarray(plate_rgb, np.float32)
    _rows = np.arange(H, dtype=np.float32); _r0 = H * 0.55
    _dark = 1.0 - 0.10 * np.clip((_rows - _r0) / max(H - _r0, 1.0), 0.0, 1.0)
    if resid_m.any() and GROUND_MODE != "off":   # evidence-insufficient ground (DB-108): "plate"=honest gray (default) / "inpaint"=NS-inpaint ground-feel (combo, video-era look)
        if GROUND_RESID == "inpaint":
            comp = _cv.inpaint(comp, resid_m.astype(np.uint8) * 255, 8, _cv.INPAINT_NS)
        else:
            _rr = np.nonzero(resid_m)[0]
            comp[resid_m] = np.clip(plate_rgb[None, :] * _dark[_rr][:, None], 0, 255).astype(np.uint8)
    if fg_occ.any() and GROUND_MODE != "off" and not (GROUND_MODE == "worldbev" and WORLDBEV_FILL):
        # honest-SHADOW plate — but on the worldbev+Tgen delivery path the persistent-obstacle
        # footprint is an A-class hole already filled with plausible ground in the map (DB-118 F2:
        # the plate rendered as the user's "black patch" ②); keep the map content there instead.
        comp[fg_occ] = np.clip(plate_rgb * 0.55, 0, 255).astype(np.uint8)
    real_cap = capg & ~resid_m
    if real_cap.any():
        comp_f = comp.astype(np.float32)
        b1_ = _cv.GaussianBlur(comp_f, (0, 0), 3)
        b2_ = _cv.GaussianBlur(comp_f, (0, 0), 9)
        wv_ = np.clip((np.arange(H, dtype=np.float32) - H * 0.55) / (H * 0.45), 0, 1) ** 1.5
        low_ = b1_ * (1 - wv_[:, None, None]) + b2_ * wv_[:, None, None]
        sm_ = comp_f * (1 - wv_[:, None, None]) + low_ * wv_[:, None, None]
        comp[real_cap] = np.clip(sm_[real_cap], 0, 255).astype(np.uint8)
    vismask = comp.copy()
    vismask[fg_occ] = np.array([255, 0, 0], np.uint8)   # DB-101 debug: target-gated foreground (red)
    # DB-101 MIDDLE-ONLY mode: do NOT outpaint the under-determined nadir cap; mask it honestly.
    # The determinable scene band (incl. directly-seen near-ground) is untouched; only the unseen
    # cap becomes a clean neutral abstain (standalone) + an explicit alpha mask (for Cosmos outpaint).
    nadir_alpha = (_capfull.astype(np.uint8) * 255)
    if GROUND_MODE == "mask":
        comp[_capfull] = np.array([48, 48, 48], np.uint8)
        vismask = comp.copy()
    ground_stats = {"cap_px": int(capg.sum()), "filled_px": int(anyg.sum()),
                    "coverage_pct": round(float(anyg.mean() * 100), 1) if len(flat_g) else 0.0,
                    "residual_inpaint_px": int(resid_m.sum()),
                    "fg_occ_px": int(fg_occ.sum()),
                    "nadir_imperfect_px": int((resid_m | fg_occ).sum()),
                    "cand_frames": len(cand_fis),
                    "cand_disp_m": [round(float(disp_g[cand_fis[0]]), 1), round(float(disp_g[cand_fis[-1]]), 1)] if cand_fis else None,
                    "low_coverage_warning": bool(len(flat_g) and anyg.mean() < 0.5)}
    if EGO_BLACK and GROUND_MODE == "off":   # DB-123: black out the ego body in band frames (see flag doc)
        _eb = egoproj.reshape(H, W) & (Zsupport > HOOD_SUPPORT_PX)
        _eb[:H // 2] = False
        if _eb.any():
            _eb = _cv.dilate(_eb.astype(np.uint8), np.ones((EGO_BLACK_DILATE, EGO_BLACK_DILATE), np.uint8)) > 0
            comp[_eb] = 0
        ground_stats["ego_black_px"] = int(_eb.sum())
    if GROUND_MODE == "off":   # DB-171 rule-5 band fallback (post-EGO_BLACK): interior band holes -> ANGULAR sample from the axis-closest in-frame camera whose sample is NOT on that camera's analytic ego mask. Root cause fixed: with annotations present, Zsupport excludes annotated objects' LiDAR returns, so parked-object footprints lose "support" and egoproj&no-support paints them black (the far-from-hood bites). Real hood/body directions land on the E-ego mask and stay black; misfires get their real anchor-time pixels back.
        _nb = comp.astype(np.int32).sum(2) >= 12
        _above = np.maximum.accumulate(_nb, 0)
        _below = np.maximum.accumulate(_nb[::-1], 0)[::-1]
        _fh = (~_nb) & _above & _below
        _fh[:H // 2] = False
        _hy, _hx = np.nonzero(_fh)
        _nfill = 0
        if _hy.size:
            _dh = DIRS.reshape(-1, 3)[_hy * W + _hx]
            _Xh = C[None, :] + _dh * 60.0
            _fbest = np.full(len(_hy), -1, np.int32)
            _fdot = np.full(len(_hy), -2.0, np.float32)
            _pxs = np.zeros(len(_hy), np.float32)
            _pys = np.zeros(len(_hy), np.float32)
            for _ci in range(len(ring_cams)):
                _K5, (_hh5, _ww5) = cals[_ci]
                _Rc5, _tc5 = poses_emc[_ci]
                _Xc5 = (_Rc5.T @ (_Xh - _tc5[None, :]).T).T
                _z5 = _Xc5[:, 2]
                _px5 = (_K5[0, 0] * _Xc5[:, 0] / np.maximum(_z5, 1e-6)).astype(np.float32) + _K5[0, 2]
                _py5 = (_K5[1, 1] * _Xc5[:, 1] / np.maximum(_z5, 1e-6)).astype(np.float32) + _K5[1, 2]
                _ok5 = (_z5 > 0.1) & (_px5 >= 1) & (_px5 < _ww5 - 1) & (_py5 >= 1) & (_py5 < _hh5 - 1)
                if _EIMC is not None and _EIMC[_ci] is not None:
                    _em5 = _EIMC[_ci]
                    _exi5 = np.clip((_px5 / 4).astype(np.int64), 0, _em5.shape[1] - 1)
                    _eyi5 = np.clip((_py5 / 4).astype(np.int64), 0, _em5.shape[0] - 1)
                    _ok5 &= ~(_em5[_eyi5, _exi5] > 0)
                _ax5 = _Rc5 @ np.array([0.0, 0.0, 1.0])
                _dot5 = (_dh @ _ax5).astype(np.float32)
                _pick5 = _ok5 & (_dot5 > _fdot)
                _fbest[_pick5] = _ci
                _fdot[_pick5] = _dot5[_pick5]
                _pxs[_pick5] = _px5[_pick5]
                _pys[_pick5] = _py5[_pick5]
            for _ci, _cam5 in enumerate(ring_cams):
                _sel5 = np.nonzero(_fbest == _ci)[0]
                if not _sel5.size:
                    continue
                _img5 = frame.images[_cam5]
                _gimg5 = np.clip(_img5.astype(np.float32) * np.exp(gains[_ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
                comp[_hy[_sel5], _hx[_sel5]] = np.clip(bilinear(_gimg5, _pxs[_sel5], _pys[_sel5]), 0, 255).astype(np.uint8)
                _nfill += int(_sel5.size)
        ground_stats["band_rule5_filled_px"] = _nfill
    if EMC_RENDER:   # DB-118 speed #1a: display-only A/B render — the 07-01 video batch patched the SAVE to pass but the 7-cam render still burned ~10-20s/frame
        # plain EMC base for the A/B
        embase = np.zeros((len(Xf), 3), np.uint8)
        for ci, cam in enumerate(ring_cams):
            img = frame.images[cam]
            gimg = np.clip(img.astype(np.float32) * np.exp(gains[ci])[None, None, :].astype(np.float32), 0, 255).astype(np.uint8)
            p = proj[ci]
            sel = np.nonzero(fbcam == ci)[0]
            if sel.size:
                embase[sel] = np.clip(bilinear(gimg, p["px"][sel], p["py"][sel]), 0, 255).astype(np.uint8)
        emc = embase.reshape(H, W, 3)
        save_rgb(REMOTE_OUT / f"{run_name}_emc.png", emc)
    save_rgb(REMOTE_OUT / f"{run_name}_segcomposite.png", comp)
    if _ego_rej is not None:   # DB-123 C: exact "blacked because of ego" zone for temporal-fill composition
        _ez = _ego_rej.reshape(H, W) & (comp.astype(np.int32).sum(2) < 12)
        _ez[:H // 2] = False
        save_rgb(REMOTE_OUT / f"{run_name}_egozone.png", np.dstack([(_ez.astype(np.uint8) * 255)] * 3))
    save_rgb(REMOTE_OUT / f"{run_name}_vismask.png", vismask)
    save_rgb(REMOTE_OUT / f"{run_name}_nadirmask.png", np.dstack([nadir_alpha] * 3))
    if FAITH_MASK:   # DB-109 A: the generative-fill region = abstained/plated cap (resid_m) + foreground-occluded ground (fg_occ); lower half only
        _ff = (resid_m | fg_occ); _ff[:H // 2] = False
        save_rgb(REMOTE_OUT / f"{run_name}_faithfill_mask.png", np.dstack([(_ff.astype(np.uint8) * 255)] * 3))
    if EMC_RENDER:
        from PIL import Image as I
        try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
        except Exception: f = ImageFont.load_default()
        rows = []
        for tag, im in (("EMC base", emc), (f"SEG-COMPOSITE (objs={n_handled} unmatched={n_unmatched} secondary={n_secondary}px filled={n_filled}px)", comp)):
            pil = I.fromarray(im).resize((1400, 700))
            bar = I.new("RGB", (1400, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), f"{run_name}  {tag}", (235, 235, 245), font=f)
            o = I.new("RGB", (1400, 724)); o.paste(bar, (0, 0)); o.paste(pil, (0, 24)); rows.append(o)
        board = I.new("RGB", (1400, 724 * 2 + 12), (8, 8, 12))
        yo = 6
        for o in rows: board.paste(o, (0, yo)); yo += o.height
        board.save(REMOTE_OUT / f"{run_name}_db89_board.jpg", quality=90)
    return {"case": run_name, "n_objects_composited": int(n_handled), "n_unmatched": int(n_unmatched),
            "n_secondary_body_px": int(n_secondary), "n_temporal_filled_px": int(n_filled),
            "omc_shifts": omc, "view_morph": morph_report, "ground_fill": ground_stats,
            "color_diag": color_diag_report}


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ultralytics"], timeout=600, check=False)
    reports = [run_case(cs, rn) for cs, rn in CASES]
    OUT["status"] = "db89_completed"; OUT["cases"] = reports; OUT["runtime_s"] = round(time.time() - t0, 2)
except Exception as exc:
    OUT["status"] = "db89_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print("DB89_JSON_BEGIN"); print(json.dumps(OUT, separators=(",", ":"))); print("DB89_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db89_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(8); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int = 2400) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_py())], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    fetched = {}
    names = ["DB89_remote_result.json"]
    for n in CASE_NAMES:
        names += [f"{n}_db89_board.jpg", f"{n}_emc.png", f"{n}_segcomposite.png"]
    for fname in names:
        raw = client.read_file(REMOTE_OUT + "/" + fname, max_size_mb=95)
        if raw is not None:
            (OUT_DIR / fname).write_bytes(raw); fetched[fname] = True
    report = {"job_state": job.get("state"), "n_fetched": len(fetched), "fetched": sorted(fetched),
              "runtime_status": {k: status.get(k) for k in ("runtime_type", "gpu_name", "active_jobs") if k in status}}
    report["secret_hits"] = secret_hits(json.dumps(report))
    return report


if __name__ == "__main__":
    rep = run_remote()
    out = Path.home() / ".waymo2panorama" / "db89_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
