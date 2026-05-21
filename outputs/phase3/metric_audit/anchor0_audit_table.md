# Anchor 0 metric audit (T5)

Source: P2.7 saved 3-panel PNGs (GT|L1|L3) downloaded from Drive.

Mask = intersection of L1/L3 any-channel-nonzero, computed identically to original eval philosophy.


## Per-cam (LPIPS lower = better; PSNR/MS-SSIM higher = better)

| cam | inter% | PSNR L1 | PSNR L3 | ΔPSNR | MS-SSIM L1 | MS-SSIM L3 | LPIPS L1 | LPIPS L3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ring_front_center | 24.0 | 8.24 | 7.91 | -0.33 | 0.292 | 0.082 | 0.220 | 0.291 |
| ring_front_left | 14.5 | 18.88 | 12.81 | -6.08 | 0.706 | 0.102 | 0.020 | 0.070 |
| ring_side_left | 11.8 | 19.14 | 10.48 | -8.66 | 0.643 | 0.093 | 0.018 | 0.057 |
| ring_rear_left | 6.2 | 13.28 | 7.74 | -5.54 | 0.586 | 0.011 | 0.017 | 0.044 |
| ring_rear_right | 8.3 | 11.49 | 8.58 | -2.91 | 0.637 | -0.003 | 0.026 | 0.060 |
| ring_side_right | 11.6 | 18.26 | 10.83 | -7.43 | 0.740 | 0.179 | 0.016 | 0.047 |
| ring_front_right | 11.3 | 10.98 | 11.57 | +0.59 | 0.626 | 0.150 | 0.035 | 0.070 |
| **MEAN** | — | **14.33** | **9.99** | **-4.34** | **0.604** | **0.088** | **0.050** | **0.091** |

## Region-separated PSNR (intersection mask, rows-thirds)

| cam | sky L1 | sky L3 | Δ sky | obj L1 | obj L3 | Δ obj | ground L1 | ground L3 | Δ ground |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ring_front_center | 7.73 | 6.85 | -0.88 | 8.66 | 8.18 | -0.48 | 7.59 | 8.18 | +0.59 |
| ring_front_left | 18.39 | 10.77 | -7.63 | 18.43 | 12.50 | -5.93 | 21.36 | 17.97 | -3.39 |
| ring_side_left | 23.50 | 10.74 | -12.77 | 18.34 | 8.71 | -9.63 | 19.96 | 17.27 | -2.69 |
| ring_rear_left | nan | nan | +nan | 17.96 | 5.02 | -12.94 | 12.19 | 9.82 | -2.37 |
| ring_rear_right | nan | nan | +nan | 19.62 | 10.40 | -9.22 | 9.29 | 7.56 | -1.74 |
| ring_side_right | 21.43 | 22.57 | +1.14 | 19.19 | 11.72 | -7.47 | 15.73 | 7.71 | -8.03 |
| ring_front_right | 6.61 | 7.82 | +1.21 | 18.62 | 16.09 | -2.53 | 22.81 | 17.87 | -4.94 |
| **MEAN** | **15.53** | **11.75** | **-3.78** | **17.26** | **10.37** | **-6.88** | **15.56** | **12.34** | **-3.22** |
