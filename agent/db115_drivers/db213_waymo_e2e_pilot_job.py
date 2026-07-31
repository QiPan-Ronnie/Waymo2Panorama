"""Bounded A100 pilot for the current eight-camera Waymo E2E Mode-B path.

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
LOG_ID = "db213_waymo_e2e_shard0_v1"
OUTPUT_ROOT = Path("/content/db213_waymo_e2e_pilot")
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


def _convert() -> tuple[Path, object]:
    sys.path.insert(0, "/content/w2p_ego")
    sys.path.insert(0, "/content/w2p_ego/code")
    from agent.db181_multids.contract import ConversionManifest
    from agent.db181_multids.waymo_e2e_adapter import convert_waymo_e2e_tfrecord

    destination = PSEUDO_ROOT / LOG_ID
    if destination.exists():
        manifest = ConversionManifest.read_json(destination / "conversion_manifest.json")
        if manifest.dataset != "waymo_e2e" or manifest.cameras != RING_CAMERAS:
            raise RuntimeError(f"existing pseudo log has incompatible manifest: {destination}")
        return destination, manifest
    return convert_waymo_e2e_tfrecord(
        SOURCE,
        PSEUDO_ROOT,
        LOG_ID,
        converter_git_commit=os.environ["W2P_CONVERTER_COMMIT"],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _pilot_anchors(frame_count: int) -> list[int]:
    desired = (0, 100, 300, 500, 700)
    anchors = sorted({min(value, frame_count - 1) for value in desired})
    if len(anchors) < min(3, frame_count):
        anchors = sorted(
            {int(round(value)) for value in __import__("numpy").linspace(0, frame_count - 1, min(5, frame_count))}
        )
    return anchors


def _render(anchors: list[int]) -> dict[str, object]:
    if not Path("/content/db125_worker.py").is_file():
        raise FileNotFoundError("/content/db125_worker.py")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    processes = []
    started = time.time()
    for worker_index, anchor in enumerate(anchors):
        outdir = OUTPUT_ROOT / f"m{worker_index}"
        outdir.mkdir(parents=True, exist_ok=True)
        log_handle = (OUTPUT_ROOT / f"worker_{worker_index}.log").open("wb")
        process = subprocess.Popen(
            [
                "python",
                "/content/db125_worker.py",
                f"e2e{worker_index}",
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
                f"worker {worker_index} failed: rc={return_code}, manifest={worker_manifest}"
            )
        if cases[0].get("error"):
            raise RuntimeError(f"worker {worker_index} case failed: {cases[0]}")
        panorama = glob.glob(str(outdir / "*_segcomposite.png"))
        if len(panorama) != 1:
            raise RuntimeError(f"worker {worker_index} panorama count={len(panorama)}")
        rows.append(
            {
                "anchor": anchor,
                "return_code": return_code,
                "panorama": panorama[0],
                "case": cases[0],
            }
        )
    return {"runtime_s": time.time() - started, "anchors": rows}


def main() -> None:
    gpu = _require_a100()
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    pseudo_log, manifest = _convert()
    anchors = _pilot_anchors(manifest.output_frame_count)
    render = _render(anchors)
    summary = {
        "gpu": gpu,
        "source": str(SOURCE),
        "pseudo_log": str(pseudo_log),
        "frame_count": manifest.output_frame_count,
        "cameras": list(manifest.cameras),
        "mode": manifest.mode,
        "has_lidar": manifest.has_lidar,
        "has_ego_pose": manifest.has_ego_pose,
        "has_annotations": manifest.has_annotations,
        "render": render,
    }
    summary_path = OUTPUT_ROOT / "db213_waymo_e2e_summary.json"
    summary_path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    archive = shutil.make_archive(str(OUTPUT_ROOT), "zip", OUTPUT_ROOT)
    print(
        "DB213_WAYMO_E2E_DONE",
        json.dumps(
            {
                "archive": archive,
                "frame_count": manifest.output_frame_count,
                "anchors": anchors,
                "gpu": gpu,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
