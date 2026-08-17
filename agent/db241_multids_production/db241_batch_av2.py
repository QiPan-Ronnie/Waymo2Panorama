"""Produce a DB-241 sample for every AV2 log already fetched under a root."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db241_driver as D  # noqa: E402
import db241_shard as SH  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else r"E:/w2p_data/av2"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"E:/w2p_data/dataset_out"


def main():
    logs = sorted(d for d in os.listdir(SRC)
                  if os.path.isdir(os.path.join(SRC, d, "sensors", "cameras")))
    print("AV2 logs found: %d%s" % (len(logs), SH.label()), flush=True)
    res = []
    for i, uuid in SH.mine(logs):
        sid = uuid.split("-")[0]
        if os.path.isfile(os.path.join(OUT, "argoverse2", sid, "manifest.json")):
            print("  %-10s already done" % sid, flush=True)
            continue
        hood = "keep" if i % 2 == 0 else "black"
        try:
            m = D.build_sample(os.path.join(SRC, uuid), OUT, "argoverse2", sid,
                               0, 93, hood)
            res.append(m)
            print(D.summarise(m), flush=True)
        except Exception as exc:                       # noqa: BLE001
            print("  %-10s FAILED %s: %s" % (sid, type(exc).__name__, str(exc)[:120]),
                  flush=True)
    ok = [m for m in res if m["accepted"]]
    print("\nAV2: %d/%d accepted this run" % (len(ok), len(res)))


if __name__ == "__main__":
    main()
