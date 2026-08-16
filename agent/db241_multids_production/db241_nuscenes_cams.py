"""nuScenes -> pseudo-AV2, cameras only.

The db181_multids adapter is the verified path, but it requires the LiDAR sweeps
to be present and errors out without them.  For DB-241 that requirement is
purely a cost: the rule-mask / B-route pipeline never reads a point, and
nuScenes ships camera and LiDAR as separate blobs, so honouring it would mean
downloading 13.75 GB per shard of data we then ignore.

Everything the renderer actually needs is in the metadata plus the JPEGs:
per-sample_data timestamps, `calibrated_sensor` (intrinsics + camera-to-ego), and
`ego_pose` (ego-to-global, one row per sample_data, which is what makes EMC work
on nuScenes at all).

nuScenes images are already rectified, so no undistortion here - unlike Waymo.

Camera frames: nuScenes uses the OpenCV convention (+x right, +y down, +z
forward), the same as AV2, so extrinsics carry over unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import numpy as np

# nuScenes channel -> pseudo-AV2 ring name.  CAM_BACK becomes `ring_rear`, which
# is not an AV2 name; present_cameras() discovers cameras from disk precisely so
# that this does not have to lie about the rig.
RING = {
    "CAM_FRONT": "ring_front_center",
    "CAM_FRONT_LEFT": "ring_front_left",
    "CAM_FRONT_RIGHT": "ring_front_right",
    "CAM_BACK_LEFT": "ring_side_left",
    "CAM_BACK_RIGHT": "ring_side_right",
    "CAM_BACK": "ring_rear",
}


def _load(meta, name):
    with open(os.path.join(meta, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def convert_scene(src_root, meta, scene_token, out_dir, link=True):
    """-> report dict, or raises if the scene has no local images."""
    import pandas as pd

    scenes = {s["token"]: s for s in _load(meta, "scene")}
    sc = scenes[scene_token]
    sensors = {s["token"]: s for s in _load(meta, "sensor")}
    calibs = {c["token"]: c for c in _load(meta, "calibrated_sensor")}
    egos = {e["token"]: e for e in _load(meta, "ego_pose")}
    samples = {s["token"]: s for s in _load(meta, "sample")}

    want = set()
    tok = sc["first_sample_token"]
    while tok:
        want.add(tok)
        tok = samples[tok]["next"]

    rows = []
    for d in _load(meta, "sample_data"):
        if d["sample_token"] not in want or not d["fileformat"] == "jpg":
            continue
        ch = sensors[calibs[d["calibrated_sensor_token"]]["sensor_token"]]["channel"]
        if ch not in RING:
            continue
        p = os.path.join(src_root, d["filename"].replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        rows.append((ch, int(d["timestamp"]) * 1000, p,
                     d["calibrated_sensor_token"], d["ego_pose_token"]))
    if not rows:
        raise RuntimeError("no local camera files for scene %s" % sc["name"])

    cam_dir = os.path.join(out_dir, "sensors", "cameras")
    os.makedirs(os.path.join(out_dir, "calibration"), exist_ok=True)
    intr, extr, seen = [], [], set()
    n_img = 0
    for ch, ts_ns, path, cs_tok, _ in rows:
        ring = RING[ch]
        d = os.path.join(cam_dir, ring)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "%d.jpg" % ts_ns)
        if not os.path.isfile(dst):
            # hardlink where possible: a scene is ~1400 JPEGs and copying every
            # one would duplicate the whole shard on disk for no benefit
            try:
                if link:
                    os.link(path, dst)
                else:
                    shutil.copyfile(path, dst)
            except OSError:
                shutil.copyfile(path, dst)
        n_img += 1
        if ring in seen:
            continue
        seen.add(ring)
        c = calibs[cs_tok]
        K = np.array(c["camera_intrinsic"], float)
        q = c["rotation"]                       # w, x, y, z
        t = c["translation"]
        intr.append({"sensor_name": ring, "fx_px": K[0, 0], "fy_px": K[1, 1],
                     "cx_px": K[0, 2], "cy_px": K[1, 2],
                     "k1": 0.0, "k2": 0.0, "k3": 0.0,
                     "height_px": 900, "width_px": 1600})
        extr.append({"sensor_name": ring, "qw": q[0], "qx": q[1], "qy": q[2],
                     "qz": q[3], "tx_m": t[0], "ty_m": t[1], "tz_m": t[2]})

    pd.DataFrame(intr).to_feather(os.path.join(out_dir, "calibration",
                                               "intrinsics.feather"))
    pd.DataFrame(extr).to_feather(os.path.join(out_dir, "calibration",
                                               "egovehicle_SE3_sensor.feather"))
    pose_rows, done = [], set()
    for _, ts_ns, _, _, ep_tok in rows:
        if ep_tok in done:
            continue
        done.add(ep_tok)
        e = egos[ep_tok]
        q, t = e["rotation"], e["translation"]
        pose_rows.append({"timestamp_ns": int(e["timestamp"]) * 1000,
                          "qw": q[0], "qx": q[1], "qy": q[2], "qz": q[3],
                          "tx_m": t[0], "ty_m": t[1], "tz_m": t[2]})
    pd.DataFrame(sorted(pose_rows, key=lambda r: r["timestamp_ns"])).to_feather(
        os.path.join(out_dir, "city_SE3_egovehicle.feather"))

    per = {}
    for ch, _, _, _, _ in rows:
        per[RING[ch]] = per.get(RING[ch], 0) + 1
    return {"scene": sc["name"], "cameras": len(seen), "images": n_img,
            "frames_per_camera": min(per.values()), "ego_poses": len(pose_rows)}


if __name__ == "__main__":
    src, meta, tok, out = sys.argv[1:5]
    print(convert_scene(src, meta, tok, out))
