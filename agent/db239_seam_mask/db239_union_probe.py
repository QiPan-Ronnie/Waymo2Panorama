"""DB-239 union-budget probe on one v15 log - the ruling's biggest open risk.

The B-93 judge shipped the hardened window-union at 16.19% of domain with the
ceiling amended to 17.5%, measured on ONE log (00a6ffc1, window speed
8.79 m/s = the population median). The union is an OR over the whole traverse,
so its budget must grow with speed; whether the fastest delivered log stays
under the ceiling decides whether the ruling survives. This probe fetches one
log's delivery window from public S3 and runs the exact shipping pipeline
(db239_b93_harden: hysteresis 16/11.2 + 2-of-5 persistence + keep-islands +
union) on it.

Population context (all 555 v15 logs, window-scoped median speed, measured
2026-08-10): min 2.35 / p10 4.67 / med 8.48 / p90 11.15 / max 15.08 m/s.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/content")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db238_screen as SC  # noqa: E402

S3 = "s3://argoverse/datasets/av2/sensor"


def fetch_window(uuid, split, w0, w1, dest):
    os.makedirs(dest, exist_ok=True)
    base = "%s/%s/%s" % (S3, split, uuid)
    cmds = []
    for rel in ("calibration/intrinsics.feather",
                "calibration/egovehicle_SE3_sensor.feather",
                "city_SE3_egovehicle.feather"):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)) or dest, exist_ok=True)
        cmds.append("cp %s/%s %s" % (base, rel, os.path.join(dest, rel)))
    ref = SC._ls_timestamps("%s/sensors/cameras/ring_front_center" % base, ".jpg")
    if not ref:
        raise RuntimeError("no front_center listing")
    for cam in SC.CAMERAS:
        ts = ref if cam == "ring_front_center" else SC._ls_timestamps(
            "%s/sensors/cameras/%s" % (base, cam), ".jpg")
        d = os.path.join(dest, "sensors", "cameras", cam)
        os.makedirs(d, exist_ok=True)
        for a in range(w0, w1 + 1):
            t = min(ts, key=lambda v: abs(v - ref[min(a, len(ref) - 1)]))
            cmds.append("cp %s/sensors/cameras/%s/%d.jpg %s" % (
                base, cam, t, os.path.join(d, "%d.jpg" % t)))
    # manifest_from_dir insists on lidar files even though the B-route never
    # reads a point; three sweeps near the window centre satisfy it
    lts = SC._ls_timestamps("%s/sensors/lidar" % base, ".feather")
    mid = ref[min((w0 + w1) // 2, len(ref) - 1)]
    d = os.path.join(dest, "sensors", "lidar")
    os.makedirs(d, exist_ok=True)
    for t in sorted(lts, key=lambda v: abs(v - mid))[:3]:
        cmds.append("cp %s/sensors/lidar/%d.feather %s" % (
            base, t, os.path.join(d, "%d.feather" % t)))
    run = os.path.join(dest, "_fetch.txt")
    with open(run, "w") as fh:
        fh.write("\n".join(sorted(set(cmds))) + "\n")
    r = subprocess.run(["s5cmd", "--no-sign-request", "run", run],
                       capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError("s5cmd failed: " + r.stderr[-400:])


def main(prefix, uuid, split, w0, w1, out_root, v_med=None):
    import db239_b93_harden as HB
    t0 = time.time()
    dest = "/content/probe_%s" % prefix
    out = os.path.join(out_root, "union_probe_%s" % prefix)
    fetch_window(uuid, split, int(w0), int(w1), dest)
    print("%s fetched (%.0fs)" % (prefix, time.time() - t0), flush=True)
    n = int(w1) - int(w0) + 1
    HB.main(dest, out, n)
    j = json.load(open(os.path.join(out, "b93_harden.json")))
    j["prefix"], j["uuid"], j["window"] = prefix, uuid, [int(w0), int(w1)]
    j["v_med_window"] = v_med
    j["total_s"] = round(time.time() - t0, 1)
    with open(os.path.join(out, "b93_harden.json"), "w") as fh:
        json.dump(j, fh, indent=1)
    print("UNION_PROBE_DONE " + json.dumps(
        {"prefix": prefix, "v_med": v_med,
         "union": j["gates"]["union_mask_frac_of_domain"],
         "masked_range": [j["gates"]["masked_frac_min"], j["gates"]["masked_frac_max"]],
         "total_s": j["total_s"]}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
         sys.argv[6], float(sys.argv[7]) if len(sys.argv) > 7 else None)
