"""
Waymo HDR compensation driver — for teammate use on Waymo data.

This script mirrors ``scripts/phase3/run_hdr_compensation.py`` for AV2, but
targets Waymo's 5-cam front-facing rig.  It loads per-cam ERP slabs + feather
weight maps from ``.npy`` files, runs the Waymo HDR adapter, and writes the
per-cam corrections + diagnostics to a JSON file.

Expected input layout
---------------------
The teammate's pipeline should produce, for each cam index ``i`` in
``[0, num_cams-1]``:

    <slabs-dir>/cam_<i>_slab.npy    — (H, W, 3) float32 in [0, 255]
    <weights-dir>/cam_<i>_weight.npy — (H, W)    float32 in [0, 1]

This mirrors the AV2 driver's convention of numbered per-cam arrays.

Output
------
``--output-json`` receives a JSON file with the following structure::

    {
      "num_cams": 5,
      "corrections": {
        "cam_0": {"gain": [1.0, 1.0, 1.0], "bias": [0.0, 0.0, 0.0]},
        ...
      },
      "overlap_diagnostics": { "pairs": [...] },
      "overlap_gap_summary": { ... }
    }

Usage example
-------------
    python scripts/run_hdr_compensation_waymo.py \\
        --slabs-dir   data/waymo_erp/scene_001/slabs \\
        --weights-dir data/waymo_erp/scene_001/weights \\
        --output-json results/waymo_hdr/scene_001_corrections.json \\
        --num-cams 5

See also: ``scripts/phase3/run_hdr_compensation.py`` for the AV2 equivalent.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Path wiring so we can import from code/waymo2panorama without installing
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../code").resolve()


def _wire_imports(code_root: Path) -> None:
    if not code_root.exists():
        raise FileNotFoundError(
            f"code root not found: {code_root!r}. "
            "Pass --w2p-code to override."
        )
    sys.path.insert(0, str(code_root))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_slabs_and_weights(
    slabs_dir: Path,
    weights_dir: Path,
    num_cams: int,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Load cam_<i>_slab.npy and cam_<i>_weight.npy for i in [0, num_cams)."""
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for i in range(num_cams):
        slab_path = slabs_dir / f"cam_{i}_slab.npy"
        weight_path = weights_dir / f"cam_{i}_weight.npy"
        if not slab_path.exists():
            raise FileNotFoundError(f"missing slab: {slab_path}")
        if not weight_path.exists():
            raise FileNotFoundError(f"missing weight: {weight_path}")
        slab = np.load(slab_path).astype(np.float32)
        weight = np.load(weight_path).astype(np.float32)
        if slab.ndim != 3 or slab.shape[2] != 3:
            raise ValueError(
                f"cam_{i} slab expected (H, W, 3), got {slab.shape}"
            )
        if weight.ndim != 2:
            raise ValueError(
                f"cam_{i} weight expected (H, W), got {weight.shape}"
            )
        slabs.append(slab)
        weights.append(weight)
    return slabs, weights


def _corrections_to_json_blob(
    corrections: np.ndarray, num_cams: int
) -> dict:
    """Serialize (num_cams, 6) corrections array to per-cam JSON dict."""
    blob: dict = {}
    for i in range(num_cams):
        x = corrections[i]
        blob[f"cam_{i}"] = {
            "gain": [float(x[0]), float(x[1]), float(x[2])],
            "bias": [float(x[3]), float(x[4]), float(x[5])],
        }
    return blob


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Waymo HDR gain+bias compensation — computes per-cam corrections "
            "for Waymo's 5-cam front-facing rig and writes JSON output."
        )
    )
    ap.add_argument(
        "--slabs-dir",
        type=Path,
        required=True,
        help="Directory containing cam_<i>_slab.npy files.",
    )
    ap.add_argument(
        "--weights-dir",
        type=Path,
        required=True,
        help="Directory containing cam_<i>_weight.npy files.",
    )
    ap.add_argument(
        "--output-json",
        type=Path,
        required=True,
        help="Path for the output JSON file (corrections + diagnostics).",
    )
    ap.add_argument(
        "--num-cams",
        type=int,
        default=5,
        help="Number of cameras (default: 5).",
    )
    ap.add_argument(
        "--valid-threshold",
        type=float,
        default=0.05,
        help="Weight threshold for overlap pixel selection (default: 0.05).",
    )
    ap.add_argument(
        "--huber-f-scale",
        type=float,
        default=8.0,
        help="Huber loss transition point in pixel-value units (default: 8.0).",
    )
    ap.add_argument(
        "--max-pixels-per-pair",
        type=int,
        default=4000,
        help="Max overlap pixels sampled per cam pair (default: 4000).",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print LS convergence diagnostics.",
    )
    ap.add_argument(
        "--w2p-code",
        type=Path,
        default=None,
        help="Path to code/ root; defaults to ../code relative to this script.",
    )
    args = ap.parse_args()

    code_root = args.w2p_code if args.w2p_code else _CODE_ROOT
    _wire_imports(code_root)

    from waymo2panorama.color.hdr_waymo_adapter import apply_waymo_hdr_compensation  # noqa: E402

    t0 = time.time()
    print(
        f"[hdr-waymo] loading {args.num_cams} cams "
        f"from slabs={args.slabs_dir} weights={args.weights_dir}",
        flush=True,
    )
    slabs, weights = _load_slabs_and_weights(
        args.slabs_dir, args.weights_dir, args.num_cams
    )

    print("[hdr-waymo] running HDR compensation ...", flush=True)
    corrections, diagnostics = apply_waymo_hdr_compensation(
        slabs,
        weights,
        num_cams=args.num_cams,
        valid_threshold=args.valid_threshold,
        max_pixels_per_pair=args.max_pixels_per_pair,
        huber_f_scale=args.huber_f_scale,
        verbose=args.verbose,
    )
    elapsed = time.time() - t0

    summary = diagnostics["overlap_gap_summary"]
    print(
        f"[hdr-waymo] lum_gap before={summary['mean_abs_lum_gap_before']:.2f} "
        f"-> after={summary['mean_abs_lum_gap_after']:.2f} "
        f"(delta={summary['delta']:+.2f}), "
        f"n_pairs={diagnostics['n_overlap_pairs']}, "
        f"t={elapsed:.2f}s",
        flush=True,
    )

    output = {
        "num_cams": int(args.num_cams),
        "corrections": _corrections_to_json_blob(corrections, args.num_cams),
        "overlap_diagnostics": diagnostics["pairs"],
        "overlap_gap_summary": summary,
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2))
    print(f"[hdr-waymo] wrote {args.output_json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
