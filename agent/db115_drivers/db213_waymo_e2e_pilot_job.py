"""Bounded A100 pilot for honest, static-rig Waymo E2E records.

The source shard is not a video: each protobuf record has a distinct context,
no physical timestamp, and only placeholder identity ``CameraImage.pose``
transforms.  This pilot therefore converts five records into five independent
one-frame Mode-B logs and never invents ego motion.

This file contains no executor URL or token.  Before launch, install the current
adapter, renderer, helper, and loader in both remote source trees.
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


SOURCE = Path(
    "/content/drive/MyDrive/koi_waymo2pano_colab/data/waymo_e2ed/"
    "test_202504211836-202504220845.tfrecord-00000-of-00266"
)
PSEUDO_ROOT = Path("/content/localav2/val")
LOG_PREFIX = "db216_e2e_static_v1"
RECORD_INDICES = (0, 100, 300, 500, 700)
OUTPUT_ROOT = Path("/content/db216_waymo_e2e_static_pilot")
DRIVE_ROOT = Path(
    "/content/drive/MyDrive/koi_waymo2pano_colab/results/"
    "db216_waymo_e2e_static_pilot"
)
RING_CAMERAS = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear",
    "ring_rear_right",
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
        ["EMC_RENDER = True", "EMC_RENDER = False"],
        [
            'EGO_IMG_MASK = "/content/egomask_cur.npz"',
            'EGO_IMG_MASK = ""',
        ],
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


def _convert() -> tuple[tuple[Path, object], ...]:
    sys.path.insert(0, "/content/w2p_ego")
    sys.path.insert(0, "/content/w2p_ego/code")
    from agent.db181_multids.waymo_e2e_adapter import convert_waymo_e2e_records

    return convert_waymo_e2e_records(
        SOURCE,
        PSEUDO_ROOT,
        LOG_PREFIX,
        record_indices=RECORD_INDICES,
        converter_git_commit=os.environ["W2P_CONVERTER_COMMIT"],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _render(converted: tuple[tuple[Path, object], ...]) -> dict[str, object]:
    if not Path("/content/db125_worker.py").is_file():
        raise FileNotFoundError("/content/db125_worker.py")
    if OUTPUT_ROOT.exists():
        raise FileExistsError(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True)
    processes = []
    started = time.time()
    for worker_index, (pseudo_log, manifest) in enumerate(converted):
        outdir = OUTPUT_ROOT / f"m{worker_index}"
        outdir.mkdir(parents=True)
        log_handle = (OUTPUT_ROOT / f"worker_{worker_index}.log").open("wb")
        process = subprocess.Popen(
            [
                "python",
                "/content/db125_worker.py",
                f"e2e{worker_index}",
                "0",
                pseudo_log.name,
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
        processes.append(
            (worker_index, pseudo_log, manifest, process, log_handle, outdir)
        )

    rows = []
    for worker_index, pseudo_log, manifest, process, log_handle, outdir in processes:
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
        if cases[0].get("error"):
            raise RuntimeError(f"worker {worker_index} case failed: {cases[0]}")
        if cases[0].get("n_objects_composited") != 0:
            raise RuntimeError(
                f"worker {worker_index} violated raw-sensor ownership: {cases[0]}"
            )
        panoramas = glob.glob(str(outdir / "*_segcomposite.png"))
        if len(panoramas) != 1:
            raise RuntimeError(f"worker {worker_index} panorama count={len(panoramas)}")
        rows.append(
            {
                "record_index": RECORD_INDICES[worker_index],
                "log_id": pseudo_log.name,
                "source_scene_id": manifest.source_scene_id,
                "return_code": return_code,
                "panorama": panoramas[0],
                "case": cases[0],
            }
        )
    return {"runtime_s": time.time() - started, "records": rows}


def _persist(summary_path: Path, render: dict[str, object]) -> None:
    DRIVE_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(summary_path, DRIVE_ROOT / summary_path.name)
    for row in render["records"]:
        source = Path(row["panorama"])
        destination = DRIVE_ROOT / f"record_{row['record_index']:06d}.png"
        shutil.copy2(source, destination)


def main() -> None:
    gpu = _require_a100()
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    converted = _convert()
    converted_record_indices = tuple(
        int(
            json.loads(
                (path / "waymo_e2e_provenance.json").read_text(encoding="utf-8")
            )["source_record_index"]
        )
        for path, _manifest in converted
    )
    if converted_record_indices != RECORD_INDICES:
        raise RuntimeError("converted record order does not match requested indices")
    render = _render(converted)
    summary = {
        "gpu": gpu,
        "source": str(SOURCE),
        "record_indices": list(RECORD_INDICES),
        "record_count": len(converted),
        "cameras": list(RING_CAMERAS),
        "mode": "B",
        "has_lidar": False,
        "has_ego_pose": False,
        "has_annotations": False,
        "physical_timestamps_available": False,
        "camera_pose_available": False,
        "camera_pose_field_status": "placeholder_identity",
        "render": render,
    }
    summary_path = OUTPUT_ROOT / "db216_waymo_e2e_summary.json"
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    _persist(summary_path, render)
    archive = shutil.make_archive(str(OUTPUT_ROOT), "zip", OUTPUT_ROOT)
    print(
        "DB216_WAYMO_E2E_DONE",
        json.dumps(
            {
                "archive": archive,
                "record_indices": RECORD_INDICES,
                "gpu": gpu,
                "drive_root": str(DRIVE_ROOT),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
