"""DB-238 dense temporal scan - how much does sparse anchor sampling miss?

The screening assumption "one frame characterises the log" is falsified: on
00a6ffc1 the worst-pair residual goes 24.85 (a86) -> 56.75 (a100) -> 11.80
(a109).  The defect is triggered by moving entities crossing the overlap, so it
is transient.

Scanning all 7 pairs over all 93 frames of 555 logs is ~69 h and impossible.
But the defect always shows up on ONE pair, and which pair it is stays stable
within a log.  So: scan the log's own worst pair densely over the whole delivery
window, using only the two cameras that pair needs.  That is 2/7 of the imagery
and 1/7 of the compute, and it yields the quantity actually needed - the
distribution of the per-frame peak, and therefore the miss rate of sparse
sampling.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/content")
import db238_screen as SC  # noqa: E402

V15 = "/content/drive/MyDrive/koi_waymo2pano_colab/datasets/av2_1plus92_v15"
OUT = ("/content/drive/MyDrive/koi_waymo2pano_colab/results/"
       "db238_scene_band_screening")
WORK = "/content/db238_dense"
LEDGER = os.path.join(OUT, "dense_scan_ledger.json")


def window_of(prefix):
    import glob
    for p in glob.glob(os.path.join(V15, "db144_v15_ledger_*.json")):
        try:
            j = json.load(open(p))
        except Exception:
            continue
        rec = (j.get("logs") or {}).get(prefix)
        if rec and isinstance(rec.get("window"), list) and len(rec["window"]) == 2:
            return int(rec["window"][0]), int(rec["window"][1])
    return None


def fetch_pair_window(uuid, split, cams_needed, frame_idxs, dest, n_lidar=2):
    """Download only the two cameras of one pair, across many frames."""
    os.makedirs(dest, exist_ok=True)
    base = f"{SC.S3}/{split}/{uuid}"
    cmds = []
    for rel in ("calibration/intrinsics.feather",
                "calibration/egovehicle_SE3_sensor.feather"):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)) or dest, exist_ok=True)
        cmds.append(f"cp {base}/{rel} {os.path.join(dest, rel)}")
    ts = {c: SC._ls_timestamps(f"{base}/sensors/cameras/{c}", ".jpg") for c in cams_needed}
    ref = SC._ls_timestamps(f"{base}/sensors/cameras/ring_front_center", ".jpg")
    lts = SC._ls_timestamps(f"{base}/sensors/lidar", ".feather")
    if not ref or not lts or any(not v for v in ts.values()):
        raise RuntimeError("listing incomplete")
    frames, want_l = [], set()
    for fi in frame_idxs:
        i = int(np.clip(fi, 0, len(ref) - 1))
        ta = ref[i]
        rec = {"anchor_idx": i, "cam_ts": {}}
        for c in cams_needed:
            t = min(ts[c], key=lambda v: abs(v - ta))
            rec["cam_ts"][c] = t
            cmds.append(f"cp {base}/sensors/cameras/{c}/{t}.jpg "
                        f"{os.path.join(dest, 'sensors', 'cameras', c, str(t))}.jpg")
        rec["lidar_ts"] = sorted(lts, key=lambda v: abs(v - ta))[:n_lidar]
        want_l.update(rec["lidar_ts"])
        frames.append(rec)
    for c in cams_needed:
        os.makedirs(os.path.join(dest, "sensors", "cameras", c), exist_ok=True)
    os.makedirs(os.path.join(dest, "sensors", "lidar"), exist_ok=True)
    for t in sorted(want_l):
        cmds.append(f"cp {base}/sensors/lidar/{t}.feather "
                    f"{os.path.join(dest, 'sensors', 'lidar', str(t))}.feather")
    script = os.path.join(dest, "_f.txt")
    with open(script, "w") as fh:
        fh.write("\n".join(sorted(set(cmds))) + "\n")
    r = SC._s5(["run", script], timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"s5cmd failed: {r.stderr[-500:]}")
    return frames


def dense_scan_log(prefix, uuid, split, pair, step=1, n_lidar=2):
    t0 = time.time()
    win = window_of(prefix)
    if win is None:
        return {"prefix": prefix, "ok": False, "error": "no window in v15 ledger"}
    a, b = pair.split("|")
    idxs = list(range(win[0], win[1] + 1, step))
    dest = os.path.join(WORK, uuid)
    try:
        cached = SC.cached_log_dir(uuid)
        if cached is not None:
            dest = cached
            ref = sorted(int(os.path.basename(p)[:-4]) for p in
                         __import__("glob").glob(os.path.join(
                             dest, "sensors", "cameras", "ring_front_center", "*.jpg")))
            frames = []
            for fi in idxs:
                i = int(np.clip(fi, 0, len(ref) - 1))
                ta = ref[i]
                rec = {"anchor_idx": i, "cam_ts": {}}
                for c in (a, b):
                    cts = sorted(int(os.path.basename(p)[:-4]) for p in
                                 __import__("glob").glob(os.path.join(
                                     dest, "sensors", "cameras", c, "*.jpg")))
                    rec["cam_ts"][c] = min(cts, key=lambda v: abs(v - ta))
                lts = sorted(int(os.path.basename(p)[:-8]) for p in
                             __import__("glob").glob(os.path.join(
                                 dest, "sensors", "lidar", "*.feather")))
                rec["lidar_ts"] = sorted(lts, key=lambda v: abs(v - ta))[:n_lidar]
                frames.append(rec)
        else:
            frames = fetch_pair_window(uuid, split, (a, b), idxs, dest, n_lidar)
        cal = SC.load_calibration(dest)
        C = np.stack([cal[c]["t"] for c in SC.CAMERAS], 0).mean(0)
        SC.pair_residual.C = C
        sup = SC.camera_support(cal)
        series = []
        for fr in frames:
            lidar = SC.load_lidar_at(dest, fr["lidar_ts"])
            imgs = SC.load_images(dest, fr["cam_ts"])
            Zd, _ = SC.depth_field(lidar, C)
            r = SC.pair_residual(a, b, cal, imgs, sup, Zd)
            series.append({"anchor": fr["anchor_idx"], "residual": r.get("residual")})
        vals = [s["residual"] for s in series if s["residual"] is not None]
        return {"prefix": prefix, "uuid": uuid, "split": split, "pair": pair,
                "window": win, "step": step, "ok": bool(vals),
                "n_frames": len(vals), "series": series,
                "peak": round(max(vals), 4) if vals else None,
                "peak_anchor": (max(series, key=lambda s: s["residual"] or -1)["anchor"]
                                if vals else None),
                "median": round(float(np.median(vals)), 4) if vals else None,
                "total_s": round(time.time() - t0, 1)}
    except Exception as exc:
        return {"prefix": prefix, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "total_s": round(time.time() - t0, 1)}
    finally:
        if SC.cached_log_dir(uuid) is None:
            import shutil
            shutil.rmtree(os.path.join(WORK, uuid), ignore_errors=True)


def main(n_logs=8, step=1):
    """n_logs <= 0 means the whole population, highest single-frame score first."""
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "screening_ledger.json")) as fh:
        recs = json.load(fh)["records"]
    scored = [(k, v) for k, v in recs.items()
              if v.get("ok") and v.get("worst_residual") is not None]
    scored.sort(key=lambda kv: -kv[1]["worst_residual"])
    if n_logs <= 0:
        # full population; riskiest first so an interruption still leaves the
        # most decision-relevant half measured
        sel = scored
    else:
        pick = []
        n = len(scored)
        for frac in np.linspace(0, 1, n_logs):
            pick.append(scored[min(int(frac * (n - 1)), n - 1)])
        seen, sel = set(), []
        for k, v in pick:
            if k not in seen:
                seen.add(k); sel.append((k, v))
        if "00a6ffc1" in recs and "00a6ffc1" not in seen:
            sel.append(("00a6ffc1", recs["00a6ffc1"]))

    led = {"schema": "db238.dense.v1", "step": step, "records": {}}
    if os.path.isfile(LEDGER):
        try:
            led = json.load(open(LEDGER))
        except Exception:
            pass
    for i, (pref, v) in enumerate(sel, 1):
        if pref in led["records"] and led["records"][pref].get("ok"):
            print(f"[{i}/{len(sel)}] {pref} cached", flush=True)
            continue
        pair = v.get("worst_pair") or "ring_side_right|ring_front_right"
        r = dense_scan_log(pref, v["uuid"], v["split"], pair, step=step)
        led["records"][pref] = r
        with open(LEDGER, "w") as fh:
            json.dump(led, fh, indent=1)
        if r.get("ok"):
            print(f"[{i}/{len(sel)}] {pref} single={v['worst_residual']:.2f} "
                  f"peak={r['peak']:.2f}@{r['peak_anchor']} "
                  f"median={r['median']:.2f} n={r['n_frames']} ({r['total_s']}s)", flush=True)
        else:
            print(f"[{i}/{len(sel)}] {pref} FAILED {r.get('error')}", flush=True)
    print("DB238_DENSE_DONE", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8,
         int(sys.argv[2]) if len(sys.argv) > 2 else 1)
