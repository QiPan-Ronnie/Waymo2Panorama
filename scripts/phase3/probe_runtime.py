"""Probe the active Colab runtime. Reports GPU / memory / torch version to Drive."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    info: dict = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    try:
        import torch
        info["torch_version"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["device_name"] = torch.cuda.get_device_name(0)
            info["device_capability"] = list(torch.cuda.get_device_capability(0))
            free, total = torch.cuda.mem_get_info()
            info["mem_free_gb"] = round(free / 1024**3, 2)
            info["mem_total_gb"] = round(total / 1024**3, 2)
        else:
            info["device_name"] = None
    except Exception as e:
        info["torch_error"] = str(e)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
