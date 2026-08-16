from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .contract import ConversionManifest
from .scene_band_policy import (
    SceneBandPolicy,
    policy_for_dataset,
    validate_manifest_for_policy,
)


@dataclass(frozen=True)
class SceneBandWorkerConfig:
    dataset: str
    source_scene_id: str
    output_log_id: str
    camera_order: tuple[str, ...]
    output_frame_count: int
    strict_sync_fraction: float | None
    supported_azimuth_deg: tuple[tuple[float, float], ...]
    honest_black_azimuth_deg: tuple[tuple[float, float], ...]
    full_panorama_class: str
    ground_pipeline: str
    sky_pipeline: str
    policy: SceneBandPolicy

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "dataset": self.dataset,
            "source_scene_id": self.source_scene_id,
            "output_log_id": self.output_log_id,
            "camera_order": list(self.camera_order),
            "output_frame_count": self.output_frame_count,
            "strict_sync_fraction": self.strict_sync_fraction,
            "supported_azimuth_deg": [
                list(interval) for interval in self.supported_azimuth_deg
            ],
            "honest_black_azimuth_deg": [
                list(interval) for interval in self.honest_black_azimuth_deg
            ],
            "full_panorama_class": self.full_panorama_class,
            "ground_pipeline": self.ground_pipeline,
            "sky_pipeline": self.sky_pipeline,
            "policy": self.policy.to_dict(),
        }


def build_scene_band_worker_config(
    manifest: ConversionManifest,
) -> SceneBandWorkerConfig:
    policy = policy_for_dataset(manifest.dataset)
    validate_manifest_for_policy(manifest, policy)
    return SceneBandWorkerConfig(
        dataset=manifest.dataset,
        source_scene_id=manifest.source_scene_id,
        output_log_id=manifest.output_log_id,
        camera_order=manifest.cameras,
        output_frame_count=manifest.output_frame_count,
        strict_sync_fraction=policy.strict_sync_fraction,
        supported_azimuth_deg=policy.supported_azimuth_deg,
        honest_black_azimuth_deg=policy.honest_black_azimuth_deg,
        full_panorama_class=policy.full_panorama_class,
        ground_pipeline=policy.ground_pipeline,
        sky_pipeline=policy.sky_pipeline,
        policy=policy,
    )


def write_scene_band_worker_config(
    manifest: ConversionManifest, destination: Path
) -> SceneBandWorkerConfig:
    config = build_scene_band_worker_config(manifest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return config


__all__ = [
    "SceneBandWorkerConfig",
    "build_scene_band_worker_config",
    "write_scene_band_worker_config",
]
