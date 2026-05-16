"""
Phase 0.5 Spike — AV2 API + data validation probe.

Run AFTER downloading one AV2 sensor log with scripts/download_av2_sample.py.

What this script verifies (before any L1 baseline code is written):
  1. av2 library is importable + version
  2. Known dataloader class paths still work
  3. All 7 ring cameras have image files for at least one common timestamp
  4. Timestamps across the 7 cams are within 50 ms (synchronization tight enough)
  5. Calibration feathers (intrinsics + extrinsics) are readable and well-formed
  6. A 2x4 mosaic of the 7 ring cams at one timestamp looks like surrounding view

Outputs:
  outputs/spike/mosaic.png      — visual eyeball gate
  outputs/spike/probe_log.txt   — full diagnostic log

Usage:
  python scripts/spike_av2_probe.py --log-dir data/argoverse2/val/<UUID>
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "outputs" / "spike"
LOG_PATH = OUT_DIR / "probe_log.txt"

RING_CAMS_7 = [
    "ring_front_center",
    "ring_front_left",
    "ring_front_right",
    "ring_side_left",
    "ring_side_right",
    "ring_rear_left",
    "ring_rear_right",
]

# 2x4 mosaic layout matching surrounding-view spatial intuition.
# Top row = forward; bottom row = rearward.
MOSAIC_LAYOUT = [
    ["ring_front_left", "ring_front_center", "ring_front_right", None],
    ["ring_rear_left", "ring_side_left", "ring_side_right", "ring_rear_right"],
]


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def step(title: str) -> None:
    log("")
    log(f"--- {title} ---")


def check_av2_import() -> tuple[bool, str]:
    try:
        import av2  # noqa: F401

        version = getattr(av2, "__version__", "<unknown>")
        log(f"OK: av2 imported. version: {version}")
        return True, version
    except ImportError as e:
        log(f"FAIL: av2 not importable: {e}")
        log("HINT: pip install av2  (or activate the right conda env)")
        return False, ""


def find_dataloader_classes() -> list[tuple[str, type]]:
    """Try several known import paths for the AV2 sensor dataloader."""
    candidates = [
        "av2.datasets.sensor.sensor_dataloader.SensorDataloader",
        "av2.datasets.sensor.av2_sensor_dataloader.AV2SensorDataLoader",
    ]
    found: list[tuple[str, type]] = []
    for path in candidates:
        try:
            module_path, class_name = path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            cls = getattr(module, class_name)
            found.append((path, cls))
            log(f"OK:   {path}")
        except Exception as e:  # noqa: BLE001
            log(f"MISS: {path}  ({type(e).__name__}: {e})")
    if not found:
        log("WARN: no known dataloader class found. Manual probe will still proceed.")
    return found


def manual_filesystem_probe(log_dir: Path) -> dict[str, tuple[Path, int]] | None:
    """Independent of av2 API. Verifies the on-disk structure."""
    cams_dir = log_dir / "sensors" / "cameras"
    if not cams_dir.exists():
        log(f"FAIL: expected dir not found: {cams_dir}")
        log("HINT: --log-dir should point at <repo>/data/argoverse2/<split>/<log_uuid>")
        return None

    cam_files: dict[str, list[Path]] = {}
    for cam in RING_CAMS_7:
        cam_dir = cams_dir / cam
        if not cam_dir.exists():
            log(f"FAIL: camera dir missing: {cam_dir}")
            return None
        files = sorted(cam_dir.glob("*.jpg"))
        if not files:
            log(f"FAIL: no .jpg files in {cam_dir}")
            return None
        cam_files[cam] = files
        first_ts = files[0].stem
        last_ts = files[-1].stem
        log(f"OK: {cam:22s} frames={len(files):4d}  first_ts={first_ts}  last_ts={last_ts}")

    # Pick the first ring_front_center timestamp as anchor; find nearest neighbor in each cam.
    front_ts = [int(p.stem) for p in cam_files["ring_front_center"]]
    span_ns = front_ts[-1] - front_ts[0]
    log(f"front_center span: {span_ns / 1e9:.2f} s  ({len(front_ts)} frames)")

    target_ts = front_ts[0]
    synced: dict[str, tuple[Path, int]] = {}
    for cam in RING_CAMS_7:
        cam_ts = np.asarray([int(p.stem) for p in cam_files[cam]], dtype=np.int64)
        idx = int(np.argmin(np.abs(cam_ts - target_ts)))
        synced[cam] = (cam_files[cam][idx], int(cam_ts[idx]))

    deltas_ms = [abs(synced[c][1] - target_ts) / 1e6 for c in RING_CAMS_7]
    max_delta_ms = max(deltas_ms)
    log(f"max timestamp delta across 7 cams @ anchor: {max_delta_ms:.2f} ms")
    if max_delta_ms > 50:
        log("WARN: delta > 50 ms — synchronization weaker than plan assumed. Note in spike-report.")
    else:
        log("OK: synchronization within 50 ms tolerance")
    return synced


def build_mosaic(synced: dict[str, tuple[Path, int]], output_path: Path) -> None:
    target_h, target_w = 256, 320
    cells: list[np.ndarray] = []

    for row in MOSAIC_LAYOUT:
        row_cells: list[np.ndarray] = []
        for cam in row:
            if cam is None:
                cell = np.zeros((target_h, target_w, 3), dtype=np.uint8)
                cv2.putText(
                    cell, "(empty)", (target_w // 2 - 30, target_h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 80), 1, cv2.LINE_AA,
                )
            else:
                path, ts = synced[cam]
                img = np.array(Image.open(path).convert("RGB"))
                log(f"  loaded {cam:22s} shape={img.shape}  ts={ts}")
                cell = cv2.resize(img, (target_w, target_h))
                label = cam.replace("ring_", "")
                cv2.rectangle(cell, (0, 0), (len(label) * 9 + 6, 22), (0, 0, 0), -1)
                cv2.putText(
                    cell, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 255), 1, cv2.LINE_AA,
                )
            row_cells.append(cell)
        cells.append(np.hstack(row_cells))
    mosaic = np.vstack(cells)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mosaic).save(output_path)
    log(f"OK: mosaic saved → {output_path}  shape={mosaic.shape}")


def probe_calibration(log_dir: Path) -> bool:
    cal_dir = log_dir / "calibration"
    if not cal_dir.exists():
        log(f"FAIL: {cal_dir} not found")
        return False

    contents = sorted(p.name for p in cal_dir.iterdir())
    log(f"calibration/ contents: {contents}")

    try:
        import pandas as pd
    except ImportError:
        log("FAIL: pandas missing — needed for .feather. pip install pandas pyarrow")
        return False

    intr_path = cal_dir / "intrinsics.feather"
    extr_path = cal_dir / "egovehicle_SE3_sensor.feather"
    ok = True

    if intr_path.exists():
        try:
            intr = pd.read_feather(intr_path)
            log(f"intrinsics.feather: {len(intr)} rows; cols={list(intr.columns)}")
            if "sensor_name" in intr.columns:
                ring_intr = intr[intr["sensor_name"].astype(str).str.startswith("ring_")]
                log(f"  ring cams in intrinsics: {len(ring_intr)}")
                log(f"  sample row ring_front_center:")
                fc = ring_intr[ring_intr["sensor_name"] == "ring_front_center"]
                if not fc.empty:
                    log(f"    {fc.iloc[0].to_dict()}")
        except Exception as e:  # noqa: BLE001
            log(f"FAIL: reading intrinsics.feather: {e}")
            log(traceback.format_exc())
            ok = False
    else:
        log(f"MISS: {intr_path}")
        ok = False

    if extr_path.exists():
        try:
            extr = pd.read_feather(extr_path)
            log(f"egovehicle_SE3_sensor.feather: {len(extr)} rows; cols={list(extr.columns)}")
            if "sensor_name" in extr.columns:
                ring_extr = extr[extr["sensor_name"].astype(str).str.startswith("ring_")]
                log(f"  ring cams in extrinsics: {len(ring_extr)}")
                fc = ring_extr[ring_extr["sensor_name"] == "ring_front_center"]
                if not fc.empty:
                    log(f"  sample row ring_front_center:")
                    log(f"    {fc.iloc[0].to_dict()}")
        except Exception as e:  # noqa: BLE001
            log(f"FAIL: reading egovehicle_SE3_sensor.feather: {e}")
            log(traceback.format_exc())
            ok = False
    else:
        log(f"MISS: {extr_path}")
        ok = False

    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--log-dir", type=Path, required=True,
        help="Path to one AV2 sensor log root, e.g. data/argoverse2/val/02a00399-...",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists():
        LOG_PATH.unlink()

    log("=" * 72)
    log("Phase 0.5 Spike — AV2 API + data probe")
    log(f"started: {datetime.now()}")
    log(f"log_dir: {args.log_dir}")
    log("=" * 72)

    step("Step 1: av2 importable")
    av2_ok, av2_version = check_av2_import()

    step("Step 2: dataloader class discovery")
    classes = find_dataloader_classes()

    if not args.log_dir.exists():
        log("")
        log(f"FAIL: log_dir does not exist: {args.log_dir}")
        log("HINT: download a log first → python scripts/download_av2_sample.py")
        return 1

    step("Step 3: manual filesystem probe")
    synced = manual_filesystem_probe(args.log_dir)
    fs_ok = synced is not None

    if fs_ok:
        step("Step 4: build 7-cam mosaic at one synced timestamp")
        try:
            build_mosaic(synced, OUT_DIR / "mosaic.png")
            mosaic_ok = True
        except Exception as e:  # noqa: BLE001
            log(f"FAIL: mosaic build: {e}")
            log(traceback.format_exc())
            mosaic_ok = False
    else:
        mosaic_ok = False

    step("Step 5: calibration probe (intrinsics + extrinsics)")
    cal_ok = probe_calibration(args.log_dir)

    step("Step 6: GO / NO-GO summary")
    rows = [
        ("av2 importable",           av2_ok),
        ("av2 dataloader class(es)", len(classes) > 0),
        ("7 ring cam dirs present",  fs_ok),
        ("mosaic produced",          mosaic_ok),
        ("calibration readable",     cal_ok),
    ]
    for name, ok in rows:
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    all_ok = all(ok for _, ok in rows)
    log("")
    if all_ok:
        log("RESULT: GO → Phase 1 can start. Open outputs/spike/mosaic.png and eyeball-verify.")
    else:
        log("RESULT: NO-GO → at least one check failed. See log above and amend plan.")
    log("")
    log("Next:")
    log("  1. Open outputs/spike/mosaic.png — verify it looks like surrounding view")
    log("  2. Fill in notes/spike-report.md based on this log")
    log("  3. Commit and proceed (or pause and amend plan)")
    log("=" * 72)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
