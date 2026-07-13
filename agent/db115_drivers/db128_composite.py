# -*- coding: utf-8 -*-
"""DB-128 cascade composite v6 — the shipped band-frame compositor for the 1+92 cascade dataset.

Layers in (all 2048x1024 BGR unless noted):
  band  : EGO_BLACK fine band render (ego body blacked, upper/lower ERP black)
  ez    : egozone mask (u8, white = pixels blacked BECAUSE of the ego mask)
  fil   : Tier-1 source = CAP-fast fill render (temporal backprojection, FAITH_MASK on)
  fai   : faithfill mask (u8, white = fill pixels that are NS-inpaint/plate, NOT real)
  wb    : Tier-2 source = CAP-fast worldbev render (shared per-log map sample)

v6 gate stack (DB-125 v3 + DB-128 four fixes; each maps to an eyeballed defect):
  G1  faithfill        : reject non-real fill pixels                     (DB-123)
  G2  sharpness        : Laplacian local var>40, win15, open/close 7     (DB-123)
  G3  colour           : fill vs wbev low-freq disagreement>45 -> trust wbev (DB-125 v3)
  G4  jurisdiction     : zone2 = egozone | interior band holes           (DB-128 #4: unmanaged holes)
  G5  specular (HSV)   : V>150 & S<70 dilate9, BOTH tiers                (DB-128 #1/#3: wet-road glare
                         is view-dependent -> reprojected glare is wrong content; Lambertian failure)
  G6  coherence        : ok open7/close11 + islands<2000px culled        (DB-128 #1: T1/T2 pixel-level
                         interleaving smears texture)
  G7  per-hole gain    : each zone2 component aligned to ITS OWN 21px band ring (DB-128 #2)
  feather              : tempo self-blur on the band-adjacent boundary   (DB-125 v3; NEVER blur(band) —
                         the EGO_BLACK black bleeds in, v2 lesson)

Residual (resid) goes to ProPainter (temporal propagation, only-inside-mask compositing).
40GB-A100 PP recipe: --subvideo_length 15 --neighbor_length 5 + PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True.

History: v1 (DB-125, plain 4-gate) -> v2 feather-blur(band) REGRESSION -> v3 (gain+colour+self-blur, shipped
02678d04) -> v4/v5 jurisdiction+lum experiments -> v6 (this file, shipped 05fa5048 fix) -> v7b clean_blur
(REGRESSION per user eyeball: big uniform smudge beats junk texture nowhere) -> **v8 PP-FULL-ZONE (shipped)**.

v8 verdict (DB-128 final, 2026-07-13): the ego-trail band (zone2) is a PHYSICS-limited region — the
front-pod rig can never observe its own footprint except at 4-6 deg grazing, so BOTH tiers only ever
hold stretched low-res pixels there (measured: in-band wbev HF 3.58 vs road 5.80; centre fill only 2%
real). Junk texture (v6) and uniform smudge (v7b) are two disguises of the same fact. The winning move
is to STOP pasting low-quality-real and hand the WHOLE zone2 to ProPainter: temporal flow propagation
from the sharp out-of-band anchors — clean, seam-free, temporally stable (3-frame strip verified;
lane paint crosses the band continuously). Semantics: band interior = 100% invented (vs ~40%
grazing-real before) — provenance note for koi; honest-black stays the fallback semantic option.
Recipe (v8c FINAL): video = EGO_BLACK band frames with the ERP FORMAT-BLACK below the content
envelope replaced by per-column road-tone edge-padding (+sigma3 grain) — WITHOUT this, PP treats the
format black as content and bleeds darkness into the zone, worsening over the sequence (user-caught
"black grows over time"); the content envelope colmax MUST be computed on band-real UNION zone2
(computing it on the holed band re-blacks the freshly filled zone — v8b bug). mask = zone2; ProPainter
--fp16 --subvideo_length 15 --neighbor_length 5 (+expandable_segments on 40GB); composite mask-only,
then restore format black below the envelope. Known minor traces: slight lane-paint smear-down (PP
colour diffusion), faint dark falloff at the very bottom edge.
compose_frame()/clean_blur() below are kept for the tier-cascade variant (02678d04-style dry scenes
where the graze-real pixels ARE decent); wet/complex scenes ship v8.
"""
import numpy as np
import cv2

H, W = 1024, 2048


def _spec_mask(img_f32):
    """View-dependent wet-road glare (specular) detector: bright AND unsaturated."""
    hsv = cv2.cvtColor(np.clip(img_f32, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2].astype(np.float32)
    s = hsv[:, :, 1].astype(np.float32)
    return cv2.dilate(((v > 150) & (s < 70)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0


def band_holes(bnz, zone):
    """Interior no-source holes of the band itself (NOT ego): black, inside the per-column
    content extent, small (200..30000 px), not touching the content bottom edge."""
    lower = np.zeros((H, W), bool)
    lower[H // 2:] = True
    colmax = np.full(W, -1)
    ys, xs = np.nonzero(bnz)
    np.maximum.at(colmax, xs, ys)
    rowidx = np.arange(H)[:, None]
    incontent = (rowidx <= (colmax[None, :] - 3)) & lower
    raw = incontent & ~bnz & ~zone
    edge_line = (rowidx >= (colmax[None, :] - 4)) & lower
    nl, lab = cv2.connectedComponents(raw.astype(np.uint8))
    keep = np.zeros_like(raw)
    for bi in range(1, nl):
        m = lab == bi
        s = m.sum()
        if s < 200 or s > 30000 or (m & edge_line).any():
            continue
        keep |= m
    return keep


def compose_frame(band, ez, fil, fai, wb):
    """v6 cascade composite for one band frame.
    Args: band/fil/wb float32 BGR HxWx3; ez/fai u8 masks.
    Returns (out u8 BGR, resid bool mask for ProPainter, stats dict)."""
    zone = ez > 127
    bnz = band.sum(2) >= 12
    zone2 = zone | band_holes(bnz, zone)
    nz = fil.sum(2) >= 12
    faith = fai > 127
    wbok = wb.sum(2) >= 12
    spec_f = _spec_mask(fil)
    spec_w = _spec_mask(wb)

    gray = cv2.cvtColor(fil.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    mu = cv2.boxFilter(lap, -1, (15, 15))
    mu2 = cv2.boxFilter(lap * lap, -1, (15, 15))
    sharp = (mu2 - mu * mu) > 40.0
    k7 = np.ones((7, 7), np.uint8)
    sharp = cv2.morphologyEx(sharp.astype(np.uint8), cv2.MORPH_OPEN, k7)
    sharp = cv2.morphologyEx(sharp, cv2.MORPH_CLOSE, k7) > 0

    ok = zone2 & nz & ~faith & sharp & ~spec_f
    k11 = np.ones((11, 11), np.uint8)
    ok = cv2.morphologyEx(ok.astype(np.uint8), cv2.MORPH_OPEN, k7)
    ok = cv2.morphologyEx(ok, cv2.MORPH_CLOSE, k11) > 0
    ok &= zone2 & nz & ~faith & ~spec_f
    nl, lab = cv2.connectedComponents(ok.astype(np.uint8))
    if nl > 1:
        cnt = np.bincount(lab.ravel())
        ok &= ~np.isin(lab, np.nonzero(cnt < 2000)[0][1:])
    fb = cv2.blur(fil, (31, 31))
    wbb = cv2.blur(wb, (31, 31))
    ok &= ~(ok & wbok & (np.abs(fb - wbb).sum(2) > 45.0))

    hole1 = zone2 & ~ok
    t2 = hole1 & wbok & ~spec_w
    resid = hole1 & ~(wbok & ~spec_w)

    fil_a, wb_a = fil.copy(), wb.copy()
    zl, zlab = cv2.connectedComponents(zone2.astype(np.uint8))
    for zi in range(1, zl):
        m = zlab == zi
        if m.sum() < 300:
            continue
        ring = (cv2.dilate(m.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0) & ~zone2 & bnz
        if ring.sum() < 300:
            continue
        bmed = np.median(band[ring], axis=0)
        for src, sm in [(fil_a, ok & m), (wb_a, t2 & m)]:
            if sm.sum() > 200:
                smed = np.median(src[sm], axis=0)
                g = np.clip(bmed / np.maximum(smed, 8.0), 0.75, 1.35)
                src[sm] = np.clip(src[sm] * g[None, :], 0, 255)

    tempo = band.copy()
    tempo[ok] = fil_a[ok]
    tempo[t2] = wb_a[t2]
    filled = ok | t2
    edge = filled & (cv2.dilate((bnz & ~zone2).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    edge |= (bnz & ~zone2) & (cv2.dilate(filled.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    if edge.any():
        tb = cv2.GaussianBlur(tempo, (9, 9), 0)
        tempo[edge] = tb[edge]
    out = np.clip(tempo, 0, 255).astype(np.uint8)
    out[~(zone2 | bnz)] = 0
    stats = {"zone2": int(zone2.sum()), "t1": int(ok.sum()), "t2": int(t2.sum()), "resid": int(resid.sum())}
    return out, resid, stats


def clean_blur(fin_u8, band, ez, spec_v=135, spec_s=80):
    """v7b post-PP polish of the filled area (run AFTER ProPainter composited the resid).

    Why: the fill's high-frequency ENERGY matches the band (HF std 6.3 vs 5.8 measured) but its
    high-frequency QUALITY is junk — patch seams, multi-source speckle, PP smears — because the
    map cell (5cm) is under-resolved at near-nadir ERP magnification. Sharpening is the WRONG drug
    (measured); the right look is a photographic near-field falloff: edge-preserving smooth of the
    filled area only, plus a sub-threshold glare kill (wet-road specular the V>150 gate missed).
    Verdict frame 05fa5048 a091: ghost-writing gone, junk texture -> clean falloff."""
    H2, W2 = fin_u8.shape[:2]
    zone = ez > 127
    bnz = band.sum(2) >= 12
    lower = np.zeros((H2, W2), bool)
    lower[H2 // 2:] = True
    fnz = fin_u8.astype(np.int32).sum(2) >= 12
    filled = lower & fnz & ~(bnz & ~zone)
    hsv = cv2.cvtColor(fin_u8, cv2.COLOR_BGR2HSV)
    spec2 = filled & (hsv[:, :, 2] > spec_v) & (hsv[:, :, 1] < spec_s)
    spec2 = cv2.dilate(spec2.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    fin = fin_u8
    if spec2.any():
        fin = cv2.inpaint(fin, (spec2 & filled).astype(np.uint8) * 255, 7, cv2.INPAINT_TELEA)
    sm = cv2.bilateralFilter(fin, 11, 45, 9)
    sm = cv2.GaussianBlur(sm, (5, 5), 0)
    dist = cv2.distanceTransform((~(bnz & ~zone)).astype(np.uint8), cv2.DIST_L2, 3)
    alpha = np.clip(dist / 6.0, 0, 1)[:, :, None] * filled[:, :, None].astype(np.float32)
    return (fin.astype(np.float32) * (1 - alpha) + sm.astype(np.float32) * alpha).astype(np.uint8)
