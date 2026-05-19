# Phase 0.5 Spike Report

## Run metadata

| Field | Value |
|---|---|
| Date | 2026-05-16 |
| Operator | Claude Code + user (via Colab MCP) |
| Runtime | Colab CPU runtime (no GPU — confirmed not needed for spike) |
| AV2 split | val |
| Log UUID | `02a00399-3857-444e-8db3-a8f58489c394` |
| Log download | s5cmd v2.2.2 → Drive `koi_waymo2pano_colab/data/argoverse2/val/<UUID>/` |
| Disk usage | ~5-10 GB (9 cameras × 319 frames) |
| Python | 3.12 (Colab default) |
| av2 version | `<unknown>` — `av2.__version__` attribute not present (module loads fine) |
| torch | 2.10.0+cpu |
| Probe exit code | **0 → GO** |
| Probe log | Colab: `/content/Waymo2Panorama/outputs/spike/probe_log.txt`; Drive synced |
| Mosaic | Colab: `/content/Waymo2Panorama/outputs/spike/mosaic.png` (512×1280); Drive synced |

## API findings

### av2 import
- `import av2` succeeds
- `__version__` attribute NOT exposed (script printed `?`); module is functional

### Dataloader class discovery
Both candidate import paths resolve successfully:

| Tried path | Result |
|---|---|
| `av2.datasets.sensor.sensor_dataloader.SensorDataloader` | ✅ OK |
| `av2.datasets.sensor.av2_sensor_dataloader.AV2SensorDataLoader` | ✅ OK |

**Decision for Phase 1**: Use `av2.datasets.sensor.sensor_dataloader.SensorDataloader` (the canonical/modern path per av2 source).

### Filesystem structure
Standard AV2 layout confirmed:
```
<UUID>/sensors/cameras/{ring_front_center, ring_front_left, ring_front_right,
                       ring_side_left, ring_side_right,
                       ring_rear_left, ring_rear_right,
                       stereo_front_left, stereo_front_right}/<timestamp_ns>.jpg
<UUID>/calibration/{intrinsics.feather, egovehicle_SE3_sensor.feather}
```

All 9 cameras present (7 ring + 2 stereo). Phase 1 will only consume the 7 ring cams.

## Synchronization findings

| Camera | Frames | first_ts (ns) | last_ts (ns) | Δ vs front_center @ anchor (ms) |
|---|---|---|---|---|
| ring_front_center | 319 | 315966070549927210 | 315966086449927219 | 0 (anchor) |
| ring_front_left | 319 | 315966070537425446 | 315966086437425439 | computed below |
| ring_front_right | 319 | 315966070562451239 | 315966086462451248 | computed below |
| ring_side_left | 319 | 315966070522412936 | 315966086422412941 | — |
| ring_side_right | 319 | 315966070527482489 | 315966086427482493 | — |
| ring_rear_left | 319 | 315966070557428279 | 315966086457428279 | — |
| ring_rear_right | 319 | 315966070542441195 | 315966086442441195 | — |

- **front_center span**: 15.90 s, 319 frames → 20 Hz ✓
- **Max timestamp delta across 7 ring cams @ anchor**: **22.49 ms** ✓ (well within 50 ms tolerance)

→ Synchronization is solid for L1 stitching. No need to adjust the plan's 50 ms threshold.

## Image resolution findings

⚠️ **Important asymmetry for Phase 1 sphere projection code**:

| Camera | Shape (H, W, 3) | Orientation |
|---|---|---|
| **ring_front_center** | **(2048, 1550, 3)** | **portrait** |
| All other 6 ring cams | (1550, 2048, 3) | landscape |

This matches AV2 documentation: front_center is mounted in portrait orientation to capture more vertical FOV (for traffic lights, tall vehicles). **Phase 1 `sphere_projection.render_camera_to_erp` must handle both orientations** — likely via per-camera intrinsics rather than a hard-coded image size.

## Calibration findings

### `intrinsics.feather`
- 9 rows (one per camera)
- columns: `[sensor_name, fx_px, fy_px, cx_px, cy_px, k1, k2, k3, height_px, width_px]`
- 7 ring cams present (plus 2 stereo)

Sample row — `ring_front_center`:
```
fx_px = 1774.3208878877233
fy_px = 1774.3208878877233
cx_px =  771.4245885312...
cy_px = 1020.7887337831...
k1    = -0.239949465442...
k2    = (truncated in log)
```

Notes:
- `fx ≈ fy` → near-square pixels ✓
- `cx ≈ width/2, cy ≈ height/2` → principal point near image center ✓ (cx_px=771 vs width=1550/2=775 ✓; cy_px=1020 vs height=2048/2=1024 ✓)
- **`k1` is non-zero despite AV2 doc claiming "undistorted"** — likely residual distortion coefficients are retained for completeness; Phase 1 can treat as pinhole (ignore `k`s) but document as `[D1-extra] AV2 distortion coefficients present`

### `egovehicle_SE3_sensor.feather`
- 11 rows (covers all 9 cameras + 2 LiDAR sensors)
- columns: `[sensor_name, qw, qx, qy, qz, tx_m, ty_m, tz_m]`
- 7 ring cams present

Sample row — `ring_front_center`:
```
qw    =  0.5029439660021882
qx    = -0.49846720965726476
qy    =  0.5016773203547583
qz    = -0.49688800640284303
tx_m  =  1.6291039690504983
ty_m  = -0.01... (truncated)
tz_m  = (not shown)
```

Notes:
- Quaternion magnitude ≈ 1.0 ✓ (rotation valid)
- This quaternion is approximately (0.5, -0.5, 0.5, -0.5) → ~90° rotation, sensor-to-egovehicle frame is rotated ~90° (consistent with portrait mounting + camera coordinate convention)
- `tx_m ≈ 1.63 m` → camera mounted ~1.63 m forward of vehicle origin ✓
- Phase 1 will use `Rotation.from_quat([qx, qy, qz, qw])` (scipy convention) and translation `[tx_m, ty_m, tz_m]` to build `T_ego_cam` 4×4.

## Mosaic eyeball check

✅ All checks pass:
- 7 ring tiles fully populated (with intentional `(empty)` 8th slot)
- **front_left / front_center / front_right** show coherent forward urban street view (sunny daytime, Miami-area)
- **side_left / side_right** show streetscape and parked vehicles
- **rear_left / rear_right** show buildings and road behind
- Same physical moment confirmed (lighting consistent, vehicles visible across overlap regions)
- **Ego vehicle hood/body visible at bottom edges of multiple tiles** → confirms need for ego mask in Phase 1 (already in plan §6 risk register)

## GO / NO-GO decision

| Check | Result |
|---|---|
| av2 importable | ✅ PASS |
| Dataloader class found | ✅ PASS (2 paths work) |
| 7 ring cam dirs present | ✅ PASS |
| Mosaic produced | ✅ PASS |
| Sync within 50 ms | ✅ PASS (22.49 ms actual) |
| Calibration readable | ✅ PASS |
| Mosaic eyeball | ✅ PASS |

**Decision: `GO` — Phase 1 can start.**

## Surprises vs plan (worth capturing for Phase 1)

1. **`ring_front_center` is portrait** (2048×1550) while the other 6 ring cams are landscape (1550×2048). Plan v2 §4 Phase 1 module signatures (e.g., `render_camera_to_erp`) must accept per-camera image shapes; cannot hard-code.
2. **AV2 "undistorted" still retains k1/k2/k3** coefficients in `intrinsics.feather`. Phase 1 will treat as pinhole (ignore k's) but flag this in `av2_loader.py` so we can revisit if seams look distorted.
3. **Both dataloader class paths work** — no need to pin one defensively in code; use `SensorDataloader` as canonical.
4. **`av2.__version__` is not exposed** — minor, but our probe printed `?` for version. Use `pip show av2` if version is needed for reproducibility log.
5. **Drive write throughput was fine** — Cell 5 (full 5-10 GB download direct to Drive) completed quickly; we don't need to stage to `/content` first.
6. **20 Hz frame rate confirmed** — 319 frames / 15.90 s = exactly 20 Hz, matching AV2 spec.

## Action items for Phase 1

- [ ] `av2_loader.py` must handle per-camera image orientation (don't hard-code shape)
- [ ] `av2_loader.py` reads intrinsics with `pd.read_feather`, builds 3×3 K per camera (ignore k1/k2/k3 at L1 unless seams demand)
- [ ] `av2_loader.py` reads `egovehicle_SE3_sensor.feather`, builds 4×4 T_ego_cam per camera
- [ ] `av2_loader.py` filters to 7 ring cams only (drop stereo for L1)
- [ ] Hand-paint ego masks for 7 cameras (one PNG each in `data/mini/ego_masks/`) once first L1 render shows where hood appears
- [ ] Pin the spike log UUID `02a00399-3857-444e-8db3-a8f58489c394` in `data/README.md` (already pinned)
- [ ] Update plan.md §10 open question 5 to confirm: AV2 IS global-shutter; no motion-blur concern (per AV2 spec)

## Artifacts

- Probe log (Drive): `/MyDrive/koi_waymo2pano_colab/outputs/spike/probe_log.txt`
- Mosaic (Drive): `/MyDrive/koi_waymo2pano_colab/outputs/spike/mosaic.png` (512×1280)
- This report (repo): `notes/spike-report.md`
- User-provided screenshot showing the mosaic + log: archived in conversation history
