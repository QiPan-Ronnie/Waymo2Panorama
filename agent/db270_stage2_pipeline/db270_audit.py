"""Full-archive audit — the gate every sample must pass before delivery.

Run AFTER production finishes:

    python3 pipeline/db270_audit.py <archive_root> [--workers 16] [--resume]

Opens every tar and checks the delivery contract:
  accepted is True            - the producer's own verdict
  keep_px_not_written == 0    - no supervised pixel was dropped
  delivered_size == 1024x512  - what Xinhan confirmed
  93 frames AND 93 masks      - full clip, nothing truncated
  a decoded frame is 512x1024 - the manifest is not trusted on its own
  mask values subset of {0,255} - strictly binary, so a downscale that went
                                bilinear somewhere would be caught here

Two failure classes are reported separately because they need different
actions:
  FAIL   - the sample is unusable; drop it from delivery and re-produce
  CLONE  - Drive kept two copies of one scene (`<scene> (1).tgz`) because two
           boxes produced it; both are normally valid, so the action is dedup,
           not re-production. See the handoff note on why this happens.

Design notes:
  - Decoding one frame + one mask per tar, not all 186: full decode of 3500
    samples is hours of CPU for a check that catches the same faults.
  - --resume keeps a JSON ledger so an interrupted audit does not restart from
    zero, and so a re-run after fixes only looks at what changed.
  - Never deletes anything. It prints the clone list; removal stays a human
    decision.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor

SIZE = [1024, 512]
FRAMES = 93


def check_tar(path):
    """-> (verdict, detail). Never raises."""
    name = os.path.basename(path)
    try:
        tf = tarfile.open(path, "r:gz")
        names = tf.getnames()
        mf = [n for n in names if os.path.basename(n) == "manifest.json"]
        if not mf:
            return "FAIL", "no manifest.json"
        man = json.load(tf.extractfile(mf[0]))

        acc = man.get("accepted")
        unw = man.get("keep_px_not_written")
        ds = man.get("delivered_size")
        if acc is not True:
            return "FAIL", "accepted=%r" % (acc,)
        if unw not in (0, None):
            return "FAIL", "keep_px_not_written=%r" % (unw,)
        if ds != SIZE:
            return "FAIL", "delivered_size=%r" % (ds,)

        fr = sorted(n for n in names if "/frames/" in n and n.endswith(".png"))
        mk = sorted(n for n in names if "/masks/" in n and n.endswith(".png"))
        if len(fr) != FRAMES or len(mk) != FRAMES:
            return "FAIL", "frames=%d masks=%d" % (len(fr), len(mk))

        import numpy as np
        from PIL import Image
        mid = FRAMES // 2
        im = np.array(Image.open(io.BytesIO(tf.extractfile(fr[mid]).read())))
        mm = np.array(Image.open(io.BytesIO(tf.extractfile(mk[mid]).read())))
        if im.shape[:2] != (SIZE[1], SIZE[0]):
            return "FAIL", "frame decoded %r" % (im.shape,)
        if mm.shape[:2] != (SIZE[1], SIZE[0]):
            return "FAIL", "mask decoded %r" % (mm.shape,)
        vals = set(np.unique(mm).tolist())
        if not vals.issubset({0, 255}):
            return "FAIL", "mask not binary: %s" % sorted(vals)[:6]
        return "PASS", ""
    except Exception as exc:                                   # noqa: BLE001
        return "FAIL", "%s: %s" % (type(exc).__name__, str(exc)[:70])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive_root")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_audit_ledger.json")
    done = {}
    if a.resume and os.path.isfile(out):
        done = json.load(open(out))
        npass = sum(1 for v in done.values() if v[0] == "PASS")
        print("resuming: %d recorded (%d PASS kept, %d will be re-checked)"
              % (len(done), npass, len(done) - npass), flush=True)

    tars, clones = [], []
    for dp, _, fs in os.walk(a.archive_root):
        for f in fs:
            if not f.endswith(".tgz"):
                continue
            p = os.path.join(dp, f)
            (clones if ("(" in f and ")" in f) else tars).append(p)
    print("found %d tars (+%d Drive clones)" % (len(tars), len(clones)),
          flush=True)

    # Re-check anything that is not a recorded PASS. Skipping every recorded
    # entry would mean a repaired sample keeps its old FAIL forever - the audit
    # would report failures that no longer exist and hide whether the fix
    # worked. Only a PASS is worth trusting across runs.
    todo = [p for p in tars if done.get(p, [None])[0] != "PASS"]
    t0 = time.time()
    n = [0]

    def one(p):
        v, d = check_tar(p)
        done[p] = [v, d]
        n[0] += 1
        if n[0] % 100 == 0:
            el = time.time() - t0
            print("  %d/%d audited (%.0f/min)"
                  % (n[0], len(todo), 60 * n[0] / max(el, 1)), flush=True)
        return v

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, todo))

    with open(out, "w") as fh:
        json.dump(done, fh, indent=1)

    fails = {p: v for p, v in done.items() if v[0] != "PASS"}
    per_src = {}
    for p, v in done.items():
        src = p.replace("\\", "/").split("/")[-2]
        d = per_src.setdefault(src, [0, 0])
        d[0] += 1
        d[1] += (v[0] == "PASS")

    print("\n==== AUDIT RESULT ====")
    for src, (tot, ok) in sorted(per_src.items()):
        print("  %-18s %4d/%4d PASS" % (src, ok, tot))
    print("  TOTAL %d/%d PASS, %d FAIL" % (len(done) - len(fails), len(done),
                                           len(fails)))
    for p, (v, d) in list(fails.items())[:25]:
        print("   FAIL %s :: %s" % (os.path.basename(p)[:44], d))
    if clones:
        print("\n  %d Drive clones (dedup, NOT corruption - verify both then "
              "delete the parenthesised one):" % len(clones))
        for c in clones[:15]:
            print("    ", os.path.basename(c)[:60])
    print("\nledger: %s" % out)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
