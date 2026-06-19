# DB-97 Ground-Fill Videos — Full Reproduction Spec (2026-06-18)

Everything another agent needs to reconstruct the 4 v1 videos exactly. The frame
identity (UUID + anchor) is deterministic; the pipeline + params + env are pinned below.

---

## 1. What the 4 videos are
4 clips. Each = one Argoverse-2 log's **93 CONSECUTIVE anchors**, rendered through the
perspective→360° **ERP panorama (1024×2048)** stack = scene-band stitching + STAGE-4
ground fill, with **sky left BLACK** (sky_fill_flux deliberately NOT run), then assembled
at **12 fps, H.264/yuv420p**. Output: `deliverables/ground_video_v1/<tag>_h264.mp4`.

## 2. EXACT FRAMES — the reproducible identity
**Dataset: Argoverse 2 (AV2) Sensor dataset, `val` split.**

| tag | AV2 log UUID (full) | anchor range | frames | window ego-displacement |
|---|---|---|---|---|
| **bmw** | `02a00399-3857-444e-8db3-a8f58489c394` | **0 – 92** | 93 | 28.7 m |
| **crowd** | `fbee355f-8878-31fa-8ac8-b9a45a3f130a` | **0 – 92** | 93 | 42.9 m |
| **clean** | `0bae3b5e-417d-3b03-abaa-806b433233b8` | **0 – 92** | 93 | 24.7 m |
| **highway** | `2c652f9e-8db8-3572-aa49-fae1344a875b` | **225 – 317** | 93 | 34.1 m |

- **Anchor definition (deterministic):** `AV2RingLoader` sorts the log's `ring_front_center`
  capture timestamps ascending; **anchor N = the N-th (0-based) sorted frame.** So
  `(UUID, anchor)` is a fully reproducible frame identity with no extra info needed.
- **highway starts at 225** (not 0) to skip a stationary stretch at the log start — ground
  fill needs sustained ego motion. bmw/crowd/clean start at 0.
- **downtown (`9f871fb4-3b8e-34b3-9161-ed961e71a6da`) was EXCLUDED** — its best 93-anchor
  window only moves 16.1 m (long red-light idle), which starves ground fill.
- Windows were picked by `video_gen_av2.py --diag` (maximize path length, penalize any
  stationary 10-frame sub-gap).

## 3. EXACT nanosecond timestamps (the hard global IDs)
Anchor index already pins the frame, but for the absolute hard ID, run this on a live
Colab endpoint (the data is mounted there); takes ~30 s and writes `REPRO_frames.json`:
```python
from waymo2panorama.data_io.av2_loader import AV2RingLoader
DATA = "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val"
ts = AV2RingLoader(f"{DATA}/{uuid}").anchor_timestamps_ns()      # sorted ascending
frame_ts = [int(ts[a]) for a in range(start, start+93)]          # the 93 timestamps
```
The `ring_front_center` jpg **filenames ARE these nanosecond timestamps** (sorted filename
order = anchor order): `.../<uuid>/sensors/cameras/ring_front_center/<ts_ns>.jpg`.
**⚠️ Do NOT read timestamps via raw Google-Drive search** — Drive holds **5+ duplicate
copies** of each UUID folder under different parents, so a blind search can return the
wrong copy. Only the loader over the mounted `…/data/argoverse2/val/<uuid>` is authoritative.
*(REPRO_frames.json not yet generated this session — both L4 runtimes were stopped to save
compute; re-run one Colab setup cell and the 4×93 timestamps export in one job.)*

## 4. Per-frame render content
Each anchor → `run_case()` in `db89_ghost_recovery.py` → the saved **"segcomposite"** =
scene band (full stitching stack) + STAGE-4 ground fill; **sky = black**. Saved per frame as
`datasets/av2_ground_video_v1/<tag>_a<NNN>_segcomposite.png` (NNN = 3-digit zero-padded anchor).

## 5. The pipeline / code — PIN THIS
- **Repo:** `github.com/QiPan-Ronnie/Waymo2Panorama`, branch `main`,
  **commit `4d49edeb7e3a457d9c38744e38c96fdc55f72c3a`** (HEAD as of this doc).
- **Core algorithm (single-file full stack):** `scripts/phase3/db89_ghost_recovery.py`
  — recentred depth-aware mosaic + LiDAR-correspondence photometric harmonization +
  asynchronous-shutter moving-object handling + STAGE-4 ground fill. ERP `H, W = 1024, 2048`.
- **Virtual centre:** the **ring-camera centroid at camera height** (the Fable-5 recentre;
  db89 default) — NOT the ego origin. This is reproduction-critical.
- **Driver:** `scripts/phase3/video_gen_av2.py` — holds `WINDOWS` (the 4 tuples in §2),
  `NWIN=93`, `FPS=12`. `batch_py()`: CASES = consecutive anchors, lean saves (segcomposite
  only; emc/board dropped), per-anchor try/except isolation, **resume-safe skip-if-exists**.
  Modes: `--diag | --submit [--only=tag] [--reverse] | --poll | --assemble`. Renders via the
  Colab+Drive framework (`ColabClient` → `/exec` a base64-injected `remote_py()`).
- **STAGE-4 ground-fill params** (db89, DB-98-fixed state, lines ≈1100–1222):
  - candidate eligibility = whole-log GEOMETRY: `|frame−anchor| ≥ 5` AND ego-displacement
    `disp ∈ (5.0, 58.0) m`; displacement-bucketed (5 m buckets over 5–58 m) × time-nearest.
  - per-point grazing gate: `egod ∈ (5.0, 28.0) m`; FOV margin `px ∈ [2, w−2]`.
  - two-box ego self-occlusion (full-length 1.0 m body box + cabin-height box).
  - **LiDAR ground-height reprojection** (cKDTree onto the measured ground band, 3 iters)
    — commit `75423ac`.
  - **source-agreement spread-gate** `SPREAD_MAX = 30.0` (abstain where sources disagree)
    → Navier-Stokes inpaint (NOT Telea) — commit `e272011`.
  - resolution-matched nadir low-pass (render at the grazing evidence's true optical res).

## 6. Assembly
Per tag: read `<tag>_a{start..start+92}_segcomposite.png` in anchor order → `cv2.VideoWriter`
mp4v @ 12 fps → re-encode `ffmpeg -y -i raw.mp4 -c:v libx264 -pix_fmt yuv420p -movflags
+faststart <tag>_h264.mp4` (cv2 mp4v alone is not broadly playable). 1024×2048 per frame.

## 7. Environment / data location
- **Colab + Google Drive framework** (agent-colab-direct v0.1.0). Workspace
  `MyDrive/koi_waymo2pano_colab/`. Data at
  `.../data/argoverse2/val/<uuid>/sensors/cameras/ring_front_center/*.jpg`.
- Loader: `waymo2panorama.data_io.av2_loader.AV2RingLoader`.
- Owner: `panq@usc.edu`, shared to `1jingshuo1@gmail.com`. Never touch `secrets/`.

## 8. HONEST caveat — v1 is a MIXED-STATE / throwaway set
`video_gen_av2.py` pulls the **live local `db89`** at each submit, and STAGE-4 evolved while
these rendered (2026-06-12 → 06-18; the DB-98 fixes `e272011` + `75423ac` landed 06-18).
So the v1 frames are **NOT all from one code SHA** — it is a mixed-state reference set. For
a CLEAN, fully-reproducible set, **re-render v2 from the single pinned SHA in §5.** Expected
known limitation (not a repro error): **bottom-nadir softness** = the rig's near-pole-behind
**physical blind spot** (DB-98). Open decision (b accept / c honest low-res render) gates v2.
