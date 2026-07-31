"""Official nuScenes mini scene-0061 conversion plus five-anchor B-band pilot.

The converter uses the current cadence-derived synchronization contract.  The
renderer keeps ground fill off (the old pilot already showed insufficient
near-ground LiDAR supply), preserves raw sensor pixels, and enables the
same-point color diagnostic without changing output pixels.

This file contains no executor URL or token.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


SOURCE_ROOT = Path("/content/nuscenes_mini_db217")
METADATA_ROOT = SOURCE_ROOT / "v1.0-mini"
PSEUDO_ROOT = Path("/content/localav2/val")
LOG_ID = "db217_nuscenes_scene0061_v1"
OUTPUT_ROOT = Path("/content/db217_nuscenes_scene0061_pilot")
DRIVE_ROOT = Path(
    "/content/drive/MyDrive/koi_waymo2pano_colab/results/"
    "db217_nuscenes_scene0061_pilot"
)
RING_CAMERAS = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear",
    "ring_side_right",
    "ring_front_right",
)
EXTRA = json.dumps(
    [
        ['GROUND_MODE = "fill"', 'GROUND_MODE = "off"'],
        [
            'ANNOTATION_POLICY = "composite"',
            'ANNOTATION_POLICY = "raw_sensor"',
        ],
        [
            'EGO_IMG_MASK = "/content/egomask_cur.npz"',
            'EGO_IMG_MASK = ""',
        ],
        ["COLOR_DIAG = False", "COLOR_DIAG = True"],
    ]
)


def _require_a100() -> str:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    name = torch.cuda.get_device_name(0)
    if "A100" not in name.upper():
        raise RuntimeError(f"expected A100, got {name!r}")
    return name


def _alignment(manifest: object) -> dict[str, object]:
    matches = [
        artifact.path.split("=", 1)[1]
        for artifact in manifest.source_artifacts
        if artifact.path.startswith("derived:nuscenes_temporal_alignment=")
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one temporal alignment descriptor: {matches}")
    return json.loads(matches[0])


def _convert() -> tuple[Path, object, dict[str, object]]:
    sys.path.insert(0, "/content/w2p_ego")
    sys.path.insert(0, "/content/w2p_ego/code")
    from agent.db181_multids.nuscenes_adapter import convert_nuscenes_scene
    from waymo2panorama.data_io.av2_loader import AV2RingLoader

    output_dir, manifest = convert_nuscenes_scene(
        source_root=SOURCE_ROOT,
        metadata_root=METADATA_ROOT,
        scene_id="scene-0061",
        output_root=PSEUDO_ROOT,
        output_log_id=LOG_ID,
        mode="B",
        converter_git_commit=os.environ["W2P_CONVERTER_COMMIT"],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    manifest.validate()
    alignment = _alignment(manifest)
    if alignment["adapter_algorithm_version"] != "nuscenes_cadence_window_v3":
        raise RuntimeError(f"unexpected alignment contract: {alignment}")
    if manifest.dataset != "nuscenes" or manifest.mode != "B":
        raise RuntimeError(f"unexpected manifest dataset/mode: {manifest}")
    if not manifest.has_lidar or not manifest.has_ego_pose or manifest.has_annotations:
        raise RuntimeError(f"unexpected evidence flags: {manifest}")
    if not 0 < manifest.output_frame_count <= manifest.source_frame_count:
        raise RuntimeError(f"invalid frame counts: {manifest}")
    if alignment["dropped_anchor_frame_count"] != (
        manifest.source_frame_count - manifest.output_frame_count
    ):
        raise RuntimeError(f"alignment drop count mismatch: {alignment}")
    windows = alignment["sync_window_ns"]
    for camera_record in manifest.camera_records:
        if camera_record.max_sync_delta_ns > int(windows[camera_record.source_name]):
            raise RuntimeError(
                f"sync delta exceeds cadence window: {camera_record}, {windows}"
            )
    for camera in manifest.cameras:
        count = len(list((output_dir / "sensors" / "cameras" / camera).glob("*.jpg")))
        if count != manifest.output_frame_count:
            raise RuntimeError(f"camera output count mismatch for {camera}: {count}")
    lidar_count = len(list((output_dir / "sensors" / "lidar").glob("*.feather")))
    if lidar_count != manifest.output_frame_count:
        raise RuntimeError(f"LiDAR output count mismatch: {lidar_count}")
    loader = AV2RingLoader(output_dir, cameras=manifest.cameras)
    loader.load_synced_frame(manifest.frames[0].anchor_timestamp_ns)
    loader.load_synced_frame(manifest.frames[-1].anchor_timestamp_ns)
    return output_dir, manifest, alignment


def _pilot_anchors(frame_count: int) -> tuple[int, ...]:
    import numpy as np

    return tuple(
        sorted(
            {
                int(round(value))
                for value in np.linspace(0, frame_count - 1, min(5, frame_count))
            }
        )
    )


def _render(anchors: tuple[int, ...]) -> dict[str, object]:
    if not Path("/content/db125_worker.py").is_file():
        raise FileNotFoundError("/content/db125_worker.py")
    if OUTPUT_ROOT.exists():
        raise FileExistsError(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True)
    processes = []
    started = time.time()
    for worker_index, anchor in enumerate(anchors):
        outdir = OUTPUT_ROOT / f"m{worker_index}"
        outdir.mkdir(parents=True)
        log_handle = (OUTPUT_ROOT / f"worker_{worker_index}.log").open("wb")
        process = subprocess.Popen(
            [
                "python",
                "/content/db125_worker.py",
                f"nusc{worker_index}",
                str(anchor),
                LOG_ID,
                str(outdir),
                EXTRA,
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "W2P_RING_CAMS": ",".join(RING_CAMERAS),
                "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            },
        )
        processes.append((worker_index, anchor, process, log_handle, outdir))

    rows = []
    for worker_index, anchor, process, log_handle, outdir in processes:
        return_code = process.wait()
        log_handle.close()
        manifests = glob.glob(str(outdir / "manifest*.json"))
        if not manifests:
            raise RuntimeError(f"worker {worker_index} produced no manifest")
        worker_manifest = json.load(open(manifests[0], encoding="utf-8"))
        cases = worker_manifest.get("cases") or []
        if return_code != 0 or worker_manifest.get("error") or len(cases) != 1:
            raise RuntimeError(
                f"worker {worker_index} failed: rc={return_code}, "
                f"manifest={worker_manifest}"
            )
        case = cases[0]
        if case.get("error") or case.get("n_objects_composited") != 0:
            raise RuntimeError(f"worker {worker_index} violated pilot contract: {case}")
        panoramas = glob.glob(str(outdir / "*_segcomposite.png"))
        territories = glob.glob(str(outdir / "*_territory.png"))
        diagnostics = glob.glob(str(outdir / "*_color_diag.json"))
        if len(panoramas) != 1 or len(territories) != 1 or len(diagnostics) != 1:
            raise RuntimeError(
                f"worker {worker_index} artifact counts="
                f"{len(panoramas)}/{len(territories)}/{len(diagnostics)}"
            )
        rows.append(
            {
                "anchor": anchor,
                "return_code": return_code,
                "panorama": panoramas[0],
                "territory": territories[0],
                "color_diagnostic": diagnostics[0],
                "case": case,
            }
        )
    return {"runtime_s": time.time() - started, "anchors": rows}


def _persist(summary_path: Path, render: dict[str, object]) -> None:
    DRIVE_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(summary_path, DRIVE_ROOT / summary_path.name)
    for row in render["anchors"]:
        anchor = int(row["anchor"])
        for key, suffix in (
            ("panorama", "panorama.png"),
            ("territory", "territory.png"),
            ("color_diagnostic", "color_diag.json"),
        ):
            shutil.copy2(row[key], DRIVE_ROOT / f"anchor_{anchor:04d}_{suffix}")


def main() -> None:
    gpu = _require_a100()
    if not METADATA_ROOT.is_dir():
        raise FileNotFoundError(METADATA_ROOT)
    pseudo_log, manifest, alignment = _convert()
    anchors = _pilot_anchors(manifest.output_frame_count)
    render = _render(anchors)
    summary = {
        "gpu": gpu,
        "source_root": str(SOURCE_ROOT),
        "pseudo_log": str(pseudo_log),
        "source_frame_count": manifest.source_frame_count,
        "output_frame_count": manifest.output_frame_count,
        "cameras": list(manifest.cameras),
        "mode": manifest.mode,
        "has_lidar": manifest.has_lidar,
        "has_ego_pose": manifest.has_ego_pose,
        "has_annotations": manifest.has_annotations,
        "alignment": alignment,
        "render": render,
    }
    summary_path = OUTPUT_ROOT / "db217_nuscenes_summary.json"
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    _persist(summary_path, render)
    archive = shutil.make_archive(str(OUTPUT_ROOT), "zip", OUTPUT_ROOT)
    print(
        "DB217_NUSCENES_DONE",
        json.dumps(
            {
                "archive": archive,
                "frame_count": manifest.output_frame_count,
                "anchors": anchors,
                "gpu": gpu,
                "drive_root": str(DRIVE_ROOT),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
