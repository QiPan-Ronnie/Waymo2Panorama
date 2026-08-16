"""DB-238 scene-band screener - cross-camera photometric residual, one anchor per log.

Read-only. Changes no production switch, deletes no sample, sets no threshold.
Produces a ranked residual per adjacent camera pair so the user can choose an
acceptance threshold with the population distribution in front of them.

Rationale and gates: deliverables/db236_scene_band_quality_closure/SCREENING_PLAN.md

Mirrors production `depth_field()` from scripts/phase3/db89_ghost_recovery.py
(verified to 0.015 m max deviation against the shipped fine_depth on 00a6ffc1
a100).  Uses static T_ego_cam rather than the renderer's EMC-compensated pose;
on a100 that approximation leaves five of seven pairs at 1.9-8.6/255, adequate
for ranking.  See SCREENING_PLAN.md section 7 for all known limits.
"""
from __future__ import annotations

import glob
import json
import math
import os
import subprocess
import time

import numpy as np

H, W = 1024, 2048
DMIN, DMAX = 1.5, 80.0
RAD = 2.0 * math.pi / W
CAMERAS = [
    "ring_front_center", "ring_front_left", "ring_front_right",
    "ring_side_left", "ring_side_right", "ring_rear_left", "ring_rear_right",
]
# adjacent pairs around the ring, in physical order
ADJACENT = [
    ("ring_front_center", "ring_front_left"),
    ("ring_front_left", "ring_side_left"),
    ("ring_side_left", "ring_rear_left"),
    ("ring_rear_left", "ring_rear_right"),
    ("ring_rear_right", "ring_side_right"),
    ("ring_side_right", "ring_front_right"),
    ("ring_front_right", "ring_front_center"),
]
S3 = "s3://argoverse/datasets/av2/sensor"
# logs localized by earlier DB-236 work; the split sub-directory there is NOT
# authoritative (everything was dropped under val/), so all of them are searched.
DRIVE_CACHE = "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2"

_uu, _vv = np.meshgrid(np.arange(W), np.arange(H))
_theta = np.pi - (_uu + 0.5) / W * 2 * np.pi
_phi = np.pi / 2 - (_vv + 0.5) / H * np.pi
DIRS = np.stack([np.cos(_phi) * np.cos(_theta),
                 np.cos(_phi) * np.sin(_theta),
                 np.sin(_phi)], -1).astype(np.float64)
DIRS_FLAT = DIRS.reshape(-1, 3)


# ---------------------------------------------------------------- S3 fetching

def _s5(args, timeout=900):
    return subprocess.run(["s5cmd", "--no-sign-request"] + args,
                          capture_output=True, text=True, timeout=timeout)


def list_split_logs(split):
    """Full log uuids present in a split, so 8-char prefixes can be resolved."""
    r = _s5(["ls", f"{S3}/{split}/"], timeout=600)
    out = []
    for line in r.stdout.splitlines():
        tok = line.strip().split()
        if tok and tok[-1].endswith("/"):
            out.append(tok[-1].rstrip("/"))
    return out


def _ls_timestamps(prefix, suffix):
    r = _s5(["ls", f"{prefix}/*{suffix}"], timeout=600)
    ts = []
    for line in r.stdout.splitlines():
        name = line.strip().split()[-1] if line.strip() else ""
        if name.endswith(suffix):
            stem = os.path.basename(name)[: -len(suffix)]
            if stem.isdigit():
                ts.append(int(stem))
    return sorted(ts)


def fetch_anchor(uuid, split, anchor_idx, dest, n_lidar=2):
    """Download only what one anchor frame needs. Returns a manifest dict."""
    os.makedirs(dest, exist_ok=True)
    base = f"{S3}/{split}/{uuid}"
    cmds, got = [], {}

    for rel in ("calibration/intrinsics.feather",
                "calibration/egovehicle_SE3_sensor.feather",
                "city_SE3_egovehicle.feather"):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)) or dest, exist_ok=True)
        cmds.append(f"cp {base}/{rel} {os.path.join(dest, rel)}")

    ref = _ls_timestamps(f"{base}/sensors/cameras/ring_front_center", ".jpg")
    if not ref:
        raise RuntimeError(f"no front_center frames listed for {uuid}")
    idx = int(np.clip(anchor_idx, 0, len(ref) - 1))
    t_anchor = ref[idx]
    got["frame_count"] = len(ref)
    got["anchor_idx"] = idx
    got["anchor_ts"] = t_anchor

    for cam in CAMERAS:
        ts = ref if cam == "ring_front_center" else _ls_timestamps(
            f"{base}/sensors/cameras/{cam}", ".jpg")
        if not ts:
            raise RuntimeError(f"no frames for {cam} in {uuid}")
        t = min(ts, key=lambda v: abs(v - t_anchor))
        d = os.path.join(dest, "sensors", "cameras", cam)
        os.makedirs(d, exist_ok=True)
        cmds.append(f"cp {base}/sensors/cameras/{cam}/{t}.jpg {os.path.join(d, str(t))}.jpg")
        got.setdefault("cam_ts", {})[cam] = t

    lts = _ls_timestamps(f"{base}/sensors/lidar", ".feather")
    if not lts:
        raise RuntimeError(f"no lidar for {uuid}")
    near = sorted(lts, key=lambda v: abs(v - t_anchor))[:max(1, n_lidar)]
    d = os.path.join(dest, "sensors", "lidar")
    os.makedirs(d, exist_ok=True)
    for t in near:
        cmds.append(f"cp {base}/sensors/lidar/{t}.feather {os.path.join(d, str(t))}.feather")
    got["lidar_ts"] = near

    script = os.path.join(dest, "_fetch.txt")
    with open(script, "w") as fh:
        fh.write("\n".join(cmds) + "\n")
    r = _s5(["run", script], timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"s5cmd run failed for {uuid}: {r.stderr[-800:]}")
    return got


# ------------------------------------------------------------- calibration/io

def load_calibration(dest):
    import pandas as pd
    from scipy.spatial.transform import Rotation
    intr = pd.read_feather(os.path.join(dest, "calibration", "intrinsics.feather"))
    extr = pd.read_feather(os.path.join(dest, "calibration", "egovehicle_SE3_sensor.feather"))
    cal = {}
    for cam in CAMERAS:
        ri = intr[intr["sensor_name"] == cam]
        re = extr[extr["sensor_name"] == cam]
        if ri.empty or re.empty:
            raise RuntimeError(f"calibration missing for {cam}")
        ri = ri.iloc[0]; re = re.iloc[0]
        K = np.array([[ri["fx_px"], 0.0, ri["cx_px"]],
                      [0.0, ri["fy_px"], ri["cy_px"]],
                      [0.0, 0.0, 1.0]], float)
        R = Rotation.from_quat([re["qx"], re["qy"], re["qz"], re["qw"]]).as_matrix()
        t = np.array([re["tx_m"], re["ty_m"], re["tz_m"]], float)
        cal[cam] = {"K": K, "R": R, "t": t,
                    "shape": (int(ri["height_px"]), int(ri["width_px"]))}
    return cal


def load_lidar(dest, max_points=250000, seed=0):
    import pandas as pd
    pts = []
    for p in sorted(glob.glob(os.path.join(dest, "sensors", "lidar", "*.feather"))):
        df = pd.read_feather(p)
        pts.append(df[["x", "y", "z"]].to_numpy(np.float64))
    if not pts:
        return np.zeros((0, 3))
    xyz = np.concatenate(pts, 0)
    if len(xyz) > max_points:
        rng = np.random.default_rng(seed)
        xyz = xyz[rng.choice(len(xyz), max_points, replace=False)]
    return xyz


def load_images(dest, cam_ts):
    from PIL import Image
    out = {}
    for cam, t in cam_ts.items():
        p = os.path.join(dest, "sensors", "cameras", cam, f"{t}.jpg")
        out[cam] = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
    return out


# ------------------------------------------------------------------- geometry

def depth_field(lidar, C):
    """Byte-for-byte the production algorithm in db89_ghost_recovery.depth_field."""
    import cv2
    from scipy.ndimage import distance_transform_edt
    Q = lidar - C[None, :]
    n = np.linalg.norm(Q, axis=1)
    m = (n > DMIN) & (n < DMAX)
    Qm, nm = Q[m], n[m]
    if len(nm) < 1000:
        dz0 = DIRS[:, :, 2]
        Zd = np.where(dz0 < -0.05,
                      np.clip((-C[2] - 0.33) / np.minimum(dz0, -1e-3), DMIN, DMAX),
                      100.0).astype(np.float32)
        return Zd.astype(np.float64), np.full((H, W), 1e9, np.float32)
    dd = Qm / nm[:, None]
    th = np.arctan2(dd[:, 1], dd[:, 0])
    ph = np.arcsin(np.clip(dd[:, 2], -1, 1))
    ui = np.clip(np.round((np.pi - th) / (2 * np.pi) * W - 0.5).astype(np.int64), 0, W - 1)
    vi = np.clip(np.round((np.pi / 2 - ph) / np.pi * H - 0.5).astype(np.int64), 0, H - 1)
    Z = np.zeros((H, W), np.float32)
    order = np.argsort(-nm)
    zf = Z.reshape(-1)
    zf[(vi * W + ui)[order]] = nm[order].astype(np.float32)
    ok = Z > 0
    dist_px, inds = distance_transform_edt(~ok, return_distances=True, return_indices=True)
    Zf = cv2.medianBlur(Z[inds[0], inds[1]].astype(np.float32), 5)
    dz = DIRS[:, :, 2]
    plane_t = np.where(dz < -0.05, (-C[2] - 0.33) / np.minimum(dz, -1e-3), np.inf).astype(np.float32)
    Zf = np.where((dist_px > 12) & np.isfinite(plane_t) & (plane_t > DMIN) & (plane_t < DMAX),
                  plane_t, Zf)
    small = cv2.resize(Zf, (W // 8, H // 8), interpolation=cv2.INTER_NEAREST)
    Zsmooth = cv2.resize(cv2.medianBlur(small, 5), (W, H), interpolation=cv2.INTER_LINEAR)
    conf = (dist_px <= 4) & (np.abs(Zf - Zsmooth) < 0.05 * Zsmooth)
    Zf = np.where(conf, Zf, Zsmooth)
    return np.where(Zf <= 0, 200.0, Zf).astype(np.float64), dist_px.astype(np.float32)


def camera_support(cal):
    sup = {}
    for cam, c in cal.items():
        dcam = DIRS_FLAT @ c["R"]
        z = dcam[:, 2]
        K = c["K"]; hh, ww = c["shape"]
        px = K[0, 0] * dcam[:, 0] / np.maximum(z, 1e-9) + K[0, 2]
        py = K[1, 1] * dcam[:, 1] / np.maximum(z, 1e-9) + K[1, 2]
        sup[cam] = ((z > 1e-6) & (px >= 0) & (px < ww) & (py >= 0) & (py < hh)).reshape(H, W)
    return sup


def _project(cal_c, X):
    Xc = (X - cal_c["t"][None, :]) @ cal_c["R"]
    z = Xc[:, 2]
    K = cal_c["K"]; hh, ww = cal_c["shape"]
    px = K[0, 0] * Xc[:, 0] / np.maximum(z, 1e-6) + K[0, 2]
    py = K[1, 1] * Xc[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
    ok = (z > 0.5) & (px >= 1) & (px < ww - 2) & (py >= 1) & (py < hh - 2)
    return px, py, ok


def pair_residual(a, b, cal, imgs, sup, Zd, band_elev_deg=35.0, max_samples=40000, seed=0):
    """Median |affine-matched A - B| over the pixels both cameras support."""
    elev = np.degrees(np.arcsin(np.clip(DIRS[:, :, 2], -1, 1)))
    m = sup[a] & sup[b] & (np.abs(elev) < band_elev_deg)
    n_ov = int(m.sum())
    if n_ov < 300:
        return {"n_overlap": n_ov, "residual": None, "reason": "overlap_too_small"}
    ys, xs = np.nonzero(m)
    if len(ys) > max_samples:
        sel = np.random.default_rng(seed).choice(len(ys), max_samples, replace=False)
        ys, xs = ys[sel], xs[sel]
    X = np.array([0.0, 0.0, 0.0])[None, :] + Zd[ys, xs][:, None] * DIRS[ys, xs]
    X = X + pair_residual.C[None, :]
    pa, qa, oka = _project(cal[a], X)
    pb, qb, okb = _project(cal[b], X)
    ok = oka & okb
    if ok.sum() < 300:
        return {"n_overlap": n_ov, "residual": None, "reason": "projection_too_small"}
    A = imgs[a][np.clip(qa[ok].astype(int), 0, imgs[a].shape[0] - 1),
                np.clip(pa[ok].astype(int), 0, imgs[a].shape[1] - 1)]
    B = imgs[b][np.clip(qb[ok].astype(int), 0, imgs[b].shape[0] - 1),
                np.clip(pb[ok].astype(int), 0, imgs[b].shape[1] - 1)]
    Aa = np.empty_like(A)
    for ch in range(3):
        sa = A[:, ch].std() + 1e-6
        sb = B[:, ch].std() + 1e-6
        Aa[:, ch] = (A[:, ch] - A[:, ch].mean()) / sa * sb + B[:, ch].mean()
    dv = np.abs(Aa - B).mean(1)
    return {"n_overlap": n_ov, "n_sampled": int(ok.sum()),
            "residual": round(float(np.median(dv)), 4),
            "p90": round(float(np.percentile(dv, 90)), 4)}


pair_residual.C = np.zeros(3)


def fetch_anchors_multi(uuid, split, anchor_idxs, dest, n_lidar=2):
    """Download several anchor frames in ONE listing pass.

    Single-frame screening was shown to miss real defects: on 00a6ffc1 the same
    log scores 20.25 at anchor 63 and 56.75 at anchor 100, because pedestrians
    and vehicles move through the overlap.  The listing is the expensive part,
    so it is done once and reused for every requested anchor.
    """
    os.makedirs(dest, exist_ok=True)
    base = f"{S3}/{split}/{uuid}"
    cmds = []
    for rel in ("calibration/intrinsics.feather",
                "calibration/egovehicle_SE3_sensor.feather",
                "city_SE3_egovehicle.feather"):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)) or dest, exist_ok=True)
        cmds.append(f"cp {base}/{rel} {os.path.join(dest, rel)}")

    ts_by_cam = {}
    for cam in CAMERAS:
        ts_by_cam[cam] = _ls_timestamps(f"{base}/sensors/cameras/{cam}", ".jpg")
        if not ts_by_cam[cam]:
            raise RuntimeError(f"no frames for {cam} in {uuid}")
    lts = _ls_timestamps(f"{base}/sensors/lidar", ".feather")
    if not lts:
        raise RuntimeError(f"no lidar for {uuid}")

    ref = ts_by_cam["ring_front_center"]
    mans = []
    want_cam, want_lidar = set(), set()
    for ai in anchor_idxs:
        idx = int(np.clip(ai, 0, len(ref) - 1))
        t_anchor = ref[idx]
        man = {"anchor_idx": idx, "anchor_ts": t_anchor, "frame_count": len(ref),
               "cam_ts": {}, "source": "s3"}
        for cam in CAMERAS:
            t = min(ts_by_cam[cam], key=lambda v: abs(v - t_anchor))
            man["cam_ts"][cam] = t
            want_cam.add((cam, t))
        man["lidar_ts"] = sorted(lts, key=lambda v: abs(v - t_anchor))[:max(1, n_lidar)]
        want_lidar.update(man["lidar_ts"])
        mans.append(man)

    for cam, t in sorted(want_cam):
        d = os.path.join(dest, "sensors", "cameras", cam)
        os.makedirs(d, exist_ok=True)
        cmds.append(f"cp {base}/sensors/cameras/{cam}/{t}.jpg {os.path.join(d, str(t))}.jpg")
    d = os.path.join(dest, "sensors", "lidar")
    os.makedirs(d, exist_ok=True)
    for t in sorted(want_lidar):
        cmds.append(f"cp {base}/sensors/lidar/{t}.feather {os.path.join(d, str(t))}.feather")

    script = os.path.join(dest, "_fetch.txt")
    with open(script, "w") as fh:
        fh.write("\n".join(cmds) + "\n")
    r = _s5(["run", script], timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"s5cmd run failed for {uuid}: {r.stderr[-800:]}")
    return mans


def cached_log_dir(uuid):
    """A previously localized full log, if one happens to be on Drive."""
    if not os.path.isdir(DRIVE_CACHE):
        return None
    for sub in ("val", "train", "test"):
        p = os.path.join(DRIVE_CACHE, sub, uuid)
        if (os.path.isfile(os.path.join(p, "calibration", "intrinsics.feather"))
                and glob.glob(os.path.join(p, "sensors", "lidar", "*.feather"))
                and glob.glob(os.path.join(p, "sensors", "cameras",
                                           "ring_front_center", "*.jpg"))):
            return p
    return None


def manifest_from_dir(log_dir, anchor_idx, n_lidar):
    """Pick the anchor frame and nearest lidar sweeps from an already-local log."""
    def _stems(sub, suffix):
        out = []
        for p in glob.glob(os.path.join(log_dir, sub, f"*{suffix}")):
            s = os.path.basename(p)[: -len(suffix)]
            if s.isdigit():
                out.append(int(s))
        return sorted(out)

    ref = _stems(os.path.join("sensors", "cameras", "ring_front_center"), ".jpg")
    if not ref:
        raise RuntimeError("cached log has no front_center frames")
    idx = int(np.clip(anchor_idx, 0, len(ref) - 1))
    t_anchor = ref[idx]
    man = {"frame_count": len(ref), "anchor_idx": idx, "anchor_ts": t_anchor,
           "cam_ts": {}, "source": "drive_cache"}
    for cam in CAMERAS:
        ts = ref if cam == "ring_front_center" else _stems(
            os.path.join("sensors", "cameras", cam), ".jpg")
        if not ts:
            raise RuntimeError(f"cached log missing {cam}")
        man["cam_ts"][cam] = min(ts, key=lambda v: abs(v - t_anchor))
    lts = _stems(os.path.join("sensors", "lidar"), ".feather")
    if not lts:
        raise RuntimeError("cached log has no lidar")
    man["lidar_ts"] = sorted(lts, key=lambda v: abs(v - t_anchor))[:max(1, n_lidar)]
    return man


def load_lidar_at(log_dir, lidar_ts, max_points=250000, seed=0):
    import pandas as pd
    pts = []
    for t in lidar_ts:
        p = os.path.join(log_dir, "sensors", "lidar", f"{t}.feather")
        if os.path.isfile(p):
            pts.append(pd.read_feather(p)[["x", "y", "z"]].to_numpy(np.float64))
    if not pts:
        return np.zeros((0, 3))
    xyz = np.concatenate(pts, 0)
    if len(xyz) > max_points:
        xyz = xyz[np.random.default_rng(seed).choice(len(xyz), max_points, replace=False)]
    return xyz


def screen_log_multi(uuid, split, anchor_idxs, workdir, n_lidar=2, keep=False):
    """Score several anchors of one log; report each and the per-log maximum."""
    t0 = time.time()
    dest = os.path.join(workdir, uuid)
    rec = {"uuid": uuid, "split": split, "requested_anchors": list(anchor_idxs)}
    used_cache = False
    try:
        cached = cached_log_dir(uuid)
        if cached is not None:
            mans = [manifest_from_dir(cached, a, n_lidar) for a in anchor_idxs]
            dest = cached
            used_cache = True
        else:
            mans = fetch_anchors_multi(uuid, split, anchor_idxs, dest, n_lidar=n_lidar)
        rec["source"] = "drive_cache" if used_cache else "s3"
        rec["frame_count"] = mans[0]["frame_count"]
        t_fetch = time.time() - t0
        cal = load_calibration(dest)
        C = np.stack([cal[c]["t"] for c in CAMERAS], 0).mean(0)
        pair_residual.C = C
        sup = camera_support(cal)
        frames = []
        for man in mans:
            lidar = load_lidar_at(dest, man["lidar_ts"])
            imgs = load_images(dest, man["cam_ts"])
            Zd, _ = depth_field(lidar, C)
            pairs = {f"{a}|{b}": pair_residual(a, b, cal, imgs, sup, Zd)
                     for a, b in ADJACENT}
            vals = [v["residual"] for v in pairs.values() if v.get("residual") is not None]
            frames.append({
                "anchor_idx": man["anchor_idx"],
                "worst_pair": max((kv for kv in pairs.items()
                                   if kv[1].get("residual") is not None),
                                  key=lambda kv: kv[1]["residual"], default=(None, {}))[0],
                "worst_residual": round(max(vals), 4) if vals else None,
                "median_residual": round(float(np.median(vals)), 4) if vals else None,
                "pairs": {k: v.get("residual") for k, v in pairs.items()},
            })
        wv = [f["worst_residual"] for f in frames if f["worst_residual"] is not None]
        rec.update({
            "ok": bool(wv),
            "n_anchors_scored": len(wv),
            "frames": frames,
            "log_worst_residual": round(max(wv), 4) if wv else None,
            "log_mean_worst": round(float(np.mean(wv)), 4) if wv else None,
            "log_worst_anchor": (max(frames, key=lambda f: f["worst_residual"] or -1)
                                 ["anchor_idx"] if wv else None),
            "fetch_s": round(t_fetch, 1),
            "total_s": round(time.time() - t0, 1),
        })
    except Exception as exc:
        rec.update({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "total_s": round(time.time() - t0, 1)})
    finally:
        if not keep and not used_cache:
            import shutil
            shutil.rmtree(os.path.join(workdir, uuid), ignore_errors=True)
    return rec


def screen_log(uuid, split, anchor_idx, workdir, n_lidar=2, keep=False):
    t0 = time.time()
    dest = os.path.join(workdir, uuid)
    rec = {"uuid": uuid, "split": split, "requested_anchor": anchor_idx}
    used_cache = False
    try:
        cached = cached_log_dir(uuid)
        if cached is not None:
            man = manifest_from_dir(cached, anchor_idx, n_lidar)
            dest = cached
            used_cache = True
        else:
            man = fetch_anchor(uuid, split, anchor_idx, dest, n_lidar=n_lidar)
        rec.update({k: man[k] for k in ("frame_count", "anchor_idx", "anchor_ts")})
        rec["source"] = "drive_cache" if used_cache else "s3"
        t_fetch = time.time() - t0
        cal = load_calibration(dest)
        lidar = load_lidar_at(dest, man["lidar_ts"])
        imgs = load_images(dest, man["cam_ts"])
        C = np.stack([cal[c]["t"] for c in CAMERAS], 0).mean(0)
        pair_residual.C = C
        Zd, _ = depth_field(lidar, C)
        sup = camera_support(cal)
        pairs = {}
        for a, b in ADJACENT:
            pairs[f"{a}|{b}"] = pair_residual(a, b, cal, imgs, sup, Zd)
        vals = [v["residual"] for v in pairs.values() if v.get("residual") is not None]
        rec.update({
            "ok": True,
            "lidar_points": int(len(lidar)),
            "camera_center": [round(float(v), 6) for v in C],
            "pairs": pairs,
            "worst_pair": max((v for v in pairs.items() if v[1].get("residual") is not None),
                              key=lambda kv: kv[1]["residual"], default=(None, {}))[0],
            "worst_residual": round(max(vals), 4) if vals else None,
            "median_residual": round(float(np.median(vals)), 4) if vals else None,
            "n_pairs_scored": len(vals),
            "fetch_s": round(t_fetch, 1),
            "total_s": round(time.time() - t0, 1),
        })
    except Exception as exc:  # keep going; the run log must show every failure
        rec.update({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "total_s": round(time.time() - t0, 1)})
    finally:
        # never delete the shared Drive cache; only our own scratch download
        if not keep and not used_cache:
            import shutil
            shutil.rmtree(os.path.join(workdir, uuid), ignore_errors=True)
    return rec
