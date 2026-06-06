"""DB-76a batteries 3+4: forward-stereo + ring-temporal coverage audit (measurement-only).

This is the GPU half of DB-76a. It does NOT repair RGB. It measures how much of the
currently-abstained / single-source near-ground can be converted back to *validated*
GREEN by two so-far-unused evidence sources:

  Battery 3 - AV2 forward STEREO pair (stereo_front_left/right): rectify -> SGM (and
    optionally RAFT-Stereo on GPU) disparity + L-R consistency + confidence -> project
    metric near-field depth to ERP target rays -> z-buffer raw-visibility check -> LOO
    residual validation. Forward only; fails on textureless/wet/reflective.

  Battery 4 - ring-temporal multi-azimuth (surface-centric, NOT the DB74 optimizer):
    anchor +/-1-2 s, ring cams + ego poses -> build a static surface candidate -> for
    each abstain/single-source ray ask "do >=2 raw views see the same surface in the
    window?" -> triangulation angle / baseline / photometric consistency / z-buffer ->
    LOO render-back. Dynamic objects removed before accumulation. Waymo rolling-shutter
    residual goes in a SEPARATE risk bucket (do not extrapolate AV2 -> Waymo).

Pre-registered build-worthy bar / kill criteria (DB-76a brief, NOT relaxed by outcome):
  - build-worthy: >=25% of the relevant abstain/single-source band becomes
    surface_valid & raw_visible & LOO_residual<3px, OR >=8-10% of task-valid ERP becomes
    validated GREEN; AND no protected-structure failure.
  - kill: stereo covers <10% of relevant band -> forward side-evidence only, don't change
    main line. temporal yields <5% validated coverage or sparse islands -> close route.

Run order: `--preflight` (confirm stereo images + ego-pose + neighbor frames exist on
Drive) FIRST, then implement+run Battery 3, then Battery 4. One bounded /status + /exec
per step. Runtime secret read only from process env / non-repo file; remote gets no token.
"""
from __future__ import annotations

import argparse
import base64
import json
import py_compile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db64_ltr_v0_phase4b_z_visibility_cause import (
    ColabClient,
    rel,
    safe_status,
    sanitize,
    secret_hits,
)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db76b_stereo_temporal_coverage"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db76b_stereo_temporal_coverage"
REMOTE_RESULT = REMOTE_OUT + "/DB76b_remote_result.json"
LOCAL_REMOTE_RESULT = OUT_DIR / "DB76b_remote_result.json"
MANIFEST = OUT_DIR / "DB76b_preflight_manifest.json"

CASE_PREFIXES = [("02a00399", "bmw"), ("0bae3b5e", "clean_far")]


# --------------------------------------------------------------------------- #
# Remote preflight: confirm stereo images + ego-pose + neighbor frames on Drive.
# --------------------------------------------------------------------------- #
def remote_preflight_python() -> str:
    code = r'''
import json, pathlib, time, traceback
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
CASE_PREFIXES = [("02a00399", "bmw"), ("0bae3b5e", "clean_far")]
REMOTE_OUT = pathlib.Path("__REMOTE_OUT__")
REMOTE_RESULT = pathlib.Path("__REMOTE_RESULT__")

OUT = {"phase": "db76b_preflight_stereo_temporal_data", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def import_ok(name):
    try:
        __import__(name); return True
    except Exception:
        return False


def probe_case(prefix, tag):
    rep = {"prefix": prefix, "tag": tag}
    matches = sorted(DATA_ROOT.glob(prefix + "*"))
    rep["log_dir_matches"] = [m.name for m in matches]
    if not matches:
        rep["error"] = "no log dir match"; return rep
    log = matches[0]
    cams_dir = log / "sensors" / "cameras"
    rep["cameras_dir_exists"] = cams_dir.exists()
    cam_dirs = sorted([p.name for p in cams_dir.iterdir() if p.is_dir()]) if cams_dir.exists() else []
    rep["camera_dirs"] = cam_dirs
    counts = {}
    for name in cam_dirs:
        counts[name] = len(list((cams_dir / name).glob("*.jpg")))
    rep["jpg_counts"] = counts
    rep["stereo_dirs_present"] = [c for c in cam_dirs if "stereo" in c]
    # calibration feathers
    import pandas as pd
    intr_p = log / "calibration" / "intrinsics.feather"
    extr_p = log / "calibration" / "egovehicle_SE3_sensor.feather"
    rep["intrinsics_exists"] = intr_p.exists(); rep["extrinsics_exists"] = extr_p.exists()
    if intr_p.exists():
        intr = pd.read_feather(intr_p)
        rep["intrinsics_sensors"] = sorted(intr["sensor_name"].astype(str).unique().tolist())
        rep["intrinsics_columns"] = list(intr.columns)
        st = {}
        for n in rep["intrinsics_sensors"]:
            if "stereo" in n:
                r = intr.loc[intr["sensor_name"] == n].iloc[0]
                st[n] = {k: (float(r[k]) if k in intr.columns else None) for k in ["fx_px", "fy_px", "cx_px", "cy_px", "width_px", "height_px"]}
        rep["stereo_intrinsics"] = st
    if extr_p.exists():
        extr = pd.read_feather(extr_p)
        rep["extrinsics_sensors"] = sorted(extr["sensor_name"].astype(str).unique().tolist())
        rep["extrinsics_columns"] = list(extr.columns)
    # ego pose
    pose_p = log / "city_SE3_egovehicle.feather"
    rep["ego_pose_exists"] = pose_p.exists()
    if pose_p.exists():
        pose = pd.read_feather(pose_p)
        rep["ego_pose_rows"] = int(len(pose))
        rep["ego_pose_columns"] = list(pose.columns)
        ts = pose["timestamp_ns"].astype("int64").to_numpy() if "timestamp_ns" in pose.columns else None
        if ts is not None and len(ts):
            rep["ego_pose_span_s"] = float((ts.max() - ts.min()) / 1e9)
    # ring temporal window availability: front_center frame count + span
    fc = cams_dir / "ring_front_center"
    if fc.exists():
        stems = sorted(int(p.stem) for p in fc.glob("*.jpg"))
        rep["ring_front_center_frames"] = len(stems)
        if len(stems) >= 2:
            rep["ring_fc_span_s"] = float((stems[-1] - stems[0]) / 1e9)
            rep["ring_fc_median_dt_ms"] = float((stems[len(stems) // 2] - stems[len(stems) // 2 - 1]) / 1e6)
    # stereo image timestamp alignment vs anchor (anchor idx 0 / 30 used in batteries 1-2)
    sfl = cams_dir / "stereo_front_left"
    if sfl.exists():
        s_stems = sorted(int(p.stem) for p in sfl.glob("*.jpg"))
        rep["stereo_front_left_frames"] = len(s_stems)
    return rep


try:
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    OUT["deps"] = {"cv2": import_ok("cv2"), "pandas": import_ok("pandas"), "pyarrow": import_ok("pyarrow"), "torch": import_ok("torch"), "av2": import_ok("av2")}
    if import_ok("torch"):
        import torch
        OUT["cuda"] = {"available": bool(torch.cuda.is_available()), "device": (torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)}
    if import_ok("cv2"):
        import cv2
        OUT["cv2_has_sgbm"] = hasattr(cv2, "StereoSGBM_create")
    cases = []
    for prefix, tag in CASE_PREFIXES:
        try:
            cases.append(probe_case(prefix, tag))
        except Exception as e:
            cases.append({"prefix": prefix, "tag": tag, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]})
    OUT["cases"] = cases
    # decision summary
    def case_ok(c):
        return bool(c.get("stereo_dirs_present") and any(v > 0 for k, v in (c.get("jpg_counts") or {}).items() if "stereo" in k) and c.get("ego_pose_exists"))
    OUT["stereo_data_available"] = {c["tag"]: case_ok(c) for c in cases}
    OUT["status"] = "db76b_preflight_completed"
except Exception as exc:
    OUT["status"] = "db76b_preflight_failed"
    OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(OUT, indent=2, default=str), encoding="utf-8")
    print("DB76B_JSON_BEGIN")
    print(json.dumps(OUT, default=str, separators=(",", ":")))
    print("DB76B_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__REMOTE_RESULT__", REMOTE_RESULT)


def remote_bash(py: str) -> str:
    code_b64 = base64.b64encode(py.encode("utf-8")).decode("ascii")
    return (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db76b_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB76B_JSON_BEGIN" not in log_tail or "DB76B_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB76B_JSON_BEGIN", 1)[1].split("DB76B_JSON_END", 1)[0].strip()
    try:
        return json.loads(body)
    except Exception:
        return None


def poll_job(client: ColabClient, job_id: str, timeout_s: int) -> dict[str, Any]:
    t0 = time.time()
    last: dict[str, Any] = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(6)
        last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running":
            return sanitize(last)
    return sanitize(last or {"state": "poll_timeout", "job_id": job_id})


def run_preflight(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_preflight_python())], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s=timeout_s)
    remote_result: dict[str, Any] | None = None
    raw = client.read_file(REMOTE_RESULT, max_size_mb=12)
    if raw is not None:
        try:
            remote_result = json.loads(raw.decode("utf-8"))
        except Exception:
            remote_result = None
    if remote_result is None:
        remote_result = parse_json_from_log(job.get("log_tail", ""))
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    LOCAL_REMOTE_RESULT.write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db76b_preflight",
        "brief": "DB-76a batteries 3+4 (forward-stereo + ring-temporal coverage), preflight step",
        "scope": {"measurement_only": True, "preflight_data_probe_only": True, "exec_count": 1,
                   "runtime_type": status.get("runtime_type"), "rgb_repair": False, "model_inference": False},
        "runtime": {"secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file", "status": safe_status(status)},
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_status": remote_result.get("status"),
        "stereo_data_available": remote_result.get("stereo_data_available"),
        "deps": remote_result.get("deps"),
        "remote_result": rel(LOCAL_REMOTE_RESULT),
        "output_location": rel(OUT_DIR),
        "drive_output_location": "results/db76b_stereo_temporal_coverage/",
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "db76b_preflight", "remote_status": remote_result.get("status"),
            "stereo_data_available": remote_result.get("stereo_data_available"),
            "secret_hits": manifest["strict_secret_scan"]["hit_count"], "manifest": rel(MANIFEST)}


B3_THRESHOLDS = {
    "stereo_depth_min_m": 1.5,
    "stereo_depth_max_m": 60.0,
    "loo_residual_px_equiv_dE": 18.0,   # depth-correct cross-cam color residual threshold (cv2 LAB)
    "build_worthy_relevant_band_converted_min": 0.25,
    "build_worthy_task_valid_validated_green_min": 0.08,
    "kill_stereo_band_coverage_lt": 0.10,
}

B3_FETCH = {
    "summary": ("DB76b_b3_summary.json", 16),
    "remote_result": ("DB76b_b3_remote_result.json", 16),
    "board": ("DB76b_b3_review_board.jpg", 60),
    "bmw_board": ("02a00399_a000_bmw_b3_board.jpg", 60),
    "bmw_stereo_cov": ("02a00399_a000_bmw_b3_stereo_coverage_overlay.png", 30),
    "bmw_depth": ("02a00399_a000_bmw_b3_stereo_depth_heat.png", 30),
    "bmw_validated": ("02a00399_a000_bmw_b3_validated_green_overlay.png", 30),
    "clean_board": ("0bae3b5e_a030_clean_far_b3_board.jpg", 60),
    "clean_stereo_cov": ("0bae3b5e_a030_clean_far_b3_stereo_coverage_overlay.png", 30),
    "claim": ("DB76b_b3_claim.json", 16),
}


def remote_battery3_python() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__")
REMOTE_RESULT = pathlib.Path("__B3_RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [pathlib.Path("/content/waymo2panorama"), pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama")]
H, W = 1024, 2048
EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
MARKED_ROIS = {
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760),
}
TH = json.loads(r"""__B3_TH__""")
DMIN, DMAX = float(TH["stereo_depth_min_m"]), float(TH["stereo_depth_max_m"])
DE_THR = float(TH["loo_residual_px_equiv_dE"])
SCALE = 0.5  # downscale stereo for SGM speed

OUT = {"phase": "db76b_battery3_forward_stereo_coverage", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"measurement_only": True, "rgb_repair": False, "forward_stereo_only": True, "model_inference": False, "red_promotion": False}}


def import_ok(n):
    try:
        __import__(n); return True
    except Exception:
        return False


def find_workdir():
    for c in WORKDIR_CANDIDATES:
        if (c / "scripts" / "phase3" / "run_a1_streetview_pipeline.py").exists():
            return c
    return None


def json_safe(o):
    if isinstance(o, dict): return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [json_safe(v) for v in o]
    if isinstance(o, np.ndarray): return o.tolist()
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o); return v if math.isfinite(v) else None
    if isinstance(o, (np.bool_,)): return bool(o)
    return o


def save_rgb(path, arr, q=92):
    import cv2
    a = np.clip(arr, 0, 255).astype("uint8")
    if path.suffix.lower() in (".jpg", ".jpeg"):
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), int(q)])
    else:
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR))


def heat(x, valid=None):
    import cv2
    a = x.astype(np.float32)
    vals = a[valid] if valid is not None and bool(np.any(valid)) else a.reshape(-1)
    if vals.size == 0: return np.zeros((*x.shape, 3), np.uint8)
    lo, hi = np.percentile(vals, [2, 98])
    u = np.clip((a - lo) * 255.0 / max(hi - lo, 1e-6), 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.applyColorMap(u, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)


def overlay(rgb, mask, color, alpha=0.6):
    out = rgb.astype(np.float32).copy(); m = mask.astype(bool)
    for c in range(3):
        out[..., c][m] = (1 - alpha) * out[..., c][m] + alpha * color[c]
    return np.clip(out, 0, 255).astype(np.uint8)


def read_calib(log_dir, names):
    import pandas as pd
    from scipy.spatial.transform import Rotation
    intr = pd.read_feather(log_dir / "calibration" / "intrinsics.feather")
    extr = pd.read_feather(log_dir / "calibration" / "egovehicle_SE3_sensor.feather")
    out = {}
    for n in names:
        ri = intr.loc[intr["sensor_name"] == n].iloc[0]
        re = extr.loc[extr["sensor_name"] == n].iloc[0]
        K = np.array([[ri["fx_px"], 0, ri["cx_px"]], [0, ri["fy_px"], ri["cy_px"]], [0, 0, 1]], float)
        dist = np.array([ri.get("k1", 0.0), ri.get("k2", 0.0), 0.0, 0.0, ri.get("k3", 0.0)], float)
        R = Rotation.from_quat([re["qx"], re["qy"], re["qz"], re["qw"]]).as_matrix()
        T = np.eye(4); T[:3, :3] = R; T[:3, 3] = [re["tx_m"], re["ty_m"], re["tz_m"]]
        out[n] = {"K": K, "dist": dist, "T_ego_cam": T, "w": int(ri["width_px"]), "h": int(ri["height_px"])}
    return out


def nearest_img(cams_dir, cam, anchor_ts):
    import cv2
    paths = sorted((cams_dir / cam).glob("*.jpg"))
    ts = np.array([int(p.stem) for p in paths], np.int64)
    i = int(np.argmin(np.abs(ts - anchor_ts)))
    img = cv2.imread(str(paths[i]))  # BGR
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB), int(ts[i])


def ego_to_uv(P):
    n = np.linalg.norm(P, axis=1)
    d = P / np.maximum(n[:, None], EPS)
    theta = np.arctan2(d[:, 1], d[:, 0])
    phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    u = (np.pi - theta) / (2 * np.pi) * W - 0.5
    v = (np.pi / 2 - phi) / np.pi * H - 0.5
    return u, v, n


def ring_skeleton(case_spec, workdir):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    import cv2
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(ts)
    weights, labs = [], []
    raw = {}
    for cam in RING_CAMS_7:
        cal = frame.calibrations[cam]
        rgb, _a, w = render_camera_to_erp(image=frame.images[cam], K=cal.K, T_ego_cam=cal.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        weights.append(w.astype(np.float32)); labs.append(cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32))
        raw[cam] = {"img": frame.images[cam], "K": cal.K, "T": cal.T_ego_cam}
    weights = np.stack(weights, 0)
    valid_stack = weights > EPS
    viscount = valid_stack.sum(0).astype(np.int32)
    valid_any = viscount > 0
    order = np.argsort(-weights, axis=0)
    owner = order[0].astype(np.int32)
    base = np.take_along_axis(np.stack([cv2.cvtColor(l.astype(np.uint8), cv2.COLOR_LAB2RGB) for l in labs], 0),
                              np.where(valid_any, owner, 0)[None, :, :, None], axis=0)[0]
    base[~valid_any] = 0
    return log_dir, ts, frame, list(RING_CAMS_7), weights, valid_stack, viscount, valid_any, order, owner, base, np.stack(labs, 0)


def stereo_depth_to_erp(log_dir, anchor_ts):
    import cv2
    cams_dir = log_dir / "sensors" / "cameras"
    cal = read_calib(log_dir, ["stereo_front_left", "stereo_front_right"])
    img_l, ts_l = nearest_img(cams_dir, "stereo_front_left", anchor_ts)
    img_r, ts_r = nearest_img(cams_dir, "stereo_front_right", anchor_ts)
    L, R = cal["stereo_front_left"], cal["stereo_front_right"]
    fullsize = (L["w"], L["h"])
    # OpenCV stereoRectify wants R,T mapping cam1(left)->cam2(right): X_right = R@X_left + T
    M = np.linalg.inv(R["T_ego_cam"]) @ L["T_ego_cam"]
    Rrot, Tt = M[:3, :3], M[:3, 3]
    R1, R2, P1, P2, Q, _r1, _r2 = cv2.stereoRectify(L["K"], L["dist"], R["K"], R["dist"], fullsize, Rrot, Tt, flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    mlx, mly = cv2.initUndistortRectifyMap(L["K"], L["dist"], R1, P1, fullsize, cv2.CV_32FC1)
    mrx, mry = cv2.initUndistortRectifyMap(R["K"], R["dist"], R2, P2, fullsize, cv2.CV_32FC1)
    rl = cv2.remap(img_l, mlx, mly, cv2.INTER_LINEAR)
    rr = cv2.remap(img_r, mrx, mry, cv2.INTER_LINEAR)
    # downscale for SGM
    ds = (int(fullsize[0] * SCALE), int(fullsize[1] * SCALE))
    gl = cv2.cvtColor(cv2.resize(rl, ds), cv2.COLOR_RGB2GRAY)
    gr = cv2.cvtColor(cv2.resize(rr, ds), cv2.COLOR_RGB2GRAY)
    nd = 128
    sg = cv2.StereoSGBM_create(minDisparity=0, numDisparities=nd, blockSize=5,
                               P1=8 * 3 * 25, P2=32 * 3 * 25, disp12MaxDiff=1,
                               uniquenessRatio=10, speckleWindowSize=100, speckleRange=2, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
    disp = sg.compute(gl, gr).astype(np.float32) / 16.0  # at downscaled res
    # upscale disparity to full res (disparity scales with width)
    disp_full = cv2.resize(disp, fullsize, interpolation=cv2.INTER_NEAREST) / SCALE
    disp_pos = disp_full > 0.5
    pts3d = cv2.reprojectImageTo3D(disp_full, Q)  # rectified-left frame, meters
    Z = pts3d[..., 2]
    valid = disp_pos & np.isfinite(Z) & (Z > DMIN) & (Z < DMAX)
    diag = {
        "baseline_m": float(np.linalg.norm(Tt)),
        "disp_pos_count": int(disp_pos.sum()),
        "disp_p50": (float(np.percentile(disp_full[disp_pos], 50)) if disp_pos.any() else None),
        "disp_p95": (float(np.percentile(disp_full[disp_pos], 95)) if disp_pos.any() else None),
        "Z_p50_on_disp": (float(np.percentile(Z[disp_pos & np.isfinite(Z)], 50)) if (disp_pos & np.isfinite(Z)).any() else None),
        "Z_p95_on_disp": (float(np.percentile(Z[disp_pos & np.isfinite(Z)], 95)) if (disp_pos & np.isfinite(Z)).any() else None),
        "valid_after_range_count": int(valid.sum()),
    }
    # rectified-left -> original-left -> ego
    pr = pts3d[valid].reshape(-1, 3)
    p_origleft = (R1.T @ pr.T).T
    p_ego = (L["T_ego_cam"][:3, :3] @ p_origleft.T).T + L["T_ego_cam"][:3, 3]
    u, v, depth_ego = ego_to_uv(p_ego)
    ui = np.round(u).astype(np.int64); vi = np.round(v).astype(np.int64)
    inb = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    ui, vi, depth_ego, p_ego = ui[inb], vi[inb], depth_ego[inb], p_ego[inb]
    # z-buffer scatter (nearest depth wins)
    stereo_depth = np.full((H, W), np.inf, np.float32)
    stereo_pt = np.zeros((H, W, 3), np.float32)
    flat = vi * W + ui
    order = np.argsort(-depth_ego)  # far first so near overwrites
    flat_o, d_o, p_o = flat[order], depth_ego[order], p_ego[order]
    sd = stereo_depth.reshape(-1); spt = stereo_pt.reshape(-1, 3)
    sd[flat_o] = d_o; spt[flat_o] = p_o
    surface_valid = np.isfinite(stereo_depth)
    stereo_depth[~surface_valid] = 0.0
    return {"surface_valid": surface_valid, "depth": stereo_depth, "pt_ego": stereo_pt,
            "n_stereo_points": int(valid.sum()), "ts_l": ts_l, "ts_r": ts_r, "diag": diag,
            "rectified_left_preview": cv2.resize(rl, (W // 2, int(L["h"] * (W // 2) / L["w"])))}


def sample_cam(img, K, T_ego_cam, X_ego):
    # X_ego: (N,3) -> pixel sample colors; returns (N,3) rgb float and visible mask
    Tci = np.linalg.inv(T_ego_cam)
    Xc = (Tci[:3, :3] @ X_ego.T).T + Tci[:3, 3]
    z = Xc[:, 2]
    uv = (K @ (Xc / np.maximum(z[:, None], EPS)).T).T
    px, py = uv[:, 0], uv[:, 1]
    h, w = img.shape[:2]
    vis = (z > 0.1) & (px >= 0) & (px < w - 1) & (py >= 0) & (py < h - 1)
    cols = np.zeros((X_ego.shape[0], 3), np.float32)
    xi = np.clip(px[vis].astype(np.int64), 0, w - 1); yi = np.clip(py[vis].astype(np.int64), 0, h - 1)
    cols[vis] = img[yi, xi].astype(np.float32)
    return cols, vis


def loo_with_stereo_depth(stereo, frame, ring_cams, viscount, order, valid_any):
    import cv2
    # On surface_valid & co-observed pixels, use stereo depth to project the ego 3D point
    # into the owner ring cam and the 2nd-strongest ring cam; compare colors (depth-correct).
    sv = stereo["surface_valid"]
    co = sv & (viscount >= 2)
    ys, xs = np.where(co)
    if ys.size == 0:
        return {"loo_pixels": 0}, np.zeros((H, W), bool)
    X = stereo["pt_ego"][ys, xs]  # (N,3) ego points (metric, from stereo)
    owner = order[0][ys, xs]; second = order[1][ys, xs]
    def rgb2lab1(c):
        return cv2.cvtColor(c.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    N = ys.size
    col_o = np.zeros((N, 3), np.float32); col_s = np.zeros((N, 3), np.float32)
    vis_o = np.zeros(N, bool); vis_s = np.zeros(N, bool)
    cache = {}
    for ci, cam in enumerate(ring_cams):
        cal = frame.calibrations[cam]; cache[ci] = (frame.images[cam], cal.K, cal.T_ego_cam)
    for ci in range(len(ring_cams)):
        img, K, T = cache[ci]
        mo = owner == ci
        if mo.any():
            c, vv = sample_cam(img, K, T, X[mo]); col_o[mo] = c; vo = np.zeros(N, bool); vis_o[mo] = vv
        ms = second == ci
        if ms.any():
            c, vv = sample_cam(img, K, T, X[ms]); col_s[ms] = c; vis_s[ms] = vv
    both = vis_o & vis_s
    dE = np.sqrt(((rgb2lab1(col_o) - rgb2lab1(col_s)) ** 2).sum(1))
    validated = both & (dE < DE_THR)
    vmap = np.zeros((H, W), bool); vmap[ys[validated], xs[validated]] = True
    res = {
        "loo_pixels": int(both.sum()),
        "loo_validated_pixels": int(validated.sum()),
        "loo_validated_rate": (float(validated.sum() / both.sum()) if both.sum() else None),
        "depth_correct_dE_p50": (float(np.percentile(dE[both], 50)) if both.sum() else None),
        "depth_correct_dE_p95": (float(np.percentile(dE[both], 95)) if both.sum() else None),
    }
    return res, vmap


def run_case(case_spec, run_name, workdir):
    import cv2
    log_dir, ts, frame, ring_cams, weights, valid_stack, viscount, valid_any, order, owner, base, labs = ring_skeleton(case_spec, workdir)
    stereo = stereo_depth_to_erp(log_dir, ts)
    sv = stereo["surface_valid"]
    # forward sector = columns where stereo lands; task band rows
    cols = np.where(sv.any(0))[0]
    fwd = np.zeros((H, W), bool)
    if cols.size:
        fwd[:, cols.min():cols.max() + 1] = True
    col_cov = valid_any.mean(1); band_rows = np.where(col_cov >= 0.5)[0]
    band = np.zeros((H, W), bool)
    if band_rows.size: band[band_rows.min():band_rows.max() + 1, :] = True
    relevant = fwd & band & (viscount == 1)            # forward single-source near-ground = what stereo should rescue
    raw_visible = sv & valid_any
    loo, vmap = loo_with_stereo_depth(stereo, frame, ring_cams, viscount, order, valid_any)

    def frac(m, sub):
        d = int(sub.sum()); return (float(int((m & sub).sum()) / d) if d else None)

    metrics = {
        "n_stereo_points": stereo["n_stereo_points"],
        "stereo_surface_valid_fraction_fullerp": float(sv.mean()),
        "forward_sector_cols": [int(cols.min()), int(cols.max())] if cols.size else None,
        "task_valid_band_rows": [int(band_rows.min()), int(band_rows.max())] if band_rows.size else None,
        # coverage on the forward task band
        "fwd_band_surface_valid_frac": frac(sv, fwd & band),
        "fwd_band_raw_visible_frac": frac(raw_visible, fwd & band),
        # the headline: how much of the forward single-source near-ground does stereo rescue
        "relevant_band_px": int(relevant.sum()),
        "relevant_band_surface_valid_frac": frac(sv, relevant),
        "relevant_band_raw_visible_frac": frac(raw_visible, relevant),
        # validated GREEN via depth-correct LOO (only on co-observed; single-source can't be LOO'd)
        "validated_green_fwd_band_frac": frac(vmap, fwd & band),
        "validated_green_taskvalid_frac": frac(vmap, band),
        "loo": loo,
        "stereo_diag": stereo.get("diag"),
    }
    # go/no-go vs pre-registered bars
    sv_rel = metrics["relevant_band_surface_valid_frac"] or 0.0
    vg_tv = metrics["validated_green_taskvalid_frac"] or 0.0
    metrics["build_worthy"] = bool(sv_rel >= __BW_REL__ or vg_tv >= __BW_TV__)
    metrics["kill_stereo_below_10pct"] = bool(sv_rel < __KILL__)
    # sidecars + board
    save_rgb(REMOTE_OUT / f"{run_name}_b3_stereo_coverage_overlay.png", overlay(overlay(base, sv, (60, 220, 90), 0.5), relevant, (255, 180, 40), 0.4))
    save_rgb(REMOTE_OUT / f"{run_name}_b3_stereo_depth_heat.png", heat(stereo["depth"], sv))
    save_rgb(REMOTE_OUT / f"{run_name}_b3_validated_green_overlay.png", overlay(base, vmap, (40, 120, 255), 0.7))
    from PIL import Image, ImageDraw, ImageFont
    try: f = ImageFont.truetype("DejaVuSans.ttf", 15)
    except Exception: f = ImageFont.load_default()
    tiles = [("base", base),
             ("stereo surface green / relevant(fwd single-src) amber", overlay(overlay(base, sv, (60, 220, 90), 0.5), relevant, (255, 180, 40), 0.4)),
             ("stereo depth (m)", heat(stereo["depth"], sv)),
             ("validated GREEN (depth-correct LOO) blue", overlay(base, vmap, (40, 120, 255), 0.7))]
    ims = []
    for t, a in tiles:
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((760, 380))
        bar = Image.new("RGB", (760, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), t, (235, 235, 245), font=f)
        o = Image.new("RGB", (760, 404)); o.paste(bar, (0, 0)); o.paste(im, (0, 24)); ims.append(o)
    board = Image.new("RGB", (760, 404 * 4 + 70), (10, 10, 14)); d = ImageDraw.Draw(board)
    d.text((8, 6), f"{run_name} DB-76a Battery 3 forward-stereo (measurement-only)", (240, 240, 250), font=f)
    d.text((8, 30), f"surf_valid(rel)={metrics['relevant_band_surface_valid_frac']} validated_taskvalid={metrics['validated_green_taskvalid_frac']} build_worthy={metrics['build_worthy']} kill={metrics['kill_stereo_below_10pct']}", (210, 230, 210), font=f)
    yo = 70
    for o in ims: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_b3_board.jpg", quality=90)
    return {"case": run_name, "metrics": metrics, "stereo_ts": [stereo["ts_l"], stereo["ts_r"], int(ts)]}


def build_batch_board(summary):
    from PIL import Image, ImageDraw, ImageFont
    ps = [REMOTE_OUT / "02a00399_a000_bmw_b3_board.jpg", REMOTE_OUT / "0bae3b5e_a030_clean_far_b3_board.jpg"]
    ims = [Image.open(p) for p in ps if p.exists()]
    if not ims: return
    w = max(i.width for i in ims); board = Image.new("RGB", (w, sum(i.height for i in ims) + 40), (8, 8, 12))
    d = ImageDraw.Draw(board)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    d.text((8, 8), f"DB-76a Battery 3 forward-stereo coverage - build_worthy_any={summary.get('build_worthy_any')}", (240, 240, 250), font=f)
    yo = 40
    for im in ims: board.paste(im, (0, yo)); yo += im.height
    board.save(REMOTE_OUT / "DB76b_b3_review_board.jpg", quality=90)


try:
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    if not import_ok("av2"):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600, check=False)
    workdir = find_workdir()
    OUT["workdir_found"] = bool(workdir)
    if workdir is None: raise RuntimeError("workdir not found")
    sys.path.insert(0, str(workdir / "scripts" / "phase3")); sys.path.insert(0, str(workdir / "code"))
    reports = [run_case(cs, rn, workdir) for cs, rn in CASES]
    by_case = {r["case"]: r for r in reports}
    bw = any(r["metrics"]["build_worthy"] for r in reports)
    summary = {"status": "db76b_b3_complete", "measurement_only": True, "source_faithful_repair_claim_allowed": False,
               "pre_registered": TH, "by_case": by_case, "build_worthy_any": bool(bw),
               "kill_stereo_below_10pct_all": bool(all(r["metrics"]["kill_stereo_below_10pct"] for r in reports)),
               "runtime_s": round(time.time() - t0, 2)}
    build_batch_board(summary)
    (REMOTE_OUT / "DB76b_b3_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    (REMOTE_OUT / "DB76b_b3_claim.json").write_text(json.dumps({"brief": "DB-76a Battery 3", "classification": "measurement_only_forward_stereo_coverage", "rgb_repair": False, "source_faithful_repair": False, "red_promotion": False, "forward_only_does_not_help_side_curb": True}, indent=2), encoding="utf-8")
    OUT["status"] = "db76b_b3_completed"; OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db76b_b3_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB76B3_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB76B3_JSON_END")
'''
    code = code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__B3_RESULT__", REMOTE_OUT + "/DB76b_b3_remote_result.json")
    code = code.replace("__B3_TH__", json.dumps(B3_THRESHOLDS))
    code = code.replace("__BW_REL__", repr(B3_THRESHOLDS["build_worthy_relevant_band_converted_min"]))
    code = code.replace("__BW_TV__", repr(B3_THRESHOLDS["build_worthy_task_valid_validated_green_min"]))
    code = code.replace("__KILL__", repr(B3_THRESHOLDS["kill_stereo_band_coverage_lt"]))
    return code


def run_battery3(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_battery3_python())], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s=timeout_s)
    b3_remote = REMOTE_OUT + "/DB76b_b3_remote_result.json"
    remote_result = None
    raw = client.read_file(b3_remote, max_size_mb=12)
    if raw is not None:
        try: remote_result = json.loads(raw.decode("utf-8"))
        except Exception: remote_result = None
    if remote_result is None:
        rr = job.get("log_tail", "")
        if "DB76B3_JSON_BEGIN" in rr and "DB76B3_JSON_END" in rr:
            try: remote_result = json.loads(rr.split("DB76B3_JSON_BEGIN", 1)[1].split("DB76B3_JSON_END", 1)[0].strip())
            except Exception: remote_result = None
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    (OUT_DIR / "DB76b_b3_remote_result.json").write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = {}
    for key, (rn, mb) in B3_FETCH.items():
        local = (OUT_DIR if key in {"summary", "remote_result", "board"} else (OUT_DIR / "fetch")) / rn
        local.parent.mkdir(parents=True, exist_ok=True)
        rb = client.read_file(REMOTE_OUT + "/" + rn, max_size_mb=mb)
        if rb is None: fetched[key] = {"path": rel(local), "exists": False}
        else: local.write_bytes(rb); fetched[key] = {"path": rel(local), "exists": True, "bytes": len(rb)}
    summary = {}
    sp = OUT_DIR / "DB76b_b3_summary.json"
    if sp.exists():
        try: summary = json.loads(sp.read_text(encoding="utf-8"))
        except Exception: summary = {}
    if not summary: summary = remote_result.get("summary") or {}
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "status": "db76b_battery3",
        "brief": "DB-76a Battery 3 forward-stereo coverage (measurement-only)",
        "scope": {"measurement_only": True, "exec_count": 1, "runtime_type": status.get("runtime_type"),
                   "rgb_repair": False, "forward_stereo_only": True, "model_inference": False, "red_promotion": False, "a100_used": True},
        "runtime": {"secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file", "status": safe_status(status)},
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_status": remote_result.get("status"), "summary": summary, "fetched_outputs": fetched,
        "pre_registered_thresholds": B3_THRESHOLDS,
        "output_location": rel(OUT_DIR), "drive_output_location": "results/db76b_stereo_temporal_coverage/",
        "decision": {"build_worthy_any": bool(summary.get("build_worthy_any")), "kill_all": bool(summary.get("kill_stereo_below_10pct_all")),
                      "vision_check_required": True, "accepted_as_source_faithful_repair": False},
        "claim_boundary": ["DB-76a Battery 3 is measurement-only forward-stereo coverage; no RGB repair, no RED.",
                            "Forward only - does NOT help the side curb/wall-base (that is Battery 4).",
                            "validated GREEN = depth-correct cross-camera LOO on co-observed pixels; vision review required."],
    }
    scan = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    (OUT_DIR / "DB76b_b3_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "db76b_battery3", "remote_status": remote_result.get("status"),
            "build_worthy_any": bool(summary.get("build_worthy_any")), "secret_hits": manifest["strict_secret_scan"]["hit_count"],
            "manifest": rel(OUT_DIR / "DB76b_b3_manifest.json")}


B4_THRESHOLDS = {
    "window_frames": 10,                # anchor +/- N lidar sweeps
    "lidar_depth_min_m": 1.5,
    "lidar_depth_max_m": 80.0,
    "loo_residual_px_equiv_dE": 18.0,
    "near_ground_lower_fraction": 0.45,  # near-ground = lower part of the task-valid band
    "build_worthy_near_ground_dense_visible_min": 0.25,
    "thin_base_near_ground_dense_visible_lt": 0.10,
}

B4_FETCH = {
    "summary": ("DB76b_b4_summary.json", 16),
    "remote_result": ("DB76b_b4_remote_result.json", 16),
    "board": ("DB76b_b4_review_board.jpg", 60),
    "bmw_board": ("02a00399_a000_bmw_b4_board.jpg", 60),
    "bmw_cov": ("02a00399_a000_bmw_b4_multiframe_coverage_overlay.png", 30),
    "bmw_depth": ("02a00399_a000_bmw_b4_lidar_depth_heat.png", 30),
    "bmw_gain": ("02a00399_a000_bmw_b4_dense_gain_overlay.png", 30),
    "clean_board": ("0bae3b5e_a030_clean_far_b4_board.jpg", 60),
    "clean_cov": ("0bae3b5e_a030_clean_far_b4_multiframe_coverage_overlay.png", 30),
    "claim": ("DB76b_b4_claim.json", 16),
}


def remote_battery4_python() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__")
REMOTE_RESULT = pathlib.Path("__B4_RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [pathlib.Path("/content/waymo2panorama"), pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama")]
H, W = 1024, 2048
EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
MARKED_ROIS = {"left_road_patch": (250, 515, 460, 715), "lower_center_road_patch": (740, 595, 1035, 745),
               "center_lane_marking": (1030, 515, 1325, 735), "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760)}
TH = json.loads(r"""__B4_TH__""")
WINDOW = int(TH["window_frames"]); DMIN, DMAX = float(TH["lidar_depth_min_m"]), float(TH["lidar_depth_max_m"])
DE_THR = float(TH["loo_residual_px_equiv_dE"]); NG_FRAC = float(TH["near_ground_lower_fraction"])
DYN_CATS = {"REGULAR_VEHICLE", "PEDESTRIAN", "BICYCLIST", "MOTORCYCLIST", "BICYCLE", "MOTORCYCLE", "BUS", "LARGE_VEHICLE", "TRUCK", "VEHICULAR_TRAILER", "TRUCK_CAB", "BOX_TRUCK", "WHEELED_RIDER", "WHEELED_DEVICE", "DOG", "ANIMAL", "STROLLER"}

OUT = {"phase": "db76b_battery4_multiframe_lidar", "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "scope": {"measurement_only": True, "rgb_repair": False, "multiframe_lidar_accumulation": True, "model_inference": False, "red_promotion": False}}


def import_ok(n):
    try:
        __import__(n); return True
    except Exception:
        return False


def find_workdir():
    for c in WORKDIR_CANDIDATES:
        if (c / "scripts" / "phase3" / "run_a1_streetview_pipeline.py").exists():
            return c
    return None


def json_safe(o):
    if isinstance(o, dict): return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [json_safe(v) for v in o]
    if isinstance(o, np.ndarray): return o.tolist()
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o); return v if math.isfinite(v) else None
    if isinstance(o, (np.bool_,)): return bool(o)
    return o


def save_rgb(path, arr, q=92):
    import cv2
    a = np.clip(arr, 0, 255).astype("uint8")
    if path.suffix.lower() in (".jpg", ".jpeg"):
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), int(q)])
    else:
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR))


def heat(x, valid=None):
    import cv2
    a = x.astype(np.float32); vals = a[valid] if valid is not None and bool(np.any(valid)) else a.reshape(-1)
    if vals.size == 0: return np.zeros((*x.shape, 3), np.uint8)
    lo, hi = np.percentile(vals, [2, 98]); u = np.clip((a - lo) * 255.0 / max(hi - lo, 1e-6), 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.applyColorMap(u, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)


def overlay(rgb, mask, color, alpha=0.6):
    out = rgb.astype(np.float32).copy(); m = mask.astype(bool)
    for c in range(3):
        out[..., c][m] = (1 - alpha) * out[..., c][m] + alpha * color[c]
    return np.clip(out, 0, 255).astype(np.uint8)


def ego_to_uv(P):
    n = np.linalg.norm(P, axis=1); d = P / np.maximum(n[:, None], EPS)
    theta = np.arctan2(d[:, 1], d[:, 0]); phi = np.arcsin(np.clip(d[:, 2], -1, 1))
    u = (np.pi - theta) / (2 * np.pi) * W - 0.5; v = (np.pi / 2 - phi) / np.pi * H - 0.5
    return u, v, n


def ring_skeleton(case_spec, workdir):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    import cv2
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    loader = AV2RingLoader(log_dir); ts = loader.anchor_timestamps_ns()[anchor_idx]; frame = loader.load_synced_frame(ts)
    weights, labs = [], []
    for cam in RING_CAMS_7:
        cal = frame.calibrations[cam]
        rgb, _a, w = render_camera_to_erp(image=frame.images[cam], K=cal.K, T_ego_cam=cal.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        weights.append(w.astype(np.float32)); labs.append(rgb.astype(np.uint8))
    weights = np.stack(weights, 0); valid_stack = weights > EPS
    viscount = valid_stack.sum(0).astype(np.int32); valid_any = viscount > 0
    order = np.argsort(-weights, axis=0); owner = order[0].astype(np.int32)
    base = np.take_along_axis(np.stack(labs, 0), np.where(valid_any, owner, 0)[None, :, :, None], axis=0)[0]
    base[~valid_any] = 0
    return log_dir, ts, frame, list(RING_CAMS_7), viscount, valid_any, order, owner, base


def load_pose_interp(log_dir):
    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp
    p = pd.read_feather(log_dir / "city_SE3_egovehicle.feather").sort_values("timestamp_ns").drop_duplicates("timestamp_ns").reset_index(drop=True)
    ts_int = p["timestamp_ns"].to_numpy(np.int64)
    t0 = int(ts_int[0])
    ts = (ts_int - t0).astype(np.float64)  # relative ns: precision-safe in float64
    quat = p[["qx", "qy", "qz", "qw"]].to_numpy(); tx = p[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
    keep = np.concatenate([[True], np.diff(ts) > 0])  # strictly increasing guard
    ts, quat, tx = ts[keep], quat[keep], tx[keep]
    slerp = Slerp(ts, Rotation.from_quat(quat))
    lo, hi = ts.min(), ts.max()

    def city_T_ego(t):
        tc = float(np.clip(float(int(t) - t0), lo, hi)); R = slerp(tc).as_matrix()
        tr = np.array([np.interp(tc, ts, tx[:, i]) for i in range(3)])
        return R, tr

    def tr_interp(tarr):
        tc = np.clip((np.asarray(tarr, np.int64) - t0).astype(np.float64), lo, hi)
        return np.stack([np.interp(tc, ts, tx[:, i]) for i in range(3)], 1)
    return city_T_ego, tr_interp


def load_boxes(log_dir):
    import pandas as pd
    from scipy.spatial.transform import Rotation
    ap = log_dir / "annotations.feather"
    if not ap.exists():
        return None
    a = pd.read_feather(ap)
    return a


def boxes_at(ann, ts):
    from scipy.spatial.transform import Rotation
    if ann is None:
        return []
    tss = ann["timestamp_ns"].to_numpy(np.int64)
    uniq = np.unique(tss); nt = uniq[np.argmin(np.abs(uniq - ts))]
    rows = ann[ann["timestamp_ns"] == nt]
    out = []
    for _, r in rows.iterrows():
        if "category" in rows.columns and str(r["category"]).upper() not in DYN_CATS:
            continue
        c = np.array([r["tx_m"], r["ty_m"], r["tz_m"]], float)
        sz = np.array([r["length_m"], r["width_m"], r["height_m"]], float)
        Rb = Rotation.from_quat([r["qx"], r["qy"], r["qz"], r["qw"]]).as_matrix()
        out.append((c, sz, Rb))
    return out


def remove_dynamic(pts, boxes, pad=0.3):
    if not boxes:
        return np.ones(len(pts), bool)
    keep = np.ones(len(pts), bool)
    for c, sz, Rb in boxes:
        local = (pts - c) @ Rb  # ego->box
        half = sz / 2 + pad
        inside = (np.abs(local[:, 0]) < half[0]) & (np.abs(local[:, 1]) < half[1]) & (np.abs(local[:, 2]) < half[2])
        keep &= ~inside
    return keep


def read_sweep(path):
    import pandas as pd
    df = pd.read_feather(path)
    xyz = df[["x", "y", "z"]].to_numpy(np.float64)
    off = df["offset_ns"].to_numpy(np.int64) if "offset_ns" in df.columns else np.zeros(len(df), np.int64)
    return xyz, off


def accumulate_lidar(log_dir, anchor_ts, window, city_T_ego, tr_interp, ann):
    sweeps = sorted((log_dir / "sensors" / "lidar").glob("*.feather"))
    stss = np.array([int(p.stem) for p in sweeps], np.int64)
    ai = int(np.argmin(np.abs(stss - anchor_ts)))
    Ra, ta = city_T_ego(anchor_ts)
    diag = {"n_sweeps": 0, "annotation_used": ann is not None, "offset_used": False, "raw_points": 0, "kept_after_dynamic": 0}

    def one_window(lo, hi):
        acc = []
        for si in range(max(0, ai + lo), min(len(sweeps), ai + hi + 1)):
            sts = int(stss[si]); xyz, off = read_sweep(sweeps[si])
            diag["raw_points"] += len(xyz)
            if off.any(): diag["offset_used"] = True
            keep = remove_dynamic(xyz, boxes_at(ann, sts))
            xyz = xyz[keep]; off = off[keep]; diag["kept_after_dynamic"] += len(xyz)
            # per-return RS: sweep-level rotation + per-point translation interp at point time
            Rsw, _ = city_T_ego(sts)
            tpt = sts + off
            tr = tr_interp(tpt.astype(np.float64))
            city = (Rsw @ xyz.T).T + tr
            ego_anchor = (city - ta) @ Ra  # inv rotation = R^T applied as (x)@R
            acc.append(ego_anchor)
            diag["n_sweeps"] += 1
        return np.concatenate(acc, 0) if acc else np.zeros((0, 3))

    multi = one_window(-window, window)
    single = one_window(0, 0)
    return multi, single, diag


def project_pts(pts):
    surface = np.zeros((H, W), bool); depth = np.full((H, W), np.inf, np.float32); pt = np.zeros((H, W, 3), np.float32)
    if len(pts) == 0:
        depth[:] = 0.0; return surface, depth, pt
    u, v, d = ego_to_uv(pts)
    m = (d > DMIN) & (d < DMAX)
    ui = np.round(u[m]).astype(np.int64); vi = np.round(v[m]).astype(np.int64); dd = d[m]; pp = pts[m]
    inb = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    ui, vi, dd, pp = ui[inb], vi[inb], dd[inb], pp[inb]
    flat = vi * W + ui; order = np.argsort(-dd)
    sd = depth.reshape(-1); sp = pt.reshape(-1, 3)
    sd[flat[order]] = dd[order]; sp[flat[order]] = pp[order]
    surface = np.isfinite(depth); depth[~surface] = 0.0
    return surface, depth, pt


def sample_cam(img, K, T_ego_cam, X):
    Tci = np.linalg.inv(T_ego_cam); Xc = (Tci[:3, :3] @ X.T).T + Tci[:3, 3]; z = Xc[:, 2]
    uv = (K @ (Xc / np.maximum(z[:, None], EPS)).T).T; px, py = uv[:, 0], uv[:, 1]
    h, w = img.shape[:2]; vis = (z > 0.1) & (px >= 0) & (px < w - 1) & (py >= 0) & (py < h - 1)
    cols = np.zeros((X.shape[0], 3), np.float32)
    cols[vis] = img[np.clip(py[vis].astype(np.int64), 0, h - 1), np.clip(px[vis].astype(np.int64), 0, w - 1)].astype(np.float32)
    return cols, vis


def loo_depth_correct(surface, pt, frame, ring_cams, viscount, order, valid_any):
    import cv2
    co = surface & (viscount >= 2); ys, xs = np.where(co)
    if ys.size == 0: return {"loo_pixels": 0}, np.zeros((H, W), bool)
    X = pt[ys, xs]; owner = order[0][ys, xs]; second = order[1][ys, xs]; N = ys.size
    co_o = np.zeros((N, 3), np.float32); co_s = np.zeros((N, 3), np.float32); vo = np.zeros(N, bool); vs = np.zeros(N, bool)
    for ci, cam in enumerate(ring_cams):
        cal = frame.calibrations[cam]; img = frame.images[cam]
        mo = owner == ci
        if mo.any():
            c, vv = sample_cam(img, cal.K, cal.T_ego_cam, X[mo]); co_o[mo] = c; vo[mo] = vv
        ms = second == ci
        if ms.any():
            c, vv = sample_cam(img, cal.K, cal.T_ego_cam, X[ms]); co_s[ms] = c; vs[ms] = vv
    both = vo & vs
    lab = lambda a: cv2.cvtColor(a.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    dE = np.sqrt(((lab(co_o) - lab(co_s)) ** 2).sum(1)); validated = both & (dE < DE_THR)
    vmap = np.zeros((H, W), bool); vmap[ys[validated], xs[validated]] = True
    return {"loo_pixels": int(both.sum()), "loo_validated_rate": (float(validated.sum() / both.sum()) if both.sum() else None),
            "depth_correct_dE_p50": (float(np.percentile(dE[both], 50)) if both.sum() else None)}, vmap


def run_case(case_spec, run_name, workdir):
    log_dir, ts, frame, ring_cams, viscount, valid_any, order, owner, base = ring_skeleton(case_spec, workdir)
    city_T_ego, tr_interp = load_pose_interp(log_dir); ann = load_boxes(log_dir)
    multi, single, diag = accumulate_lidar(log_dir, ts, WINDOW, city_T_ego, tr_interp, ann)
    sv_m, depth_m, pt_m = project_pts(multi)
    sv_s, _ds, _ps = project_pts(single)
    raw_vis = sv_m & valid_any
    loo, vmap = loo_depth_correct(sv_m, pt_m, frame, ring_cams, viscount, order, valid_any)
    # bands
    col_cov = valid_any.mean(1); br = np.where(col_cov >= 0.5)[0]
    band = np.zeros((H, W), bool); y0, y1 = (int(br.min()), int(br.max())) if br.size else (0, H - 1)
    band[y0:y1 + 1, :] = True
    ng_top = int(y0 + NG_FRAC * (y1 - y0)); near_ground = np.zeros((H, W), bool); near_ground[ng_top:y1 + 1, :] = True
    near_ground &= band
    ng_single_src = near_ground & (viscount == 1)
    cw = np.zeros((H, W), bool); x0, yy0, x1, yy1 = MARKED_ROIS["right_curb_sidewalk_wall_base"]; cw[yy0:yy1, x0:x1] = True

    def frac(m, sub):
        d = int(sub.sum()); return (float(int((m & sub).sum()) / d) if d else None)

    metrics = {
        "diag": diag,
        "n_points_multiframe": int(len(multi)), "n_points_singleframe": int(len(single)),
        "surface_valid_fullerp_multi": float(sv_m.mean()), "surface_valid_fullerp_single": float(sv_s.mean()),
        # leader's core question: near-ground dense+raw-visible coverage, multi vs single
        "near_ground_dense_visible_multi": frac(raw_vis, near_ground),
        "near_ground_dense_visible_single": frac(sv_s & valid_any, near_ground),
        "near_ground_singlesrc_dense_visible_multi": frac(raw_vis, ng_single_src),
        "near_ground_singlesrc_dense_visible_single": frac(sv_s & valid_any, ng_single_src),
        "curb_wall_dense_visible_multi": frac(raw_vis, cw & near_ground),
        "curb_wall_dense_visible_single": frac(sv_s & valid_any, cw & near_ground),
        # depth quality where ring-LOO possible (co-observed only)
        "loo": loo,
        "validated_green_near_ground_frac": frac(vmap, near_ground),
        "near_ground_band_rows": [ng_top, y1],
    }
    ngv = metrics["near_ground_dense_visible_multi"] or 0.0
    metrics["build_worthy_base_sufficient"] = bool(ngv >= __BW_NG__)
    metrics["thin_base"] = bool(ngv < __KILL_NG__)
    # sidecars
    cov = overlay(overlay(base, sv_m, (60, 220, 90), 0.5), ng_single_src, (255, 180, 40), 0.35)
    save_rgb(REMOTE_OUT / f"{run_name}_b4_multiframe_coverage_overlay.png", cov)
    save_rgb(REMOTE_OUT / f"{run_name}_b4_lidar_depth_heat.png", heat(depth_m, sv_m))
    gain = overlay(overlay(base, sv_s, (80, 80, 255), 0.5), sv_m & ~sv_s, (40, 255, 120), 0.6)
    save_rgb(REMOTE_OUT / f"{run_name}_b4_dense_gain_overlay.png", gain)
    from PIL import Image, ImageDraw, ImageFont
    try: f = ImageFont.truetype("DejaVuSans.ttf", 15)
    except Exception: f = ImageFont.load_default()
    tiles = [("base", base), ("multi surface green / near-ground single-src amber", cov),
             ("lidar depth (m)", heat(depth_m, sv_m)), ("single-frame blue / multi-frame GAIN green", gain)]
    ims = []
    for t, a in tiles:
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((760, 380))
        bar = Image.new("RGB", (760, 24), (15, 15, 22)); ImageDraw.Draw(bar).text((6, 4), t, (235, 235, 245), font=f)
        o = Image.new("RGB", (760, 404)); o.paste(bar, (0, 0)); o.paste(im, (0, 24)); ims.append(o)
    board = Image.new("RGB", (760, 404 * 4 + 72), (10, 10, 14)); d = ImageDraw.Draw(board)
    d.text((8, 6), f"{run_name} DB-76a Battery 4 multi-frame LiDAR (measurement-only)", (240, 240, 250), font=f)
    d.text((8, 28), f"near-ground dense+visible multi={metrics['near_ground_dense_visible_multi']} single={metrics['near_ground_dense_visible_single']}", (210, 230, 210), font=f)
    d.text((8, 50), f"curb/wall multi={metrics['curb_wall_dense_visible_multi']} single={metrics['curb_wall_dense_visible_single']} | base_sufficient={metrics['build_worthy_base_sufficient']} thin={metrics['thin_base']}", (210, 230, 210), font=f)
    yo = 72
    for o in ims: board.paste(o, (0, yo)); yo += o.height
    board.save(REMOTE_OUT / f"{run_name}_b4_board.jpg", quality=90)
    return {"case": run_name, "metrics": metrics, "anchor_ts": int(ts)}


def build_batch_board(summary):
    from PIL import Image, ImageDraw, ImageFont
    ps = [REMOTE_OUT / "02a00399_a000_bmw_b4_board.jpg", REMOTE_OUT / "0bae3b5e_a030_clean_far_b4_board.jpg"]
    ims = [Image.open(p) for p in ps if p.exists()]
    if not ims: return
    w = max(i.width for i in ims); board = Image.new("RGB", (w, sum(i.height for i in ims) + 40), (8, 8, 12)); d = ImageDraw.Draw(board)
    try: f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: f = ImageFont.load_default()
    d.text((8, 8), f"DB-76a Battery 4 multi-frame LiDAR geometry base - base_sufficient_any={summary.get('base_sufficient_any')}", (240, 240, 250), font=f)
    yo = 40
    for im in ims: board.paste(im, (0, yo)); yo += im.height
    board.save(REMOTE_OUT / "DB76b_b4_review_board.jpg", quality=90)


try:
    t0 = time.time(); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    if not import_ok("av2"):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600, check=False)
    workdir = find_workdir(); OUT["workdir_found"] = bool(workdir)
    if workdir is None: raise RuntimeError("workdir not found")
    sys.path.insert(0, str(workdir / "scripts" / "phase3")); sys.path.insert(0, str(workdir / "code"))
    reports = [run_case(cs, rn, workdir) for cs, rn in CASES]
    by_case = {r["case"]: r for r in reports}
    base_ok = any(r["metrics"]["build_worthy_base_sufficient"] for r in reports)
    summary = {"status": "db76b_b4_complete", "measurement_only": True, "pre_registered": TH, "by_case": by_case,
               "base_sufficient_any": bool(base_ok), "thin_base_all": bool(all(r["metrics"]["thin_base"] for r in reports)),
               "runtime_s": round(time.time() - t0, 2)}
    build_batch_board(summary)
    (REMOTE_OUT / "DB76b_b4_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    (REMOTE_OUT / "DB76b_b4_claim.json").write_text(json.dumps({"brief": "DB-76a Battery 4", "classification": "measurement_only_multiframe_lidar_geometry_base", "rgb_repair": False, "source_faithful_repair": False, "red_promotion": False, "note": "single-source near-ground has no ring-LOO; coverage = dense+raw-visible static surface, depth quality only validated on co-observed subset"}, indent=2), encoding="utf-8")
    OUT["status"] = "db76b_b4_completed"; OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db76b_b4_failed"; OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB76B4_JSON_BEGIN"); print(json.dumps(json_safe(OUT), separators=(",", ":"))); print("DB76B4_JSON_END")
'''
    code = code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__B4_RESULT__", REMOTE_OUT + "/DB76b_b4_remote_result.json")
    code = code.replace("__B4_TH__", json.dumps(B4_THRESHOLDS))
    code = code.replace("__BW_NG__", repr(B4_THRESHOLDS["build_worthy_near_ground_dense_visible_min"]))
    code = code.replace("__KILL_NG__", repr(B4_THRESHOLDS["thin_base_near_ground_dense_visible_lt"]))
    return code


def run_battery4(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_battery4_python())], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job = poll_job(client, submit["job_id"], timeout_s=timeout_s)
    b4_remote = REMOTE_OUT + "/DB76b_b4_remote_result.json"
    remote_result = None
    raw = client.read_file(b4_remote, max_size_mb=12)
    if raw is not None:
        try: remote_result = json.loads(raw.decode("utf-8"))
        except Exception: remote_result = None
    if remote_result is None:
        rr = job.get("log_tail", "")
        if "DB76B4_JSON_BEGIN" in rr and "DB76B4_JSON_END" in rr:
            try: remote_result = json.loads(rr.split("DB76B4_JSON_BEGIN", 1)[1].split("DB76B4_JSON_END", 1)[0].strip())
            except Exception: remote_result = None
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    (OUT_DIR / "DB76b_b4_remote_result.json").write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = {}
    for key, (rn, mb) in B4_FETCH.items():
        local = (OUT_DIR if key in {"summary", "remote_result", "board"} else (OUT_DIR / "fetch")) / rn
        local.parent.mkdir(parents=True, exist_ok=True)
        rb = client.read_file(REMOTE_OUT + "/" + rn, max_size_mb=mb)
        if rb is None: fetched[key] = {"path": rel(local), "exists": False}
        else: local.write_bytes(rb); fetched[key] = {"path": rel(local), "exists": True, "bytes": len(rb)}
    summary = {}
    sp = OUT_DIR / "DB76b_b4_summary.json"
    if sp.exists():
        try: summary = json.loads(sp.read_text(encoding="utf-8"))
        except Exception: summary = {}
    if not summary: summary = remote_result.get("summary") or {}
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "status": "db76b_battery4",
        "brief": "DB-76a Battery 4 multi-frame LiDAR geometry base (measurement-only)",
        "scope": {"measurement_only": True, "exec_count": 1, "runtime_type": status.get("runtime_type"),
                   "rgb_repair": False, "multiframe_lidar": True, "model_inference": False, "red_promotion": False, "gpu_bound": False},
        "runtime": {"secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file", "status": safe_status(status)},
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_status": remote_result.get("status"), "summary": summary, "fetched_outputs": fetched,
        "pre_registered_thresholds": B4_THRESHOLDS,
        "output_location": rel(OUT_DIR), "drive_output_location": "results/db76b_stereo_temporal_coverage/",
        "decision": {"base_sufficient_any": bool(summary.get("base_sufficient_any")), "thin_base_all": bool(summary.get("thin_base_all")),
                      "vision_check_required": True, "accepted_as_source_faithful_repair": False},
        "claim_boundary": ["DB-76a Battery 4 is measurement-only multi-frame LiDAR geometry base; no RGB repair, no RED.",
                            "single-source near-ground has no ring-LOO; coverage = dense + raw-visible static surface; depth quality validated only on co-observed subset.",
                            "vision review required (does the accumulated surface look like a real static surface, not smear?)."],
    }
    scan = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    (OUT_DIR / "DB76b_b4_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "db76b_battery4", "remote_status": remote_result.get("status"),
            "base_sufficient_any": bool(summary.get("base_sufficient_any")), "secret_hits": manifest["strict_secret_scan"]["hit_count"],
            "manifest": rel(OUT_DIR / "DB76b_b4_manifest.json")}


def check_compile() -> None:
    py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    compile(remote_preflight_python(), "<db76b_preflight_remote>", "exec")
    compile(remote_battery3_python(), "<db76b_battery3_remote>", "exec")
    compile(remote_battery4_python(), "<db76b_battery4_remote>", "exec")
    print(json.dumps({"status": "compile_ok", "wrapper": True, "preflight_remote": True, "battery3_remote": True, "battery4_remote": True}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--battery3", action="store_true")
    parser.add_argument("--battery4", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=900)
    args = parser.parse_args()
    if args.check:
        check_compile(); return
    if args.preflight:
        print(json.dumps(run_preflight(args.timeout_s), indent=2)); return
    if args.battery3:
        print(json.dumps(run_battery3(args.timeout_s), indent=2)); return
    if args.battery4:
        print(json.dumps(run_battery4(args.timeout_s), indent=2)); return
    print(json.dumps({"status": "ready", "message": "Use --check / --preflight / --battery3 / --battery4 (DB-76a batteries 3-4)."}, indent=2))


if __name__ == "__main__":
    main()
