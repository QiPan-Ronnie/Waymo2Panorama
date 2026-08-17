"""Package the four review samples and upload them to Colab."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_koi_review as B     # noqa: E402
import colab                     # noqa: E402

ENTRIES = [
    ("argoverse2", "val", "a060c4c1-b9fc-39c1-9d30-d93a124c9066",
     r"E:/w2p_data/av2/a060c4c1-b9fc-39c1-9d30-d93a124c9066",
     r"E:/w2p_data/_review_out/argoverse2/a060c4c1-b9fc-39c1-9d30-d93a124c9066",
     ["7 ring cameras, 20 Hz. Source frames are the raw AV2 JPEGs, unmodified."]),
    ("nuscenes", "trainval", "scene-0001",
     r"E:/w2p_data/_review_work/nusc_scene-0001",
     r"E:/w2p_data/_review_out/nuscenes/scene-0001",
     ["6 cameras. Images are nuScenes' own rectified JPEGs, unmodified.",
      "Frame spacing is NOT uniform in the source: its stream is 10 Hz with a",
      "LiDAR-synced keyframe inserted every ~250 ms, so real gaps cycle",
      "50/100/100 ms. We resample onto the least-jittery grid we can find",
      "(cv 0.29 -> 0.20); perfectly uniform is impossible for this dataset."]),
    ("waymo_perception", "val", "segment-10203656353524179475_7625_000_7645_000",
     r"E:/w2p_data/_review_work/wp_10203656353524179475",
     r"E:/w2p_data/_review_out/waymo_perception/10203656353524179475_7625_000_7645_000",
     ["5 cameras, NO rear camera - the ring does not close.",
      "Source frames here are RECTIFIED by us: Waymo ships k1=0.347 radial",
      "distortion, which displaces an edge pixel ~71 px, wider than a whole seam",
      "strip, and the renderer is a pinhole model.",
      "Only ~25% of the band is real pixels: 158 deg azimuth gap plus the",
      "narrowest camera seeing only +-12.1 deg vertically. koi should see this",
      "number before we mass-produce this source."]),
    ("waymo_e2e", "val", "0075c28f1f1a68c4c1c8dcf37958046b",
     r"E:/w2p_data/_review_work/e2e_0075c28f1f1a68c4",
     r"E:/w2p_data/_review_out/waymo_e2e/0075c28f1f1a68c4c1c8dcf37958046b",
     ["8 cameras, ring closes, 360 deg. No LiDAR in this dataset at all.",
      "Source frames are RECTIFIED by us (k=0.0736 -> ~6.8 px at the edge).",
      "E2E ships zeroed per-camera timestamps and an identity ego pose, so",
      "exposure-time motion compensation is not possible; the rig is treated as",
      "static and the seam strips get 6 px of skirt instead of 4.",
      "Its val split is globally shuffled - one segment's ~200 frames sit across",
      "all 93 shards - so this window was assembled by targeted range reads."]),
]


def main():
    os.makedirs(B.OUT, exist_ok=True)
    built = []
    print("packaging:")
    for ds, split, sid, log, sample, extra in ENTRIES:
        built.append(B.package(ds, split, sid, log, sample, extra))
    with open(os.path.join(B.OUT, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(readme(built))

    files = []
    for r, _, fs in os.walk(B.OUT):
        for f in fs:
            files.append(os.path.join(r, f))
    total = sum(os.path.getsize(f) for f in files) / 1e6
    print("\n%d files, %.0f MB -> Colab" % (len(files), total))

    remote_root = "/content/db241_koi_review"
    colab.sh("rm -rf %s && mkdir -p %s" % (remote_root, remote_root), 120)
    t0 = time.time()
    sent = 0
    for i, f in enumerate(sorted(files), 1):
        rel = os.path.relpath(f, B.OUT).replace("\\", "/")
        colab.put("%s/%s" % (remote_root, rel), f)
        sent += os.path.getsize(f)
        if i % 100 == 0 or i == len(files):
            print("  %d/%d  %.0f/%.0f MB  %.0fs" %
                  (i, len(files), sent / 1e6, total, time.time() - t0), flush=True)
    r, out = colab.shout("cd %s && du -sh . && find . -maxdepth 1 -type d | sort && "
                         "echo '--- per sample ---' && "
                         "for d in */; do echo \"$d $(ls $d/frames 2>/dev/null|wc -l) frames, "
                         "$(ls $d/masks 2>/dev/null|wc -l) masks, "
                         "$(find $d/source_frames -type f 2>/dev/null|wc -l) source imgs\"; done"
                         % remote_root, 300)
    print("\n=== on Colab ===")
    print(out)


def readme(built):
    L = ["# DB-241 四个数据集的构造 —— 请 koi 过目\n",
         "每个数据集一个完整样本：**93 帧成品视频 + 93 张对应 mask + 实际用到的原始相机图**。",
         "文件夹名 = `数据集__官方split__源数据原始ID`，可直接回原始 release 查这个场景。\n",
         "## 四个样本\n",
         "| 文件夹 | 相机 | 环 | 接缝带 | mask 占带 | 带内真实像素 |",
         "|---|---|---|---|---|---|"]
    for name, man, _n in built:
        w = sorted(v.get("strip_w", 0) for v in man["pairs"].values())
        L.append("| `%s` | %d | %s | %d–%d px | %.1f%% | 见 SOURCE.txt |"
                 % (name, man["n_cameras"],
                    "闭合" if man["ring_closed"] else "**开口（无后视相机）**",
                    min(w), max(w), 100 * man["rule_mask_frac_of_band"]))
    L += ["",
          "## 每个文件夹里有什么\n",
          "```",
          "clip.mp4                    93 帧成品视频",
          "frames/fr_0000..0092.png    93 帧 ERP（2048x1024，接缝已涂黑）",
          "masks/ mk_0000..0092.png    93 张黑白 mask，与帧一一对应",
          "rule_mask.png               该窗口冻结的接缝条（93 帧共用一张）",
          "source_frames/<相机>/*.jpg   **这 93 帧实际用到的原始相机图**",
          "SOURCE.txt                  出处与全部关键数字",
          "manifest.json               机器可读的同一份信息",
          "```\n",
          "根目录另有 `CONSTRUCTION_<数据集>.png` 四张构造图，一张图看懂三层：",
          "**原始相机图 → 拼成的 ERP → 对应 mask**。\n",
          "## mask 契约（与 av2_1plus92_v15 §3 完全一致）\n",
          "> **白 (255) = 严格真实的相机像素** —— 可信监督，算 loss  ",
          "> **黑 (0) = 没有可信真实像素** —— 交给生成模型\n",
          "「严格真实」是**代码强制**的，不是声明：mask 由「投影实际落到哪里」构建，",
          "而不是由「相机声称覆盖哪里」构建，每个样本的 manifest 里",
          "`keep_px_not_written: 0`。\n",
          "黑色来自且仅来自三处：rig 自身盲区（天顶、天底、环不闭合时的空洞）、",
          "接缝条、以及车头（本批四个样本都是 `hood_variant: keep`，未挖车头）。\n",
          "**不要用阈值从画面反推 mask** —— 黑是合法的图像颜色（夜景），",
          "区分「缺失」和「暗物体」的是 mask 通道本身。\n",
          "## 三件想请你拍板的事\n",
          "**1. Waymo Perception 带内只有约 25% 是真实像素。**",
          "你 7/31 说「就算 open waymo 只有 252 度也没关系，后面两个就算是生的」——",
          "判断依然成立，但实测密度更低：去畸变裁边后方位覆盖 202°、空洞 158°，",
          "而且最窄那路相机垂直只有 ±12.1°。想先让你看到这个数字。\n",
          "**2. 车头两版没做。** 你 8/14 要的车头黑/保留两版按场景各半，我们没做，",
          "因为四个 rig 都没有验证过的车身 mask：v15 那张是 1+92 时代几何、对不上现在的渲染；",
          "用「车头是唯一相对车静止的物体」实测，AV2 车身在画面最左右（车后）、带内仅约 0.02%。",
          "凭猜画一张就是在真实像素上涂黑。请定：(A) 每 rig 做验证 mask，(B) 不做，(C) 只做 AV2。\n",
          "**3. loss 一定要乘 mask。** 你 8/14 跟路易斯说「这一次要完全只算景硕给你 mask 的地方，",
          "绝对不要算那些黑色」。他 stage-1 现在的极性是**相反**的（白区不算、mask 区算），",
          "麻烦确认 stage-2 的代码确实改了 —— 没改的话上面所有 mask 工作在训练端都不生效。\n",
          "## 通过后",
          "四个源可直接在 Colab 上量产，同一套配方、同一个 golden test 守着。",
          "本机已产 930 个样本（E2E 339 / AV2 222 / Perception 199 / nuScenes 170）作为参考。\n"]
    return "\n".join(L)


if __name__ == "__main__":
    main()
