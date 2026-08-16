from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agent.db232_validate_final_scene_bands import ValidationError, validate_delivery


def _write_case(root: Path, *, target_px: int = 2) -> None:
    case = "sample_a000"
    rows = [10, 12]
    Image.new("RGB", (2048, 1024)).save(root / f"{case}_segcomposite.png")
    Image.new("RGB", (2048, 3)).save(root / f"{case}_scene_band.png")

    full_mask = np.zeros((1024, 2048), dtype=np.uint8)
    full_mask[10, :target_px] = 255
    Image.fromarray(full_mask).save(root / f"{case}_angular_gap_mask.png")
    Image.fromarray(full_mask[10:13]).save(root / f"{case}_scene_band_angular_mask.png")

    manifest = {
        "status": "db89_completed",
        "cases": [
            {
                "case": case,
                "n_objects_composited": 0,
                "photometric_seam_ownership": {"seams_optimized": 1},
                "angular_gap_fallback": {
                    "projection": "direction_only",
                    "safe_band_rows": rows,
                    "target_px": target_px,
                    "filled_px": target_px,
                    "unfilled_px": 0,
                    "scene_band_rows": rows,
                    "scene_band_shape": [3, 2048],
                    "scene_band_file": f"{case}_scene_band.png",
                    "scene_band_angular_mask_file": (
                        f"{case}_scene_band_angular_mask.png"
                    ),
                },
            }
        ],
    }
    (root / "manifest_video_sample.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_validate_delivery_checks_manifest_images_and_mask_population(tmp_path: Path) -> None:
    _write_case(tmp_path)

    report = validate_delivery(tmp_path, expected_manifests=1, expected_cases=1)

    assert report["status"] == "passed"
    assert report["cases"] == 1
    assert report["angular_fallback_pixels"] == 2


def test_validate_delivery_rejects_mask_population_mismatch(tmp_path: Path) -> None:
    _write_case(tmp_path, target_px=2)
    mask = np.zeros((1024, 2048), dtype=np.uint8)
    mask[10, 0] = 255
    Image.fromarray(mask).save(tmp_path / "sample_a000_angular_gap_mask.png")

    with pytest.raises(ValidationError, match="mask population"):
        validate_delivery(tmp_path, expected_manifests=1, expected_cases=1)
