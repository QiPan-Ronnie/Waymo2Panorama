"""DB-11 / A1 — the FULL Google-Street-View-style composited pipeline (with our LiDAR).

Fixes A0's 3 failures by doing the steps Google does that A0 skipped, using OPEN-SOURCE
components where they exist (cv2.detail stitching pipeline, cv2 DIS flow, pyransac3d):

  1. coarse-plane reproject  -> our render_camera_to_erp (reviewed) with a LiDAR plane depth map
  2. (optional) DIS optical-flow residual align in the overlap band   [--flow]
  3. object-aware seam routing -> cv2.detail GraphCutSeamFinder (+ non-planar objects forced to
     their single L1 camera so the seam can't cut through them)
  4. multiband blend          -> cv2.detail MultiBandBlender
  5. CONFINE + COMPOSITE onto L1 (far field BYTE-IDENTICAL to L1; never hard-replace) -> our
     _seam_alpha / _label_and_base; non-planar near objects kept as L1 single-camera.

CPU. Plane fit via pyransac3d (open-source).
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.blending.seam_confined import _label_and_base, _seam_alpha  # noqa: E402 (reviewed)
from parallax_budget_map import _erp_rays, _safe_corr  # noqa: E402
from run_a0_plane_dibr_probe import load_lidar_feather  # noqa: E402 (reviewed feather reader)
from erp_geometry_metric import relative_warp  # noqa: E402 (validated far-field ruler)

BMW_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
FAR = 1000.0


# ---------------- plane fitting (open-source pyransac3d) ----------------
def fit_planes_p3(pts, seed=0):
    import pyransac3d as p3
    z = pts[:, 2]
    rng = np.linalg.norm(pts[:, :2], axis=1)
    inrange = (rng > 3.0) & (rng < 50.0)
    z_lo = np.percentile(z, 35)
    # ground
    gsel = inrange & (z < z_lo + 0.3)
    ground = None
    if int(gsel.sum()) > 200:
        eq, inl = p3.Plane().fit(pts[gsel], thresh=0.15, maxIteration=500)
        n = np.array(eq[:3], float); nn = np.linalg.norm(n)
        if nn > 1e-6:
            n /= nn; d = -eq[3] / nn  # plane n·X = d
            if abs(n[2]) > 0.85:
                resid = float(np.abs(pts[gsel] @ n - d)[inl].std()) if len(inl) else 0.0
                ground = {"n": n, "d": float(d), "n_inl": int(len(inl)), "resid_m": resid}
    # facades per azimuth sector
    if ground is not None:
        gd = np.abs(pts @ ground["n"] - ground["d"])
        nong = inrange & (gd > 0.5)
    else:
        nong = inrange & (z > z_lo + 0.8)
    th = np.arctan2(pts[:, 1], pts[:, 0])
    facades, n_sect = [], 12
    for s in range(n_sect):
        lo = -np.pi + s * (2 * np.pi / n_sect); hi = lo + 2 * np.pi / n_sect
        sm = nong & (th >= lo) & (th < hi)
        if int(sm.sum()) < 400:
            continue
        sub = pts[sm]
        eq, inl = p3.Plane().fit(sub, thresh=0.20, maxIteration=300)
        if len(inl) < 300:
            continue
        n = np.array(eq[:3], float); nn = np.linalg.norm(n)
        if nn < 1e-6 or abs(n[2] / nn) > 0.45:
            continue
        n /= nn; d = -eq[3] / nn
        thi = np.arctan2(sub[inl][:, 1], sub[inl][:, 0])
        facades.append({"n": n, "d": float(d), "n_inl": int(len(inl)),
                        "resid_m": float(np.abs(sub @ n - d)[inl].std()),
                        "theta_lo": float(thi.min()), "theta_hi": float(thi.max())})
    return ground, facades


def build_plane_convergence(ground, facades, erp_hw):
    H, W = erp_hw
    rays = _erp_rays(erp_hw).reshape(-1, 3)
    theta = np.arctan2(rays[:, 1], rays[:, 0])
    lam = np.full(rays.shape[0], FAR)

    def apply(n, d, sel=None):
        nr = rays @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            l = d / nr
        valid = np.isfinite(l) & (l > 0.5) & (l < 80.0) & (np.abs(nr) > 1e-3)
        if sel is not None:
            valid &= sel
        upd = valid & (l < lam); lam[upd] = l[upd]

    if ground is not None:
        apply(ground["n"], ground["d"])
    for f in facades:
        apply(f["n"], f["d"], (theta >= f["theta_lo"] - 0.05) & (theta <= f["theta_hi"] + 0.05))
    return lam.reshape(H, W)


def off_plane_object_erp(pts, ground, facades, erp_hw, off_thresh=0.6, dilate_px=9):
    """ERP mask of NON-PLANAR near objects: LiDAR points far from ALL fitted planes,
    within range, projected to ERP. These get kept as L1 single-camera (seam routed around)."""
    H, W = erp_hw
    planes = ([ground] if ground else []) + facades
    if not planes:
        return np.zeros((H, W), bool)
    rng = np.linalg.norm(pts, axis=1)
    near = (rng > 1.0) & (rng < 40.0)
    P = pts[near]
    dmin = np.full(len(P), 1e9)
    for pl in planes:
        dmin = np.minimum(dmin, np.abs(P @ pl["n"] - pl["d"]))
    obj = P[dmin > off_thresh]
    if len(obj) == 0:
        return np.zeros((H, W), bool)
    # project to ERP (ego origin spherical)
    x, y, z = obj[:, 0], obj[:, 1], obj[:, 2]
    th = np.arctan2(y, x); ph = np.arctan2(z, np.sqrt(x * x + y * y))
    u = ((np.pi - th) / (2 * np.pi) * W).astype(int) % W
    v = ((np.pi / 2 - ph) / np.pi * H).astype(int).clip(0, H - 1)
    m = np.zeros((H, W), np.uint8); m[v, u] = 1
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1, dilate_px * 2 + 1))
    return cv2.dilate(m, k).astype(bool)


# ---------------- OSS seam + blend (cv2.detail) ----------------
def detail_seam_blend(slabs_u8, masks_u8, seam_scale=0.33):
    """OSS seam (GraphCut, at reduced scale for speed — as OpenCV's stitcher does) + full-res
    MultiBand blend."""
    H, W = slabs_u8[0].shape[:2]
    hs, ws = max(1, int(H * seam_scale)), max(1, int(W * seam_scale))
    corners_s = [(0, 0)] * len(slabs_u8)
    imgs_s = [cv2.resize(s, (ws, hs), interpolation=cv2.INTER_AREA).astype(np.float32) for s in slabs_u8]
    masks_s = [cv2.resize(m, (ws, hs), interpolation=cv2.INTER_NEAREST) for m in masks_u8]
    sf = cv2.detail_GraphCutSeamFinder("COST_COLOR_GRAD")
    seam_s = sf.find(imgs_s, corners_s, [m.copy() for m in masks_s])
    seam_s = [np.asarray(m.get() if hasattr(m, "get") else m) for m in seam_s]
    seam = []  # upscale seam masks to full res, clamp to full-res validity
    for ms, mfull in zip(seam_s, masks_u8):
        up = cv2.resize(ms, (W, H), interpolation=cv2.INTER_NEAREST)
        seam.append((((up > 0) & (mfull > 0)).astype(np.uint8)) * 255)
    bl = cv2.detail_MultiBandBlender(); bl.prepare((0, 0, W, H))
    for s, m in zip(slabs_u8, seam):
        bl.feed(s.astype(np.int16), m, (0, 0))
    res, _ = bl.blend(None, None)
    return np.clip(np.asarray(res), 0, 255).astype(np.uint8), seam


def dis_flow_align(slabs, alphas, band, ref):
    """Optional: warp each plane-DIBR slab toward the L1 reference in the band via DIS flow."""
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    gref = cv2.cvtColor(np.clip(ref, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    out = []
    yy, xx = np.meshgrid(np.arange(slabs[0].shape[1]), np.arange(slabs[0].shape[0]))
    for s, a in zip(slabs, alphas):
        g = cv2.cvtColor(np.clip(s, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
        fl = dis.calc(g, gref, None)  # flow from slab->ref
        m = (a & band)
        fl[~m] = 0  # only warp inside the band where this cam is valid
        mapx = (xx + fl[..., 0]).astype(np.float32); mapy = (yy + fl[..., 1]).astype(np.float32)
        w = cv2.remap(s.astype(np.float32), mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        out.append(np.where(m[..., None], w, s))
    return out


def _rw(img, w):
    h = round(img.shape[0] * w / img.shape[1])
    return cv2.resize(np.clip(img, 0, 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_AREA)


def _label(img, txt):
    band = np.zeros((30, img.shape[1], 3), np.uint8)
    cv2.putText(band, txt, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return np.vstack([band, img])


def _save(path, rgb, q=92):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, q])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--uuid", default=BMW_UUID)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline"))
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-hw", type=int, default=64)
    ap.add_argument("--flow", action="store_true", help="enable DIS optical-flow residual align (step 2)")
    ap.add_argument("--review-w", type=int, default=1300)
    args = ap.parse_args()
    erp_hw = (args.erp_h, args.erp_w); out = args.out_dir; out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    loader = AV2RingLoader(args.av2_root / args.uuid)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[args.anchor])
    pts, _, dms = load_lidar_feather(args.av2_root / args.uuid, ts[args.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    print(f"[load] {args.uuid} a{args.anchor:03d}  lidar {pts.shape[0]} pts", flush=True)

    ground, facades = fit_planes_p3(pts)
    print(f"[planes] ground={'y' if ground else 'N'} facades={len(facades)}", flush=True)
    conv = build_plane_convergence(ground, facades, erp_hw)
    obj_mask = off_plane_object_erp(pts, ground, facades, erp_hw)
    print(f"[obj] non-planar near-object ERP frac={obj_mask.mean()*100:.2f}%", flush=True)

    # render L1 (rotation-only) and plane-DIBR
    l1_slabs, l1_w, pl_slabs, pl_w, alphas = [], [], [], [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, a1, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        rp, ap_, wp = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=conv)
        l1_slabs.append(r1); l1_w.append(w1); pl_slabs.append(rp); pl_w.append(wp); alphas.append(ap_)
    L1 = hard_select(l1_slabs, l1_w)
    argmax, valid, base = _label_and_base(l1_slabs, l1_w)  # L1 base + label
    alpha, boundary = _seam_alpha(argmax, valid, args.band_hw)
    band = alpha > 0

    # plane-DIBR slabs to use in band; force non-planar objects OUT of plane-DIBR masks (keep L1 there)
    masks = []
    for k, a in enumerate(alphas):
        m = (a & ~obj_mask).astype(np.uint8) * 255
        masks.append(m)
    slabs_for_stitch = pl_slabs
    if args.flow:
        slabs_for_stitch = dis_flow_align(pl_slabs, alphas, band, L1)

    stitched, seam_masks = detail_seam_blend([np.clip(s, 0, 255).astype(np.uint8) for s in slabs_for_stitch], masks)

    # composite: L1 base, feather to stitched inside band ONLY where stitched has content
    # (fixes dark/black patches: where plane-DIBR slabs are empty, keep L1 instead of darkening).
    vs = (stitched.sum(2) > 0)
    a3 = (alpha * vs.astype(np.float32))[..., None]
    res = base.astype(np.float32) * (1 - a3) + stitched.astype(np.float32) * a3
    res = np.clip(res, 0, 255).astype(np.uint8)
    res[obj_mask] = base[obj_mask]              # non-planar near objects kept as L1 single-cam
    nob = a3[..., 0] == 0.0
    res[nob] = base[nob]                        # far field + empty-stitch + objects = byte-identical L1
    tag = "A1_flow" if args.flow else "A1_core"

    # far-field fidelity vs L1 (must be ~0 outside band by construction)
    try:
        rw = relative_warp(L1, res)
        rw = {k: (float(v) if np.isscalar(v) or isinstance(v, (int, float)) else v) for k, v in rw.items()} if isinstance(rw, dict) else {"val": float(rw)}
    except Exception as e:
        rw = {"error": str(e)}

    # outputs: stacked review L1 vs A1; crops at the 2 worst-parallax seams
    _save(out / f"{tag}_L1_vs_result.jpg", np.vstack([_label(_rw(L1, args.review_w), "L1 hard_select"),
                                                      _label(_rw(res, args.review_w), f"{tag} (plane-DIBR + OSS seam/blend, confined)")]))
    # crops
    cand = []
    for i, j in RING_PAIRS:
        b, _ = build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=args.band_hw, threshold=1e-6)
        bm = b & (conv < 80.0)
        if int(bm.sum()) < 200:
            continue
        cand.append((int(bm.sum()), int(np.median(np.where(bm)[1])), (i, j)))
    cand.sort(reverse=True)
    crop_cols = []
    for npx, cc, (i, j) in cand[:2]:
        c0, c1 = max(0, cc - 230), min(args.erp_w, cc + 230)
        crop_cols.append(np.vstack([_label(L1[300:760, c0:c1], f"L1 seam{i}-{j}"),
                                    _label(res[300:760, c0:c1], f"{tag} seam{i}-{j}")]))
    if crop_cols:
        h = max(c.shape[0] for c in crop_cols)
        crop_cols = [np.vstack([c, np.zeros((h - c.shape[0], c.shape[1], 3), np.uint8)]) if c.shape[0] < h else c for c in crop_cols]
        _save(out / f"{tag}_seam_crops.jpg", np.hstack(crop_cols), q=95)
    # object mask viz
    ov = L1.copy(); ov[obj_mask] = (0.5 * ov[obj_mask] + np.array([255, 0, 255]) * 0.5).astype(np.uint8)
    _save(out / f"{tag}_objmask.jpg", _rw(ov, args.review_w))

    diag = {"tag": tag, "uuid": args.uuid, "anchor": args.anchor, "lidar_pts": int(pts.shape[0]),
            "ground": bool(ground), "n_facades": len(facades), "obj_frac": float(obj_mask.mean()),
            "far_field_relative_warp_vs_L1": rw, "runtime_s": round(time.time() - t0, 1)}
    with open(out / f"{tag}_diag.json", "w") as f:
        json.dump(diag, f, indent=2, default=str)
    print("[diag]", json.dumps(diag, indent=2, default=str), flush=True)
    print(f"[saved] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
