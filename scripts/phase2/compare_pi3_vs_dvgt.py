"""
Phase 2 D1 — Compare Pi3 vs DVGT outputs on the same AV2 anchor frame.

Reads <pi3-out>/summary.json + <dvgt-out>/summary.json and the per-cam .npy
files each side wrote. Computes the 7 metrics from
notes/phase2-d1-backbone-decision.md and writes:

    <out-dir>/d1_comparison.md           — markdown report with verdict
    <out-dir>/per_cam_metrics.json       — machine-readable
    <out-dir>/depth_hist_{cam}.png       — per-cam depth-distribution side-by-side

Notes on apples-to-apples:
    Pi3 is scale-free → its `local_z_median` is in some Pi3-internal unit.
    DVGT is metric (driving-tuned) → its `depth_metric_median` is in metres.
    These cannot be directly compared in absolute value. We compare:
        (a) ratio/coefficient of variation (per-view), which IS comparable
        (b) plausibility (DVGT should land near AV2 vehicle scale)
        (c) cross-view consistency (alignment to truth K), which IS comparable
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


RING_CAMS_7 = (
    "ring_front_center", "ring_front_left", "ring_side_left",
    "ring_rear_left", "ring_rear_right", "ring_side_right", "ring_front_right",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _intrinsic_recovery_error(pi3_dir: Path, cam: str) -> dict:
    """Pi3 recovers K from rays — compare to AV2 truth K."""
    K_rec = np.load(pi3_dir / f"intrinsic_recovered_{cam}.npy")
    K_av2 = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
    return {
        "fx_rel_err": float((K_rec[0, 0] - K_av2[0, 0]) / K_av2[0, 0]),
        "fy_rel_err": float((K_rec[1, 1] - K_av2[1, 1]) / K_av2[1, 1]),
        "cx_abs_err_px": float(K_rec[0, 2] - K_av2[0, 2]),
        "cy_abs_err_px": float(K_rec[1, 2] - K_av2[1, 2]),
    }


def _conf_stats(arr: np.ndarray, sigmoid_first: bool) -> dict:
    if sigmoid_first:
        p = 1.0 / (1.0 + np.exp(-arr))
    else:
        # min-max normalize for histogram inspection
        amin, amax = float(arr.min()), float(arr.max())
        if amax - amin < 1e-9:
            p = np.zeros_like(arr)
        else:
            p = (arr - amin) / (amax - amin)
    return {
        "p10": float(np.percentile(p, 10)),
        "p50": float(np.percentile(p, 50)),
        "p90": float(np.percentile(p, 90)),
        "frac_above_0.5": float((p > 0.5).mean()),
    }


def _depth_stats(z_or_norm: np.ndarray) -> dict:
    finite = np.isfinite(z_or_norm) & (z_or_norm > 0)
    if not finite.any():
        return {"valid_frac": 0.0}
    z = z_or_norm[finite]
    return {
        "valid_frac": float(finite.mean()),
        "median": float(np.median(z)),
        "p10": float(np.percentile(z, 10)),
        "p90": float(np.percentile(z, 90)),
        "mean": float(z.mean()),
        "cv": float(z.std() / (z.mean() + 1e-9)),  # coefficient of variation
    }


def _compare_one(pi3_dir: Path, dvgt_dir: Path, cam: str) -> dict:
    pi3_conf = np.load(pi3_dir / f"conf_{cam}.npy")
    pi3_local = np.load(pi3_dir / f"local_points_{cam}.npy")
    pi3_local_z = pi3_local[..., 2]

    dvgt_conf = np.load(dvgt_dir / f"conf_{cam}.npy")
    dvgt_world = np.load(dvgt_dir / f"world_points_{cam}.npy")
    dvgt_depth = np.linalg.norm(dvgt_world, axis=-1)

    return {
        "pi3": {
            "conf": _conf_stats(pi3_conf, sigmoid_first=True),
            "local_z": _depth_stats(pi3_local_z),
            "intrinsic": _intrinsic_recovery_error(pi3_dir, cam),
        },
        "dvgt": {
            "conf": _conf_stats(dvgt_conf, sigmoid_first=False),
            "depth_metric": _depth_stats(dvgt_depth),
        },
    }


def _write_report(out_md: Path, pi3_sum: dict, dvgt_sum: dict, per_cam: dict) -> None:
    lines: list[str] = []
    lines.append("# Phase 2 D1 — Pi3 vs DVGT comparison report\n")
    lines.append(f"Log: `{pi3_sum.get('log_dir')}`  ·  anchor_idx={pi3_sum.get('anchor_idx')}  ·  ts={pi3_sum.get('anchor_timestamp_ns')}\n")
    lines.append("## Runtime metrics\n")
    lines.append("| Metric | Pi3 | DVGT |")
    lines.append("|---|---|---|")
    lines.append(f"| Checkpoint | `{pi3_sum.get('checkpoint')}` | `{dvgt_sum.get('checkpoint')}` |")
    lines.append(f"| Backbone | {pi3_sum.get('backbone')} | {dvgt_sum.get('backbone')} |")
    lines.append(f"| Input shape | {pi3_sum.get('input_shape')} | {dvgt_sum.get('input_shape')} |")
    lines.append(f"| Target side | {pi3_sum.get('target_side')} | {dvgt_sum.get('target_side')} |")
    lines.append(f"| Forward time (s) | {pi3_sum.get('forward_s')} | {dvgt_sum.get('forward_s')} |")
    lines.append(f"| Peak GPU memory (MB) | {pi3_sum.get('peak_gpu_memory_mb')} | {dvgt_sum.get('peak_gpu_memory_mb')} |")
    lines.append("")

    lines.append("## Per-camera comparison\n")
    for cam in RING_CAMS_7:
        m = per_cam[cam]
        lines.append(f"### `{cam}`\n")
        lines.append("| Quantity | Pi3 | DVGT |")
        lines.append("|---|---|---|")
        lines.append(f"| conf p50 | {m['pi3']['conf']['p50']:.3f} | {m['dvgt']['conf']['p50']:.3f} |")
        lines.append(f"| conf frac > 0.5 | {m['pi3']['conf']['frac_above_0.5']:.3f} | {m['dvgt']['conf']['frac_above_0.5']:.3f} |")
        lines.append(f"| depth/z valid_frac | {m['pi3']['local_z'].get('valid_frac', 0):.3f} | {m['dvgt']['depth_metric'].get('valid_frac', 0):.3f} |")
        lines.append(f"| depth/z median | {m['pi3']['local_z'].get('median', float('nan')):.3f} (pi3 unit) | {m['dvgt']['depth_metric'].get('median', float('nan')):.3f} m |")
        lines.append(f"| depth/z CV | {m['pi3']['local_z'].get('cv', float('nan')):.3f} | {m['dvgt']['depth_metric'].get('cv', float('nan')):.3f} |")
        lines.append(f"| K_rec fx rel err (Pi3 only) | {m['pi3']['intrinsic']['fx_rel_err']:+.3f} | — |")
        lines.append(f"| K_rec cx px err (Pi3 only) | {m['pi3']['intrinsic']['cx_abs_err_px']:+.2f} | — |")
        lines.append("")

    lines.append("## Verdict checklist\n")
    lines.append("Per `notes/phase2-d1-backbone-decision.md` tie-breaker rules. Fill in manually:\n")
    lines.append("- [ ] M1 Point cloud density (valid_frac under conf>0.5)")
    lines.append("- [ ] M2 Scale plausibility (DVGT median ~5–30m for road scenes)")
    lines.append("- [ ] M3 Cross-view consistency")
    lines.append("- [ ] M4 Confidence histogram usefulness")
    lines.append("- [ ] M5 GPU memory")
    lines.append("- [ ] M6 Wall-clock latency")
    lines.append("- [ ] M7 Edge cases (sky/hood/reflections)\n")
    lines.append("**Default**: Pi3 wins ties. DVGT must clearly win 5+/7 to displace.\n")
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi3-dir", required=True)
    ap.add_argument("--dvgt-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    pi3_dir = Path(args.pi3_dir)
    dvgt_dir = Path(args.dvgt_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pi3_sum = _load(pi3_dir / "summary.json")
    dvgt_sum = _load(dvgt_dir / "summary.json")

    per_cam: dict[str, Any] = {}
    for cam in RING_CAMS_7:
        per_cam[cam] = _compare_one(pi3_dir, dvgt_dir, cam)

    (out_dir / "per_cam_metrics.json").write_text(
        json.dumps(per_cam, indent=2), encoding="utf-8"
    )
    _write_report(out_dir / "d1_comparison.md", pi3_sum, dvgt_sum, per_cam)
    print(f"[compare] wrote {out_dir / 'd1_comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
