"""DB-270 — one acquisition interface over four rigs that publish nothing alike.

    fetch(job, root) -> pseudo-AV2 log directory ready for the producer

`job` is whatever `db270_catalog` planned for that source; the only keys every
source shares are `source` and `scene`.  Everything else about how the bytes
arrive differs, and the differences are not incidental — they set the cost model
and the shape of the planner:

    argoverse2        per-FILE anonymous S3.  Ask for exactly the 7x93 JPEGs a
                      sample needs and nothing else: ~700 MB, no credentials.
    waymo_perception  per-SEGMENT GCS, authenticated.  A segment is one ~900 MB
                      tfrecord holding all five cameras; there is no smaller unit,
                      so we pull it, convert, and delete.
    waymo_e2e         per-RECORD GCS by HTTP Range.  The split is globally
                      shuffled — one segment's ~200 frames sit across all 93
                      shards — so a naive rebuild costs 230 GB.  A prebuilt index
                      (db241_e2e_index) says where each record lives, and a sample
                      then costs ~190 MB of targeted reads.
    nuscenes          per-SHARD tarball, anonymous S3, and this is the awkward one:
                      camera data ships ONLY as ten ~16.5 GB `.tgz` files. gzip is
                      not seekable, so there is no such thing as fetching one
                      scene.  We stream a shard through tar, keep just the camera
                      JPEGs, and then produce every scene that shard completed.

That last one is why nuScenes is planned shard-first while the other three are
planned scene-first — see `db270_catalog.nuscenes_plan`.

Everything here is resumable at file granularity: an interrupted fetch re-runs
and only pays for what is missing.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_UP = os.path.dirname(HERE)
# Two layouts must both work: the repo (`agent/db270_.../` beside
# `agent/db241_.../`) and the shipped run tree (`pipeline/` beside `code/`).
for _cand in (os.path.join(_UP, "db241_multids_production"),
              os.path.join(_UP, "code", "db241_multids_production")):
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.insert(0, _cand)

NUSC_S3 = "https://motional-nuscenes.s3.amazonaws.com/public/v1.0"
NUSC_SHARDS = ["v1.0-trainval%02d_blobs_camera.tgz" % k for k in range(1, 11)]
NUSC_META = "v1.0-trainval_meta.tgz"
WAYMO_PERCEP_BUCKET = "waymo_open_dataset_v_1_4_3"


# --------------------------------------------------------------------- helpers
def _stream(url, headers=None, timeout=3600):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def _authed_stream(url, timeout=3600):
    """GCS stream that treats 401 as "token aged out, wait for a fresh one".

    Burning a scene on 401 is the wrong response: the pump lands a new token
    file every ~10 min, so pause until the file's mtime changes (2 missed
    cycles of patience), re-read, retry. The 2026-08-19 outage turned a
    ~30-min token gap into 1022 failed_cpu scenes because every 401 was fatal.
    """
    for attempt in range(4):
        try:
            return _stream(url, {"Authorization": "Bearer " + _gcs_token()},
                           timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != 401 or attempt == 3:
                raise
        f = os.environ.get("W2P_GCS_TOKEN_FILE", "")
        if not (f and os.path.isfile(f)):
            continue
        try:
            m0 = os.path.getmtime(f)
        except OSError:
            continue
        t0 = time.time()
        while time.time() - t0 < 1500:
            time.sleep(20)
            try:
                if os.path.getmtime(f) != m0:
                    break
            except OSError:
                pass
    raise RuntimeError("GCS 401 persisted after token-refresh waits: " + url)


def _gcs_token():
    """A gcloud token lasts ~1 h and a shard pull can outlive it.

    W2P_GCS_TOKEN_FILE points at a file some other process keeps fresh; reading
    it per call is what stopped a mid-run expiry from presenting as HTTP 401
    flakiness and killing 61 shards in the DB-241 run.
    """
    f = os.environ.get("W2P_GCS_TOKEN_FILE", "")
    if f and os.path.isfile(f):
        with open(f) as fh:
            t = fh.read().strip()
        if t:
            return t
    t = os.environ.get("W2P_GCS_TOKEN", "")
    if t:
        return t
    out = subprocess.run(["gcloud", "auth", "print-access-token"],
                         capture_output=True, text=True, timeout=120)
    t = out.stdout.strip()
    if not t:
        raise RuntimeError("no GCS token: set W2P_GCS_TOKEN or run gcloud auth login")
    return t


# ----------------------------------------------------------------- argoverse2
def fetch_argoverse2(job, root):
    """Per-file S3. The only source where we can ask for exactly what we need.

    ALWAYS call fetch_log - it is per-file idempotent (existing non-empty files
    are skipped), so a complete log costs one cheap S3 listing per camera. The
    old guard skipped the whole fetch whenever calibration existed, which made
    a half-downloaded log (calibration present, cameras incomplete) a PERMANENT
    poisoned cache: 16 scenes burned on one box with "only 1 ring cameras
    present" before this was caught, and retries could never heal them.
    """
    import db241_fetch_av2 as FA
    dest = os.path.join(root, "data", "pseudo_av2")
    log = os.path.join(dest, job["uid"])
    FA.fetch_log(job["uid"], dest, split=job["part"],
                 n_frames=job.get("frames", 125))
    return log


# ------------------------------------------------------------ waymo perception
def _convert_guard(log):
    """Skip-or-rebuild decision for a converted scene directory.

    "calibration/ exists" was the old completeness test, and it is exactly how
    half-converted directories become PERMANENT poisoned caches: a convert cut
    down mid-write (SIGKILL during the fleet restart, OOM) leaves calibration
    in place with camera files missing, every retry trusts it, and the scene
    fails forever in the GPU stage (pandas/PIL FileNotFoundError - seen on 3
    boxes after the 401 outage). Completeness is now an explicit _CONVERT_OK
    sentinel written AFTER convert returns; anything without it is torn down
    and rebuilt from the source of truth.
    """
    ok = os.path.join(log, "_CONVERT_OK")
    if os.path.isfile(ok):
        return True
    if os.path.isdir(log):
        import shutil
        shutil.rmtree(log, ignore_errors=True)
    return False


def fetch_waymo_perception(job, root):
    """Whole ~900 MB tfrecord, converted then deleted — there is no smaller unit."""
    import db241_waymo_tfrecord as W
    log = os.path.join(root, "data", "pseudo_av2", "wp_" + job["scene"])
    if _convert_guard(log):
        return log
    tmp = os.path.join(root, "data", "raw", "_wp_%s_%d.tfrecord"
                       % (job["scene"][:10], os.getpid()))
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    url = "https://storage.googleapis.com/%s/%s" % (WAYMO_PERCEP_BUCKET, job["obj"])
    try:
        with _authed_stream(url) as r, \
                open(tmp, "wb") as fh:
            while True:
                b = r.read(1 << 22)
                if not b:
                    break
                fh.write(b)
        W.convert(tmp, log, e2e=False, max_frames=100)
        with open(os.path.join(log, "_CONVERT_OK"), "w") as fh:
            fh.write("1")
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)
    return log


# -------------------------------------------------------------------- waymo e2e
def fetch_waymo_e2e(job, root):
    """Targeted range reads driven by the prebuilt record index."""
    import db241_e2e_index as E
    import db241_waymo_tfrecord as W
    log = os.path.join(root, "data", "pseudo_av2", "e2_" + job["scene"])
    if _convert_guard(log):
        return log
    plan = json.load(open(os.path.join(root, "data", "raw", "e2e_plan.json")))
    tmp = os.path.join(root, "data", "raw", "_e2e_%s_%d.tfrecord"
                       % (job["scene"][:10], os.getpid()))
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    try:
        E.fetch_records([tuple(e) for e in plan[job["key"]]], tmp)
        W.convert(tmp, log, e2e=True, max_frames=93)
        with open(os.path.join(log, "_CONVERT_OK"), "w") as fh:
            fh.write("1")
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)
    return log


# --------------------------------------------------------------------- nuscenes
def nuscenes_meta(root, verbose=True):
    """The 462 MB metadata tarball — scene/sample/calibration tables for all 850."""
    meta = os.path.join(root, "data", "raw", "nuscenes", "v1.0-trainval")
    if os.path.isfile(os.path.join(meta, "scene.json")):
        return meta
    dst = os.path.dirname(meta)
    os.makedirs(dst, exist_ok=True)
    if verbose:
        print("  nuscenes: pulling %s (462 MB)" % NUSC_META, flush=True)
    with _stream("%s/%s" % (NUSC_S3, NUSC_META)) as r:
        with tarfile.open(fileobj=_Reader(r), mode="r|gz") as tf:
            tf.extractall(dst)
    return meta


class _Reader(io.RawIOBase):
    """urlopen's object is not a seekable file; tar's stream mode only needs read."""

    def __init__(self, resp):
        self.r = resp
        self.n = 0

    def readable(self):
        return True

    def read(self, size=-1):
        b = self.r.read(size if size and size > 0 else 1 << 20)
        self.n += len(b)
        return b


def nuscenes_unpack(name, base, verbose=True, progress_gb=2, limit_jpg=0):
    """Stream one nuScenes tarball, keeping only camera JPEGs.

    gzip is not seekable, so this reads the whole archive either way; what it
    avoids is *storing* it. Extracting on the fly writes only the camera share
    and never lands the ~16.5 GB archive on disk, which is the difference
    between needing that much scratch per shard and needing none.

    `limit_jpg` stops early — for smoke-testing the route without paying for the
    whole shard. A run that stops early does NOT get a done-marker.
    """
    os.makedirs(base, exist_ok=True)
    if verbose:
        print("  nuscenes: streaming %s (camera only)" % name, flush=True)
    kept, rd, nxt = 0, None, progress_gb << 30
    with _stream("%s/%s" % (NUSC_S3, name)) as r:
        rd = _Reader(r)
        with tarfile.open(fileobj=rd, mode="r|gz") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                p = m.name.replace("\\", "/")
                # keep both: `samples` is the 2 Hz keyframes, `sweeps` the 12 Hz
                # stream the 93-frame window actually rides on
                if "/CAM" not in p or not p.endswith(".jpg"):
                    continue
                tf.extract(m, base)
                kept += 1
                if limit_jpg and kept >= limit_jpg:
                    break
                if verbose and rd.n >= nxt:
                    print("    %.1f GB read, %d jpg kept" % (rd.n / 2**30, kept),
                          flush=True)
                    nxt += progress_gb << 30
    return {"archive": name, "jpg": kept, "bytes_read": rd.n if rd else 0,
            "partial": bool(limit_jpg)}


def nuscenes_shard(k, root, verbose=True, progress_gb=2):
    """Unpack camera shard k (1-10), once. Idempotent by marker file.

    A shard interrupted halfway is re-streamed rather than resumed: there is no
    safe way to resume a gzip stream mid-member, and the extracted files that did
    land are simply re-written.
    """
    base = os.path.join(root, "data", "raw", "nuscenes")
    mark = os.path.join(base, ".shard%02d.done" % k)
    if os.path.isfile(mark):
        return base
    rep = nuscenes_unpack(NUSC_SHARDS[k - 1], base, verbose, progress_gb)
    with open(mark, "w") as fh:
        json.dump(rep, fh)
    if verbose:
        print("  nuscenes: shard %02d done, %d jpg" % (k, rep["jpg"]), flush=True)
    return base


def fetch_nuscenes(job, root):
    """Convert one scene out of an already-unpacked shard."""
    import db241_nuscenes_cams as NC
    src = os.path.join(root, "data", "raw", "nuscenes")
    meta = nuscenes_meta(root)
    log = os.path.join(root, "data", "pseudo_av2", "ns_" + job["scene"])
    if os.path.isdir(os.path.join(log, "calibration")):
        return log
    NC.convert_scene(src, meta, job["token"], log, link=True)
    return log


# ------------------------------------------------------------------ the switch
FETCHERS = {
    "argoverse2": fetch_argoverse2,
    "waymo_perception": fetch_waymo_perception,
    "waymo_e2e": fetch_waymo_e2e,
    "nuscenes": fetch_nuscenes,
}


def fetch(job, root):
    return FETCHERS[job["source"]](job, root)


# What each route costs and what it needs. Measured 2026-08-19 on a ~7.6 MB/s
# link, fetch->convert->produce->ACCEPTED; see deliverables/db270_stage2_dataset/
# ACQUISITION.md. Kept here so the planner and the RUNBOOK agree with the code
# rather than with a doc someone edited separately.
ROUTES = {
    "argoverse2": {
        "unit": "file", "auth": None, "host": "s3.amazonaws.com/argoverse",
        "scenes": 1000, "measured": "131 s / 374 MB for 7 cams x 125 frames"},
    "waymo_perception": {
        "unit": "segment", "auth": "gcloud", "host": "storage.googleapis.com",
        "scenes": 1150, "measured": "56 s, 270 MB kept from a ~900 MB tfrecord"},
    "waymo_e2e": {
        "unit": "record", "auth": "gcloud", "host": "storage.googleapis.com",
        "scenes": "478 val + ~1900 training + ~870 test",
        "measured": "~190 MB of targeted range reads per sample; the index is "
                    "all-or-nothing per split (~2.1 frames per segment per shard)"},
    "nuscenes": {
        "unit": "shard", "auth": None,
        "host": "motional-nuscenes.s3.amazonaws.com", "scenes": 850,
        "measured": "16.4 GB -> 85 complete scenes, 115,961 jpg (shard 01, "
                    "measured); camera-only tarballs, gzip so no "
                    "per-scene access exists"},
}


if __name__ == "__main__":
    print(json.dumps(ROUTES, indent=1))
