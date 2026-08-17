"""GPU path for the two hot spots in sample production.

Measured per-frame cost on this box (AV2, 7 cameras, 2048x1024 ERP):

    camera support   1213 ms   48%
    render            758 ms   30%
    JPEG decode       436 ms   17%
    PNG encode        110 ms    4%

The first two are the same operation applied to a 2M-ray grid - project every
direction into every camera, test bounds, sample - which is what a GPU is for.
They also dominate host RAM: each CPU worker peaked at 524 MB, so 30 workers did
not fit in 31 GB and the run died with MemoryError. Moving the grids into VRAM
cuts both.

Numerically this must agree with the CPU path, not merely look similar: the
rule mask koi approved is pinned by a golden test, and a GPU result that differs
by a pixel would break it. Everything here is float64 for the geometry and the
same bilinear formula as `db239_seam_mask.bilinear_rgb`, so `verify_matches_cpu`
is expected to report exact equality on the support masks and <1 DN on colour.

Enable with W2P_GPU=1; absent or unavailable, callers keep the CPU path.
"""
from __future__ import annotations

import os

import numpy as np

_state = {"torch": None, "dev": None, "dirs": None, "checked": False}


def available():
    """True if a usable CUDA device is present. Cached; never raises."""
    if _state["checked"]:
        return _state["torch"] is not None
    _state["checked"] = True
    if os.environ.get("W2P_GPU", "") not in ("1", "true", "yes"):
        return False
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        _state["torch"] = torch
        _state["dev"] = torch.device("cuda")
        return True
    except Exception:
        _state["torch"] = None
        return False


def _dirs(DIRS_FLAT):
    """Upload the ERP ray grid once; it is identical for every frame and camera."""
    if _state["torch"] is None and not available():
        raise RuntimeError("GPU path requested but unavailable; call available() first")
    t = _state["torch"]
    if _state["dirs"] is None:
        _state["dirs"] = t.as_tensor(np.ascontiguousarray(DIRS_FLAT),
                                     dtype=t.float64, device=_state["dev"])
    return _state["dirs"]


FORWARD_MIN = 0.05          # db239_seam_mask.DIRECTIONAL_FORWARD_MIN


def support_and_own(pose, cams, DIRS_FLAT, H, W):
    """Per-camera support masks plus the winning camera per pixel.

    Line-for-line the CPU `camera_support_emc` + ownership loop, not a
    reimplementation of the same idea. Two details are load-bearing and were both
    wrong in the first attempt, which cost 313 px of ownership disagreement:

      * support uses `DIRS_FLAT @ R` - the ray direction only. Using
        `(1e6*dir - t) @ R` looks equivalent because the translation is tiny
        against 1e6, but it shifts z by a per-camera constant, and z is what
        decides ownership between cameras.
      * the border is `1 <= p < size - 1`, and the forward test is z > 0.05, not
        the 0.5 used by `_project`.
    """
    t = _state["torch"]
    D = _dirs(DIRS_FLAT)
    face = t.full((H * W,), -2.0, dtype=t.float64, device=_state["dev"])
    own = t.full((H * W,), -1, dtype=t.int16, device=_state["dev"])
    sup = {}
    for i, cam in enumerate(cams):
        c = pose[cam]
        R = t.as_tensor(c["R"], dtype=t.float64, device=_state["dev"])
        K = c["K"]
        hh, ww = c["shape"]
        dcam = D @ R
        z = dcam[:, 2]
        zc = t.clamp(z, min=1e-6)
        px = K[0, 0] * dcam[:, 0] / zc + K[0, 2]
        py = K[1, 1] * dcam[:, 1] / zc + K[1, 2]
        ok = ((z > FORWARD_MIN) & (px >= 1.0) & (px < ww - 1.0)
              & (py >= 1.0) & (py < hh - 1.0))
        sup[cam] = ok
        # ownership: the CPU path compares float32 of the same z, so match it
        f = z.to(t.float32)
        win = ok & (f > face.to(t.float32))
        face = t.where(win, f.to(t.float64), face)
        own = t.where(win, t.tensor(i, dtype=t.int16, device=_state["dev"]), own)
    return sup, own


def sample_frame(pose, cams, imgs, own, domain, DIRS_FLAT, H, W):
    """Rotation-only composite on device. -> (erp uint8 HxWx3, written bool HxW)."""
    t = _state["torch"]
    D = _dirs(DIRS_FLAT)
    dom = t.as_tensor(np.ascontiguousarray(domain.reshape(-1)), device=_state["dev"])
    erp = t.zeros((H * W, 3), dtype=t.float32, device=_state["dev"])
    written = t.zeros((H * W,), dtype=t.bool, device=_state["dev"])
    for i, cam in enumerate(cams):
        m = (own == i) & dom
        if not bool(m.any()):
            continue
        idx = m.nonzero(as_tuple=True)[0]
        c = pose[cam]
        R = t.as_tensor(c["R"], dtype=t.float64, device=_state["dev"])
        tr = t.as_tensor(c["t"], dtype=t.float64, device=_state["dev"])
        K = c["K"]
        hh, ww = c["shape"]
        # exactly db238_screen._project on X = 1e6 * dir: subtract t, z > 0.5,
        # bounds [1, size-2). Different from the support test above on purpose -
        # the CPU path uses two different predicates and so must this.
        Xc = (D[idx] * 1e6 - tr) @ R
        zr = Xc[:, 2]
        z = t.clamp(zr, min=1e-6)
        px = K[0, 0] * Xc[:, 0] / z + K[0, 2]
        py = K[1, 1] * Xc[:, 1] / z + K[1, 2]
        ok = ((zr > 0.5) & (px >= 1) & (px < ww - 2)
              & (py >= 1) & (py < hh - 2))
        if not bool(ok.any()):
            continue
        idx, px, py = idx[ok], px[ok], py[ok]
        img = t.as_tensor(np.ascontiguousarray(imgs[cam], dtype=np.float32),
                          device=_state["dev"])
        x0 = px.floor().to(t.int64).clamp(0, ww - 2)
        y0 = py.floor().to(t.int64).clamp(0, hh - 2)
        fx = (px - px.floor()).to(t.float32).unsqueeze(1)
        fy = (py - py.floor()).to(t.float32).unsqueeze(1)
        v00 = img[y0, x0]
        v01 = img[y0, x0 + 1]
        v10 = img[y0 + 1, x0]
        v11 = img[y0 + 1, x0 + 1]
        erp[idx] = ((v00 * (1 - fx) + v01 * fx) * (1 - fy)
                    + (v10 * (1 - fx) + v11 * fx) * fy)
        written[idx] = True
        del img
    return (erp.reshape(H, W, 3).clamp(0, 255).to(t.uint8).cpu().numpy(),
            written.reshape(H, W).cpu().numpy())
