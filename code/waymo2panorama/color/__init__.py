"""Cross-camera color compensation (HDR / exposure / WB).

See `hdr_gain_estimate.py` for the global LS gain+bias estimator (route 14 / 新-E).
"""

from waymo2panorama.color.hdr_gain_estimate import (
    apply_correction,
    extract_overlap_pixels,
    global_color_correction,
)

__all__ = [
    "apply_correction",
    "extract_overlap_pixels",
    "global_color_correction",
]
