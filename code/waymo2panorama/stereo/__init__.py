"""Stereo modules for cross-camera depth estimation (Phase 3 route 13 / 新-D)."""
from .wide_baseline_stereo import (  # noqa: F401
    ADJACENT_PAIRS,
    StereoMatchResult,
    compute_F_from_known_T,
    epipolar_ransac_filter,
    extract_pair_features,
    match_with_lightglue,
    process_anchor_all_pairs,
    process_cam_pair,
    triangulate_sparse,
)
