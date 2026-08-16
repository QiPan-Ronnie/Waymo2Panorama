"""Waymo tfrecord -> pseudo-AV2 log, for both Perception (v1.4.x) and E2E (v1.0).

Why not the db181_multids adapter: that one reads the v2.0.1 parquet components.
This reads the tfrecord directly, which is the distribution we already have and
the only one E2E ships in.  It is also where undistortion has to live - see below.

Undistortion is mandatory, not cosmetic.  Waymo camera intrinsics carry real
radial distortion (Perception k1 = 0.347), and the renderer's `_project` is a
pinhole model.  At the image edge, r/f = 960/2055 = 0.467, so the radial term
displaces a pixel by k1*(r/f)^2 = 7.4%, about 71 px - wider than an entire seam
strip.  Feeding distorted JPEGs to a pinhole projector would put the seams in the
wrong place and the mask would then cover the wrong pixels.  So images are
rectified here and pinhole intrinsics are written alongside them.

Coordinate frames: Waymo's camera frame is +x forward (optical), +y left, +z up.
The renderer expects the OpenCV convention (+x right, +y down, +z forward), which
is what AV2 stores.  The fixed change-of-basis is applied once, on write.
"""
from __future__ import annotations

import io
import math
import os
import struct
import sys

import numpy as np

CAMNAME = {1: "FRONT", 2: "FRONT_LEFT", 3: "FRONT_RIGHT", 4: "SIDE_LEFT",
           5: "SIDE_RIGHT", 6: "REAR_LEFT", 7: "REAR", 8: "REAR_RIGHT"}

# Waymo camera id -> pseudo-AV2 ring name.  The five-camera Perception rig has no
# rear, so its ring legitimately does not close; adjacent_pairs() detects that
# from the support maps rather than from these names.
RING = {1: "ring_front_center", 2: "ring_front_left", 3: "ring_front_right",
        4: "ring_side_left", 5: "ring_side_right",
        6: "ring_rear_left", 7: "ring_rear", 8: "ring_rear_right"}

# OpenCV camera axes expressed in Waymo camera axes:
#   opencv x(right) = waymo -y ; opencv y(down) = waymo -z ; opencv z(fwd) = waymo +x
CV_IN_WAYMO = np.array([[0.0, 0.0, 1.0],
                        [-1.0, 0.0, 0.0],
                        [0.0, -1.0, 0.0]])


# ---------------------------------------------------------------- proto reader
def _varint(b, i):
    v = s = 0
    while True:
        x = b[i]; i += 1
        v |= (x & 0x7F) << s
        if not x & 0x80:
            return v, i
        s += 7


def _fields(b, start, end):
    i = start
    while i < end:
        try:
            key, i = _varint(b, i)
        except IndexError:
            return
        f, w = key >> 3, key & 7
        if w == 0:
            try:
                v, i = _varint(b, i)
            except IndexError:
                return
            yield f, w, v
        elif w == 1:
            if i + 8 > end:
                return
            yield f, w, struct.unpack("<d", b[i:i + 8])[0]; i += 8
        elif w == 2:
            try:
                ln, j = _varint(b, i)
            except IndexError:
                return
            if j + ln > end:
                return
            yield f, w, (j, j + ln); i = j + ln
        elif w == 5:
            if i + 4 > end:
                return
            yield f, w, struct.unpack("<f", b[i:i + 4])[0]; i += 4
        else:
            return


def _doubles(b, a, z):
    """repeated double, packed or not."""
    out = []
    for f, w, v in _fields(b, a, z):
        if f == 1 and w == 1:
            out.append(v)
        elif f == 1 and w == 2:
            va, vb = v
            out += [struct.unpack("<d", b[va + 8 * k:va + 8 * k + 8])[0]
                    for k in range((vb - va) // 8)]
    return out


def iter_records(path):
    """Yield raw record payloads from a tfrecord, streaming."""
    with open(path, "rb") as fh:
        while True:
            hdr = fh.read(12)
            if len(hdr) < 12:
                return
            (ln,) = struct.unpack("<Q", hdr[:8])
            payload = fh.read(ln)
            fh.read(4)
            if len(payload) < ln:
                return
            yield payload


def parse_frame(d, e2e):
    """-> dict(context, ts, cams={id: jpeg}, calib={id: {...}}, pose=4x4|None)"""
    span = (0, len(d))
    if e2e:
        span = None
        for f, w, v in _fields(d, 0, len(d)):
            if f == 1 and w == 2:
                span = v
                break
        if span is None:
            return None
    fa, fb = span
    out = {"context": None, "ts": None, "cams": {}, "calib": {}, "pose": None}
    for f2, w2, v2 in _fields(d, fa, fb):
        if f2 == 1 and w2 == 2:                       # Context
            ca, cb = v2
            for f3, w3, v3 in _fields(d, ca, cb):
                if f3 == 1 and w3 == 2:
                    na, nb = v3
                    out["context"] = d[na:nb].decode("ascii", "replace")
                elif f3 == 2 and w3 == 2:             # camera_calibrations
                    xa, xb = v3
                    cid, intr, extr, wh = None, [], None, [None, None]
                    for f4, w4, v4 in _fields(d, xa, xb):
                        if f4 == 1 and w4 == 0:
                            cid = v4
                        elif f4 == 2 and w4 == 1:
                            intr.append(v4)
                        elif f4 == 2 and w4 == 2:
                            ia, ib = v4
                            intr += [struct.unpack("<d", d[ia + 8 * k:ia + 8 * k + 8])[0]
                                     for k in range((ib - ia) // 8)]
                        elif f4 == 3 and w4 == 2:
                            ta, tb = v4
                            extr = _doubles(d, ta, tb)
                        elif f4 == 4 and w4 == 0:
                            wh[0] = v4
                        elif f4 == 5 and w4 == 0:
                            wh[1] = v4
                    if cid:
                        out["calib"][cid] = {"intr": intr, "extr": extr, "wh": tuple(wh)}
        elif f2 == 2 and w2 == 0:
            out["ts"] = v2
        elif f2 == 3 and w2 == 2:
            ta, tb = v2
            t = _doubles(d, ta, tb)
            if len(t) == 16:
                out["pose"] = np.array(t).reshape(4, 4)
        elif f2 == 4 and w2 == 2:                     # CameraImage
            ia, ib = v2
            cid, jpg, trig = None, None, None
            for f4, w4, v4 in _fields(d, ia, ib):
                if f4 == 1 and w4 == 0:
                    cid = v4
                elif f4 == 2 and w4 == 2:
                    ja, jb = v4
                    jpg = d[ja:jb]
                elif f4 == 7 and w4 == 1:
                    trig = v4
            if cid and jpg:
                out["cams"][cid] = (jpg, trig)
    return out


# ------------------------------------------------------------------ conversion
def _undistort_maps(K, dist, w, h):
    import cv2
    newK, _ = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), 0.0, (w, h))
    m1, m2 = cv2.initUndistortRectifyMap(K, dist, None, newK, (w, h), cv2.CV_16SC2)
    return newK, m1, m2


def convert(tfrecord, out_dir, e2e=False, max_frames=None, context_filter=None,
            undistort=True):
    """Write one pseudo-AV2 log.  Returns a small report dict."""
    import cv2
    import pandas as pd
    from scipy.spatial.transform import Rotation

    cam_dir = os.path.join(out_dir, "sensors", "cameras")
    os.makedirs(os.path.join(out_dir, "calibration"), exist_ok=True)

    intr_rows, extr_rows, pose_rows = [], [], []
    maps, wrote, ctx_seen = {}, 0, None
    for payload in iter_records(tfrecord):
        fr = parse_frame(payload, e2e)
        if fr is None or not fr["cams"]:
            continue
        if context_filter and fr["context"] != context_filter:
            continue
        if ctx_seen is None:
            ctx_seen = fr["context"]
            for cid, c in sorted(fr["calib"].items()):
                k = c["intr"]
                w, h = c["wh"]
                K = np.array([[k[0], 0, k[2]], [0, k[1], k[3]], [0, 0, 1]], float)
                dist = np.array(k[4:9], float) if len(k) >= 9 else np.zeros(5)
                if undistort and np.abs(dist).max() > 1e-6:
                    newK, m1, m2 = _undistort_maps(K, dist, w, h)
                    maps[cid] = (m1, m2)
                else:
                    newK = K
                name = RING[cid]
                intr_rows.append({"sensor_name": name,
                                  "fx_px": newK[0, 0], "fy_px": newK[1, 1],
                                  "cx_px": newK[0, 2], "cy_px": newK[1, 2],
                                  "k1": 0.0, "k2": 0.0, "k3": 0.0,
                                  "height_px": int(h), "width_px": int(w)})
                E = np.array(c["extr"]).reshape(4, 4)          # waymo cam -> vehicle
                R = E[:3, :3] @ CV_IN_WAYMO                    # -> opencv cam
                q = Rotation.from_matrix(R).as_quat()          # x,y,z,w
                extr_rows.append({"sensor_name": name,
                                  "qw": q[3], "qx": q[0], "qy": q[1], "qz": q[2],
                                  "tx_m": E[0, 3], "ty_m": E[1, 3], "tz_m": E[2, 3]})
        elif fr["context"] != ctx_seen:
            continue

        # per-camera timestamp: trigger time if real, else the frame stamp
        base_ns = int(fr["ts"]) * 1000 if fr["ts"] else wrote * 100_000_000
        for cid, (jpg, trig) in sorted(fr["cams"].items()):
            ts_ns = int(trig * 1e9) if (trig and trig > 1.0) else base_ns
            d = os.path.join(cam_dir, RING[cid])
            os.makedirs(d, exist_ok=True)
            if cid in maps:
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                img = cv2.remap(img, maps[cid][0], maps[cid][1], cv2.INTER_LINEAR)
                cv2.imwrite(os.path.join(d, "%d.jpg" % ts_ns), img,
                            [cv2.IMWRITE_JPEG_QUALITY, 96])
            else:
                with open(os.path.join(d, "%d.jpg" % ts_ns), "wb") as fh:
                    fh.write(jpg)
        if fr["pose"] is not None:
            P = fr["pose"]
            q = Rotation.from_matrix(P[:3, :3]).as_quat()
            pose_rows.append({"timestamp_ns": base_ns,
                              "qw": q[3], "qx": q[0], "qy": q[1], "qz": q[2],
                              "tx_m": P[0, 3], "ty_m": P[1, 3], "tz_m": P[2, 3]})
        wrote += 1
        if max_frames and wrote >= max_frames:
            break

    pd.DataFrame(intr_rows).to_feather(
        os.path.join(out_dir, "calibration", "intrinsics.feather"))
    pd.DataFrame(extr_rows).to_feather(
        os.path.join(out_dir, "calibration", "egovehicle_SE3_sensor.feather"))
    if pose_rows:
        pd.DataFrame(pose_rows).to_feather(
            os.path.join(out_dir, "city_SE3_egovehicle.feather"))
    return {"context": ctx_seen, "frames": wrote, "cameras": len(intr_rows),
            "undistorted": sorted(RING[c] for c in maps)}


if __name__ == "__main__":
    tf, out = sys.argv[1:3]
    e2e = "--e2e" in sys.argv
    n = int(sys.argv[sys.argv.index("-n") + 1]) if "-n" in sys.argv else None
    print(convert(tf, out, e2e=e2e, max_frames=n))
