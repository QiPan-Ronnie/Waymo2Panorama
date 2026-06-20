"""DB-102 STEP 0 analysis: read the bevaudit npy files and print the RADIAL profile
(median nvalid / frac nvalid>=4 / median grazing / median lum_std per ring-radius
bucket) per scene + a GO/NO-GO verdict for BEV reconstruction of the 3-7 m annulus.

  python _db102_analyze.py
"""
import pathlib, glob
import numpy as np

D = pathlib.Path(r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/agent/_db102")
BUCKETS = [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8)]


def analyse(npy):
    a = np.load(npy)  # cols: x,y,rr,ncount,graze_max,az_spread,lum_std
    rr, nc, gz, azs, ls = a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6]
    print(f"\n=== {pathlib.Path(npy).name}  ({len(a)} cells) ===")
    print(f"{'ring(m)':>8} {'cells':>6} {'medN':>5} {'N>=4%':>6} {'medGraze':>8} {'medLumStd':>9} {'medAzSpr':>8}")
    annulus_ok = []
    for lo, hi in BUCKETS:
        m = (rr >= lo) & (rr < hi)
        if not m.any():
            continue
        medN = np.median(nc[m]); fr4 = 100 * np.mean(nc[m] >= 4)
        # grazing/lumstd only meaningful where there IS coverage
        mc = m & (nc >= 1)
        medG = np.median(gz[mc]) if mc.any() else -1
        medL = np.median(ls[mc]) if mc.any() else -1
        medA = np.median(azs[mc]) if mc.any() else -1
        print(f"{lo}-{hi:<6} {int(m.sum()):>6} {medN:>5.1f} {fr4:>5.0f}% {medG:>7.1f}d {medL:>9.1f} {medA:>8.2f}")
        if 3 <= lo < 7:
            annulus_ok.append((medN >= 4) and (medG >= 8) and (medL <= 18))
    verdict = "GO" if (annulus_ok and all(annulus_ok)) else "WEAK/NO-GO"
    print(f"  -> annulus(3-7m) per-bucket recoverable={annulus_ok}  => {verdict}")
    return verdict


if __name__ == "__main__":
    files = sorted(glob.glob(str(D / "*_bevaudit.npy")))
    if not files:
        print("no bevaudit npy yet in", D)
    else:
        verdicts = {pathlib.Path(f).name: analyse(f) for f in files}
        print("\n==== SUMMARY ====")
        for k, v in verdicts.items():
            print(f"  {k}: {v}")
        print("GATE: GO only if the 3-7 m annulus has medN>=4 & medGraze>=8deg & medLumStd<=18 in every bucket.")
        print("  (lum_std high => sources disagree => occlusion-leak/misregistration => BEV would double-image, not recover.)")
