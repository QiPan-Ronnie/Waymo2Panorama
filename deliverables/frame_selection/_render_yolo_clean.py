"""Render the 3 anchors YOLO v2 says are truly ghost-free, plus 3 ghostiest, for visual."""
import sys
sys.path.insert(0, "/content/waymo2panorama/code")
import numpy as np
from pathlib import Path
from PIL import Image
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.multiband import multiband_blend

LOG = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-3857-444e-8db3-a8f58489c394")
out_dir = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/ghost_scoring_yolo_v2/02a00399/visual_check")
out_dir.mkdir(parents=True, exist_ok=True)

CLEAN = [105, 200, 210]
GHOSTY = [20, 130, 290]  # top 3 most edge-objects (6 each)

loader = AV2RingLoader(LOG)
ts_all = loader.anchor_timestamps_ns()

for label, anchors in [("clean", CLEAN), ("ghosty", GHOSTY)]:
    for a in anchors:
        ts = ts_all[a]
        frame = loader.load_synced_frame(ts)
        slabs, weights = [], []
        for cam in RING_CAMS_7:
            calib = frame.calibrations[cam]
            rgb, _alpha, w = render_camera_to_erp(
                image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
                erp_hw=(1024, 2048), convergence_distance_m=None,
            )
            slabs.append(rgb); weights.append(w)
        erp = multiband_blend(slabs, weights, num_bands=5, wrap=True)
        # Thumb
        im = Image.fromarray(erp).copy()
        im.thumbnail((1024, 512))
        im.save(out_dir / f"{label}_anchor{a:03d}_thumb.png")
        print(f"saved {label}_anchor{a:03d}_thumb.png")
