"""★ codex round-4 lever: OBJECT-MOAT min-cut seam routing (the missing CPU piece = seam TOPOLOGY).
The doubling that survives single-source is the cut running THROUGH a near object (-> one hard broken
copy). Fix: route ONE hard seam per ring pair through the agreeing FAR corridor, with near objects as
near-infinite-cost MOATS so the whole object comes from ONE camera (never split). Custom cut cost
(codex): high on cross-view RGB residual (after align+gain), LiDAR-near range, off-plane object mask,
and align-flow magnitude; low on sky/far-facade/far-road. Vectorized DP vertical min-seam per overlap,
single-source compose (ZERO blending) on the flow-aligned slabs. Source-faithful, no generation.

Run (CPU): python _seamroute.py --uuid <UUID> --tag <name>  ->  results/seamroute/SR_<tag>_*
Outputs L1 | align(argmax seam) | moat-seam-route, full pano + LOSSLESS crops. Vision-judge: are near
objects taken WHOLE from one camera (no split/step) where argmax cut through them?"""
from __future__ import annotations
import sys, time, argparse
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import run_a1_streetview_pipeline as a1

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/seamroute"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0
SPOTS = {"bmw": {"curb": (1085, 540, 730, 210), "bmw": (1760, 320, 670, 250), "graycar": (720, 310, 620, 250), "wall": (1150, 320, 640, 250)}}
DEFAULT_SPOTS = {"a": (720, 310, 620, 250), "b": (1400, 310, 620, 250), "c": (300, 310, 620, 250)}


def lab(im, t, h=30):
    b = np.zeros((h, im.shape[1], 3), np.uint8); cv2.putText(b, t, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return np.vstack([b, im])


def rw(im, w):
    return cv2.resize(np.clip(im, 0, 255).astype(np.uint8), (w, round(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)


def dp_seam(cost):
    """Vectorized DP min vertical seam over a (h,w) cost; returns seam col per row."""
    h, w = cost.shape
    M = cost.astype(np.float64).copy(); back = np.zeros((h, w), np.int32)
    INF = 1e18
    for r in range(1, h):
        prev = M[r - 1]
        left = np.empty(w); left[0] = INF; left[1:] = prev[:-1]
        right = np.empty(w); right[-1] = INF; right[:-1] = prev[1:]
        ch = np.stack([left, prev, right], 0)  # (3,w): -1,0,+1
        k = ch.argmin(0)
        M[r] += ch[k, np.arange(w)]
        back[r] = np.arange(w) + (k - 1)
    seam = np.zeros(h, np.int32); seam[h - 1] = int(np.argmin(M[h - 1]))
    for r in range(h - 1, 0, -1):
        seam[r - 1] = back[r, seam[r]]
    return seam


def virtual_center_select(si, sj, wi, wj, dis, max_disp=60.0, fb_thresh=2.0, flow_sigma=1.5):
    """codex r6: Surround360 virtual-centre view-interp WITHOUT the blend-ghost. Warp BOTH cams toward
    the virtual centre (i by shift·flow_ij, j by (1-shift)·flow_ji, shift=wj/(wi+wj)), then HARD-SELECT
    the higher-weight camera's WARP per pixel (single source -> no ghost). Both are warped to the same
    centre, so the seam is viewpoint-CONTINUOUS (kills the step that warp-to-winner leaves). reliable =
    overlap & FB-consistent & selected-warp-covered; caller composites onto the base only where reliable
    (textured co-visible surfaces), else keeps the base (textureless/occluded abstain to L1)."""
    H, W = si.shape[:2]
    overlap = (wi > 1e-6) & (wj > 1e-6)
    novel = np.zeros((H, W, 3), np.float32); reliable = np.zeros((H, W), bool)
    if int(overlap.sum()) < 100:
        return novel, reliable
    gi = cv2.cvtColor(np.clip(si, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gj = cv2.cvtColor(np.clip(sj, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    kov = cv2.dilate(overlap.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    gi = np.where(kov, gi, 0).astype(np.uint8); gj = np.where(kov, gj, 0).astype(np.uint8)
    flow_ij = dis.calc(gi, gj, None); flow_ji = dis.calc(gj, gi, None)
    if flow_sigma > 0:
        for fl in (flow_ij, flow_ji):
            fl[..., 0] = cv2.GaussianBlur(fl[..., 0], (0, 0), flow_sigma); fl[..., 1] = cv2.GaussianBlur(fl[..., 1], (0, 0), flow_sigma)
    np.clip(flow_ij, -max_disp, max_disp, out=flow_ij); np.clip(flow_ji, -max_disp, max_disp, out=flow_ji)
    cons = a1._fb_consistency(flow_ij, flow_ji, fb_thresh)
    shift = np.zeros((H, W), np.float32); ssum = (wi + wj)
    shift[overlap] = (wj[overlap] / np.maximum(ssum[overlap], 1e-9)).astype(np.float32)
    xx, yy = np.meshgrid(np.arange(W), np.arange(H)); xx = xx.astype(np.float32); yy = yy.astype(np.float32)
    mix = (xx - shift * flow_ij[..., 0]).astype(np.float32); miy = (yy - shift * flow_ij[..., 1]).astype(np.float32)
    mjx = (xx - (1.0 - shift) * flow_ji[..., 0]).astype(np.float32); mjy = (yy - (1.0 - shift) * flow_ji[..., 1]).astype(np.float32)
    warp_i = cv2.remap(si.astype(np.float32), mix, miy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    warp_j = cv2.remap(sj.astype(np.float32), mjx, mjy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    cov_i = cv2.remap((si.astype(np.float32).sum(2) > 0).astype(np.float32), mix, miy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    cov_j = cv2.remap((sj.astype(np.float32).sum(2) > 0).astype(np.float32), mjx, mjy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    sel_j = shift > 0.5                         # higher-weight cam's warp (single source)
    novel = np.where(sel_j[..., None], warp_j, warp_i)
    cov_sel = np.where(sel_j, cov_j, cov_i)
    reliable = overlap & cons & (cov_sel > 0.999)
    return novel, reliable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID); ap.add_argument("--tag", default="bmw")
    ap.add_argument("--anchor", type=int, default=0); ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--max-disp", type=float, default=60.0); ap.add_argument("--fb-thresh", type=float, default=2.0)
    ap.add_argument("--near-m", type=float, default=18.0, help="range below this = near -> moat")
    ap.add_argument("--line-w", type=float, default=10.0, help="DB-15: cost weight for cutting along ground lines (lane markings/curb)")
    a = ap.parse_args(); t0 = time.time(); erp_hw = (H, W)

    loader = a1.AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[a.anchor])
    pts, _l, dms = a1.load_lidar_feather(ROOT / a.uuid, ts[a.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts); obj_mask = a1.off_plane_object_erp(pts, ground, facades, erp_hw)
    cams = {cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}
    l1_slabs, l1_w = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        r1, _a, w1 = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        l1_slabs.append(r1); l1_w.append(w1)
    L1 = a1.hard_select(l1_slabs, l1_w)
    w_base, nr = a1.object_coherent_weights(l1_w, obj_mask)

    # flow-aligned + gain slabs (single-source content), and the argmax-seam align baseline
    warped = a1.flow_align_chain(l1_slabs, l1_w, band_hw=a.band_hw, max_disp=a.max_disp, fb_thresh=a.fb_thresh)
    try: warped = a1.apply_hdr(warped, a1.compute_hdr_gains([w.astype(np.float32) for w in warped], w_base))
    except Exception: pass
    align = a1.hard_select(warped, w_base)
    # view_none (the user's reference — Surround360 blend, ghosts) for head-to-head comparison
    view, _vt, _vn = a1.view_interp_panorama(l1_slabs, l1_slabs, l1_w, obj_mask, L1, band_half_width=a.band_hw,
                                             max_disp=a.max_disp, fb_thresh=a.fb_thresh, protect_obj=False, struct_thresh=0.0)

    # per-pixel LiDAR range map (nearest), for the near-moat
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]; th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    ul = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W); vl = ((np.pi / 2 - ph) / np.pi * H - 0.5); rng = np.linalg.norm(pts, axis=1)
    ui = np.round(ul).astype(int) % W; vi = np.round(vl).astype(int); ok = (vi >= 0) & (vi < H) & (rng > 1.0) & (rng < 80.0)
    ui, vi, rng = ui[ok], vi[ok], rng[ok]; rangemap = np.full((H, W), np.inf, np.float32)
    for k in range(len(ui)):
        if rng[k] < rangemap[vi[k], ui[k]]: rangemap[vi[k], ui[k]] = rng[k]
    # densify the near-mask: dilate near LiDAR returns so a sparse hit still moats its object region
    near_pts = np.zeros((H, W), np.uint8); near_pts[(np.isfinite(rangemap)) & (rangemap < a.near_m)] = 1
    near_mask = cv2.dilate(near_pts, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))).astype(bool)
    objm = cv2.dilate(obj_mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))).astype(bool)
    # TALL mask = LiDAR points >0.5m above the ground plane (cars/poles/walls) — excluded from the
    # ground-road reproject; the low curb (~0.15m) stays IN so it gets aligned, not moated out.
    if ground:
        hh = pts @ np.asarray(ground["n"]) - ground["d"]
        rr = np.linalg.norm(pts, axis=1)
        tp = pts[(hh > 0.5) & (rr < 45.0)]
        if len(tp):
            tx, ty, tz = tp[:, 0], tp[:, 1], tp[:, 2]
            tth = np.arctan2(ty, tx); tph = np.arctan2(tz, np.hypot(tx, ty))
            tu = ((np.pi - tth) / (2 * np.pi) * W).astype(int) % W; tv = ((np.pi / 2 - tph) / np.pi * H).astype(int).clip(0, H - 1)
            tm = np.zeros((H, W), np.uint8); tm[tv, tu] = 1
            tall_mask = cv2.dilate(tm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))).astype(bool)
        else:
            tall_mask = objm
    else:
        tall_mask = objm

    # codex r5: NEAR-GROUND no-cut band = near surface below the horizon (curb/sidewalk/road). The seam
    # must NOT cut through it (grazing angle -> depth-uncorrectable). Force it to one camera per pair.
    rows = np.arange(H)[:, None] * np.ones((1, W))
    ng_global = (rows > H * 0.52) & (~tall_mask)   # below-horizon ground (road/sidewalk/curb), not tall

    # DB-20260603-15: ground high-gradient map (lane markings / curb edges / cracks). Penalize the DP
    # seam from running ALONG these -> route through uniform asphalt + cross lines PERPENDICULARLY ->
    # the parallax offset shows as a tiny jog instead of a long wavy double-line. Pure cut-LOCATION change.
    grayL1 = cv2.cvtColor(L1, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gmag = np.hypot(cv2.Sobel(grayL1, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(grayL1, cv2.CV_32F, 0, 1, ksize=3))
    gmag = gmag / (np.percentile(gmag, 99.5) + 1e-6)                       # robust 0..~1 normalise
    ground_region = (rows > H * 0.50) & (~tall_mask)                      # below-horizon ground, off tall objects
    ground_line = cv2.dilate(np.where(ground_region, np.clip(gmag, 0, 1), 0.0).astype(np.float32),
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

    label = np.stack([w.astype(np.float32) for w in w_base], 0).argmax(0)  # global base label
    routed = 0
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 300: continue
        cc = a1._circular_center_col(ov, W); roll = (W // 2 - cc) if cc is not None else 0
        ovr = np.roll(ov, roll, 1)
        ys, xs = np.where(ovr)
        r0, r1, c0, c1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        si = np.roll(warped[i], roll, 1)[r0:r1, c0:c1]; sj = np.roll(warped[j], roll, 1)[r0:r1, c0:c1]
        ovb = ovr[r0:r1, c0:c1]
        rgb = np.abs(si.astype(np.float32) - sj.astype(np.float32)).mean(2) / 255.0  # 0..1, high = parallax
        moat = (np.roll(near_mask, roll, 1) | np.roll(objm, roll, 1))[r0:r1, c0:c1]
        # cut cost: low on agreeing/far corridor, BIG moat on near objects, BIG outside overlap,
        # + DB-15: penalize cutting along ground lines (lane markings/curb) -> route off them
        gline = np.roll(ground_line, roll, 1)[r0:r1, c0:c1]
        cost = rgb + 60.0 * moat.astype(np.float32) + a.line_w * gline + 1000.0 * (~ovb).astype(np.float32)
        cost = cv2.GaussianBlur(cost, (0, 0), 1.5)
        seam = dp_seam(cost)  # col per local row
        # which camera is on the LEFT of the seam? compare exclusive-region mean cols
        wir = np.roll(l1_w[i], roll, 1)[r0:r1, c0:c1]; wjr = np.roll(l1_w[j], roll, 1)[r0:r1, c0:c1]
        iex = (wir > 1e-6) & (wjr <= 1e-6); jex = (wjr > 1e-6) & (wir <= 1e-6)
        i_mean = np.where(iex.any(), np.where(iex)[1].mean() if iex.any() else 0, 0)
        j_mean = np.where(jex.any(), np.where(jex)[1].mean() if jex.any() else c1 - c0, 0)
        left_cam, right_cam = (i, j) if i_mean <= j_mean else (j, i)
        lab_box = np.full((r1 - r0, c1 - c0), -1, np.int32)
        cols = np.arange(c1 - c0)[None, :]
        leftmask = cols < seam[:, None]
        lab_box[leftmask & ovb] = left_cam
        lab_box[(~leftmask) & ovb] = right_cam
        # NOTE: the codex-r5 near-ground NO-CUT force was REMOVED — forcing the whole below-horizon band
        # to one camera regressed dense-object scenes (2c65: smeared a parked SUV whose dark body the
        # LiDAR height mask missed). Curb stays at the grazing floor; safe > marginal curb gain.
        # write back into the global label only inside this overlap
        lab_full = np.full((H, W), -1, np.int32); lab_full[r0:r1, c0:c1] = lab_box
        lab_full = np.roll(lab_full, -roll, 1)
        m = lab_full >= 0
        label[m] = lab_full[m]; routed += 1

    # single-source compose by the routed label, on flow-aligned+gain slabs (ZERO blending)
    rgb_stack = np.stack([w.astype(np.float32) for w in warped], 0)
    valid = np.stack([w.astype(np.float32) for w in l1_w], 0).max(0) > 0
    routed_pano = np.take_along_axis(rgb_stack, label[None, ..., None], axis=0)[0]
    routed_pano = np.where(valid[..., None], routed_pano, 0).astype(np.uint8)

    # --- ground-PLANE near-road reproject: aligns the flat road/sidewalk across the seam GEOMETRICALLY
    # (no texture needed -> bypasses the flow-abstain that jags the near-ground curb). Same routed
    # camera per pixel (single source, no ghost), just re-rendered at the LiDAR ground-plane depth so
    # both sides of the seam land at the true ground position -> continuous road. Raised curb lip (off
    # plane) is the irreducible residual. Confined to near-ground seam bands, off objects. ---
    bandU = np.zeros((H, W), bool)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        b, _s = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bandU |= b & ov
    # codex r6: VIRTUAL-CENTRE HARD-SELECT composited onto the moat-route base — removes the viewpoint
    # STEP on textured co-visible seams (warp BOTH cams to the virtual centre, then SELECT one; no blend
    # -> no ghost). FB-gated -> textureless/occluded abstain to the base. (Replaces the gated near-ground
    # reproject, which fired ~0.12% = the near-ground is not cross-view-verifiable at grazing angles.)
    dis = a1._make_dis()
    finalf = routed_pano.astype(np.float32).copy()
    fired_vc = np.zeros((H, W), np.float32)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        cc = a1._circular_center_col(ov, W); roll = (W // 2 - cc) if cc is not None else 0
        si = np.roll(l1_slabs[i], roll, 1); sj = np.roll(l1_slabs[j], roll, 1)
        wir = np.roll(l1_w[i], roll, 1); wjr = np.roll(l1_w[j], roll, 1)
        band, signed = a1.build_voronoi_seam_band(wir.astype(np.float32), wjr.astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        if int(band.sum()) < 100: continue
        novel, reliable = virtual_center_select(si, sj, wir, wjr, dis, max_disp=a.max_disp, fb_thresh=a.fb_thresh)
        d = np.clip(np.abs(signed) / a.band_hw, 0, 1); feather = np.where(band, 0.5 * (1 + np.cos(np.pi * d)), 0).astype(np.float32)
        eff = feather * band.astype(np.float32) * reliable.astype(np.float32)
        novel_o = np.roll(novel, -roll, 1); eff_o = np.roll(eff, -roll, 1)
        a3 = eff_o[..., None]
        finalf = finalf * (1 - a3) + novel_o * a3
        fired_vc = np.maximum(fired_vc, eff_o)
    final = np.where(valid[..., None], np.clip(finalf, 0, 255), 0).astype(np.uint8)
    print(f"[{a.tag}] virtual-centre select fired={100*(fired_vc>0).mean():.2f}%", flush=True)
    print(f"[{a.tag}] seams routed={routed} near_mask={100*near_mask.mean():.1f}% obj={100*obj_mask.mean():.2f}% rt={time.time()-t0:.0f}s", flush=True)

    # --- GROUND-PLANE near-road reproject (NEW lever): for the below-horizon ROAD/sidewalk, re-render the
    # SAME routed camera at the LiDAR GROUND-plane depth (IPM). A road point then lands at its TRUE ego
    # position no matter which camera shot it -> lane lines + curb-base stay CONTINUOUS across the seam
    # (pure geometry, no texture -> fixes the low-texture-asphalt kink that flow/vc-select abstain on and
    # leave as rotation-only L1). Ground-ONLY (facades=[] -> no building distortion). The raised curb lip
    # (~0.15m off-plane) is the irreducible residual. Single-source (routed label) -> cannot ghost. ---
    final_ground = final.copy(); road_fired = 0.0
    if ground is not None:
        conv_g = a1.build_plane_convergence(ground, [], erp_hw)        # ground hit-depth below horizon, FAR above
        g_stack = np.stack([a1.render_camera_to_erp(frame.images[cam], cams[cam].K, cams[cam].T_ego_cam,
                            erp_hw=erp_hw, convergence_distance_m=conv_g)[0].astype(np.float32)
                            for cam in a1.RING_CAMS_7], 0)
        ground_pano = np.take_along_axis(g_stack, label[None, ..., None], axis=0)[0]   # same routed cam, ground depth
        # confine to the SEAM BAND (away-from-seam road stays the clean current deliverable) AND to the
        # near-mid depth window 3-35m: far grazing rays (just below horizon, hit ground >35m) are the
        # IPM stretch source -> excluded; the user's circled kinks are on the 3-35m near road.
        bandU_d = cv2.dilate(bandU.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))).astype(bool)
        on_ground = (conv_g > 3.0) & (conv_g < 35.0) & (~tall_mask) & (ground_pano.sum(2) > 0) & valid & bandU_d
        rm = cv2.GaussianBlur(on_ground.astype(np.float32), (0, 0), 2.0)[..., None]     # thin feather at road boundary
        final_ground = np.where(valid[..., None], np.clip(final.astype(np.float32) * (1 - rm) + ground_pano * rm, 0, 255), 0).astype(np.uint8)
        road_fired = 100.0 * on_ground.mean()
    print(f"[{a.tag}] ground-road reproject fired={road_fired:.2f}%", flush=True)

    res = {"L1": L1, "deliverable (current)": final, "+ground-road (NEW)": final_ground}
    cv2.imwrite(str(OUT / f"SR_{a.tag}_compare.jpg"), cv2.cvtColor(np.vstack([lab(rw(v, 1900), k) for k, v in res.items()]), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 93])
    cv2.imwrite(str(OUT / f"SR_{a.tag}_pano.jpg"), cv2.cvtColor(rw(routed_pano, 2048), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(OUT / f"SR_{a.tag}_ground_pano.jpg"), cv2.cvtColor(rw(final_ground, 2048), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    # full-res lossless deliverable = the DiT360 trimap-clamp init (refine the thin seam, far byte-exact)
    cv2.imwrite(str(OUT / f"SR_{a.tag}_final_1024x2048.png"), cv2.cvtColor(final, cv2.COLOR_RGB2BGR))
    # DB-18 (codex r10): ground-RISK seam mask for DiT360 — preserve=white(255), generate=black(0).
    # NOT a thin vertical strip: the wavy defect lives on the NEAR-GROUND, so the seam ribbon WIDENS
    # downward (curb/road), and tall-object interiors are HARD-excluded so DiT never regenerates a car.
    seam_edge = ((label != np.roll(label, 1, 1)) | (label != np.roll(label, -1, 1))) & valid
    ribbon = np.zeros((H, W), np.uint8)
    for lo, hi, rad in [(0, int(H * 0.50), 8), (int(H * 0.50), int(H * 0.72), 26), (int(H * 0.72), H, 46)]:
        b = np.zeros((H, W), np.uint8); b[lo:hi][seam_edge[lo:hi]] = 1
        ribbon |= cv2.dilate(b, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rad * 2 + 1, rad * 2 + 1)))
    risk = ribbon.astype(bool) & (~objm) & valid
    cv2.imwrite(str(OUT / f"SR_{a.tag}_seamcore.png"), np.where(~risk, 255, 0).astype(np.uint8))
    print(f"[{a.tag}] DB18 seamcore risk-mask = {100*risk.mean():.2f}% of pano", flush=True)
    spots = SPOTS.get(a.tag, DEFAULT_SPOTS)
    PAL = np.array([[60, 60, 210], [60, 210, 60], [210, 60, 60], [60, 210, 210], [210, 60, 210], [210, 210, 60], [210, 130, 60]], np.uint8)
    labcol = PAL[np.clip(label, 0, 6)]               # BGR camera-id colour per pixel
    ngc = L1.copy(); ngc[ng_global] = (0.45 * ngc[ng_global] + np.array([0, 255, 0]) * 0.55).astype(np.uint8)
    for name, (u, v0, v1, hw) in spots.items():
        roll = W // 2 - u; cc = W // 2
        row = [lab(cv2.cvtColor(np.roll(v, roll, 1)[v0:v1, cc - hw:cc + hw], cv2.COLOR_RGB2BGR), k) for k, v in res.items()]
        base = cv2.cvtColor(np.roll(L1, roll, 1)[v0:v1, cc - hw:cc + hw], cv2.COLOR_RGB2BGR)
        lc = np.roll(labcol, roll, 1)[v0:v1, cc - hw:cc + hw]
        row.append(lab((0.45 * base + 0.55 * lc).astype(np.uint8), "cam-id"))
        row.append(lab(cv2.cvtColor(np.roll(ngc, roll, 1)[v0:v1, cc - hw:cc + hw], cv2.COLOR_RGB2BGR), "near-ground=green"))
        cv2.imwrite(str(OUT / f"SR_{a.tag}_{name}.png"), np.hstack(row))
    print(f"[saved] {OUT}/SR_{a.tag}_{{compare.jpg, pano.jpg, {list(spots)}}}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
