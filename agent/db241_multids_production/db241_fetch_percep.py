"""Fetch N more Waymo Perception validation segments, convert, and produce samples.

Downloads to a scratch path, converts (undistorting), produces the sample, then
deletes the tfrecord - a segment is ~900 MB and only the 93 undistorted frames
are worth keeping, so holding them all would cost ~225 GB for 250 segments.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BUCKET = "waymo_open_dataset_v_1_4_3"
PREFIX = "individual_files/validation/"
AGENT = r"D:\BaiduSyncdisk\2024 to future\koi chen\w2p-db236\agent"
sys.path.insert(0, os.path.join(AGENT, "db241_multids_production"))
import db241_waymo_tfrecord as W  # noqa: E402
import db241_driver as D  # noqa: E402

RAW = r"E:\w2p_data\waymo_percep"
PSEUDO = os.path.join(RAW, "pseudo_av2")
OUT = r"E:\w2p_data\dataset_out"
WANT = int(sys.argv[1]) if len(sys.argv) > 1 else 6
TOK = os.environ["W2P_GCS_TOKEN"]


def api(url):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOK})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def list_segments():
    out, tok = [], None
    while True:
        u = ("https://storage.googleapis.com/storage/v1/b/%s/o?prefix=%s&maxResults=200"
             % (BUCKET, PREFIX))
        if tok:
            u += "&pageToken=" + tok
        j = api(u)
        out += [(i["name"], int(i["size"])) for i in j.get("items", [])
                if i["name"].endswith(".tfrecord")]
        tok = j.get("nextPageToken")
        if not tok:
            return out


def download(obj, dest):
    req = urllib.request.Request(
        "https://storage.googleapis.com/%s/%s" % (BUCKET, obj),
        headers={"Authorization": "Bearer " + TOK})
    with urllib.request.urlopen(req, timeout=3600) as r, open(dest, "wb") as fh:
        while True:
            b = r.read(1 << 22)
            if not b:
                break
            fh.write(b)
    return os.path.getsize(dest)


def main():
    segs = list_segments()
    print("validation segments available: %d" % len(segs), flush=True)
    done = 0
    for obj, size in segs:
        if done >= WANT:
            break
        ctx = obj.split("segment-")[-1].split("_")[0]
        sid = ctx[:20]
        if os.path.isdir(os.path.join(OUT, "waymo_perception", sid)):
            continue
        # per-process: a fixed scratch path races when two producers run
        # at once and the loser converts the winner's bytes (see db241_batch_e2e)
        tmp = os.path.join(RAW, "_tmp_%d.tfrecord" % os.getpid())
        t0 = time.time()
        try:
            mb = download(obj, tmp) / 1e6
            log = os.path.join(PSEUDO, "wp_" + sid)
            if not os.path.isdir(log):
                W.convert(tmp, log, e2e=False, max_frames=100)
            hood = "keep" if done % 2 == 0 else "black"
            m = D.build_sample(log, OUT, "waymo_perception", sid, 0, 93, hood)
            done += 1
            print("[%d/%d] %s  %.0f MB  %.0fs  %s"
                  % (done, WANT, sid, mb, time.time() - t0, D.summarise(m)), flush=True)
        except Exception as exc:                       # noqa: BLE001
            print("  skip %s: %s: %s" % (sid, type(exc).__name__, str(exc)[:140]),
                  flush=True)
        finally:
            if os.path.isfile(tmp):
                os.remove(tmp)


if __name__ == "__main__":
    main()
