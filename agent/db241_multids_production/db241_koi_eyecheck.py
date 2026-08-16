"""Build the per-source eye-check submission koi asked for on 08-14.

"每个 data set 都发个样本到群主给我瞄一眼" - one sample per source, before mass
production. This assembles exactly that: one representative sample from each of
the four sources, its clip, a side-by-side still, and the numbers koi has not
seen yet - in particular that a Waymo Perception sample is only 27% real pixels.

Picks a daytime sample where possible: the mask is the thing under review, and a
night scene makes it hard to see.
"""
import json
import os
import shutil
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = r"E:/w2p_data/dataset_out"
PKG = r"E:/w2p_data/koi_eyecheck_2026-08-16"
BAND = slice(330, 700)


def brightness(d):
    p = os.path.join(d, "frames", "fr_0037.png")
    if not os.path.isfile(p):
        return -1
    a = np.asarray(Image.open(p).convert("L"))
    v = a[a > 0]
    return float(v.mean()) if v.size else -1


def pick(ds):
    """Brightest accepted sample - the mask is what is under review."""
    root = os.path.join(OUT, ds)
    best, bs = None, -1
    for s in sorted(os.listdir(root)):
        d = os.path.join(root, s)
        mp = os.path.join(d, "manifest.json")
        if not os.path.isfile(mp):
            continue
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        if not m.get("accepted"):
            continue
        b = brightness(d)
        if b > bs:
            best, bs = (d, m), b
    return best


def main():
    os.makedirs(PKG, exist_ok=True)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 26)
    except Exception:
        font = ImageFont.load_default()
    picks, rows = {}, []
    for ds in sorted(os.listdir(OUT)):
        if not os.path.isdir(os.path.join(OUT, ds)):
            continue
        got = pick(ds)
        if not got:
            continue
        d, m = got
        picks[ds] = (d, m)
        sub = os.path.join(PKG, ds)
        os.makedirs(sub, exist_ok=True)
        for f in ("clip.mp4", "rule_mask.png", "manifest.json"):
            p = os.path.join(d, f)
            if os.path.isfile(p):
                shutil.copyfile(p, os.path.join(sub, f))
        # one frame + its mask, so the pairing is inspectable without the clip
        for k in (0, 37, 92):
            for kind, pre in (("frames", "fr"), ("masks", "mk")):
                p = os.path.join(d, kind, "%s_%04d.png" % (pre, k))
                if os.path.isfile(p):
                    shutil.copyfile(p, os.path.join(sub, "%s_%04d.png" % (pre, k)))
        fr = np.asarray(Image.open(os.path.join(d, "frames", "fr_0037.png")).convert("RGB"))
        cov = 100 * (fr[BAND].sum(2) > 0).mean()
        rows.append((ds, m, fr, cov))

    canvas = Image.new("RGB", (2048, len(rows) * 412), (14, 16, 20))
    dr = ImageDraw.Draw(canvas)
    for i, (ds, m, fr, cov) in enumerate(rows):
        rm = np.asarray(Image.open(os.path.join(PKG, ds, "rule_mask.png")).convert("L")) > 127
        red = fr.copy()
        red[rm] = [255, 60, 60]
        y = i * 412
        dr.text((14, y + 6),
                "%s   %d cams   ring %s   seam mask %.1f%% of band   real pixels %.0f%%"
                % (ds, m["n_cameras"], "closed" if m["ring_closed"] else "OPEN (rear gap)",
                   100 * m["rule_mask_frac_of_band"], cov),
                font=font, fill=(235, 238, 242))
        canvas.paste(Image.fromarray(red[BAND]), (0, y + 42))
    canvas.save(os.path.join(PKG, "EYECHECK_four_sources.png"))

    with open(os.path.join(PKG, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(readme(rows))
    print("built %s with %d sources" % (PKG, len(rows)))
    for ds, m, _, cov in rows:
        print("  %-18s mask %.1f%%  real %.0f%%  ring %s"
              % (ds, 100 * m["rule_mask_frac_of_band"], cov,
                 "closed" if m["ring_closed"] else "OPEN"))


def readme(rows):
    L = ["# 每个数据源一个样本 - 请 koi 过目\n",
         "按 08-14 会议「每个 data set 都发个样本到群里给我瞄一眼」，量产前先送审。\n",
         "红色 = 接缝 mask（会被涂黑，不算 loss）。黑色 = 相机物理上看不到的地方。\n",
         "## 四个源\n",
         "| 源 | 相机 | 环是否闭合 | 接缝带 | 接缝 mask 占带 | 带内真实像素 |",
         "|---|---|---|---|---|---|"]
    for ds, m, _, cov in rows:
        w = sorted(v.get("strip_w", 0) for v in m["pairs"].values())
        L.append("| %s | %d | %s | %d–%d px | %.1f%% | **%.0f%%** |"
                 % (ds, m["n_cameras"],
                    "闭合" if m["ring_closed"] else "**开口（车后无相机）**",
                    min(w), max(w), 100 * m["rule_mask_frac_of_band"], cov))
    L += ["",
          "## 两个需要你拍板的点\n",
          "**1. Waymo Perception 带内只有约 27% 是真实像素。**",
          "你 7/31 说「就算 open waymo 只有 252 度也没关系，后面两个就算是生的」——",
          "这个判断依然成立，但实测密度比 252° 这个数字给人的印象低得多：",
          "五个相机的方位覆盖实测 202°（去畸变裁边后），空洞 158°；",
          "而且最窄的那路相机垂直只有 ±12.1°，所以带的上下也大片是黑的。",
          "两者叠加后，一个样本约 73% 是黑的。想先让你看到这个数字再决定要不要用。\n",
          "**2. 车头两版目前没做。**",
          "你 8/14 要求车头黑 / 车头保留两版按场景各半。我们没有任何一个 rig 的",
          "验证过的车身 mask：v15 那张是 1+92 时代的几何，对不上现在的渲染；",
          "用「车头是唯一相对车静止的物体」去实测，AV2 的车身在画面最左右（车后），",
          "带内只占约 0.02%。凭猜画一张 mask 就是在真实像素上涂黑，所以没有做。",
          "请定：(A) 每个 rig 做验证过的车身 mask，(B) 确认不做，(C) 只对 AV2 做。\n",
          "## 每个文件夹里有什么\n",
          "```",
          "<源>/clip.mp4          93 帧预览",
          "<源>/fr_0000/0037/0092.png   三帧成品（mask 已涂黑）",
          "<源>/mk_0000/0037/0092.png   对应的黑白 mask（白=真实，算 loss）",
          "<源>/rule_mask.png     该窗口冻结的接缝条",
          "<源>/manifest.json     出处 + 各项数字",
          "```\n",
          "mask 契约与 av2_1plus92_v15 §3 完全一致：**白 = 严格真实的相机像素**，",
          "**黑 = 没有可信真实像素，交给生成模型**。每个样本的 manifest 里",
          "`keep_px_not_written: 0`，即「白区 100% 是真的」是被代码强制保证的，不是声明。\n"]
    return "\n".join(L)


if __name__ == "__main__":
    main()
