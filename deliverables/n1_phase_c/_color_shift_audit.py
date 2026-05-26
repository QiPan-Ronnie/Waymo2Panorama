"""5.22 prompt §1b — AV2 color shift audit.

Question: does AV2 ring-cam data have the same "shadow car (half-dark / half-light)"
phenomenon Xihan saw on Waymo? Quick test: compute per-cam median luminance
on each of 5 val logs (anchor 0). High cross-cam luminance gap = exposure
mismatch = likely shadow-car artifact in stitched output.
"""
import sys, json
sys.path.insert(0, "/content/waymo2panorama/code")
import numpy as np
from pathlib import Path
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

LOGS = [
    "02a00399-3857-444e-8db3-a8f58489c394",
    "0bae3b5e-417d-3b03-abaa-806b433233b8",
    "2c652f9e-8db8-3572-aa49-fae1344a875b",
    "9f871fb4-3b8e-34b3-9161-ed961e71a6da",
    "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
]

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
results = []

for log_id in LOGS:
    log_dir = ROOT / log_id
    loader = AV2RingLoader(log_dir)
    ts_all = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts_all[0])

    per_cam_lum = {}
    for cam in RING_CAMS_7:
        img = frame.images[cam]
        # BT.601 luma
        gray = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
        gray = gray.astype(np.float32)
        # Skip pure black borders (letterbox) if any
        nonzero = gray[gray > 5]
        if nonzero.size == 0:
            per_cam_lum[cam] = float("nan")
            continue
        per_cam_lum[cam] = float(np.median(nonzero))

    vals = [v for v in per_cam_lum.values() if v == v]
    lum_min = min(vals)
    lum_max = max(vals)
    lum_gap_db = 20.0 * np.log10(max(lum_max / max(lum_min, 1e-6), 1.0))

    # Find which cam is darkest and which is brightest
    min_cam = min(per_cam_lum, key=lambda c: per_cam_lum[c])
    max_cam = max(per_cam_lum, key=lambda c: per_cam_lum[c])

    print(f"\\n{log_id[:8]}:")
    cam_strs = ", ".join(f"{c[5:]}={per_cam_lum[c]:.0f}" for c in RING_CAMS_7)
    print(f"  cam medians: {cam_strs}")
    print(f"  min={min_cam[5:]}={lum_min:.0f}, max={max_cam[5:]}={lum_max:.0f}, gap={lum_gap_db:.2f} dB")

    results.append({
        "log_id": log_id[:8],
        "per_cam_lum_median": per_cam_lum,
        "lum_min": lum_min,
        "lum_max": lum_max,
        "lum_gap_db": float(lum_gap_db),
        "darkest_cam": min_cam,
        "brightest_cam": max_cam,
    })

# Save summary
out_path = Path("/content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/color_shift_audit/audit.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w") as f:
    json.dump(results, f, indent=2)

print(f"\\n\\nSummary across 5 val logs:")
gaps = [r["lum_gap_db"] for r in results]
print(f"  lum_gap_db range: {min(gaps):.2f} - {max(gaps):.2f}")
print(f"  mean: {np.mean(gaps):.2f} dB")
print(f"  median: {np.median(gaps):.2f} dB")
print(f"\\nAV2 has cross-cam exposure imbalance (HDR adapter motivated): "
      f"{'YES (>3dB on >50% logs)' if sum(g > 3 for g in gaps) >= 3 else 'MILD'}")
