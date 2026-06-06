"""DB-77C deliverable v0 (local CPU): A1 plausible panorama + 3-tier provenance data contract.

The honest "explored-to-its-limit" floor deliverable for the plausible-renderer line, regardless
of EXP-A/B outcome. Per leader + opus audit: 3-tier graceful degradation on the A1 base.
  tier0 raw         : hard_select single-source pixels (trust the camera) — valid & not seam/abstain/gen
  tier1 poisson     : safe seam-band, low-freq tone-harmonized (presentation; structure preserved)
  tier2 learned     : RESERVED (Difix/UniDepth-IBR refine; EXP-A verified Difix is safe but faint on A1;
                       filled only if EXP-B's IBR render improves the near-field — else stays empty)
  tier3 abstain     : near-field object-ghost / no-geometry — generated_mask, NOT source-faithful
  generated         : A1 outpaint (sky / out-of-FOV) — generated_mask
Outputs: erp_presentation_rgb (A1 + Poisson) + tier_map + risk_map + generated/abstain masks +
provenance manifest (per-tier fractions + claim boundary) + a review board. NO pixel edits beyond
the already-computed Poisson safe band; NO network; NO A100.
"""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "deliverables" / "db77c_deliverable_v0"
OUT.mkdir(parents=True, exist_ok=True)
SEAM = ROOT / "deliverables" / "db77c_leashed_seam"
A1 = ROOT / "deliverables/gpt_pro_sources/01_A1_view_none_bmw_2048x1024.png"
A1P = SEAM / "A1_poisson_harmonized.png"
W, H = 2048, 1024
ROIS = {"left_road": (250, 515, 460, 715), "lower_center [abstain]": (740, 595, 1035, 745),
        "center_lane": (1030, 515, 1325, 735), "right_curb [abstain]": (1300, 500, 1575, 760)}


def font(s):
    for n in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(n, s)
        except Exception:
            pass
    return ImageFont.load_default()


def ld(p, gray=False):
    im = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR)
    im = cv2.resize(im, (W, H), interpolation=cv2.INTER_NEAREST)
    return im if gray else cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def ov(rgb, m, c, a=0.55):
    o = rgb.astype(np.float32).copy()
    for i in range(3):
        o[..., i][m] = (1 - a) * o[..., i][m] + a * c[i]
    return np.clip(o, 0, 255).astype(np.uint8)


def main():
    a1 = ld(A1)
    pres = ld(A1P) if A1P.exists() else a1.copy()    # presentation rgb = A1 + Poisson safe-seam
    gen = ld(SEAM / "mask_generated.png", True) > 127
    seam = ld(SEAM / "mask_seam_band.png", True) > 127
    abst = ld(SEAM / "mask_abstain.png", True) > 127
    safe = ld(SEAM / "mask_safe_seam.png", True) > 127
    valid = a1.sum(-1) > 20

    # tier map: 0 raw, 1 poisson(safe), 2 learned(reserved/empty), 3 abstain, 4 generated
    tier = np.zeros((H, W), np.uint8)
    tier[valid] = 0
    tier[safe & valid] = 1
    tier[gen & valid] = 4
    tier[abst & valid] = 3
    tier[~valid] = 255   # out-of-FOV black (not part of the ring band)

    # risk map: raw-far low, seam mid, abstain/near-field/generated high
    yy = np.arange(H)[:, None].astype(np.float32) * np.ones((1, W), np.float32)
    nearground = np.clip((yy - 430) / 360, 0, 1)
    risk = 0.15 + 0.25 * nearground
    risk[seam & valid] = np.maximum(risk[seam & valid], 0.55)
    risk[gen & valid] = 0.8
    risk[abst & valid] = 1.0
    risk[~valid] = 0.0

    def frac(m):
        return float((m & valid).sum() / max(valid.sum(), 1))

    prov = {
        "deliverable": "db77c_v0_plausible_panorama_data_contract",
        "base": "A1_view_none (hard_select multi-center mosaic + sky/out-of-FOV outpaint)",
        "presentation_rgb": "A1 + Poisson low-freq tone-harmonize on the safe seam-band (presentation, NOT source-faithful)",
        "tier_legend": {"0_raw": "single-source hard_select pixel", "1_poisson": "safe seam-band tone-harmonized",
                        "2_learned": "RESERVED for verified learned refine (EXP-A/B); empty in v0",
                        "3_abstain": "near-field object-ghost / no-geometry; generated_mask; NOT source-faithful",
                        "4_generated": "A1 outpaint (sky/out-of-FOV); generated_mask", "255": "out-of-FOV black"},
        "tier_fractions_of_band": {"raw": frac(tier == 0), "poisson": frac(tier == 1),
                                   "abstain": frac(tier == 3), "generated": frac(tier == 4)},
        "abstain_band_fraction": frac(abst), "generated_band_fraction": frac(gen), "seam_band_fraction": frac(seam),
        "claim_boundary": ["PLAUSIBLE presentation panorama, NOT source-faithful sensor truth.",
                           "generated/abstain pixels carry masks; the near-field object-ghost is the geometry wall (DB-76a/77B), honestly abstained.",
                           "tier2 (learned refine) reserved: EXP-A verified Difix is safe+controllable but faint on A1's soft seams; fill only if a learned render demonstrably improves the near-field (EXP-B)."],
    }
    cv2.imwrite(str(OUT / "erp_presentation_rgb.png"), cv2.cvtColor(pres, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(OUT / "tier_map.png"), tier)
    cv2.imwrite(str(OUT / "risk_map.png"), (np.clip(risk, 0, 1) * 255).astype(np.uint8))
    cv2.imwrite(str(OUT / "generated_mask.png"), (gen.astype(np.uint8) * 255))
    cv2.imwrite(str(OUT / "abstain_mask.png"), (abst.astype(np.uint8) * 255))
    (OUT / "provenance_manifest.json").write_text(json.dumps(prov, indent=2), encoding="utf-8")

    # tier overlay viz
    tv = pres.copy()
    tv = ov(tv, tier == 1, (60, 220, 90), 0.45)     # poisson green
    tv = ov(tv, tier == 4, (255, 40, 220), 0.5)     # generated magenta
    tv = ov(tv, tier == 3, (255, 40, 40), 0.6)      # abstain red
    riskv = cv2.applyColorMap((np.clip(risk, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    riskv = cv2.cvtColor(riskv, cv2.COLOR_BGR2RGB)

    f = font(16); fs = font(13)
    cols = [("presentation rgb (A1+Poisson)", pres), ("tier: green=poisson red=ABSTAIN magenta=generated", tv), ("risk map", riskv)]
    CW = 660; bh = 40 + (int(H * CW / W) + 24) + 220
    board = Image.new("RGB", (3 * CW + 20, bh), (12, 12, 16)); d = ImageDraw.Draw(board)
    d.text((8, 8), "DB-77C deliverable v0 — A1 plausible panorama + 3-tier provenance contract (CPU, no A100). tier2(learned) reserved for EXP-B.", (245, 245, 250), font=f)
    th = int(H * CW / W); x = 4
    for t, im in cols:
        bar = Image.new("RGB", (CW, 22), (22, 22, 30)); ImageDraw.Draw(bar).text((6, 3), t, (230, 230, 240), font=fs)
        board.paste(bar, (x, 40)); board.paste(Image.fromarray(im).resize((CW, th)), (x, 62)); x += CW + 6
    yo = 62 + th + 8
    lines = [f"tier fractions of band: raw={prov['tier_fractions_of_band']['raw']:.3f}  poisson={prov['tier_fractions_of_band']['poisson']:.3f}  abstain={prov['tier_fractions_of_band']['abstain']:.3f}  generated={prov['tier_fractions_of_band']['generated']:.3f}",
             "PLAUSIBLE presentation (NOT source-faithful). Near-field ghost = geometry wall → abstain+generated_mask. tier2 learned-refine reserved (EXP-A: Difix safe but faint; EXP-B pending)."]
    for i, ln in enumerate(lines):
        d.text((8, yo + i * 20), ln, (210, 220, 230), font=fs)
    board.save(OUT / "DB77C_deliverable_v0_board.jpg", quality=92)
    print(json.dumps(prov, indent=2))
    print("board:", OUT / "DB77C_deliverable_v0_board.jpg")


if __name__ == "__main__":
    main()
