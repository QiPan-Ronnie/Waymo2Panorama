from __future__ import annotations

import argparse
import os
import subprocess


PROMPT = (
    "continuous existing street panorama patch; preserve the existing curb, "
    "sidewalk edge, road texture, and white lane markings as continuous real "
    "street geometry; smooth only the local seam; no vehicles, no people, no "
    "traffic lights, no signs, no new buildings, no vertical slice artifacts"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dit360-root", default="/content/DiT360")
    ap.add_argument(
        "--init",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db36_user_redline_mask/G_bmw_pano.jpg",
    )
    ap.add_argument(
        "--mask",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db36_user_redline_mask/db36_g_user_redline_mask_preserve_nonseam.png",
    )
    ap.add_argument(
        "--out",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db36_user_redline_mask/G_bmw_pano_user_redline_tau5",
    )
    ap.add_argument(
        "--zip-out",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/results/db36_user_redline_mask/G_bmw_pano_user_redline_tau5_review.zip",
    )
    args = ap.parse_args()

    os.environ.setdefault("HF_HOME", "/content/hf_cache")
    os.environ.setdefault("HF_HUB_CACHE", "/content/hf_cache/hub")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("DIFFUSERS_OFFLINE", "1")

    case = (
        "name=db36_user_redline_tau5,"
        f"core_mask={args.mask},"
        "tau=5,halo_px=16,halo_weight=0.25,far_weight=1.0"
    )
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
        case,
        "--crop",
        "long_source=850,420,1650,720",
        "--crop",
        "right_white=1440,360,2048,720",
        "--crop",
        "lower_right=1600,560,2048,760",
        "--prompt",
        PROMPT,
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
        "db36_user_redline_tau5",
    ]
    subprocess.run(pack_cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
