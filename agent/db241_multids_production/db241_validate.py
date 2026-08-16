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
        return False, ["no manifest.json"]
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
        return False, bad

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
    return not bad, bad


def main():
    dirs = sorted(glob.glob(os.path.join(OUT, "*", "*")))
    dirs = [d for d in dirs if os.path.isdir(d)]
    ok, fail = 0, []
    per = {}
    for d in dirs:
        good, probs = check_sample(d)
        ds = os.path.basename(os.path.dirname(d))
        per.setdefault(ds, [0, 0])
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
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
