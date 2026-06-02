"""DB-11 / A0 kill-test: COARSE-PLANE LiDAR-DIBR vs rotation-only vs per-pixel-LiDAR.

Question: does a robust fitted plane (ground + facade) reproject adjacent cameras so they
AGREE at the seam (NCC up) — better than rotation-only L1 (convergence=None) and WITHOUT the
per-pixel raw-LiDAR smear (E2)? If the plane can't even raise seam NCC on the mid-range
doubling surface, the whole coarse-plane-DIBR program is downgraded.

Renders all 7 ring cams to ERP under 3 convergence modes and measures, per adjacent seam,
the NCC between the two overlapping cameras' slabs in the supported seam band.
Reuses the existing AV2 loader / renderer / LiDAR-depth / seam-band code.
CPU only.
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
from waymo2panorama.depth.lidar_to_erp_depth import (  # noqa: E402
    project_lidar_to_erp_depth, visualize_depth_map,
)
from parallax_budget_map import _erp_rays, _safe_corr  # noqa: E402

BMW_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
FAR = 1000.0


def load_lidar_feather(log_dir, anchor_ts_ns, max_delta_ms=75.0):
    """Read the nearest LiDAR sweep .feather directly (ego frame xyz), no av2 dep."""
    import pyarrow.feather as pf
    sweep_dir = Path(log_dir) / "sensors" / "lidar"
    sweeps = sorted(sweep_dir.glob("*.feather"))
    if not sweeps:
        raise FileNotFoundError(f"No LiDAR sweeps in {sweep_dir}")
    tss = np.array([int(p.stem) for p in sweeps], dtype=np.int64)
    idx = int(np.argmin(np.abs(tss - anchor_ts_ns)))
    delta_ms = abs(int(tss[idx]) - int(anchor_ts_ns)) / 1e6
    if delta_ms > max_delta_ms:
        raise ValueError(f"nearest LiDAR sweep {delta_ms:.1f}ms > {max_delta_ms}ms")
    tbl = pf.read_table(sweeps[idx])
    xyz = np.stack([tbl.column("x").to_numpy(), tbl.column("y").to_numpy(),
                    tbl.column("z").to_numpy()], axis=1).astype(np.float64)
    return xyz, int(tss[idx]), float(delta_ms)


# ---------------- plane fitting ----------------
def ransac_plane(pts, thresh, iters, seed):
    rng = np.random.default_rng(seed)
    n = len(pts)
    if n < 50:
        return None
    best_cnt, best_inl = 0, None
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        p0, p1, p2 = pts[idx]
        nrm = np.cross(p1 - p0, p2 - p0)
        ln = np.linalg.norm(nrm)
        if ln < 1e-6:
            continue
        nrm = nrm / ln
        d = float(nrm @ p0)
        dist = np.abs(pts @ nrm - d)
        cnt = int((dist < thresh).sum())
        if cnt > best_cnt:
            best_cnt, best_inl = cnt, dist < thresh
    if best_inl is None or best_cnt < 50:
        return None
    P = pts[best_inl]
    c = P.mean(0)
    _, _, Vt = np.linalg.svd(P - c, full_matrices=False)
    nrm = Vt[-1] / np.linalg.norm(Vt[-1])
    d = float(nrm @ c)
    dist = np.abs(pts @ nrm - d)
    inl = dist < thresh
    return {"n": nrm.astype(np.float64), "d": d, "inl": inl,
            "n_inl": int(inl.sum()), "resid_m": float(dist[inl].std())}


def fit_planes(pts, seed=0):
    """pts: (N,3) ego frame (x fwd, y left, z up). Returns ground plane + facade planes."""
    z = pts[:, 2]
    rng = np.linalg.norm(pts[:, :2], axis=1)
    inrange = (rng > 3.0) & (rng < 50.0)
    # ground: low points
    z_lo = np.percentile(z, 35)
    gmask = inrange & (z < z_lo + 0.3)
    ground = ransac_plane(pts[gmask], thresh=0.15, iters=400, seed=seed)
    if ground is not None and abs(ground["n"][2]) < 0.85:
        ground = None  # not horizontal -> reject
    # facades: non-ground, elevated, vertical, per azimuth sector
    if ground is not None:
        gd = np.abs(pts @ ground["n"] - ground["d"])
        nonground = inrange & (gd > 0.5)
    else:
        nonground = inrange & (z > z_lo + 0.8)
    theta_pt = np.arctan2(pts[:, 1], pts[:, 0])  # ego azimuth
    facades = []
    n_sect = 12
    for s in range(n_sect):
        lo = -np.pi + s * (2 * np.pi / n_sect)
        hi = lo + (2 * np.pi / n_sect)
        sm = nonground & (theta_pt >= lo) & (theta_pt < hi)
        if int(sm.sum()) < 400:
            continue
        pl = ransac_plane(pts[sm], thresh=0.20, iters=300, seed=seed + s + 1)
        if pl is None or pl["n_inl"] < 300:
            continue
        if abs(pl["n"][2]) > 0.45:  # not vertical -> skip
            continue
        # azimuth window of this plane's inliers (in ego theta)
        sub = pts[sm][pl["inl"]]
        th = np.arctan2(sub[:, 1], sub[:, 0])
        facades.append({"n": pl["n"], "d": pl["d"], "n_inl": pl["n_inl"],
                        "resid_m": pl["resid_m"],
                        "theta_lo": float(th.min()), "theta_hi": float(th.max())})
    return ground, facades


def build_plane_convergence(ground, facades, erp_hw):
    H, W = erp_hw
    rays = _erp_rays(erp_hw).reshape(-1, 3)  # (HW,3) ego unit rays
    theta = np.arctan2(rays[:, 1], rays[:, 0])
    lam = np.full(rays.shape[0], FAR, dtype=np.float64)

    def apply(n, d, sel=None):
        nr = rays @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            l = d / nr
        valid = np.isfinite(l) & (l > 0.5) & (l < 80.0) & (np.abs(nr) > 1e-3)
        if sel is not None:
            valid &= sel
        upd = valid & (l < lam)
        lam[upd] = l[upd]

    if ground is not None:
        apply(ground["n"], ground["d"])
    for f in facades:
        sel = (theta >= f["theta_lo"] - 0.05) & (theta <= f["theta_hi"] + 0.05)
        apply(f["n"], f["d"], sel)
    return lam.reshape(H, W).astype(np.float64)


# ---------------- viz helpers ----------------
def _g(rgb):
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)


def _rw(img, w):
    h = round(img.shape[0] * w / img.shape[1])
    return cv2.resize(np.clip(img, 0, 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_AREA)


def _label(img, txt):
    band = np.zeros((30, img.shape[1], 3), np.uint8)
    cv2.putText(band, txt, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return np.vstack([band, img])


def _save(path, rgb, q=90):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, q])


def crop_seams(hards, ref_w, near_mask, band_hw, rows_band=(300, 740)):
    cand = []
    for i, j in RING_PAIRS:
        band, _ = build_voronoi_seam_band(ref_w[i].astype(np.float32), ref_w[j].astype(np.float32),
                                          band_half_width=band_hw, threshold=1e-6)
        m = band & near_mask
        if int(m.sum()) < 200:
            continue
        cand.append((int(m.sum()), int(np.median(np.where(m)[1])), (i, j)))
    cand.sort(reverse=True)
    r0, r1 = rows_band
    cols = []
    for npx, cc, (i, j) in cand[:2]:
        c0, c1 = max(0, cc - 220), min(hards["none_rotonly_L1"].shape[1], cc + 220)
        stack = [_label(hards[n][r0:r1, c0:c1], f"{lab} seam{i}-{j}")
                 for n, lab in [("none_rotonly_L1", "L1 rot-only"), ("plane_dibr", "PLANE-DIBR"), ("lidar_perpixel_E2", "per-pixel E2")]]
        cols.append(np.vstack(stack))
    if not cols:
        return None
    h = max(c.shape[0] for c in cols)
    cols = [np.vstack([c, np.zeros((h - c.shape[0], c.shape[1], 3), np.uint8)]) if c.shape[0] < h else c for c in cols]
    return np.hstack(cols)


def render_all(frame, erp_hw, conv):
    slabs, weights, alphas, centers = [], [], [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        cm = conv
        rgb, al, w = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam,
                                          erp_hw=erp_hw, convergence_distance_m=cm)
        slabs.append(rgb); weights.append(w); alphas.append(al)
        centers.append(cb.T_ego_cam[:3, 3].astype(np.float64))
    return slabs, weights, alphas, centers


def seam_ncc(slabs, weights, alphas, near_mask, band_hw):
    grays = [_g(s) for s in slabs]
    rows = []
    agg_band, agg_near = [], []
    for i, j in RING_PAIRS:
        wi, wj = weights[i].astype(np.float32), weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, _ = build_voronoi_seam_band(wi, wj, band_half_width=band_hw, threshold=1e-6)
        m = band & overlap & alphas[i] & alphas[j]
        m_near = m & near_mask
        ncc_b = _safe_corr(grays[i], grays[j], m)
        ncc_n = _safe_corr(grays[i], grays[j], m_near)
        rows.append({"pair": [int(i), int(j)], "band_px": int(m.sum()),
                     "near_px": int(m_near.sum()),
                     "ncc_band": ncc_b, "ncc_near": ncc_n})
        if ncc_b is not None:
            agg_band.append(ncc_b)
        if ncc_n is not None:
            agg_near.append(ncc_n)
    return rows, (float(np.mean(agg_band)) if agg_band else None,
                  float(np.mean(agg_near)) if agg_near else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--uuid", default=BMW_UUID)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a0_plane_dibr_probe"))
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-hw", type=int, default=48)
    ap.add_argument("--review-w", type=int, default=1200)
    args = ap.parse_args()
    erp_hw = (args.erp_h, args.erp_w)
    out = args.out_dir; out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    loader = AV2RingLoader(args.av2_root / args.uuid)
    ts = loader.anchor_timestamps_ns()
    anchor_ts = ts[args.anchor]
    frame = loader.load_synced_frame(anchor_ts)
    print(f"[load] {args.uuid} a{args.anchor:03d} ts={anchor_ts}", flush=True)

    pts, sweep_ts, dms = load_lidar_feather(args.av2_root / args.uuid, anchor_ts, max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    print(f"[lidar] {pts.shape[0]} pts, delta {dms:.1f} ms", flush=True)

    ground, facades = fit_planes(pts, seed=0)
    print(f"[planes] ground={'yes' if ground else 'NO'} facades={len(facades)}", flush=True)

    conv_plane = build_plane_convergence(ground, facades, erp_hw)
    near_mask = conv_plane < 80.0  # where a plane gave a near/mid depth (the doubling region)
    depth_lidar, depth_sum = project_lidar_to_erp_depth(pts, erp_hw=erp_hw, min_range_m=0.5,
                                                        max_range_m=80.0, densify_radius_px=10, fill_far_m=FAR)

    modes = {"none_rotonly_L1": None, "plane_dibr": conv_plane, "lidar_perpixel_E2": depth_lidar}
    results, hards = {}, {}
    ref_w = None
    for name, conv in modes.items():
        slabs, weights, alphas, centers = render_all(frame, erp_hw, conv)
        rows, (mb, mn) = seam_ncc(slabs, weights, alphas, near_mask, args.band_hw)
        hards[name] = hard_select(slabs, weights)
        if name == "none_rotonly_L1":
            ref_w = weights
        results[name] = {"per_seam": rows, "mean_ncc_band": mb, "mean_ncc_near": mn}
        print(f"[mode {name}] mean_ncc_band={mb} mean_ncc_near(doubling)={mn}", flush=True)

    crop_img = crop_seams(hards, ref_w, near_mask, args.band_hw)
    if crop_img is not None:
        _save(out / "a0_seam_crops.jpg", crop_img, q=95)

    # review montage: hard_select under 3 modes + depth viz
    rows_img = [_label(_rw(hards["none_rotonly_L1"], args.review_w), "L1 rotation-only (single-center)"),
                _label(_rw(hards["plane_dibr"], args.review_w), "COARSE-PLANE DIBR (NEW)"),
                _label(_rw(hards["lidar_perpixel_E2"], args.review_w), "per-pixel raw LiDAR (E2)")]
    _save(out / "a0_hardselect_3modes.jpg", np.vstack(rows_img))
    _save(out / "a0_plane_depth.jpg", _rw(visualize_depth_map(conv_plane, log_clip_m=80.0), args.review_w))
    _save(out / "a0_lidar_depth.jpg", _rw(visualize_depth_map(depth_lidar, log_clip_m=80.0), args.review_w))

    diag = {
        "anchor": f"{args.uuid}_a{args.anchor:03d}", "lidar_pts": int(pts.shape[0]), "lidar_delta_ms": float(dms),
        "ground_plane": None if ground is None else {"n": ground["n"].tolist(), "d": ground["d"], "n_inl": ground["n_inl"], "resid_m": ground["resid_m"]},
        "n_facades": len(facades),
        "facades": [{"n": f["n"].tolist(), "d": f["d"], "n_inl": f["n_inl"], "resid_m": f["resid_m"]} for f in facades],
        "near_mask_frac_erp": float(near_mask.mean()),
        "modes": results,
        "VERDICT_numbers": {
            m: {"mean_ncc_band": results[m]["mean_ncc_band"], "mean_ncc_near_doubling": results[m]["mean_ncc_near"]}
            for m in modes
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    with open(out / "a0_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2, ensure_ascii=False)
    print("[VERDICT]", json.dumps(diag["VERDICT_numbers"], indent=2), flush=True)
    print(f"[saved] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
