"""
scripts/download_av2_sample.py

Download ONE Argoverse 2 sensor log into data/argoverse2/<split>/<log_id>/.

AV2 sensor dataset is hosted on a public S3 bucket:
    s3://argoverse/datasets/av2/sensor/<split>/<log_id>/

We use s5cmd (recommended) for parallel download. If s5cmd is not installed,
this script prints the exact command for you to run manually.

Pinned default: a val-split log, daytime suburban. Override with --log-id.
Each sensor log is ~5-10 GB.

Usage:
    python scripts/download_av2_sample.py                       # use defaults
    python scripts/download_av2_sample.py --log-id <UUID>
    python scripts/download_av2_sample.py --dry-run             # print s5cmd cmd only
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA_DIR = REPO_ROOT / "data" / "argoverse2"

# Pinned default — small daytime suburban log from val split. Confirm during spike.
DEFAULT_LOG_ID = "02a00399-3857-444e-8db3-a8f58489c394"
DEFAULT_SPLIT = "val"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-id", default=DEFAULT_LOG_ID, help=f"AV2 log UUID (default {DEFAULT_LOG_ID})")
    parser.add_argument("--split", default=DEFAULT_SPLIT, choices=["train", "val", "test"])
    parser.add_argument("--target-dir", type=Path, default=LOCAL_DATA_DIR)
    parser.add_argument("--dry-run", action="store_true", help="print command, do not execute")
    args = parser.parse_args()

    target = args.target_dir / args.split / args.log_id
    target.mkdir(parents=True, exist_ok=True)

    src = f"s3://argoverse/datasets/av2/sensor/{args.split}/{args.log_id}/*"
    dst = f"{target}/"

    s5cmd_path = shutil.which("s5cmd")

    if s5cmd_path is None:
        print("s5cmd is not installed. To install:")
        print("  conda install -c conda-forge s5cmd          (recommended on Windows)")
        print("  -- or -- ")
        print("  brew install peak/tap/s5cmd                 (mac)")
        print()
        print("Then run this command manually:")
        print(f"  s5cmd --no-sign-request cp \"{src}\" \"{dst}\"")
        print()
        print("Alternative: use aws cli (slower):")
        print(f"  aws s3 cp --recursive --no-sign-request \"s3://argoverse/datasets/av2/sensor/{args.split}/{args.log_id}/\" \"{dst}\"")
        return 2

    cmd = [s5cmd_path, "--no-sign-request", "cp", src, dst]
    print("Running:")
    print(f"  {' '.join(cmd)}")

    if args.dry_run:
        print("(dry-run; not executing)")
        return 0

    print(f"Target dir: {target}")
    print("This may take several minutes (5-10 GB). Progress will print below.")
    print()
    rc = subprocess.run(cmd).returncode
    if rc == 0:
        print()
        print(f"Done. Log downloaded to: {target}")
        print(f"Next: python scripts/spike_av2_probe.py --log-dir {target}")
    else:
        print(f"FAILED with exit code {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
