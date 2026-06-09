"""DB-81: B1 photometric layer on the db80 centroid base (CPU, no A100, no generation).

P1 — cross-camera colour harmonisation supervised by LiDAR correspondences:
  every accumulated static LiDAR point seen by >=2 ring cams gives a colour pair
  (bilinear RGB in each camera); solve per-camera per-channel log-gains c_i with
  ring-closed least squares (sum c_i = 0), apply exp(c_i) at render time.
P2 — per-channel lateral chromatic-aberration alignment:
  per camera, grid-search radial coefficient k in r' = r*(1 + k*r_n^2) for R and B
  against G by maximising gradient NCC on the outer annulus; remap R/B before render.
Renders cen_depth (DB-80 step B) vs cen_depth+B1, with seam-step and fringe closeups.
Self-orchestrating (db79/db80 pattern): ONE bounded /exec, fetch, Read-verify.
"""
from __future__ import annotations
import base64, json, time, urllib.parse
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db81_photometric"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db81_photometric"
RESULT = REMOTE_OUT + "/DB81_remote_result.json"

CASE_NAMES = ["02a00399_a000_bmw", "0bae3b5e_a030_clean_far", "2c652f9e_a030_highway",
              "9f871fb4_a030_downtown", "fbee355f_a030_crowd"]


def remote_py() -> str:
    code = r'''
import json, math, pathlib, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_RESULT = pathlib.Path("__RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
H, W = 1024, 2048; EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"),
         ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far"),
         ("2c652f9e-8db8-3572-aa49-fae1344a875b:30:highway", "2c652f9e_a030_highway"),
         ("9f871fb4-3b8e-34b3-9161-ed961e71a6da:30:downtown", "9f871fb4_a030_downtown"),
         ("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "fbee355f_a030_crowd")]
WINDOW = 10; DMIN, DMAX = 1.5, 80.0; STATIC_DISP_M = 0.5
SAT_LO, SAT_HI = 10, 245
CA_KS = np.linspace(-4e-3, 4e-3, 17)   # normalized-radius^2 coefficient grid
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}
OUT = {"phase": "db81_photometric", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"photometric_only": True, "generation": False, "learning": False, "a100": False}}

sys.path.insert(0, "/content/waymo2panorama/scripts/phase3"); sys.path.insert(0, "/content/waymo2panorama/code")


def save_rgb(path, arr):
    import cv2
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(arr, 0, 255).astype("uint8"), cv2.COLOR_RGB2BGR))


def erp_dirs():
    u = np.arange(W); v = np.arange(H); uu, vv = np.meshgrid(u, v)
    theta = np.pi - (uu + 0.5) / W * 2 * np.pi; phi = np.pi / 2 - (vv + 0.5) / H * np.pi
    cph = np.cos(phi)
    return np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], -1).astype(np.float64)


def load_all(case_spec):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir); ts = loader.anchor_timestamps_ns()[anchor_idx]; frame = loader.load_synced_frame(ts)
    p = pd.read_feather(log_dir / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
    ti = p["timestamp_ns"].to_numpy(np.int64); t0 = int(ti[0]); tss = (ti - t0).astype(np.float64)
    quat = p[["qx", "qy", "qz", "qw"]].to_numpy(); tx = p[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
    keep = np.concatenate([[True], np.diff(tss) > 0]); tss, quat, tx = tss[keep], quat[keep], tx[keep]
    slerp = Slerp(tss, Rotation.from_quat(quat)); lo, hi = tss.min(), tss.max()
    def cte(t):
        tc = float(np.clip(float(int(t) - t0), lo, hi)); return slerp(tc).as_matrix(), np.array([np.interp(tc, tss, tx[:, i]) for i in range(3)])
    def tri(ta):
        tc = np.clip((np.asarray(ta, np.int64) - t0).astype(np.float64), lo, hi); return np.stack([np.interp(tc, tss, tx[:, i]) for i in range(3)], 1)
    ann = pd.read_feather(log_dir / "annotations.feather") if (log_dir / "annotations.feather").exists() else None
    return log_dir, ts, frame, list(RING_CAMS_7), cte, tri, ann


def moving_tracks(ann, t_lo, t_hi):
    if ann is None or "track_uuid" not in ann.columns: return set()
    sub = ann[(ann["timestamp_ns"] >= t_lo) & (ann["timestamp_ns"] <= t_hi)]
    mv = set()
    for uid, g in sub.groupby("track_uuid"):
        if "category" in g.columns and str(g["category"].iloc[0]).upper() not in DYN_CATS: continue
        c = g[["tx_m", "ty_m", "tz_m"]].to_numpy(float)
        if len(c) >= 2 and float(np.linalg.norm(c.max(0) - c.min(0))) > STATIC_DISP_M: mv.add(uid)
    return mv


def boxes_at(ann, ts, moving):
    from scipy.spatial.transform import Rotation
    if ann is None: return []
    tss = ann["timestamp_ns"].to_numpy(np.int64); nt = np.unique(tss)[np.argmin(np.abs(np.unique(tss) - ts))]
    out = []
    for _, r in ann[ann["timestamp_ns"] == nt].iterrows():
        if "category" in ann.columns and str(r["category"]).upper() not in DYN_CATS: continue
        if "track_uuid" in ann.columns and r["track_uuid"] not in moving: continue
        out.append((np.array([r["tx_m"], r["ty_m"], r["tz_m"]], float), np.array([r["length_m"], r["width_m"], r["height_m"]], float),
                    Rotation.from_quat([r["qx"], r["qy"], r["qz"], r["qw"]]).as_matrix()))
    return out


def remove_dyn(pts, boxes, pad=0.3):
    if not boxes or len(pts) == 0: return np.ones(len(pts), bool)
    keep = np.ones(len(pts), bool)
    for c, sz, Rb in boxes:
        loc = (pts - c) @ Rb; half = sz / 2 + pad
        keep &= ~((np.abs(loc[:, 0]) < half[0]) & (np.abs(loc[:, 1]) < half[1]) & (np.abs(loc[:, 2]) < half[2]))
    return keep


def accumulate_lidar(log_dir, anchor_ts, cte, tri, ann):
    import pandas as pd
    sweeps = sorted((log_dir / "sensors" / "lidar").glob("*.feather"))
    stss = np.array([int(p.stem) for p in sweeps], np.int64); ai = int(np.argmin(np.abs(stss - anchor_ts)))
    t_lo, t_hi = int(stss[max(0, ai - WINDOW)]), int(stss[min(len(stss) - 1, ai + WINDOW)])
    moving = moving_tracks(ann, t_lo, t_hi)
    Ra, ta = cte(anchor_ts); acc = []
    for si in range(max(0, ai - WINDOW), min(len(sweeps), ai + WINDOW + 1)):
        sts = int(stss[si]); df = pd.read_feather(sweeps[si]); xyz = df[["x", "y", "z"]].to_numpy(np.float64)
        off = df["offset_ns"].to_numpy(np.int64) if "offset_ns" in df.columns else np.zeros(len(df), np.int64)
        keep = remove_dyn(xyz, boxes_at(ann, sts, moving)); xyz, off = xyz[keep], off[keep]
        Rsw, _ = cte(sts); city = (Rsw @ xyz.T).T + tri((sts + off).astype(np.int64))
        acc.append((city - ta) @ Ra)
    return np.concatenate(acc, 0) if acc else np.zeros((0, 3))


def bilinear(img, px, py):
    x0 = np.floor(px).astype(np.int64); y0 = np.floor(py).astype(np.int64)
    fx = (px - x0)[:, None]; fy = (py - y0)[:, None]
    hh, ww = img.shape[:2]
    x0c = np.clip(x0, 0, ww - 2); y0c = np.clip(y0, 0, hh - 2)
    a = img[y0c, x0c].astype(np.float64); b = img[y0c, x0c + 1].astype(np.float64)
    c = img[y0c + 1, x0c].astype(np.float64); d = img[y0c + 1, x0c + 1].astype(np.float64)
    return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy


def collect_pairs(frame, ring_cams, lidar, C):
    """LiDAR cross-camera colour pairs: (cam_i, cam_j, rgb_i, rgb_j) for co-observed points."""
    sub = lidar[np.random.RandomState(0).choice(len(lidar), min(len(lidar), 200000), replace=False)]
    Q = sub - C[None, :]; n = np.linalg.norm(Q, axis=1)
    m = (n > DMIN) & (n < DMAX); sub = sub[m]
    obs = []
    for ci, cam in enumerate(ring_cams):
        cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
        Tci = np.linalg.inv(T); img = frame.images[cam]; hh, ww = img.shape[:2]
        Xc = (Tci[:3, :3] @ sub.T).T + Tci[:3, 3]; z = Xc[:, 2]
        px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]; py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
        ok = (z > 0.5) & (px >= 2) & (px < ww - 2) & (py >= 2) & (py < hh - 2)
        rgb = np.zeros((len(sub), 3)); rgb[ok] = bilinear(img, px[ok], py[ok])
        good = ok & (rgb.min(1) > SAT_LO) & (rgb.max(1) < SAT_HI)
        obs.append((good, rgb))
    pairs = []
    nc = len(ring_cams)
    for i in range(nc):
        for j in range(i + 1, nc):
            both = obs[i][0] & obs[j][0]
            if both.sum() < 50: continue
            pairs.append((i, j, obs[i][1][both], obs[j][1][both]))
    return pairs


def solve_gains(pairs, nc):
    """Per-channel log-gain c (nc,3), sum c = 0, weighted by pair count."""
    gains = np.zeros((nc, 3))
    stats = {}
    for ch in range(3):
        A = np.zeros((nc, nc)); b = np.zeros(nc)
        for i, j, ri, rj in pairs:
            li = np.log(np.maximum(ri[:, ch], 1.0)); lj = np.log(np.maximum(rj[:, ch], 1.0))
            wgt = len(li); dm = float(np.median(lj - li))
            A[i, i] += wgt; A[j, j] += wgt; A[i, j] -= wgt; A[j, i] -= wgt
            b[i] += wgt * dm; b[j] -= wgt * dm
        A += np.ones((nc, nc))   # gauge: sum c = 0
        c = np.linalg.solve(A, b)
        gains[:, ch] = c - c.mean()
    # before/after pair colour difference (median |log ratio| per channel, averaged)
    def pair_diff(g):
        ds = []
        for i, j, ri, rj in pairs:
            li = np.log(np.maximum(ri, 1.0)) + g[i][None, :]
            lj = np.log(np.maximum(rj, 1.0)) + g[j][None, :]
            ds.append(np.abs(li - lj).mean(1))
        return float(np.median(np.concatenate(ds)))
    stats["pair_logdiff_before"] = pair_diff(np.zeros((nc, 3)))
    stats["pair_logdiff_after"] = pair_diff(gains)
    stats["reduction_frac"] = 1.0 - stats["pair_logdiff_after"] / max(stats["pair_logdiff_before"], 1e-9)
    return gains, stats


def estimate_ca(img):
    """Per-channel radial coefficient k (R and B vs G) maximizing gradient NCC on the outer annulus."""
    import cv2
    hh, ww = img.shape[:2]
    cx, cy = ww / 2, hh / 2; rmax = math.hypot(cx, cy)
    yy, xx = np.mgrid[0:hh, 0:ww]
    rn2 = (((xx - cx) ** 2 + (yy - cy) ** 2) / rmax ** 2).astype(np.float32)
    annulus = rn2 > 0.45
    small = cv2.resize(img, (ww // 2, hh // 2), interpolation=cv2.INTER_AREA)  # speed: estimate at half res
    hh2, ww2 = small.shape[:2]; cx2, cy2 = ww2 / 2, hh2 / 2; rmax2 = math.hypot(cx2, cy2)
    yy2, xx2 = np.mgrid[0:hh2, 0:ww2].astype(np.float32)
    rn2s = (((xx2 - cx2) ** 2 + (yy2 - cy2) ** 2) / rmax2 ** 2)
    ann2 = rn2s > 0.45
    G = cv2.Sobel(small[:, :, 1].astype(np.float32), cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(small[:, :, 1].astype(np.float32), cv2.CV_32F, 0, 1) ** 2
    def ncc(a, b, m):
        a = a[m] - a[m].mean(); b = b[m] - b[m].mean()
        return float((a * b).sum() / max(np.sqrt((a * a).sum() * (b * b).sum()), 1e-9))
    best = {}
    for ch, name in ((0, "R"), (2, "B")):
        scores = []
        for k in CA_KS:
            scale = 1.0 + k * rn2s
            mx = (cx2 + (xx2 - cx2) * scale).astype(np.float32); my = (cy2 + (yy2 - cy2) * scale).astype(np.float32)
            shifted = cv2.remap(small[:, :, ch].astype(np.float32), mx, my, cv2.INTER_LINEAR)
            Sg = cv2.Sobel(shifted, cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(shifted, cv2.CV_32F, 0, 1) ** 2
            scores.append(ncc(Sg, G, ann2))
        ki = int(np.argmax(scores))
        best[name] = {"k": float(CA_KS[ki]), "ncc_best": float(scores[ki]), "ncc_k0": float(scores[len(CA_KS) // 2])}
    return best, (rn2, cx, cy)


def apply_ca(img, ca, geom):
    import cv2
    rn2, cx, cy = geom
    hh, ww = img.shape[:2]
    yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
    out = img.copy()
    for ch, name in ((0, "R"), (2, "B")):
        k = ca[name]["k"]
        if abs(k) < 1e-9: continue
        scale = (1.0 + k * rn2).astype(np.float32)
        mx = (cx + (xx - cx) * scale); my = (cy + (yy - cy) * scale)
        out[:, :, ch] = cv2.remap(img[:, :, ch], mx, my, cv2.INTER_LINEAR)
    return out


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
    dirs = erp_dirs(); dz = dirs[:, :, 2]
    plane_t = np.where(dz < -0.05, (-C[2] - 0.33) / np.minimum(dz, -1e-3), np.inf).astype(np.float32)
    use_plane = (dist_px > 12) & np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX)
    Zf = np.where(use_plane, plane_t, Zf)
    return np.where(Zf <= 0, 200.0, Zf)


def render(frame, ring_cams, C, Zd, images):
    import cv2
    dirs = erp_dirs()
    X = C[None, None, :] + Zd[:, :, None].astype(np.float64) * dirs
    best = np.full((H, W), np.inf, np.float32)
    out = np.zeros((H, W, 3), np.uint8)
    for ci, cam in enumerate(ring_cams):
        cal = frame.calibrations[cam]; K = np.asarray(cal.K, float); T = np.asarray(cal.T_ego_cam, float)
        Tci = np.linalg.inv(T); img = images[ci]; hh, ww = img.shape[:2]
        Xf = X.reshape(-1, 3)
        Xc = (Tci[:3, :3] @ Xf.T).T + Tci[:3, 3]; z = Xc[:, 2]
        px = (K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]).astype(np.float32)
        py = (K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]).astype(np.float32)
        ok = (z > 0.1) & (px >= 1) & (px < ww - 1) & (py >= 1) & (py < hh - 1)
        cvec = T[:3, 3] - C; df = dirs.reshape(-1, 3); along = df @ cvec
        bperp = np.sqrt(np.maximum(float(cvec @ cvec) - along * along, 0.0)).astype(np.float32)
        score = np.where(ok, bperp, np.inf)
        upd = score < best.reshape(-1)
        if not upd.any(): continue
        mapx = np.where(upd, px, 0).reshape(H, W); mapy = np.where(upd, py, 0).reshape(H, W)
        col = cv2.remap(img, mapx, mapy, cv2.INTER_LINEAR)
        u2 = upd.reshape(H, W); out[u2] = col[u2]
        be = best.reshape(-1); be[upd] = score[upd]; best = be.reshape(H, W)
    return out


def run_case(case_spec, run_name):
    from PIL import Image, ImageDraw, ImageFont
    log_dir, ts, frame, ring_cams, cte, tri, ann = load_all(case_spec)
    cents = np.stack([np.asarray(frame.calibrations[c].T_ego_cam, float)[:3, 3] for c in ring_cams], 0)
    C = cents.mean(axis=0)
    lidar = accumulate_lidar(log_dir, ts, cte, tri, ann)
    # P1 gains
    pairs = collect_pairs(frame, ring_cams, lidar, C)
    gains, gstats = solve_gains(pairs, len(ring_cams))
    # P2 CA per camera
    ca_all = {}
    images_b1 = []
    for ci, cam in enumerate(ring_cams):
        img = frame.images[cam]
        ca, geom = estimate_ca(img)
        ca_all[cam] = ca
        img2 = apply_ca(img, ca, geom)
        g = np.exp(gains[ci])[None, None, :]
        img2 = np.clip(img2.astype(np.float32) * g.astype(np.float32), 0, 255).astype(np.uint8)
        images_b1.append(img2)
    images_raw = [frame.images[c] for c in ring_cams]
    Zd = depth_field(lidar, C)
    base = render(frame, ring_cams, C, Zd, images_raw)
    b1 = render(frame, ring_cams, C, Zd, images_b1)
    save_rgb(REMOTE_OUT / f"{run_name}_cen_depth_base.png", base)
    save_rgb(REMOTE_OUT / f"{run_name}_cen_depth_b1.png", b1)
    # board
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    rows = []
    for tag, im in ((f"cen_depth BASE (DB-80)", base), (f"cen_depth + B1 (P1 gains + P2 CA) | pair logdiff {gstats['pair_logdiff_before']:.4f}->{gstats['pair_logdiff_after']:.4f} ({gstats['reduction_frac']*100:.0f}% cut)", b1)):
        pil = Image.fromarray(im).resize((1400, 700))
        bar = Image.new("RGB", (1400, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), f"{run_name}  {tag}", (235, 235, 245), font=f)
        o = Image.new("RGB", (1400, 724)); o.paste(bar, (0, 0)); o.paste(pil, (0, 24)); rows.append(o)
    # closeups: mid-band strip halves for seam steps + near-ground strip for fringe
    cu = []
    for (x0, y0, x1, y1, lab) in [(200, 480, 900, 700, "midband_left"), (1100, 480, 1800, 700, "midband_right"), (600, 700, 1500, 850, "nearground")]:
        for tag, im in (("base", base), ("b1", b1)):
            crop = Image.fromarray(im[y0:y1, x0:x1])
            bar = Image.new("RGB", (crop.width, 20), (25, 25, 32)); ImageDraw.Draw(bar).text((4, 3), f"{lab} | {tag}", (230, 230, 240), font=f)
            t = Image.new("RGB", (crop.width, crop.height + 20)); t.paste(bar, (0, 0)); t.paste(crop, (0, 20)); cu.append(t)
    cw = max(t.width for t in cu); chh = sum(cu[i].height for i in range(0, len(cu), 2))
    strip = Image.new("RGB", (cw * 2, max(t.height for t in cu) * (len(cu) // 2)), (8, 8, 12))
    for i, t in enumerate(cu): strip.paste(t, ((i % 2) * cw, (i // 2) * max(x.height for x in cu)))
    board = Image.new("RGB", (max(1400, strip.width), 724 * 2 + strip.height + 16), (8, 8, 12))
    yo = 8
    for o in rows: board.paste(o, (0, yo)); yo += o.height
    board.paste(strip, (0, yo))
    board.save(REMOTE_OUT / f"{run_name}_db81_board.jpg", quality=90)
    return {"case": run_name, "gain_stats": gstats,
            "gains_exp": {ring_cams[i]: np.exp(gains[i]).round(4).tolist() for i in range(len(ring_cams))},
            "ca": {k: v for k, v in ca_all.items()}, "n_pairs_groups": len(pairs)}


def json_safe(o):
    if isinstance(o, dict): return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [json_safe(v) for v in o]
    if isinstance(o, np.ndarray): return o.tolist()
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o); return v if math.isfinite(v) else None
    if isinstance(o, (np.bool_,)): return bool(o)
    return o


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    reports = [run_case(cs, rn) for cs, rn in CASES]
    OUT["status"] = "db81_completed"; OUT["cases"] = json_safe(reports); OUT["runtime_s"] = round(time.time() - t0, 2)
    (REMOTE_OUT / "DB81_summary.json").write_text(json.dumps(json_safe({"by_case": reports})), encoding="utf-8")
except Exception as exc:
    OUT["status"] = "db81_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB81_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB81_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__RESULT__", RESULT)


def remote_bash(py: str) -> str:
    b = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return "set +x\npython - <<'PY'\nimport base64\ncode = base64.b64decode('" + b + "').decode('utf-8')\nexec(compile(code, '<db81_remote>', 'exec'))\nPY"


def poll_job(client, job_id, timeout_s):
    t0 = time.time(); last = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7); last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running": return sanitize(last)
    return sanitize(last or {"state": "poll_timeout"})


def run_remote(timeout_s: int = 2400) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient(); status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_py())], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s)
    fetched = {}
    names = ["DB81_remote_result.json", "DB81_summary.json"]
    for n in CASE_NAMES:
        names += [f"{n}_db81_board.jpg", f"{n}_cen_depth_b1.png", f"{n}_cen_depth_base.png"]
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
    out = Path.home() / ".waymo2panorama" / "db81_run_report.json"
    out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)
