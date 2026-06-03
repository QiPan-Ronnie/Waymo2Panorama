"""DB-18 D5 — object-safety GATE for generative outputs (the judge for every DiT360 result).

Flags NET-NEW salient objects (person/bicycle/car/motorcycle/bus/truck/traffic-light/stop-sign) that
appear in the GENERATED region of the output but were NOT in the source. A hallucinated car/person =
the disqualifier for faithful AV data. It does NOT prove faithfulness — only catches obvious new
instances at the detector's recall.

Uses torchvision Faster R-CNN (COCO) — already in the env, NO pip install / env churn. Runs on CPU by
default (safe alongside a FLUX GPU job; ~20s for 2 panos).

Mask convention (matches the DiT runner): preserve PNG white(255)=preserve source, black(0)=generated.
Generated region = preserve < 128.

usage: python _object_gate.py <source.png> <gen.png> <preserve_mask.png> <out_prefix> [conf=0.50] [device=cpu]
emits <out_prefix>_gate.json + <out_prefix>_gate.jpg (source dets green, NET-NEW red).
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import cv2
import numpy as np
import torch
from torchvision.models.detection import fasterrcnn_resnet50_fpn, FasterRCNN_ResNet50_FPN_Weights

# torchvision COCO category ids (91-class, with gaps)
SALIENT = {1: "person", 2: "bicycle", 3: "car", 4: "motorcycle", 6: "bus", 8: "truck", 10: "traffic_light", 13: "stop_sign"}


def _iou(a, b):
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0); inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def main():
    src_p, gen_p, mask_p, out_prefix = sys.argv[1:5]
    conf = float(sys.argv[5]) if len(sys.argv) > 5 else 0.50
    device = sys.argv[6] if len(sys.argv) > 6 else "cpu"
    model = fasterrcnn_resnet50_fpn(weights=FasterRCNN_ResNet50_FPN_Weights.DEFAULT).eval().to(device)

    src = cv2.imread(src_p); gen = cv2.imread(gen_p)
    assert src is not None, f"cannot read source: {src_p}"
    assert gen is not None, f"cannot read gen: {gen_p}"
    H, W = gen.shape[:2]
    preserve = cv2.imread(mask_p, 0)
    assert preserve is not None, f"cannot read mask: {mask_p} -- a safety gate must NOT silently pass on a bad mask"
    if preserve.shape[:2] != (H, W):
        preserve = cv2.resize(preserve, (W, H), interpolation=cv2.INTER_NEAREST)
    generated = preserve < 128

    def dets(img_bgr):
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        t = torch.from_numpy(rgb).permute(2, 0, 1).float().div(255.0).to(device)
        with torch.no_grad():
            o = model([t])[0]
        out = []
        for box, lbl, sc in zip(o["boxes"].cpu().numpy(), o["labels"].cpu().numpy(), o["scores"].cpu().numpy()):
            if float(sc) >= conf and int(lbl) in SALIENT:
                out.append((int(lbl), [float(v) for v in box], float(sc)))
        return out

    sd, gd = dets(src), dets(gen)
    netnew = []
    for c, box, cf in gd:
        # BOX-AREA overlap with the generated region (NOT center-only: a hallucination straddling the
        # seam whose centroid lands on a preserved pixel must still be caught).
        x0, y0, x1, y1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(W, x1), min(H, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        gen_overlap = float(generated[y0:y1, x0:x1].mean())
        if gen_overlap < 0.30:                       # object mostly OUTSIDE the generated region -> not a fill-invention
            continue
        if any(c == c2 and _iou(box, b2) > 0.3 for c2, b2, _ in sd):
            continue                                  # matches a pre-existing source object of the same class -> not new
        netnew.append({"cls": SALIENT[c], "box": [round(v, 1) for v in box], "conf": round(cf, 3), "gen_overlap": round(gen_overlap, 2)})

    res = {"source": src_p, "gen": gen_p, "mask": mask_p, "conf": conf,
           "src_salient": len(sd), "gen_salient": len(gd), "netnew_count": len(netnew),
           "PASS": len(netnew) == 0, "netnew": netnew}
    Path(out_prefix).parent.mkdir(parents=True, exist_ok=True)
    with open(f"{out_prefix}_gate.json", "w") as f:
        json.dump(res, f, indent=2)
    viz = gen.copy()
    for c, box, cf in sd:
        cv2.rectangle(viz, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), (0, 200, 0), 1)
    for nn in netnew:
        b = nn["box"]; cv2.rectangle(viz, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 0, 255), 3)
        cv2.putText(viz, f"NEW {nn['cls']} {nn['conf']}", (int(b[0]), max(0, int(b[1]) - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.imwrite(f"{out_prefix}_gate.jpg", viz, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(json.dumps(res, indent=2), flush=True)
    print(f"[gate] {'PASS' if res['PASS'] else 'FAIL'} netnew={res['netnew_count']} src={len(sd)} gen={len(gd)} -> {out_prefix}_gate.{{json,jpg}}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
