# Doubled-Pair Metric: Honest Negative Finding

Ran score_panorama_doubled.py at conf=0.3 and conf=0.1 across 160 panoramas:

| metric              | multiband | hard_hdr_of |
|---------------------|-----------|-------------|
| conf=0.3 mean det   | 1.06      | 1.52        |
| conf=0.3 mean dbl   | 0.05      | 0.06        |
| conf=0.1 mean det   | 3.84      | 4.89        |
| conf=0.1 mean dbl   | 1.03      | 1.23        |
| dbl/det ratio (0.1) | 0.268     | 0.252       |

The doubled-pair count SCALES with total detection count — more detections = more pair candidates that happen to fall in IoU range (0.1, 0.7). Without normalization the metric measures detection-count, not doubling.

After normalization (dbl/det), hard_hdr_of is MARGINALLY better (0.252 vs 0.268), but the effect size is tiny (~6% relative) and likely within noise.

**Conclusion**: panorama-YOLO bbox metrics do not reliably capture doubled-feature ghosts. The actual ghost (a faded second copy of an object) is often seen by YOLO as ONE object with reduced confidence, not TWO separate boxes.

**Alternative metrics to try**:
1. Pixel-level: cross-correlation of overlap-zone slabs before/after pipeline
2. Direct: count high-freq overlap-zone energy as ghost proxy
3. Human eval: A/B preference test on Mechanical Turk
4. Feature consistency: SIFT/ORB feature count in overlap (more features = sharper = better)
