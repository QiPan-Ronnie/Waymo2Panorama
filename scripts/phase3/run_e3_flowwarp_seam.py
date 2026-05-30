"""E3 (backlog #1): flow-warped REAL-pixel seam fusion.

Instead of estimating per-pixel DEPTH and reprojecting (E2, depth-accuracy-bound), MEASURE the
cross-view parallax directly with optical flow (RAFT) between the two overlapping cameras' ERP
slabs in each seam band, WARP the neighbor camera's REAL pixels to align, and feather the two
aligned real views across the seam. Flow = end-to-end disparity -> immune to the baseline*focal*
delta(1/z) sensitivity that sank E2. Far field stays byte-identical to L1; unreliable (forward-
backward inconsistent) pixels fall back to L1. No content is invented (diffusion sliver-fill is a
later pass). Judge: relative_warp far-field ~0 + NCC vs neighbor + VISION.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _pad8(a, axis):
    n = a.shape[axis]
    p = (-n) % 8
    if p == 0:
        return a, 0
    pad = [(0, 0)] * a.ndim
    pad[axis] = (0, p)
    return np.pad(a, pad, mode="reflect"), p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=72)
    ap.add_argument("--fb-thresh", type=float, default=2.0)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--w2p-code", default=str(Path(__file__).resolve().parent.parent.parent / "code"))
    args = ap.parse_args()
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, args.w2p_code)

    import torch
    from torchvision.models.optical_flow import raft_large, Raft_Large_Weights
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select
    from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    erp_hw = (args.erp_h, args.erp_w)
    H, W = erp_hw
    loader = AV2RingLoader(Path(args.log_dir))
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[args.anchor])

    slabs, weights = [], []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _, w = render_camera_to_erp(image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
                                         erp_hw=erp_hw, convergence_distance_m=None)
        slabs.append(rgb.astype(np.float32)); weights.append(w.astype(np.float32))
    base = hard_select(slabs, weights)  # uint8 L1

    wts = Raft_Large_Weights.C_T_SKHT_V2
    model = raft_large(weights=wts, progress=False).eval().to(dev)
    tf = wts.transforms()

    def raft(imgA, imgB):
        a, _ = _pad8(imgA, 0); a, pw = _pad8(a, 1)
        b, _ = _pad8(imgB, 0); b, _ = _pad8(b, 1)
        ta = torch.from_numpy(a).permute(2, 0, 1)[None].float()
        tb = torch.from_numpy(b).permute(2, 0, 1)[None].float()
        ta, tb = tf(ta, tb)
        with torch.no_grad():
            fl = model(ta.to(dev), tb.to(dev))[-1][0].cpu().numpy().transpose(1, 2, 0)
        return fl[:imgA.shape[0], :imgA.shape[1]]

    out = base.astype(np.float32).copy()
    diag = []
    for ia, ib in RING_PAIRS:  # RING_PAIRS are integer indices into RING_CAMS_7
        ca, cb = RING_CAMS_7[ia], RING_CAMS_7[ib]
        band, signed = build_voronoi_seam_band(weights[ia], weights[ib], band_half_width=args.band_half_width)
        if int(band.sum()) < 50:
            continue
        cols = np.where(band.any(0))[0]
        c0, c1 = max(0, int(cols.min()) - 48), min(W, int(cols.max()) + 48)
        A = np.clip(slabs[ia][:, c0:c1], 0, 255).astype(np.uint8)
        B = np.clip(slabs[ib][:, c0:c1], 0, 255).astype(np.uint8)
        # CRITICAL: RAFT must only see the SHARED overlap content; each slab's exclusive FoV
        # wedge (black in the other) otherwise dominates the flow (-> 100-200px garbage).
        ov = ((weights[ia][:, c0:c1] > 0) & (weights[ib][:, c0:c1] > 0))[..., None]
        A = (A * ov).astype(np.uint8)
        B = (B * ov).astype(np.uint8)
        flow_ab = raft(A, B)
        flow_ba = raft(B, A)
        gx, gy = np.meshgrid(np.arange(c1 - c0), np.arange(H))
        mapx = (gx + flow_ab[..., 0]).astype(np.float32)
        mapy = (gy + flow_ab[..., 1]).astype(np.float32)
        alignedB = cv2.remap(B.astype(np.float32), mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        fb_x = cv2.remap(flow_ba[..., 0], mapx, mapy, cv2.INTER_LINEAR)
        fb_y = cv2.remap(flow_ba[..., 1], mapx, mapy, cv2.INTER_LINEAR)
        err = np.sqrt((flow_ab[..., 0] + fb_x) ** 2 + (flow_ab[..., 1] + fb_y) ** 2)
        rel = err < args.fb_thresh
        sw = signed[:, c0:c1]
        wgt = np.clip(0.5 + sw / (2.0 * args.band_half_width), 0, 1)[..., None]
        blended = A.astype(np.float32) * (1 - wgt) + alignedB * wgt
        use = band[:, c0:c1] & rel & (A.sum(2) > 0) & (alignedB.sum(2) > 0)
        crop = out[:, c0:c1]
        crop[use] = blended[use]
        out[:, c0:c1] = crop
        brel = float(rel[band[:, c0:c1]].mean()) if band[:, c0:c1].any() else 0.0
        bw = band[:, c0:c1]
        fmag = np.sqrt(flow_ab[..., 0] ** 2 + flow_ab[..., 1] ** 2)
        med_fmag = float(np.median(fmag[bw])) if bw.any() else 0.0
        med_err = float(np.median(err[bw])) if bw.any() else 0.0
        diag.append((f"{ca}|{cb}", int(band.sum()), round(brel, 3), round(med_fmag, 1), round(med_err, 1)))
        print(f"[{args.tag}] seam {ca}|{cb}: band={int(band.sum())} reliable={brel:.2f} med|flow|={med_fmag:.1f} med_fberr={med_err:.1f}", flush=True)
        if args.debug:
            def heat(x, hi):
                u = np.clip(x / hi * 255, 0, 255).astype(np.uint8)
                return cv2.applyColorMap(u, cv2.COLORMAP_JET)
            r0, r1 = 320, 760
            tiles = [A[r0:r1], B[r0:r1], np.clip(alignedB, 0, 255).astype(np.uint8)[r0:r1],
                     heat(fmag, 40)[r0:r1], heat(err, 6)[r0:r1],
                     np.repeat((bw[r0:r1, :, None] * 255).astype(np.uint8), 3, 2)]
            mw = max(t.shape[1] for t in tiles)
            tiles = [np.pad(t, ((0, 0), (0, mw - t.shape[1]), (0, 0))) for t in tiles]
            cv2.imwrite(str(Path(args.out_dir) / f"{args.tag}_dbg_{ia}_{ib}.png"),
                        cv2.cvtColor(np.vstack(tiles), cv2.COLOR_RGB2BGR))
    out = np.clip(out, 0, 255).astype(np.uint8)
    chg = float(np.any(out != base, axis=-1).mean())
    print(f"[{args.tag}] E3 flow-warp changed {100*chg:.2f}% pixels; seams={diag}", flush=True)

    Image.fromarray(base).save(Path(args.out_dir) / f"{args.tag}_L1.png")
    Image.fromarray(out).save(Path(args.out_dir) / f"{args.tag}_E3.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
