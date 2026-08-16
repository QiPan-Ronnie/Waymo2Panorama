"""DB-241 driver: source dataset -> pseudo-AV2 log -> koi-approved rule-mask sample.

One entry point per dataset.  Everything downstream of the adapter is shared, so
a sample from nuScenes and a sample from AV2 differ only in where the pixels came
from - not in how the mask was decided.  That is the whole point: koi signed off
on one recipe, and a per-dataset special case would quietly unsign it.

Package layout follows av2_1plus92_v15, because that is the shape Louison already
consumes:

    <out>/<dataset>/<sample_id>/
        frames/  fr_0000.png .. fr_0092.png    93 ERP frames, masked regions black
        masks/   mk_0000.png .. mk_0092.png    93 single-channel masks
        rule_mask.png                          the frozen strip mask for the window
        manifest.json                          provenance + the numbers that gate it
        clip.mp4                               10 fps preview

Mask contract, identical to v15 section 3:

    White (255) = strictly real camera pixel - trustworthy supervision
    Black   (0) = no trustworthy real pixel  - the generative model's territory

"strictly real" is enforced, not asserted: `keep = written & ~kill`, where
`written` is where a projection actually landed.  `manifest["keep_px_not_written"]`
must be 0 or the sample is rejected - see FOUNDATION.md invariant I3.

`keep_px_dark_scene` is reported alongside it and is NOT a defect: a night scene
has genuinely black pixels, and gating on colour would reject good night data -
the exact confusion v15 warns about when it says the mask channel, not the pixel
value, is what separates "missing" from "dark".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
for _p in ("db238_screening", "db239_seam_mask", "db240_rule_dataset",
           "db181_multids_snapshot"):
    sys.path.insert(0, os.path.join(AGENT, _p))

import db240_rule_dataset as DS  # noqa: E402


# Per-dataset knobs.  Only three things legitimately vary between rigs; anything
# else varying would mean the recipe is not actually shared.
PROFILES = {
    "argoverse2": {
        "skirt_px": 4,          # EMC available -> the approved value
        "undistort": False,     # AV2 ring cameras are already pinhole
        "note": "reference rig - this is the one koi signed off on",
    },
    "nuscenes": {
        "skirt_px": 4,          # per-sample_data timestamps + ego_pose -> EMC works
        "undistort": False,     # nuScenes ships rectified images
        "note": "6 cameras, ring closes, images already undistorted",
    },
    "waymo_perception": {
        "skirt_px": 4,          # real timestamps and pose -> EMC works
        "undistort": True,      # k1=0.347 -> ~71 px at the image edge, mandatory
        "note": "5 cameras, ring does NOT close: ~130 deg rear gap stays black",
    },
    "waymo_e2e": {
        "skirt_px": 6,          # no per-camera timestamps -> no EMC; +2 px covers
        "undistort": True,      # k=0.0736 -> ~6.8 px, larger than the 4 px skirt
        "note": "8 cameras, ring closes, but timestamps are zeroed -> static rig",
    },
}


def _clip(frames_dir, out_mp4, n, fps=10):
    """Preview clip.  Prefers ffmpeg (h264, plays anywhere); falls back to cv2.

    ffmpeg is not on PATH on this machine, so the cv2 path is the one that
    actually runs here - it is mp4v, which some players refuse, hence the
    preference order rather than picking one.
    """
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
             "-i", os.path.join(frames_dir, "fr_%04d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out_mp4],
            check=True, timeout=900)
        return "ffmpeg/h264"
    except Exception:
        pass
    try:
        import cv2
        from PIL import Image
        first = np.asarray(Image.open(os.path.join(frames_dir, "fr_0000.png")))
        h, w = first.shape[:2]
        vw = cv2.VideoWriter(out_mp4, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for k in range(n):
            fr = np.asarray(Image.open(os.path.join(frames_dir, "fr_%04d.png" % k)))
            vw.write(fr[:, :, ::-1])
        vw.release()
        return "cv2/mp4v"
    except Exception as exc:
        return "failed: %s" % exc


def build_sample(log_dir, out_root, dataset, sample_id, start=0, n=93,
                 hood_variant="keep", hood_mask_path=None, make_clip=True):
    """Run the frozen producer on one pseudo-AV2 log and gate the result."""
    if dataset not in PROFILES:
        raise ValueError("unknown dataset %r; known: %s"
                         % (dataset, sorted(PROFILES)))
    prof = PROFILES[dataset]

    prev_skirt = DS.SKIRT_PX
    DS.SKIRT_PX = prof["skirt_px"]
    try:
        out_dir = os.path.join(out_root, dataset, sample_id)
        man = DS.produce(log_dir, out_dir, dataset, sample_id, start=start, n=n,
                         hood_variant=hood_variant, hood_mask_path=hood_mask_path)
    finally:
        DS.SKIRT_PX = prev_skirt

    man["profile"] = dict(prof)
    man["mask_contract"] = {
        "255": "strictly real camera pixel - trustworthy supervision",
        "0": "no trustworthy real pixel - generative model's territory",
        "reference": "av2_1plus92_v15 section 3",
    }

    # Gates.  A sample that trips one is written but flagged, never silently shipped.
    gates = []
    if man["keep_px_not_written"] != 0:
        gates.append("I3 VIOLATED: %d KEEP pixels had no projection land on them"
                     % man["keep_px_not_written"])
    if not man["ring_closed"]:
        gates.append("ring does not close - a coverage gap is left black by design "
                     "(expected for waymo_perception)")
    if man["rule_mask_frac_of_band"] > 0.60:
        gates.append("K2: rule mask covers %.1f%% of band (>60%%)"
                     % (100 * man["rule_mask_frac_of_band"]))
    widths = [v.get("strip_w", 0) for v in man["pairs"].values()]
    if widths and max(widths) > 150:
        gates.append("K1: widest seam strip %d px (>150)" % max(widths))
    man["gates"] = gates
    man["accepted"] = not any(g.startswith(("I3", "K1", "K2")) for g in gates)

    if make_clip:
        man["clip"] = _clip(os.path.join(out_dir, "frames"),
                            os.path.join(out_dir, "clip.mp4"), n)

    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1)
    return man


def summarise(man):
    return ("%-18s %-14s cams=%d ring=%-5s strips=%s mask=%.1f%% of band  "
            "unwritten=%d  %s"
            % (man["dataset"], man["scene_id"][:14], man["n_cameras"],
               man["ring_closed"],
               sorted(v.get("strip_w", 0) for v in man["pairs"].values()),
               100 * man["rule_mask_frac_of_band"],
               man["keep_px_not_written"],
               "ACCEPTED" if man["accepted"] else "FLAGGED: " + "; ".join(man["gates"])))


if __name__ == "__main__":
    log_dir, out_root, dataset, sample_id = sys.argv[1:5]
    print(summarise(build_sample(log_dir, out_root, dataset, sample_id)))
