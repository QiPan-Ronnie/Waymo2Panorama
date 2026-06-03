"""Confidence-gated DrivingForward (single-centre 3DGS) ⊕ L1: the one remaining 'beat-L1-cleanly'
shot. Use the de-doubled 3DGS ONLY where it is internally coherent (not shredded); keep sharp L1
where the 3DGS shreds (near-ground) and in the far field. Shred detector: 3DGS comb/streak is
impulse-like high-freq → |dfwd - medianBlur| large. Vision-judge the BMW (single?) + overall sharpness."""
from __future__ import annotations
import sys
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import hard_select

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
DFWD = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/dibr_drivingforward_av2_repro/02a00399_a000_dfwd_ERP.jpg")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/killtest"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048


def lab(im, t):
    b = np.zeros((24, im.shape[1], 3), np.uint8); cv2.putText(b, t, (5, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return np.vstack([b, im])


def main():
    loader = AV2RingLoader(ROOT / BMW); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    l1s, l1w = [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r, _a, wt = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        l1s.append(r); l1w.append(wt)
    L1 = hard_select(l1s, l1w)                              # RGB
    dfwd_bgr = cv2.imread(str(DFWD)); dfwd = cv2.cvtColor(cv2.resize(dfwd_bgr, (W, H)), cv2.COLOR_BGR2RGB)

    valid = dfwd.sum(2) > 8                                  # 3DGS has content (not black top/bottom/gaps)
    # shred detector: impulse-like comb/streak → big diff from median; also incoherent local gradient
    med = cv2.medianBlur(dfwd, 5)
    shred = np.abs(dfwd.astype(np.int16) - med.astype(np.int16)).mean(2) > 14
    shred = cv2.dilate(shred.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))).astype(bool)
    gate = (valid & ~shred).astype(np.float32)
    gate = cv2.GaussianBlur(gate, (0, 0), 3.0)              # feather
    gate = np.clip(gate, 0, 1)[..., None]
    out = (L1.astype(np.float32) * (1 - gate) + dfwd.astype(np.float32) * gate).astype(np.uint8)
    print(f"[gate] dfwd valid {valid.mean()*100:.1f}%  shred {shred.mean()*100:.1f}%  used {(gate[...,0]>0.5).mean()*100:.1f}%", flush=True)

    def rw(im, w=1500): return cv2.resize(im, (w, round(im.shape[0]*w/im.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "DFWDGATE_full.jpg"), cv2.cvtColor(np.vstack([lab(rw(L1), "L1"), lab(rw(dfwd), "DrivingForward"), lab(rw(out), "gated DFWD+L1")]), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    # zoom BMW (u~1750) + a building seam (u~300)
    rows = []
    for u, v0, v1, tag in [(1760, 360, 600, "bmw"), (300, 330, 560, "storefront")]:
        roll = W // 2 - u; cc = W // 2
        def cr(im): return cv2.cvtColor(np.roll(im, roll, 1)[v0:v1, cc-180:cc+180], cv2.COLOR_RGB2BGR)
        rows.append(np.hstack([lab(cr(L1), "L1 "+tag), lab(cr(dfwd), "DFWD"), lab(cr(out), "gated")]))
    g = np.vstack(rows); g = cv2.resize(g, (g.shape[1]*2, g.shape[0]*2), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / "DFWDGATE_zoom.jpg"), g, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("[saved] DFWDGATE_full.jpg + DFWDGATE_zoom.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
