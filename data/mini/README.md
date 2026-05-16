# data/mini/

Small artifacts that ARE tracked in git (unlike `data/argoverse2/` which is gitignored).

## Subdirectories

- `ego_masks/` — hand-painted PNG masks per ring camera (Phase 1, painted once we see a render)
- `spike_mosaic.png` (optional) — copy of `outputs/spike/mosaic.png` if small enough to commit as evidence

## Why these are tracked
- Ego masks are small, hand-crafted, reusable across all logs from the same vehicle, and slow to recreate
- The spike mosaic serves as the "this was the API/data state when we started" historical artifact
