"""DB-11 / A1 (CORRECTED 2026-06-02) — Google-Street-View ⊕ Meta-Surround360 ⊕ our-LiDAR.

This is the RE-DO after the /code-review retracted the first A1 (it was BUGGY + UNFAITHFUL:
the old `dis_flow_align` had a meshgrid coord SWAP (#1), no spatial propagation (#5), the
cv2.detail seam mask left BLACK holes (#7), and — the real sin — it warped each slab TOWARD
the L1 reference instead of doing Surround360's novel-view synthesis. None of those are here.

Direction kept exactly as the user asked — "Google map view + Meta 360 + our own method":

  GOOGLE  : coarse LiDAR-plane DIBR reproject (ground + a few facade planes) pre-aligns the 7
            non-co-located cameras into one ERP so the residual disparity the flow has to fix
            is small (Street View's step-1 coarse mesh; OUR plane comes from real LiDAR).
  META    : Surround360 OPTICAL-FLOW NOVEL-VIEW SYNTHESIS in each seam band — for every output
            ray at fractional position `shift = w_j/(w_i+w_j)` between cam i (shift=0) and cam j
            (shift=1), warp i forward by `shift·flow_ij` and j backward by `(1-shift)·flow_ji`
            and blend (1-shift):shift. A near object that L1 DOUBLES is warped to ONE shared
            virtual-center position before averaging → singled, not blended-ghosted, not cut.
  OURS    : (a) the plane pre-align uses real LiDAR; (b) FB-consistency gating — where the flow
            is unreliable (textureless / occlusion, the E3 starvation failure mode) we ABSTAIN
            and keep L1 (no black tears, no hallucinated warp); (c) the whole edit is CONFINED
            to the seam bands and COMPOSITED onto L1 (far field BYTE-IDENTICAL to L1); (d)
            non-planar near objects (LiDAR off all planes — the BMW) are kept as L1 single-cam.

Modes:
  --mode view   (default) : Surround360 view-interp (the real test).
  --mode core             : plane-DIBR + seam-confined low-freq multiband (reviewed code), no
                            flow — the "modest L1+" ablation, NO cv2.detail black-hole path.
  --prealign plane|none   : view-interp flow input = LiDAR-plane-DIBR slabs (default) or L1
                            rotation-only slabs (pure-Surround360 ablation: does LiDAR help?).

CPU-only math (cv2 DIS flow + numpy); plane fit via pyransac3d (open-source).
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select, compute_hdr_gains, apply_hdr  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.blending.seam_confined import _label_and_base, _seam_alpha  # noqa: E402 (reviewed)
from waymo2panorama.blending.seam_confined import multiband_lowfreq_blend, blend_seam_confined  # noqa: E402 (reviewed)
from parallax_budget_map import _erp_rays  # noqa: E402
from run_a0_plane_dibr_probe import load_lidar_feather  # noqa: E402 (reviewed feather reader)
from erp_geometry_metric import relative_warp  # noqa: E402 (validated far-field ruler)

BMW_UUID = "02a00399-3857-444e-8db3-a8f58489c394"
FAR = 1000.0


# ---------------- plane fitting (open-source pyransac3d) ----------------
def fit_planes_p3(pts, seed=0):
    try:
        import pyransac3d as p3
    except ImportError:  # self-bootstrap on a bare Colab (review: pyransac3d not in repo deps)
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pyransac3d"], check=False)
        import pyransac3d as p3
    z = pts[:, 2]
    rng = np.linalg.norm(pts[:, :2], axis=1)
    inrange = (rng > 3.0) & (rng < 50.0)
    z_lo = np.percentile(z, 35)
    # ground
    gsel = inrange & (z < z_lo + 0.3)
    ground = None
    if int(gsel.sum()) > 200:
        eq, inl = p3.Plane().fit(pts[gsel], thresh=0.15, maxIteration=500)
        n = np.array(eq[:3], float); nn = np.linalg.norm(n)
        if nn > 1e-6:
            n /= nn; d = -eq[3] / nn  # plane n·X = d
            if n[2] < 0:               # canonicalize: ground normal points UP, so hh=pts@n-d>0 == ABOVE ground
                n, d = -n, -d          # (pyransac3d returns arbitrary sign; tall-object masks rely on this)
            if abs(n[2]) > 0.85:
                resid = float(np.abs(pts[gsel] @ n - d)[inl].std()) if len(inl) else 0.0
                ground = {"n": n, "d": float(d), "n_inl": int(len(inl)), "resid_m": resid}
    # facades per azimuth sector
    if ground is not None:
        gd = np.abs(pts @ ground["n"] - ground["d"])
        nong = inrange & (gd > 0.5)
    else:
        nong = inrange & (z > z_lo + 0.8)
    th = np.arctan2(pts[:, 1], pts[:, 0])
    facades, n_sect = [], 12
    for s in range(n_sect):
        lo = -np.pi + s * (2 * np.pi / n_sect); hi = lo + 2 * np.pi / n_sect
        sm = nong & (th >= lo) & (th < hi)
        if int(sm.sum()) < 400:
            continue
        sub = pts[sm]
        eq, inl = p3.Plane().fit(sub, thresh=0.20, maxIteration=300)
        if len(inl) < 300:
            continue
        n = np.array(eq[:3], float); nn = np.linalg.norm(n)
        if nn < 1e-6 or abs(n[2] / nn) > 0.45:
            continue
        n /= nn; d = -eq[3] / nn
        thi = np.arctan2(sub[inl][:, 1], sub[inl][:, 0])
        facades.append({"n": n, "d": float(d), "n_inl": int(len(inl)),
                        "resid_m": float(np.abs(sub @ n - d)[inl].std()),
                        "theta_lo": float(thi.min()), "theta_hi": float(thi.max())})
    return ground, facades


def build_plane_convergence(ground, facades, erp_hw):
    H, W = erp_hw
    rays = _erp_rays(erp_hw).reshape(-1, 3)
    theta = np.arctan2(rays[:, 1], rays[:, 0])
    lam = np.full(rays.shape[0], FAR)

    def apply(n, d, sel=None):
        nr = rays @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            l = d / nr
        valid = np.isfinite(l) & (l > 0.5) & (l < 80.0) & (np.abs(nr) > 1e-3)
        if sel is not None:
            valid &= sel
        upd = valid & (l < lam); lam[upd] = l[upd]

    if ground is not None:
        apply(ground["n"], ground["d"])
    for f in facades:
        apply(f["n"], f["d"], (theta >= f["theta_lo"] - 0.05) & (theta <= f["theta_hi"] + 0.05))
    return lam.reshape(H, W)


def object_coherent_weights(l1_w, obj_mask, min_area=150, max_area=9000):
    """Google-style seam ROUTING around compact near objects. L1 hard_select cuts the seam at the
    cos²-weight crossover, which can slice straight through a car/pole that straddles two cameras —
    that cut IS the 'frame parallax' (the object split between two viewpoints). Here, for each
    COMPACT off-plane object component we assign the WHOLE object to its single best-viewing camera
    (zero the other cams' weight inside it where the best cam is valid), so the seam routes around
    it and the object stays whole + single. LARGE components (facades/buildings) are left untouched
    (routing a whole facade to one cam would drop coverage). General: every AV scene has cars/poles.
    Returns (modified weights, n_routed)."""
    K = len(l1_w)
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(obj_mask.astype(np.uint8), connectivity=8)
    w_mod = [w.copy() for w in l1_w]
    routed = 0
    for k in range(1, n):
        area = int(stats[k, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        comp = lbl == k
        sums = [float(l1_w[m][comp].sum()) for m in range(K)]
        c = int(np.argmax(sums))
        cvalid = l1_w[c] > 1e-6
        sel = comp & cvalid
        if int(sel.sum()) < min_area // 2:
            continue  # best cam doesn't actually cover most of the object → don't force it
        for m in range(K):
            if m != c:
                w_mod[m][sel] = 0.0
        routed += 1
    return w_mod, routed


def off_plane_object_erp(pts, ground, facades, erp_hw, off_thresh=0.6, dilate_px=9):
    """ERP mask of NON-PLANAR near objects: LiDAR points far from ALL fitted planes,
    within range, projected to ERP. These get kept as L1 single-camera (seam routed around)."""
    H, W = erp_hw
    planes = ([ground] if ground else []) + facades
    if not planes:
        return np.zeros((H, W), bool)
    rng = np.linalg.norm(pts, axis=1)
    near = (rng > 1.0) & (rng < 40.0)
    P = pts[near]
    dmin = np.full(len(P), 1e9)
    for pl in planes:
        dmin = np.minimum(dmin, np.abs(P @ pl["n"] - pl["d"]))
    obj = P[dmin > off_thresh]
    if len(obj) == 0:
        return np.zeros((H, W), bool)
    # project to ERP (ego origin spherical) — matches sphere_projection: theta = pi - (u+.5)/W*2pi
    x, y, z = obj[:, 0], obj[:, 1], obj[:, 2]
    th = np.arctan2(y, x); ph = np.arctan2(z, np.sqrt(x * x + y * y))
    u = ((np.pi - th) / (2 * np.pi) * W).astype(int) % W
    v = ((np.pi / 2 - ph) / np.pi * H).astype(int).clip(0, H - 1)
    m = np.zeros((H, W), np.uint8); m[v, u] = 1
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1, dilate_px * 2 + 1))
    return cv2.dilate(m, k).astype(bool)


# ---------------- Meta Surround360 optical-flow novel-view synthesis ----------------
def _make_dis():
    """DIS optical flow with spatial propagation + mean normalization ON (FIX #5: without
    setUseSpatialPropagation the flow leaves black holes where samples fall outside)."""
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    try:
        dis.setUseSpatialPropagation(True)
        dis.setUseMeanNormalization(True)
        dis.setVariationalRefinementIterations(8)
    except Exception:
        pass
    return dis


def _fb_consistency(flow_fwd, flow_bwd, thresh=2.0):
    """Forward-backward consistency: a pixel's flow is reliable iff
    |flow_fwd(x) + flow_bwd(x + flow_fwd(x))| < thresh px. Unreliable = occlusion / textureless
    garbage → caller keeps L1 there (this is the E3-starvation safety valve)."""
    H, W = flow_fwd.shape[:2]
    # FIX #1: correct meshgrid ORDER — xx are COLUMN indices, yy are ROW indices.
    xx, yy = np.meshgrid(np.arange(W), np.arange(H))
    mx = (xx + flow_fwd[..., 0]).astype(np.float32)
    my = (yy + flow_fwd[..., 1]).astype(np.float32)
    bwd_at = cv2.remap(flow_bwd, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    err = flow_fwd + bwd_at
    mag = np.sqrt((err[..., 0] ** 2 + err[..., 1] ** 2))
    return mag < float(thresh)


def surround360_view_interp(slab_i, slab_j, wi, wj, dis, max_disp=60.0, fb_thresh=2.0,
                            flow_sigma=1.5, struct_thresh=0.0, return_debug=False):
    """Synthesize the virtual-center novel view between two overlapping ERP slabs.

    shift = wj/(wi+wj) ∈ [0,1] is the angular fraction of the output ray between cam i (shift=0,
    its optical axis) and cam j (shift=1). For output column u:
        warp_i(u) = slab_i(u − shift·flow_ij(u))       # move i's content toward the virtual centre
        warp_j(u) = slab_j(u − (1−shift)·flow_ji(u))   # move j's content toward it
        novel(u)  = (1−shift)·warp_i(u) + shift·warp_j(u)
    A doubled near object (at u_i in i, u_j in j) is warped to the SAME u* = u_i+shift·(u_j−u_i)
    in both before averaging → it appears ONCE (singled), not as two ghosts.

    Returns (novel float32 HxWx3, reliable bool HxW). `reliable` is overlap ∧ both-have-content
    ∧ FB-consistent — the caller composites novel onto L1 only where reliable.
    """
    H, W = slab_i.shape[:2]
    overlap = (wi > 1e-6) & (wj > 1e-6)
    novel = np.zeros((H, W, 3), np.float32)
    reliable = np.zeros((H, W), bool)
    if int(overlap.sum()) < 100:
        return novel, reliable
    gi = cv2.cvtColor(np.clip(slab_i, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gj = cv2.cvtColor(np.clip(slab_j, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    # CRITICAL (Surround360-faithful): compute flow on the OVERLAP STRIP only. Adjacent ring cams
    # overlap in just a ~18.6deg wedge; feeding DIS the full mostly-disjoint slabs makes it match
    # content ~300px apart and that garbage flow propagates into the band (median in-band |flow|
    # was 300px instead of the true ~3px parallax). Masking both grayscales to the (dilated)
    # overlap → outside the wedge both are 0 → DIS sees black==black → 0 flow → only true parallax
    # remains inside. This single fix takes FB-consistency from ~0% to 55-84% per seam.
    kov = cv2.dilate(overlap.astype(np.uint8),
                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    gi = np.where(kov, gi, 0).astype(np.uint8)
    gj = np.where(kov, gj, 0).astype(np.uint8)
    flow_ij = dis.calc(gi, gj, None)  # i -> j  (where each i pixel lands in j)
    flow_ji = dis.calc(gj, gi, None)  # j -> i
    # regularize + clip the flow (Surround360 smooths the flow; clip stops a wild vector warping
    # content across the band). Do this BEFORE the FB check so consistency reflects the flow we
    # actually warp with (review: cons was being measured on the pre-smooth flow).
    if flow_sigma > 0:
        for fl in (flow_ij, flow_ji):
            fl[..., 0] = cv2.GaussianBlur(fl[..., 0], (0, 0), flow_sigma)
            fl[..., 1] = cv2.GaussianBlur(fl[..., 1], (0, 0), flow_sigma)
    np.clip(flow_ij, -max_disp, max_disp, out=flow_ij)
    np.clip(flow_ji, -max_disp, max_disp, out=flow_ji)
    cons = _fb_consistency(flow_ij, flow_ji, fb_thresh)

    shift = np.zeros((H, W), np.float32)
    s = (wi + wj)
    shift[overlap] = (wj[overlap] / np.maximum(s[overlap], 1e-9)).astype(np.float32)

    xx, yy = np.meshgrid(np.arange(W), np.arange(H))  # FIX #1 order: xx=cols, yy=rows
    xx = xx.astype(np.float32); yy = yy.astype(np.float32)
    mix_x = (xx - shift * flow_ij[..., 0]).astype(np.float32)
    mix_y = (yy - shift * flow_ij[..., 1]).astype(np.float32)
    mjx = (xx - (1.0 - shift) * flow_ji[..., 0]).astype(np.float32)
    mjy = (yy - (1.0 - shift) * flow_ji[..., 1]).astype(np.float32)
    warp_i = cv2.remap(slab_i.astype(np.float32), mix_x, mix_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    warp_j = cv2.remap(slab_j.astype(np.float32), mjx, mjy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    a = shift[..., None]
    novel = warp_i * (1.0 - a) + warp_j * a

    # CONTRACT FIX (review): gate reliability on plane CONTENT AT THE WARPED SAMPLE LOCATION, not at
    # the output pixel. A warp can sample a plane-DIBR interior hole (the A0 lost-ground case) via
    # bilinear interp and pull genuine black in. Remap a coverage channel through the SAME maps with
    # BORDER_CONSTANT=0; require ~full coverage. Any warp that touches a hole (or its bilinear
    # neighborhood) abstains → caller keeps byte-exact L1 there (no darkening, no black tears).
    cov_i = (slab_i.astype(np.float32).sum(2) > 0).astype(np.float32)
    cov_j = (slab_j.astype(np.float32).sum(2) > 0).astype(np.float32)
    warp_ci = cv2.remap(cov_i, mix_x, mix_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    warp_cj = cv2.remap(cov_j, mjx, mjy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    covered = (warp_ci > 0.999) & (warp_cj > 0.999)

    # STRUCTURE-AGREEMENT path (Google/LiDAR plane edge; enabled only when struct_thresh>0, i.e.
    # plane prealign). Where the two slabs' HIGH-FREQUENCY structure already matches, blending is
    # safe and singles the surface WITHOUT needing reliable flow: this covers (a) flat textureless
    # surfaces — no texture to ghost — and (b) plane-DIBR-registered facades. The ghost-prone case
    # (textured + geometrically misaligned) has HIGH struct residual → it is NOT trusted here and
    # must instead pass the flow (cons) gate, else it abstains to L1. High-pass = exposure-robust.
    if struct_thresh > 0:
        hp_i = gi.astype(np.float32) - cv2.GaussianBlur(gi.astype(np.float32), (0, 0), 8.0)
        hp_j = gj.astype(np.float32) - cv2.GaussianBlur(gj.astype(np.float32), (0, 0), 8.0)
        struct_resid = cv2.GaussianBlur(np.abs(hp_i - hp_j), (0, 0), 3.0)
        struct_agree = struct_resid < float(struct_thresh)
        trust = cons | struct_agree
    else:
        trust = cons
    reliable = overlap & covered & trust
    if return_debug:
        fmag = np.sqrt((flow_ij[..., 0] ** 2 + flow_ij[..., 1] ** 2))
        return novel, reliable, {"overlap": overlap, "covered": covered, "cons": cons,
                                 "trust": trust, "fmag": fmag}
    return novel, reliable


def _circular_center_col(overlap, W):
    """Circular-mean column of the overlap (handles the back seam that wraps across u=0/W)."""
    cols = np.flatnonzero(overlap.any(axis=0))
    if cols.size == 0:
        return None
    ang = cols.astype(np.float64) / W * 2 * np.pi
    cx = np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())
    return int(round((cx / (2 * np.pi)) * W)) % W


def view_interp_panorama(content_slabs, l1_slabs, l1_w, obj_mask, base, band_half_width,
                         max_disp, fb_thresh, protect_obj=False, struct_thresh=0.0):
    """Run Surround360 view-interp on every ring seam and composite onto the L1 base.

    content_slabs : the slabs whose CONTENT feeds the flow + warp (plane-DIBR if --prealign plane,
                    else the L1 rotation-only slabs). l1_w defines the seam geometry (shift/band).
    Far field and FB-unreliable pixels stay byte-exact L1.

    protect_obj=False (default for VIEW): do NOT force non-planar objects to L1. Surround360's
    view-interp is *designed* to carry objects smoothly across the seam (it warps both views to a
    shared virtual centre, not a hard cut), so the FB-consistency gate is the right safety valve —
    forcing objects to L1 would forbid the method from touching exactly the textured doubling
    surfaces (buildings / the BMW) it exists to fix. Set True only for the conservative ablation.
    """
    H, W = base.shape[:2]
    dis = _make_dis()
    out = base.astype(np.float32).copy()
    touched = np.zeros((H, W), np.float32)
    n_pairs = 0
    for (i, j) in RING_PAIRS:
        overlap = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(overlap.sum()) < 200:
            continue
        cc = _circular_center_col(overlap, W)
        roll = (W // 2 - cc) if cc is not None else 0
        si = np.roll(content_slabs[i], roll, axis=1)
        sj = np.roll(content_slabs[j], roll, axis=1)
        wir = np.roll(l1_w[i], roll, axis=1)
        wjr = np.roll(l1_w[j], roll, axis=1)
        band, signed = build_voronoi_seam_band(wir.astype(np.float32), wjr.astype(np.float32),
                                               band_half_width=band_half_width, threshold=1e-6)
        if int(band.sum()) < 100:
            continue
        novel, reliable = surround360_view_interp(si, sj, wir, wjr, dis,
                                                  max_disp=max_disp, fb_thresh=fb_thresh,
                                                  struct_thresh=struct_thresh)
        # cos feather: 1 on the seam → 0 at band_half_width
        d = np.clip(np.abs(signed) / float(band_half_width), 0.0, 1.0)
        feather = np.where(band, 0.5 * (1.0 + np.cos(np.pi * d)), 0.0).astype(np.float32)
        eff_r = feather * band.astype(np.float32) * reliable.astype(np.float32)
        # roll back to the canonical frame
        novel_o = np.roll(novel, -roll, axis=1)
        eff = np.roll(eff_r, -roll, axis=1)
        if protect_obj:
            eff = eff * (~obj_mask).astype(np.float32)  # conservative ablation only
        a3 = eff[..., None]
        out = out * (1.0 - a3) + novel_o * a3
        touched = np.maximum(touched, eff)
        n_pairs += 1
    out = np.clip(out, 0, 255).astype(np.uint8)
    # GUARANTEE byte-exact L1 where untouched (far field + objects + unreliable flow)
    far = touched <= 0.0
    out[far] = base[far]
    return out, touched, n_pairs


def core_plane_blend(plane_slabs, l1_slabs, l1_w, obj_mask, base, band_half_width):
    """--mode core: plane-DIBR slabs (empty pixels filled by L1) → seam-confined low-freq
    multiband (reviewed `multiband_lowfreq_blend`), composited onto L1. NO cv2.detail / no flow
    (drops the #7 black-hole path entirely). The 'modest L1+' ablation."""
    argmax, valid, _ = _label_and_base(l1_slabs, l1_w)
    alpha, _ = _seam_alpha(argmax, valid, band_half_width)
    filled = []
    for ps, ls in zip(plane_slabs, l1_slabs):
        m = (ps.astype(np.float32).sum(2) > 0)[..., None]
        filled.append(np.where(m, ps.astype(np.float32), ls.astype(np.float32)))
    mb = multiband_lowfreq_blend(filled, l1_w, lowfreq_cutoff=4)
    a = (alpha * (~obj_mask).astype(np.float32))[..., None]
    out = base.astype(np.float32) * (1.0 - a) + mb.astype(np.float32) * a
    out = np.clip(out, 0, 255).astype(np.uint8)
    untouched = a[..., 0] <= 0.0
    out[untouched] = base[untouched]
    return out, (a[..., 0])


# ---------------- SINGLE-SOURCE seam alignment (Surround360/Google paradigm) ----------------
# Research (Surround360 NovelView, Jump §5.5, Google Seamless SV, Zhang&Liu CVPR'14, SEAGULL):
# GHOST comes EXCLUSIVELY from mixing two sources at one pixel. The production paradigm is
# warp-to-ALIGN → hard-select (single source) → blend only LOW-FREQ colour across the thin seam.
# So here we WARP the losing slab toward its neighbour INSIDE the band, then hard_select (never
# average), then E1.5 low-freq colour. This cannot create the translucent overlap of view-interp.
def _align_cur_to_prev(cur, prev, w_cur, w_prev, dis, band_hw=80, max_disp=40.0,
                       fb_thresh=2.0, flow_sigma=1.5):
    """Resample slab `cur` to AGREE with `prev` inside their shared seam band (DIS flow, overlap-
    masked, FB-gated, tapered to 0 at the band edge). `cur` is only warped, NEVER mixed with `prev`
    → single-source. Outside band / FB-fail / far field → `cur` unchanged (taper=0 → identity)."""
    H, W = cur.shape[:2]
    overlap = (w_cur > 1e-6) & (w_prev > 1e-6)
    if int(overlap.sum()) < 200:
        return cur
    cc = _circular_center_col(overlap, W); roll = (W // 2 - cc) if cc is not None else 0
    cr = np.roll(cur, roll, 1); pr = np.roll(prev, roll, 1)
    wc = np.roll(w_cur, roll, 1); wp = np.roll(w_prev, roll, 1)
    ov = (wc > 1e-6) & (wp > 1e-6)
    band, signed = build_voronoi_seam_band(wc.astype(np.float32), wp.astype(np.float32),
                                           band_half_width=band_hw, threshold=1e-6)
    if int(band.sum()) < 100:
        return cur
    gc = cv2.cvtColor(np.clip(cr, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gp = cv2.cvtColor(np.clip(pr, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    kov = cv2.dilate(ov.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    gc = np.where(kov, gc, 0).astype(np.uint8); gp = np.where(kov, gp, 0).astype(np.uint8)
    f_pc = dis.calc(gp, gc, None)  # prev -> cur : where prev's pixel lands in cur
    f_cp = dis.calc(gc, gp, None)  # cur -> prev (for FB consistency)
    if flow_sigma > 0:
        for fl in (f_pc, f_cp):
            fl[..., 0] = cv2.GaussianBlur(fl[..., 0], (0, 0), flow_sigma)
            fl[..., 1] = cv2.GaussianBlur(fl[..., 1], (0, 0), flow_sigma)
    np.clip(f_pc, -max_disp, max_disp, out=f_pc); np.clip(f_cp, -max_disp, max_disp, out=f_cp)
    cons = _fb_consistency(f_pc, f_cp, fb_thresh)
    # FEATHER the FB mask (review fix): multiplying a HARD boolean into the displacement field makes
    # the resampling map STEP by |flow| across a cons-island border → a visible TEAR at near-object
    # silhouettes (where FB fails and flow is large). Blurring cons → the displacement ramps smoothly
    # to 0 instead of jumping. Still single-source + band-tapered, so the leak is benign.
    cons_soft = cv2.GaussianBlur(cons.astype(np.float32), (0, 0), 3.0)
    d = np.clip(np.abs(signed) / float(band_hw), 0.0, 1.0)
    ramp = np.where(band, 0.5 * (1.0 + np.cos(np.pi * d)), 0.0).astype(np.float32)  # 1@seam→0@edge
    taper = ramp * cons_soft  # warp only in-band AND smoothly faded where flow is FB-inconsistent
    xx, yy = np.meshgrid(np.arange(W), np.arange(H))
    mapx = (xx.astype(np.float32) + f_pc[..., 0] * taper).astype(np.float32)
    mapy = (yy.astype(np.float32) + f_pc[..., 1] * taper).astype(np.float32)
    warped = cv2.remap(cr.astype(np.float32), mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    cov = (cr.astype(np.float32).sum(2) > 0).astype(np.float32)
    wcov = cv2.remap(cov, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    out = np.where((wcov > 0.5)[..., None], warped, cr.astype(np.float32))  # no warp-into-hole black
    return np.roll(out, -roll, axis=1)


def graphcut_label(slabs, weights, scale=0.45):
    """SINGLE-SOURCE seam ROUTING via cv2.detail GraphCutSeamFinder (Google/Zhang&Liu paradigm):
    route the cut through pixels where the two cameras AGREE (low colour+gradient cost) and AROUND
    disagreeing near objects, instead of the geometry-only cos²-argmax bisector. Returns a per-pixel
    camera LABEL; the caller hard_selects by it (still ONE camera per pixel → no ghost/wash). Runs
    the finder at reduced scale for speed, then assigns at FULL res with an argmax-weight COVERAGE
    FILL so there are NO uncovered/black pixels (the old #7 bug)."""
    K = len(slabs); H, W = slabs[0].shape[:2]
    hs, ws = max(8, int(H * scale)), max(8, int(W * scale))
    imgs_s = [cv2.resize(np.clip(s, 0, 255).astype(np.float32), (ws, hs), interpolation=cv2.INTER_AREA) for s in slabs]
    masks_s = [cv2.resize((w > 1e-6).astype(np.uint8) * 255, (ws, hs), interpolation=cv2.INTER_NEAREST) for w in weights]
    corners = [(0, 0)] * K
    try:
        sf = cv2.detail_GraphCutSeamFinder("COST_COLOR_GRAD")
        seam_s = sf.find(imgs_s, corners, [m.copy() for m in masks_s])
        seam_s = [np.asarray(m.get() if hasattr(m, "get") else m) for m in seam_s]
    except Exception as e:
        print(f"[graphcut] finder failed ({e}); falling back to argmax label", flush=True)
        seam_s = None
    wstack = np.stack([w.astype(np.float32) for w in weights], axis=0)
    argmax_w = wstack.argmax(axis=0)
    anyvalid = (wstack.max(axis=0) > 1e-6)
    if seam_s is None:
        label = argmax_w
    else:
        valid = [(w > 1e-6) for w in weights]
        seam_full = [cv2.resize(ms, (W, H), interpolation=cv2.INTER_NEAREST) for ms in seam_s]
        sel = np.stack([(seam_full[k] > 0) & valid[k] for k in range(K)], axis=0)  # (K,H,W)
        wsel = np.where(sel, wstack, -1.0)
        label = wsel.argmax(axis=0)              # among seam-selected cams, the highest-weight one
        nosel = (~sel.any(axis=0)) & anyvalid    # GraphCut didn't cover (rounding) → argmax-weight fill
        label[nosel] = argmax_w[nosel]
    label[~anyvalid] = 0
    rgb = np.stack([s.astype(np.float32) for s in slabs], axis=0)
    res = np.take_along_axis(rgb, label[None, ..., None], axis=0)[0]
    res[~anyvalid] = 0
    return res.astype(np.uint8), label


def flow_align_chain(slabs, weights, band_hw=80, max_disp=40.0, fb_thresh=2.0):
    """Chain-warp each ring slab to AGREE with its anchor-side neighbour in the seam band; caller
    hard_selects (single source). front_center=anchor; CCW 0→1→2→3, CW 0→6→5→4, back seam 3-4.
    Like of_chain_warp but: DIS flow overlap-masked (the 300px fix), CORRECT warp direction (fixes
    the legacy #2 sign bug), FB-gated, band-confined+tapered → far field byte-exact, single-source."""
    dis = _make_dis()
    warped = [s.astype(np.float32).copy() for s in slabs]
    for chain in ([0, 1, 2, 3], [0, 6, 5, 4]):
        for k in range(1, len(chain)):
            prev, cur = chain[k - 1], chain[k]
            warped[cur] = _align_cur_to_prev(warped[cur], warped[prev], weights[cur], weights[prev],
                                             dis, band_hw=band_hw, max_disp=max_disp, fb_thresh=fb_thresh)
    warped[4] = _align_cur_to_prev(warped[4], warped[3], weights[4], weights[3], dis,
                                   band_hw=band_hw, max_disp=max_disp, fb_thresh=fb_thresh)
    return warped


# ---------------- viz helpers ----------------
def _rw(img, w):
    h = round(img.shape[0] * w / img.shape[1])
    return cv2.resize(np.clip(img, 0, 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_AREA)


def _label(img, txt):
    band = np.zeros((30, img.shape[1], 3), np.uint8)
    cv2.putText(band, txt, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return np.vstack([band, img])


def _save(path, rgb, q=92):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, q])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"))
    ap.add_argument("--uuid", default=BMW_UUID)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--out-dir", type=Path, default=Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline"))
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--mode", choices=["align", "view", "core"], default="align",
                    help="align = warp-to-align + HARD-SELECT + low-freq colour (single-source, no "
                         "blend ghost — recommended); view = view-interp alpha-blend (ghosts); core")
    ap.add_argument("--prealign", choices=["plane", "ground", "none"], default="ground",
                    help="view-interp flow input pre-align: 'ground' = LiDAR GROUND plane only "
                         "(connects the near-road seam, no facade distortion — recommended); "
                         "'plane' = ground+facade planes (distorts facades); 'none' = L1 rot-only")
    ap.add_argument("--max-disp", type=float, default=60.0)
    ap.add_argument("--fb-thresh", type=float, default=2.0)
    ap.add_argument("--struct-thresh", type=float, default=15.0,
                    help="(plane/ground prealign) high-freq structure-residual threshold for the "
                         "alignment-trust path; 0 disables (pure flow). Higher = trust more flat/"
                         "aligned regions (safe in ground mode: facades stay L1 → fires only where "
                         "structure already agrees). 15 ~ near-road fired 71%; 25 ~ 82%.")
    ap.add_argument("--protect-obj", action="store_true",
                    help="(view mode, ablation) force non-planar objects to L1 instead of letting "
                         "view-interp carry them across the seam")
    ap.add_argument("--with-photo", action="store_true",
                    help="(view mode) use the proven E1.5 low-freq photometric seam blend as the "
                         "base, so the band is photometrically clean even where view-interp abstains "
                         "(textureless wall); view-interp adds the geometric singling on top")
    ap.add_argument("--obj-route", action="store_true",
                    help="(view/align) route the L1 seam AROUND compact near objects (cars/poles) so "
                         "they come from one camera and aren't sliced — fixes object frame-parallax")
    ap.add_argument("--color", choices=["none", "gain", "lowfreq"], default="none",
                    help="(align) seam colour: none (byte-exact, AV2 exposure-matched), gain (global "
                         "per-cam exposure match, no spatial wash), lowfreq (OLD E1.5, white-spot bug)")
    ap.add_argument("--seam", choices=["argmax", "graphcut"], default="argmax",
                    help="(align) seam routing: argmax (cos² bisector) or graphcut (route the cut "
                         "through agreeing regions / around objects — single-source, invisible seam)")
    ap.add_argument("--review-w", type=int, default=1300)
    args = ap.parse_args()
    erp_hw = (args.erp_h, args.erp_w); out = args.out_dir; out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    loader = AV2RingLoader(args.av2_root / args.uuid)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[args.anchor])
    pts, _, dms = load_lidar_feather(args.av2_root / args.uuid, ts[args.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    print(f"[load] {args.uuid} a{args.anchor:03d}  lidar {pts.shape[0]} pts", flush=True)

    ground, facades = fit_planes_p3(pts)
    print(f"[planes] ground={'y' if ground else 'N'} facades={len(facades)}", flush=True)
    conv = build_plane_convergence(ground, facades, erp_hw)          # ground+facades (for obj/crops)
    obj_mask = off_plane_object_erp(pts, ground, facades, erp_hw)
    print(f"[obj] non-planar near-object ERP frac={obj_mask.mean()*100:.2f}%", flush=True)

    # convergence map used to PRE-ALIGN the view-interp content:
    #   ground = GROUND plane only → connects the near-road seam (largest-parallax, low-texture →
    #            flow drifts → FB rejects) WITHOUT the facade distortion the full-plane mode causes.
    #   plane  = ground + facades.   none = no pre-align (rotation-only content).
    if args.prealign == "ground":
        conv_pre = build_plane_convergence(ground, [], erp_hw) if ground else None
    elif args.prealign == "plane":
        conv_pre = conv
    else:
        conv_pre = None

    # render L1 (rotation-only) always; pre-aligned slabs only if a pre-align mode is set
    l1_slabs, l1_w, pl_slabs = [], [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, _a1, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                           convergence_distance_m=None)
        l1_slabs.append(r1); l1_w.append(w1)
        if conv_pre is not None:
            rp, _ap, _wp = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                                convergence_distance_m=conv_pre)
            pl_slabs.append(rp)
        else:
            pl_slabs.append(r1)
    L1 = hard_select(l1_slabs, l1_w)
    # base weights = L1, optionally with the seam ROUTED around compact near objects (fixes the
    # car frame-parallax: object comes from one camera, not sliced across the seam).
    w_base = l1_w
    if args.obj_route and args.mode in ("view", "align"):
        w_base, n_routed = object_coherent_weights(l1_w, obj_mask)
        print(f"[obj-route] routed {n_routed} compact near-objects to a single camera", flush=True)
    _, _, base = _label_and_base(l1_slabs, w_base)  # (object-coherent) hard_select base (uint8)

    tag = f"A1_{args.mode}"
    if args.mode == "view":
        tag += f"_{args.prealign}"
        if args.obj_route:
            tag += "_route"
        if args.prealign in ("plane", "ground"):
            # pre-aligned slabs with interior holes (A0 lost-ground) FILLED by L1 so the warped-
            # coverage gate doesn't reject the band. Where the chosen plane(s) have content it
            # pre-aligns the two cams geometrically via LiDAR → residual flow ~0 → fires even where
            # flow alone drifts (the near road). Elsewhere = L1. 'ground' aligns ONLY the road
            # (well-fit, planar → no facade distortion); 'plane' also aligns facades (distorts them).
            content = []
            for ps, ls in zip(pl_slabs, l1_slabs):
                m = (ps.astype(np.float32).sum(2) > 0)[..., None]
                content.append(np.where(m, ps.astype(np.float32), ls.astype(np.float32)))
        else:
            content = l1_slabs
        # struct-trust path is safe with a pre-align (flat/aligned regions only); off for pure flow.
        st = args.struct_thresh if args.prealign in ("plane", "ground") else 0.0
        view_base = base
        if args.with_photo:
            # E1.5 low-freq photometric seam blend (reviewed, far-field byte-exact) as the base, so
            # the band is colour-clean even where view-interp abstains; geometric singling adds on top.
            view_base = blend_seam_confined(l1_slabs, w_base, band_half_width=args.band_hw,
                                            lowfreq_cutoff=5)["out"]
            tag += "_photo"
        res, touched, n_pairs = view_interp_panorama(
            content, l1_slabs, l1_w, obj_mask, view_base,
            band_half_width=args.band_hw, max_disp=args.max_disp, fb_thresh=args.fb_thresh,
            protect_obj=args.protect_obj, struct_thresh=st)
        edit_frac = float((touched > 0).mean())
        print(f"[view] pairs={n_pairs} edited_frac={edit_frac*100:.2f}% "
              f"reliable_mean_alpha={float(touched[touched>0].mean()) if (touched>0).any() else 0:.3f}", flush=True)
    elif args.mode == "align":
        # ★ SINGLE-SOURCE (research-grounded): warp each slab to AGREE with its neighbour in the band
        # (flow, FB-gated, tapered) → hard_select (NEVER average → cannot ghost). Colour handling:
        #   none    = no colour correction (AV2 is exposure-matched → minor step; far field byte-exact).
        #   gain    = GLOBAL per-camera exposure gain (compute_hdr_gains/apply_hdr) — matches exposure
        #             with NO spatial wash (fixes the white-spot the wide multiband low-freq caused).
        #   lowfreq = the OLD E1.5 wide multiband (DEPRECATED — white-spot wash on near-black walls).
        warped = flow_align_chain(l1_slabs, l1_w, band_hw=args.band_hw, max_disp=args.max_disp,
                                  fb_thresh=args.fb_thresh)
        if args.obj_route:
            tag += "_route"
        tag += f"_{args.seam}_{args.color}"
        # colour: global exposure gain (no spatial wash) applied to the warped slabs first
        slabs_c = warped
        if args.color == "gain":
            gains = compute_hdr_gains([w.astype(np.float32) for w in warped], w_base)
            slabs_c = apply_hdr(warped, gains)
        # seam: graph-cut single-source routing (around objects / through agreeing) OR cos²-argmax
        if args.seam == "graphcut":
            res, _ = graphcut_label(slabs_c, w_base, scale=0.45)
        elif args.color == "lowfreq":  # argmax + OLD wide multiband (white-spot; kept as ablation)
            res = blend_seam_confined(warped, w_base, band_half_width=args.band_hw, lowfreq_cutoff=5)["out"]
        else:                          # argmax single-source
            res = hard_select(slabs_c, w_base)
        touched = (np.abs(res.astype(np.int16) - L1.astype(np.int16)).sum(2) > 3).astype(np.float32)
        edit_frac = float((touched > 0).mean())
        print(f"[align] seam={args.seam} color={args.color} obj_route={args.obj_route} "
              f"edited_frac={edit_frac*100:.2f}%", flush=True)
    else:
        res, touched = core_plane_blend(pl_slabs, l1_slabs, l1_w, obj_mask, base,
                                        band_half_width=args.band_hw)
        edit_frac = float((touched > 0).mean())
        print(f"[core] edited_frac={edit_frac*100:.2f}%", flush=True)

    # far-field fidelity vs L1 (must be ~0 outside the bands by construction)
    try:
        rw = relative_warp(L1, res)
        rw = {k: (float(v) if np.isscalar(v) or isinstance(v, (int, float)) else v)
              for k, v in rw.items()} if isinstance(rw, dict) else {"val": float(rw)}
    except Exception as e:
        rw = {"error": str(e)}

    # outputs: stacked review L1 vs result; crops at the 2 worst-parallax seams
    _save(out / f"{tag}_L1_vs_result.jpg",
          np.vstack([_label(_rw(L1, args.review_w), "L1 hard_select"),
                     _label(_rw(res, args.review_w), f"{tag}")]))
    cand = []
    for i, j in RING_PAIRS:
        b, _ = build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32),
                                       band_half_width=args.band_hw, threshold=1e-6)
        bm = b & (conv < 80.0)
        if int(bm.sum()) < 200:
            continue
        cc = _circular_center_col(bm, args.erp_w)  # wrap-aware (review: plain median mis-centers the back seam)
        if cc is None:
            cc = int(np.median(np.where(bm)[1]))
        cand.append((int(bm.sum()), cc, (i, j)))
    cand.sort(reverse=True)
    crop_cols = []
    cw = args.erp_w // 2
    for npx, cc, (i, j) in cand[:2]:
        roll = cw - cc  # roll the seam (incl. the wrapping back seam) to centre → contiguous crop
        L1r = np.roll(L1, roll, axis=1); resr = np.roll(res, roll, axis=1)
        c0, c1 = cw - 230, cw + 230
        crop_cols.append(np.vstack([_label(L1r[300:760, c0:c1], f"L1 seam{i}-{j}"),
                                    _label(resr[300:760, c0:c1], f"{tag} seam{i}-{j}")]))
    if crop_cols:
        h = max(c.shape[0] for c in crop_cols)
        crop_cols = [np.vstack([c, np.zeros((h - c.shape[0], c.shape[1], 3), np.uint8)])
                     if c.shape[0] < h else c for c in crop_cols]
        _save(out / f"{tag}_seam_crops.jpg", np.hstack(crop_cols), q=95)
    # edited-region viz
    ov = L1.copy()
    em = touched > 0
    ov[em] = (0.5 * ov[em] + np.array([0, 255, 255]) * 0.5).astype(np.uint8)
    ov[obj_mask] = (0.5 * ov[obj_mask] + np.array([255, 0, 255]) * 0.5).astype(np.uint8)
    _save(out / f"{tag}_editmask.jpg", _rw(ov, args.review_w))

    diag = {"tag": tag, "mode": args.mode, "prealign": args.prealign, "uuid": args.uuid,
            "anchor": args.anchor, "lidar_pts": int(pts.shape[0]), "ground": bool(ground),
            "n_facades": len(facades), "obj_frac": float(obj_mask.mean()),
            "edited_frac": edit_frac, "band_hw": args.band_hw, "max_disp": args.max_disp,
            "fb_thresh": args.fb_thresh, "far_field_relative_warp_vs_L1": rw,
            "runtime_s": round(time.time() - t0, 1)}
    with open(out / f"{tag}_diag.json", "w") as f:
        json.dump(diag, f, indent=2, default=str)
    print("[diag]", json.dumps(diag, indent=2, default=str), flush=True)
    print(f"[saved] {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
