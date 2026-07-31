"""
AV2 ring camera loader (Phase 1).

Loads synchronized ring-camera frames from one Argoverse 2-compatible sensor log.
The unchanged AV2 seven-camera ring remains the default.

Per Phase 0.5 spike findings (see notes/spike-report.md):
  - 7 ring cams (drop the 2 stereo cams):
      ring_front_center, ring_front_left, ring_front_right,
      ring_side_left, ring_side_right, ring_rear_left, ring_rear_right
  - ring_front_center is portrait (2048 x 1550); the other 6 are landscape (1550 x 2048)
  - calibration files: calibration/intrinsics.feather + calibration/egovehicle_SE3_sensor.feather
  - 20 Hz; max sync delta across 7 cams ~22 ms

This loader is intentionally minimal: it does NOT depend on `av2.datasets.sensor.SensorDataloader`
to keep us decoupled from av2 version changes. We read calibration .feather + image filesystem
directly. The spike confirmed both code paths (loader class + filesystem) work, and the
filesystem path is simpler.
"""
from __future__ import annotations

import os
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
import pyarrow.feather as feather
from PIL import Image
from scipy.spatial.transform import Rotation

# Default order is meaningful: front_center is the sync anchor; then walk roughly clockwise.
RING_CAMS_7: tuple[str, ...] = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


def resolve_ring_cameras(explicit: Optional[Iterable[str]] = None) -> tuple[str, ...]:
    """Resolve and validate the ordered camera membership for one loader."""
    if explicit is None:
        configured = os.environ.get("W2P_RING_CAMS")
        if configured is None:
            return RING_CAMS_7
        cameras = tuple(configured.split(","))
    else:
        if isinstance(explicit, (str, bytes)):
            raise ValueError(
                "explicit ring cameras must be an iterable of camera names, not str or bytes"
            )
        cameras = tuple(explicit)

    if not cameras:
        raise ValueError("ring camera membership must contain at least one camera")
    if any(not isinstance(name, str) for name in cameras):
        raise ValueError("ring camera names must be strings")

    cameras = tuple(name.strip() for name in cameras)
    if any(not name for name in cameras):
        raise ValueError("ring camera membership contains an empty camera name")

    seen: set[str] = set()
    for name in cameras:
        if name in seen:
            raise ValueError(f"duplicate ring camera name: {name!r}")
        seen.add(name)
    return cameras


def _read_feather(path: Path):
    """Read Feather V1/V2 while containing pyarrow 24's API deprecation warning."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"pyarrow\.feather\.read_table is deprecated",
            category=FutureWarning,
        )
        return feather.read_table(str(path)).to_pandas()


@dataclass(frozen=True)
class CameraCalibration:
    """Per-camera calibration values used by Phase 1."""

    name: str
    K: np.ndarray             # 3x3 pinhole intrinsics
    T_ego_cam: np.ndarray     # 4x4 SE(3): maps a point in camera frame to a point in ego frame
    image_width: int
    image_height: int
    distortion_k: np.ndarray  # (k1, k2, k3) — informational; AV2 imagery is already undistorted


@dataclass
class FrameSample:
    """One synchronized frame of all configured ring cams at one anchor timestamp."""

    anchor_timestamp_ns: int
    images: dict[str, np.ndarray]                # cam_name -> H x W x 3 uint8 RGB
    timestamps_ns: dict[str, int]                 # cam_name -> actual frame timestamp
    calibrations: dict[str, CameraCalibration]   # cam_name -> calibration


class AV2RingLoader:
    """Read one AV2-compatible sensor log using an ordered ring-camera contract."""

    def __init__(self, log_dir: Path, cameras: Optional[Iterable[str]] = None):
        self.log_dir = Path(log_dir)
        self._cameras = resolve_ring_cameras(cameras)
        if not self.log_dir.exists():
            raise FileNotFoundError(f"AV2 log dir not found: {self.log_dir}")
        self._calibrations = self._load_calibrations()
        self._image_paths = self._index_images()

    # ---- public ----

    def cameras(self: Optional["AV2RingLoader"] = None) -> tuple[str, ...]:
        """Return configured cameras, or the AV2 default for a legacy class call."""
        if self is None:
            return RING_CAMS_7
        return self._cameras

    def num_anchor_frames(self) -> int:
        """Number of frames for the first (anchor) camera."""
        return len(self._image_paths[self._cameras[0]])

    def anchor_timestamps_ns(self) -> list[int]:
        """All timestamps for the first camera, used as sync anchors."""
        return [int(p.stem) for p in self._image_paths[self._cameras[0]]]

    def calibration(self, cam: str) -> CameraCalibration:
        return self._calibrations[cam]

    def load_synced_frame(self, anchor_ts_ns: int) -> FrameSample:
        """Load nearest frames; an exact timestamp tie selects the smaller timestamp."""
        images: dict[str, np.ndarray] = {}
        timestamps: dict[str, int] = {}
        for cam in self._cameras:
            paths = self._image_paths[cam]
            cam_ts = np.array([int(p.stem) for p in paths], dtype=np.int64)
            idx = int(np.argmin(np.abs(cam_ts - anchor_ts_ns)))
            img = np.asarray(Image.open(paths[idx]).convert("RGB"))
            images[cam] = img
            timestamps[cam] = int(cam_ts[idx])
        return FrameSample(
            anchor_timestamp_ns=anchor_ts_ns,
            images=images,
            timestamps_ns=timestamps,
            calibrations={c: self._calibrations[c] for c in self._cameras},
        )

    def iter_synced_frames(
        self,
        start: int = 0,
        stop: Optional[int] = None,
        step: int = 1,
    ) -> Iterator[FrameSample]:
        ts_all = self.anchor_timestamps_ns()
        stop = stop if stop is not None else len(ts_all)
        for i in range(start, stop, step):
            yield self.load_synced_frame(ts_all[i])

    # ---- internals ----

    def _load_calibrations(self) -> dict[str, CameraCalibration]:
        intr_path = self.log_dir / "calibration" / "intrinsics.feather"
        extr_path = self.log_dir / "calibration" / "egovehicle_SE3_sensor.feather"
        if not intr_path.exists():
            raise FileNotFoundError(f"missing intrinsics: {intr_path}")
        if not extr_path.exists():
            raise FileNotFoundError(f"missing extrinsics: {extr_path}")

        intr = _read_feather(intr_path)
        extr = _read_feather(extr_path)

        out: dict[str, CameraCalibration] = {}
        for cam in self._cameras:
            row_i = intr.loc[intr["sensor_name"] == cam]
            row_e = extr.loc[extr["sensor_name"] == cam]
            if row_i.empty or row_e.empty:
                raise KeyError(f"calibration row missing for {cam}")
            r_i = row_i.iloc[0]
            r_e = row_e.iloc[0]

            fx, fy = float(r_i["fx_px"]), float(r_i["fy_px"])
            cx, cy = float(r_i["cx_px"]), float(r_i["cy_px"])
            K = np.array(
                [[fx, 0.0, cx],
                 [0.0, fy, cy],
                 [0.0, 0.0, 1.0]],
                dtype=np.float64,
            )
            w = int(r_i["width_px"])
            h = int(r_i["height_px"])
            dist = np.array(
                [float(r_i["k1"]), float(r_i["k2"]), float(r_i["k3"])],
                dtype=np.float64,
            )

            # scipy quaternion convention is (x, y, z, w)
            qw, qx, qy, qz = (
                float(r_e["qw"]), float(r_e["qx"]),
                float(r_e["qy"]), float(r_e["qz"]),
            )
            R_ego_cam = Rotation.from_quat([qx, qy, qz, qw]).as_matrix()
            t = np.array(
                [float(r_e["tx_m"]), float(r_e["ty_m"]), float(r_e["tz_m"])],
                dtype=np.float64,
            )
            T = np.eye(4, dtype=np.float64)
            T[:3, :3] = R_ego_cam
            T[:3, 3] = t

            out[cam] = CameraCalibration(
                name=cam,
                K=K,
                T_ego_cam=T,
                image_width=w,
                image_height=h,
                distortion_k=dist,
            )
        return out

    def _index_images(self) -> dict[str, list[Path]]:
        idx: dict[str, list[Path]] = {}
        cams_dir = self.log_dir / "sensors" / "cameras"
        for cam in self._cameras:
            cam_dir = cams_dir / cam
            paths = list(cam_dir.glob("*.jpg"))
            if not paths:
                raise FileNotFoundError(f"no .jpg files in {cam_dir}")
            timestamped_paths: list[tuple[int, str, Path]] = []
            for path in paths:
                try:
                    timestamp_ns = int(path.stem)
                except ValueError as exc:
                    raise ValueError(
                        "camera image filename must have an integer timestamp stem; "
                        f"got {path.name!r} for {cam}"
                    ) from exc
                timestamped_paths.append((timestamp_ns, path.name, path))
            idx[cam] = [path for _, _, path in sorted(timestamped_paths)]
        return idx
