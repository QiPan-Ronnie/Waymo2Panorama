"""DB-18 D5 — object-safety GATE for generative outputs (the judge for every DiT360 result).

Flags NET-NEW salient objects (car/person/bus/truck/motorcycle/bicycle/traffic-light/stop-sign) that
appear in the GENERATED region of the output but were NOT in the source. This catches the failure mode
that disqualifies generative AV data (a hallucinated car/person teaches wrong statistics).

It does NOT prove faithfulness — it only catches obvious new salient instances at the detector's recall.

Convention: `preserve_mask` PNG = white(255) preserves source, black(0) = generated (matches the DiT
runner). Generated region = preserve < 128.

usage: python _object_gate.py <source.png> <gen.png> <preserve_mask.png> <out_prefix> [conf=0.30]
emits <out_prefix>_gate.json + <out_prefix>_gate.jpg (source dets green, gen dets cyan, NET-NEW red).
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import cv2
import numpy as np

SALIENT = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 9: "traffic_light", 11: "stop_sign"}


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a; bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0); ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0); inter = iw * ih
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def main():
    src_p, gen_p, mask_p, out_prefix = sys.argv[1:5]
    conf = float(sys.argv[5]) if len(sys.argv) > 5 else 0.30
    from ultralytics import YOLO
    model = YOLO("yolov8x.pt")
    src = cv2.imread(src_p); gen = cv2.imread(gen_p)
    H, W = gen.shape[:2]
    preserve = cv2.imread(mask_p, 0)
    preserve = cv2.resize(preserve, (W, H), interpolation=cv2.INTER_NEAREST) if preserve.shape[:2] != (H, W) else preserve
    generated = preserve < 128   # the region DiT was allowed to change

    def dets(img):
        r = model(img, conf=conf, verbose=False)[0]
        out = []
        for c, box, cf in zip(r.boxes.cls.cpu().numpy(), r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()):
            c = int(c)
            if c in SALIENT:
                out.append((c, [float(v) for v in box], float(cf)))
        return out

    sd, gd = dets(src), dets(gen)
    netnew = []
    for c, box, cf in gd:
        cx, cy = int(0.5 * (box[0] + box[2])), int(0.5 * (box[1] + box[3]))
        if not (0 <= cy < H and 0 <= cx < W and generated[cy, cx]):
            continue  # only flag objects whose center is in the GENERATED region
        if any(c == c2 and _iou(box, b2) > 0.3 for c2, b2, _ in sd):
            continue  # matches a pre-existing source object -> not new
        netnew.append({"cls": SALIENT[c], "box": [round(v, 1) for v in box], "conf": round(cf, 3)})

    res = {"source": src_p, "gen": gen_p, "mask": mask_p, "conf": conf,
           "src_salient": len(sd), "gen_salient": len(gd), "netnew_count": len(netnew),
           "PASS": len(netnew) == 0, "netnew": netnew}
    Path(out_prefix).parent.mkdir(parents=True, exist_ok=True)
    with open(f"{out_prefix}_gate.json", "w") as f:
        json.dump(res, f, indent=2)
    # overlay
    viz = gen.copy()
    for c, box, cf in sd:
        cv2.rectangle(viz, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), (0, 200, 0), 1)
    for nn in netnew:
        b = nn["box"]; cv2.rectangle(viz, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 0, 255), 3)
        cv2.putText(viz, f"NEW {nn['cls']} {nn['conf']}", (int(b[0]), max(0, int(b[1]) - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.imwrite(f"{out_prefix}_gate.jpg", viz, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(json.dumps(res, indent=2), flush=True)
    print(f"[gate] {'PASS' if res['PASS'] else 'FAIL'} netnew={res['netnew_count']} -> {out_prefix}_gate.{{json,jpg}}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
