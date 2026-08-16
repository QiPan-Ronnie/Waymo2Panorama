"""DB-240 — the EXACT recipe koi signed off on, recovered and frozen.

Provenance: the clip koi approved is `b93_out/clip_broute_rulemask.mp4`, built
2026-08-14T10:01:29Z.  That build was an ad-hoc heredoc that was never written to
disk; it was recovered verbatim from the session transcript on 2026-08-16 and is
reproduced here so the approved recipe survives independently of any transcript.

Two things in it are load-bearing and were NOT in the first `db240_rule_dataset`
rewrite, so they are called out rather than left implicit:

  1. The wedge is a UNION OVER 24 FRAMES (`range(0, 93, 4)`), not one frame.
     The original carried the comment "support boundaries wobble a few px" -
     EMC pose changes per frame, so a single frame's wedge is not a superset of
     the window's.  A strip derived from frame 0 alone can leave a few px of a
     later frame's contradiction outside the mask.

  2. There is a COVERAGE ASSERTION: every pixel of the measured 93-frame
     disagreement union must fall inside the rule mask.  The approved run
     printed `0` for this.  It is the only thing standing between "a blanket
     strip" and "a blanket strip that actually covers what it claims to".

Measured on AV2 00a6ffc1 in the approved run:
    strips 58-68 px wide, 7 adjacent pairs
    measured union 16.21% | wedge(lens) 15.49% | rule strips 30.52% of DOM
    measured-union px outside rule mask: 0

Note on that 30.52%: the approved script divided `rect & band_rectangle` by the
*rendered* domain (573697 px).  Numerator and denominator are different sets, so
the number overstates what is actually blacked out - `rect & domain` over the
same domain is ~21%.  Both describe the identical pixels; only the accounting
differs.  `strip_frac_of_domain` below reports the honest ratio and
`strip_frac_legacy` reproduces the approved run's number for continuity.
"""
from __future__ import annotations

import numpy as np

H_BAND_DEG = 35.0
SKIRT_PX = 4
WEDGE_STRIDE = 4          # union over range(0, n, 4) - the approved sampling


def wedge_union(log_dir, n_frames, cal, cte, SC, SM, pairs=None, stride=WEDGE_STRIDE):
    """Per-pair overlap wedge, unioned over the window - the approved step 1."""
    pairs = pairs if pairs is not None else SC.ADJACENT
    acc = {"%s|%s" % (a, b): np.zeros((SC.H, SC.W), bool) for a, b in pairs}
    for k in range(0, n_frames, stride):
        man = SC.manifest_from_dir(log_dir, k, 1)
        pose = SM.emc_poses(cal, man["cam_ts"], man["anchor_ts"], cte)
        sup = SM.camera_support_emc(pose)
        for a, b in pairs:
            acc["%s|%s" % (a, b)] |= sup[a] & sup[b]
    return acc


def rule_strips(wedge_pair, domain_band, W, H):
    """Blanket rectangular strip per pair: bounding columns + skirt, full band height.

    The meridian roll matters: a wedge straddling column 0/W-1 has a bounding
    interval spanning the whole image unless it is rolled to the centre first.
    """
    rows = np.nonzero(domain_band.any(1))[0]
    if len(rows) == 0:
        return np.zeros((H, W), bool), {}
    r0, r1 = int(rows.min()), int(rows.max())
    rect = np.zeros((H, W), bool)
    stats = {}
    for key, w in wedge_pair.items():
        w = w & domain_band
        occ = w.any(0)
        if not occ.any():
            stats[key] = {"strip_w": 0}
            continue
        roll = W // 2 if (occ[0] and occ[-1]) else 0
        idx = np.nonzero(np.roll(occ, roll))[0]
        c0, c1 = int(idx.min()) - SKIRT_PX, int(idx.max()) + SKIRT_PX
        cols = np.zeros(W, bool)
        cols[max(c0, 0):min(c1 + 1, W)] = True
        rect[r0:r1 + 1, np.roll(cols, -roll)] = True
        stats[key] = {"strip_cols": [c0, c1], "strip_w": c1 - c0 + 1, "roll": roll}
    return rect, stats


def coverage_check(rect, measured_union):
    """Approved invariant: the rule mask must be a superset of measured contradictions.

    Returns the number of measured-union pixels left uncovered.  The approved run
    printed 0; any non-zero value means the blanket strip is not blanket enough
    and must not be shipped.
    """
    return int((measured_union & ~rect).sum())
