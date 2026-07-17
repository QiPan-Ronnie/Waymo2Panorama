from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

import cv2
import numpy as np
import pandas as pd
from scipy.spatial.transform import Rotation, Slerp

from .baseline import BaselineResult, six_slot_median
from .config import DEFAULT_CONFIG, ExperimentConfig
from .geometry import camera_rays, intersect_rays_with_plane, project_city_to_image
from .observability import HeldoutSplit, PatchObservability, select_heldout_groups
from .operator import EWAObservationSet

if TYPE_CHECKING:
    from waymo2panorama.data_io.av2_loader import AV2RingLoader

RING_CAMS_7: tuple[str, ...] = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


SOURCE_FRAME_STEP = 4
COARSE_FRAME_STEP = 8
LIDAR_FRAME_STEP = 4
PATCH_LATERAL_OFFSETS_M = (-2.0, -1.0, 0.0, 1.0, 2.0)
# DB-123 v8-fine analytic AV2 fleet-body mask.  These are ego-frame vehicle
# constants, shared by every log; over-rejection is intentionally safer than
# admitting specular hood/roof pixels as "ground evidence".
EGO_BOXES = (
    (np.array([-1.25, -1.05, -0.45]), np.array([3.60, 1.05, 0.60])),
    (np.array([-1.00, -0.78, 0.60]), np.array([1.30, 0.78, 1.10])),
)
EGO_MASK_DILATE_QUARTER_PX = 5
EGO_NEAR_LIMIT_M = 8.0


@dataclass(frozen=True)
class GroundPatch:
    patch_id: str
    center_xy: tuple[float, float]
    plane_n: tuple[float, float, float]
    plane_d: float
    plane_rmse_m: float
    anchor_frame_idx: int

    def xyz_from_xy(self, xy: np.ndarray) -> np.ndarray:
        points = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        n = np.asarray(self.plane_n, dtype=np.float64)
        if abs(n[2]) < 0.5:
            raise ValueError("local ground plane is too vertical")
        z = -(points @ n[:2] + self.plane_d) / n[2]
        return np.c_[points, z]


@dataclass(frozen=True)
class SourceView:
    source_id: int
    frame_idx: int
    camera_idx: int
    camera_name: str
    timestamp_ns: int
    image_path: Path
    K: np.ndarray
    T_city_cam: np.ndarray
    image_width: int
    image_height: int

    @property
    def group_id(self) -> str:
        return f"f{self.frame_idx:03d}:{self.camera_name}"


@dataclass(frozen=True)
class Box3D:
    center_city: np.ndarray
    size_lwh: np.ndarray
    R_city_box: np.ndarray
    category: str


@dataclass(frozen=True)
class ObservationArrays:
    centers_cell: np.ndarray
    covariance_cell: np.ndarray
    source_ids: np.ndarray
    rgb: np.ndarray
    provenance: dict[str, np.ndarray]

    def build_operator(
        self,
        *,
        grid_hw: tuple[int, int],
        config: ExperimentConfig,
        device: str,
    ) -> EWAObservationSet:
        return EWAObservationSet.from_numpy(
            centers_cell=self.centers_cell,
            covariance_cell=self.covariance_cell,
            source_ids=self.source_ids,
            rgb=self.rgb,
            grid_hw=grid_hw,
            support_sigma=config.pixel_support_sigma,
            pose_shift_limit_cell=config.pose_shift_limit_cell,
            provenance=self.provenance,
            pair_chunk_size=config.pair_chunk_size,
            device=device,
        )


@dataclass(frozen=True)
class PatchExtraction:
    train_observations: ObservationArrays
    heldout_observations: ObservationArrays
    baseline: BaselineResult
    training_groups: tuple[str, ...]
    heldout_groups: tuple[str, ...]
    diagnostics: dict[str, object]


class PoseTable:
    def __init__(self, pose_frame: pd.DataFrame):
        frame = pose_frame.sort_values("timestamp_ns").drop_duplicates("timestamp_ns")
        self.timestamp_ns = frame["timestamp_ns"].to_numpy(np.int64)
        self.t0 = int(self.timestamp_ns[0])
        self.seconds = (self.timestamp_ns - self.t0).astype(np.float64)
        self.translation = frame[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
        quaternion = frame[["qx", "qy", "qz", "qw"]].to_numpy(np.float64)
        self.rotation = Rotation.from_quat(quaternion)
        self.slerp = Slerp(self.seconds, self.rotation)

    @classmethod
    def from_log(cls, log_dir: Path) -> "PoseTable":
        return cls(pd.read_feather(Path(log_dir) / "city_SE3_egovehicle.feather"))

    def at(self, timestamp_ns: int | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        timestamp = np.asarray(timestamp_ns, dtype=np.int64)
        one = timestamp.ndim == 0
        query = np.atleast_1d(timestamp)
        seconds = np.clip(
            (query - self.t0).astype(np.float64), self.seconds[0], self.seconds[-1]
        )
        rotation = self.slerp(seconds).as_matrix()
        translation = np.column_stack(
            [np.interp(seconds, self.seconds, self.translation[:, axis]) for axis in range(3)]
        )
        return (rotation[0], translation[0]) if one else (rotation, translation)

    def matrix_at(self, timestamp_ns: int) -> np.ndarray:
        rotation, translation = self.at(timestamp_ns)
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] = rotation
        matrix[:3, 3] = translation
        return matrix


class AnnotationIndex:
    def __init__(self, path: Path):
        self.frame = pd.read_feather(path) if path.exists() else pd.DataFrame()
        self.timestamps = (
            np.sort(self.frame["timestamp_ns"].unique()).astype(np.int64)
            if not self.frame.empty
            else np.empty(0, np.int64)
        )
        self._cache: dict[int, tuple[Box3D, ...]] = {}

    def boxes_at(self, timestamp_ns: int) -> tuple[Box3D, ...]:
        if not len(self.timestamps):
            return ()
        nearest = int(self.timestamps[np.argmin(np.abs(self.timestamps - timestamp_ns))])
        if nearest in self._cache:
            return self._cache[nearest]
        boxes: list[Box3D] = []
        for _, row in self.frame[self.frame["timestamp_ns"] == nearest].iterrows():
            q = [row["qx"], row["qy"], row["qz"], row["qw"]]
            boxes.append(
                Box3D(
                    center_city=np.array([row["tx_m"], row["ty_m"], row["tz_m"]], np.float64),
                    size_lwh=np.array(
                        [row["length_m"], row["width_m"], row["height_m"]], np.float64
                    ),
                    R_city_box=Rotation.from_quat(q).as_matrix(),
                    category=str(row.get("category", "unknown")),
                )
            )
        self._cache[nearest] = tuple(boxes)
        return self._cache[nearest]


def build_analytic_ego_masks(loader: AV2RingLoader) -> dict[str, np.ndarray]:
    """Build DB-123's conservative quarter-resolution ego masks from calibration."""

    masks: dict[str, np.ndarray] = {}
    for camera in RING_CAMS_7:
        calibration = loader.calibration(camera)
        K = calibration.K
        T_ego_cam = calibration.T_ego_cam
        rotation = T_ego_cam[:3, :3]
        origin = T_ego_cam[:3, 3]
        height = calibration.image_height // 4
        width = calibration.image_width // 4
        uu, vv = np.meshgrid(
            (np.arange(width) * 4 + 2).astype(np.float64),
            (np.arange(height) * 4 + 2).astype(np.float64),
        )
        rays_camera = np.stack(
            (
                (uu - K[0, 2]) / K[0, 0],
                (vv - K[1, 2]) / K[1, 1],
                np.ones_like(uu),
            ),
            axis=-1,
        )
        rays_ego = rays_camera @ rotation.T
        mask = np.zeros((height, width), bool)
        for lower, upper in EGO_BOXES:
            with np.errstate(divide="ignore", invalid="ignore"):
                t1 = (lower - origin) / rays_ego
                t2 = (upper - origin) / rays_ego
            t_min = np.nanmax(np.minimum(t1, t2), axis=-1)
            t_max = np.nanmin(np.maximum(t1, t2), axis=-1)
            mask |= (
                (t_max >= np.maximum(t_min, 0.0))
                & (t_max > 0.0)
                & (t_min < EGO_NEAR_LIMIT_M)
            )
        mask = cv2.dilate(
            mask.astype(np.uint8),
            np.ones(
                (EGO_MASK_DILATE_QUARTER_PX, EGO_MASK_DILATE_QUARTER_PX),
                np.uint8,
            ),
        ).astype(bool)
        masks[camera] = mask
    return masks


def _ego_pixels(mask_quarter: np.ndarray, uv: np.ndarray) -> np.ndarray:
    points = np.nan_to_num(np.asarray(uv), nan=0.0, posinf=0.0, neginf=0.0)
    x = np.clip((points[:, 0] // 4).astype(int), 0, mask_quarter.shape[1] - 1)
    y = np.clip((points[:, 1] // 4).astype(int), 0, mask_quarter.shape[0] - 1)
    return mask_quarter[y, x]


def build_source_views(
    log_dir: Path,
    window: tuple[int, int],
    *,
    frame_step: int = SOURCE_FRAME_STEP,
) -> tuple[list[SourceView], PoseTable, AV2RingLoader]:
    from waymo2panorama.data_io.av2_loader import AV2RingLoader

    log_dir = Path(log_dir)
    loader = AV2RingLoader(log_dir)
    poses = PoseTable.from_log(log_dir)
    anchors = np.asarray(loader.anchor_timestamps_ns(), dtype=np.int64)
    start, stop = window
    if start < 0 or stop >= len(anchors) or start > stop:
        raise ValueError(f"window {window} outside {len(anchors)} anchors")
    frame_indices = list(range(start, stop + 1, frame_step))
    if frame_indices[-1] != stop:
        frame_indices.append(stop)
    views: list[SourceView] = []
    for frame_idx in frame_indices:
        anchor = int(anchors[frame_idx])
        for camera_idx, camera in enumerate(RING_CAMS_7):
            paths = loader._image_paths[camera]  # stable, local project loader index
            timestamps = np.asarray([int(path.stem) for path in paths], np.int64)
            nearest = int(np.argmin(np.abs(timestamps - anchor)))
            timestamp = int(timestamps[nearest])
            calibration = loader.calibration(camera)
            T_city_cam = poses.matrix_at(timestamp) @ calibration.T_ego_cam
            views.append(
                SourceView(
                    source_id=len(views),
                    frame_idx=frame_idx,
                    camera_idx=camera_idx,
                    camera_name=camera,
                    timestamp_ns=timestamp,
                    image_path=paths[nearest],
                    K=calibration.K,
                    T_city_cam=T_city_cam,
                    image_width=calibration.image_width,
                    image_height=calibration.image_height,
                )
            )
    return views, poses, loader


def _window_holdout_frames(window: tuple[int, int]) -> set[int]:
    start, stop = window
    length = stop - start + 1
    heldout_length = max(1, int(round(0.20 * length)))
    heldout_start = start + (length - heldout_length) // 2
    return set(range(heldout_start, heldout_start + heldout_length))


def _patch_bounds(patch: GroundPatch, config: ExperimentConfig) -> tuple[np.ndarray, np.ndarray]:
    centre = np.asarray(patch.center_xy, np.float64)
    half = config.patch_size_m / 2.0
    return centre - half, centre + half


def _grid_xyz(patch: GroundPatch, config: ExperimentConfig) -> np.ndarray:
    lower, _ = _patch_bounds(patch, config)
    coordinate = (np.arange(config.grid_hw) + 0.5) * config.cell_m
    xx, yy = np.meshgrid(lower[0] + coordinate, lower[1] + coordinate)
    return patch.xyz_from_xy(np.column_stack((xx.ravel(), yy.ravel())))


def _bilinear_rgb(image_rgb: np.ndarray, uv: np.ndarray) -> np.ndarray:
    points = np.asarray(uv, np.float32)
    values = cv2.remap(
        image_rgb.astype(np.float32) / 255.0,
        points[:, 0].reshape(-1, 1),
        points[:, 1].reshape(-1, 1),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )
    return values[:, 0]


def _occluded_by_boxes(
    origin_city: np.ndarray,
    hit_city: np.ndarray,
    boxes: Sequence[Box3D],
    *,
    padding_m: float = 0.20,
) -> np.ndarray:
    """Segment-vs-OBB test; true means an annotation blocks the ground hit."""

    hits = np.asarray(hit_city, np.float64).reshape(-1, 3)
    occluded = np.zeros(len(hits), bool)
    for box in boxes:
        if np.linalg.norm(box.center_city[:2] - np.median(hits[:, :2], axis=0)) > 50.0:
            continue
        origin_local = (np.asarray(origin_city) - box.center_city) @ box.R_city_box
        hit_local = (hits - box.center_city) @ box.R_city_box
        direction = hit_local - origin_local
        half = box.size_lwh / 2.0 + padding_m
        t_min = np.zeros(len(hits), np.float64)
        t_max = np.ones(len(hits), np.float64)
        possible = np.ones(len(hits), bool)
        for axis in range(3):
            d = direction[:, axis]
            parallel = np.abs(d) < 1.0e-10
            possible &= ~(parallel & (abs(origin_local[axis]) > half[axis]))
            inv = np.zeros_like(d)
            np.divide(1.0, d, out=inv, where=~parallel)
            ta = (-half[axis] - origin_local[axis]) * inv
            tb = (half[axis] - origin_local[axis]) * inv
            near = np.minimum(ta, tb)
            far = np.maximum(ta, tb)
            t_min = np.maximum(t_min, np.where(parallel, 0.0, near))
            t_max = np.minimum(t_max, np.where(parallel, 1.0, far))
        occluded |= possible & (t_max >= t_min) & (t_max > 0.0) & (t_min < 0.999)
    return occluded


def _vectorized_footprints(
    uv: np.ndarray,
    view: SourceView,
    patch: GroundPatch,
    config: ExperimentConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return centre hits, covariance, area, aspect, range and validity."""

    points = np.asarray(uv, np.float64).reshape(-1, 2)
    offsets = np.array(
        [[0.0, 0.0], [-0.5, 0.0], [0.5, 0.0], [0.0, -0.5], [0.0, 0.5]]
    )
    probes = points[:, None, :] + offsets[None]
    rays_camera = camera_rays(probes.reshape(-1, 2), view.K).reshape(len(points), 5, 3)
    rotation = view.T_city_cam[:3, :3]
    rays_city = np.einsum("ij,nkj->nki", rotation, rays_camera)
    origin = view.T_city_cam[:3, 3]
    hits_flat, valid_flat = intersect_rays_with_plane(
        np.broadcast_to(origin, (len(points) * 5, 3)),
        rays_city.reshape(-1, 3),
        patch.plane_n,
        patch.plane_d,
    )
    hits = hits_flat.reshape(len(points), 5, 3)
    valid = valid_flat.reshape(len(points), 5).all(axis=1)
    J = np.stack(
        (hits[:, 2, :2] - hits[:, 1, :2], hits[:, 4, :2] - hits[:, 3, :2]),
        axis=-1,
    )
    covariance = np.einsum("nij,nkj->nik", J, J) / 12.0
    covariance += (config.cell_m**2 / 12.0) * np.eye(2)[None]
    eigenvalues = np.linalg.eigvalsh(covariance)
    positive = np.isfinite(eigenvalues).all(axis=1) & (eigenvalues[:, 0] > 0.0)
    area = np.pi * np.sqrt(np.maximum(0.0, eigenvalues.prod(axis=1)))
    aspect = np.sqrt(
        np.divide(
            eigenvalues[:, 1],
            eigenvalues[:, 0],
            out=np.full(len(points), np.inf),
            where=eigenvalues[:, 0] > 0,
        )
    )
    ranges = np.linalg.norm(hits[:, 0] - origin, axis=1)
    lower, upper = _patch_bounds(patch, config)
    inside = (
        (hits[:, 0, 0] >= lower[0])
        & (hits[:, 0, 0] < upper[0])
        & (hits[:, 0, 1] >= lower[1])
        & (hits[:, 0, 1] < upper[1])
    )
    valid &= (
        positive
        & inside
        & (ranges >= config.min_source_range_m)
        & (ranges <= config.max_source_range_m)
        & (area <= config.max_footprint_area_m2)
        & (aspect <= config.max_footprint_aspect)
    )
    return hits[:, 0], covariance, area, aspect, ranges, valid


def _patch_image_bbox(
    patch: GroundPatch, view: SourceView, config: ExperimentConfig
) -> tuple[int, int, int, int] | None:
    lower, upper = _patch_bounds(patch, config)
    corners_xy = np.array(
        [
            [lower[0], lower[1]],
            [lower[0], upper[1]],
            [upper[0], lower[1]],
            [upper[0], upper[1]],
            [(lower[0] + upper[0]) / 2, (lower[1] + upper[1]) / 2],
        ]
    )
    uv, valid = project_city_to_image(
        patch.xyz_from_xy(corners_xy), view.K, view.T_city_cam
    )
    if not valid.any():
        return None
    visible = uv[valid]
    u0 = max(0, int(np.floor(visible[:, 0].min())) - 2)
    u1 = min(view.image_width - 1, int(np.ceil(visible[:, 0].max())) + 2)
    v0 = max(0, int(np.floor(visible[:, 1].min())) - 2)
    v1 = min(view.image_height - 1, int(np.ceil(visible[:, 1].max())) + 2)
    return (u0, v0, u1, v1) if u0 <= u1 and v0 <= v1 else None


def _raw_pixels_for_view(
    image_rgb: np.ndarray,
    patch: GroundPatch,
    view: SourceView,
    boxes: Sequence[Box3D],
    config: ExperimentConfig,
    ego_mask: np.ndarray | None = None,
) -> dict[str, np.ndarray] | None:
    bbox = _patch_image_bbox(patch, view, config)
    if bbox is None:
        return None
    u0, v0, u1, v1 = bbox
    yy, xx = np.mgrid[v0 : v1 + 1, u0 : u1 + 1]
    uv = np.column_stack((xx.ravel(), yy.ravel())).astype(np.float64)
    hits, covariance, area, aspect, ranges, valid = _vectorized_footprints(
        uv, view, patch, config
    )
    rgb_u8 = image_rgb[uv[:, 1].astype(int), uv[:, 0].astype(int)]
    radiometric = ~(
        (rgb_u8 <= 3).all(axis=1) | (rgb_u8 >= 252).all(axis=1)
    )
    valid &= radiometric
    if ego_mask is not None:
        valid &= ~_ego_pixels(ego_mask, uv)
    if valid.any() and boxes:
        candidate = np.flatnonzero(valid)
        blocked = _occluded_by_boxes(
            view.T_city_cam[:3, 3], hits[candidate], boxes
        )
        valid[candidate[blocked]] = False
    if not valid.any():
        return None
    keep = np.flatnonzero(valid)
    lower, _ = _patch_bounds(patch, config)
    centres = (hits[keep, :2] - lower) / config.cell_m - 0.5
    return {
        "centers_cell": centres.astype(np.float32),
        "covariance_cell": (covariance[keep] / config.cell_m**2).astype(np.float32),
        "rgb": (rgb_u8[keep].astype(np.float32) / 255.0),
        "u": uv[keep, 0].astype(np.int32),
        "v": uv[keep, 1].astype(np.int32),
        "area_m2": area[keep].astype(np.float32),
        "aspect": aspect[keep].astype(np.float32),
        "range_m": ranges[keep].astype(np.float32),
    }


def _baseline_samples_for_view(
    image_rgb: np.ndarray,
    grid_xyz: np.ndarray,
    view: SourceView,
    boxes: Sequence[Box3D],
    config: ExperimentConfig,
    ego_mask: np.ndarray | None = None,
) -> dict[str, np.ndarray] | None:
    uv, valid = project_city_to_image(grid_xyz, view.K, view.T_city_cam)
    origin = view.T_city_cam[:3, 3]
    ranges = np.linalg.norm(grid_xyz - origin, axis=1)
    valid &= (
        (uv[:, 0] >= 0.5)
        & (uv[:, 0] <= view.image_width - 1.5)
        & (uv[:, 1] >= 0.5)
        & (uv[:, 1] <= view.image_height - 1.5)
        & (ranges >= config.min_source_range_m)
        & (ranges <= config.max_source_range_m)
    )
    if ego_mask is not None:
        valid &= ~_ego_pixels(ego_mask, uv)
    if valid.any() and boxes:
        candidate = np.flatnonzero(valid)
        blocked = _occluded_by_boxes(origin, grid_xyz[candidate], boxes)
        valid[candidate[blocked]] = False
    if not valid.any():
        return None
    texel = np.flatnonzero(valid)
    rgb = _bilinear_rgb(image_rgb, uv[valid])
    radiometric = ~((rgb <= 3 / 255).all(axis=1) | (rgb >= 252 / 255).all(axis=1))
    return {
        "texel_ids": texel[radiometric].astype(np.int64),
        "rgb": rgb[radiometric].astype(np.float32),
        "range_m": ranges[texel[radiometric]].astype(np.float32),
    }


def _concat_records(
    records: list[dict[str, np.ndarray]],
    original_sources: list[int],
    views_by_id: dict[int, SourceView],
) -> ObservationArrays:
    if not records:
        raise ValueError("no sensor-native observations survived")
    source_map = {source: index for index, source in enumerate(sorted(set(original_sources)))}
    source_ids = np.concatenate(
        [
            np.full(len(record["rgb"]), source_map[source], np.int64)
            for record, source in zip(records, original_sources, strict=True)
        ]
    )
    original = np.concatenate(
        [
            np.full(len(record["rgb"]), source, np.int64)
            for record, source in zip(records, original_sources, strict=True)
        ]
    )
    provenance: dict[str, np.ndarray] = {
        "original_source_id": original,
        "frame_idx": np.concatenate(
            [
                np.full(len(record["rgb"]), views_by_id[source].frame_idx, np.int32)
                for record, source in zip(records, original_sources, strict=True)
            ]
        ),
        "camera_idx": np.concatenate(
            [
                np.full(len(record["rgb"]), views_by_id[source].camera_idx, np.int16)
                for record, source in zip(records, original_sources, strict=True)
            ]
        ),
    }
    for key in ("u", "v", "area_m2", "aspect", "range_m"):
        provenance[key] = np.concatenate([record[key] for record in records])
    return ObservationArrays(
        centers_cell=np.concatenate([record["centers_cell"] for record in records]),
        covariance_cell=np.concatenate([record["covariance_cell"] for record in records]),
        source_ids=source_ids,
        rgb=np.concatenate([record["rgb"] for record in records]),
        provenance=provenance,
    )


def extract_patch(
    log_dir: Path,
    patch: GroundPatch,
    window: tuple[int, int],
    *,
    config: ExperimentConfig = DEFAULT_CONFIG,
    device: str = "cuda",
    progress: bool = True,
    training_groups: Sequence[str] | None = None,
    heldout_groups: Sequence[str] | None = None,
) -> PatchExtraction:
    """Extract A/B/C evidence directly from raw AV2 pixels for one frozen patch."""

    views, _, loader_for_masks = build_source_views(log_dir, window)
    # The mask is derived from fleet geometry and calibration only.  It never
    # sees reconstruction output, so applying it cannot leak the held-out view.
    ego_masks = build_analytic_ego_masks(loader_for_masks)
    views_by_id = {view.source_id: view for view in views}
    annotations = AnnotationIndex(Path(log_dir) / "annotations.feather")
    heldout_frames = _window_holdout_frames(window)
    if (training_groups is None) != (heldout_groups is None):
        raise ValueError("training_groups and heldout_groups must be supplied together")
    frozen_training = set(training_groups or ())
    frozen_heldout = set(heldout_groups or ())
    if frozen_training & frozen_heldout:
        raise ValueError("frozen training and held-out groups overlap")
    grid_xyz = _grid_xyz(patch, config)
    train_records: list[dict[str, np.ndarray]] = []
    train_sources: list[int] = []
    heldout_records: list[dict[str, np.ndarray]] = []
    heldout_sources: list[int] = []
    baseline_records: list[dict[str, np.ndarray]] = []
    baseline_sources: list[int] = []
    training_groups: list[str] = []
    heldout_groups: list[str] = []

    for index, view in enumerate(views):
        if frozen_training or frozen_heldout:
            if view.group_id not in frozen_training | frozen_heldout:
                continue
            heldout = view.group_id in frozen_heldout
        else:
            heldout = view.frame_idx in heldout_frames
        image_bgr = cv2.imread(str(view.image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise FileNotFoundError(view.image_path)
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        boxes = annotations.boxes_at(view.timestamp_ns)
        ego_mask = ego_masks[view.camera_name]
        raw = _raw_pixels_for_view(
            image_rgb, patch, view, boxes, config, ego_mask=ego_mask
        )
        if raw is not None:
            if heldout:
                heldout_records.append(raw)
                heldout_sources.append(view.source_id)
                heldout_groups.append(view.group_id)
            else:
                train_records.append(raw)
                train_sources.append(view.source_id)
                training_groups.append(view.group_id)
        if not heldout:
            baseline = _baseline_samples_for_view(
                image_rgb, grid_xyz, view, boxes, config, ego_mask=ego_mask
            )
            if baseline is not None and len(baseline["rgb"]):
                baseline_records.append(baseline)
                baseline_sources.append(view.source_id)
        if progress and (index + 1) % 20 == 0:
            print(
                f"DB145_EXTRACT {patch.patch_id} view={index + 1}/{len(views)} "
                f"train_obs={sum(len(x['rgb']) for x in train_records)} "
                f"heldout_obs={sum(len(x['rgb']) for x in heldout_records)}",
                flush=True,
            )

    train = _concat_records(train_records, train_sources, views_by_id)
    heldout = _concat_records(heldout_records, heldout_sources, views_by_id)
    texel_ids = np.concatenate([record["texel_ids"] for record in baseline_records])
    source_ids = np.concatenate(
        [
            np.full(len(record["rgb"]), source, np.int64)
            for record, source in zip(baseline_records, baseline_sources, strict=True)
        ]
    )
    baseline_result = six_slot_median(
        texel_ids,
        source_ids,
        np.concatenate([record["range_m"] for record in baseline_records]),
        np.concatenate([record["rgb"] for record in baseline_records]),
        grid_hw=(config.grid_hw, config.grid_hw),
    )
    diagnostics = {
        "device_requested": device,
        "source_frame_step": SOURCE_FRAME_STEP,
        "n_source_views": len(views),
        "n_training_observations": len(train.rgb),
        "n_heldout_observations": len(heldout.rgb),
        "n_training_sources": int(train.source_ids.max() + 1),
        "n_heldout_sources": int(heldout.source_ids.max() + 1),
        "baseline_coverage": float(baseline_result.valid.mean()),
        "heldout_frame_indices": sorted(heldout_frames),
        "heldout_protocol": (
            "p0_geometry_evidence_groups"
            if frozen_training or frozen_heldout
            else "legacy_central_time_block"
        ),
    }
    return PatchExtraction(
        train,
        heldout,
        baseline_result,
        tuple(sorted(set(training_groups))),
        tuple(sorted(set(heldout_groups))),
        diagnostics,
    )


def freeze_patch_heldout_groups(
    log_dir: Path,
    patch: GroundPatch,
    window: tuple[int, int],
    *,
    config: ExperimentConfig = DEFAULT_CONFIG,
) -> tuple[HeldoutSplit, dict[str, int]]:
    """Count geometry-valid raw pixels per group, then freeze a disjoint split.

    This P0 function reads calibration and ego geometry only.  It does not read
    RGB values, reconstruction output, or any held-out metric.
    """

    views, _, loader = build_source_views(log_dir, window)
    ego_masks = build_analytic_ego_masks(loader)
    counts: dict[str, int] = {}
    cameras: dict[str, str] = {}
    times: dict[str, int] = {}
    for view in views:
        bbox = _patch_image_bbox(patch, view, config)
        if bbox is None:
            continue
        u0, v0, u1, v1 = bbox
        yy, xx = np.mgrid[v0 : v1 + 1, u0 : u1 + 1]
        uv = np.column_stack((xx.ravel(), yy.ravel())).astype(np.float64)
        _, _, _, _, _, valid = _vectorized_footprints(uv, view, patch, config)
        valid &= ~_ego_pixels(ego_masks[view.camera_name], uv)
        count = int(valid.sum())
        if count <= 0:
            continue
        counts[view.group_id] = count
        cameras[view.group_id] = view.camera_name
        times[view.group_id] = view.frame_idx
    split = select_heldout_groups(
        counts,
        group_camera=cameras,
        group_time=times,
        target_fraction=config.heldout_time_fraction,
    )
    return split, counts


def load_lidar_city(
    log_dir: Path,
    poses: PoseTable,
    window: tuple[int, int],
    loader: AV2RingLoader,
    *,
    frame_step: int = LIDAR_FRAME_STEP,
    max_points_per_sweep: int = 80_000,
) -> np.ndarray:
    anchors = np.asarray(loader.anchor_timestamps_ns(), np.int64)
    sweeps = sorted((Path(log_dir) / "sensors" / "lidar").glob("*.feather"))
    sweep_ts = np.asarray([int(path.stem) for path in sweeps], np.int64)
    chosen: set[int] = set()
    for frame_idx in range(window[0], window[1] + 1, frame_step):
        chosen.add(int(np.argmin(np.abs(sweep_ts - anchors[frame_idx]))))
    output: list[np.ndarray] = []
    for sweep_idx in sorted(chosen):
        frame = pd.read_feather(sweeps[sweep_idx], columns=["x", "y", "z"])
        xyz = frame[["x", "y", "z"]].to_numpy(np.float64)
        if len(xyz) > max_points_per_sweep:
            stride = int(np.ceil(len(xyz) / max_points_per_sweep))
            xyz = xyz[::stride]
        rotation, translation = poses.at(int(sweep_ts[sweep_idx]))
        output.append((rotation @ xyz.T).T + translation)
    return np.concatenate(output) if output else np.empty((0, 3), np.float64)


def fit_local_ground_plane(
    points_city: np.ndarray,
    center_xy: tuple[float, float],
    *,
    radius_m: float = 3.0,
) -> tuple[np.ndarray, float, float, int] | None:
    points = np.asarray(points_city, np.float64)
    center = np.asarray(center_xy, np.float64)
    distance = np.linalg.norm(points[:, :2] - center, axis=1)
    local = points[distance <= radius_m]
    if len(local) < 80:
        return None
    # Road is normally the dense lower mode.  This excludes vehicle roofs and
    # facades before the iterative robust plane fit.
    z_floor = np.quantile(local[:, 2], 0.10)
    local = local[(local[:, 2] >= z_floor - 0.15) & (local[:, 2] <= z_floor + 0.35)]
    if len(local) < 80:
        return None
    xy = local[:, :2] - center
    keep = np.ones(len(local), bool)
    coefficients = np.zeros(3)
    for _ in range(4):
        design = np.c_[xy[keep], np.ones(keep.sum())]
        coefficients, *_ = np.linalg.lstsq(design, local[keep, 2], rcond=None)
        residual = local[:, 2] - (np.c_[xy, np.ones(len(xy))] @ coefficients)
        median = np.median(residual)
        mad = np.median(np.abs(residual - median)) + 1.0e-4
        keep = np.abs(residual - median) <= max(0.04, 3.0 * 1.4826 * mad)
        if keep.sum() < 60:
            return None
    a, b, c_local = coefficients
    normal = np.array([-a, -b, 1.0], np.float64)
    normal /= np.linalg.norm(normal)
    c_city = c_local - a * center[0] - b * center[1]
    d = -c_city / np.sqrt(a * a + b * b + 1.0)
    residual_normal = (
        local[keep] @ normal + d
    )
    rmse = float(np.sqrt(np.mean(residual_normal**2)))
    return normal, float(d), rmse, int(keep.sum())


def patch_overlaps_annotation(
    center_xy: tuple[float, float], boxes: Sequence[Box3D], config: ExperimentConfig
) -> bool:
    centre = np.asarray(center_xy, np.float64)
    half_patch = config.patch_size_m / 2.0
    for box in boxes:
        relative = (centre - box.center_city[:2]) @ box.R_city_box[:2, :2]
        half_box = box.size_lwh[:2] / 2.0 + 0.5
        if (np.abs(relative) <= half_box + half_patch).all():
            return True
    return False


def coarse_patch_observability(
    patch: GroundPatch,
    views: Iterable[SourceView],
    *,
    config: ExperimentConfig = DEFAULT_CONFIG,
) -> PatchObservability:
    grid_xyz = _grid_xyz(patch, config)
    covered = np.zeros(len(grid_xyz), bool)
    usable_views = 0
    camera_ids: set[int] = set()
    aspects: list[float] = []
    angles: list[float] = []
    phases: list[np.ndarray] = []
    center_xyz = patch.xyz_from_xy(np.asarray(patch.center_xy)[None])[0]
    for view in views:
        uv, valid = project_city_to_image(grid_xyz, view.K, view.T_city_cam)
        ranges = np.linalg.norm(grid_xyz - view.T_city_cam[:3, 3], axis=1)
        valid &= (
            (uv[:, 0] >= 1)
            & (uv[:, 0] < view.image_width - 1)
            & (uv[:, 1] >= 1)
            & (uv[:, 1] < view.image_height - 1)
            & (ranges >= config.min_source_range_m)
            & (ranges <= config.max_source_range_m)
        )
        if valid.sum() < 20:
            continue
        covered |= valid
        usable_views += 1
        camera_ids.add(view.camera_idx)
        centre_uv, centre_valid = project_city_to_image(
            center_xyz, view.K, view.T_city_cam
        )
        if centre_valid:
            _, _, _, aspect, _, fp_valid = _vectorized_footprints(
                centre_uv[None], view, patch, config
            )
            if fp_valid[0]:
                aspects.append(float(aspect[0]))
        vector = view.T_city_cam[:2, 3] - np.asarray(patch.center_xy)
        angles.append(float(np.arctan2(vector[1], vector[0])))
        valid_uv = uv[valid]
        phases.append(np.mod(valid_uv[: min(200, len(valid_uv))], 1.0))
    if angles:
        resultant = abs(np.mean(np.exp(1j * np.asarray(angles))))
        angular_diversity = float(1.0 - resultant)
    else:
        angular_diversity = 0.0
    if phases:
        phase = np.concatenate(phases)
        hist, _ = np.histogramdd(phase, bins=(4, 4), range=((0, 1), (0, 1)))
        probability = hist.ravel() / max(hist.sum(), 1)
        nonzero = probability > 0
        entropy = float(-(probability[nonzero] * np.log(probability[nonzero])).sum() / np.log(16))
    else:
        entropy = 0.0
    return PatchObservability(
        patch_id=patch.patch_id,
        center_xy=patch.center_xy,
        coverage_fraction=float(covered.mean()),
        source_view_count=usable_views,
        angular_diversity=angular_diversity,
        subpixel_phase_entropy=entropy,
        camera_diversity=len(camera_ids) / len(RING_CAMS_7),
        median_footprint_aspect=float(np.median(aspects)) if aspects else 40.0,
        plane_rmse_m=patch.plane_rmse_m,
        min_evidence=bool(covered.mean() >= 0.20 and usable_views >= 3),
    )


def generate_patch_candidates(
    log_dir: Path,
    window: tuple[int, int],
    *,
    config: ExperimentConfig = DEFAULT_CONFIG,
) -> tuple[list[GroundPatch], list[PatchObservability], dict[str, object]]:
    """Generate and score patches without reading reconstruction outputs."""

    coarse_views, poses, loader = build_source_views(
        log_dir, window, frame_step=COARSE_FRAME_STEP
    )
    lidar_city = load_lidar_city(log_dir, poses, window, loader)
    annotations = AnnotationIndex(Path(log_dir) / "annotations.feather")
    anchors = np.asarray(loader.anchor_timestamps_ns(), np.int64)
    patches: list[GroundPatch] = []
    scores: list[PatchObservability] = []
    rejection_counts: dict[str, int] = {}
    for frame_idx in range(window[0], window[1] + 1, 10):
        rotation, translation = poses.at(int(anchors[frame_idx]))
        yaw_axis = rotation[:2, 0]
        lateral = np.array([-yaw_axis[1], yaw_axis[0]])
        lateral /= max(np.linalg.norm(lateral), 1.0e-8)
        boxes = annotations.boxes_at(int(anchors[frame_idx]))
        for offset in PATCH_LATERAL_OFFSETS_M:
            center = translation[:2] + offset * lateral
            if patch_overlaps_annotation(tuple(center), boxes, config):
                rejection_counts["annotation_overlap"] = (
                    rejection_counts.get("annotation_overlap", 0) + 1
                )
                continue
            fit = fit_local_ground_plane(lidar_city, tuple(center))
            if fit is None:
                rejection_counts["plane_missing"] = rejection_counts.get("plane_missing", 0) + 1
                continue
            normal, d, rmse, _ = fit
            if rmse > 0.05:
                rejection_counts["plane_rmse"] = rejection_counts.get("plane_rmse", 0) + 1
                continue
            patch = GroundPatch(
                patch_id=f"f{frame_idx:03d}_lat{offset:+.0f}",
                center_xy=(float(center[0]), float(center[1])),
                plane_n=tuple(float(x) for x in normal),
                plane_d=d,
                plane_rmse_m=rmse,
                anchor_frame_idx=frame_idx,
            )
            score = coarse_patch_observability(patch, coarse_views, config=config)
            patches.append(patch)
            scores.append(score)
    diagnostics = {
        "n_lidar_points": len(lidar_city),
        "n_candidate_patches": len(patches),
        "rejection_counts": rejection_counts,
    }
    return patches, scores, diagnostics
