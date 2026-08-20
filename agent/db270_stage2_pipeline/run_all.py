"""W2P stage-2 orchestrator — keeps the A100 busy by never letting the GPU wait.

The work per scene is two different bottlenecks bolted together:

    fetch + convert   network and CPU (a Perception segment is ~900 MB; the
                      converter is single-threaded protobuf + undistort)
    produce + gap     GPU (rotation-only ERP resample, then YOLO over every
                      camera of every frame)

Run them as one queue and the GPU idles through every download. So this runs a
CPU pool that stays N_PREFETCH scenes AHEAD, writing converted pseudo-logs into
a ready queue, while a smaller GPU pool consumes that queue. Both pools are
sized from the box, not guessed, and the ledger makes a killed run resume at
scene granularity instead of restarting.

    python run_all.py --source waymo_perception --want 500
    python run_all.py --all --want 500            # every source, quota each
    python run_all.py --resume                    # pick up where it stopped

Stages per scene, each recorded in state/ledger.json:
    fetch -> convert -> produce(blanket) -> gap(v14) -> hood -> clip -> done
"""
from __future__ import annotations

import os

# L0 - the spin tax (independent review, 2026-08-19). The workload calls a
# degenerate (2M x 3) @ (3 x 3) gemm ~1300 times per sample; OpenBLAS splits it
# across OMP_NUM_THREADS workers that then BUSY-WAIT tens of ms each until
# their thread timeout - with calls a few ms apart they never sleep. Measured
# symptoms this explains: load 12/12 with GPU at 3%, 4 workers at 90%%
# efficiency yet 6 workers ABSOLUTELY slower. Parallelism belongs to the
# worker pool alone; every library gets one thread. Bit-identical: an
# (N,3)@(3,3) split by rows computes the same 3-term dot products either way.
# MUST run before numpy/torch load, hence top of file.
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_k] = "1"
os.environ.setdefault("OPENBLAS_THREAD_TIMEOUT", "1")
# The produce path's GPU renderer (db240_gpu, the v15 kernel) gates on this
# env var and CACHES its first check. Until now it was only being switched on
# as a side effect of db270_gpusm.install() - meaning a box with gpusm_gap
# disabled silently rendered on CPU. Explicit is better: on a box without CUDA
# (the TPU box) available() still returns False safely.
os.environ.setdefault("W2P_GPU", "1")
# L6 - db242's pools default to 12 threads each; 4 workers x (12 decode + 12
# write) threads on 12 cores is pure oversubscription. The env knobs already
# exist in db242_fastio, they were just never set.
os.environ.setdefault("W2P_DECODE_THREADS", "3")
os.environ.setdefault("W2P_WRITE_THREADS", "2")

import argparse
import json
import multiprocessing as mp
import queue
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(ROOT, "config.json")))
LEDGER = os.path.join(ROOT, "state", "ledger.json")
_LOCK = threading.Lock()


# ------------------------------------------------------------------ ledger
def ledger_read():
    with _LOCK:
        try:
            return json.load(open(LEDGER))
        except Exception:
            return {"schema": "w2p.stage2.ledger.v1", "scenes": {}}


def ledger_set(key, **kw):
    with _LOCK:
        try:
            d = json.load(open(LEDGER))
        except Exception:
            d = {"schema": "w2p.stage2.ledger.v1", "scenes": {}}
        e = d["scenes"].setdefault(key, {})
        e.update(kw)
        e["t"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        # PID in the temp name: _LOCK only serialises threads, and more than one
        # run_all process can share a box (a manual relaunch racing the
        # sentinel's, or a stray survivor). With one fixed ".tmp" they clobber
        # each other and the loser's os.replace raises FileNotFoundError - which
        # this function's callers turn into a FAILED SCENE. A good sample was
        # being thrown away over a bookkeeping race.
        tmp = "%s.tmp.%d" % (LEDGER, os.getpid())
        try:
            json.dump(d, open(tmp, "w"), indent=1)
            os.replace(tmp, LEDGER)
        except Exception:                                  # noqa: BLE001
            # The ledger is progress bookkeeping; the Drive archive is the
            # durable truth. Never let a ledger hiccup fail a produced sample.
            try:
                os.path.isfile(tmp) and os.remove(tmp)
            except OSError:
                pass


# ------------------------------------------------------------- box sizing
def box_shape():
    """Measured, not assumed: pool sizes come from this machine."""
    cpu = os.cpu_count() or 8
    gpus, vram = 0, 0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        rows = [r for r in out.splitlines() if r.strip()]
        gpus = len(rows)
        if rows:
            vram = int(rows[0].split(",")[1])
    except Exception:
        pass
    # one GPU worker per ~18 GB: YOLOv8 + the ERP grids sit near 12-16 GB each
    gpu_workers = max(1, min(CFG["parallel"]["gpu_workers"],
                             (vram // 18000) or 1)) if gpus else 0
    cpu_workers = max(2, min(CFG["parallel"]["cpu_workers"], cpu - gpu_workers - 1))
    return {"cpu": cpu, "gpus": gpus, "vram_mb": vram,
            "gpu_workers": gpu_workers or 2, "cpu_workers": cpu_workers}


# -------------------------------------------------------------- the stages
def stage_cpu(job):
    """fetch + convert -> a pseudo-AV2 log on disk. Pure CPU/network.

    All four routes go through `db270_acquire.fetch`, which is the only place
    that knows a Perception segment is a 900 MB tfrecord and a nuScenes scene
    lives inside a 16.5 GB tarball. Keeping that knowledge in one module is what
    lets this function stay four lines.
    """
    sys.path.insert(0, HERE)
    import db270_acquire as A
    log = A.fetch(job, ROOT)
    return {"log": log, "cached": False}


def n_frames(source):
    """Clip length for this source.

    93 is the house unit, but it is NOT a rule anyone handed down - it entered
    the record as Jingshuo's own report of what the sources could supply
    (0731:3) and koi only ever reused it. A source whose clips are shorter is
    not disqualified, it just ships shorter clips: PandaSet is exactly 80 frames
    in all 618 (sequence, camera) pairs, measured from the archive's central
    directory, so if it is ever taken as the OOD holdout it needs 80 here and
    nothing else about the layout changes.
    """
    return int(CFG.get("frames_per_source", {}).get(source,
                                                    CFG["frames_per_sample"]))


def stage_gpu(job, log):
    """produce(blanket) -> gap(v14) -> hood. GPU-resident."""
    # `code` itself, not just its subdirectories: db245 reaches for the absolute
    # `agent.db181_multids.external_object_ownership`, which db270_bundle vendors
    # in at code/agent/. db244 is on the path too and does not look like it.
    sys.path.insert(0, os.path.join(ROOT, "code"))
    for p in ("db238_screening", "db239_seam_mask", "db240_rule_dataset",
              "db241_multids_production", "db242_a100", "db244_match_mask",
              "db245_object_ownership", "db263_cut_vehicle", "db267_hood_apply"):
        sys.path.insert(0, os.path.join(ROOT, "code", p))
    import db241_driver as D
    import db263_production as GAP
    import cv2 as _cv2
    _cv2.setNumThreads(1)          # L0: worker pool owns all parallelism
    try:
        import torch as _torch
        _torch.set_num_threads(1)
    except Exception:
        pass

    # Concurrency-safety for the 4-worker GPU pool: db245 holds ONE YOLO
    # instance in a module singleton and ultralytics predict() is stateful -
    # concurrent calls from several threads share one predictor and race. Both
    # db245's own calls and db263's go through OB._model(), so replacing that
    # one accessor with a thread-local factory gives every GPU worker its own
    # model (~2.5 GB VRAM each, trivial on 80 GB) without touching the frozen
    # modules themselves.
    import db245_object_ownership as OB
    # GPU JPEG decode for the gap chain only - 34x measured on the A100, and
    # scoped away from the produce path because nvJPEG's IDCT rounds +-1 DN
    # differently and delivered frame bytes must stay the frozen decoder's.
    # Gate: config nvjpeg_gap AND a CUDA device AND a passing ab_gap() check
    # recorded by the operator (see db270_nvjpeg docstring).
    # PNG encode was the single largest CPU item: PIL's default compress
    # level 6 costs 23 s per 93-frame sample, level 1 costs 8 s, and the pixels
    # are bit-identical either way - PNG is lossless, the level only sets how
    # hard zlib tries (files grow ~16%, and the 2048 masters are pruned anyway).
    # Patched here, not in db242: the frozen module keeps its documented
    # behaviour, the orchestrator owns the speed/size trade.
    if CFG.get("png_fast", True):
        import db242_fastio as FIO242

        def _fast_save(arr, path):
            from PIL import Image
            Image.fromarray(arr).save(path, compress_level=1)
            return path

        FIO242.FrameWriter._save = staticmethod(_fast_save)
    # L1 (review 2026-08-19): gap-stage geometry through the verified GPU
    # kernel. Gated on gpusm_gap AND a passing db270_gpusm.ab() per rig -
    # same acceptance bar as nvjpeg (which failed it and stays off).
    if CFG.get("gpusm_gap"):
        try:
            import db270_gpusm
            if db270_gpusm.install() is None:
                print("  gpusm: no CUDA device, staying on CPU geometry",
                      flush=True)
        except Exception as exc:                               # noqa: BLE001
            print("  gpusm unavailable: %s" % str(exc)[:80], flush=True)
    if CFG.get("nvjpeg_gap"):
        try:
            import db270_nvjpeg
            db270_nvjpeg.install()
        except Exception as exc:                               # noqa: BLE001
            print("  nvjpeg unavailable, staying on CPU decode: %s"
                  % str(exc)[:80], flush=True)
    if not getattr(OB, "_db270_threadlocal", False):
        _tl = threading.local()
        _name = OB.YOLO_MODEL

        def _tl_model():
            if getattr(_tl, "m", None) is None:
                from ultralytics import YOLO
                _tl.m = YOLO(_name)
            return _tl.m

        OB._model = _tl_model
        OB._db270_threadlocal = True

    src, sid = job["source"], job["scene"]
    split = "held_out_ood" if src == CFG["ood_holdout"] else job["split"]
    out_root = os.path.join(ROOT, "data", "samples", split)
    ship = os.path.join(out_root, src, sid)
    # Idempotence across kills: once a sample is delivered at target size, no
    # stage may touch it again. The dangerous window is a kill after downscale
    # but before the ledger write - on resume, build_sample would be skipped
    # (manifest exists) and the gap layer would re-measure PIXEL-calibrated
    # rules on a 1024-wide raster. Detect via the manifest itself, not the ledger.
    man_p = os.path.join(ship, "manifest.json")
    if os.path.isfile(man_p):
        prev = json.load(open(man_p))
        if prev.get("delivered_size"):
            g = os.path.join(ship, "gap_manifest.json")
            gm = json.load(open(g)) if os.path.isfile(g) else {}
            return {"gap_pct": gm.get("pct_of_supervision", 0.0),
                    "gap_frames": gm.get("frames_touched", 0), "sample": ship,
                    "delivered": prev["delivered_size"], "resumed": True}
    if not os.path.isfile(os.path.join(ship, "manifest.json")):
        # L3: build_sample's clip is 93 frames of 2048x1024 x264 (~20 CPU-s)
        # and db270_downscale re-encodes a 1024x512 clip over it - the first
        # encode is 100% discarded work unless the master is being kept.
        keep_clip = bool(CFG.get("keep_master")) or not CFG.get("deliver_size")
        D.build_sample(log, out_root, src, sid, 0, n_frames(src),
                       job["hood"], make_clip=keep_clip)
    # db263 writes ITS manifest as `manifest.json` in out_dir. That is right when
    # it runs to a separate directory, which is how it was developed; run in
    # place it silently ERASES the producer's manifest — the provenance,
    # n_cameras, rule_mask_frac_of_band and the keep_px_not_written invariant
    # that finalize.py and the delivery README read. Observed on a real sample:
    # manifest.json came back with schema db263.cut_vehicle.v14 and nothing else.
    # Keep both, and leave the frozen v14 module untouched.
    prod_man = os.path.join(ship, "manifest.json")
    saved = open(prod_man, "rb").read() if os.path.isfile(prod_man) else None
    man = GAP.refine_scene(log, ship, ship, n_frames(src))
    if os.path.isfile(prod_man):
        os.replace(prod_man, os.path.join(ship, "gap_manifest.json"))
    if saved is not None:
        with open(prod_man, "wb") as fh:
            fh.write(saved)
    if job["hood"] == "black" and src == "argoverse2":
        import db267_hood_apply as HOOD
        HOOD.BACKUP = os.path.join(ROOT, "data", "hood_backup")
        HOOD.apply_sample(ship, HOOD.rig_hood_mask(), n_frames(src))
    # Trace every output frame back to the native dataset's own identifiers.
    # Failing this must not lose the sample - the pixels are already correct.
    try:
        import db270_provenance as PROV
        PROV.build(ship, log, job, ROOT)
    except Exception as exc:                                   # noqa: BLE001
        print("  provenance skipped for %s/%s: %s"
              % (src, sid, str(exc)[:120]), flush=True)
    # LAST, after every frozen rule has run in the 2048x1024 pixel space it was
    # calibrated in: emit Louison's canvas. db263 v14 measures in pixels
    # (MIN_H=14, MIN_OUT_COLS=8) and db240's golden reference is the 2048 raster,
    # so producing at 1024x512 would change what they decide. Downscaling after
    # costs nothing and keeps the validation chain intact.
    down = None
    size = CFG.get("deliver_size")
    if size:
        import db270_downscale as DS
        down = DS.downscale_sample(ship, tuple(size),
                                   keep_master=CFG.get("keep_master", False))
    out = {"gap_pct": man["pct_of_supervision"],
           "gap_frames": man["frames_touched"], "sample": ship}
    if down:
        out["delivered"] = down["size"]
        out["keep_px_kept_frac"] = down.get("keep_px_kept_frac")
    return out


# ------------------------------------------------------------- the pipeline
def run(jobs, shape, log_every=1):
    ready = queue.Queue(maxsize=shape["gpu_workers"] * 3)
    done = {"ok": 0, "fail": 0}
    t0 = time.time()

    def cpu_loop(sub):
        for job in sub:
            key = "%s/%s" % (job["source"], job["scene"])
            st = ledger_read()["scenes"].get(key, {})
            if st.get("stage") == "done":
                continue
            try:
                ledger_set(key, stage="fetch")
                r = stage_cpu(job)
                ledger_set(key, stage="converted", log=r["log"])
                ready.put((job, r["log"]))
            except Exception as exc:
                ledger_set(key, stage="failed_cpu", err=str(exc)[:200])
                done["fail"] += 1
                print("  CPU-FAIL %s: %s" % (key, str(exc)[:120]), flush=True)

    def gpu_loop():
        while True:
            item = ready.get()
            if item is None:
                return
            job, log = item
            key = "%s/%s" % (job["source"], job["scene"])
            try:
                ledger_set(key, stage="produce")
                r = stage_gpu(job, log)
                # The pseudo-log has served its purpose (produce, gap and
                # provenance all read it inside stage_gpu). 3500 logs x ~0.3 GB
                # is ~1 TB against 185 GB of disk, so reclaim is not optional.
                # nuScenes logs are hardlinks into data/raw - removing them is
                # refcount bookkeeping, not data loss.
                if CFG.get("reclaim_pseudo", True):
                    import shutil
                    shutil.rmtree(log, ignore_errors=True)
                # Durability: the tar on Drive is what survives the VM and what
                # other boxes consult. Its failure is retried by the next
                # startup sweep, never fatal to the sample.
                arch = CFG.get("archive_root")
                if arch:
                    try:
                        import db270_archive as AR
                        sp = ("held_out_ood" if job["source"] == CFG["ood_holdout"]
                              else job["split"])
                        AR.archive_sample(arch, r["sample"], sp, job["source"],
                                          job["scene"])
                        r["archived"] = True
                        # RAM-disk mode: the whole tree lives in /dev/shm, so
                        # local copies must not accumulate - the Drive tar is
                        # the durable copy and _done() consults it. Steady-state
                        # footprint stays at (workers x one sample) instead of
                        # growing to the full quota.
                        if CFG.get("prune_local_after_archive"):
                            import shutil
                            shutil.rmtree(r["sample"], ignore_errors=True)
                    except Exception as exc:               # noqa: BLE001
                        print("  archive failed %s: %s" % (key, str(exc)[:100]),
                              flush=True)
                ledger_set(key, stage="done", **r)
                done["ok"] += 1
                if done["ok"] % log_every == 0:
                    el = time.time() - t0
                    print("[%3d ok / %d fail] %s  gap %.3f%% (%d fr)  %.0fs  "
                          "%.1f/min" % (done["ok"], done["fail"], key,
                                        r["gap_pct"], r["gap_frames"], el,
                                        60 * done["ok"] / max(el, 1)), flush=True)
            except Exception:
                # Drop the converted tree so the retry rebuilds it from source.
                # Deciding whether a cached directory is "complete enough" is
                # the guess that has now failed three times (calibration/ as the
                # test, then a _CONVERT_OK stamp back-filled from ledger state
                # that turned out to describe an EARLIER convert). A GPU failure
                # is the ground truth that this tree is unusable, so stop
                # guessing and let the next claim re-fetch. Costs one re-download
                # for a genuinely transient failure; buys immunity to the whole
                # poisoned-cache class, which otherwise fails forever.
                try:
                    import shutil
                    shutil.rmtree(log, ignore_errors=True)
                except Exception:                          # noqa: BLE001
                    pass
                ledger_set(key, stage="failed_gpu",
                           err=traceback.format_exc()[-300:])
                done["fail"] += 1
                print("  GPU-FAIL %s (converted tree dropped for rebuild)"
                      % key, flush=True)

    nc = shape["cpu_workers"]
    cts = [threading.Thread(target=cpu_loop, args=(jobs[i::nc],), daemon=True)
           for i in range(nc)]
    gts = [threading.Thread(target=gpu_loop, daemon=True)
           for _ in range(shape["gpu_workers"])]
    for t in cts + gts:
        t.start()
    for t in cts:
        t.join()
    for _ in gts:
        ready.put(None)
    for t in gts:
        t.join()
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=CFG["sources"] + ["all"], default="all")
    # The RUNBOOK and every example say `--all`; without this it is an
    # "unrecognized arguments" error at the top of a 30-hour run.
    ap.add_argument("--all", action="store_true", help="every source (default)")
    ap.add_argument("--want", type=int, default=CFG["quota_per_source"])
    ap.add_argument("--workers", type=int, default=0, help="override GPU workers")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--shard", default="", metavar="I/N",
                    help="take slice i of n of the planned jobs (0-based), e.g. "
                         "0/2 and 1/2 on two boxes. The plan is deterministic, "
                         "so the slices are disjoint without any coordination.")
    a = ap.parse_args()

    shape = box_shape()
    if a.workers:
        shape["gpu_workers"] = a.workers
    print("box: %d CPU, %d GPU (%d MB) -> %d GPU workers, %d CPU workers"
          % (shape["cpu"], shape["gpus"], shape["vram_mb"],
             shape["gpu_workers"], shape["cpu_workers"]), flush=True)

    sys.path.insert(0, HERE)
    import plan_jobs
    if CFG.get("archive_root"):
        import db270_archive as AR
        sw = AR.sweep(CFG["archive_root"],
                      os.path.join(ROOT, "data", "samples"))
        if sw["archived"] or sw["failed"]:
            print("startup sweep: %(archived)d archived, %(failed)d failed" % sw,
                  flush=True)
    srcs = CFG["sources"] if (a.all or a.source == "all") else [a.source]
    # Per-source quota, because the sources are not interchangeable: argoverse2
    # publishes exactly 1000 logs so asking it for 1000 samples demands a 100%
    # accept rate, while waymo_e2e has 2508 producible segments and barely
    # notices. `--want` overrides everything when given explicitly.
    per = CFG.get("quota_per_source_override", {})

    def quota_of(src):
        return a.want if a.want != CFG["quota_per_source"] else per.get(
            src, CFG["quota_per_source"])

    # nuScenes runs in SHARD CYCLES, everything else in one plan. Reason:
    # planning its full quota would unpack every needed shard up front -
    # 9 x 16.5 GB = ~150 GB of raw JPEG on a 185 GB disk, alongside the growing
    # samples. A cycle unpacks ONE shard (~85 scenes, measured), produces it,
    # then deletes the raw JPEG trees; the pseudo-logs are hardlinks, so the
    # produced scenes lose nothing. Peak raw is one shard, ~17 GB.
    def shard(jobs):
        if not a.shard:
            return jobs
        i, n = (int(v) for v in a.shard.split("/"))
        return jobs[i::n]

    # nuScenes is partitioned by SHARD, not by job: slicing its jobs would still
    # have every box download the same 16.5 GB tarball. See db270_catalog.nuscenes.
    ns_part = (tuple(int(v) for v in a.shard.split("/")) if a.shard else (0, 1))

    other = [x for x in srcs if x != "nuscenes"]
    jobs = []
    for x in other:
        jobs += shard(plan_jobs.plan(x, quota_of(x), ROOT, CFG))
    print("planned %d scenes: %s" % (len(jobs), {x: sum(
        1 for j in jobs if j["source"] == x) for x in other}), flush=True)
    if a.dry:
        for j in jobs[:5]:
            print("  ", j)
        return
    tot = {"ok": 0, "fail": 0}
    if jobs:
        d = run(jobs, shape)
        tot["ok"] += d["ok"]; tot["fail"] += d["fail"]
    if "nuscenes" in srcs:
        import shutil
        want_ns = quota_of("nuscenes")
        while True:
            have = plan_jobs._have(ROOT, "nuscenes")
            if have >= want_ns:
                break
            # One new shard per cycle, then plan EVERYTHING it completed
            # (~85 scenes, measured). Asking for a fixed chunk instead invites
            # a second unpack whenever chunk exceeds a shard's actual yield,
            # and that second shard would be deleted below unproduced.
            njobs = plan_jobs.plan("nuscenes", want_ns, ROOT, CFG,
                                   nuscenes_max_new=1,
                                   nuscenes_shard=ns_part)
            if not njobs:
                print("nuscenes: no new scenes available at %d/%d - stopping"
                      % (have, want_ns), flush=True)
                break
            print("nuscenes cycle: %d scenes (have %d / want %d)"
                  % (len(njobs), have, want_ns), flush=True)
            d = run(njobs, shape)
            tot["ok"] += d["ok"]; tot["fail"] += d["fail"]
            for sub in ("samples", "sweeps"):
                shutil.rmtree(os.path.join(ROOT, "data", "raw", "nuscenes", sub),
                              ignore_errors=True)
    print("FINISHED ok=%d fail=%d" % (tot["ok"], tot["fail"]), flush=True)


if __name__ == "__main__":
    mp.freeze_support()
    main()
