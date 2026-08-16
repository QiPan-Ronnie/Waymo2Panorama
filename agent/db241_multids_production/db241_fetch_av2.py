"""Fetch the first N frames of AV2 val logs from the public S3 bucket.

Only what a 93-frame sample needs: the seven ring cameras' first 93 JPEGs, the
calibration, and the ego-pose table.  No LiDAR - the rule-mask path never reads a
sweep, and skipping it is most of the download.

Anonymous HTTPS, no credentials, which is why this works from the USC network
where the tunnel does not.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BUCKET = "https://s3.amazonaws.com/argoverse"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
CAMS = ["ring_front_center", "ring_front_left", "ring_front_right",
        "ring_side_left", "ring_side_right", "ring_rear_left", "ring_rear_right"]


def _list(prefix, delimiter=True, limit=None):
    keys, pres, tok = [], [], None
    while True:
        u = "%s/?list-type=2&max-keys=1000&prefix=%s" % (BUCKET, urllib.parse.quote(prefix))
        if delimiter:
            u += "&delimiter=/"
        if tok:
            u += "&continuation-token=" + urllib.parse.quote(tok)
        x = ET.fromstring(urllib.request.urlopen(u, timeout=90).read())
        pres += [p.find(NS + "Prefix").text for p in x.findall(NS + "CommonPrefixes")]
        keys += [c.find(NS + "Key").text for c in x.findall(NS + "Contents")]
        t = x.find(NS + "NextContinuationToken")
        if t is None or (limit and len(keys) + len(pres) >= limit):
            break
        tok = t.text
    return pres, keys


def _get(key, dest):
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return 0
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen("%s/%s" % (BUCKET, urllib.parse.quote(key)),
                                timeout=120) as r:
        data = r.read()
    with open(dest, "wb") as fh:
        fh.write(data)
    return len(data)


def fetch_log(uuid, dest_root, split="val", n_frames=93, workers=12):
    """-> bytes downloaded, or raises if the log lacks a full 7-camera window."""
    base = "datasets/av2/sensor/%s/%s/" % (split, uuid)
    out = os.path.join(dest_root, uuid)
    jobs = []
    for f in ("calibration/intrinsics.feather",
              "calibration/egovehicle_SE3_sensor.feather",
              "city_SE3_egovehicle.feather"):
        jobs.append((base + f, os.path.join(out, f)))
    for cam in CAMS:
        _, keys = _list(base + "sensors/cameras/%s/" % cam, delimiter=False)
        keys = sorted(k for k in keys if k.endswith(".jpg"))[:n_frames]
        if len(keys) < n_frames:
            raise RuntimeError("%s has only %d frames on %s" % (uuid, len(keys), cam))
        for k in keys:
            jobs.append((k, os.path.join(out, "sensors", "cameras", cam,
                                         os.path.basename(k))))
    total = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for got in ex.map(lambda j: _get(*j), jobs):
            total += got
    return total


def list_logs(splits=("val", "train"), limit=None):
    """Log ids across splits, val first.

    val alone is 150 logs, which the producer exhausts well before the 400/source
    target - after that the fetcher spins requesting offsets past the end of the
    list and looks busy while doing nothing. train adds 700 more.
    """
    ids = []
    for sp in splits:
        pres, _ = _list("datasets/av2/sensor/%s/" % sp)
        ids += [(sp, p.rstrip("/").rsplit("/", 1)[-1]) for p in pres]
    return ids[:limit] if limit else ids


def list_val_logs(limit=None):
    return [u for _s, u in list_logs(("val",), limit)]


if __name__ == "__main__":
    dest = sys.argv[1]
    want = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    ids = list_logs()
    have = set(os.listdir(dest)) if os.path.isdir(dest) else set()
    # Skip by what is already on disk rather than by a caller-supplied offset:
    # an offset walks off the end once a split is exhausted and the fetcher then
    # spins doing nothing while looking busy.
    todo = [(sp, u) for sp, u in ids if u not in have]
    print("logs available: %d (%d already local, %d to go)"
          % (len(ids), len(have), len(todo)), flush=True)
    done = 0
    for sp, uuid in todo:
        if done >= want:
            break
        try:
            mb = fetch_log(uuid, dest, split=sp) / 1e6
            done += 1
            print("  [%d/%d] %s/%s  %.0f MB" % (done, want, sp, uuid, mb), flush=True)
        except Exception as exc:                       # noqa: BLE001
            print("  skip %s: %s" % (uuid, str(exc)[:90]), flush=True)
