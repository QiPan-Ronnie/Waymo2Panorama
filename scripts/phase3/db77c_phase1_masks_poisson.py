"""DB-77C Phase 1 (local CPU, NO A100): masks + Poisson low-freq tone-harmonize on the A1 base.

On the A1_view_none base, build the leash masks and clean ONLY the safe faint seams:
  - generated_mask : A1 outpainted region (hard_select is black there) -> mark as generated.
  - seam_band      : hard_select source-id boundary dilated to ~8-24px.
  - protected      : structure proxy (lane / curb / wall-base / strong edge) = object/structure moat
                     (full YOLO car/person/bike/pole/sign/window object-moat is added at the Difix/A100 step;
                      local has no ultralytics, so this is the structure-proxy stand-in).
  - abstain        : near-field object ghost zones (lower-center black BMW/SUV edge + right curb-wall-base
                     ROIs) + very-dark near-ground = HARD ABSTAIN, never harmonized.
  - safe_seam      = seam_band & ~protected & ~abstain & valid(hard_select).

Poisson-style low-freq tone-harmonize on safe_seam ONLY: keep A1 high-freq structure, swap in the
surrounding low-freq tone (fills the DC offset, invents nothing, moves no structure). Edits are
confined to the band by construction (band-outside Δ == 0).

Outputs A1+Poisson + masks + a {A1 | A1+Poisson | masks-overlay} x 4-ROI compare board for the leader.
NO pixel edits outside the safe band, NO network, NO A100.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db77c_leashed_seam"
OUT_DIR.mkdir(parents=True, exist_ok=True)

A1_PATH = ROOT / "deliverables/gpt_pro_sources/01_A1_view_none_bmw_2048x1024.png"
HS_PATH = ROOT / "deliverables/gpt_pro_sources/04_L1_hard_select_bmw_2048x1024.png"
SID_PATH = ROOT / "deliverables/layered_target_raycaster/db74_temporal_candidate_stack/fetch/02a00399_a000_bmw_source_id_before.png"

W, H = 2048, 1024
ROIS = {
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch [ABSTAIN: BMW/SUV ghost]": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base [ABSTAIN: ghost]": (1300, 500, 1575, 760),
}
ABSTAIN_ROIS = [(740, 595, 1035, 745), (1300, 500, 1575, 760)]
SEAM_DILATE = 12  # 8-24px band


def font(sz):
    for n in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(n, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def load(p, gray=False):
    im = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR)
    if im is None:
        raise FileNotFoundError(p)
    if gray:
        return cv2.resize(im, (W, H), interpolation=cv2.INTER_NEAREST)
    return cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2RGB), (W, H))


def structure_protected(a1):
    y = (0.299 * a1[..., 0] + 0.587 * a1[..., 1] + 0.114 * a1[..., 2]).astype(np.float32)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, 3); gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, 3)
    edge = np.sqrt(gx * gx + gy * gy)
    hsv = cv2.cvtColor(a1, cv2.COLOR_RGB2HSV); sat = hsv[..., 1].astype(np.float32); val = hsv[..., 2].astype(np.float32)
    yy = np.arange(H)[:, None]
    road_band = (yy > 390) & (yy < 800)
    lane = ((val > 168) & (sat < 98) & road_band)
    lane = cv2.dilate(lane.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
    curb = (edge > np.percentile(edge, 88)) & road_band & (yy > 520)
    # protected = lane/curb (object-moat) ONLY. Poisson is low-freq tone-harmonize and preserves
    # high-freq structure, so facade walls/windows can be safely tone-harmonized (NOT protected).
    # Full YOLO object-moat (car/person/bike/pole/sign/window) is added at the Difix/A100 step.
    return (lane | curb)


def main():
    a1 = load(A1_PATH); hs = load(HS_PATH); sid = load(SID_PATH, gray=True)
    ylum = (0.299 * a1[..., 0] + 0.587 * a1[..., 1] + 0.114 * a1[..., 2])

    hs_dark = hs.sum(-1) < 20
    a1_content = a1.sum(-1) > 20
    valid_hs = ~hs_dark
    generated = hs_dark & a1_content                       # A1 outpaint (hard_select was black)

    # seam band from hard_select source-id boundary
    b = ((sid != np.roll(sid, 1, 0)) | (sid != np.roll(sid, 1, 1)) | (sid != np.roll(sid, -1, 1)))
    boundary = b & valid_hs
    seam_band = cv2.dilate(boundary.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SEAM_DILATE * 2 + 1,) * 2)) > 0
    seam_band &= valid_hs

    protected = structure_protected(a1)
    abstain = np.zeros((H, W), bool)
    for (x0, y0, x1, y1) in ABSTAIN_ROIS:
        abstain[y0:y1, x0:x1] = True
    yy = np.arange(H)[:, None]
    abstain |= (ylum < 40) & (yy > 540) & (yy < 800)       # very-dark near-ground BMW/SUV body

    safe_seam = seam_band & (~protected) & (~abstain) & valid_hs

    # Poisson-style low-freq tone harmonize on safe_seam only: keep A1 high-freq, swap low-freq tone
    a1f = a1.astype(np.float32)
    lp_a1 = cv2.GaussianBlur(a1f, (0, 0), 15)
    inpaint_mask = cv2.dilate(safe_seam.astype(np.uint8), np.ones((5, 5), np.uint8))
    filled = cv2.inpaint(a1, inpaint_mask, 8, cv2.INPAINT_TELEA).astype(np.float32)
    lp_surround = cv2.GaussianBlur(filled, (0, 0), 15)
    a1_poisson = a1f.copy()
    a1_poisson[safe_seam] = np.clip(a1f[safe_seam] - lp_a1[safe_seam] + lp_surround[safe_seam], 0, 255)
    a1_poisson = a1_poisson.astype(np.uint8)

    # gate metrics
    band_n = int(safe_seam.sum())
    delta = np.abs(a1_poisson.astype(np.int16) - a1.astype(np.int16)).max(-1)
    out_band_changed = int(((delta > 2) & (~safe_seam)).sum())
    metrics = {
        "generated_frac": float(generated.mean()),
        "seam_band_frac": float(seam_band.mean()),
        "protected_frac": float(protected.mean()),
        "abstain_frac": float(abstain.mean()),
        "safe_seam_frac": float(safe_seam.mean()),
        "safe_seam_px": band_n,
        "out_of_band_changed_px": out_band_changed,   # MUST be 0 (edits confined to band)
        "in_band_mean_tone_delta": float(delta[safe_seam].mean()) if band_n else 0.0,
        "in_band_max_tone_delta": int(delta[safe_seam].max()) if band_n else 0,
    }

    # save masks + harmonized
    def ov(rgb, m, c, a=0.55):
        o = rgb.astype(np.float32).copy()
        for i in range(3):
            o[..., i][m] = (1 - a) * o[..., i][m] + a * c[i]
        return np.clip(o, 0, 255).astype(np.uint8)
    masks_ov = ov(ov(ov(ov(a1, generated, (255, 40, 220)), abstain, (255, 40, 40), 0.5), protected, (40, 220, 220), 0.35), safe_seam, (60, 255, 90), 0.7)
    cv2.imwrite(str(OUT_DIR / "A1_poisson_harmonized.png"), cv2.cvtColor(a1_poisson, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(OUT_DIR / "masks_overlay.png"), cv2.cvtColor(masks_ov, cv2.COLOR_RGB2BGR))
    for nm, m in [("generated", generated), ("seam_band", seam_band), ("protected", protected), ("abstain", abstain), ("safe_seam", safe_seam)]:
        cv2.imwrite(str(OUT_DIR / f"mask_{nm}.png"), (m.astype(np.uint8) * 255))

    # compare board: cols = A1 | A1+Poisson | masks-overlay ; rows = full ERP + 4 ROIs
    cols = [("A1 base", a1), ("A1 + Poisson tone-harmonize (safe band only)", a1_poisson),
            ("masks: green=safe-seam magenta=generated red=ABSTAIN cyan=protected", masks_ov)]
    CW = 600; LAB = 200; PAD = 6
    f_h = font(15); f_l = font(13); f_t = font(17)
    roi_items = list(ROIS.items())
    row_h = [min(260, int((y1 - y0) * CW / (x1 - x0))) for _n, (x0, y0, x1, y1) in roi_items]
    thumb_h = int(H * CW / W)
    bw = LAB + 3 * (CW + PAD) + PAD
    bh = 40 + 30 + (thumb_h + 24) + sum(rh + 24 for rh in row_h) + 20
    board = Image.new("RGB", (bw, bh), (14, 14, 18)); d = ImageDraw.Draw(board)
    d.text((8, 8), "DB-77C Phase 1 — A1 base + Poisson faint-seam harmonize (CPU, no edits outside safe band, near-field ghost = ABSTAIN)", (245, 245, 250), font=f_t)
    x = LAB + PAD
    for title, _im in cols:
        d.text((x + 4, 34), title[:64], (255, 235, 120), font=f_l); x += CW + PAD
    y = 64
    d.text((6, y + thumb_h // 2), "FULL ERP", (210, 220, 230), font=f_l)
    x = LAB + PAD
    for _t, im in cols:
        board.paste(Image.fromarray(im).resize((CW, thumb_h)), (x, y)); x += CW + PAD
    y += thumb_h + 24
    for ri, ((name, (x0, y0, x1, y1)), rh) in enumerate(zip(roi_items, row_h)):
        col = (255, 90, 90) if "ABSTAIN" in name else (210, 220, 230)
        ww = name.split(); ln = ""; yy2 = y + 2
        for w in ww:
            if d.textlength(ln + " " + w, font=f_l) > LAB - 8:
                d.text((6, yy2), ln, col, font=f_l); yy2 += 15; ln = w
            else:
                ln = (ln + " " + w).strip()
        d.text((6, yy2), ln, col, font=f_l)
        x = LAB + PAD
        for _t, im in cols:
            board.paste(Image.fromarray(im[y0:y1, x0:x1]).resize((CW, rh)), (x, y)); x += CW + PAD
        y += rh + 24
    board.save(OUT_DIR / "DB77C_phase1_masks_poisson_board.jpg", quality=92)
    import json
    (OUT_DIR / "DB77C_phase1_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print("board:", OUT_DIR / "DB77C_phase1_masks_poisson_board.jpg")


if __name__ == "__main__":
    main()
