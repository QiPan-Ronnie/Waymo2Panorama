"""Run object gate for DB-14 outputs and package selected files for local review."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Directory containing per-case subdirs")
    ap.add_argument("--init", required=True)
    ap.add_argument("--mask", required=True)
    ap.add_argument("--zip-out", required=True)
    ap.add_argument("--case", action="append", required=True)
    ap.add_argument("--conf", type=float, default=0.5)
    args = ap.parse_args()

    root = Path(args.root)
    init = Path(args.init)
    mask = Path(args.mask)
    zip_out = Path(args.zip_out)
    summary = []

    for name in args.case:
        core = root / name / f"{name}_corecompose.png"
        prefix = root / name / f"{name}_gate"
        if not core.exists():
            raise FileNotFoundError(core)
        subprocess.run(
            [
                sys.executable,
                "scripts/phase3/_object_gate.py",
                str(init),
                str(core),
                str(mask),
                str(prefix),
                str(args.conf),
                "cpu",
            ],
            check=True,
        )
        diag_path = root / name / f"{name}_diagnostics.json"
        gate_path = root / name / f"{name}_gate_gate.json"
        diag = json.loads(diag_path.read_text()) if diag_path.exists() else {}
        gate = json.loads(gate_path.read_text()) if gate_path.exists() else {}
        summary.append(
            {
                "name": name,
                "core_mae": diag.get("corecompose_core_mae_vs_init"),
                "halo_mae": diag.get("corecompose_halo_mae_vs_init"),
                "far_mae": diag.get("corecompose_far_mae_vs_init"),
                "case_runtime_s": diag.get("case_runtime_s"),
                "gate_fail": gate.get("fail"),
                "netnew": gate.get("netnew"),
            }
        )

    zip_out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.glob("*/*")):
            if p.suffix.lower() in {".png", ".jpg", ".json"}:
                zf.write(p, p.relative_to(root.parent))
        zf.writestr(f"{root.name}/summary.json", json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[zip] {zip_out} {zip_out.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
