# Data Layout

This directory stages downloaded autonomous-driving sensor data. Large files
are gitignored (see `../.gitignore`); only this README and `mini/` placeholders
are tracked.

## Structure

```
data/
├── README.md                       (this file)
├── argoverse2/                     (gitignored — download via scripts/download_av2_sample.py)
│   ├── val/<log_uuid>/             (one log = ~5-10 GB)
│   │   ├── sensors/
│   │   │   ├── cameras/
│   │   │   │   ├── ring_front_center/<timestamp_ns>.jpg
│   │   │   │   ├── ring_front_left/...
│   │   │   │   ├── ring_front_right/...
│   │   │   │   ├── ring_side_left/...
│   │   │   │   ├── ring_side_right/...
│   │   │   │   ├── ring_rear_left/...
│   │   │   │   └── ring_rear_right/...
│   │   │   └── lidar/<timestamp_ns>.feather
│   │   ├── calibration/
│   │   │   ├── intrinsics.feather
│   │   │   └── egovehicle_SE3_sensor.feather
│   │   └── city_SE3_egovehicle.feather
│   ├── train/                      (later phases)
│   └── test/                       (later phases)
├── waymo/                          (Track B — gitignored)
├── nuscenes/                       (Phase 3 — gitignored)
└── mini/
    ├── ego_masks/                  (hand-painted PNG masks per camera; small, tracked)
    └── README.md                   (mini-sample explanations, tracked)
```

## Pinned spike log

| Field | Value |
|---|---|
| Split | `val` |
| Log UUID | `02a00399-3857-444e-8db3-a8f58489c394` (default — confirm/replace during spike) |
| Approx. size | 5-10 GB |
| Why this one | Default AV2 val-split log; suburban daytime |

To override: pass `--log-id <UUID>` to `download_av2_sample.py`.

## How to download

```powershell
# from repo root, with waymo2pano-py310 conda env active
python scripts/download_av2_sample.py                       # uses pinned default
python scripts/download_av2_sample.py --log-id <UUID>       # specific log
python scripts/download_av2_sample.py --dry-run             # print s5cmd command only
```

If you don't have `s5cmd`:
```powershell
conda install -c conda-forge s5cmd
```

## Why we pinned this log
The spike's job is to validate AV2 API assumptions, not to find a perfect log.
Any val-split log will surface API / sync / calibration issues equally well.
We will pick the "good demo log" later in Phase 1 based on Phase 0.5 findings.

## License reminder
AV2 is CC-BY-NC. Any derivative we publish inherits NC. Do **not** push raw
AV2 imagery to this GitHub repo (already gitignored).
