"""DB-20260602-13 OVERFIT kill-test — per-scene OPTIMIZE a dense inverse-depth for ONE seam
strip (the BMW seam = cams ring_side_right[i] / ring_rear_right[j]) via cross-view photometric
consistency + edge-aware smoothness + LiDAR anchor, then reproject the agreeing colour to the
TRUE ego-centre. Decisive question: does a REGULARIZED OPTIMIZED depth SINGLE the near BMW
cleanly vs L1's doubled-at-cut, or does it still smear/double?

WHY: 5 vision-judged angles proved in-band doubling is depth-accuracy-bound. The LiDAR
evidence-pack showed straddle=0 (both rotation-only copies sit ~20px on the SAME side of the
true ego-centre azimuth), so the only faithful op is reproject-BOTH-to-true via ACCURATE dense
depth. The 24-plane raw-RGB argmax plane-sweep was confident <1%. This asks if a continuous,
smooth, LiDAR-anchored, subpixel OPTIMIZED depth does better.

METHOD (torch autograd on A100):
 1. L1 baseline (rotation-only) + Voronoi seam band (band_half_width=90) for pair (5,4).
 2. Per-ERP-pixel inv_d (requires_grad) over the band, warm-started from a coarse plane-sweep
    argmax (raw-RGB), clamped to [1/80, 1/2] m^-1.
 3. Forward: depth=1/inv_d, P_ego=depth*d_ego, project into cam_i and cam_j (R_cam_ego,
    t_ego_cam, K pinhole), bilinearly sample BOTH raw camera images -> c_i, c_j (torch
    grid_sample, fully differentiable in inv_d). Losses:
       (a) PHOTOMETRIC: robust |c_i-c_j| (Charbonnier) + a gradient/census-ish term, only where
           BOTH project in-bounds-in-front.
       (b) EDGE-AWARE SMOOTHNESS on inv_d, weighted by exp(-|grad image|).
       (c) LiDAR ANCHOR: where LiDAR projects into the band, inv_d ~= 1/lidar_range (Huber).
    Adam ~600 iters.
 4. RENDER ego-centre: at optimized depth the agreeing colour 0.5*(c_i+c_j) = de-doubled band;
    composite onto L1 (band only; far field = L1). Where final photometric residual stays HIGH
    (occlusion / unresolved) -> fall back to L1 (do NOT smear).
 5. SAVE comparison crop at the BMW (u~1760, v 360-600, +/-180px):
    L1 | plane-sweep-argmax | optimized-depth-reproject -> Drive results/killtest/DB13_overfit_bmw.jpg
    Print: final photometric residual median, % band converged below a confident threshold,
    optimized-depth agreement with LiDAR (median |1/inv_d - lidar_range|).

=== VERDICT (2026-06-02, A100, BMW seam ring_side_right/ring_rear_right) ===
MIXED, leaning POS-for-LiDAR-depth / NEG-for-photometric-optimization.

WHAT SINGLES THE BMW: accurate DENSE depth, not the photometric optimizer.
 - Pure dense-LiDAR-depth reproject (densify sparse 11%-of-band LiDAR -> per-pixel depth,
   reproject BOTH cams to ego-centre, average the agreeing colour) SINGLES the BMW cleanly:
   BMW-region cross-view residual 6.82/255, 77.6% of both-valid px below the conf threshold,
   at the correct ~20.9m depth. Visually (panel 3) it is the cleanest of all four — one coherent
   white SUV, continuous façade, single tree. This BEATS plane-sweep argmax (panel 2: shreds the
   car + tears the façade) and is cleaner than L1's seam-cut through the car (panel 1).
 - So the LiDAR evidence-pack conclusion holds: with an ACCURATE per-pixel depth, reproject-both-
   to-true is faithful and de-doubles the near object. The doubling really was depth-accuracy-bound.

WHY THE OPTIMIZER IS A NEG: the free cross-view PHOTOMETRIC objective has a degenerate far-depth
 attractor. As depth->infinity the two cameras' rays converge (parallax->0) so |c_i-c_j|->0 for
 ANY content -> the global photometric minimum is "send every pixel to the far clamp", regardless
 of true depth. With Adam it did exactly that: 92.8% of both-valid band pixels pinned at the 80m
 clamp (prior says ~20m), |opt_depth - lidar| = 3.2m only because the LiDAR-anchored 11% held.
 Its headline "resid 5.91 / 94.6% conf" is the trivial zero-parallax minimum, NOT a real singling;
 visually (panel 4) it actually ADDS salt-and-pepper speckle to the façade vs the pure-LiDAR panel.
 Edge-aware smoothness + a sparse/densified LiDAR Huber prior were not enough to overcome the
 collapse; the photometric signal in this seam is unreliable (low-texture façade, occlusion at the
 car edge, non-Lambertian glass/paint).

IS THE LEARNED DENSE-DEPTH ROUTE WORTH SCALING? Qualified YES for the DEPTH, NO for self-supervised
 PHOTOMETRIC depth here. The win comes from having an accurate dense depth (LiDAR-densified), so a
 LEARNED depth net that is strongly LiDAR/metric-SUPERVISED (not purely photometric/self-supervised)
 is the route that could scale — it must inherit the metric depth, not discover it from cross-view
 photo-consistency, which is degenerate at these baselines. A single per-pixel depth still cannot
 represent fg/bg at a true occlusion boundary (the car edge against background): that is where even
 the LiDAR-depth reproject must FALL BACK to L1, and where layered/MPI would be needed. Net: dense
 metric depth singles the near object; the cheapest-falsification photometric-overfit does NOT
 deliver that depth on its own.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import cv2
import numpy as np
import torch
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import hard_select
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band
from run_a0_plane_dibr_probe import load_lidar_feather

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/killtest"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
# BMW seam: ring_side_right (idx 5) = cam_i, ring_rear_right (idx 4) = cam_j
I, J = 5, 4   # RING_CAMS_7 indices; RING_PAIR (5,4) per hard_hdr_of
DEV = "cuda"
DMIN, DMAX = 2.0, 80.0   # metric depth clamp
INVDMIN, INVDMAX = 1.0 / DMAX, 1.0 / DMIN

CROP_U, CROP_V0, CROP_V1, CROP_HALF = 1760, 360, 600, 180


def erp_ray_dirs():
    """Ego unit rays for every ERP pixel, matching render_camera_to_erp exactly."""
    u_idx = np.arange(W, dtype=np.float64)
    v_idx = np.arange(H, dtype=np.float64)
    uu, vv = np.meshgrid(u_idx, v_idx)
    theta = np.pi - (uu + 0.5) / W * (2.0 * np.pi)
    phi = (np.pi / 2.0) - (vv + 0.5) / H * np.pi
    cphi = np.cos(phi)
    d = np.stack([cphi * np.cos(theta), cphi * np.sin(theta), np.sin(phi)], axis=-1)
    return d  # (H,W,3)


def lab(im, t):
    b = np.zeros((24, im.shape[1], 3), np.uint8)
    cv2.putText(b, t, (5, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return np.vstack([b, im])


def project_torch(P_ego, R_cam_ego, t_ego_cam, K, w_img, h_img):
    """P_ego: (...,3) torch. Returns grid_sample normalized coords (...,2) in [-1,1],
    plus a valid mask (in-front & in-bounds)."""
    Pc = torch.einsum("ab,...b->...a", R_cam_ego, P_ego - t_ego_cam)  # cam frame
    z = Pc[..., 2]
    in_front = z > 1e-6
    zs = torch.where(in_front, z, torch.ones_like(z))
    u = K[0, 0] * (Pc[..., 0] / zs) + K[0, 2]
    v = K[1, 1] * (Pc[..., 1] / zs) + K[1, 2]
    margin = 0.5
    inb = (u >= margin) & (u <= w_img - 1 - margin) & (v >= margin) & (v <= h_img - 1 - margin)
    valid = in_front & inb
    # normalize to [-1,1] for grid_sample (align_corners=True)
    gx = u / (w_img - 1) * 2 - 1
    gy = v / (h_img - 1) * 2 - 1
    return gx, gy, valid


def sample_img(img_t, gx, gy):
    """img_t: (1,3,Himg,Wimg). gx,gy: (N,) in [-1,1]. -> (N,3)."""
    grid = torch.stack([gx, gy], dim=-1).view(1, -1, 1, 2)
    out = F.grid_sample(img_t, grid, mode="bilinear", padding_mode="border", align_corners=True)
    return out.view(3, -1).t()  # (N,3)


def main():
    t0 = time.time()
    loader = AV2RingLoader(ROOT / BMW)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[0])
    cams = {cam: frame.calibrations[cam] for cam in RING_CAMS_7}

    # ---- L1 baseline + weights ----
    l1s, l1w = [], []
    for cam in RING_CAMS_7:
        cb = cams[cam]
        r, _a, wt = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        l1s.append(r); l1w.append(wt)
    L1 = hard_select(l1s, l1w)

    ci_name, cj_name = RING_CAMS_7[I], RING_CAMS_7[J]
    ci, cj = cams[ci_name], cams[cj_name]
    band, signed = build_voronoi_seam_band(l1w[I].astype(np.float32), l1w[J].astype(np.float32), band_half_width=90, threshold=1e-6)
    ov = (l1w[I] > 1e-6) & (l1w[J] > 1e-6)
    bm = band & ov
    nb = int(bm.sum())
    print(f"[band] pair=({ci_name},{cj_name}) band&overlap px={nb}", flush=True)

    # ---- 24-plane raw-RGB argmax plane-sweep (the baseline to beat + warm start) ----
    DEPTHS = np.geomspace(2.5, 60.0, 24).astype(np.float64)
    best_cost = np.full((H, W), 1e9, np.float32)
    best_d = np.full((H, W), 15.0, np.float32)
    best_col = np.zeros((H, W, 3), np.float32)
    for d in DEPTHS:
        si, ai, _ = render_camera_to_erp(frame.images[ci_name], ci.K, ci.T_ego_cam, erp_hw=(H, W), convergence_distance_m=float(d))
        sj, aj, _ = render_camera_to_erp(frame.images[cj_name], cj.K, cj.T_ego_cam, erp_hw=(H, W), convergence_distance_m=float(d))
        both = ai & aj & bm
        cost = np.where(both, np.abs(si - sj).mean(2), 1e9).astype(np.float32)
        cost_s = cv2.blur(cost, (5, 5))
        upd = cost_s < best_cost
        best_d = np.where(upd, np.float32(d), best_d)
        best_col = np.where(upd[..., None], 0.5 * (si + sj), best_col)
        best_cost = np.where(upd, cost_s, best_cost)
    # plane-sweep-argmax composite (feathered, conf-gated like _planesweep, for the panel)
    ps_out = L1.astype(np.float32).copy()
    agree = best_cost < 32.0
    distinct = best_cost < 1e8
    conf_ps = (bm & agree & distinct).astype(np.float32)
    dd = np.clip(np.abs(signed) / 90.0, 0, 1)
    ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
    a_ps = cv2.GaussianBlur(conf_ps * ramp, (0, 0), 2.0)[..., None]
    ps_out = np.clip(ps_out * (1 - a_ps) + best_col * a_ps, 0, 255).astype(np.uint8)
    print(f"[planesweep warm] argmax conf(<32) frac of band={100*conf_ps[bm].mean():.1f}%", flush=True)

    # ---- band pixel list ----
    vy, vx = np.where(bm)
    Npx = len(vy)
    d_ego_full = erp_ray_dirs()  # (H,W,3)
    d_ego = torch.tensor(d_ego_full[vy, vx], dtype=torch.float32, device=DEV)  # (N,3)

    # warm-start inv_d from plane-sweep argmax depth (clamped)
    d0 = np.clip(best_d[vy, vx], DMIN, DMAX).astype(np.float32)
    inv_d0 = (1.0 / d0)
    inv_d = torch.tensor(inv_d0, dtype=torch.float32, device=DEV, requires_grad=True)

    # ---- camera tensors ----
    def cam_t(cb, name):
        img = torch.tensor(frame.images[name].astype(np.float32), device=DEV).permute(2, 0, 1)[None] / 1.0  # (1,3,Himg,Wimg) 0..255
        R_cam_ego = torch.tensor(cb.T_ego_cam[:3, :3].T, dtype=torch.float32, device=DEV)
        t = torch.tensor(cb.T_ego_cam[:3, 3], dtype=torch.float32, device=DEV)
        K = torch.tensor(cb.K, dtype=torch.float32, device=DEV)
        return img, R_cam_ego, t, K, cb.image_width, cb.image_height
    img_i, Ri, ti, Ki, wi_img, hi_img = cam_t(ci, ci_name)
    img_j, Rj, tj, Kj, wj_img, hj_img = cam_t(cj, cj_name)

    # ---- edge-aware smoothness setup: 2D inv_d map over band bbox, image-gradient weights ----
    # Build a dense (H,W) scatter index so we can compute spatial neighbours within the band.
    idx_map = -np.ones((H, W), np.int64)
    idx_map[vy, vx] = np.arange(Npx)
    idx_map_t = torch.tensor(idx_map, device=DEV)
    # right/down neighbour indices (within band)
    def neigh(dy, dx):
        ny, nx = vy + dy, vx + dx
        okn = (ny >= 0) & (ny < H) & (nx >= 0) & (nx < W)
        nidx = np.full(Npx, -1, np.int64)
        nidx[okn] = idx_map[np.clip(ny, 0, H - 1), np.clip(nx, 0, W - 1)]
        valid = nidx >= 0
        return torch.tensor(nidx, device=DEV), torch.tensor(valid, device=DEV)
    nR, vR = neigh(0, 1)
    nD, vD = neigh(1, 0)
    # image-gradient (use L1 RGB) -> edge weight exp(-grad)
    Lg = cv2.cvtColor(L1, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    gx_im = np.zeros((H, W), np.float32); gy_im = np.zeros((H, W), np.float32)
    gx_im[:, :-1] = np.abs(Lg[:, 1:] - Lg[:, :-1])
    gy_im[:-1, :] = np.abs(Lg[1:, :] - Lg[:-1, :])
    wR = torch.tensor(np.exp(-12.0 * gx_im[vy, vx]), dtype=torch.float32, device=DEV)
    wD = torch.tensor(np.exp(-12.0 * gy_im[vy, vx]), dtype=torch.float32, device=DEV)

    # ---- LiDAR anchor: project sweep points to band ERP pixels, get per-pixel lidar range ----
    pts, lts, ldelta = load_lidar_feather(ROOT / BMW, ts[0], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3]
    # true ego-centre ERP (u,v) of each lidar pt + its ego range
    px, py, pz = pts[:, 0], pts[:, 1], pts[:, 2]
    th = np.arctan2(py, px); ph = np.arctan2(pz, np.hypot(px, py))
    ulid = ((np.pi - th) / (2 * np.pi) * W - 0.5) % W
    vlid = ((np.pi / 2 - ph) / np.pi * H - 0.5)
    rlid = np.linalg.norm(pts, axis=1)
    uli = np.round(ulid).astype(int) % W
    vli = np.round(vlid).astype(int)
    okl = (vli >= 0) & (vli < H) & (bm[np.clip(vli, 0, H - 1), uli])
    # nearest-range per band pixel (z-buffer: keep closest)
    lid_range_map = np.full((H, W), np.nan, np.float32)
    for k in np.flatnonzero(okl):
        vv_, uu_ = vli[k], uli[k]
        if np.isnan(lid_range_map[vv_, uu_]) or rlid[k] < lid_range_map[vv_, uu_]:
            lid_range_map[vv_, uu_] = rlid[k]
    lid_band = lid_range_map[vy, vx]
    has_lid = np.isfinite(lid_band) & (lid_band >= DMIN) & (lid_band <= DMAX)
    n_lid = int(has_lid.sum())
    lid_inv_t = torch.tensor(np.where(has_lid, 1.0 / np.where(has_lid, lid_band, 1.0), 0.0), dtype=torch.float32, device=DEV)
    has_lid_t = torch.tensor(has_lid, dtype=torch.bool, device=DEV)
    print(f"[lidar] sweep N={len(pts)} delta={ldelta:.1f}ms; band pixels with LiDAR anchor={n_lid} ({100*n_lid/max(Npx,1):.1f}% of band)", flush=True)

    # ---- DENSIFY LiDAR across the band (inpaint sparse depth -> dense prior) ----
    # Sparse LiDAR (11% of band) leaves the BMW under-anchored and lets photometric collapse
    # depth to the FAR clamp (zero-parallax degenerate min). Build a dense LiDAR prior by
    # nearest-pixel fill of inverse-depth inside the band, so every band pixel (incl. BMW) has
    # a LiDAR-consistent target. This is the "LiDAR-anchored accurate dense depth" the test wants.
    invlid_map = np.full((H, W), np.nan, np.float32)
    invlid_map[vy[has_lid], vx[has_lid]] = 1.0 / lid_band[has_lid]
    # distance-transform nearest fill, then smooth, confined to band
    knownmask = np.isfinite(invlid_map).astype(np.uint8)
    if knownmask.sum() > 0:
        _, lbl = cv2.distanceTransformWithLabels((1 - knownmask).astype(np.uint8), cv2.DIST_L2, 5,
                                                  labelType=cv2.DIST_LABEL_PIXEL)
        ky, kx = np.where(knownmask > 0)
        # map label -> known coord. OpenCV labels are 1..K in scan order of zero-distance pixels.
        order = np.lexsort((kx, ky))  # not guaranteed to match cv2 label order -> use direct kNN instead
    # robust + simple: per band pixel, take inverse-depth of nearest known LiDAR band pixel
    from scipy.spatial import cKDTree
    known_idx = np.where(has_lid)[0]
    tree = cKDTree(np.stack([vy[has_lid], vx[has_lid]], axis=1))
    dist_nn, nn = tree.query(np.stack([vy, vx], axis=1), k=1)
    inv_prior = (1.0 / lid_band[has_lid])[nn]               # (Npx,) dense inv-depth prior
    nn_dist_px = dist_nn                                    # px distance to nearest LiDAR support
    # confidence in the prior fades with distance to nearest LiDAR point (px)
    prior_conf = np.exp(-(nn_dist_px / 40.0)).astype(np.float32)
    inv_prior_t = torch.tensor(inv_prior.astype(np.float32), device=DEV)
    prior_conf_t = torch.tensor(prior_conf, device=DEV)
    # re-init inv_d from the DENSE LiDAR prior (not the degenerate plane-sweep argmax)
    with torch.no_grad():
        inv_d.copy_(torch.tensor(np.clip(inv_prior, INVDMIN, INVDMAX).astype(np.float32), device=DEV))
    print(f"[densify] dense LiDAR prior: nn-dist med={np.median(nn_dist_px):.1f}px p90={np.percentile(nn_dist_px,90):.1f}px", flush=True)

    # ---- DECISIVE PRE-OPT MEASUREMENT: photometric residual at the PURE DENSE-LiDAR depth ----
    # (does reprojecting BOTH cams to ego-centre at the LiDAR-accurate depth SINGLE the BMW?)
    with torch.no_grad():
        invL = torch.tensor(np.clip(inv_prior, INVDMIN, INVDMAX).astype(np.float32), device=DEV)
        depthL = 1.0 / invL
        P_egoL = depthL[:, None] * d_ego
        gix, giy, vi = project_torch(P_egoL, Ri, ti, Ki, wi_img, hi_img)
        gjx, gjy, vj = project_torch(P_egoL, Rj, tj, Kj, wj_img, hj_img)
        ci_L = sample_img(img_i, gix, giy); cj_L = sample_img(img_j, gjx, gjy)
        bothL = (vi & vj).cpu().numpy()
        residL = torch.abs(ci_L - cj_L).mean(1).cpu().numpy()
    in_bmw0 = (np.abs(((vx - CROP_U + W / 2) % W) - W / 2) <= CROP_HALF) & (vy >= CROP_V0) & (vy <= CROP_V1)
    bmwL = bothL & in_bmw0
    print(f"[AT-LIDAR-DEPTH] whole band median RGB resid (both)={np.median(residL[bothL]):.2f}/255  "
          f"%resid<18={100*np.mean((residL<18)[bothL]):.1f}%", flush=True)
    print(f"[AT-LIDAR-DEPTH] BMW region median RGB resid={np.median(residL[bmwL]):.2f}/255  "
          f"%resid<18={100*np.mean((residL<18)[bmwL]):.1f}%  n={int(bmwL.sum())}", flush=True)

    # ---- optimize (LiDAR-prior-anchored; photometric only refines subpixel near the prior) ----
    # Strong prior anchor prevents the far-depth photometric collapse; photometric + smoothness
    # then refine within the LiDAR basin. This is the regularized OPTIMIZED depth under test.
    W_PHOTO, W_SMOOTH, W_PRIOR = 1.0, 0.25, 6.0
    CHARB = 1.0
    opt = torch.optim.Adam([inv_d], lr=0.01)
    NITERS = 600
    for it in range(NITERS):
        opt.zero_grad()
        invc = inv_d.clamp(INVDMIN, INVDMAX)
        depth = 1.0 / invc
        P_ego = depth[:, None] * d_ego  # (N,3)
        gix, giy, vi = project_torch(P_ego, Ri, ti, Ki, wi_img, hi_img)
        gjx, gjy, vj = project_torch(P_ego, Rj, tj, Kj, wj_img, hj_img)
        ci_rgb = sample_img(img_i, gix, giy)  # (N,3) 0..255
        cj_rgb = sample_img(img_j, gjx, gjy)
        both = vi & vj
        # (a) photometric Charbonnier on color diff
        diff = (ci_rgb - cj_rgb) / 255.0
        photo = torch.sqrt((diff ** 2).sum(1) + CHARB ** 2 / 255.0 ** 2) - CHARB / 255.0
        photo_m = torch.where(both, photo, torch.zeros_like(photo))
        n_both = both.float().sum().clamp(min=1.0)
        loss_photo = photo_m.sum() / n_both
        # (b) edge-aware smoothness on inv_d (right + down neighbours)
        def smooth_term(nidx, vmask, wedge):
            di = torch.abs(invc - invc[nidx.clamp(min=0)])
            di = torch.where(vmask, di, torch.zeros_like(di))
            return (wedge * di).sum() / vmask.float().sum().clamp(min=1.0)
        loss_smooth = smooth_term(nR, vR, wR) + smooth_term(nD, vD, wD)
        # (c) DENSE LiDAR-prior anchor on inv_d (Huber, weighted by prior confidence).
        # Prevents the degenerate far-depth photometric collapse; photometric then refines
        # subpixel within the LiDAR basin.
        pr = invc - inv_prior_t
        pr_h = F.huber_loss(invc, inv_prior_t, delta=0.02, reduction="none")
        loss_prior = (prior_conf_t * pr_h).sum() / prior_conf_t.sum().clamp(min=1.0)
        loss = W_PHOTO * loss_photo + W_SMOOTH * loss_smooth + W_PRIOR * loss_prior
        loss.backward()
        opt.step()
        if it % 100 == 0 or it == NITERS - 1:
            with torch.no_grad():
                med_photo = photo_m[both].median().item() * 255.0 if int(both.sum()) > 0 else float("nan")
                med_dev = (np.abs((1.0 / invc.detach().cpu().numpy()) - (1.0 / inv_prior))).item() if False else float(np.median(np.abs((1.0 / invc.detach().cpu().numpy()) - (1.0 / inv_prior))))
                print(f"  it{it:4d} loss={loss.item():.4f} photo={loss_photo.item():.4f} smooth={loss_smooth.item():.4f} prior={float(loss_prior):.4f} medRGBresid={med_photo:.2f} medΔdepthVsPrior={med_dev:.2f}m", flush=True)

    # ---- final forward / metrics ----
    with torch.no_grad():
        invc = inv_d.clamp(INVDMIN, INVDMAX)
        depth = 1.0 / invc
        P_ego = depth[:, None] * d_ego
        gix, giy, vi = project_torch(P_ego, Ri, ti, Ki, wi_img, hi_img)
        gjx, gjy, vj = project_torch(P_ego, Rj, tj, Kj, wj_img, hj_img)
        ci_rgb = sample_img(img_i, gix, giy)
        cj_rgb = sample_img(img_j, gjx, gjy)
        both = (vi & vj)
        resid_rgb = torch.abs(ci_rgb - cj_rgb).mean(1)  # (N,) 0..255
        agree_col = 0.5 * (ci_rgb + cj_rgb)
        # fall back to L1 colour where only one cam valid or residual high
        depth_np = depth.cpu().numpy()
        resid_np = resid_rgb.cpu().numpy()
        both_np = both.cpu().numpy()
        agree_np = agree_col.cpu().numpy()
        invc_np = invc.cpu().numpy()

    CONF_THRESH = 18.0  # RGB residual "converged/confident" threshold (0..255)
    conf = both_np & (resid_np < CONF_THRESH)
    # honest fractions: report BOTH the both-valid coverage and the conf-rate AMONG both-valid
    frac_both = float(both_np.mean()) if Npx else float("nan")
    med_resid = float(np.median(resid_np[both_np])) if int(both_np.sum()) else float("nan")
    conf_rate_amongboth = float((resid_np[both_np] < CONF_THRESH).mean()) if int(both_np.sum()) else float("nan")

    # LiDAR agreement on band (where anchor exists): |opt_depth - lidar_range|
    med_lidagree = float(np.median(np.abs(depth_np[has_lid] - lid_band[has_lid]))) if n_lid > 0 else float("nan")

    # ---- BMW region: tight box around the car at the cut; measure on consistent subsets ----
    in_bmw = (np.abs(((vx - CROP_U + W / 2) % W) - W / 2) <= CROP_HALF) & (vy >= CROP_V0) & (vy <= CROP_V1)
    bmw_both = both_np & in_bmw
    bmw_both_n = int(bmw_both.sum())
    if bmw_both_n > 0:
        med_resid_bmw = float(np.median(resid_np[bmw_both]))                       # OPTIMIZED depth resid
        med_resid_bmw_L = float(np.median(residL[bmw_both]))                       # pure-LiDAR depth resid (SAME pixels)
        conf_rate_bmw = float((resid_np[bmw_both] < CONF_THRESH).mean())
        conf_rate_bmw_L = float((residL[bmw_both] < CONF_THRESH).mean())
        frac_both_bmw = float(bmw_both.sum() / max(int(in_bmw.sum()), 1))
        med_depth_bmw = float(np.median(depth_np[bmw_both]))
        med_prior_bmw = float(np.median((1.0 / inv_prior)[bmw_both]))               # dense-LiDAR prior depth
    else:
        med_resid_bmw = med_resid_bmw_L = conf_rate_bmw = conf_rate_bmw_L = float("nan")
        frac_both_bmw = med_depth_bmw = med_prior_bmw = float("nan")
    bmw_has_lid = has_lid & in_bmw
    med_lidagree_bmw = float(np.median(np.abs(depth_np[bmw_has_lid] - lid_band[bmw_has_lid]))) if int(bmw_has_lid.sum()) > 0 else float("nan")
    med_lidrange_bmw = float(np.median(lid_band[bmw_has_lid])) if int(bmw_has_lid.sum()) > 0 else float("nan")

    # ---- composite optimized-depth reproject onto L1 (band only, conf-gated, fall back to L1) ----
    def composite(agree_arr, conf_arr):
        cm = np.zeros((H, W, 3), np.float32); cf = np.zeros((H, W), np.float32)
        cm[vy, vx] = agree_arr; cf[vy, vx] = conf_arr.astype(np.float32)
        a = cv2.GaussianBlur(cf * ramp, (0, 0), 2.0)[..., None]
        return np.clip(L1.astype(np.float32) * (1 - a) + cm * a, 0, 255).astype(np.uint8)

    opt_out = composite(agree_np, conf)
    # pure-dense-LiDAR-depth reproject (no photometric refine) — does GT depth single it?
    conf_L = bothL & (residL < CONF_THRESH)
    agree_L = (0.5 * (ci_L + cj_L)).cpu().numpy()
    lid_out = composite(agree_L, conf_L)

    # ---- SAVE comparison crop (L1 | plane-sweep-argmax | LiDAR-depth | optimized) ----
    roll = W // 2 - CROP_U; cc = W // 2
    def cr(im):
        return cv2.cvtColor(np.roll(im, roll, 1)[CROP_V0:CROP_V1, cc - CROP_HALF:cc + CROP_HALF], cv2.COLOR_RGB2BGR)
    panel = np.hstack([lab(cr(L1), "L1 (doubled-at-cut)"),
                       lab(cr(ps_out), "plane-sweep argmax"),
                       lab(cr(lid_out), "dense-LiDAR depth reproj"),
                       lab(cr(opt_out), "OPTIMIZED depth reproject")])
    panel = cv2.resize(panel, (panel.shape[1] * 2, panel.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
    outp = OUT / "DB13_overfit_bmw.jpg"
    cv2.imwrite(str(outp), panel, [cv2.IMWRITE_JPEG_QUALITY, 95])

    # full-pano reference (small) for context
    def rw(im, w=1500):
        return cv2.resize(im, (w, round(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "DB13_overfit_full.jpg"),
                cv2.cvtColor(np.vstack([lab(rw(L1), "L1"), lab(rw(opt_out), "DB13 optimized-depth reproject (band)")]), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 90])

    print("\n========== DB13 METRICS ==========", flush=True)
    print(f"band px={Npx}  lidar-anchored px={n_lid} ({100*n_lid/max(Npx,1):.1f}%)", flush=True)
    print(f"[WHOLE BAND] both-cam-valid coverage = {100*frac_both:.1f}%  (rest: 1 cam only -> auto L1)", flush=True)
    print(f"[WHOLE BAND] median RGB resid (both-valid) OPT={med_resid:.2f}  conf<{CONF_THRESH:.0f} among-both={100*conf_rate_amongboth:.1f}%", flush=True)
    print(f"[WHOLE BAND] median |opt_depth - lidar_range| at anchors = {med_lidagree:.2f} m", flush=True)
    print("--- BMW car region (same pixel set, head-to-head) ---", flush=True)
    print(f"[BMW] both-valid px={bmw_both_n}  both-coverage={100*frac_both_bmw:.1f}%", flush=True)
    print(f"[BMW] median RGB resid : pure-LiDAR-depth={med_resid_bmw_L:.2f}  vs  OPTIMIZED={med_resid_bmw:.2f}  /255", flush=True)
    print(f"[BMW] %conf (<{CONF_THRESH:.0f}/255): pure-LiDAR-depth={100*conf_rate_bmw_L:.1f}%  vs  OPTIMIZED={100*conf_rate_bmw:.1f}%", flush=True)
    print(f"[BMW] depths: opt={med_depth_bmw:.1f}m  dense-LiDAR-prior={med_prior_bmw:.1f}m  lidar@anchors={med_lidrange_bmw:.1f}m  |opt-lidar|={med_lidagree_bmw:.2f}m", flush=True)
    # depth distribution of the OPTIMIZED depth over both-valid band pixels (exposes far-clamp leak)
    dvb = depth_np[both_np]
    print(f"[OPT-depth dist over both-valid band] p10={np.percentile(dvb,10):.1f} p50={np.percentile(dvb,50):.1f} "
          f"p90={np.percentile(dvb,90):.1f}m  frac@clamp(>=78m)={100*np.mean(dvb>=78):.1f}%", flush=True)
    print(f"[PRIOR-depth dist over both-valid band] p10={np.percentile((1/inv_prior)[both_np],10):.1f} "
          f"p50={np.percentile((1/inv_prior)[both_np],50):.1f} p90={np.percentile((1/inv_prior)[both_np],90):.1f}m", flush=True)
    print(f"[saved] {outp}", flush=True)
    print(f"[runtime] {time.time()-t0:.0f}s", flush=True)
    print("==================================", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
