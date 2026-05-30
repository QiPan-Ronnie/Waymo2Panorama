"""ERP geometric-fidelity metrics: GCSR (primary, no-reference) + VDR (relative-to-L1).

Built from the 2026-05-29 metric-design + adversarial-verify workflow (wf_55c3370b-47c).
Goal: turn the user's visual judgment ("3DGS is warped, L1 is clean") into a HARD number
that is LOW for the rigid-clean L1 panorama and HIGH for the depth-re-rendered 3DGS panorama,
and ALSO emit a visual diagnostic so the eye can verify the number (vision-first discipline).

The one exact ERP invariant everything rests on (verified against
code/waymo2panorama/projection/sphere_projection.py:85-100):
    theta = pi - (u+0.5)/W * 2*pi          # azimuth, ego x=forward, y=left
    phi   = pi/2 - (v+0.5)/H * pi           # elevation, z=up
    d     = (cos phi cos theta, cos phi sin theta, sin phi)   # unit ray, ego frame
Under any rigid single-center spherical projection, a straight 3D line images to a GREAT
CIRCLE = a plane through the origin {d : n.d=0}. L1 is exactly such a projection -> residual ~0.
A depth-based 3DGS re-render bends the line off any single plane -> residual > 0.

GCSR (Great-Circle Sagitta Residual, PRIMARY, absolute/no-ref):
  track each edge as a CONNECTED PIXEL COMPONENT (Canny + connectedComponents, NOT LSD --
  LSD fragments a wavy edge into locally-straight chords and launders the warp), fit ONE
  great-circle plane over the whole component (TLS + IRLS Tukey), and report the SAGITTA
  (max off-endpoint-chord angular deviation) which is immune to fragmentation.

VDR (Vertical-edge Drift Residual, SECONDARY, relative-to-L1):
  detect near-vertical long edges on the L1 anchor, TRANSFER their pixel locations onto the
  test image, and report the per-edge change in RMS column deviation. Ego pitch/roll and real
  leaning structures cancel in the difference (they appear in both images).

FoV-boundary guard (the trap vision caught): the per-camera curved black/content boundary in
an L1 ERP is NOT a 3D line's great circle; Canny fires on it. We erode the content mask and
drop edge pixels near the mask boundary so L1's correct FoV arcs do not inflate GCSR.
"""
from __future__ import annotations

import argparse
import json
import os

import cv2
import numpy as np

# ----------------------------------------------------------------------------- ERP geometry
def back_project(u, v, H, W):
    """ERP pixel (u col, v row) -> unit ray in ego frame, exact repo convention."""
    theta = np.pi - (np.asarray(u, np.float64) + 0.5) / W * (2.0 * np.pi)
    phi = (np.pi / 2.0) - (np.asarray(v, np.float64) + 0.5) / H * np.pi
    cph = np.cos(phi)
    return np.stack([cph * np.cos(theta), cph * np.sin(theta), np.sin(phi)], axis=-1)


def _fit_great_circle(D, iters=3):
    """Robust TLS plane-through-origin fit. Returns (normal n, inlier mask, e_rms in rad)."""
    w = np.ones(len(D))
    n = np.array([0.0, 0.0, 1.0])
    for _ in range(iters):
        Dw = D * w[:, None]
        M = Dw.T @ D
        evals, evecs = np.linalg.eigh(M)  # ascending
        n = evecs[:, 0]
        r = np.abs(D @ n)  # |sin(angular dist to plane)| ~ angular residual
        med = np.median(r)
        mad = np.median(np.abs(r - med)) + 1e-12
        c = max(1.5 * mad * 1.4826, 1e-4)  # Tukey scale; floor avoids collapse on perfect lines
        ww = np.where(r < c, (1.0 - (r / c) ** 2) ** 2, 0.0)
        if ww.sum() < 3:
            break
        w = ww
    inl = w > 0
    res = np.arcsin(np.clip(np.abs(D[inl] @ n), 0, 1)) if inl.any() else np.arcsin(np.clip(np.abs(D @ n), 0, 1))
    e_rms = float(np.sqrt(np.mean(res ** 2)))
    return n, inl, e_rms


# ----------------------------------------------------------------------------- I/O
def load_erp(path, H=1024, W=2048):
    """Load an ERP image, resize to canonical HxW (2:1), return (rgb_uint8, content_mask_bool)."""
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(path)
    bgr = cv2.resize(bgr, (W, H), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    content = gray > 8  # non-black (the FoV band)
    return rgb, content


# ----------------------------------------------------------------------------- GCSR (primary)
def gcsr(rgb, content_mask, *, boundary_margin=10, canny=(50, 150), min_pix=40,
         polar_frac=0.45, min_arc_rad=0.15, curve_cap_rad=0.05, return_chains=False):
    H, W = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # valid region = eroded content (kill FoV-boundary arcs + near-black ringing)
    cm = content_mask.astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * boundary_margin + 1,) * 2)
    valid = cv2.erode(cm, k) > 0

    edges = cv2.Canny(gray, canny[0], canny[1])
    edges = (edges > 0) & valid
    edges_u8 = edges.astype(np.uint8)

    num, labels = cv2.connectedComponents(edges_u8, connectivity=8)
    vlim = polar_frac * H
    chains = []
    for lbl in range(1, num):
        ys, xs = np.where(labels == lbl)
        if len(xs) < min_pix:
            continue
        keep = np.abs(ys - H / 2.0) <= vlim  # polar guard
        xs, ys = xs[keep], ys[keep]
        if len(xs) < min_pix:
            continue
        D = back_project(xs, ys, H, W)
        n, inl, e_rms = _fit_great_circle(D)
        # endpoints via dominant in-plane spread (largest-eigval direction)
        M = D.T @ D
        ev, evec = np.linalg.eigh(M)
        a1 = evec[:, 2]
        t = D @ a1
        i0, i1 = int(np.argmin(t)), int(np.argmax(t))
        d0, d1 = D[i0], D[i1]
        L_rad = float(np.arccos(np.clip(d0 @ d1, -1, 1)))
        if L_rad < min_arc_rad:
            continue
        n_ep = np.cross(d0, d1)
        nn = np.linalg.norm(n_ep)
        if nn < 1e-9:
            continue
        n_ep = n_ep / nn
        e_sag = float(np.max(np.abs(np.arcsin(np.clip(D @ n_ep, -1, 1)))))
        chains.append(dict(lbl=int(lbl), npix=int(len(xs)), L_rad=L_rad,
                           e_rms=e_rms, e_sag=e_sag, xs=xs, ys=ys,
                           curved=bool(e_rms > curve_cap_rad)))

    if not chains:
        out = dict(GCSR_sag_mrad=float("nan"), GCSR_rms_mrad=float("nan"),
                   n_chains=0, total_arc_rad=0.0, scoreable=False)
        return (out, []) if return_chains else out

    L = np.array([c["L_rad"] for c in chains])
    sag = np.array([c["e_sag"] for c in chains])
    rms = np.array([c["e_rms"] for c in chains])
    total_arc = float(L.sum())
    # length-weighted median sagitta (robust to occasional genuinely-curved objects)
    order = np.argsort(sag)
    cw = np.cumsum(L[order])
    med_sag = float(sag[order][np.searchsorted(cw, cw[-1] / 2.0)])
    gcsr_rms = float(np.sum(L * rms) / L.sum())
    p90_sag = float(np.percentile(sag, 90))
    out = dict(
        GCSR_sag_mrad=med_sag * 1e3,
        GCSR_sag_p90_mrad=p90_sag * 1e3,
        GCSR_rms_mrad=gcsr_rms * 1e3,
        n_chains=len(chains),
        total_arc_rad=total_arc,
        frac_curved=float(np.mean([c["curved"] for c in chains])),
        scoreable=bool(len(chains) >= 30 and total_arc >= 6.0),
    )
    return (out, chains) if return_chains else out


# ----------------------------------------------------------------------------- VLS (primary, no-ref)
def vertical_lean(rgb, content_mask, *, boundary_margin=10, lean_max_deg=25.0,
                  min_len_frac=0.02, polar_frac=0.45, return_segs=False):
    """Vertical-Lean Spread: roll-invariant spread of near-vertical edge tilts.

    A gravity-vertical 3D line is an EXACT vertical ERP column (a meridian), anywhere in the
    image, for any rigid sphere projection. So in clean L1 every near-vertical structure has
    lean ~0 -> tiny spread. A depth-re-rendered 3DGS warp makes buildings lean inconsistently
    -> large spread. A global ego roll tilts ALL verticals by the SAME angle -> it moves the
    MEAN lean but NOT the spread, so spread is roll/pitch-invariant (kills that false positive)
    without needing a reference or image alignment. Per-segment tilt is not laundered by LSD
    fragmentation (a leaning edge's chords all lean the same way).
    """
    H, W = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    nan_out = dict(VLS_spread_mrad=float("nan"), VLS_spread_deg=float("nan"),
                   VLS_p90_dev_deg=float("nan"), VLS_mean_lean_deg=float("nan"),
                   n_vert=0, total_len_px=0.0, scoreable=False)
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(gray)[0]
    if lines is None:
        return (nan_out, []) if return_segs else nan_out
    lines = lines.reshape(-1, 4)
    Lmin = min_len_frac * H
    vlim = polar_frac * H
    segs = []
    for x1, y1, x2, y2 in lines:
        if y1 > y2:  # order top (smaller y) first
            x1, y1, x2, y2 = x2, y2, x1, y1
        dx, dy = x2 - x1, y2 - y1  # dy >= 0
        L = float(np.hypot(dx, dy))
        if L < Lmin:
            continue
        lean = float(np.degrees(np.arctan2(dx, dy)))  # 0 = vertical column; signed lean
        if abs(lean) > lean_max_deg:
            continue
        if abs((y1 + y2) / 2.0 - H / 2.0) > vlim:  # polar guard
            continue
        # FoV guard: keep only segments whose midpoint is in real content (not the black band);
        # near-vertical filter already excludes the horizontal FoV-boundary arcs.
        mx, my = int(np.clip((x1 + x2) / 2, 0, W - 1)), int(np.clip((y1 + y2) / 2, 0, H - 1))
        if not content_mask[my, mx]:
            continue
        segs.append(dict(lean=lean, L=L, x1=x1, y1=y1, x2=x2, y2=y2))
    if len(segs) < 8:
        out = dict(nan_out); out["n_vert"] = len(segs)
        return (out, segs) if return_segs else out
    leans = np.array([s["lean"] for s in segs])
    Ls = np.array([s["L"] for s in segs])
    wmean = float(np.sum(leans * Ls) / Ls.sum())  # global-roll proxy
    spread = float(np.sqrt(np.sum(Ls * (leans - wmean) ** 2) / Ls.sum()))  # roll-invariant
    p90 = float(np.percentile(np.abs(leans - wmean), 90))
    deg2mrad = np.pi / 180.0 * 1e3
    out = dict(
        VLS_spread_mrad=spread * deg2mrad,
        VLS_spread_deg=spread,
        VLS_p90_dev_deg=p90,
        VLS_mean_lean_deg=wmean,  # global roll diagnostic (NOT a warp signal)
        n_vert=len(segs),
        total_len_px=float(Ls.sum()),
        scoreable=bool(len(segs) >= 8),
    )
    for s in segs:
        s["dev"] = abs(s["lean"] - wmean)
    return (out, segs) if return_segs else out


# ----------------------------------------------------------------------------- VDR (relative)
def _subpix_col(gray_dx_abs, x0, y, win):
    W = gray_dx_abs.shape[1]
    lo, hi = int(max(0, x0 - win)), int(min(W, x0 + win + 1))
    seg = gray_dx_abs[int(y), lo:hi]
    s = seg.sum()
    if s < 1e-6:
        return None
    xs = np.arange(lo, hi)
    return float((xs * seg).sum() / s)


def vdr(test_rgb, l1_rgb, *, theta_max_deg=8.0, min_len_frac=0.10,
        win_l1=6, win_tf=15, min_matched=8):
    H, W = l1_rgb.shape[:2]
    l1_gray = cv2.cvtColor(l1_rgb, cv2.COLOR_RGB2GRAY)
    tf_gray = cv2.cvtColor(test_rgb, cv2.COLOR_RGB2GRAY)
    l1_dx = np.abs(cv2.Sobel(l1_gray, cv2.CV_64F, 1, 0, ksize=3))
    tf_dx = np.abs(cv2.Sobel(tf_gray, cv2.CV_64F, 1, 0, ksize=3))
    tf_content = tf_gray > 8

    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(l1_gray)[0]
    if lines is None:
        return dict(VDR_mrad=float("nan"), n_matched=0, scoreable=False)
    lines = lines.reshape(-1, 4)
    Lmin = min_len_frac * H
    tan_max = np.tan(np.radians(theta_max_deg))
    col2mrad = (2 * np.pi / W) * 1e3
    veq = H / 2.0 - 0.5

    deltas, l1_devs, tf_devs, matched = [], [], [], []
    for x1, y1, x2, y2 in lines:
        dx, dy = x2 - x1, y2 - y1
        if abs(dy) < Lmin:
            continue
        if abs(dx) > tan_max * abs(dy):  # not near-vertical
            continue
        ya, yb = sorted([y1, y2])
        rows = np.arange(int(ya), int(yb) + 1)
        if len(rows) < Lmin:
            continue
        # L1 polyline (subpixel)
        l1x, tfx, vr = [], [], []
        for y in rows:
            xc = x1 + (x2 - x1) * (y - y1) / (dy if dy != 0 else 1e-9)
            xl = _subpix_col(l1_dx, xc, y, win_l1)
            if xl is None:
                continue
            if not tf_content[int(y), int(np.clip(xl, 0, W - 1))]:
                continue  # no 3DGS content under this L1 vertical -> can't transfer
            xt = _subpix_col(tf_dx, xl, y, win_tf)
            if xt is None:
                continue
            l1x.append(xl); tfx.append(xt); vr.append(y)
        if len(vr) < max(min_matched, 0.5 * len(rows)):
            continue
        l1x = np.array(l1x); tfx = np.array(tfx); vr = np.array(vr, float)
        # OLS fit x = a + b(y-veq), DEV = RMS(x - a) i.e. deviation from a perfect column
        def dev(xarr):
            b, a = np.polyfit(vr - veq, xarr, 1)
            return float(np.sqrt(np.mean((xarr - a) ** 2)))
        d_l1, d_tf = dev(l1x), dev(tfx)
        l1_devs.append(d_l1); tf_devs.append(d_tf)
        deltas.append((d_tf - d_l1) * col2mrad)
        matched.append((l1x.mean(), vr.mean()))
    n = len(deltas)
    if n < min_matched:
        return dict(VDR_mrad=float("nan"), n_matched=n, scoreable=False)
    return dict(
        VDR_mrad=float(np.mean(deltas)),
        VDR_median_mrad=float(np.median(deltas)),
        DEV_L1_mrad=float(np.mean(l1_devs) * col2mrad),
        DEV_test_mrad=float(np.mean(tf_devs) * col2mrad),
        n_matched=n,
        scoreable=bool(n >= min_matched),
    )


# ----------------------------------------------------------------------------- relative warp (E1/E2 judge)
def relative_warp(l1_rgb, test_rgb, *, content_mask=None, warp_px=2.0, polar_frac=0.45):
    """Dense-flow displacement of `test` vs the L1 anchor (cv2 DIS), the RIGHT ruler for E1/E2.

    For seam-confined fusion the output is aligned to L1 by construction (far field byte-identical,
    only ~7 seam strips edited), so this needs NO rotation discount: far-field frac_warp must be ~0
    and all motion confined to the strips. Validated to fire on a synthetic low-frequency warp.
    Returns median/p90 displacement in equator-equivalent px, far-field warp-fraction, coverage.
    """
    H, W = l1_rgb.shape[:2]
    a = cv2.cvtColor(l1_rgb, cv2.COLOR_RGB2GRAY)
    tb = test_rgb if test_rgb.shape[:2] == (H, W) else cv2.resize(test_rgb, (W, H), interpolation=cv2.INTER_AREA)
    b = cv2.cvtColor(tb, cv2.COLOR_RGB2GRAY)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    dis.setUseSpatialPropagation(True)
    flow = dis.calc(a, b, None)  # (H,W,2) -> (du, dv)
    du, dv = flow[..., 0], flow[..., 1]
    v = np.arange(H)[:, None].astype(np.float64)
    phi = np.pi / 2.0 - (v + 0.5) / H * np.pi
    disp = np.sqrt((np.cos(phi) * du) ** 2 + dv ** 2)  # equator-equivalent px
    mask = (np.abs(v - H / 2.0) <= polar_frac * H) & np.ones((H, W), bool)
    if content_mask is not None:
        mask = mask & content_mask
    vals = disp[mask]
    if vals.size == 0:
        return dict(M_med_px=float("nan"), M_p90_px=float("nan"), frac_warp=float("nan"), frac_valid=0.0)
    return dict(
        M_med_px=float(np.median(vals)),
        M_p90_px=float(np.percentile(vals, 90)),
        frac_warp=float(np.mean(vals > warp_px)),
        frac_valid=float(mask.mean()),
    )


# ----------------------------------------------------------------------------- diagnostic viz
def draw_segs(rgb, segs, dev_lo=0.0, dev_hi=5.0):
    """Overlay near-vertical edges colored by |lean - mean_lean| in deg: green=aligned -> red=off."""
    vis = rgb.copy()
    for s in segs:
        f = np.clip((s.get("dev", 0.0) - dev_lo) / (dev_hi - dev_lo), 0, 1)
        color = (int(255 * f), int(255 * (1 - f)), 40)  # RGB: green->red
        cv2.line(vis, (int(s["x1"]), int(s["y1"])), (int(s["x2"]), int(s["y2"])), color, 2, cv2.LINE_AA)
    return vis


def stack_labeled(images, labels):
    out = []
    for im, lab in zip(images, labels):
        im = im.copy()
        cv2.rectangle(im, (0, 0), (im.shape[1], 40), (0, 0, 0), -1)
        cv2.putText(im, lab, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
        out.append(im)
    return np.vstack(out)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--l1", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--outdir", default="deliverables/erp_geometry_metric")
    ap.add_argument("--tag", default="bmw")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    l1_rgb, l1_cm = load_erp(args.l1)
    tf_rgb, tf_cm = load_erp(args.test)

    # PRIMARY: roll-invariant vertical-lean spread (absolute, no-ref, alignment-free)
    l1_vls, l1_segs = vertical_lean(l1_rgb, l1_cm, return_segs=True)
    tf_vls, tf_segs = vertical_lean(tf_rgb, tf_cm, return_segs=True)
    vls_ratio = (tf_vls["VLS_spread_mrad"] / l1_vls["VLS_spread_mrad"]
                 if l1_vls.get("VLS_spread_mrad") and not np.isnan(l1_vls["VLS_spread_mrad"]) else float("nan"))
    # SECONDARY (experimental, no-ref great-circle): kept for the record, known weak on merged components
    l1_gcsr = gcsr(l1_rgb, l1_cm)
    tf_gcsr = gcsr(tf_rgb, tf_cm)

    report = dict(
        tag=args.tag,
        PRIMARY_VLS=dict(L1=l1_vls, TEST_3DGS=tf_vls, VLS_spread_ratio=vls_ratio),
        secondary_GCSR=dict(L1=l1_gcsr, TEST_3DGS=tf_gcsr),
    )
    print(json.dumps(report, indent=2))
    with open(os.path.join(args.outdir, f"{args.tag}_metrics.json"), "w") as f:
        json.dump(report, f, indent=2)

    vis = stack_labeled(
        [draw_segs(l1_rgb, l1_segs), draw_segs(tf_rgb, tf_segs)],
        [f"L1 hard_select  VLS_spread={l1_vls['VLS_spread_deg']:.2f}deg  n_vert={l1_vls['n_vert']}  "
         f"(mean_lean={l1_vls['VLS_mean_lean_deg']:+.2f}deg)",
         f"3DGS re-render  VLS_spread={tf_vls['VLS_spread_deg']:.2f}deg  n_vert={tf_vls['n_vert']}  "
         f"(mean_lean={tf_vls['VLS_mean_lean_deg']:+.2f}deg)  ratio={vls_ratio:.1f}x"],
    )
    out_png = os.path.join(args.outdir, f"{args.tag}_vls_diagnostic.png")
    cv2.imwrite(out_png, cv2.cvtColor(vis, cv2.COLOR_RGB2BGR))
    print("wrote", out_png)


if __name__ == "__main__":
    main()
