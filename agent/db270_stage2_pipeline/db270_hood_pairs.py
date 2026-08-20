"""Build hood A/B PAIRS for argoverse2 without re-rendering anything.

Why this is cheap: db267's apply_sample is pure post-processing. It reads a
shipped sample's frames/masks, blackens the fixed rig hood region, and rewrites
the manifest - no renderer, no LiDAR, no GPU. So a 'keep' sample can be turned
into its 'black' twin for the cost of decoding and re-encoding 93 PNGs.

The reverse is NOT symmetric: a 'black' sample has already had those pixels
destroyed, and the npz backup lives on the producing VM's local disk, which
does not survive a VM swap. Turning black -> keep therefore means re-rendering
the scene, which is ~100x the cost. This tool only does the cheap direction.

Output goes to a SEPARATE archive root:

    <archive>/            <- untouched: the 50/50 mixed delivery, 3500 samples
    <archive>_hood_pairs/ <- added: the twin of every 'keep' argoverse2 sample

so the existing delivery is never mutated and the pairing is opt-in at
packaging time. Idempotent: an existing twin tar is skipped, and --shard lets
the fleet split the work the same way production does.

    python3 pipeline/db270_hood_pairs.py --shard 0/5 [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "pipeline" else HERE
for _c in (os.path.join(ROOT, "code", "db267_hood_apply"),
           os.path.join(ROOT, "pipeline")):
    if os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)


def fit_hood(hood, h, w):
    """Bring the rig hood mask to the DELIVERED resolution.

    rig_hood_mask() is authored at production resolution (1024x2048) because
    that is where the pipeline applies it - before db270_downscale halves it.
    Archived samples are the delivered 512x1024, so the raw mask indexes past
    the end of the image (IndexError at row 597 of 512).

    Downsampled with block-MAX, not block-min or nearest: a hood pixel that
    survives into the delivered frame shows ego bodywork where the contract
    promises scene, so over-covering the boundary by half a pixel is the safe
    error and under-covering is not. This mirrors the pipeline's own
    宁过勿漏 rule for ego-body removal (DB-123).
    """
    import numpy as np
    a = np.asarray(hood) > 0
    if a.shape == (h, w):
        return a.astype(np.uint8)
    fh, fw = a.shape[0] // h, a.shape[1] // w
    if fh >= 1 and fw >= 1 and a.shape == (h * fh, w * fw):
        m = a.reshape(h, fh, w, fw).max(axis=(1, 3))
    else:
        from PIL import Image
        m = np.array(Image.fromarray(a.astype(np.uint8) * 255)
                     .resize((w, h), Image.BILINEAR)) > 0
    # One pixel of dilation. Measured against the pipeline's own shipped
    # hood_mask.png, plain block-max still MISSED 10 px it had blacked - the
    # halved grid cannot land exactly on the production boundary. A leftover
    # hood pixel puts ego bodywork where the contract promises scene, so the
    # boundary error must fall on the over-covering side (DB-123 宁过勿漏).
    d = m.copy()
    d[1:] |= m[:-1]
    d[:-1] |= m[1:]
    d[:, 1:] |= m[:, :-1]
    d[:, :-1] |= m[:, 1:]
    return d.astype(np.uint8)


def twin_root(archive):
    return archive.rstrip("/\\") + "_hood_pairs"


def list_keep(archive, shard=""):
    """Every 'keep' argoverse2 sample, deterministically sharded.

    The variant is decided by plan_jobs._hood_of - a sha1 of the scene id - so
    it can be computed without opening a single tar. Listing all 897 and asking
    each manifest would mean 897 gzip opens over Drive FUSE just to discard
    half of them. make_twin still re-checks the manifest, so a mismatch between
    the prediction and the shipped sample is caught rather than trusted.
    """
    import plan_jobs as PJ
    out = []
    for split in ("train", "test"):
        d = os.path.join(archive, split, "argoverse2")
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".tgz") or "(" in f:
                continue
            scene = f[:-4]
            if PJ._hood_of("argoverse2", scene) != "keep":
                continue
            out.append((split, scene, os.path.join(d, f)))
    if shard:
        i, n = (int(v) for v in shard.split("/"))
        out = out[i::n]
    return out


def make_twin(split, scene, tar_path, archive, hood):
    """-> status string. Never raises."""
    dst_dir = os.path.join(twin_root(archive), split, "argoverse2")
    dst = os.path.join(dst_dir, scene + ".tgz")
    if os.path.isfile(dst):
        return "exists"
    tmp = tempfile.mkdtemp(prefix="hoodpair_")
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            man_name = [n for n in tf.getnames()
                        if os.path.basename(n) == "manifest.json"][0]
            man = json.load(tf.extractfile(man_name))
            if man.get("hood_variant") != "keep":
                return "not-keep"
            tf.extractall(tmp)
        sample_dir = os.path.join(tmp, scene)
        if not os.path.isdir(sample_dir):
            subs = [d for d in os.listdir(tmp)
                    if os.path.isdir(os.path.join(tmp, d))]
            if len(subs) != 1:
                return "unexpected tar layout"
            sample_dir = os.path.join(tmp, subs[0])

        import db267_hood_apply as HOOD
        from PIL import Image as _I
        import numpy as _np
        probe = _np.array(_I.open(os.path.join(sample_dir, "frames",
                                               "fr_0000.png")))
        hood_fit = fit_hood(hood, probe.shape[0], probe.shape[1])
        HOOD.BACKUP = os.path.join(tmp, "_bak")
        res = HOOD.apply_sample(sample_dir, hood_fit, 93)

        # verify the twin really differs before publishing it
        m2 = json.load(open(os.path.join(sample_dir, "manifest.json")))
        if m2.get("hood_variant") != "black" or not m2.get("hood_mask_applied"):
            return "apply did not mark black"
        if not m2.get("hood_px_withdrawn_per_frame"):
            return "apply withdrew 0 px"
        m2["paired_with"] = {"archive": "primary", "variant": "keep",
                             "scene": scene, "split": split}
        # Provenance, stated rather than implied: the pipeline detects the hood
        # PER SCENE from the raw cameras, but those are long deleted for an
        # archived sample, so a twin can only use the FIXED rig mask fitted to
        # delivery resolution. Measured against a pipeline-produced sample that
        # covers ~0.4% more of the frame and can still miss ~1 px at the
        # boundary. Deliberately biased to over-cover: a surviving hood pixel
        # would put ego bodywork where the contract promises scene.
        m2["hood_twin_provenance"] = {
            "made_by": "db270_hood_pairs",
            "mask": "db267 rig_hood_mask fitted to delivery res, block-max +1px",
            "not_per_scene_detected": True,
            "vs_pipeline_mask": "covers ~+0.4% of frame; boundary may differ ~1px",
        }
        json.dump(m2, open(os.path.join(sample_dir, "manifest.json"), "w"),
                  indent=1)

        os.makedirs(dst_dir, exist_ok=True)
        part = dst + ".part"
        with tarfile.open(part, "w:gz", compresslevel=1) as tf:
            tf.add(sample_dir, arcname=scene)
        os.replace(part, dst)
        return "made (%s)" % res
    except Exception as exc:                                   # noqa: BLE001
        return "FAIL %s: %s" % (type(exc).__name__, str(exc)[:60])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="")
    ap.add_argument("--shard", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    archive = a.archive
    if not archive:
        cfg = json.load(open(os.path.join(ROOT, "config.json")))
        archive = cfg["archive_root"]

    import db267_hood_apply as HOOD
    hood = HOOD.rig_hood_mask()

    todo = list_keep(archive, a.shard)
    if a.limit:
        todo = todo[:a.limit]
    print("keep-variant samples in this shard: %d" % len(todo), flush=True)

    t0, made, skipped, failed = time.time(), 0, 0, []
    for k, (split, scene, p) in enumerate(todo, 1):
        r = make_twin(split, scene, p, archive, hood)
        if r.startswith("made"):
            made += 1
        elif r in ("exists", "not-keep"):
            skipped += 1
        else:
            failed.append((scene, r))
        if k % 10 == 0 or k == len(todo):
            el = time.time() - t0
            print("  %d/%d  made=%d skipped=%d failed=%d  (%.1f/min)"
                  % (k, len(todo), made, skipped, len(failed),
                     60 * k / max(el, 1)), flush=True)
    print("DONE made=%d skipped=%d failed=%d" % (made, skipped, len(failed)),
          flush=True)
    for s, e in failed[:15]:
        print("   FAIL %s :: %s" % (s[:36], e), flush=True)


if __name__ == "__main__":
    main()
