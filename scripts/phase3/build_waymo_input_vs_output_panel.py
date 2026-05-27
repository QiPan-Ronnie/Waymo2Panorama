"""Build a panel showing the 8 Waymo cam inputs + Xihan's distance-to-boundary
panorama + our L1+L2 HDR+multiband output. Runs on Colab (all inputs live in
Drive) and writes the panel back to Drive.

Layout (top → bottom):
  Row 1: 5 narrow-FOV "tall" cams      FRONT FRONT_LEFT FRONT_RIGHT SIDE_LEFT SIDE_RIGHT
  Row 2: 3 wide-FOV "short" cams       REAR_LEFT REAR REAR_RIGHT
  Row 3: Xihan distance-to-boundary panorama (his baseline, 4096x2048)
  Row 4: Our L1+L2 HDR+multiband output (color shift fixed)
"""
from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path

# These paths are Colab-mounted Drive paths
EXTRACT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/frame0_extracted")
OUR_PNG = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/l1_waymo_8e7373_hdr_multiband_4096x2048.png")
XIHAN_PANO = Path("/content/xihan_pano.jpg")
OUT_DIR = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/input_vs_output")

CAM_ORDER_ROW1 = ["cam_01_FRONT.jpg", "cam_02_FRONT_LEFT.jpg", "cam_03_FRONT_RIGHT.jpg",
                  "cam_04_SIDE_LEFT.jpg", "cam_05_SIDE_RIGHT.jpg"]
CAM_ORDER_ROW2 = ["cam_06_REAR_LEFT.jpg", "cam_07_REAR.jpg", "cam_08_REAR_RIGHT.jpg"]


def annotate(img, text, h=44):
    H, W = img.shape[:2]
    bar = np.zeros((h, W, 3), dtype=np.uint8)
    cv2.putText(bar, text, (12, h-14), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (255,255,255), 2, cv2.LINE_AA)
    return np.concatenate([bar, img], axis=0)


def thumb_cam(rgb, target_w):
    H, W = rgb.shape[:2]
    new_h = int(H * target_w / W)
    return cv2.resize(rgb, (target_w, new_h), interpolation=cv2.INTER_AREA)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_W = 380   # per-cam thumb width

    # Load cams
    row1_imgs = []
    for name in CAM_ORDER_ROW1:
        bgr = cv2.imread(str(EXTRACT / name))
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        th = thumb_cam(rgb, THUMB_W)
        row1_imgs.append(annotate(th, name.replace(".jpg",""), h=30))

    row2_imgs = []
    for name in CAM_ORDER_ROW2:
        bgr = cv2.imread(str(EXTRACT / name))
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        th = thumb_cam(rgb, THUMB_W)
        row2_imgs.append(annotate(th, name.replace(".jpg",""), h=30))

    # Pad row2 to same width as row1 (5 vs 3 cams)
    max_h_row1 = max(im.shape[0] for im in row1_imgs)
    max_h_row2 = max(im.shape[0] for im in row2_imgs)
    row1 = np.concatenate([np.pad(im, ((0,max_h_row1-im.shape[0]),(0,0),(0,0))) for im in row1_imgs], axis=1)
    row2 = np.concatenate([np.pad(im, ((0,max_h_row2-im.shape[0]),(0,0),(0,0))) for im in row2_imgs], axis=1)
    # Pad row2 in width to match row1
    if row2.shape[1] < row1.shape[1]:
        row2 = np.pad(row2, ((0,0),(0,row1.shape[1]-row2.shape[1]),(0,0)))
    cams_panel = np.concatenate([row1, row2], axis=0)

    panel_w = cams_panel.shape[1]
    print(f"cam grid: {panel_w}x{cams_panel.shape[0]}")

    # Load Xihan panorama + our output, resize to panel_w
    xihan_bgr = cv2.imread(str(XIHAN_PANO))
    xihan_rgb = cv2.cvtColor(xihan_bgr, cv2.COLOR_BGR2RGB)
    our_bgr = cv2.imread(str(OUR_PNG))
    our_rgb = cv2.cvtColor(our_bgr, cv2.COLOR_BGR2RGB)
    def fit(img, w):
        h = int(img.shape[0] * w / img.shape[1])
        return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    xihan_fit = fit(xihan_rgb, panel_w)
    our_fit = fit(our_rgb, panel_w)

    # Final panel
    final = np.concatenate([
        annotate(cams_panel, "8 Waymo E2ED cam inputs — frame 8e737334...085 (5 narrow 972x1079 + 3 wide 972x551/587)", h=46),
        annotate(xihan_fit, "(B) Xihan distance-to-boundary panorama  — his baseline, mid-cam over-exposed (Y=194)", h=46),
        annotate(our_fit, "(C) Our L1 sphere + 8-cam L2 HDR + multiband  — color shift fixed, uniform brightness", h=46),
    ], axis=0)

    out_path = OUT_DIR / "input_vs_output_panel.png"
    cv2.imwrite(str(out_path), cv2.cvtColor(final, cv2.COLOR_RGB2BGR))
    print(f"wrote {out_path}  ({final.shape[1]}x{final.shape[0]})")

    # Thumb 1400-wide for embedding in markdown
    thumb_w = 1400
    thumb_h = int(final.shape[0] * thumb_w / final.shape[1])
    thumb = cv2.resize(final, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
    thumb_path = OUT_DIR / "input_vs_output_panel_thumb.png"
    cv2.imwrite(str(thumb_path), cv2.cvtColor(thumb, cv2.COLOR_RGB2BGR))
    print(f"wrote {thumb_path}  ({thumb_w}x{thumb_h})")


if __name__ == "__main__":
    main()
