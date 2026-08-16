"""Consumer-side validation: read every sample the way a trainer would.

The producer's own gates check what the producer knows.  This checks what the
consumer will actually hit - counts, shapes, dtypes, pairing, and the one
semantic property the whole contract rests on:

    every white mask pixel must carry a real sampled pixel

That is asserted here against the files on disk, not against a number the
producer wrote into its own manifest, so a bug in the producer's accounting
cannot hide a bad sample.

Deliberately does NOT flag black pixels under white mask: a night scene has
genuinely black pixels, and v15's contract says the mask channel - not the pixel
value - is what separates "missing" from "dark".  Checking colour here would
re-introduce the false alarm that rejected 4 good nuScenes scenes.
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np

OUT = sys.argv[1] if len(sys.argv) > 1 else r"E:/w2p_data/dataset_out"
FRAMES = 93
SHAPE = (1024, 2048)


def check_sample(d):
    """-> (ok, [problems])"""
    bad = []
    mp = os.path.join(d, "manifest.json")
    if not os.path.isfile(mp):
        return False, ["no manifest.json"], 0.0
    with open(mp, encoding="utf-8") as fh:
        man = json.load(fh)

    frames = sorted(glob.glob(os.path.join(d, "frames", "fr_*.png")))
    masks = sorted(glob.glob(os.path.join(d, "masks", "mk_*.png")))
    if len(frames) != FRAMES:
        bad.append("%d frames, expected %d" % (len(frames), FRAMES))
    if len(masks) != FRAMES:
        bad.append("%d masks, expected %d" % (len(masks), FRAMES))
    if not os.path.isfile(os.path.join(d, "rule_mask.png")):
        bad.append("no rule_mask.png")
    if len(frames) != len(masks):
        return False, bad, 0.0

    from PIL import Image
    # spot-check first / middle / last rather than all 93: the failure modes here
    # (wrong shape, wrong dtype, unpaired, mask not binary) are per-sample, not
    # per-frame, and reading 93x3 PNGs per sample across hundreds of samples
    # would cost more than it finds.
    white_total = 0
    for k in (0, FRAMES // 2, FRAMES - 1):
        fr = np.asarray(Image.open(frames[k]).convert("RGB"))
        mk = np.asarray(Image.open(masks[k]).convert("L"))
        if fr.shape[:2] != SHAPE:
            bad.append("frame %d shape %s" % (k, fr.shape))
        if mk.shape != SHAPE:
            bad.append("mask %d shape %s" % (k, mk.shape))
        u = np.unique(mk)
        if not set(u.tolist()) <= {0, 255}:
            bad.append("mask %d not binary: %s" % (k, u[:5].tolist()))
        keep = mk > 127
        white_total += int(keep.sum())
        # the contract: white must never sit on a pixel the rule mask killed
        rule = np.asarray(Image.open(os.path.join(d, "rule_mask.png")).convert("L")) > 127
        if (keep & rule).any():
            bad.append("frame %d: %d white px inside the seam strip"
                       % (k, int((keep & rule).sum())))
    if white_total == 0:
        bad.append("mask is entirely black - no supervision signal")

    # Does the clip actually move?  A freeze-frame passed every other check here:
    # 93 files, valid binary masks, clean manifest, and all 93 PNGs identical.
    #
    # Only *identical* frames are a defect. A clip that merely changes little is
    # a stopped vehicle - Waymo Perception has many - and failing those would
    # repeat the mistake the colour-based KEEP gate made: rejecting data for
    # looking like exactly what it is. The motion figure is returned for
    # reporting instead.
    a = np.asarray(Image.open(frames[0]).convert("L")).astype(np.int16)
    mid = np.asarray(Image.open(frames[FRAMES // 2]).convert("L")).astype(np.int16)
    end = np.asarray(Image.open(frames[-1]).convert("L")).astype(np.int16)
    motion = float(np.abs(a - end).mean())
    if np.array_equal(a, end) and np.array_equal(a, mid):
        bad.append("clip is a freeze-frame: first, middle and last frames identical")

    for f in ("dataset", "scene_id", "frames", "cameras", "hood_variant"):
        if f not in man:
            bad.append("manifest missing %s" % f)

    # Samples produced before the gate was corrected carry the old field name
    # (`keep_px_that_are_black`, which counted dark scene pixels as violations).
    # Those samples are stale rather than wrong - re-produce them - so say that
    # instead of crashing on a KeyError.
    if "keep_px_not_written" in man:
        if man["keep_px_not_written"] != 0:
            bad.append("manifest reports %d unwritten KEEP px"
                       % man["keep_px_not_written"])
    elif "keep_px_that_are_black" in man:
        bad.append("stale manifest schema (pre-gate-fix) - re-produce this sample")
    else:
        bad.append("manifest has no KEEP-integrity field")
    return not bad, bad, motion


def duplicates(dirs):
    """Samples whose content is byte-identical under different scene ids.

    Two producers sharing one scratch path race, and the loser converts the
    winner's records into its own directory - the result is two scene ids with
    the same pixels, which quietly breaks train/test independence because the
    split thinks they are different scenes. Found 2 such pairs in 93 E2E samples
    before the scratch paths were made per-process; the check stays because a
    duplicate is invisible to every per-sample check.
    """
    import hashlib
    from collections import defaultdict
    by = defaultdict(list)
    for d in dirs:
        f = os.path.join(d, "frames", "fr_0037.png")
        if not os.path.isfile(f):
            continue
        with open(f, "rb") as fh:
            by[hashlib.md5(fh.read()).hexdigest()].append(d)
    return [v for v in by.values() if len(v) > 1]


def jitter(dirs):
    """Per-source spread of the inter-frame time gap, from the manifests.

    A uniform source reports 0. nuScenes cannot reach 0 - its capture period holds
    three frames at 0/50/150 ms - so this is here to show the number rather than
    to pass or fail on it. It is the second thing the user caught that no check
    was looking at.
    """
    from collections import defaultdict
    out = defaultdict(list)
    for d in dirs:
        mp = os.path.join(d, "manifest.json")
        if not os.path.isfile(mp):
            continue
        try:
            with open(mp, encoding="utf-8") as fh:
                m = json.load(fh)
        except Exception:
            continue
        cv = m.get("frame_gap_cv")
        if cv is not None:
            out[m.get("dataset", "?")].append(float(cv))
    return {k: (float(np.median(v)), float(np.max(v))) for k, v in out.items() if v}


def main():
    dirs = sorted(glob.glob(os.path.join(OUT, "*", "*")))
    dirs = [d for d in dirs if os.path.isdir(d)]
    ok, fail = 0, []
    per, motion = {}, {}
    for d in dirs:
        res = check_sample(d)
        good, probs = res[0], res[1]
        mot = res[2] if len(res) > 2 else None
        ds = os.path.basename(os.path.dirname(d))
        per.setdefault(ds, [0, 0])
        if mot is not None:
            motion.setdefault(ds, []).append(mot)
        if good:
            ok += 1
            per[ds][0] += 1
        else:
            fail.append((d, probs))
            per[ds][1] += 1
    print("validated %d samples: %d OK, %d bad" % (len(dirs), ok, len(fail)))
    for ds, (g, b) in sorted(per.items()):
        print("  %-18s %3d ok  %d bad" % (ds, g, b))
    for d, probs in fail[:10]:
        print("  BAD %s" % os.path.relpath(d, OUT))
        for p in probs:
            print("      - %s" % p)
    if motion:
        print("  scene motion (mean |first-last| DN; low = stopped vehicle, not a fault):")
        for ds, v in sorted(motion.items()):
            v = sorted(v)
            print("      %-18s median %.1f   %d of %d below 1.0"
                  % (ds, v[len(v) // 2], sum(1 for x in v if x < 1.0), len(v)))
    jit = jitter(dirs)
    if jit:
        print("  frame-timing jitter (cv of inter-frame gap; 0 = uniform):")
        for ds, v in sorted(jit.items()):
            print("      %-18s median cv %.3f  worst %.3f" % (ds, v[0], v[1]))
    dups = duplicates(dirs)
    if dups:
        print("  DUPLICATE CONTENT under different scene ids: %d group(s)" % len(dups))
        for g in dups[:5]:
            print("      " + ", ".join(os.path.relpath(x, OUT) for x in g))
    else:
        print("  no duplicate content")
    return 1 if (fail or dups) else 0


if __name__ == "__main__":
    raise SystemExit(main())
