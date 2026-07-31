# DB-226 Cross-Log Luminance Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a raw same-ray observation bundle and held-out-by-log falsification report that can decide whether a fixed per-camera monotonic luminance response is justified, without changing rendered pixels.

**Architecture:** A new pure module owns the versioned raw-observation contract, fixed absolute brightness profiles, deterministic log split, and no-leakage evaluation. The existing renderer only collects already-available same-ray evidence and writes one NPZ plus a JSON sidecar per anchor. A remote driver selects logs before results are observed, runs four workers concurrently, and archives every sidecar/bundle; the candidate pixel transform is a separate gated plan only if this diagnostic passes.

**Tech Stack:** Python 3, NumPy, JSON/NPZ, pytest, existing AV2 renderer/worker, PowerShell plus the authorized Colab executor.

---

## File map

- Create `scripts/phase3/db226_luma_response.py`: pure observation validation, fixed-bin profiling, deterministic log split, and held-out shape evaluation.
- Create `scripts/phase3/test_db226_luma_response.py`: synthetic P0 tests for raw provenance, fixed bins, split leakage, transferable/non-transferable shape, and determinism.
- Modify `scripts/phase3/db89_ghost_recovery.py`: collect raw same-ray samples already present in the diagnostic branch and write versioned NPZ/JSON references; no render-path change when `COLOR_DIAG=False`.
- Create `agent/db115_drivers/db226_monotonic_luma_job.py`: discover/select logs, freeze split, run four workers, verify artifacts and hashes, and copy the archive to Drive.
- Create `agent/db115_drivers/db226_analyze.py`: consume only frozen bundles, produce pair/log/bin coverage and held-out-vs-zero-shape evaluation.
- Modify `scripts/phase3/test_db214_artifact_primitives.py`: source-contract assertions that DB-226 remains gated and raw observations are not gain-corrected before export.

### Task 1: Raw same-ray observation contract and fixed profiles

**Files:**
- Create: `scripts/phase3/db226_luma_response.py`
- Create: `scripts/phase3/test_db226_luma_response.py`

- [ ] **Step 1: Write failing raw-contract and fixed-bin tests**

```python
def test_collect_pair_samples_preserves_raw_rgb_and_ids():
    rgb_a = np.array([[10, 20, 30], [40, 50, 60]], np.float32)
    rgb_b = np.array([[12, 24, 36], [44, 55, 66]], np.float32)
    batch = collect_pair_samples(
        rgb_a, rgb_b,
        erp_flat_index=np.array([7, 11]),
        xy_a=np.array([[.1, .2], [.3, .4]]),
        xy_b=np.array([[.5, .6], [.7, .8]]),
        depth_m=np.array([9., 18.]),
        parallax_deg=np.array([1., 2.]),
    )
    np.testing.assert_array_equal(batch["rgb_a"], rgb_a)
    np.testing.assert_array_equal(batch["rgb_b"], rgb_b)
    np.testing.assert_array_equal(batch["erp_flat_index"], [7, 11])
    assert "gain" not in batch


def test_fixed_profile_uses_shared_absolute_edges_and_signed_residual():
    y = np.array([8., 12., 24., 40., 72., 112., 176., 224.])
    rgb_a = np.repeat(y[:, None], 3, axis=1)
    rgb_b = rgb_a * np.exp(np.linspace(-.20, .20, len(y)))[:, None]
    batch = collect_pair_samples(
        rgb_a, rgb_b,
        erp_flat_index=np.arange(len(y)),
        xy_a=np.zeros((len(y), 2)), xy_b=np.zeros((len(y), 2)),
        depth_m=np.full(len(y), 20.), parallax_deg=np.full(len(y), 1.),
    )
    report = fixed_brightness_profile(batch, gain_log_a=0., gain_log_b=0., min_count=1)
    medians = [row["signed_residual_median"] for row in report["bins"]
               if row["signed_residual_median"] is not None]
    assert medians[0] < 0 < medians[-1]
    np.testing.assert_allclose(report["log_luma_edges"], DEFAULT_LOG_LUMA_EDGES)


def test_unsupported_fixed_bins_remain_null():
    batch = synthetic_constant_batch(luma=64., n=8)
    report = fixed_brightness_profile(batch, gain_log_a=0., gain_log_b=0., min_count=9)
    assert all(row["reliable"] is False for row in report["bins"])
    assert all(row["signed_residual_median"] is None for row in report["bins"])
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest scripts/phase3/test_db226_luma_response.py -q`

Expected: collection failure because `scripts.phase3.db226_luma_response` does not exist.

- [ ] **Step 3: Implement the minimal raw contract and profile**

```python
SCHEMA_VERSION = "db226_same_ray_v1"
DEFAULT_LOG_LUMA_EDGES = np.log(
    np.asarray([2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 160, 192, 224, 255], float)
)


def collect_pair_samples(rgb_a, rgb_b, *, erp_flat_index, xy_a, xy_b,
                         depth_m, parallax_deg):
    a = np.asarray(rgb_a, np.float32)
    b = np.asarray(rgb_b, np.float32)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3:
        raise ValueError("rgb_a and rgb_b must have matching shape (N, 3)")
    n = len(a)
    arrays = {
        "erp_flat_index": np.asarray(erp_flat_index, np.int64),
        "rgb_a": a.copy(), "rgb_b": b.copy(),
        "xy_a": np.asarray(xy_a, np.float32),
        "xy_b": np.asarray(xy_b, np.float32),
        "depth_m": np.asarray(depth_m, np.float32),
        "parallax_deg": np.asarray(parallax_deg, np.float32),
    }
    if any(len(value) != n for value in arrays.values()):
        raise ValueError("all pair-sample arrays must have the same length")
    finite = np.ones(n, bool)
    for value in arrays.values():
        finite &= np.isfinite(value).all(axis=1) if value.ndim == 2 else np.isfinite(value)
    finite &= (a.mean(1) > 0) & (b.mean(1) > 0)
    return {key: value[finite] for key, value in arrays.items()}


def fixed_brightness_profile(batch, *, gain_log_a, gain_log_b,
                             edges=DEFAULT_LOG_LUMA_EDGES, min_count=32,
                             max_parallax_deg=None):
    a = batch["rgb_a"].astype(np.float64)
    b = batch["rgb_b"].astype(np.float64)
    raw_a = np.log(np.maximum(a.mean(1), 1e-6))
    raw_b = np.log(np.maximum(b.mean(1), 1e-6))
    corrected_a = raw_a + float(gain_log_a)
    corrected_b = raw_b + float(gain_log_b)
    shared = 0.5 * (corrected_a + corrected_b)
    residual = corrected_b - corrected_a
    saturated = ((a <= 1) | (a >= 254) | (b <= 1) | (b >= 254)).any(1)
    usable = ~saturated
    if max_parallax_deg is not None:
        usable &= batch["parallax_deg"] <= max_parallax_deg
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        selected = (shared >= lo) & (shared < hi)
        kept = selected & usable
        values = residual[kept]
        reliable = len(values) >= min_count
        rows.append({
            "lo": float(lo), "hi": float(hi), "n": int(selected.sum()),
            "usable_n": int(kept.sum()), "saturated_n": int((selected & saturated).sum()),
            "reliable": reliable,
            "signed_residual_median": float(np.median(values)) if reliable else None,
            "signed_residual_mad": float(np.median(np.abs(values - np.median(values)))) if reliable else None,
            "abs_residual_p90": float(np.quantile(np.abs(values), .90)) if reliable else None,
        })
    return {"log_luma_edges": np.asarray(edges).tolist(), "bins": rows}
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest scripts/phase3/test_db226_luma_response.py -q`

Expected: all Task-1 tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- scripts/phase3/db226_luma_response.py scripts/phase3/test_db226_luma_response.py
git commit -m "feat(db226): preserve raw same-ray luminance evidence"
```

### Task 2: Gated renderer export with audit metadata

**Files:**
- Modify: `scripts/phase3/db89_ghost_recovery.py:772-816`
- Modify: `scripts/phase3/test_db214_artifact_primitives.py`

- [ ] **Step 1: Write failing source-contract test**

```python
def test_renderer_db226_export_is_gated_raw_and_hashed():
    code = remote_py()
    assert "collect_pair_samples(" in code
    assert "_color_diag_samples.npz" in code
    assert '"gain_applied_to_npz": False' in code
    assert '"sample_sha256"' in code
    gated = code[code.index("if COLOR_DIAG:"):code.index("def sample_cam_patch")]
    assert "np.savez_compressed" in gated
```

- [ ] **Step 2: Run the single test and verify RED**

Run: `python -m pytest scripts/phase3/test_db214_artifact_primitives.py::test_renderer_db226_export_is_gated_raw_and_hashed -q`

Expected: failure because no DB-226 bundle is written.

- [ ] **Step 3: Wire raw samples without touching render pixels**

Import `collect_pair_samples`, `fixed_brightness_profile`, and `SCHEMA_VERSION`. In the existing `COLOR_DIAG` block, retain samples before the current saturation filter, calculate `depth_m` from the ERP depth field, calculate camera-baseline parallax from the two camera centres and the same 3D point, store arrays under deterministic prefixes `pair_000`, `pair_001`, and write:

```python
_sample_path = REMOTE_OUT / f"{run_name}_color_diag_samples.npz"
np.savez_compressed(_sample_path, **_sample_arrays)
_sample_sha256 = hashlib.sha256(_sample_path.read_bytes()).hexdigest()
color_diag_report.update({
    "schema_version": SCHEMA_VERSION,
    "dataset": "av2",
    "log_id": LOG_UUID,
    "anchor_index": int(anchor_idx),
    "camera_order": list(ring_cams),
    "luma_definition": "mean_rgb_linear_code_value",
    "gain_applied_to_npz": False,
    "sat_lo": float(SAT_LO),
    "sat_hi": float(SAT_HI),
    "sample_npz": _sample_path.name,
    "sample_sha256": _sample_sha256,
})
```

Each pair JSON entry must include `sample_prefix`, `boundary_n`, `geometry_valid_n`, `unpoisoned_n`, `unsaturated_n`, `emitted_n`, the existing scalar gains, and a `fixed_brightness_profile`. The NPZ stores raw float RGB, source coordinates, ERP index, depth, and parallax; it never stores gain-corrected RGB as training input.

- [ ] **Step 4: Run renderer-contract and helper tests**

Run: `python -m pytest scripts/phase3/test_db214_artifact_primitives.py scripts/phase3/test_db226_luma_response.py -q`

Expected: all tests pass and the legacy DB-215 assertions remain green.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- scripts/phase3/db89_ghost_recovery.py scripts/phase3/test_db214_artifact_primitives.py
git commit -m "feat(db226): export gated raw luminance observations"
```

### Task 3: No-leakage cross-log evaluator

**Files:**
- Modify: `scripts/phase3/db226_luma_response.py`
- Modify: `scripts/phase3/test_db226_luma_response.py`
- Create: `agent/db115_drivers/db226_analyze.py`

- [ ] **Step 1: Write failing split and transfer tests**

```python
def test_log_split_is_deterministic_and_disjoint():
    first = split_log_ids(["c", "a", "b", "d", "e", "f"], holdout_fraction=1/3)
    second = split_log_ids(["f", "e", "d", "c", "b", "a"], holdout_fraction=1/3)
    assert first == second
    assert set(first["train_log_ids"]).isdisjoint(first["heldout_log_ids"])


def test_evaluator_rejects_log_leakage():
    with pytest.raises(ValueError, match="disjoint"):
        evaluate_profile_transfer([], train_log_ids=["same"], heldout_log_ids=["same"])


def test_fixed_shape_transfers_but_frame_offsets_do_not_fake_it():
    stable = synthetic_profile_rows(shape=np.linspace(-.15, .15, 6), offsets=[-.4, .2, .7])
    verdict = evaluate_profile_transfer(
        stable, train_log_ids=["train0", "train1"], heldout_log_ids=["heldout"])
    assert verdict["majority_heldout_pairs_improved"] is True
    offsets_only = synthetic_profile_rows(shape=np.zeros(6), offsets=[-.4, .2, .7])
    neutral = evaluate_profile_transfer(
        offsets_only, train_log_ids=["train0", "train1"], heldout_log_ids=["heldout"])
    assert neutral["majority_heldout_pairs_improved"] is False
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest scripts/phase3/test_db226_luma_response.py -q`

Expected: missing `split_log_ids` and `evaluate_profile_transfer`.

- [ ] **Step 3: Implement deterministic split and zero-shape comparison**

`split_log_ids` sorts IDs, ranks them by `sha256("db226-v1:" + log_id)`, and assigns the first `round(N * holdout_fraction)` to held-out. `evaluate_profile_transfer` canonicalizes camera-pair orientation, subtracts each frame's weighted median residual, builds the training median shape per absolute bin, and evaluates the frozen shape on each held-out pair-frame. It returns baseline zero-shape MAE, frozen-shape MAE, signed correlation, supported-bin coverage, per-log wins, per-pair wins, and the registered majority verdict. It must reject overlapping log sets and must never estimate a held-out offset beyond subtracting the scalar baseline already allowed by production.

- [ ] **Step 4: Implement the CLI analyzer**

```python
def main() -> None:
    args = parse_args()
    manifest = json.loads(Path(args.split_manifest).read_text(encoding="utf-8"))
    rows = load_verified_sidecars(Path(args.input_root), manifest)
    report = evaluate_profile_transfer(
        rows,
        train_log_ids=manifest["train_log_ids"],
        heldout_log_ids=manifest["heldout_log_ids"],
    )
    report["split_manifest_sha256"] = sha256_file(Path(args.split_manifest))
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
```

The loader verifies every NPZ hash before reading it and reports correlation sensitivity for all samples, `rho>=0.30`, `rho>=0.45`, `rho>=0.60`, plus parallax caps of 2°, 5°, and unrestricted. The primary verdict is frozen before execution: `rho>=0.45`, `parallax<=5°`, at least three reliable bins, majority of held-out pairs and majority of held-out logs improved.

- [ ] **Step 5: Run tests and commit Task 3**

Run: `python -m pytest scripts/phase3/test_db226_luma_response.py -q`

Expected: all tests pass.

```powershell
git add -- scripts/phase3/db226_luma_response.py scripts/phase3/test_db226_luma_response.py agent/db115_drivers/db226_analyze.py
git commit -m "feat(db226): add heldout log response falsification"
```

### Task 4: Reproducible four-worker A100 driver

**Files:**
- Create: `agent/db115_drivers/db226_monotonic_luma_job.py`
- Modify: `scripts/phase3/test_db226_luma_response.py`

- [ ] **Step 1: Write failing driver-contract test**

```python
def test_remote_driver_freezes_split_before_launch_and_verifies_bundles():
    source = Path("agent/db115_drivers/db226_monotonic_luma_job.py").read_text()
    assert "split_manifest.json" in source
    assert source.index("split_manifest.json") < source.index("subprocess.Popen")
    assert "max_workers = 4" in source
    assert "sample_sha256" in source
    assert "db226_analyze.py" in source
```

- [ ] **Step 2: Run the driver-contract test and verify RED**

Run: `python -m pytest scripts/phase3/test_db226_luma_response.py::test_remote_driver_freezes_split_before_launch_and_verifies_bundles -q`

Expected: file-not-found failure.

- [ ] **Step 3: Implement the driver**

The driver discovers valid AV2 log directories from the configured Drive val root, excludes logs with fewer than three synchronized front-center frames, deterministically selects 24 IDs by hash before rendering, freezes a 16-train/8-heldout manifest, and chooses quarter/middle/three-quarter anchors without reading any output metric. It symlinks selected logs into `/content/localav2`, launches at most four `db125_worker.py` processes concurrently with `GROUND_MODE=off`, `ANNOTATION_POLICY=raw_sensor`, `COLOR_DIAG=True`, `EMC_RENDER=False`, and verifies for all 72 anchors: worker manifest, JSON sidecar, NPZ, SHA-256, schema, log ID, and zero case errors. It then runs `db226_analyze.py`, creates an archive, and copies the split, report, logs, and archive to `results/db226_monotonic_luma/`.

- [ ] **Step 4: Run local tests and compile checks**

Run: `python -m pytest scripts/phase3/test_db226_luma_response.py scripts/phase3/test_db214_artifact_primitives.py -q`

Run: `python -m py_compile scripts/phase3/db226_luma_response.py agent/db115_drivers/db226_analyze.py agent/db115_drivers/db226_monotonic_luma_job.py`

Expected: all tests and compilation pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- agent/db115_drivers/db226_monotonic_luma_job.py scripts/phase3/test_db226_luma_response.py
git commit -m "feat(db226): run frozen cross-log A100 diagnostic"
```

### Task 5: Local verification, remote execution, and decision

**Files:**
- Update after evidence: `agent/decision_briefs.md`
- Update after evidence: `agent/PIPELINE_1plus92.md`
- Update after evidence: `agent/HANDOFF-2026-07-30.md`
- Create locally after download: `deliverables/db226_monotonic_luma/EVIDENCE.md`

- [ ] **Step 1: Run the full relevant local suite**

Run: `python -m pytest scripts/phase3/test_db214_artifact_primitives.py scripts/phase3/test_db223_pandaset_adapter.py scripts/phase3/test_db226_luma_response.py -q`

Expected: all selected tests pass; record the exact count instead of claiming the repository-wide suite.

- [ ] **Step 2: Run lint/format and inspect diff**

Run: `python -m ruff check scripts/phase3/db226_luma_response.py scripts/phase3/db89_ghost_recovery.py scripts/phase3/test_db226_luma_response.py agent/db115_drivers/db226_analyze.py agent/db115_drivers/db226_monotonic_luma_job.py`

Run: `git diff --check`

Expected: both exit zero.

- [ ] **Step 3: Verify the authorized A100 and upload exact tracked inputs**

Call `/status`, require `NVIDIA A100-SXM4-80GB` and no incompatible active job. Upload the renderer, helper, analyzer, and driver by base64 `/write`; read back each file and verify SHA-256 before launch. Credentials must stay in process memory and never be written to the repository.

- [ ] **Step 4: Launch and monitor the four-worker job**

POST `/exec`, capture the returned job ID, poll `/jobs/<id>` until terminal, require exit code zero, then independently `/read` the split manifest and final report. A worker `rc=0` without all 72 verified bundles is failure.

- [ ] **Step 5: Apply the registered decision rule**

If transfer coverage is insufficient, record `UNKNOWN / data support`; if the fixed profile fails majority held-out pair/log improvement or sensitivity stability, record `NEG` and stop without a pixel candidate. Only if it passes all registered diagnostic gates, write a new candidate implementation plan; do not improvise the curve in this task.

- [ ] **Step 6: Download evidence, perform required visual audit, and document**

Download the report, split, representative full ERPs, territory maps, JSON sidecars, and NPZ hashes into `deliverables/db226_monotonic_luma/`. Inspect every selected held-out full ERP/territory image for the existing color territories and for any measurement mismatch around people, vehicles, and text. The diagnostic does not change pixels, so visual review validates the measurement population rather than claiming a visual fix.

- [ ] **Step 7: Commit the evidence-backed decision**

```powershell
git add -- agent/decision_briefs.md agent/PIPELINE_1plus92.md agent/HANDOFF-2026-07-30.md
git commit -m "docs(db226): record cross-log luminance verdict"
```

Do not stage the untracked evidence directories unless explicitly chosen after size/provenance review.

## Self-review result

- Spec coverage: diagnostic measurement, log-disjoint split, fixed bins, geometry sensitivity, no pixel changes, A100 execution, and kill rules all map to Tasks 1-5. The conditional pixel candidate is deliberately excluded and requires a new plan only after a pass.
- Placeholder scan: no TBD/TODO or unspecified error-handling steps remain.
- Type consistency: renderer NPZ fields match `collect_pair_samples`; JSON `sample_prefix` and SHA fields match the analyzer/driver contracts; train/heldout keys are consistent throughout.
