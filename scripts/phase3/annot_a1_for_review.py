import cv2, numpy as np, shutil, os
from pathlib import Path
L = Path(r"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\deliverables\a1_streetview_pipeline")
MEET = r"D:\BaiduSyncdisk\2024 to future\koi chen\meeting"

def annot(src, marks, dst):
    im = cv2.imread(str(L / src)); H, W = im.shape[:2]
    for fx, fy, txt in marks:
        x, y = int(fx * W), int(fy * H)
        cv2.ellipse(im, (x, y), (75, 48), 0, 0, 360, (0, 0, 255), 4)
        cv2.putText(im, txt, (max(2, x - 95), max(22, y - 56)), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 255), 2)
    cv2.imwrite(os.path.join(MEET, dst), im, [cv2.IMWRITE_JPEG_QUALITY, 93]); print("wrote", dst, (W, H))

annot("A1_flow_seam_crops.jpg", [(0.24, 0.66, "FLOW black hole"), (0.70, 0.71, "FLOW tear (wall corner)")], "ANNOT_A1_flow_seamcrops.jpg")
annot("A1_core_seam_crops.jpg", [(0.27, 0.67, "sign ghost"), (0.77, 0.69, "blend artifact (face)")], "ANNOT_A1_core_seamcrops.jpg")
for f in ["A1_flow_L1_vs_result.jpg", "A1_core_L1_vs_result.jpg", "A1_flow_seam_crops.jpg", "A1_core_seam_crops.jpg"]:
    shutil.copy(L / f, os.path.join(MEET, f))
print("copied raw figs to meeting/")
