"""DB-270 — what scenes each source can offer, and in what order to take them.

The catalogue is deliberately separate from the fetcher.  Three of the four
sources can be enumerated before any bytes move, which lets the split be decided
up front (see `plan_jobs`) and lets a resumed run land the same scene in the same
half.  nuScenes cannot, and the reason is worth stating rather than papering
over:

    argoverse2        S3 prefix listing -> 1000 log ids in about a second
    waymo_perception  GCS object listing -> 1150 segments in about a second
    waymo_e2e         needs `e2e_plan.json`, which costs ~1150 range reads PER
                      shard to build (db270_build_e2e_plan). Staged, not live.
    nuscenes          camera data ships only as ten ~16.5 GB tarballs and nothing
                      published says which scene is in which shard. So nuScenes
                      is planned SHARD-FIRST: unpack shard k, ask the metadata
                      which scenes just became complete on disk, take those.

Consequence for the orchestrator: for three sources `want` scenes means `want`
downloads; for nuScenes it means "keep opening shards until `want` scenes are
complete", ~85 scenes per shard.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "db241_multids_production"))

PERCEP_BUCKET = "waymo_open_dataset_v_1_4_3"
PERCEP_SPLITS = ("validation", "training", "testing")
CAM6 = ("CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT",
        "CAM_BACK", "CAM_BACK_LEFT", "CAM_BACK_RIGHT")


def _raw(root, *p):
    return os.path.join(root, "data", "raw", *p)


# ----------------------------------------------------------------- argoverse2
def argoverse2(root, refresh=False):
    p = _raw(root, "av2_index.json")
    if refresh or not os.path.isfile(p):
        import db241_fetch_av2 as FA
        ids = FA.list_logs(("train", "val", "test"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump([[s, u] for s, u in ids], fh)
    # Full native log id as the sample id, not an 8-char prefix. Truncation
    # is a traceability loss for free, and the split hash is taken over this
    # string - so it has to be the identifier the source dataset publishes.
    return [{"source": "argoverse2", "scene": uid, "native_scene_id": uid,
             "uid": uid, "part": part} for part, uid in json.load(open(p))]


# ------------------------------------------------------------ waymo perception
def waymo_perception(root, refresh=False):
    p = _raw(root, "percep_index.json")
    if refresh or not os.path.isfile(p):
        import db270_acquire as A
        rows = []
        for sp in PERCEP_SPLITS:
            tok = None
            while True:
                u = ("https://storage.googleapis.com/storage/v1/b/%s/o"
                     "?prefix=individual_files/%s/&maxResults=400"
                     % (PERCEP_BUCKET, sp))
                if tok:
                    u += "&pageToken=" + tok
                req = urllib.request.Request(
                    u, headers={"Authorization": "Bearer " + A._gcs_token()})
                with urllib.request.urlopen(req, timeout=120) as r:
                    j = json.load(r)
                rows += [[sp, i["name"]] for i in j.get("items", [])
                         if i["name"].endswith(".tfrecord")]
                tok = j.get("nextPageToken")
                if not tok:
                    break
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump(rows, fh)
    # The 20-digit context id IS Waymo's scene identifier; the rest of the
    # object name is the time range, kept in `obj` for the exact tfrecord.
    return [{"source": "waymo_perception",
             "scene": obj.split("segment-")[-1].split("_")[0],
             "native_scene_id": os.path.basename(obj)[:-len(".tfrecord")],
             "obj": obj, "part": sp}
            for sp, obj in json.load(open(p))]


# -------------------------------------------------------------------- waymo e2e
def waymo_e2e(root, refresh=False):
    p = _raw(root, "e2e_plan.json")
    if not os.path.isfile(p):
        raise RuntimeError(
            "e2e_plan.json missing - build it once with db270_build_e2e_plan.py; "
            "it is ~1150 range reads per shard and is not something to do inline")
    # The full segment key, not a 16-char slice: it is the `context.name`
    # prefix E2E identifies a segment by, and the plan is keyed on it.
    return [{"source": "waymo_e2e", "scene": k, "native_scene_id": k, "key": k}
            for k in sorted(json.load(open(p)))]


# --------------------------------------------------------------------- nuscenes
def nuscenes_local_scenes(root, min_frames=93, src=None, meta=None):
    """Scenes whose six cameras all have >= min_frames JPEGs on disk right now.

    Reads the metadata, not the directory tree: a scene's frames are interleaved
    with every other scene's in one flat `sweeps/CAM_*` folder, so "which scenes
    did that shard complete" is a metadata question, not a filesystem one.

    `src`/`meta` override the in-root locations so an existing nuScenes tree
    elsewhere on the box can be catalogued without re-downloading it.
    """
    import db270_acquire as A
    meta = meta or A.nuscenes_meta(root)
    src = src or _raw(root, "nuscenes")
    scenes = json.load(open(os.path.join(meta, "scene.json"), encoding="utf-8"))
    sensors = {s["token"]: s for s in json.load(
        open(os.path.join(meta, "sensor.json"), encoding="utf-8"))}
    calibs = {c["token"]: c for c in json.load(
        open(os.path.join(meta, "calibrated_sensor.json"), encoding="utf-8"))}
    samples = {}
    for s in json.load(open(os.path.join(meta, "sample.json"), encoding="utf-8")):
        samples[s["token"]] = s

    want = {}
    for sc in scenes:
        tok, ss = sc["first_sample_token"], set()
        while tok:
            ss.add(tok)
            tok = samples[tok]["next"]
        want[sc["token"]] = (sc["name"], ss)
    sample_to_scene = {}
    for tok, (_name, ss) in want.items():
        for s in ss:
            sample_to_scene[s] = tok

    have = {}
    for d in json.load(open(os.path.join(meta, "sample_data.json"),
                            encoding="utf-8")):
        if d["fileformat"] != "jpg":
            continue
        sc_tok = sample_to_scene.get(d["sample_token"])
        if sc_tok is None:
            continue
        ch = sensors[calibs[d["calibrated_sensor_token"]]["sensor_token"]]["channel"]
        if ch not in CAM6:
            continue
        if not os.path.isfile(os.path.join(src, d["filename"].replace("/", os.sep))):
            continue
        have.setdefault(sc_tok, {}).setdefault(ch, 0)
        have[sc_tok][ch] += 1

    out = []
    for sc_tok, per in have.items():
        if len(per) == len(CAM6) and min(per.values()) >= min_frames:
            out.append({"source": "nuscenes", "scene": want[sc_tok][0],
                        "native_scene_id": want[sc_tok][0],
                        "token": sc_tok, "frames": min(per.values())})
    out.sort(key=lambda j: j["scene"])
    return out


def nuscenes(root, want=0, max_shards=10, verbose=True, max_new=None,
             shard_i=0, shard_n=1):
    """Shard-first: open shards until `want` scenes are complete (0 = whatever
    is already unpacked, opening nothing new).

    `max_new` caps how many NEW shards this one call may unpack. The production
    cycle passes 1: it produces a shard's scenes and then deletes the raw JPEG
    tree, so unpacking a second shard in the same call would only queue 16.5 GB
    of files for that deletion - a whole shard's download thrown away.

    `shard_i/shard_n` PARTITION THE SHARDS ACROSS THE FLEET, and this is load
    bearing, not a tidiness knob. The `.shardNN.done` marker is per-box local
    state, so without a partition every box independently starts at shard 01:
    five boxes each pull the same 16.5 GB and then produce the same ~85 scenes,
    because the nuScenes jobs deliberately skip the `shard()` slice that the
    other three sources get. Archive dedup would hide the waste - one shard's
    worth of output for five shards' worth of download and GPU time. Giving box
    i the shards i, i+n, i+2n... costs the same bandwidth and yields n times the
    scenes. Boxes fall back to other shards only after exhausting their own, so
    a dead box's shards still get picked up; with 10 shards x ~85 scenes = ~850
    against a 700 quota, that fallback almost never runs.
    """
    import db270_acquire as A
    jobs = nuscenes_local_scenes(root)
    if not want:
        return jobs
    mine = [k for k in range(1, max_shards + 1) if (k - 1) % shard_n == shard_i]
    order = mine + [k for k in range(1, max_shards + 1) if k not in mine]
    opened = 0
    for k in order:
        if len(jobs) >= want or (max_new is not None and opened >= max_new):
            break
        mark = _raw(root, "nuscenes", ".shard%02d.done" % k)
        if os.path.isfile(mark):
            continue
        if verbose:
            print("nuscenes: %d/%d scenes, opening shard %02d (box owns %s)"
                  % (len(jobs), want, k, mine), flush=True)
        A.nuscenes_shard(k, root, verbose=verbose)
        opened += 1
        jobs = nuscenes_local_scenes(root)
    return jobs


CATALOGS = {"argoverse2": argoverse2, "waymo_perception": waymo_perception,
            "waymo_e2e": waymo_e2e, "nuscenes": nuscenes}


def catalog(source, root, **kw):
    return CATALOGS[source](root, **kw)


if __name__ == "__main__":
    root = sys.argv[1]
    for s in ("argoverse2", "waymo_perception", "waymo_e2e", "nuscenes"):
        try:
            n = len(catalog(s, root))
            print("%-18s %5d scenes" % (s, n))
        except Exception as exc:                          # noqa: BLE001
            print("%-18s  --   %s" % (s, str(exc)[:110]))
