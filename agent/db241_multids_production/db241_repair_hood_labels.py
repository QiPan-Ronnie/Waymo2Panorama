"""Correct hood_variant in manifests produced before the label bug was found.

No hood mask was ever supplied, so every sample on disk is the keep variant.
Roughly half of them claim 'black'. The pixels are right; only the label lies,
and a trainer that filters on hood_variant would silently get keep data in both
buckets.

Rewrites the label only - never the frames, never the mask - and records that the
correction happened so the manifest does not simply look as if it was always
right.
"""
import glob
import json
import os

OUT = r"E:/w2p_data/dataset_out"
fixed = already = 0
for mp in glob.glob(os.path.join(OUT, "*", "*", "manifest.json")):
    try:
        with open(mp, encoding="utf-8") as fh:
            m = json.load(fh)
    except Exception:
        continue
    if m.get("hood_mask_applied") is not None:
        already += 1
        continue
    requested = m.get("hood_variant", "keep")
    m["hood_variant"] = "keep"
    m["hood_variant_requested"] = requested
    m["hood_mask_applied"] = False
    m["hood_note"] = ("no hood mask supplied for this rig - this sample is the "
                      "keep variant regardless of what was requested")
    m["label_corrected_2026_08_16"] = (
        "hood_variant said %r; no mask was applied, so it is 'keep'" % requested)
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    fixed += 1
print("corrected %d manifests, %d already truthful" % (fixed, already))
