"""Assemble the produced samples into the package koi asked for on 08-14.

The four things that meeting decided about packaging, and where each lands:

  "each dataset ~500, four sources"      -> per-source counts in the README table
  "hold one whole dataset out as OOD,
   the smallest one"                     -> chosen by produced-sample count, not
                                            by a guess about official size
  "split the rest 7:3 or 8:2"            -> deterministic, by sorted scene id, so
                                            the split is reproducible from the
                                            manifest alone and does not move when
                                            new samples are added later
  "write down what you packaged and
   what you kept"                        -> README.md, generated from the actual
                                            files on disk rather than from intent

Nothing here re-derives a mask.  If a sample was flagged by a gate it stays out
of the packaged set and is listed in the README under rejected, with the reason.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

OUT = r"E:/w2p_data/dataset_out"
PKG = r"E:/w2p_data/db241_delivery"
TRAIN_FRAC = 0.8


def load_samples(out_root=OUT):
    found = {}
    if not os.path.isdir(out_root):
        return found
    for ds in sorted(os.listdir(out_root)):
        d = os.path.join(out_root, ds)
        if not os.path.isdir(d):
            continue
        rows = []
        for s in sorted(os.listdir(d)):
            mp = os.path.join(d, s, "manifest.json")
            if os.path.isfile(mp):
                with open(mp, encoding="utf-8") as fh:
                    m = json.load(fh)
                m["_path"] = os.path.join(d, s)
                rows.append(m)
        if rows:
            found[ds] = rows
    return found


def split(rows, frac=TRAIN_FRAC):
    """Deterministic by scene id - reproducible, and stable as samples are added."""
    ordered = sorted(rows, key=lambda m: str(m["scene_id"]))
    n_train = int(round(len(ordered) * frac))
    return ordered[:n_train], ordered[n_train:]


def build(out_root=OUT, pkg_root=PKG, copy=True):
    found = load_samples(out_root)
    if not found:
        return None
    accepted = {ds: [m for m in rows if m.get("accepted")] for ds, rows in found.items()}
    rejected = {ds: [m for m in rows if not m.get("accepted")] for ds, rows in found.items()}
    live = {ds: rows for ds, rows in accepted.items() if rows}
    if not live:
        return None

    # OOD = the smallest source by produced samples, per koi 08-14. With one
    # source there is nothing to hold out; say so rather than emptying the pack.
    ood = min(live, key=lambda ds: len(live[ds])) if len(live) > 1 else None

    if copy:
        os.makedirs(pkg_root, exist_ok=True)
    plan = {"schema": "db241.delivery.v1", "train_frac": TRAIN_FRAC,
            "ood_holdout": ood, "sources": {}}
    for ds, rows in live.items():
        if ds == ood:
            plan["sources"][ds] = {"role": "ood_holdout", "n": len(rows),
                                   "packaged_for_louison": 0,
                                   "scenes": [m["scene_id"] for m in rows]}
            continue
        tr, te = split(rows)
        plan["sources"][ds] = {"role": "train_test", "n": len(rows),
                               "packaged_for_louison": len(tr),
                               "train": [m["scene_id"] for m in tr],
                               "test": [m["scene_id"] for m in te]}
    plan["rejected"] = {ds: [{"scene_id": m["scene_id"], "gates": m.get("gates", [])}
                             for m in rows] for ds, rows in rejected.items() if rows}

    if copy:
        for ds, rows in live.items():
            role = plan["sources"][ds]["role"]
            for m in rows:
                if role == "ood_holdout":
                    sub = "held_out_ood"
                else:
                    sub = "train" if m["scene_id"] in plan["sources"][ds]["train"] else "test"
                dst = os.path.join(pkg_root, sub, ds, str(m["scene_id"]))
                if not os.path.isdir(dst):
                    shutil.copytree(m["_path"], dst)
        with open(os.path.join(pkg_root, "split.json"), "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=1)
        with open(os.path.join(pkg_root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(readme(plan, live))
    return plan


def readme(plan, live):
    L = []
    A = L.append
    A("# DB-241 stage-2 dataset\n")
    A("Built to the contract koi set on 2026-08-14: **93 homogeneous frames per")
    A("sample**, no 1+92, no ground or sky fill, no back-projection, seams painted")
    A("out with a blanket rule mask, and the ego hood in two variants split across")
    A("scenes rather than within a scene.\n")
    A("## Mask contract\n")
    A("Identical to `av2_1plus92_v15` section 3, single-channel PNG:\n")
    A("> **White (255) = strictly real camera pixel** - trustworthy supervision.  ")
    A("> **Black (0) = no trustworthy real pixel** - the generative model's territory.\n")
    A("\"Strictly real\" is enforced rather than asserted: the mask is built from where")
    A("a projection actually landed, not from where a camera claims coverage, and every")
    A("packaged sample carries `keep_px_not_written: 0` in its manifest. Black arises")
    A("from three sources only - the rig's own blind spots (sky top, nadir, and any")
    A("uncovered azimuth on a rig whose ring does not close), the seam strips, and the")
    A("hood on `hood_variant: black` scenes.\n")
    A("`keep_px_dark_scene` also appears and is **not** a defect: night scenes have")
    A("genuinely black pixels. Gating on colour would throw away good night data.\n")
    A("**Do not threshold the frames to recover the mask.** Black is a valid image")
    A("colour; the mask channel is what separates \"missing\" from \"a dark object\".\n")
    A("## Loss\n")
    A("Per koi, 2026-08-14: compute loss **only** on the white region. Multiply the")
    A("per-frame mask into the loss; do not let the black regions contribute.\n")
    A("## What is packaged and what is held back\n")
    A("| source | samples | role | given to Louison | held back |")
    A("|---|---|---|---|---|")
    for ds, info in sorted(plan["sources"].items()):
        if info["role"] == "ood_holdout":
            A("| **%s** | %d | **OOD holdout** | 0 | all %d |" % (ds, info["n"], info["n"]))
        else:
            A("| %s | %d | train/test | %d (train) | %d (test) |"
              % (ds, info["n"], len(info["train"]), len(info["test"])))
    A("")
    if plan["ood_holdout"]:
        A("`%s` is the OOD holdout: koi's rule was to keep the **smallest** source out"
          % plan["ood_holdout"])
        A("of the training package entirely, so it can serve as a never-seen evaluation")
        A("set. It is packaged here under `held_out_ood/` for our own evaluation and")
        A("must not be handed over.\n")
    else:
        A("No OOD holdout yet - only one source has accepted samples, so there is")
        A("nothing to hold out. This must be revisited before delivery.\n")
    A("Train/test split is %d:%d, deterministic by sorted scene id, so it is"
      % (round(TRAIN_FRAC * 10), 10 - round(TRAIN_FRAC * 10)))
    A("reproducible from `split.json` and stable when more samples are added later.\n")
    if plan.get("rejected"):
        A("## Rejected samples\n")
        A("| source | scene | why |")
        A("|---|---|---|")
        for ds, rows in sorted(plan["rejected"].items()):
            for r in rows:
                A("| %s | %s | %s |" % (ds, r["scene_id"], "; ".join(r["gates"]) or "-"))
        A("")
    A("## Layout\n")
    A("```")
    A("train/<source>/<scene>/  frames/fr_0000..0092.png   93 ERP frames, 2048x1024")
    A("                         masks/ mk_0000..0092.png   93 single-channel masks")
    A("                         rule_mask.png              frozen seam strips")
    A("                         manifest.json              provenance + gate numbers")
    A("                         clip.mp4                   10 fps preview")
    A("test/<source>/<scene>/   same")
    A("held_out_ood/<source>/<scene>/  same - NOT for training")
    A("```\n")
    A("## Per-rig facts worth knowing before training\n")
    A("| source | cameras | ring closes | seam strip px | mask %% of band |")
    A("|---|---|---|---|---|")
    for ds, rows in sorted(live.items()):
        m = rows[0]
        w = sorted(v.get("strip_w", 0) for v in m["pairs"].values())
        A("| %s | %d | %s | %d-%d | %.1f%% |"
          % (ds, m["n_cameras"], "yes" if m["ring_closed"] else "**no**",
             min(w), max(w), 100 * m["rule_mask_frac_of_band"]))
    A("")
    A("A rig whose ring does not close has a real angular gap with no camera in it")
    A("(Waymo Perception has no rear camera). That gap is black in the frame and 0 in")
    A("the mask, exactly like any other blind spot - it is not a seam and is not")
    A("strip-masked.\n")
    return "\n".join(L)


if __name__ == "__main__":
    p = build(copy="--dry" not in sys.argv)
    print(json.dumps(p, indent=1) if p else "no samples yet")
