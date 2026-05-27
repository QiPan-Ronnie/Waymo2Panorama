# Phase 0.5 Spike Report — TEMPLATE

> Copy this file to `notes/spike-report.md` (without the `-template` suffix) and fill in.
> Delete this top blockquote when done.

---

## Run metadata

| Field | Value |
|---|---|
| Date | YYYY-MM-DD |
| Operator | (your name / agent slot) |
| AV2 split | val / train / test |
| Log UUID | `<UUID>` |
| Log download method | `s5cmd` / `aws cli` / manual |
| Total disk usage of log | __ GB |
| Conda env | `waymo2pano-py310` |
| Python version | __ |
| av2 version | __ |
| Probe script exit code | 0 / 1 |
| Probe log location | `outputs/spike/probe_log.txt` |
| Mosaic location | `outputs/spike/mosaic.png` |

---

## API findings

### av2 import
- av2 module: __
- version: __
- import path that worked: __

### Dataloader class discovery
| Tried path | Result |
|---|---|
| `av2.datasets.sensor.sensor_dataloader.SensorDataloader` | OK / MISS / error |
| `av2.datasets.sensor.av2_sensor_dataloader.AV2SensorDataLoader` | OK / MISS / error |

**Class we'll use in Phase 1**: `<full.import.path>`

### Filesystem structure
- `sensors/cameras/<cam>/*.jpg` present: yes / no
- per-cam frame count: __ (front_center) / __ (others)
- Surprises: __

---

## Synchronization findings

| Cam | Frames | First ts (ns) | Last ts (ns) | Δ vs front_center @ anchor (ms) |
|---|---|---|---|---|
| ring_front_center | __ | __ | __ | 0 (anchor) |
| ring_front_left | __ | __ | __ | __ |
| ring_front_right | __ | __ | __ | __ |
| ring_side_left | __ | __ | __ | __ |
| ring_side_right | __ | __ | __ | __ |
| ring_rear_left | __ | __ | __ | __ |
| ring_rear_right | __ | __ | __ | __ |

**Max delta**: __ ms  
**Within 50 ms tolerance?** yes / no  
**Implication for L1 stitching**: __

---

## Calibration findings

### `intrinsics.feather`
- rows: __
- columns: __
- ring cam rows: __
- `ring_front_center` row sample:
  ```
  <paste the dict printed by probe script>
  ```

### `egovehicle_SE3_sensor.feather`
- rows: __
- columns: __
- ring cam rows: __
- `ring_front_center` row sample:
  ```
  <paste the dict printed by probe script>
  ```

### Sanity check
- K is 3×3 plausible? __ (focal length range, principal point ~ image center?)
- T_ego_cam is 4×4 SE(3) plausible? __ (translation in meters; rotation orthonormal?)
- Distortion coefficients present? __ (AV2 doc says imagery is undistorted — confirm)

---

## Mosaic eyeball check

Open `outputs/spike/mosaic.png`. Verify:
- [ ] All 7 ring cam tiles populated (no black tile except the labeled "empty")
- [ ] Front-left / front-center / front-right tiles show forward-facing scene
- [ ] Rear-left / rear-right tiles show rearward scene
- [ ] Side-left / side-right tiles show sideward scene
- [ ] All 7 tiles look like the same physical moment (same lighting, same vehicles visible across overlap regions)
- [ ] Hood / car body visible at bottom of some tiles (will be masked later)

**Notes**: __

---

## GO / NO-GO decision

| Check | Result |
|---|---|
| av2 importable | PASS / FAIL |
| Dataloader class found | PASS / FAIL |
| 7 ring cam dirs present | PASS / FAIL |
| Mosaic produced | PASS / FAIL |
| Sync within 50 ms | PASS / FAIL |
| Calibration readable | PASS / FAIL |
| Mosaic eyeball pass | PASS / FAIL |

**Decision**: `GO` / `NO-GO` for Phase 1

---

## Surprises vs plan

What did we find that the plan didn't predict? (Even small things — they're cheap insurance.)
- __

---

## Action items before / during Phase 1

- [ ] Pin the specific log UUID for Phase 1 use in `data/README.md`
- [ ] If sync > 50 ms: document the implication in `plan.md` §6 risk register
- [ ] If API class differs from plan §4 Phase 1: update `av2_loader.py` reference name
- [ ] If calibration columns differ from expected: adjust `av2_loader.py` extraction logic
- [ ] Other: __

---

## Outputs to commit alongside this report

- `outputs/spike/mosaic.png` — though gitignored by default, you may copy to `data/mini/spike_mosaic.png` if small enough to track
- `outputs/spike/probe_log.txt` — same treatment if useful
- This filled-in report
