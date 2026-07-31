# DB-212 Multi-Dataset Reproducibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing PandaSet, nuScenes, and Waymo Perception one-off remote experiments into a replayable pseudo-AV2 adapter pipeline with explicit camera, timing, geometry, evidence, and honest-black contracts.

**Architecture:** Keep the production `db89` panorama kernel dataset-agnostic. Dataset-specific adapters convert source records into a versioned pseudo-AV2 directory, while one shared contract module writes and validates a manifest before rendering. The AV2 loader accepts an explicit camera tuple (or the existing `W2P_RING_CAMS` compatibility environment variable) so the renderer consumes the manifest-defined ring rather than a hard-coded seven-camera assumption.

**Tech Stack:** Python 3.10+, `pytest`, NumPy, SciPy `Rotation`, pandas/pyarrow, Pillow; optional source SDKs stay isolated inside their adapters.

**Scope split:** Waymo E2E is a separate DB-213 plan because it has no LiDAR/boxes/ego pose and therefore is not a pseudo-AV2 conversion of the same contract. This plan still adds the B-only contract fields E2E will consume. Algorithm changes for blue-person overlap, text tearing, and radiometry are a separate DB-214 plan and do not enter this adapter branch.

---

## File map

- Modify `code/waymo2panorama/data_io/av2_loader.py`: explicit camera/anchor configuration; no dataset knowledge.
- Create `agent/db181_multids/__init__.py`: package marker and public contract exports.
- Create `agent/db181_multids/contract.py`: manifest schema, evidence-mode rules, JSON I/O, directory validation.
- Create `agent/db181_multids/geometry.py`: checked quaternion/SE(3) conversions shared by adapters.
- Create `agent/db181_multids/io.py`: atomic link/copy and feather writers; no source-SDK imports.
- Create `agent/db181_multids/pandaset_adapter.py`: PandaSet 80-frame converter.
- Create `agent/db181_multids/nuscenes_adapter.py`: nuScenes scene converter, explicitly B-only by default.
- Create `agent/db181_multids/waymo_perception_adapter.py`: Waymo v2 component converter with 252-degree support declaration.
- Create `agent/db181_multids/validate.py`: CLI validator and compact preflight report.
- Create `agent/db181_multids/tests/`: behavior tests and synthetic source fixtures.
- Create `deliverables/db181_multids/manifests/`: committed manifests for every rerun pilot; images remain under the existing deliverable directory.

---

### Task 1: Make camera membership an explicit loader contract

**Files:**
- Modify: `code/waymo2panorama/data_io/av2_loader.py:19-189`
- Create: `agent/db181_multids/tests/test_loader_camera_contract.py`

- [ ] **Step 1: Write the failing resolver tests**

```python
from waymo2panorama.data_io.av2_loader import RING_CAMS_7, resolve_ring_cameras


def test_camera_contract_defaults_to_av2(monkeypatch):
    monkeypatch.delenv("W2P_RING_CAMS", raising=False)
    assert resolve_ring_cameras() == RING_CAMS_7


def test_camera_contract_uses_ordered_environment_value(monkeypatch):
    monkeypatch.setenv("W2P_RING_CAMS", "front,left,rear,right")
    assert resolve_ring_cameras() == ("front", "left", "rear", "right")


def test_camera_contract_rejects_duplicates(monkeypatch):
    monkeypatch.setenv("W2P_RING_CAMS", "front,left,front")
    with pytest.raises(ValueError, match="duplicate"):
        resolve_ring_cameras()
```

- [ ] **Step 2: Run RED**

Run: `$env:PYTHONPATH='code'; python -m pytest agent/db181_multids/tests/test_loader_camera_contract.py -q`

Expected: collection succeeds and fails because `resolve_ring_cameras` does not exist.

- [ ] **Step 3: Implement the minimal resolver and instance state**

Add `import os`, define `resolve_ring_cameras(explicit=None)`, reject empty names/duplicates, and in `AV2RingLoader.__init__` assign `self._cameras`. Replace every internal `RING_CAMS_7` iteration and hard-coded `ring_front_center` anchor with `self._cameras` and `self._cameras[0]`. Keep `RING_CAMS_7` unchanged as the AV2 default.

- [ ] **Step 4: Extend the test with a two-camera synthetic pseudo log**

Create two tiny JPEG streams plus calibration feather tables, instantiate `AV2RingLoader(log_dir, cameras=("front", "rear"))`, and assert that calibration, indexing, nearest-frame timestamps, and `FrameSample` keys contain exactly those two cameras in order.

- [ ] **Step 5: Run GREEN and the package smoke test**

Run: `$env:PYTHONPATH='code'; python -m pytest agent/db181_multids/tests/test_loader_camera_contract.py -q`

Expected: all tests pass with no warnings.

- [ ] **Step 6: Commit**

```powershell
git add code/waymo2panorama/data_io/av2_loader.py agent/db181_multids/tests/test_loader_camera_contract.py
git commit -m "feat(db212): make ring-camera membership explicit"
```

---

### Task 2: Define the evidence and manifest contract

**Files:**
- Create: `agent/db181_multids/__init__.py`
- Create: `agent/db181_multids/contract.py`
- Create: `agent/db181_multids/tests/test_contract.py`

- [ ] **Step 1: Write failing manifest-invariant tests**

Tests must construct real `ConversionManifest` objects and assert:

```python
def test_waymo_252_requires_honest_black_interval():
    manifest = valid_manifest(
        dataset="waymo_perception",
        cameras=("front", "front_left", "side_left", "side_right", "front_right"),
        supported_azimuth_deg=((-126.0, 126.0),),
        honest_black_azimuth_deg=((126.0, 234.0),),
    )
    manifest.validate()


def test_a_mode_requires_lidar_and_real_mask():
    manifest = valid_manifest(mode="A", has_lidar=False, real_mask_pattern=None)
    with pytest.raises(ValueError, match="A mode requires"):
        manifest.validate()


def test_frame_contract_does_not_silently_pad_pandaset():
    manifest = valid_manifest(dataset="pandaset", source_frame_count=80, output_frame_count=93)
    with pytest.raises(ValueError, match="silent frame padding"):
        manifest.validate()
```

- [ ] **Step 2: Run RED**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests/test_contract.py -q`

Expected: failure because `agent.db181_multids.contract` does not exist.

- [ ] **Step 3: Implement minimal frozen dataclasses**

Implement `FrameRecord`, `CameraRecord`, and `ConversionManifest`. Required manifest fields are: `schema_version`, `dataset`, `source_scene_id`, `output_log_id`, `mode`, ordered `cameras`, `anchor_camera`, source/output frame counts, frame-rate contract, camera/LiDAR timestamps and maximum sync delta, calibration SHA-256, source file hashes, `has_lidar`, `has_ego_pose`, `has_annotations`, `real_mask_pattern`, `faithfill_mask_pattern`, `honest_black_mask_pattern`, supported and honest-black azimuth intervals, coordinate-convention transform, converter git commit, and creation timestamp.

`validate()` must reject duplicate/missing cameras, absent anchor, frame padding, A mode without LiDAR/real-mask evidence, unsupported azimuths not covered by honest black, non-finite transforms, and missing provenance hashes.

- [ ] **Step 4: Implement deterministic JSON round-trip**

`write_json(path)` writes UTF-8 through a sibling temporary file and `Path.replace`; `read_json(path)` reconstructs and validates. JSON uses sorted keys and a trailing newline so manifests are stable under diff.

- [ ] **Step 5: Run GREEN**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests/test_contract.py -q`

Expected: all contract tests pass.

- [ ] **Step 6: Commit**

```powershell
git add agent/db181_multids/__init__.py agent/db181_multids/contract.py agent/db181_multids/tests/test_contract.py
git commit -m "feat(db212): add evidence-bearing conversion manifest"
```

---

### Task 3: Lock geometry and filesystem behavior

**Files:**
- Create: `agent/db181_multids/geometry.py`
- Create: `agent/db181_multids/io.py`
- Create: `agent/db181_multids/tests/test_geometry.py`
- Create: `agent/db181_multids/tests/test_io.py`

- [ ] **Step 1: Write RED tests for geometry**

Use `scipy.spatial.transform.Rotation` to assert quaternion-to-matrix-to-quaternion round trips for identity, 90-degree PandaSet yaw normalization, and near-180-degree rotations. Assert `relative_transform(T_world_ego, T_world_sensor)` reconstructs `T_world_sensor` within `1e-9`. This replaces the scratch scripts' trace-division formula, which is singular near 180 degrees.

- [ ] **Step 2: Write RED tests for materialization**

Create a real temporary source file. Assert `materialize_file(src, dst, prefer_hardlink=True)` produces byte-identical output and falls back to `shutil.copy2` when `os.link` raises `OSError`. Assert feather writers preserve explicit dtypes for empty annotations rather than injecting a fake object at `z=-50`.

- [ ] **Step 3: Run RED**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests/test_geometry.py agent/db181_multids/tests/test_io.py -q`

Expected: module-not-found failures.

- [ ] **Step 4: Implement the minimal shared modules and run GREEN**

Run the same command. Expected: all tests pass; no fake annotation row is produced.

- [ ] **Step 5: Commit**

```powershell
git add agent/db181_multids/geometry.py agent/db181_multids/io.py agent/db181_multids/tests/test_geometry.py agent/db181_multids/tests/test_io.py
git commit -m "feat(db212): add checked geometry and pseudo-av2 IO"
```

---

### Task 4: Recover PandaSet as a reproducible 1+79 A/B adapter

**Files:**
- Create: `agent/db181_multids/pandaset_adapter.py`
- Create: `agent/db181_multids/tests/test_pandaset_adapter.py`

- [ ] **Step 1: Write a synthetic six-camera, two-frame PandaSet fixture**

The fixture must use the real directory and JSON keys from scene 019: camera `intrinsics.json`, `poses.json`, `timestamps.json`, numbered JPEGs, LiDAR `poses.json`, `timestamps.json`, and `.pkl.gz` point tables.

- [ ] **Step 2: Write RED assertions**

Call `convert_pandaset_scene(source_scene, output_root, output_log_id, ego_origin="sensor_rig")`. Assert six ordered pseudo-AV2 cameras, two output frames (never 93), +90-degree ego-axis normalization, camera extrinsics computed relative to the same ego pose chain, point clouds in ego coordinates, an actually empty annotations feather, and a valid manifest declaring `1+1` for the fixture / `1+79` for a full scene.

- [ ] **Step 3: Run RED**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests/test_pandaset_adapter.py -q`

Expected: failure because the adapter does not exist.

- [ ] **Step 4: Implement minimal conversion**

Port only the verified conversion logic from the historical scene-019 scratch run. Use shared geometry, preserve exact source timestamps, compute and record per-camera nearest-LiDAR sync deltas, and make any rig-to-ground origin shift an explicit `--ego-origin ground --ground-quantile 0.05 --ground-radius-m 10` option recorded in the manifest. Never silently shift coordinates.

- [ ] **Step 5: Run GREEN and validate the committed manifest schema**

Run the same test command. Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add agent/db181_multids/pandaset_adapter.py agent/db181_multids/tests/test_pandaset_adapter.py
git commit -m "feat(db212): recover PandaSet pseudo-av2 adapter"
```

---

### Task 5: Recover nuScenes as an explicitly B-only adapter

**Files:**
- Create: `agent/db181_multids/nuscenes_adapter.py`
- Create: `agent/db181_multids/tests/test_nuscenes_adapter.py`

- [ ] **Step 1: Write a minimal metadata fixture**

Provide real-shaped `scene`, `sample`, `sample_data`, `ego_pose`, `calibrated_sensor`, and `sensor` JSON entries for six cameras plus `LIDAR_TOP`; include mismatched 12-Hz camera / 20-Hz LiDAR timestamps.

- [ ] **Step 2: Write RED assertions**

Assert six-camera mapping, microseconds-to-nanoseconds conversion, nearest-sync deltas in the manifest, proper sensor-to-ego LiDAR transform, and `mode="B"`. Calling with `mode="A"` must raise unless an explicit experimental override is passed; even under override, the manifest must retain the observed real-fill evidence rather than claim A-ready.

- [ ] **Step 3: Run RED**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests/test_nuscenes_adapter.py -q`

Expected: adapter-not-found failure.

- [ ] **Step 4: Implement minimal conversion and run GREEN**

Use table indices instead of repeatedly scanning every JSON list. Preserve the real scene identity and source checksums. Do not fabricate annotations or near-ground evidence.

- [ ] **Step 5: Commit**

```powershell
git add agent/db181_multids/nuscenes_adapter.py agent/db181_multids/tests/test_nuscenes_adapter.py
git commit -m "feat(db212): recover nuScenes B-only adapter"
```

---

### Task 6: Recover Waymo Perception with an honest 252-degree contract

**Files:**
- Create: `agent/db181_multids/waymo_perception_adapter.py`
- Create: `agent/db181_multids/tests/test_waymo_perception_adapter.py`

- [ ] **Step 1: Write a tiny parquet fixture matching Waymo v2 component columns**

Include `camera_image`, `camera_calibration`, `vehicle_pose`, `lidar`, `lidar_calibration`, and `lidar_box` rows for two timestamps and all five cameras.

- [ ] **Step 2: Write RED assertions**

Assert Waymo-camera to OpenCV axis conversion, TOP range-image point reconstruction, box heading quaternion, five-camera frame counts, and manifest coverage. The manifest must state supported `252 degrees` and unsupported/honest-black `108 degrees`; no function may label this output full-360 real coverage.

- [ ] **Step 3: Run RED**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests/test_waymo_perception_adapter.py -q`

Expected: adapter-not-found failure.

- [ ] **Step 4: Implement conversion and run GREEN**

Stream parquet batches; do not load image blobs for an entire segment at once. Include component file checksums and segment context name in the manifest.

- [ ] **Step 5: Commit**

```powershell
git add agent/db181_multids/waymo_perception_adapter.py agent/db181_multids/tests/test_waymo_perception_adapter.py
git commit -m "feat(db212): recover Waymo Perception adapter"
```

---

### Task 7: Add preflight validation and renderer handoff

**Files:**
- Create: `agent/db181_multids/validate.py`
- Create: `agent/db181_multids/tests/test_validate.py`
- Modify: `agent/decision_briefs.md:8-20`

- [ ] **Step 1: Write RED validator tests**

Assert failures for missing images, missing calibration rows, unordered timestamps, excessive sync delta, hash mismatch, camera-list disagreement, A without LiDAR/masks, and unsupported angles without honest black. Assert the compact JSON report separates `error`, `warning`, and `documented_limitation`.

- [ ] **Step 2: Run RED**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests/test_validate.py -q`

Expected: validator-not-found failure.

- [ ] **Step 3: Implement validator and renderer environment export**

`python -m agent.db181_multids.validate LOG_DIR --json REPORT.json` validates the manifest plus filesystem and exits non-zero on errors. `--emit-env` prints only `W2P_RING_CAMS=<ordered names>` and the log ID; it must never print credentials or mutate either bundled source tree.

- [ ] **Step 4: Run the complete local suite**

Run: `$env:PYTHONPATH='code;.'; python -m pytest agent/db181_multids/tests -q`

Expected: all tests pass; at least one test is collected from every task.

- [ ] **Step 5: Mutation-check critical assertions**

Temporarily invert the A-mode LiDAR rule, delete the Waymo honest-black interval, and allow a duplicate camera. Each corresponding test must fail. Revert each mutation and rerun the complete suite.

- [ ] **Step 6: Update the decision brief with evidence status**

Record that the old images are visual evidence only; adapters become code-verified only after this suite; production readiness still requires remote full-scene pilots and manifests.

- [ ] **Step 7: Commit**

```powershell
git add agent/db181_multids/validate.py agent/db181_multids/tests/test_validate.py agent/decision_briefs.md
git commit -m "test(db212): enforce multi-dataset evidence contract"
```

---

### Task 8: Run bounded remote pilots after tunnel authorization

**Files:**
- Create: `deliverables/db181_multids/manifests/<dataset>-<scene>.json`
- Create: `deliverables/db181_multids/db212_pilot_summary.json`
- Create: `deliverables/db181_multids/db212_visual_board_<dataset>.png`
- Modify: `agent/progress.md`

- [ ] **Step 1: Verify runtime and inputs without launching render work**

Call `/status`; then use `/exec` to record GPU model/free memory, CPU count, disk free, installed source SDKs, source dataset paths, and active jobs. Stop if the source checksum differs from the manifest or free scratch is below 150 GB.

- [ ] **Step 2: Run adapters/preflight concurrently on CPU**

Launch PandaSet, nuScenes, and Waymo Perception conversion jobs independently. Each job writes its own completion sentinel, log, manifest, and preflight report. No renderer starts for a failed preflight.

- [ ] **Step 3: Start at most two render workers, then verify actual utilization**

Start one PandaSet A/B anchor and one Waymo Perception anchor. After 150 seconds run `nvidia-smi`; if utilization is below 10%, inspect full logs and bottlenecks before adding jobs. Add nuScenes B only when memory and I/O headroom are measured. Do not launch uncapped full datasets.

- [ ] **Step 4: Apply the predeclared gates**

PandaSet: two scenes, real-filled lower half at least 60%, black at most 5%, no frame padding. nuScenes: B-band supported region at least 95% real coverage; A remains killed. Waymo Perception: supported 252 degrees continuous and unsupported 108 degrees byte-black. Every dataset: camera sync report, source-ID map, real/faithfill/black masks, and three timepoints.

- [ ] **Step 5: Pull and visually inspect every board at full resolution**

Classify each failure as sensor-origin, timing, calibration/geometry, depth/projection, photometric, fusion/ownership, or source-less. Metrics are guards; visual evidence decides.

- [ ] **Step 6: Commit only reproducible evidence**

```powershell
git add deliverables/db181_multids/manifests deliverables/db181_multids/db212_pilot_summary.json deliverables/db181_multids/db212_visual_board_*.png agent/progress.md
git commit -m "exp(db212): validate reproducible multi-dataset pilots"
```

---

## Self-review

- Spec coverage: explicit camera configuration, calibration/pose geometry, timing, manifest/provenance, A/B evidence, PandaSet 1+79, nuScenes B-only, Waymo 252+108 honest black, remote bounded pilots, visual inspection, and mutation tests all map to tasks above.
- Deliberate exclusions: Waymo E2E and the three algorithm changes are separate designs because their state models differ; this prevents an adapter task from silently changing `db89` semantics.
- Placeholder scan: every task names concrete files, commands, expected results, and failure behavior.
- Type consistency: all adapters emit one `ConversionManifest`; the loader consumes the same ordered camera tuple; the validator checks both before renderer handoff.
