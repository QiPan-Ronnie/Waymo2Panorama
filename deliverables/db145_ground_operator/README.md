# DB-145 result — sensor-native ground inverse

Final status: **CONDITIONAL / NOT PRODUCTION**. The ungated B solver is killed; the raw-pixel footprint hypothesis survives only as an observability-gated follow-up.

## What was proved

- The exact frozen r3 run used 3 automatically selected AV2 logs × 2 automatically selected `2m×2m` patches, one shared configuration, and disjoint held-out camera/time groups.
- On both dry high-observability patches, B improved identical held-out raw-pixel robust MAE and median RGB L2 versus A. The dry-turn improvement was also visually real: the road-marking boundary aligned better.
- L4 was more than sufficient: all six r3 patches finished in 394.4 s wall time, with 387.5 MB peak CUDA memory.

## Why it still cannot be used

- Dry-straight low-observability B was **113.9% worse** than A although its latent texture looked sharper.
- Dry-turn low showed checker/chromatic edge artifacts.
- Wet-low produced severe diagonal checker and moiré patterns. Its scalar MAE improved, but the full-resolution eye check failed; this is the project's “eyes over metrics” rule in action.
- C rejects whole bad source views, but the failures are local and view-dependent, so C did not reliably remove them.
- The current “valid” mask means only “some EWA support exists.” It does not prove that the inverse problem is locally conditioned. Therefore those white pixels cannot be called strictly real.

## Protocol history

- `r1`: invalidated after visual inspection found AV2 hood/roof pixels being treated as ground. DB-123's analytic fleet-body mask was then added symmetrically to A/B/C and held-out.
- `r2`: rejected in P0 because two held-out blocks contained less than 10% of geometry-valid pixels.
- `r3`: valid. Each patch froze evidence-bearing held-out groups in the 10–35% range before optimization.

## Correct next direction

Do not sharpen B further. Build a truncated, observability-gated inverse:

1. Freeze an inner validation split using training groups only; never inspect the outer r3 held-out when deciding whether to enable B.
2. Compute local conditioning from footprint orientation, area, phase diversity, and source geometry.
3. Keep only modes that are supported by both the condition test and inner held-out improvement.
4. Use A where B is not proven; use honest black where even A lacks real support.
5. Treat wet/specular disagreement as an abstention signal, not as a request for a larger BRDF model.

Artifacts: `manifest_r3.json`, `verdict_r3.json`, `verdict_board_r3.png`, and the full compact r3 evidence tree under `r3/`.
