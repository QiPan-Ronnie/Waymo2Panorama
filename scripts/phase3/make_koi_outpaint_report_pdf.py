# -*- coding: utf-8 -*-
"""Build a short Koi-facing PDF report for the DiT360 keep-center outpaint experiment.
Style mirrors the meeting reference PDF (markdown-export look): prose + big stacked ERP images.
matplotlib + Microsoft YaHei (CJK). A4 portrait, 4 pages: text / input imgs / output imgs / text.
"""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
from PIL import Image

CJK = FontProperties(fname=r"C:\Windows\Fonts\msyh.ttc")
PW, PH = 8.27, 11.69  # A4 inches
XL, W = 0.09, 0.82

BASE = Path(r"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\deliverables\koi_outpaint_center")
SRC = BASE.parent / "dit360_seam_completion/runs_v14_trimap_clamp_bmw/trimap_r008_h016_w025_tau5"
OUT_PDF = Path(r"D:\BaiduSyncdisk\2024 to future\koi chen\meeting\DiT360_outpaint_汇报_2026-05-30.pdf")


def text_page(pdf, lines):
    fig = plt.figure(figsize=(PW, PH))
    y = 0.95
    for ln in lines:
        t = ln.get("t", "")
        size = ln.get("size", 11.5)
        color = ln.get("c", "#111111")
        y -= ln.get("gap", 0.0)
        fig.text(XL, y, t, fontproperties=CJK, fontsize=size, color=color,
                 ha="left", va="top", wrap=False)
        y -= (size / 72.0 / PH) * 1.85
    pdf.savefig(fig); plt.close(fig)


def image_page(pdf, header, items):
    fig = plt.figure(figsize=(PW, PH))
    y = 0.95
    if header:
        fig.text(XL, y, header, fontproperties=CJK, fontsize=14, color="#111111",
                 ha="left", va="top")
        y -= 0.045
    for cap, path in items:
        if cap:
            fig.text(XL, y, cap, fontproperties=CJK, fontsize=10.5, color="#333333",
                     ha="left", va="top")
            y -= 0.026
        im = Image.open(path)
        aspect = im.height / im.width
        h = W * (PW / PH) * aspect
        ax = fig.add_axes([XL, y - h, W, h])
        ax.imshow(im); ax.axis("off")
        y -= (h + 0.035)
    pdf.savefig(fig); plt.close(fig)


with PdfPages(OUT_PDF) as pdf:
    # --- Page 1: 标题 + 架构 + Input 说明 (纯文字) ---
    text_page(pdf, [
        {"t": "DiT360 Outpainting 实验汇报", "size": 19},
        {"t": "只保留正中心一小块，让模型补全整个 360°   ·   BMW 场景   ·   2026-05-30", "size": 11, "c": "#555555", "gap": 0.004},

        {"t": "一、用的是不是 DiT360 原论文的架构？—— 是。", "size": 14, "gap": 0.05},
        {"t": "• 生成主干就是官方 DiT360：FLUX.1-dev + 官方 DiT360 LoRA，分辨率 1024×2048", "gap": 0.012},
        {"t": "  （论文唯一的训练尺寸，换尺寸结果会异常）。", },
        {"t": "• 补全机制就是官方 editing.py 那一套：先把原图做 RF-Inversion 反演，再用", "gap": 0.006},
        {"t": "  Personalize-Anything 的注意力保留（mask 白色=保留原图，其余区域生成）。", },
        {"t": "  论文里 inpaint / outpaint 的区别只是“换一张 mask”而已。", },
        {"t": "• 我们在官方流程上只多加了一层“潜变量钳制”：把要保留的中心硬锁到原图", "gap": 0.006},
        {"t": "  （保证中心锚点逐字节不变），并把一致性参数 tau 调得更强。", },
        {"t": "  → 架构主体 = 原论文，这一层只是我们的“保真外壳”，不改模型本身。", "c": "#1a5a1a"},

        {"t": "二、DiT360 吃进去的数据（Input）", "size": 14, "gap": 0.05},
        {"t": "DiT360 需要三样东西：", "gap": 0.012},
        {"t": "① 一张 1024×2048 的 ERP 全景原图 —— 我们用 7 路环视相机 L1 hard_select", "gap": 0.010},
        {"t": "   拼出来的 BMW 街景（下一页上图）；", },
        {"t": "② 一张同尺寸的 mask：白=保留、黑=生成。这次按 Koi 的要求，只保留正中心", "gap": 0.006},
        {"t": "   约 5%（前向相机的“正前方道路 + 天空”那一块），其余约 95%（两侧 / 后方 /", },
        {"t": "   天 / 地）全部交给模型生成（下一页下图，绿=保留，红=生成）；", },
        {"t": "③ 一句文字 prompt（街景描述）。", "gap": 0.006},
    ])

    # --- Page 2: Input 图 ---
    image_page(pdf, "Input：原图 + mask（这次只保留正中心 ~5%）", [
        ("① 原图（L1 hard_select 拼的 BMW 360，黑色处是相机看不到的天/地）",
         SRC / "trimap_r008_h016_w025_tau5_hard_select_fullres_1024x2048.png"),
        ("② mask 叠加：绿 = 保留（只留正中心前向那一块）， 红 = 要模型生成（~95%）",
         BASE / "bmw_hardselect_coremask_sector_preview.jpg"),
    ])

    # --- Page 3: 输出 图 ---
    image_page(pdf, "三、DiT360 吐出来的结果（Output）", [
        ("一张完整的 1024×2048 ERP 全景：中心保留原样，四周 95% 全由模型 outpaint 生成。",
         BASE / "results/hardselect_sector/hardselect_sector_raw.png"),
        ("（保留“中心矩形”的另一种变体，结果类似；换另一张源图也几乎一致。）",
         BASE / "results/hardselect_window/hardselect_window_raw.png"),
    ])

    # --- Page 4: 解释与结论 ---
    text_page(pdf, [
        {"t": "四、解释与结论", "size": 14},
        {"t": "• 生成本身很强：仅凭 5% 的中心锚点，DiT360 就补出了连贯、逼真、含天含地", "gap": 0.05},
        {"t": "  的完整 360，看起来就是一条真实街道。", },
        {"t": "• 但保留的中心是个明显的“方框”：真实中心是大晴天的蓝天，模型生成的四周", "gap": 0.014},
        {"t": "  却是阴天灰调，光照 / 色调 / 车道线都接不上，中心像被贴了一块进去。", },
        {"t": "• 那 95% 完全是虚构的：模型编了一座完全不同的城市（像欧洲老街、红砖楼），", "gap": 0.014},
        {"t": "  还编出了小汽车、面包车、招牌等显著物体 —— 现实里根本没有。", },
        {"t": "• 原因：DiT360 对 mask 区域是“按 prompt 自行创作”。mask 旁边的真实图像", "gap": 0.014},
        {"t": "  越充分、越连续，模型越容易补得像原场景；这次只给中心、左右没有真实", },
        {"t": "  对照区域，它就只能凭空编。", },
        {"t": "• 对 Bosch world-model 训练数据：虚构的显著物体 = 教错场景统计。", "gap": 0.014, "c": "#8a1a1a"},
        {"t": "  所以“只留中心、补全整个 360”作为“看看 DiT360 能力”的演示很惊艳，", "c": "#8a1a1a"},
        {"t": "  但不能直接当作忠实的训练数据。", "c": "#8a1a1a"},
        {"t": "一句话：DiT360 是很强的“全景生成器”，但不是对真实 360 的“忠实重建器”。", "size": 12.5, "gap": 0.05, "c": "#111111"},
        {"t": "保留区越小、真实对照越少，它编得越多。", "size": 12.5, "c": "#111111"},
    ])

print("wrote", OUT_PDF)
