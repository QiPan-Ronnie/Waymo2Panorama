"""Index the Waymo E2E val split by HTTP Range, then fetch only the records wanted.

The problem this solves: WOD-E2E ships its val split globally shuffled.  One
20-second segment's ~200 frames are scattered across all 93 shards, ~2 per shard,
so reconstructing a single 93-frame window naively means pulling 230 GB.

The shape of a tfrecord makes that unnecessary.  Records are contiguous and each
begins with its own length, so walking the file needs only the first few KB of
each record - enough to read the length and the `context.name` that identifies
the segment and the frame number.  That is ~4 KB per record instead of ~2 MB:

    index the entire val split   ~430 MB   (93 shards x ~1150 records x 4 KB)
    then fetch one 93-frame sample  ~190 MB  (only the records that belong to it)

versus 230 GB to download the split.  Two orders of magnitude, and the index is
reusable for every sample afterwards.

Context names look like `<hash>-<frame>`; the suffix is the frame number, so a
segment's window is chosen by sorting suffixes, not by trusting file order.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

BUCKET = "waymo_open_dataset_end_to_end_camera_v_1_0_0"
HOST = "https://storage.googleapis.com"
HEAD = 6144                      # enough for the length + context of any record
_local = threading.local()


TOKEN_FILE = os.environ.get("W2P_GCS_TOKEN_FILE", "")
_tok_cache = {"v": "", "mtime": 0.0}


def _tok():
    """Access token, re-read from a file so a long run survives expiry.

    A gcloud access token lasts ~1 h.  Indexing 93 shards takes longer than that,
    and capturing the token once meant 61 shards died on HTTP 401 after the first
    32 - the failure looked like a network problem and was really a clock.  Point
    W2P_GCS_TOKEN_FILE at a file some other process keeps fresh and this picks up
    the new value without restarting.
    """
    if TOKEN_FILE and os.path.isfile(TOKEN_FILE):
        try:
            m = os.path.getmtime(TOKEN_FILE)
            if m != _tok_cache["mtime"]:
                with open(TOKEN_FILE) as fh:
                    _tok_cache["v"] = fh.read().strip()
                _tok_cache["mtime"] = m
            if _tok_cache["v"]:
                return _tok_cache["v"]
        except Exception:
            pass
    t = os.environ.get("W2P_GCS_TOKEN")
    if not t:
        raise RuntimeError("set W2P_GCS_TOKEN or W2P_GCS_TOKEN_FILE")
    return t


def _wait_fresh_token(max_wait=1500):
    """Block until the token FILE changes, then let _tok() pick it up.

    A 401 means the token aged out, and burning the scene is the wrong
    response: the pump refreshes the file every ~10 min, so the right move is
    to pause the download until a new token lands. The fleet-wide outage of
    2026-08-19 turned a ~30-min token gap into 1022 failed_cpu scenes because
    every 401 was treated as fatal. 25 min of patience covers two missed pump
    cycles; only after that is the 401 allowed to propagate.
    """
    if not (TOKEN_FILE and os.path.isfile(TOKEN_FILE)):
        return
    try:
        m0 = os.path.getmtime(TOKEN_FILE)
    except OSError:
        return
    t0 = time.time()
    while time.time() - t0 < max_wait:
        time.sleep(20)
        try:
            if os.path.getmtime(TOKEN_FILE) != m0:
                return
        except OSError:
            pass


def _get(obj, a, b, retries=4):
    url = "%s/%s/%s" % (HOST, BUCKET, obj)
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + _tok(),
                "Range": "bytes=%d-%d" % (a, b)})
            with urllib.request.urlopen(req, timeout=90,
                                        context=ssl.create_default_context()) as r:
                return r.read(), int(r.headers.get("Content-Range", "/0").split("/")[-1])
        except urllib.error.HTTPError as e:
            # 401 = stale token, not a bad object: wait for a fresh token
            # WITHOUT spending the retry budget, then go around again.
            if e.code == 401:
                _wait_fresh_token()
                continue
            if k == retries - 1:
                raise
        except Exception:
            if k == retries - 1:
                raise
    # only reachable when every round ended in 401 + a fruitless wait
    raise RuntimeError("GCS 401 persisted after token-refresh waits: " + obj)


# ------------------------------------------------------------------ proto bits
def _varint(b, i):
    v = s = 0
    while True:
        x = b[i]; i += 1
        v |= (x & 0x7F) << s
        if not x & 0x80:
            return v, i
        s += 7


def _ctx_of(chunk):
    """E2EDFrame -> frame(1) -> context(1) -> name(1). Returns None if truncated."""
    try:
        key, i = _varint(chunk, 0)
        if key >> 3 != 1 or key & 7 != 2:
            return None
        ln, i = _varint(chunk, i)
        key, i = _varint(chunk, i)                    # frame.context
        if key >> 3 != 1 or key & 7 != 2:
            return None
        ln, i = _varint(chunk, i)
        key, i = _varint(chunk, i)                    # context.name
        if key >> 3 != 1 or key & 7 != 2:
            return None
        ln, i = _varint(chunk, i)
        return chunk[i:i + ln].decode("ascii", "replace")
    except (IndexError, UnicodeDecodeError):
        return None


def index_shard(obj):
    """-> [(offset, length, context), ...] for one shard."""
    out, off, total = [], 0, None
    while total is None or off < total:
        data, total = _get(obj, off, off + 12 + HEAD - 1)
        if len(data) < 12:
            break
        (ln,) = struct.unpack("<Q", data[:8])
        out.append((off, ln, _ctx_of(data[12:])))
        off += 12 + ln + 4
    return out


def build_index(shards, out_json, workers=24):
    """Index shards in parallel, resuming from and tolerating partial results.

    One shard failing must not take the run down with it.  `ex.map` re-raises in
    submission order, so a single transient GCS error after 24 good shards kills
    the whole pass and throws away 24 x 200 s of work; `submit` + per-future
    try/except keeps the rest.  Shards already in `out_json` are skipped, so a
    rerun costs only what is missing.
    """
    idx = {}
    if os.path.isfile(out_json):
        try:
            with open(out_json) as fh:
                idx = json.load(fh)
            print("  resuming: %d shards already indexed" % len(idx), flush=True)
        except Exception:
            idx = {}
    todo = [s for s in shards if s not in idx]
    done, failed = [len(idx)], []
    lock = threading.Lock()

    def one(obj):
        rows = index_shard(obj)
        with lock:
            idx[obj] = rows
            done[0] += 1
            print("  [%d/%d] %s  %d records" % (done[0], len(shards), obj[-22:],
                                                len(rows)), flush=True)
            tmp = out_json + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(idx, fh)
            os.replace(tmp, out_json)          # never leave a half-written index

    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, s): s for s in todo}
        for f in cf.as_completed(futs):
            try:
                f.result()
            except Exception as exc:           # noqa: BLE001
                failed.append(futs[f])
                print("  FAILED %s: %s" % (futs[f][-22:], str(exc)[:110]), flush=True)
    if failed:
        print("  %d shards failed; rerun to pick them up" % len(failed), flush=True)
    return idx


def list_shards(prefix="val"):
    url = ("%s/storage/v1/b/%s/o?prefix=%s&maxResults=400"
           % (HOST, BUCKET, prefix))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _tok()})
    with urllib.request.urlopen(req, timeout=90) as r:
        j = json.load(r)
    return sorted(i["name"] for i in j.get("items", []))


def segments(idx):
    """-> {segment_prefix: [(frame_no, shard, offset, length), ...] sorted}"""
    segs = {}
    for obj, rows in idx.items():
        for off, ln, ctx in rows:
            if not ctx or "-" not in ctx:
                continue
            pre, suf = ctx.rsplit("-", 1)
            if not suf.isdigit():
                continue
            segs.setdefault(pre, []).append((int(suf), obj, off, ln))
    for k in segs:
        segs[k].sort()
    return segs


def longest_run(frames, want=93):
    """Longest consecutive frame-number run; -> list of the first `want` entries."""
    best, cur = [], []
    for i, e in enumerate(frames):
        if cur and e[0] == cur[-1][0] + 1:
            cur.append(e)
        else:
            cur = [e]
        if len(cur) > len(best):
            best = list(cur)
    return best[:want] if len(best) >= want else None


def fetch_records(entries, dest, workers=12):
    """Download the chosen records into one concatenated tfrecord."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    parts = [None] * len(entries)

    def one(i):
        _, obj, off, ln = entries[i]
        data, _ = _get(obj, off, off + 12 + ln + 4 - 1)
        parts[i] = data
        return len(data)

    with cf.ThreadPoolExecutor(workers) as ex:
        total = sum(ex.map(one, range(len(entries))))
    with open(dest, "wb") as fh:
        for p in parts:
            if p:
                fh.write(p)
    return total


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "index":
        out = sys.argv[2]
        sh = list_shards("val")
        print("val shards: %d" % len(sh), flush=True)
        build_index(sh, out)
    elif cmd == "plan":
        idx = json.load(open(sys.argv[2]))
        segs = segments(idx)
        runs = {k: longest_run(v) for k, v in segs.items()}
        ok = {k: v for k, v in runs.items() if v}
        print("segments: %d   with a 93-frame consecutive run: %d" % (len(segs), len(ok)))
        for k in list(ok)[:5]:
            print("  %s  frames %d..%d" % (k[:16], ok[k][0][0], ok[k][-1][0]))
