"""DB-238 Phase D - distribution, acceptance rates, ranking. Chooses no threshold.

Reads the screening ledger and writes RESULTS.md plus a machine-readable summary.
Acceptance rates are reported at several candidate thresholds so the user can
pick one with the population in view.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

LEDGER = ("/content/drive/MyDrive/koi_waymo2pano_colab/results/"
          "db238_scene_band_screening/screening_ledger.json")
OUT = os.path.dirname(LEDGER)
# for reference only - the user's marked defective scene on the SCREENER scale
A100_MARKED = 56.75
CANDIDATES = [15, 20, 25, 30, 40, 50]


def main(ledger_path=LEDGER):
    with open(ledger_path) as fh:
        led = json.load(fh)
    recs = led["records"]
    ok = {k: v for k, v in recs.items() if v.get("ok") and v.get("worst_residual") is not None}
    bad = {k: v for k, v in recs.items() if not v.get("ok")}
    print(f"records={len(recs)}  scored={len(ok)}  failed={len(bad)}")

    worst = np.array([v["worst_residual"] for v in ok.values()])
    med = np.array([v["median_residual"] for v in ok.values()])
    names = list(ok.keys())
    order = np.argsort(-worst)

    qs = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    pct = {q: float(np.percentile(worst, q)) for q in qs}

    # which camera pair is the worst offender, population-wide
    pair_counts = {}
    pair_vals = {}
    for v in ok.values():
        wp = v.get("worst_pair")
        if wp:
            pair_counts[wp] = pair_counts.get(wp, 0) + 1
        for pk, pv in (v.get("pairs") or {}).items():
            if pv.get("residual") is not None:
                pair_vals.setdefault(pk, []).append(pv["residual"])

    lines = []
    A = lines.append
    A("# DB-238 screening results\n")
    A(f"Scored **{len(ok)}** of {len(recs)} records; **{len(bad)}** failed.\n")
    A("> The threshold is deliberately NOT chosen here. These are the numbers "
      "needed to choose one.\n")
    A("> Magnitudes are on the SCREENER's scale, which runs systematically "
      "high versus production (Phase A: rank correlation 1.000, magnitude up to "
      "+41%). Do not transplant production thresholds.\n")

    A("\n## Worst-pair residual distribution\n")
    A("| percentile | value |")
    A("| ---: | ---: |")
    for q in qs:
        A(f"| p{q} | {pct[q]:.2f} |")
    A(f"\nmean {worst.mean():.2f}, sd {worst.std():.2f}, "
      f"min {worst.min():.2f}, max {worst.max():.2f}\n")
    A(f"\nThe user-marked defective scene (a100) scores **{A100_MARKED:.2f}** on "
      f"this scale, which sits at percentile "
      f"**{100.0 * (worst < A100_MARKED).mean():.1f}** of this population.\n")

    A("\n## Acceptance rate at candidate thresholds\n")
    A("| threshold | accepted | rejected | acceptance rate |")
    A("| ---: | ---: | ---: | ---: |")
    for t in CANDIDATES:
        acc = int((worst <= t).sum())
        A(f"| {t} | {acc} | {len(worst)-acc} | {100.0*acc/len(worst):.1f}% |")
    A("\nThe 2026-07-31 BOSCH guidance was to prefer fewer clean samples over "
      "contaminated ones, but to report the acceptance rate and avoid "
      "over-killing.\n")

    A("\n## Which camera pair fails most often\n")
    A("| pair | times it is the worst pair | median residual | p90 |")
    A("| --- | ---: | ---: | ---: |")
    for pk in sorted(pair_vals, key=lambda k: -np.median(pair_vals[k])):
        v = np.array(pair_vals[pk])
        A(f"| {pk.replace('ring_','')} | {pair_counts.get(pk,0)} | "
          f"{np.median(v):.2f} | {np.percentile(v,90):.2f} |")

    A("\n## 20 worst samples (candidates for rejection)\n")
    A("| rank | prefix | worst pair | worst | median |")
    A("| ---: | --- | --- | ---: | ---: |")
    for i, k in enumerate([names[i] for i in order[:20]], 1):
        v = ok[k]
        A(f"| {i} | {k} | {str(v.get('worst_pair','')).replace('ring_','')} | "
          f"{v['worst_residual']:.2f} | {v['median_residual']:.2f} |")

    A("\n## 20 best samples\n")
    A("| prefix | worst pair | worst | median |")
    A("| --- | --- | ---: | ---: |")
    for k in [names[i] for i in order[-20:]][::-1]:
        v = ok[k]
        A(f"| {k} | {str(v.get('worst_pair','')).replace('ring_','')} | "
          f"{v['worst_residual']:.2f} | {v['median_residual']:.2f} |")

    if bad:
        A("\n## Failures (counted in the denominator, not hidden)\n")
        A("| prefix | error |")
        A("| --- | --- |")
        for k, v in list(bad.items())[:40]:
            A(f"| {k} | {str(v.get('error',''))[:110]} |")

    md = "\n".join(lines) + "\n"
    with open(os.path.join(OUT, "RESULTS.md"), "w") as fh:
        fh.write(md)
    summary = {
        "scored": len(ok), "failed": len(bad),
        "percentiles": pct,
        "a100_marked_scene_value": A100_MARKED,
        "a100_percentile": float(100.0 * (worst < A100_MARKED).mean()),
        "acceptance": {str(t): {"accepted": int((worst <= t).sum()),
                                "rate": float((worst <= t).mean())} for t in CANDIDATES},
        "worst20": [{"prefix": names[i], "worst": float(worst[i]),
                     "pair": ok[names[i]].get("worst_pair")} for i in order[:20]],
        "best20": [{"prefix": names[i], "worst": float(worst[i]),
                    "pair": ok[names[i]].get("worst_pair")} for i in order[-20:][::-1]],
    }
    with open(os.path.join(OUT, "screening_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(md[:3000])
    print("wrote", os.path.join(OUT, "RESULTS.md"))
    return summary


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else LEDGER)
