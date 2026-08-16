from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

from .contract import ConversionManifest


DepthMode = Literal["metric", "plane_far"]
PanoramaClass = Literal[
    "production",
    "metric_candidate",
    "scene_band_sky_only",
    "partial_252deg",
    "camera_only_candidate",
]


@dataclass(frozen=True)
class SceneBandPolicy:
    dataset: str
    aliases: tuple[str, ...]
    allowed_manifest_modes: tuple[Literal["A", "B"], ...]
    band_depth_mode: DepthMode
    strict_sync_fraction: float | None
    requires_lidar: bool
    requires_ego_pose: bool
    supported_azimuth_deg: tuple[tuple[float, float], ...]
    honest_black_azimuth_deg: tuple[tuple[float, float], ...]
    full_panorama_class: PanoramaClass
    ground_pipeline: str
    sky_pipeline: str
    annotation_policy: Literal["raw_sensor"] = "raw_sensor"
    gain_per_channel: bool = False
    gain_prior_w: float = 0.05
    gain_strength: float = 1.0
    depth_seamramp_deg: float = 10.546875
    ground_mode: Literal["off"] = "off"
    cap_only: bool = True
    band_torch: bool = True
    emc_render: bool = False
    ego_black: bool = False
    disable_av2_ego_img_mask: bool = True

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["aliases"] = list(self.aliases)
        value["allowed_manifest_modes"] = list(self.allowed_manifest_modes)
        value["supported_azimuth_deg"] = [
            list(interval) for interval in self.supported_azimuth_deg
        ]
        value["honest_black_azimuth_deg"] = [
            list(interval) for interval in self.honest_black_azimuth_deg
        ]
        return value


_POLICIES = (
    SceneBandPolicy(
        dataset="av2",
        aliases=("argoverse2", "argoverse_2"),
        allowed_manifest_modes=("A",),
        band_depth_mode="metric",
        strict_sync_fraction=None,
        requires_lidar=True,
        requires_ego_pose=True,
        supported_azimuth_deg=((0.0, 360.0),),
        honest_black_azimuth_deg=(),
        full_panorama_class="production",
        ground_pipeline="av2_v15_metric",
        sky_pipeline="flux_outpaint",
        ego_black=True,
        disable_av2_ego_img_mask=False,
    ),
    SceneBandPolicy(
        dataset="pandaset",
        aliases=("panda",),
        allowed_manifest_modes=("B",),
        band_depth_mode="plane_far",
        strict_sync_fraction=None,
        requires_lidar=True,
        requires_ego_pose=True,
        supported_azimuth_deg=((0.0, 360.0),),
        honest_black_azimuth_deg=(),
        full_panorama_class="metric_candidate",
        ground_pipeline="dataset_metric_candidate",
        sky_pipeline="flux_outpaint_candidate",
    ),
    SceneBandPolicy(
        dataset="nuscenes",
        aliases=("nusc", "nu_scenes"),
        allowed_manifest_modes=("B",),
        band_depth_mode="plane_far",
        strict_sync_fraction=0.5,
        requires_lidar=True,
        requires_ego_pose=True,
        supported_azimuth_deg=((0.0, 360.0),),
        honest_black_azimuth_deg=(),
        full_panorama_class="scene_band_sky_only",
        ground_pipeline="insufficient_lidar_honest_black",
        sky_pipeline="flux_outpaint_candidate",
    ),
    SceneBandPolicy(
        dataset="waymo_perception",
        aliases=("waymo", "waymo_open", "streetcrafter_waymo"),
        allowed_manifest_modes=("A",),
        band_depth_mode="metric",
        strict_sync_fraction=None,
        requires_lidar=True,
        requires_ego_pose=True,
        supported_azimuth_deg=((0.0, 126.0), (234.0, 360.0)),
        honest_black_azimuth_deg=((126.0, 234.0),),
        full_panorama_class="partial_252deg",
        ground_pipeline="observed_azimuth_metric_only",
        sky_pipeline="observed_azimuth_outpaint_only",
    ),
    SceneBandPolicy(
        dataset="waymo_e2e",
        aliases=("waymo_end_to_end", "waymo_end2end"),
        allowed_manifest_modes=("B",),
        band_depth_mode="plane_far",
        strict_sync_fraction=None,
        requires_lidar=False,
        requires_ego_pose=False,
        supported_azimuth_deg=((0.0, 360.0),),
        honest_black_azimuth_deg=(),
        full_panorama_class="camera_only_candidate",
        ground_pipeline="unsupported_honest_black",
        sky_pipeline="flux_outpaint_candidate",
    ),
)


def _normalize_dataset(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


_POLICY_BY_NAME = {
    _normalize_dataset(name): policy
    for policy in _POLICIES
    for name in (policy.dataset, *policy.aliases)
}


def policy_for_dataset(dataset: str) -> SceneBandPolicy:
    try:
        return _POLICY_BY_NAME[_normalize_dataset(dataset)]
    except KeyError as error:
        supported = ", ".join(policy.dataset for policy in _POLICIES)
        raise ValueError(
            f"unsupported dataset {dataset!r}; supported datasets: {supported}"
        ) from error


def validate_manifest_for_policy(
    manifest: ConversionManifest, policy: SceneBandPolicy
) -> None:
    manifest.validate()
    if manifest.dataset != policy.dataset:
        raise ValueError(
            f"manifest dataset {manifest.dataset!r} does not match policy {policy.dataset!r}"
        )
    if manifest.mode not in policy.allowed_manifest_modes:
        raise ValueError(
            f"manifest mode {manifest.mode!r} is not allowed for {policy.dataset}"
        )
    if manifest.has_lidar is not policy.requires_lidar:
        raise ValueError(
            f"manifest has_lidar={manifest.has_lidar} contradicts {policy.dataset} policy"
        )
    if manifest.has_ego_pose is not policy.requires_ego_pose:
        raise ValueError(
            f"manifest has_ego_pose={manifest.has_ego_pose} contradicts "
            f"{policy.dataset} policy"
        )
    if manifest.supported_azimuth_deg != policy.supported_azimuth_deg:
        raise ValueError(
            "manifest supported azimuth contradicts the dataset evidence boundary"
        )
    if manifest.honest_black_azimuth_deg != policy.honest_black_azimuth_deg:
        raise ValueError(
            "manifest honest-black azimuth contradicts the dataset evidence boundary"
        )


def _python_literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if value is True:
        return "True"
    if value is False:
        return "False"
    return repr(value)


def _replace_assignment_once(source: str, name: str, value: object) -> str:
    pattern = re.compile(
        rf"^(?P<prefix>\s*{re.escape(name)}\s*=\s*)"
        rf"(?P<value>[^#\r\n]*?)(?P<suffix>\s*(?:#.*)?)$",
        re.MULTILINE,
    )
    matches = tuple(pattern.finditer(source))
    if not matches:
        raise ValueError(f"missing DB89 assignment for {name}")
    if len(matches) != 1:
        raise ValueError(f"duplicate DB89 assignment for {name}: {len(matches)}")
    return pattern.sub(
        lambda match: (
            match.group("prefix") + _python_literal(value) + match.group("suffix")
        ),
        source,
        count=1,
    )


def apply_policy_to_db89_source(source: str, policy: SceneBandPolicy) -> str:
    assignments: tuple[tuple[str, object], ...] = (
        ("GROUND_MODE", policy.ground_mode),
        ("ANNOTATION_POLICY", policy.annotation_policy),
        ("GAIN_PER_CHANNEL", policy.gain_per_channel),
        ("GAIN_PRIOR_W", policy.gain_prior_w),
        ("GAIN_STRENGTH", policy.gain_strength),
        ("DEPTH_SEAMRAMP_DEG", policy.depth_seamramp_deg),
        ("BAND_DEPTH_MODE", policy.band_depth_mode),
        ("CAP_ONLY", policy.cap_only),
        ("BAND_TORCH", policy.band_torch),
        ("EMC_RENDER", policy.emc_render),
        ("EGO_BLACK", policy.ego_black),
    )
    if policy.disable_av2_ego_img_mask:
        assignments += (("EGO_IMG_MASK", ""),)
    rendered = source
    for name, value in assignments:
        rendered = _replace_assignment_once(rendered, name, value)
    return rendered


__all__ = [
    "SceneBandPolicy",
    "apply_policy_to_db89_source",
    "policy_for_dataset",
    "validate_manifest_for_policy",
]
