# DB-145 Sensor-Native Ground Operator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run the bounded DB-145 kill-test that compares the v10/v15 six-slot median ground baseline against a source-pixel-native anisotropic footprint inverse solver on six automatically selected AV2 ground patches, with strict held-out raw-camera validation.

**Architecture:** The experiment is a standalone package under `agent/db145_ground_operator/`; it does not modify db89, db144, the v15 dataset, or any production mask contract. Geometry code converts source-camera pixels into local-ground intersections and EWA footprint covariances. A sparse differentiable operator predicts raw pixel colours from an 80×80 latent ground texture, while a separate cell-centric implementation reproduces the six-nearest-source median baseline. The remote runner processes one log at a time, freezes scene/patch/held-out choices before solving, writes evidence to Drive, and never embeds executor credentials.

**Tech Stack:** Python 3.10+, NumPy, SciPy, OpenCV, PyArrow, AV2, PyTorch CUDA, pytest, Matplotlib/Pillow for evidence boards.

**Hard contract:** Follow `agent/decision_briefs.md` DB-145. One fixed configuration across all logs; three scene roles; two automatic 2m×2m patches per log; no generation; no scene-specific tuning; no free geometry; no result-driven patch selection; held-out raw views are the truth test; 4 L4 GPU-hour ceiling.

---

## File map

- `agent/db145_ground_operator/__init__.py` — package identity and version.
- `agent/db145_ground_operator/config.py` — frozen constants and dataclasses; no per-scene knobs.
- `agent/db145_ground_operator/geometry.py` — plane/ray intersection, pixel footprint Jacobian/covariance, projection helpers.
- `agent/db145_ground_operator/operator.py` — fixed-support differentiable EWA sparse operator.
- `agent/db145_ground_operator/baseline.py` — v10/v15-style ≤6 closest source-view median.
- `agent/db145_ground_operator/observability.py` — scene motion score, patch candidates, high/low patch selection, held-out split.
- `agent/db145_ground_operator/solver.py` — B robust inverse and C view-outlier rejection.
- `agent/db145_ground_operator/av2_extract.py` — AV2 pose/calibration/LiDAR/raw-image ingestion and evidence gates.
- `agent/db145_ground_operator/evaluate.py` — held-out raw-view metrics and render-back images.
- `agent/db145_ground_operator/report.py` — per-patch and six-patch verdict boards.
- `agent/db145_ground_operator/remote_run.py` — P0/P1 CLI for Colab; writes checkpoints and Drive outputs.
- `agent/db145_ground_operator/launch.py` — local deployment/archive launcher using `COLAB_URL`/`COLAB_TOKEN` only.
- `agent/db145_ground_operator/tests/` — deterministic synthetic/unit tests.
- `deliverables/db145_ground_operator/` — fetched manifests, metrics, boards, and final verdict only.

## Frozen numerical configuration

```python
PATCH_SIZE_M = 2.0
CELL_M = 0.025
GRID_HW = 80
PIXEL_SUPPORT_SIGMA = 3.0
MAX_SOURCE_RANGE_M = 30.0
MIN_SOURCE_RANGE_M = 2.5
MAX_FOOTPRINT_ASPECT = 40.0
MAX_FOOTPRINT_AREA_M2 = 0.20
POSE_SHIFT_LIMIT_CELL = 0.5
LOG_GAIN_LIMIT = 0.10
HUBER_DELTA = 0.04
TV_WEIGHT = 5.0e-4
COARSE_TIE_WEIGHT = 2.0e-3
SOLVER_STEPS = 300
LEARNING_RATE = 2.0e-2
HELDOUT_TIME_FRACTION = 0.20
RANDOM_SEED = 145
```

These constants are shared by all six patches. Any change after P0 manifest freeze requires a new DB-145 run ID and invalidates comparisons from the older run.

---

### Task 1: Geometry and anisotropic footprint primitives

**Files:**
- Create: `agent/db145_ground_operator/__init__.py`
- Create: `agent/db145_ground_operator/config.py`
- Create: `agent/db145_ground_operator/geometry.py`
- Create: `agent/db145_ground_operator/tests/test_geometry.py`

- [ ] **Step 1: Write failing plane-intersection and footprint tests**

```python
import numpy as np

from agent.db145_ground_operator.geometry import (
    PixelFootprint,
    intersect_rays_with_plane,
    pixel_footprint_on_plane,
)


def test_intersect_rays_with_horizontal_plane():
    origins = np.array([[0.0, 0.0, 2.0]])
    rays = np.array([[0.0, 1.0, -1.0]])
    xyz, valid = intersect_rays_with_plane(
        origins, rays, np.array([0.0, 0.0, 1.0]), -0.0
    )
    assert valid.tolist() == [True]
    np.testing.assert_allclose(xyz[0], [0.0, 2.0, 0.0], atol=1e-8)


def test_grazing_pixel_footprint_is_anisotropic():
    K = np.array([[1000.0, 0.0, 1000.0], [0.0, 1000.0, 775.0], [0.0, 0.0, 1.0]])
    T_city_cam = np.eye(4)
    T_city_cam[:3, 3] = [0.0, 0.0, 2.0]
    fp = pixel_footprint_on_plane(
        uv=np.array([1000.0, 875.0]),
        K=K,
        T_city_cam=T_city_cam,
        plane_n=np.array([0.0, 0.0, 1.0]),
        plane_d=0.0,
    )
    assert isinstance(fp, PixelFootprint)
    assert fp.valid
    assert fp.aspect_ratio > 5.0
    assert fp.area_m2 > 0.0
```

- [ ] **Step 2: Run the test and verify it fails because the module is absent**

Run:

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_geometry.py
```

Expected: import failure for `agent.db145_ground_operator.geometry`.

- [ ] **Step 3: Implement immutable config and geometry functions**

Use a frozen `ExperimentConfig` dataclass containing the numerical constants above. `pixel_footprint_on_plane()` must:

1. create camera rays for `(u±0.5,v)` and `(u,v±0.5)`;
2. transform them by `T_city_cam`;
3. intersect them with `n·X+d=0`;
4. use central differences to form `J=d(city_xy)/d(pixel_uv)`;
5. set `cov_xy = J @ J.T / 12 + I * CELL_M**2 / 12`;
6. report eigendecomposition-derived area and aspect;
7. return invalid rather than extrapolate when any ray is behind the camera, near the horizon, non-finite, or exceeds the fixed footprint limits.

The public dataclass is:

```python
@dataclass(frozen=True)
class PixelFootprint:
    center_xy: np.ndarray
    covariance_xy: np.ndarray
    jacobian_xy_uv: np.ndarray
    area_m2: float
    aspect_ratio: float
    range_m: float
    valid: bool
```

- [ ] **Step 4: Run geometry tests**

Run:

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_geometry.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add agent/db145_ground_operator
git commit -m "feat(db145): add ground footprint geometry"
```

---

### Task 2: Differentiable EWA operator and six-slot median baseline

**Files:**
- Create: `agent/db145_ground_operator/operator.py`
- Create: `agent/db145_ground_operator/baseline.py`
- Create: `agent/db145_ground_operator/tests/test_operator.py`
- Create: `agent/db145_ground_operator/tests/test_baseline.py`

- [ ] **Step 1: Write failing operator tests**

```python
import numpy as np
import torch

from agent.db145_ground_operator.operator import EWAObservationSet


def test_isotropic_one_texel_observation_is_identity():
    obs = EWAObservationSet.from_numpy(
        centers_cell=np.array([[1.0, 1.0]], np.float32),
        covariance_cell=np.array([[[0.03, 0.0], [0.0, 0.03]]], np.float32),
        source_ids=np.array([0], np.int64),
        rgb=np.array([[0.2, 0.4, 0.6]], np.float32),
        grid_hw=(3, 3),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    texture = torch.zeros((3, 3, 3))
    texture[1, 1] = torch.tensor([0.2, 0.4, 0.6])
    pred = obs.predict(texture, torch.zeros((1, 2)))
    torch.testing.assert_close(pred[0], texture[1, 1], atol=1e-4, rtol=0.0)


def test_anisotropic_operator_blurs_only_long_axis():
    obs = EWAObservationSet.from_numpy(
        centers_cell=np.array([[2.0, 2.0]], np.float32),
        covariance_cell=np.array([[[4.0, 0.0], [0.0, 0.03]]], np.float32),
        source_ids=np.array([0], np.int64),
        rgb=np.zeros((1, 3), np.float32),
        grid_hw=(5, 5),
        support_sigma=3.0,
        pose_shift_limit_cell=0.5,
    )
    vertical = torch.zeros((5, 5, 3))
    vertical[:, 2] = 1.0
    horizontal = torch.zeros((5, 5, 3))
    horizontal[2, :] = 1.0
    shift = torch.zeros((1, 2))
    pred_vertical = obs.predict(vertical, shift)[0, 0]
    pred_horizontal = obs.predict(horizontal, shift)[0, 0]
    assert float(pred_horizontal) > float(pred_vertical) + 0.25
```

The second test compares two complete tensors and asserts the expected directional contrast ordering.

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_operator.py agent/db145_ground_operator/tests/test_baseline.py
```

Expected: missing modules.

- [ ] **Step 3: Implement fixed-support differentiable EWA**

`EWAObservationSet.from_numpy()` precomputes candidate `(observation_id, texel_id, delta_xy)` pairs using covariance support enlarged by `POSE_SHIFT_LIMIT_CELL`. `predict(texture, source_shift)` recomputes Gaussian weights from shifted deltas, uses `scatter_add_` for weighted RGB and normalization, and processes pairs in configurable chunks. No dense `N_obs×6400` tensor is allowed.

Required invariants:

- every observation weight sums to one within `1e-5`;
- source shift is clamped by `0.5*tanh(raw_shift)`;
- observations with empty support are rejected during construction;
- operator provenance retains source ID, frame index, camera index, raw `(u,v)`, footprint area/aspect, and RGB.

- [ ] **Step 4: Implement baseline**

`six_slot_median(samples, n_cells, slots=6)` accepts one projected colour per `(source_view, texel)`, ranks source views by ground range, keeps at most six distinct sources, and returns:

```python
BaselineResult(
    texture_rgb: np.ndarray,   # H×W×3 float32
    valid: np.ndarray,         # H×W bool
    source_count: np.ndarray,  # H×W uint16
)
```

The baseline must be order-invariant, use a true channel-wise median, and leave cells with zero observations black/invalid.

- [ ] **Step 5: Run operator/baseline tests**

Expected: all pass on CPU and CUDA when available.

- [ ] **Step 6: Commit**

```powershell
git add agent/db145_ground_operator
git commit -m "feat(db145): add anisotropic operator and median baseline"
```

---

### Task 3: Observability, automatic scene roles, patch choice, and held-out split

**Files:**
- Create: `agent/db145_ground_operator/observability.py`
- Create: `agent/db145_ground_operator/tests/test_observability.py`

- [ ] **Step 1: Write failing deterministic-selection tests**

Tests must cover:

- scene role chooses minimum curvature for `dry_straight` and maximum curvature for `dry_turn`;
- the same log cannot fill both roles;
- `wet_or_specular` is fixed by an evidence label from prior project diagnosis, not reconstruction output;
- high patch maximizes the frozen observability score;
- low patch minimizes it subject to minimum evidence;
- results are invariant to candidate input order;
- held-out groups are disjoint from training groups and contain at least 10% of valid observations.

Use exact synthetic `SceneMotion` and `PatchObservability` instances and assert exact selected IDs.

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_observability.py
```

- [ ] **Step 3: Implement frozen scene-role selection**

The P0 candidate pool is historical and fixed before result images:

```python
SCENE_CANDIDATES = {
    "02a00399-3857-444e-8db3-a8f58489c394": {"split": "val", "wet": False},
    "02678d04-cc9f-3148-9f95-1ba66347dff9": {"split": "val", "wet": False},
    "2c652f9e-8db8-3572-aa49-fae1344a875b": {"split": "val", "wet": False},
    "8749f79f-a30b-3c3f-8a44-dbfa682bbef1": {"split": "val", "wet": False},
    "05fa5048-f355-3274-b565-c0ddc547b315": {
        "split": "val",
        "wet": True,
        "evidence": "DB-128 wet-road Lambertian failure",
    },
}
```

For the four dry candidates, read the already frozen v15 window when available, otherwise the documented canonical window. Compute path length, endpoint displacement ratio, total absolute yaw change, maximum lateral deviation from the endpoint chord, and dmax. Reject dmax<8m. Normalize curvature metrics with robust ranks; minimum score is straight and maximum is turn. The wet role is `05fa5048-f355-3274-b565-c0ddc547b315` because its wet-road failure was diagnosed before DB-145.

- [ ] **Step 4: Implement patch and held-out selection**

Patch candidates are generated from the selected window trajectory every ten frames at lateral offsets `[-2,-1,0,1,2]m`. Reject candidates that:

- lack a local plane with LiDAR RMSE≤0.05m;
- overlap a dynamic annotation footprint with a 0.5m margin;
- lie outside 2.5–30m source range for all views;
- have less than 20% coarse texel coverage.

Frozen score:

```python
score = (
    0.35 * coverage_fraction
    + 0.20 * clipped_log_view_count
    + 0.20 * angular_diversity
    + 0.15 * subpixel_phase_entropy
    + 0.10 * camera_diversity
    - 0.15 * clipped_log_median_aspect
)
```

Select the highest score and lowest score above the evidence floor, at least 4m apart. Held-out selection prefers a complete camera with ≥10% and ≤35% of observations; otherwise freeze the central contiguous 20% time block. Persist exact group IDs before optimization.

- [ ] **Step 5: Run tests and commit**

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_observability.py
git add agent/db145_ground_operator
git commit -m "feat(db145): freeze observability and heldout selection"
```

---

### Task 4: Robust inverse solver and held-out truth test

**Files:**
- Create: `agent/db145_ground_operator/solver.py`
- Create: `agent/db145_ground_operator/evaluate.py`
- Create: `agent/db145_ground_operator/tests/test_solver.py`
- Create: `agent/db145_ground_operator/tests/test_evaluate.py`

- [ ] **Step 1: Write a synthetic recovery kill-test**

Build a 32×32 texture with diagonal paint lines. Generate observations from two complementary anisotropic footprint families, add fixed per-source gains and small subcell shifts, and reserve one source family as held-out. Assertions:

- the B solver reduces held-out MAE by at least 15% relative to six-slot median;
- recovered shifts stay within 0.5 cell and zero-mean gauge;
- gains stay within ±10%;
- an unobserved quadrant remains invalid rather than regularizer-filled;
- when all training footprints share the same long axis, the observability/uncertainty marks the unresolved direction and the test does not demand recovery.

- [ ] **Step 2: Verify the solver test fails**

Run:

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_solver.py
```

- [ ] **Step 3: Implement B solver**

Optimize:

```text
Huber(gain[source] * raw_rgb - H(T, shift[source]))
+ 5e-4 * edge-aware TV(T)
+ 2e-3 * coarse consistency(T)
+ 1e-3 * ||shift||²
+ 1e-3 * ||log_gain||²
```

Rules:

- initialize `T` from valid baseline pixels only;
- do not optimize plane, camera intrinsics, camera extrinsics, or per-pixel depth;
- use `shift=0.5*tanh(raw_shift)`, subtract mean shift each step, and pin the time-nearest source;
- use `gain=exp(0.10*tanh(raw_gain))`, normalize geometric mean each step, and pin the same source;
- unknown texels are tracked separately from texture values and remain invalid in exported provenance;
- save loss curves and max allocated CUDA memory.

- [ ] **Step 4: Implement C rejection**

Warm-start from B. Compute per-source robust residual median. Reject a source only when it exceeds global median + `3*MAD` and has at least 100 observations; additionally apply the existing relative bright/low-saturation wet-road gate using per-source median normalization. Re-solve with the same hyperparameters. Output the rejected source IDs and reason codes.

- [ ] **Step 5: Implement held-out evaluation**

Metrics on the identical held-out pixels:

- robust RGB MAE (trim top 5%);
- median ΔE-like Euclidean RGB error;
- grayscale SSIM on a dense predicted/raw crop when at least 1000 pixels exist;
- edge MAE on Sobel magnitude;
- coverage and abstention.

Always save raw crop, predicted crop, valid mask, and absolute-error heatmap. Metrics are guards; full-resolution images are required for verdict.

- [ ] **Step 6: Run tests and commit**

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_solver.py agent/db145_ground_operator/tests/test_evaluate.py
git add agent/db145_ground_operator
git commit -m "feat(db145): add inverse solver and heldout evaluation"
```

---

### Task 5: AV2 source-native extraction and evidence gates

**Files:**
- Create: `agent/db145_ground_operator/av2_extract.py`
- Create: `agent/db145_ground_operator/tests/test_av2_extract.py`

- [ ] **Step 1: Write tests using a tiny fake AV2 frame**

The fixture must define one calibrated camera, two poses, a horizontal plane, a synthetic image, one moving box, and one ego box. Assert:

- pixel-centre rays land at the expected city XY;
- pixel-corner covariance has the expected orientation;
- a moving-box-intersecting ray is rejected;
- an ego-body-intersecting ray is rejected;
- held-out source groups never enter training arrays;
- source-native records retain `(frame_idx,camera_idx,u,v)` and are not reduced to grid IDs.

- [ ] **Step 2: Verify tests fail**

- [ ] **Step 3: Implement AV2 adapters**

Reuse project conventions without importing the db89 monolith:

- `AV2RingLoader` and `RING_CAMS_7`;
- camera capture-time pose interpolation from `city_SE3_egovehicle.feather`;
- `T_ego_cam` and `K` from frame calibration;
- accumulated LiDAR transformed into city coordinates with per-point offsets;
- dynamic boxes from `annotations.feather`;
- analytic ego image mask from `db123_egomask_analytic.py` when available.

For each source view:

1. project patch corners to find a padded image bounding box;
2. iterate every raw image pixel in that box;
3. intersect centre and four half-pixel rays with the fixed local plane;
4. reject range, horizon, footprint, saturation, ego-body, dynamic-box, and footprint/non-patch cases;
5. append raw RGB plus complete footprint and provenance.

For baseline A, independently project every latent texel centre into the same source views, apply the same evidence gates, bilinear-sample raw RGB, and produce one sample per `(source_view, texel)`.

- [ ] **Step 4: Run tests and commit**

```powershell
python -m pytest -q agent/db145_ground_operator/tests/test_av2_extract.py
git add agent/db145_ground_operator
git commit -m "feat(db145): add source-native AV2 extractor"
```

---

### Task 6: Reproducible remote runner, manifest freeze, and reports

**Files:**
- Create: `agent/db145_ground_operator/remote_run.py`
- Create: `agent/db145_ground_operator/report.py`
- Create: `agent/db145_ground_operator/launch.py`
- Create: `agent/db145_ground_operator/tests/test_manifest.py`

- [ ] **Step 1: Write manifest and resume tests**

Assert:

- manifest includes git commit, config hash, CUDA/Torch versions, scene roles, full UUIDs, windows, patch centres/planes, held-out groups, and selection evidence;
- P1 refuses to start when current config hash differs from frozen P0;
- completed patches are skipped only when their output checksum matches;
- executor URL/token strings never appear in manifest, logs, archive, or report.

- [ ] **Step 2: Implement P0**

Run:

```bash
python -m agent.db145_ground_operator.remote_run p0 \
  --run-id db145_r1_20260717 \
  --drive-root /content/drive/MyDrive/koi_waymo2pano_colab/results/db145_ground_operator
```

The command:

1. install/check dependencies;
2. fetch only pose/calibration metadata for dry scene selection;
3. freeze role selection to `manifest.json`;
4. localize each selected log sequentially;
5. fit planes and compute patch observability;
6. freeze high/low patch and held-out groups;
7. save footprint/observability previews;
8. copy P0 evidence to Drive;
9. print `DB145_P0_FROZEN <config_sha>`.

No optimizer is allowed in P0.

- [ ] **Step 3: Implement P1**

Run:

```bash
python -m agent.db145_ground_operator.remote_run p1 \
  --manifest /content/drive/MyDrive/koi_waymo2pano_colab/results/db145_ground_operator/db145_r1_20260717/manifest.json
```

The command processes one log at a time:

1. verify config hash;
2. extract A and B/C observations;
3. solve A/B/C sequentially by patch;
4. evaluate held-out;
5. save source overlays, observability, A/B/C latent, uncertainty, held-out render/raw/error, metrics, and checksums;
6. sync the log result to Drive before deleting ephemeral localized data;
7. enforce elapsed GPU time and stop before 4 hours.

- [ ] **Step 4: Implement deployment**

`launch.py`:

- reads only `COLAB_URL` and `COLAB_TOKEN` from the process environment;
- creates a zip from committed `agent/db145_ground_operator/` files;
- uploads it using `scripts/_colab.py` protocol;
- launches a detached file-style remote command;
- polls Drive marker files, not only executor job state;
- never prints the token.

- [ ] **Step 5: Run all local tests and commit**

```powershell
python -m pytest -q agent/db145_ground_operator/tests
git status --short
git add agent/db145_ground_operator docs/superpowers/plans/2026-07-17-db145-sensor-native-ground-operator.md
git commit -m "feat(db145): add reproducible patch experiment runner"
```

Expected: all DB-145 tests pass; only intended files are tracked.

---

### Task 7: Execute P0 on the live L4 and freeze the experiment

**Files produced:**
- `deliverables/db145_ground_operator/manifest.json`
- `deliverables/db145_ground_operator/p0_scene_selection.json`
- `deliverables/db145_ground_operator/p0_observability_board.jpg`
- Drive mirror under `results/db145_ground_operator/<run_id>/`

- [ ] **Step 1: Verify live runtime**

Expected GPU: `NVIDIA L4`, free memory >20GB, no active job.

- [ ] **Step 2: Deploy committed code**

Upload archive and verify remote SHA256 against local SHA256.

- [ ] **Step 3: Run P0 only**

Do not start P1 in the same command. P0 must finish and write the immutable manifest.

- [ ] **Step 4: Fetch and audit manifest**

Checks:

- exactly three roles and unique logs;
- wet role is historical `05fa5048-f355-3274-b565-c0ddc547b315`;
- BMW appears at most once;
- exactly two patches per log;
- selections were made before any reconstruction metrics exist;
- held-out groups are non-empty and disjoint;
- local-plane RMSE and observability fields are present.

- [ ] **Step 5: Commit frozen manifest**

Only the compact manifest and P0 summary enter Git; raw observations remain in Drive.

---

### Task 8: Execute P1, vision-check six patches, and render a verdict

**Files produced:**
- `deliverables/db145_ground_operator/{dry_straight,dry_turn,wet_or_specular}_{high,low}/`
- `deliverables/db145_ground_operator/verdict_board.jpg`
- `deliverables/db145_ground_operator/verdict.json`

- [ ] **Step 1: Run P1 from the frozen manifest**

Process one log at a time and check Drive completion markers. Stop on config mismatch, data corruption, repeated CUDA failure, or elapsed GPU budget.

- [ ] **Step 2: Fetch compact evidence**

Fetch metrics JSON, boards, full-resolution patch textures, held-out raw/pred/error images, uncertainty, and provenance summaries. Do not fetch multi-gigabyte raw observation archives unless debugging a failed patch.

- [ ] **Step 3: Inspect every image**

For all six patches compare:

- raw source + footprint overlay;
- A/B/C latent texture;
- uncertainty/unknown;
- held-out raw vs A/B/C render;
- ERP crop if available.

Reject double edges, ringing, grid/quilt, fake paint, drift, or “sharp only in latent”.

- [ ] **Step 4: Apply DB-145 verdict logic mechanically**

- Kill if B does not improve held-out on both dry high-observability patches.
- Kill if improvement requires scene-specific changes.
- Downgrade wet to rejection/abstain if dry passes and wet fails.
- Downgrade to conditional observability-gated use if only turn/high-observability passes.
- Promote only if all brief pass criteria hold.

- [ ] **Step 5: Record result**

Update `agent/progress.md` with exact metrics, vision verdict, runtime/VRAM, failures, artifacts, and next decision. Update DB-145 status in `agent/decision_briefs.md` to DONE/KILLED/CONDITIONAL. Do not alter v15.

- [ ] **Step 6: Final verification and commit**

Run:

```powershell
python -m pytest -q agent/db145_ground_operator/tests
git diff --check
git status --short
```

Commit only DB-145 code, compact evidence, and living-doc updates.

---

## Plan self-review

- **Spec coverage:** Question, A/B/C, three roles, two automatic patches, held-out raw validation, provenance, observability, vision review, kill criteria, max scope, no production mutations, and GPU budget all map to Tasks 1–8.
- **Scope separation:** db89/db144/v15 are read-only references. DB-145 is a standalone package and Drive result tree.
- **Truth guard:** synthetic sharpness is insufficient; held-out raw-camera error and full-resolution images are mandatory.
- **Generality guard:** scene/patch/held-out choices and all parameters freeze before reconstruction.
- **Resource guard:** source observations are chunked; logs are localized sequentially; P1 stops before 4 L4 GPU-hours.
- **Secret guard:** credentials remain process environment only and are excluded from manifests, logs, archives, and Git.
