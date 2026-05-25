"""
Waymo ring camera loader (Phase 1) — skeleton handoff (WS1.4).

Loads synchronized 5-camera frames from one Waymo Open Dataset segment.

This module provides the SAME public API as ``av2_loader.AV2RingLoader`` so that
all downstream consumers (HDR compensation, cylinder projection, sphere/IPM
drivers, ego masks) can use Waymo as a drop-in replacement for AV2.

Key differences vs AV2 (see also the per-cam stub docstrings below):
  - 5 ring cameras (Waymo) vs 7 (AV2)
      ("FRONT", "FRONT_LEFT", "FRONT_RIGHT", "SIDE_LEFT", "SIDE_RIGHT")
  - 10 Hz capture (Waymo) vs 20 Hz (AV2) — affects sync delta tolerance only.
  - Calibration is stored per-frame in the tfrecord proto
    (``frame.context.camera_calibrations``), not in standalone .feather files.
  - Images live INSIDE the tfrecord frames, not as separate ``.jpg`` files,
    so ``_index_images`` must materialize / cache them somehow.
  - Image orientation TBD: AV2's ``ring_front_center`` is portrait; Waymo's
    ``FRONT`` is landscape (1280 x 1920) — but the teammate should confirm
    against actual segments before downstream code assumes anything.
  - Distortion model: AV2 imagery is already undistorted (radial only stored
    informationally). Waymo intrinsics include both radial (k1,k2,k3) AND
    tangential (p1,p2); the imagery may need explicit ``cv2.undistort``.

The Waymo-specific internals (``_load_calibrations`` and ``_index_images``) are
deliberately left as ``NotImplementedError`` stubs; the teammate who owns the
Waymo data pipeline fills them in. Everything else (the public API and the
synchronized-frame logic in ``load_synced_frame`` / ``iter_synced_frames``)
is identical in shape to AV2, so once the stubs are filled the class is fully
functional with zero changes to downstream code.

We intentionally do NOT import ``waymo_open_dataset`` as a hard dependency —
the teammate adds that to the environment when they wire up the stubs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from PIL import Image

# Reuse the AV2 dataclasses verbatim. Cross-loader API parity is the whole
# point of this skeleton — downstream code consumes ``FrameSample`` and
# ``CameraCalibration`` without caring which dataset produced it.
from waymo2panorama.data_io.av2_loader import CameraCalibration, FrameSample

# Canonical Waymo Open Dataset camera-name enum order. The teammate may
# rename to lowercase / a project-specific convention if their data uses
# that — but the count (5) and the semantic mapping should stay the same.
RING_CAMS_5: tuple[str, ...] = (
    "FRONT",
    "FRONT_LEFT",
    "FRONT_RIGHT",
    "SIDE_LEFT",
    "SIDE_RIGHT",
)


class WaymoRingLoader:
    """Read a single Waymo segment, restricted to the 5 ring cameras.

    Public API mirrors ``AV2RingLoader`` exactly so downstream HDR / cylinder /
    sphere / IPM drivers can swap loaders without changes.

    .. warning::
       This is a HANDOFF SKELETON. Both ``_load_calibrations`` and
       ``_index_images`` currently raise ``NotImplementedError``. Because
       ``__init__`` calls ``_load_calibrations`` first, *instantiating*
       ``WaymoRingLoader`` will raise immediately until the teammate fills in
       at least the calibration stub. ``_index_images`` is reached after
       calibration succeeds; ``load_synced_frame`` then works as-is by reading
       from the maps populated by both stubs.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        if not self.log_dir.exists():
            raise FileNotFoundError(f"Waymo segment dir not found: {self.log_dir}")
        # NOTE: this raises NotImplementedError until the teammate fills the stub.
        self._calibrations = self._load_calibrations()
        self._image_paths = self._index_images()

    # ---- public ----

    @staticmethod
    def cameras() -> tuple[str, ...]:
        return RING_CAMS_5

    def num_anchor_frames(self) -> int:
        """Number of FRONT frames (= number of anchor timestamps)."""
        return len(self._image_paths["FRONT"])

    def anchor_timestamps_ns(self) -> list[int]:
        """All FRONT-camera timestamps (used as sync anchors)."""
        return [int(p.stem) for p in self._image_paths["FRONT"]]

    def calibration(self, cam: str) -> CameraCalibration:
        return self._calibrations[cam]

    def load_synced_frame(self, anchor_ts_ns: int) -> FrameSample:
        """Load one synchronized frame; each cam snaps to its own nearest timestamp.

        Implementation is identical to ``AV2RingLoader.load_synced_frame``.
        Works AS-IS once the two internal stubs are implemented and
        ``_image_paths`` maps cam_name -> sorted list of frame-file paths
        whose stem is the integer timestamp in nanoseconds (mirroring AV2's
        on-disk convention).
        """
        images: dict[str, np.ndarray] = {}
        timestamps: dict[str, int] = {}
        for cam in RING_CAMS_5:
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
            calibrations={c: self._calibrations[c] for c in RING_CAMS_5},
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

    # ---- internals (STUBS — teammate fills these in) ----

    def _load_calibrations(self) -> dict[str, CameraCalibration]:
        """TODO(teammate): Waymo calibration parsing.

        Waymo Open Dataset typically stores per-frame proto with cameras'
        intrinsics + extrinsics in tfrecord segments (e.g.,
        ``training-segment-XXXXXX.tfrecord``). Standard parsing:

          - Read tfrecord -> ``waymo_open_dataset.dataset_pb2.Frame``
          - For each ``frame.context.camera_calibrations``:
              - ``cam_name = waymo_open_dataset.dataset_pb2.CameraName.Name.Name(calib.name)``
                e.g., ``"FRONT"``
              - ``K = [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]`` from
                ``calib.intrinsic[0..3]``
              - ``distortion = calib.intrinsic[4..8]`` (k1, k2, k3, p1, p2)
              - ``extrinsic = calib.extrinsic.transform`` (16-element
                row-major 4x4 SE(3))
              - ``width = calib.width, height = calib.height``
          - Build ``CameraCalibration(name=cam_name, K=K, T_ego_cam=extrinsic,
            image_width=width, image_height=height, distortion_k=...)``.

        Reference: https://github.com/waymo-research/waymo-open-dataset

        Note: Waymo's extrinsic is cam_to_vehicle (== ``T_ego_cam`` in our
        convention), which matches AV2. The intrinsic distortion includes
        both radial (k1, k2, k3) AND tangential (p1, p2), unlike AV2 which
        only stores radial (k1, k2, k3). Our ``CameraCalibration`` dataclass
        currently stores only ``distortion_k`` (3,) — you may need to add
        ``p1, p2`` fields if undistortion matters downstream (AV2 images come
        undistorted; Waymo may need explicit undistortion via
        ``cv2.undistort``).
        """
        raise NotImplementedError(
            "Waymo calibration parsing not implemented. See docstring for hints. "
            "Required: parse self.log_dir for tfrecord files, extract "
            "CameraCalibration per cam."
        )

    def _index_images(self) -> dict[str, list[Path]]:
        """TODO(teammate): Waymo per-camera image indexing.

        Unlike AV2 (which stores each camera frame as a standalone ``.jpg``
        under ``sensors/cameras/<cam>/<timestamp_ns>.jpg``), Waymo packs the
        per-camera JPEG payloads INSIDE the tfrecord ``Frame`` proto under
        ``frame.images[].image`` (encoded bytes) plus
        ``frame.images[].camera_trigger_time`` / ``pose_timestamp``.

        Two reasonable implementations:

          1. Materialize: pre-extract every camera JPEG to disk under a layout
             matching AV2 (``self.log_dir / "extracted" / cam / "{ts_ns}.jpg"``),
             then sorted-glob those paths. Keeps ``load_synced_frame`` working
             AS-IS because the rest of the loader just reads ``.jpg`` from a
             ``Path`` whose ``.stem`` is the integer timestamp.

          2. Virtualize: build an in-memory index keyed by cam, holding
             (timestamp_ns, frame_idx, image_idx) tuples, and override
             ``load_synced_frame`` to decode the JPEG straight from the proto
             via ``tf.io.decode_jpeg``. Saves disk, but breaks the ``Path``
             contract that ``num_anchor_frames`` / ``anchor_timestamps_ns``
             currently rely on — you'd need to refactor those too.

        Option (1) is the lower-risk drop-in. Option (2) is faster on big
        segments but a deeper change.

        The returned dict MUST map every name in ``RING_CAMS_5`` to a sorted
        list of ``Path``\\ s whose ``.stem`` parses to an integer timestamp
        in nanoseconds (matching the convention ``load_synced_frame`` uses).
        """
        raise NotImplementedError(
            "Waymo image indexing not implemented. See docstring for hints. "
            "Required: enumerate per-cam image paths (or in-memory index) for "
            "each name in RING_CAMS_5."
        )
