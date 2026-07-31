"""Remote DB-215 job: measure same-ray color residuals on three AV2 logs.

This file contains no executor URL or token.  Upload it only after the DB-214
renderer and helper have been installed in both remote source trees.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import time


ROOT = "/content/db215_color_rootcause"
CASES = [
    (
        "1842383a-1577-3b7a-90db-41a9a6668ee2",
        [68, 70, 72],
        "db215_184",
    ),
    (
        "e453f164-dd36-3f1a-9471-05c2627cbaa5",
        [68, 70, 72],
        "db215_e453",
    ),
    (
        "00a6ffc1-6ce9-3bc3-a060-6006e9893a1a",
        [98, 100, 102],
        "db215_blue",
    ),
]
EXTRA = json.dumps(
    [
        ['GROUND_MODE = "fill"', 'GROUND_MODE = "off"'],
        ['ANNOTATION_POLICY = "composite"', 'ANNOTATION_POLICY = "raw_sensor"'],
        ["COLOR_DIAG = False", "COLOR_DIAG = True"],
        ["EMC_RENDER = True", "EMC_RENDER = False"],
        ["EGO_BLACK = False", "EGO_BLACK = True"],
        ['EGO_IMG_MASK = "/content/egomask_cur.npz"', 'EGO_IMG_MASK = ""'],
    ]
)


def main() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    processes: list[tuple[str, subprocess.Popen[bytes], object]] = []
    started = time.time()
    for uuid, anchors, tag in CASES:
        outdir = os.path.join(ROOT, tag)
        os.makedirs(outdir, exist_ok=True)
        log_handle = open(os.path.join(ROOT, f"{tag}.log"), "wb")
        process = subprocess.Popen(
            [
                "python",
                "/content/db125_worker.py",
                tag,
                ",".join(str(anchor) for anchor in anchors),
                uuid,
                outdir,
                EXTRA,
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            },
        )
        processes.append((tag, process, log_handle))

    return_codes = {}
    for tag, process, log_handle in processes:
        return_codes[tag] = process.wait()
        log_handle.close()

    summary = {"return_codes": return_codes, "runtime_s": time.time() - started, "logs": {}}
    for uuid, anchors, tag in CASES:
        outdir = os.path.join(ROOT, tag)
        manifests = glob.glob(os.path.join(outdir, "manifest*.json"))
        assert manifests, f"missing manifest: {tag}"
        manifest = json.load(open(manifests[0], encoding="utf-8"))
        cases = manifest.get("cases") or []
        assert len(cases) == len(anchors), (tag, len(cases), len(anchors))
        assert not manifest.get("error"), (tag, manifest.get("error"))
        assert all(not case.get("error") for case in cases), (tag, cases)
        rows = []
        for anchor in anchors:
            prefix = os.path.join(outdir, f"{tag}_a{anchor:03d}")
            diag_path = prefix + "_color_diag.json"
            territory_path = prefix + "_territory.png"
            pano_path = prefix + "_segcomposite.png"
            assert os.path.exists(diag_path), diag_path
            assert os.path.exists(territory_path), territory_path
            assert os.path.exists(pano_path), pano_path
            diag = json.load(open(diag_path, encoding="utf-8"))
            assert diag.get("measurement") == "same_3d_ray_at_curved_ownership_boundary"
            rows.append({"anchor": anchor, "pairs": diag.get("pairs") or []})
        summary["logs"][tag] = {"uuid": uuid, "anchors": rows}

    summary_path = os.path.join(ROOT, "db215_summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1)
    archive = shutil.make_archive(ROOT, "zip", ROOT)
    print(
        "DB215_DONE",
        json.dumps(
            {
                "archive": archive,
                "return_codes": return_codes,
                "pair_counts": {
                    tag: [len(item["pairs"]) for item in row["anchors"]]
                    for tag, row in summary["logs"].items()
                },
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
