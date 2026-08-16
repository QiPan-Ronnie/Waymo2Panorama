"""DB-238 screening job - phases A (validate), B (pilot), C (full population).

Run on the authorized L4.  Checkpoints after every log so a runtime death costs
at most one log.  Never deletes a sample and never chooses a threshold.

  python db238_screen_job.py A      # reproduce the a100 seven-pair table from S3
  python db238_screen_job.py B      # 10-log pilot
  python db238_screen_job.py C      # full 555
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/content")

import db238_screen as SC  # noqa: E402

V15 = "/content/drive/MyDrive/koi_waymo2pano_colab/datasets/av2_1plus92_v15"
OUT_DRIVE = ("/content/drive/MyDrive/koi_waymo2pano_colab/results/"
             "db238_scene_band_screening")
WORK = "/content/db238_work"
LEDGER = os.path.join(OUT_DRIVE, "screening_ledger.json")

# measured on 00a6ffc1 a100 through the full production pipeline (D1/D5)
A100_TRUTH = {
    "ring_front_left|ring_side_left": 1.88,
    "ring_front_center|ring_front_left": 2.83,
    "ring_side_left|ring_rear_left": 4.80,
    "ring_rear_left|ring_rear_right": 7.19,
    "ring_front_right|ring_front_center": 8.59,
    "ring_rear_right|ring_side_right": 11.01,
    "ring_side_right|ring_front_right": 52.82,
}


def _load_ledger():
    if os.path.isfile(LEDGER):
        try:
            with open(LEDGER) as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"schema": "db238.screening.v1", "records": {}}


def _save_ledger(led):
    os.makedirs(OUT_DRIVE, exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(led, fh, indent=1)
    os.replace(tmp, LEDGER)


def _anchor_table():
    """prefix -> anchor index, from the v15 ledgers' window start."""
    out = {}
    import glob as _g
    for p in _g.glob(os.path.join(V15, "db144_v15_ledger_*.json")):
        try:
            with open(p) as fh:
                j = json.load(fh)
        except Exception:
            continue
        for pref, rec in (j.get("logs") or {}).items():
            win = rec.get("window")
            if isinstance(win, list) and win:
                out.setdefault(pref, int(win[0]))
    return out


def _population():
    """[(prefix, split_dir)] for the 555 delivered samples."""
    pop = []
    for sub in ("val", "train"):
        d = os.path.join(V15, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith("_w1"):
                pop.append((name[:-3], sub))
    return pop


def _resolve_uuids(prefixes):
    """8-char prefix -> full uuid, and which S3 split holds it."""
    mapping = {}
    for split in ("val", "train"):
        try:
            logs = SC.list_split_logs(split)
        except Exception as exc:
            print(f"WARN could not list {split}: {exc}", flush=True)
            continue
        print(f"  s3 {split}: {len(logs)} logs", flush=True)
        for u in logs:
            mapping.setdefault(u[:8], (u, split))
    return {p: mapping[p] for p in prefixes if p in mapping}


def _find_split(uuid):
    """The v15 train/val folders are NOT the S3 split; resolve it for real."""
    if SC.cached_log_dir(uuid) is not None:
        return "cached"
    for split in ("train", "val", "test"):
        try:
            if SC._ls_timestamps(
                    f"{SC.S3}/{split}/{uuid}/sensors/cameras/ring_front_center", ".jpg"):
                return split
        except Exception:
            continue
    return None


def phase_a():
    print("=== PHASE A: reproduce the a100 seven-pair table ===", flush=True)
    uuid = "00a6ffc1-6ce9-3bc3-a060-6006e9893a1a"
    split = _find_split(uuid)
    print(f"  source for {uuid[:8]}: {split}", flush=True)
    if split is None:
        print("  cannot locate the validation log anywhere", flush=True)
        return None
    best = None
    for n_lidar in (2, 5, 11):
        rec = SC.screen_log(uuid, split if split != "cached" else "val", 100,
                            WORK, n_lidar=n_lidar)
        if not rec.get("ok"):
            print(f"  n_lidar={n_lidar} FAILED: {rec.get('error')}", flush=True)
            continue
        print(f"\n  --- n_lidar={n_lidar}  ({rec['lidar_points']:,} pts, "
              f"{rec['total_s']}s) ---", flush=True)
        devs, ranks_ok = [], []
        for k, truth in sorted(A100_TRUTH.items(), key=lambda kv: kv[1]):
            got = rec["pairs"].get(k, {}).get("residual")
            if got is None:
                alt = k.split("|")[1] + "|" + k.split("|")[0]
                got = rec["pairs"].get(alt, {}).get("residual")
            if got is None:
                print(f"    {k:<40} truth={truth:6.2f}  MISSING", flush=True)
                continue
            rel = (got - truth) / max(truth, 1e-6)
            devs.append(abs(rel))
            ranks_ok.append((truth, got))
            print(f"    {k:<40} truth={truth:6.2f}  got={got:6.2f}  "
                  f"rel={rel:+.1%}", flush=True)
        if len(ranks_ok) >= 5:
            import numpy as np
            t = np.array([a for a, _ in ranks_ok]); g = np.array([b for _, b in ranks_ok])
            sp = np.corrcoef(np.argsort(np.argsort(t)), np.argsort(np.argsort(g)))[0, 1]
            print(f"    rank correlation vs truth: {sp:+.3f}   "
                  f"max |rel dev| = {max(devs):.1%}", flush=True)
            if best is None or max(devs) < best[1]:
                best = (n_lidar, max(devs), sp)
    os.makedirs(OUT_DRIVE, exist_ok=True)
    with open(os.path.join(OUT_DRIVE, "phase_a_validation.json"), "w") as fh:
        json.dump({"best_n_lidar": best[0] if best else None,
                   "max_rel_dev": best[1] if best else None,
                   "rank_corr": best[2] if best else None,
                   "truth": A100_TRUTH}, fh, indent=1)
    print(f"\nPHASE A best n_lidar={best[0] if best else None} "
          f"max_rel_dev={best[1]:.1%}" if best else "PHASE A produced no result",
          flush=True)
    print("DB238_PHASE_A_DONE", flush=True)
    return best


def _run_population(items, n_lidar, tag):
    led = _load_ledger()
    recs = led["records"]
    todo = [(p, u, s, a) for (p, u, s, a) in items if p not in recs or not recs[p].get("ok")]
    print(f"{tag}: {len(items)} requested, {len(items)-len(todo)} already done, "
          f"{len(todo)} to run", flush=True)
    t0 = time.time()
    for i, (pref, uuid, split, anchor) in enumerate(todo, 1):
        rec = SC.screen_log(uuid, split, anchor, WORK, n_lidar=n_lidar)
        rec["prefix"] = pref
        recs[pref] = rec
        led["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        led["n_lidar"] = n_lidar
        _save_ledger(led)
        el = time.time() - t0
        rate = el / i
        status = (f"worst={rec.get('worst_residual')}" if rec.get("ok")
                  else f"FAIL {rec.get('error', '')[:70]}")
        print(f"[{i}/{len(todo)}] {pref} {status}  "
              f"({rec.get('total_s')}s, avg {rate:.0f}s, "
              f"eta {(len(todo)-i)*rate/60:.0f}m)", flush=True)
    ok = sum(1 for r in recs.values() if r.get("ok"))
    print(f"{tag} complete: {ok}/{len(recs)} scored", flush=True)
    return led


def phase_b(n_lidar):
    print("=== PHASE B: 10-log pilot ===", flush=True)
    anchors = _anchor_table()
    pop = [(p, s) for p, s in _population() if s == "val"][:10]
    res = _resolve_uuids([p for p, _ in pop])
    items = [(p, res[p][0], res[p][1], anchors.get(p, 50)) for p, _ in pop if p in res]
    print(f"resolved {len(items)}/{len(pop)} prefixes to full uuids", flush=True)
    led = _run_population(items, n_lidar, "PHASE B")
    print("DB238_PHASE_B_DONE", flush=True)
    return led


def phase_c(n_lidar):
    print("=== PHASE C: full population ===", flush=True)
    anchors = _anchor_table()
    pop = _population()
    print(f"v15 population: {len(pop)} samples", flush=True)
    res = _resolve_uuids([p for p, _ in pop])
    items = [(p, res[p][0], res[p][1], anchors.get(p, 50)) for p, _ in pop if p in res]
    missing = [p for p, _ in pop if p not in res]
    print(f"resolved {len(items)}/{len(pop)}; unresolved: {len(missing)}", flush=True)
    if missing:
        os.makedirs(OUT_DRIVE, exist_ok=True)
        with open(os.path.join(OUT_DRIVE, "unresolved_prefixes.json"), "w") as fh:
            json.dump(missing, fh, indent=1)
    led = _run_population(items, n_lidar, "PHASE C")
    led["unresolved_prefixes"] = missing
    _save_ledger(led)
    print("DB238_PHASE_C_DONE", flush=True)
    return led


if __name__ == "__main__":
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT_DRIVE, exist_ok=True)
    phase = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
    nl = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    if phase == "A":
        phase_a()
    elif phase == "B":
        phase_b(nl)
    elif phase == "C":
        phase_c(nl)
    else:
        raise SystemExit(f"unknown phase {phase}")
