"""Honest sky fill for the black upper hemisphere (zero scene parameters, CPU).

The cameras cover ~nothing above the horizon band, leaving a black zenith cap.
The ABSTAIN-compatible fill: extend only OBSERVED sky colours smoothly to the
zenith — per-column boundary samples (sky-likeness voted by brightness+blueness,
non-sky columns borrow the nearest sky column via EDT), vertical ease toward the
global zenith colour, lateral smoothing. No structures, no clouds invented.
Generative sky (DiT360, DB-93) remains the upgrade path when FLUX is available."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import cv2

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "deliverables" / "db89_ghost_recovery"
OUT = ROOT / "deliverables" / "sky_fill_v3"


def fill_sky(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    black = (img.astype(np.int32).sum(2) < 12)
    # the zenith cap = black region connected to the top row, per column
    cap_h = np.zeros(w, np.int32)
    for u in range(w):
        col = black[:, u]
        k = 0
        while k < h and col[k]:
            k += 1
        cap_h[u] = k
    if cap_h.max() == 0:
        return img
    # per-column boundary sample: median of the first 6 valid rows below the cap
    samples = np.zeros((w, 3), np.float32)
    valid = np.zeros(w, bool)
    for u in range(w):
        k = cap_h[u]
        band = img[k:k + 6, u].astype(np.float32)
        if band.shape[0] >= 3:
            s = np.median(band, 0)
            samples[u] = s
            b, g, r = s[2], s[1], s[0]  # note: caller passes RGB; treat generically
            bright = s.mean()
            blueish = s[2] >= s[0] - 8   # RGB: B channel >= R - 8
            valid[u] = bright > 110 and blueish
    if not valid.any():
        valid[:] = True
    # non-sky columns borrow the nearest sky column (circular EDT in u)
    idx = np.arange(w)
    vu = idx[valid]
    # circular nearest via tripling
    vu3 = np.concatenate([vu - w, vu, vu + w])
    nearest = vu3[np.abs(vu3[None, :] - idx[:, None]).argmin(1)] % w
    samples = samples[nearest]
    # lateral smoothing of the boundary samples (wrap-aware)
    pad = 256
    sm = np.concatenate([samples[-pad:], samples, samples[:pad]], 0)
    sm = cv2.GaussianBlur(sm[None, :, :], (0, 0), 96)[0]   # sky is LOW-frequency: kill column curtains
    samples = sm[pad:-pad]
    zenith = np.median(samples, 0) * np.array([0.92, 0.96, 1.04])  # zenith slightly deeper blue
    out = img.copy()
    for u in range(w):
        k = cap_h[u]
        if k == 0:
            continue
        t = (np.arange(k, dtype=np.float32) / max(k, 1))[:, None]  # 0 at zenith, 1 AT the junction
        col = zenith[None, :] * (1 - t) + samples[u][None, :] * t
        out[:k, u] = np.clip(col, 0, 255).astype(np.uint8)
    # feather the junction row band
    j = cv2.GaussianBlur(out.astype(np.float32), (0, 0), 1.2)
    band = np.zeros((h, w), np.float32)
    for u in range(w):
        k = cap_h[u]
        band[max(0, k - 3):k + 3, u] = 1.0
    band = cv2.GaussianBlur(band, (0, 0), 2)[:, :, None]
    return np.clip(out * (1 - band) + j * band, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)
    for p in sorted(SRC.glob("*_segcomposite.png")):
        img = np.array(Image.open(p))
        res = fill_sky(img)
        Image.fromarray(res).save(OUT / p.name.replace("_segcomposite", "_skyfill"))
        print("done", p.name)
