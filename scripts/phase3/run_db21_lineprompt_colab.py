from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


LINE_PROMPT = (
    "continuous existing street panorama patch; preserve the existing white curb "
    "and lane markings as straight continuous lines; preserve existing sidewalk "
    "and road edges; smooth local seam repair only; no vehicles, no people, "
    "no signs, no traffic lights, no new buildings"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dit360-root", default="/content/DiT360")
    ap.add_argument("--init", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db14_inputs/G_bmw_pano.jpg")
    ap.add_argument(
        "--mask",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db21_current_masks/rg_line_narrow_mask_preserve_nonseam.png",
    )
    ap.add_argument(
        "--out",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db21_current_mask/G_bmw_pano_rg_line_narrow_lineprompt",
    )
    ap.add_argument(
        "--zip-out",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db21_current_mask/G_bmw_pano_rg_line_narrow_lineprompt_review.zip",
    )
    args = ap.parse_args()

    os.environ.setdefault("HF_HOME", "/content/hf_cache")
    os.environ.setdefault("HF_HUB_CACHE", "/content/hf_cache/hub")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("DIFFUSERS_OFFLINE", "1")

    run_cmd = [
        "python",
        "scripts/phase3/run_dit360_trimap_clamp.py",
        "--dit360-root",
        args.dit360_root,
        "--init-image",
        args.init,
        "--out-dir",
        args.out,
        "--case",
        f"name=db21_rgline_tau5_lineprompt,core_mask={args.mask},tau=5,halo_px=16,halo_weight=0.25,far_weight=1.0",
        "--crop",
        "right_bmw=1420,360,2048,760",
        "--crop",
        "right_ground=1480,560,2048,760",
        "--crop",
        "darkwall_ground=1090,470,1510,760",
        "--prompt",
        LINE_PROMPT,
        "--steps",
        "50",
        "--guidance",
        "2.8",
        "--seed",
        "0",
        "--height",
        "1024",
        "--width",
        "2048",
    ]
    subprocess.run(run_cmd, check=True)

    pack_cmd = [
        "python",
        "scripts/phase3/db14_gate_pack.py",
        "--root",
        args.out,
        "--init",
        args.init,
        "--mask",
        args.mask,
        "--zip-out",
        args.zip_out,
        "--case",
        "db21_rgline_tau5_lineprompt",
    ]
    subprocess.run(pack_cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
