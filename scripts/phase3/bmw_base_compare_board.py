"""BMW base comparison board (local, CPU-only, NO pixel edits, NO A100).

Pull the existing BMW ERP bases (hard_select / DB75 / G_bmw_pano / A1_view_none /
BEST_bmw_pano), crop the SAME 4 marked ROIs from each, and lay them out as a
same-frame comparison board (1 row per ROI, 1 column per base) plus a full-ERP
thumbnail strip. Each column is labelled with how that base was produced. The
near-field ghost/double-image hotspots (right curb-wall-base, lower-center black
BMW/SUV) are flagged so the leader can pick a base on the "just looks good" bar.

This does NOT modify any pixels and does NOT touch the network/A100.
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "base_compare_bmw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# (label, how-produced, path) — order = roughly safest→most-edited
BASES = [
    ("hard_select", "pure L1 hard-select (multi-center mosaic; hard seam, NO ghost)",
     "deliverables/gpt_pro_sources/04_L1_hard_select_bmw_2048x1024.png"),
    ("DB75", "source-MIXED seam-band blend (softened; NOT single-source truth)",
     "deliverables/layered_target_raycaster/db75_full_erp_source_mixed_fallback/fetch/02a00399_a000_bmw_source_mixed_candidate.png"),
    ("BEST_bmw_pano", "donor composite / MIXED (best-donor stitch)",
     "deliverables/ghostkill/BEST_bmw_pano.jpg"),
    ("A1_view_none", "A1 keepout COMPLETION variant",
     "deliverables/gpt_pro_sources/01_A1_view_none_bmw_2048x1024.png"),
    ("G_bmw_pano", "classic BMW diagnostic — COMPLETION/generative-style",
     "deliverables/gpt_pro_sources/02_G_bmw_pano_2048x1024.jpg"),
]

# ROI coords on the 2048x1024 ERP (x0,y0,x1,y1)
ROIS = {
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch  [near-field BMW/SUV ghost zone]": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base  [near-field ghost/double-image zone]": (1300, 500, 1575, 760),
}
GHOST_ROWS = {1, 3}  # row indices (into ROIS) that are the near-field ghost hotspots

W, H = 2048, 1024
CELL_W = 360
LAB_W = 210
THUMB_H = 170
HEAD_H = 64
PAD = 6


def font(sz):
    for n in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(n, sz)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    imgs = []
    for label, how, rel in BASES:
        p = ROOT / rel
        if not p.exists():
            raise FileNotFoundError(p)
        imgs.append(Image.open(p).convert("RGB").resize((W, H)))

    f_hdr = font(15); f_sub = font(12); f_lab = font(14); f_title = font(18)
    ncol = len(BASES)
    board_w = LAB_W + ncol * (CELL_W + PAD) + PAD

    # precompute ROI row heights (preserve ROI aspect at CELL_W)
    roi_items = list(ROIS.items())
    row_h = []
    for _name, (x0, y0, x1, y1) in roi_items:
        rh = int((y1 - y0) * CELL_W / (x1 - x0))
        row_h.append(rh)

    board_h = 40 + HEAD_H + (THUMB_H + 26) + sum(rh + 26 for rh in row_h) + 30
    board = Image.new("RGB", (board_w, board_h), (14, 14, 18))
    d = ImageDraw.Draw(board)
    d.text((8, 8), "BMW 02a00399:0 — base comparison for leader (\"just looks good\" bar). NO pixel edits. Red headers = near-field ghost/double-image hotspots.", (245, 245, 250), font=f_title)

    # column headers (base label + how produced)
    x = LAB_W + PAD
    for ci, (label, how, _rel) in enumerate(BASES):
        d.rectangle([x, 40, x + CELL_W, 40 + HEAD_H], fill=(28, 28, 38))
        d.text((x + 6, 44), label, (255, 235, 120), font=f_hdr)
        # wrap how-produced
        words = how.split(); line = ""; yy = 62
        for w in words:
            if d.textlength(line + " " + w, font=f_sub) > CELL_W - 12:
                d.text((x + 6, yy), line, (200, 210, 220), font=f_sub); yy += 13; line = w
            else:
                line = (line + " " + w).strip()
        d.text((x + 6, yy), line, (200, 210, 220), font=f_sub)
        x += CELL_W + PAD

    # full-ERP thumbnail strip
    y = 40 + HEAD_H
    d.text((8, y + THUMB_H // 2), "FULL ERP", (220, 220, 230), font=f_lab)
    x = LAB_W + PAD
    for im in imgs:
        th = im.resize((CELL_W, THUMB_H))
        board.paste(th, (x, y)); x += CELL_W + PAD
    y += THUMB_H + 26

    # ROI rows
    for ri, ((name, (x0, y0, x1, y1)), rh) in enumerate(zip(roi_items, row_h)):
        lab_col = (255, 90, 90) if ri in GHOST_ROWS else (210, 220, 230)
        # wrap roi name in label column
        words = name.split(); line = ""; yy = y + 4
        for w in words:
            if d.textlength(line + " " + w, font=f_lab) > LAB_W - 10:
                d.text((6, yy), line, lab_col, font=f_lab); yy += 16; line = w
            else:
                line = (line + " " + w).strip()
        d.text((6, yy), line, lab_col, font=f_lab)
        x = LAB_W + PAD
        for im in imgs:
            crop = im.crop((x0, y0, x1, y1)).resize((CELL_W, rh))
            board.paste(crop, (x, y))
            if ri in GHOST_ROWS:
                dd = ImageDraw.Draw(board); dd.rectangle([x, y, x + CELL_W - 1, y + rh - 1], outline=(255, 70, 70), width=2)
            x += CELL_W + PAD
        y += rh + 26

    out = OUT_DIR / "BMW_base_compare_board.jpg"
    board.save(out, quality=92)
    print("saved:", out, board.size)


if __name__ == "__main__":
    main()
