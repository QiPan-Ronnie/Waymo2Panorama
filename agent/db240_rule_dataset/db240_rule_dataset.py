"""DB-240 rule-mask dataset producer — one recipe, every dataset.

koi, 2026-08-14 meeting:
  - "直接把中间直接 mask 就行了" -> blanket rule mask at the seams (approved 00:36:01)
  - "你现在 93 个直接全部用就好了 ... 他已经可以吃这种破洞，完全不用补" (00:42:04)
    -> 93 homogeneous frames, no 1+92, no first-frame completion, no ground fill
  - hood: two variants, one per scene, ~50/50 across scenes (00:44:13-00:45:28)
  - "先不要有任何反投影" (00:43:35)

Why one producer covers all datasets: every `db181_multids` adapter converts its
source into the AV2 directory layout with `ring_*` camera names, so this reads
that layout and never learns a per-dataset special case.

Why it needs no LiDAR and no annotations: the pixels are a rotation-only resample
of one raw JPEG (DB-239 B-route, warp identically zero), and the rule mask is
derived from calibration geometry alone.  That is what makes the LiDAR-free
Waymo E2E usable under this spec, and it is why this runs on a laptop.

Outputs per scene:
    frames/fr_%04d.png   93 ERP frames, rotation-only, rule mask applied (black)
    masks/mk_%04d.png    255 = KEEP (real pixel, supervise), 0 = RECONSTRUCT
    rule_mask.png        the frozen mask for the window (identical every frame)
    manifest.json        provenance: dataset, scene, window, hood variant, geometry
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "db238_screening"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "db239_seam_mask"))

import db238_screen as SC  # noqa: E402
import db239_seam_mask as SM  # noqa: E402
import db240_gpu as GPU  # noqa: E402

H, W = SC.H, SC.W
ELEV_DEG = 35.0
SKIRT_PX = 4


def present_cameras(log_dir):
    """Cameras this scene actually has, discovered from disk - not from a list.

    Filtering against AV2's seven hard-coded names silently drops any camera a
    converted dataset names differently: nuScenes maps its six to
    ring_front_center/front_left/front_right/side_left/side_right/`ring_rear`,
    and `ring_rear` is not an AV2 name.  Losing it would not raise - it would
    quietly produce a five-camera rig with a fake closed ring and a seam strip
    painted across the unrendered rear.
    """
    root = os.path.join(log_dir, "sensors", "cameras")
    if not os.path.isdir(root):
        return []
    out = []
    for cam in sorted(os.listdir(root)):
        if glob.glob(os.path.join(root, cam, "*.jpg")) or \
           glob.glob(os.path.join(root, cam, "*.png")):
            out.append(cam)
    return out


def adjacent_pairs(cams, pose, sup=None):
    """Ring neighbours by optical-axis azimuth, so a 5- or 8-camera rig works.

    AV2's hard-coded ADJACENT list is a 7-camera fact; deriving the ring from the
    calibration keeps the same code correct for every converted dataset.

    The wrap-around pair is NOT assumed.  A 252-degree rig (Waymo Perception has
    no rear camera) has a real gap between its two end cameras: they share no
    field of view, so pairing them would paint a strip across open sky and
    ground that no seam runs through.  When `sup` is supplied the closing pair is
    kept only if those two cameras actually overlap; a pair with zero shared
    support is dropped and reported, which is also how a genuine coverage gap
    announces itself in the manifest.
    """
    az = {}
    for c in cams:
        z = pose[c]["R"][:, 2]
        az[c] = np.arctan2(z[1], z[0])
    order = sorted(cams, key=lambda c: az[c])
    ring = [(order[i], order[i + 1]) for i in range(len(order) - 1)]
    close = (order[-1], order[0])
    if sup is None or (sup[close[0]] & sup[close[1]]).any():
        ring.append(close)
    return ring


def uniform_time_indices(log_dir, cams, start, n):
    """Frame indices sampled on a uniform TIME grid, not by file order.

    Taking the k-th file assumes the source captures at a constant rate.  AV2
    (20 Hz), Waymo Perception (10 Hz) and E2E (synthetic 10 Hz) do; nuScenes does
    not.  Its camera stream runs at 10 Hz with a LiDAR-synced keyframe inserted
    every ~250 ms, so the real spacing cycles 50 / 100 / 100 ms.  Played back at a
    constant frame rate that is visible stutter - the scene lurches forward twice
    as far between some frames as between others - and a video model trained on it
    would be learning inconsistent motion.

    The grid step is the median inter-frame gap, so a uniform source is a no-op:
    every target lands exactly on its own frame and the indices come back as
    start..start+n-1.

    Returns None when the window cannot be covered, so the caller can fall back
    rather than silently emitting a short or repeating clip.
    """
    ref_cam = "ring_front_center" if "ring_front_center" in cams else cams[0]
    stems = sorted(
        int(os.path.basename(p)[:-4])
        for p in glob.glob(os.path.join(log_dir, "sensors", "cameras", ref_cam, "*.jpg"))
        if os.path.basename(p)[:-4].isdigit())
    if len(stems) < n:
        return None
    ts = np.asarray(stems, dtype=np.int64)
    med = int(np.median(np.diff(ts)))
    if med <= 0:
        return None
    t0 = ts[min(start, len(ts) - 1)]

    def sample(step):
        targets = t0 + step * np.arange(n, dtype=np.int64)
        if targets[-1] > ts[-1]:
            return None
        idx = np.searchsorted(ts, targets)
        idx = np.clip(idx, 1, len(ts) - 1)
        idx = np.where(np.abs(ts[idx - 1] - targets) <= np.abs(ts[idx] - targets),
                       idx - 1, idx)
        # force strictly increasing: a tie can round two targets onto one frame
        out = []
        for i in idx.tolist():
            if out and i <= out[-1]:
                i = out[-1] + 1
            if i >= len(ts):
                return None
            out.append(int(i))
        d = np.diff(ts[out]).astype(float)
        return out, float(d.std() / d.mean()) if d.mean() else (out, 9.9)

    # Search the step rather than assuming the median. nuScenes' capture period is
    # 250 ms holding three frames at offsets 0/50/150, so NO step yields uniform
    # spacing - the median step scores cv 0.289 (mostly 100 ms with sudden 50 ms
    # hitches) while 125 ms scores 0.218 (a regular 100/150 alternation, which
    # reads far smoother). On a genuinely uniform source the median wins with
    # cv = 0 and the search is a no-op.
    cands = []
    for mult in (0.5, 2.0 / 3, 0.75, 1.0, 1.25, 4.0 / 3, 1.5, 2.0, 2.5, 3.0):
        step = int(round(med * mult))
        got = sample(step)
        if got:
            cands.append((got[1], step, got[0]))
    if not cands:
        return None
    # Lowest jitter wins, but near-ties go to the shorter step: a longer step
    # covers more seconds in the same 93 frames, so each frame moves further and
    # the clip drifts away from the per-frame motion the other rigs deliver.
    floor = min(c[0] for c in cands)
    return min((c for c in cands if c[0] <= floor + 0.02), key=lambda c: c[1])[2]


WEDGE_STRIDE = 4          # koi-approved sampling: union over range(0, n, 4)


def rule_mask(log_dir, cal, cte, cams, pairs, domain_band, n=93,
              stride=WEDGE_STRIDE, indices=None):
    """Blanket rectangular strip over each adjacent pair's overlap wedge.

    Rectangular by intent: koi asked for the simple, no-confidence-value mask.

    The wedge is unioned over the whole window, not read off one frame.  Under
    EMC the per-camera rotation changes every frame, so the support boundary
    wobbles a few px and a frame-0 wedge is not a superset of the window's.
    Measured on AV2 00a6ffc1: frame-0 strips leave 5 px of the 93-frame measured
    disagreement uncovered; the 24-frame union leaves 0 and reproduces the mask
    koi signed off on bit-for-bit.  See db240_koi_reference.py for the recovered
    original and that A/B.
    """
    acc = {"%s|%s" % (a, b): np.zeros((H, W), bool) for a, b in pairs}
    grid = indices if indices is not None else list(range(n))
    for k in grid[::stride]:
        man = SC.manifest_from_dir(log_dir, k, 0, cams)
        cam_ts = {c: man["cam_ts"][c] for c in cams}
        pose = SM.emc_poses({c: cal[c] for c in cams}, cam_ts, man["anchor_ts"], cte)
        sup = SM.camera_support_emc(pose)
        for a, b in pairs:
            acc["%s|%s" % (a, b)] |= sup[a] & sup[b]

    rows = np.nonzero(domain_band.any(1))[0]
    if len(rows) == 0:
        return np.zeros((H, W), bool), {}
    r0, r1 = int(rows.min()), int(rows.max())
    rect = np.zeros((H, W), bool)
    stats = {}
    for key, w in acc.items():
        w = w & domain_band
        occ = w.any(0)
        if not occ.any():
            stats[key] = {"overlap_px": 0, "strip_w": 0}
            continue
        roll = W // 2 if (occ[0] and occ[-1]) else 0        # wrap at the meridian
        idx = np.nonzero(np.roll(occ, roll))[0]
        c0, c1 = int(idx.min()) - SKIRT_PX, int(idx.max()) + SKIRT_PX
        cols = np.zeros(W, bool)
        cols[max(c0, 0):min(c1 + 1, W)] = True
        rect[r0:r1 + 1, np.roll(cols, -roll)] = True
        stats[key] = {"overlap_px": int(w.sum()),
                      "strip_cols": [c0, c1], "strip_w": c1 - c0 + 1}
    return rect, stats


def render_frame(log_dir, k, cal, cte, cams, elev_domain):
    """Rotation-only composite: every pixel is one bilinear tap of one raw JPEG.

    Returns `written`, not just `domain`.  The two are not the same: a camera's
    support map says a ray *should* land in its image, while `_project` decides
    whether it actually does, and the two disagree on ~330 px per frame at the
    support boundary.  Those pixels stay black.  Calling them domain and marking
    them KEEP tells the trainer to compute loss against a hole - which is exactly
    the thing the mask contract exists to prevent - so the mask has to be built
    from what was sampled, not from what was claimed.
    """
    man = SC.manifest_from_dir(log_dir, k, 0, cams)
    cam_ts = {c: man["cam_ts"][c] for c in cams}
    imgs = SC.load_images(log_dir, cam_ts)
    pose = SM.emc_poses({c: cal[c] for c in cams}, cam_ts, man["anchor_ts"], cte)

    if GPU.available():
        # Verified against the CPU path on AV2: support, ownership and written
        # differ by 0 px, and the saved uint8 frame is byte-identical over all
        # 2,097,152 pixels. 8.4x faster warm, and the grids live in VRAM rather
        # than in the 524 MB per-worker host footprint that capped parallelism.
        sup_g, own_g = GPU.support_and_own(pose, cams, SC.DIRS_FLAT, H, W)
        own = own_g.reshape(H, W).cpu().numpy()
        domain = (own >= 0) & elev_domain
        erp, written = GPU.sample_frame(pose, cams, imgs, own_g, domain,
                                        SC.DIRS_FLAT, H, W)
        return erp.astype(np.float32), written, pose

    sup = SM.camera_support_emc(pose)
    face = np.full((H, W), -2.0, np.float32)
    own = np.full((H, W), -1, np.int16)
    for i, cam in enumerate(cams):
        f = (SC.DIRS_FLAT @ pose[cam]["R"])[:, 2].reshape(H, W)
        u = sup[cam] & (f > face)
        face[u] = f[u]
        own[u] = i
    domain = (own >= 0) & elev_domain
    erp = np.zeros((H, W, 3), np.float32)
    written = np.zeros((H, W), bool)
    for i, cam in enumerate(cams):
        m = (own == i) & domain
        if not m.any():
            continue
        ys, xs = np.nonzero(m)
        X = 1e6 * SC.DIRS[ys, xs]
        px, py, ok = SC._project(pose[cam], X)
        written[ys[ok], xs[ok]] = True
        erp[ys[ok], xs[ok]] = SM.bilinear_rgb(imgs[cam], px[ok], py[ok])
    return erp, written, pose


def produce(log_dir, out_dir, dataset, scene_id, start=0, n=93,
            hood_variant="keep", hood_mask_path=None):
    from PIL import Image
    os.makedirs(os.path.join(out_dir, "frames"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "masks"), exist_ok=True)
    cams = present_cameras(log_dir)
    cal = SC.load_calibration(log_dir, cams)
    cte = SM.load_ego_interp(log_dir)
    if len(cams) < 4:
        raise RuntimeError("only %d ring cameras present: %s" % (len(cams), cams))
    elev = np.degrees(np.arcsin(np.clip(SC.DIRS[:, :, 2], -1, 1)))
    elev_domain = np.abs(elev) < ELEV_DEG

    # Frames are chosen on a uniform time grid. On a constant-rate source this is
    # exactly start..start+n-1; on nuScenes it drops the LiDAR-synced extra frames
    # that make playback stutter. Falls back to file order if the grid cannot be
    # laid down, rather than emitting a short clip.
    indices = uniform_time_indices(log_dir, cams, start, n)
    grid_uniform = indices is not None
    if indices is None:
        indices = [start + i for i in range(n)]
    # Record what the frame spacing actually came out as. A clip whose gaps vary
    # teaches the model inconsistent motion, and it is not visible in any
    # per-frame check - it only shows up on playback, which is how it was missed.
    _gaps = np.diff([SC.manifest_from_dir(log_dir, k, 0, cams)["anchor_ts"]
                     for k in indices]).astype(float)
    frame_gap_cv = float(_gaps.std() / _gaps.mean()) if _gaps.size and _gaps.mean() else 0.0
    frame_gap_ms = float(np.median(_gaps) / 1e6) if _gaps.size else 0.0

    # the rule mask is frozen for the window - zero flicker by construction -
    # but it is derived from the whole window's wedge union, not one frame
    _, dom0, pose0 = render_frame(log_dir, indices[0], cal, cte, cams, elev_domain)
    sup0 = SM.camera_support_emc(pose0)
    pairs = adjacent_pairs(cams, pose0, sup0)
    ring_closed = len(pairs) == len(cams)
    rect, pair_stats = rule_mask(log_dir, cal, cte, cams, pairs, elev_domain, n=n,
                                 indices=indices)
    rect &= elev_domain

    # koi asked for two hood variants split across scenes.  That only exists if a
    # hood mask is actually supplied: without one, "black" and "keep" produce
    # byte-identical data under two different labels, which is worse than having
    # one variant because the manifest would claim a contrast that is not there.
    # So the manifest records what was applied, not what was requested.
    hood = None
    if hood_variant == "black" and hood_mask_path and os.path.isfile(hood_mask_path):
        hood = np.asarray(Image.open(hood_mask_path).convert("L")) > 127
    hood_applied = hood is not None
    hood_effective = "black" if hood_applied else "keep"

    dom_px = int(dom0.sum())
    keep_not_written = 0        # the invariant: must be 0 (I3)
    keep_dark = 0               # informational: real pixels that happen to be black
    for i in range(n):
        erp, written, _ = render_frame(log_dir, indices[i], cal, cte, cams, elev_domain)
        kill = rect | (hood if hood is not None else False)
        out = erp.copy()
        out[kill] = 0
        keep = written & ~kill        # KEEP means "a real sensor pixel landed here"
        keep_not_written += int((keep & ~written).sum())
        # NOT a defect: a night scene has genuinely black pixels, and v15's own
        # contract warns "black is a valid image colour - the mask channel is what
        # disambiguates missing from dark". Counted only so the number is visible.
        keep_dark += int((keep & (out.sum(2) == 0)).sum())
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(
            os.path.join(out_dir, "frames", "fr_%04d.png" % i))
        Image.fromarray((keep * 255).astype(np.uint8)).save(
            os.path.join(out_dir, "masks", "mk_%04d.png" % i))
    Image.fromarray((rect * 255).astype(np.uint8)).save(
        os.path.join(out_dir, "rule_mask.png"))

    man = {"schema": "db240.rulemask.v2", "dataset": dataset, "scene_id": scene_id,
           "window": [indices[0], indices[-1]], "frames": n,
           "frame_indices_uniform_in_time": grid_uniform,
           "frame_gap_ms": round(frame_gap_ms, 2),
           "frame_gap_cv": round(frame_gap_cv, 4),
           "cameras": cams, "n_cameras": len(cams),
           "hood_variant": hood_effective,
           "hood_variant_requested": hood_variant,
           "hood_mask_applied": hood_applied,
           "hood_note": None if hood_applied else
               "no hood mask supplied for this rig - this sample is the "
               "keep variant regardless of what was requested",
           "projection": "rotation_only",
           "mask_rule": "blanket rectangular strip per adjacent-pair overlap wedge, "
                        "+%d px skirt, wedge unioned every %d frames" % (SKIRT_PX, WEDGE_STRIDE),
           "ring_closed": ring_closed,
           "coverage_gap_deg": None if ring_closed else "rig does not close: "
                              "no overlap between ring ends, rear left unmasked and unrendered",
           "domain_px": dom_px,
           "rule_mask_px": int(rect.sum()),
           "rule_mask_frac_of_band": round(float(rect.sum()) / max(int(elev_domain.sum()), 1), 5),
           "rule_mask_frac_of_domain": round(float((rect & dom0).sum()) / max(dom_px, 1), 5),
           "keep_px_not_written": keep_not_written,
           "keep_px_dark_scene": keep_dark,
           "pairs": pair_stats,
           "contract": {"255": "KEEP - real sensor pixel, compute loss here",
                        "0": "RECONSTRUCT - no loss"}}
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1)
    return man


if __name__ == "__main__":
    print(json.dumps(produce(*sys.argv[1:6]), indent=1))
