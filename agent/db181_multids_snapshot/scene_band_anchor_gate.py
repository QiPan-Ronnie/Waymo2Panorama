from __future__ import annotations

from .contract import ConversionManifest
from .nuscenes_strict_sync import strict_sync_report
from .scene_band_policy import policy_for_dataset, validate_manifest_for_policy


def validate_requested_anchors(
    manifest: ConversionManifest, requested: tuple[int, ...]
) -> tuple[int, ...]:
    policy = policy_for_dataset(manifest.dataset)
    validate_manifest_for_policy(manifest, policy)
    if not requested:
        raise ValueError("at least one anchor is required")
    if len(set(requested)) != len(requested):
        raise ValueError("requested anchors must be unique")
    outside = sorted(
        anchor
        for anchor in requested
        if isinstance(anchor, bool)
        or not isinstance(anchor, int)
        or anchor < 0
        or anchor >= manifest.output_frame_count
    )
    if outside:
        raise ValueError(f"requested anchors outside manifest frame range: {outside}")
    if policy.strict_sync_fraction is not None:
        report = strict_sync_report(
            manifest.frames,
            manifest.cameras,
            fraction=policy.strict_sync_fraction,
            include_lidar=manifest.has_lidar,
        )
        accepted = set(report.accepted_indices)
        rejected = sorted(anchor for anchor in requested if anchor not in accepted)
        if rejected:
            raise ValueError(f"strict sync rejected requested anchors: {rejected}")
    return requested


__all__ = ["validate_requested_anchors"]
