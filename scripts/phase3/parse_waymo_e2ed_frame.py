"""Parse one frame from a Waymo End-to-End Camera Driving Dataset tfrecord.

E2ED record proto = E2EDFrame, with field `frame` being a standard
dataset_pb2.Frame which holds:
  - frame.context.camera_calibrations: per-cam intrinsics + extrinsics
  - frame.images: per-cam encoded JPEG bytes

This module:
  - Reads tfrecord shard (raw length-prefixed protobuf records, no TF needed)
  - Parses record at given --frame-idx as E2EDFrame
  - Decodes 8 cam JPEGs to numpy arrays
  - Writes:
      out_dir/cam_<idx>_<NAME>.jpg
      out_dir/frame_meta.json   (calibrations + frame_id + cam name mapping)
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np


def _read_record(f) -> bytes | None:
    """One length-prefixed record from a tfrecord file (8B len + 4B crc + data + 4B crc)."""
    header = f.read(8)
    if not header or len(header) < 8:
        return None
    length = struct.unpack("<Q", header)[0]
    f.read(4)               # length CRC
    data = f.read(length)
    f.read(4)               # data CRC
    return data


def _iter_records(path: Path):
    with open(path, "rb") as f:
        while True:
            rec = _read_record(f)
            if rec is None:
                break
            yield rec


def extract(frame, msg_label: str) -> tuple[list[dict], list[np.ndarray]]:
    """Pull (cams, images) from a standard dataset_pb2.Frame."""
    import cv2
    from waymo_open_dataset import dataset_pb2

    # cam id -> calibration proto
    cal_by_id = {int(c.name): c for c in frame.context.camera_calibrations}
    cams: list[dict] = []
    images: list[np.ndarray] = []
    for img in frame.images:
        cam_id = int(img.name)
        cal = cal_by_id.get(cam_id)
        if cal is None:
            print(f"  WARN: cam_id={cam_id} has no calibration, skipping")
            continue
        cam_name = dataset_pb2.CameraName.Name.Name(cam_id)
        intr = list(cal.intrinsic)  # [fx, fy, cx, cy, k1, k2, k3, p1, p2]
        K = [[intr[0], 0.0,     intr[2]],
             [0.0,     intr[1], intr[3]],
             [0.0,     0.0,     1.0]]
        T = list(cal.extrinsic.transform)   # 16-element row-major 4x4
        arr_bgr = cv2.imdecode(
            np.frombuffer(bytes(img.image), np.uint8), cv2.IMREAD_COLOR)
        arr_rgb = cv2.cvtColor(arr_bgr, cv2.COLOR_BGR2RGB)
        cams.append({
            "idx": cam_id,
            "name": cam_name,
            "K": K,
            "T_ego_cam_flat16_rowmajor": T,
            "distortion": {"k1": intr[4], "k2": intr[5], "k3": intr[6],
                           "p1": intr[7], "p2": intr[8]},
            "width": int(cal.width),
            "height": int(cal.height),
            "image_shape_hw": [int(arr_rgb.shape[0]), int(arr_rgb.shape[1])],
        })
        images.append(arr_rgb)
        print(f"  cam {cam_id:2d} {cam_name:18s} K[fx={intr[0]:.1f},cx={intr[2]:.1f}] "
              f"img={arr_rgb.shape[1]}x{arr_rgb.shape[0]}")
    return cams, images


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tfrecord", required=True, type=Path)
    ap.add_argument("--frame-idx", type=int, default=0)
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    import cv2
    from waymo_open_dataset.protos import end_to_end_driving_data_pb2 as e2ed

    target = args.frame_idx
    for i, rec in enumerate(_iter_records(args.tfrecord)):
        if i != target:
            continue
        msg = e2ed.E2EDFrame()
        msg.ParseFromString(rec)
        frame = msg.frame
        ctx = frame.context
        print(f"=== E2EDFrame at idx {i} (rec_bytes={len(rec)}) ===")
        print(f"  context.name = {ctx.name!r}")
        print(f"  camera_calibrations: {len(ctx.camera_calibrations)}")
        print(f"  images:              {len(frame.images)}")

        cams, images = extract(frame, "e2ed.E2EDFrame.frame")
        for j, (cam, img) in enumerate(zip(cams, images)):
            out_jpg = args.out_dir / f"cam_{cam['idx']:02d}_{cam['name']}.jpg"
            cv2.imwrite(str(out_jpg), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        meta = {
            "tfrecord": str(args.tfrecord),
            "frame_idx": i,
            "context_name": ctx.name,
            "proto": "waymo_open_dataset.protos.end_to_end_driving_data_pb2.E2EDFrame",
            "num_cams": len(cams),
            "cams": cams,
        }
        (args.out_dir / "frame_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
        print(f"=== wrote {len(cams)} cams + frame_meta.json to {args.out_dir} ===")
        return

    raise SystemExit(f"no record at idx {target}")


if __name__ == "__main__":
    main()
