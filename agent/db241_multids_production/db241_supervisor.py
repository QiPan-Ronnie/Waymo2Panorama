"""Keep the four DB-241 producers running unattended.

Each batch script exits when it finishes its quota or hits an error; left alone
the pipeline stalls at whatever it reached.  This restarts whichever one is not
running, in a fixed order, and stops everything if the disk gets tight - filling
E: would corrupt in-flight samples rather than merely pausing production.

It does not touch the producer logic. If a batch keeps dying it will keep being
restarted and the log will show why; that is deliberate, because the alternative
(a supervisor that decides a source is hopeless) hides the failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

AGENT = r"D:\BaiduSyncdisk\2024 to future\koi chen\w2p-db236\agent\db241_multids_production"
SCRATCH = os.environ.get("W2P_SCRATCH", os.path.dirname(os.path.abspath(__file__)))
OUT = r"E:/w2p_data/dataset_out"
TOKEN = r"E:/w2p_data/gcs_token.txt"
MIN_FREE_GB = 25
TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 500

JOBS = [
    ("argoverse2", [sys.executable, "-u", os.path.join(AGENT, "db241_batch_av2.py")],
     r"E:/w2p_data/av2_batch.log"),
    ("nuscenes", [sys.executable, "-u", os.path.join(SCRATCH, "batch_nusc_cams.py"), "300"],
     r"E:/w2p_data/nusc_batch.log"),
    ("waymo_perception", [sys.executable, "-u", os.path.join(SCRATCH, "fetch_percep.py"), "200"],
     r"E:/w2p_data/percep_batch.log"),
    ("waymo_e2e", [sys.executable, "-u", os.path.join(AGENT, "db241_batch_e2e.py"), "400"],
     r"E:/w2p_data/e2e_batch.log"),
]
# AV2 needs its raw logs fetched before the batch has anything to chew on
FETCH = [sys.executable, "-u", os.path.join(AGENT, "db241_fetch_av2.py"),
         "E:/w2p_data/av2", "40"]


def free_gb(path="E:/"):
    import ctypes
    free = ctypes.c_ulonglong(0)
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(path), None, None,
                                               ctypes.pointer(free))
    return free.value / 1e9


def count(ds):
    d = os.path.join(OUT, ds)
    if not os.path.isdir(d):
        return 0
    return sum(1 for s in os.listdir(d)
               if os.path.isfile(os.path.join(d, s, "manifest.json")))


def main():
    env = dict(os.environ)
    env["W2P_GCS_TOKEN_FILE"] = TOKEN
    procs, fetch_proc, fetch_round = {}, None, 0
    while True:
        gb = free_gb()
        if gb < MIN_FREE_GB * 2:
            # Reclaim before refusing to run: source logs whose sample is already
            # finished are pure ballast, and at the production target they weigh
            # more than the dataset. Only then decide whether to stop.
            subprocess.run([sys.executable, os.path.join(AGENT, "db241_reclaim.py"),
                            "--apply"], capture_output=True)
            gb = free_gb()
        if gb < MIN_FREE_GB:
            print("[supervisor] only %.0f GB free - stopping producers" % gb, flush=True)
            for p in procs.values():
                p.terminate()
            return

        counts = {ds: count(ds) for ds, _, _ in JOBS}
        if all(c >= TARGET for c in counts.values()):
            print("[supervisor] all sources at target: %s" % counts, flush=True)
            return

        if os.path.isfile(TOKEN):
            with open(TOKEN) as fh:
                env["W2P_GCS_TOKEN"] = fh.read().strip()

        for ds, cmd, log in JOBS:
            p = procs.get(ds)
            if p is not None and p.poll() is None:
                continue
            if counts[ds] >= TARGET:
                continue
            with open(log, "a") as fh:
                procs[ds] = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                             env=env, cwd=SCRATCH)
            print("[supervisor] started %-18s (have %d)" % (ds, counts[ds]), flush=True)

        if (fetch_proc is None or fetch_proc.poll() is not None) and counts["argoverse2"] < TARGET:
            fetch_round += 1
            with open(r"E:/w2p_data/av2_fetch.log", "a") as fh:
                fetch_proc = subprocess.Popen(FETCH + [str(fetch_round * 40)], stdout=fh,
                                              stderr=subprocess.STDOUT, env=env, cwd=SCRATCH)
            print("[supervisor] av2 fetch round %d" % fetch_round, flush=True)

        print("[supervisor] %s  free %.0f GB" % (counts, gb), flush=True)
        time.sleep(180)


if __name__ == "__main__":
    main()
