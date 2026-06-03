from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


SKY_PROMPT = (
    "continuous blue sky with natural clouds above the existing building tops; "
    "preserve the existing rooftops and horizon edge; no vehicles, no people, "
    "no signs, no traffic lights, no new buildings, no new objects"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dit360-root", default="/content/DiT360")
    ap.add_argument("--init", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db14_inputs/G_bmw_pano.jpg")
    ap.add_argument("--tag", default="G_bmw_pano")
    ap.add_argument("--root", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db19_combo")
    args = ap.parse_args()

    os.environ.setdefault("HF_HOME", "/content/hf_cache")
    os.environ.setdefault("HF_HUB_CACHE", "/content/hf_cache/hub")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("DIFFUSERS_OFFLINE", "1")

    root = Path(args.root)
    mask_dir = root / f"{args.tag}_masks"
    out_dir = root / f"{args.tag}_sky_t50_s0"
    zip_out = root / f"{args.tag}_sky_t50_s0_review.zip"
    mask_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "python",
            "scripts/phase3/_outpaint_mask.py",
            args.init,
            str(mask_dir),
            "0.5",
            "60",
        ],
        check=True,
    )
    sky_mask = mask_dir / "opmask_sky.png"

    subprocess.run(
        [
            "python",
            "scripts/phase3/run_dit360_trimap_clamp.py",
            "--dit360-root",
            args.dit360_root,
            "--init-image",
            args.init,
            "--out-dir",
            str(out_dir),
            "--case",
            f"name={args.tag}_sky_t50_s0,core_mask={sky_mask},tau=50,halo_px=32,halo_weight=0.25,far_weight=1.0",
            "--crop",
            "roofline=900,300,1530,560",
            "--crop",
            "right_bmw=1420,360,2048,760",
            "--prompt",
            SKY_PROMPT,
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
        ],
        check=True,
    )

    subprocess.run(
        [
            "python",
            "scripts/phase3/db14_gate_pack.py",
            "--root",
            str(out_dir),
            "--init",
            args.init,
            "--mask",
            str(sky_mask),
            "--zip-out",
            str(zip_out),
            "--case",
            f"{args.tag}_sky_t50_s0",
        ],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
