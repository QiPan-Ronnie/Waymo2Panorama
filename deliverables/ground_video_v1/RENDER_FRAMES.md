# AV2 渲染帧清单 — `ground_video_v1`(旧 · 带地面 outpaint)

> 本文件夹 = **带地面 outpaint 的视频**:`GROUND_MODE="fill"`,场景中间带 + STAGE-4 地面填充,**天空留黑**。即给 BOSCH 看的「问题视频」(底部白团 / 车被吃 / smear 等缺陷在此暴露)。
> 渲染的 AV2 帧范围与隔壁 `../route2_middle_v1/`(新 · 中间-only)**完全相同**,唯一区别是 `GROUND_MODE`(见 §3)。

## 1. 渲染帧总表

数据集:**Argoverse 2 Sensor,val 划分**。"anchor" = 环视相机同步采集的帧序号(从 0 起)。每段 **93 帧连续 anchor**。

| 场景 tag | AV2 log UUID | 起始 anchor | 结束 anchor | 渲染帧数 | 该 log 总 anchor 数 F | 自车位移(窗口内) |
|---|---|---:|---:|---:|---:|---:|
| **bmw** | `02a00399-3857-444e-8db3-a8f58489c394` | 0 | 92 | **93** | 319 | ≈ 28.7 m |
| **crowd** | `fbee355f-8878-31fa-8ac8-b9a45a3f130a` | 0 | 92 | **93** | 320 | ≈ 42.9 m |
| **clean** | `0bae3b5e-417d-3b03-abaa-806b433233b8` | 0 | 92 | **93** | 319 | ≈ 24.7 m |
| **highway** | `2c652f9e-8db8-3572-aa49-fae1344a875b` | 225 | 317 | **93** | 319 | ≈ 34.1 m |

- 合计:**4 段 × 93 帧 = 372 帧**。
- bmw / crowd / clean 从 log 开头(anchor 0)起;**highway 从 anchor 225 起**(跳过 log 开头自车静止的一段,见 §5)。

## 2. 时长 / 时间戳(可回溯 AV2 原始数据)

| 场景 | 起始 anchor 时间戳 (ns) | 结束 anchor 时间戳 (ns) | AV2 真实录制时长 | 播放时长 |
|---|---|---|---:|---:|
| bmw | `315966070549927210` | `315966075149927216` | 4.60 s | 7.75 s |
| crowd | `315967341199927216` | `315967345799927215` | 4.60 s | 7.75 s |
| clean | `315969524349927218` | `315969528949927215` | 4.60 s | 7.75 s |
| highway | `315966690199927220` | `315966694799927220` | 4.60 s | 7.75 s |

- AV2 环视 anchor ≈ 20 Hz,故 93 帧 ≈ **4.60 s 真实录制**。
- 视频以 **FPS = 12** 封装 ⇒ 播放时长 = 93 / 12 = **7.75 s/段**(相当于慢放 ~1.7×)。

## 3. 旧 vs 新:同帧、只差 `GROUND_MODE`

| | 本文件夹 `ground_video_v1`(旧) | `../route2_middle_v1`(新) |
|---|---|---|
| `GROUND_MODE` | **`"fill"`** | `"off"` |
| 内容 | 场景中间带 + STAGE-4 地面 outpaint;天空黑 | 场景中间带 only;天空 + 地面**全黑** |
| 用途 | 暴露地面填充缺陷(BOSCH 会议分析) | 给下游 Cosmos DiT 的干净输入 |
| 渲染的 AV2 帧 | **完全一致**(上表) | **完全一致**(上表) |
| 核心算法 | 同一套 Fable-5 三修(虚拟中心 / EMC 快门位姿 / OMC + view-morph 接缝) | 同上 |

## 4. 渲染参数

- ERP 输出:**1024 × 2048**(H×W,2:1 等距圆柱),虚拟中心 = 环视相机质心 @ 相机高度 1.44 m。
- 窗口长度 `NWIN = 93`,封装帧率 `FPS = 12`。
- 核心脚本:`scripts/phase3/db89_ghost_recovery.py`(Fable-5 endorsed core);驱动:`scripts/phase3/video_gen_av2.py`(`GROUND_MODE="fill"`,默认)。
- 地面 outpaint = `run_case` 内 STAGE-4;天空 outpaint 是另一个脚本,这批**故意不跑** ⇒ 天空留黑。

## 5. 窗口选取依据

起始 anchor 由自车位移诊断(`video_gen_av2.py --diag`)选出:在每个 log 内取**自车持续运动**的 93 帧窗口(最大化窗口内行驶里程,惩罚任何静止子段),以保证有足够视差/纹理供拼接。
- highway log 开头自车静止,故起点后移到 **anchor 225**;其余三段从 anchor 0 即有运动。

## 6. 源帧文件(逐帧回溯)

- Drive 源帧:`koi_waymo2pano_colab/datasets/av2_ground_video_v1/{tag}_a{NNN}_segcomposite.png`(`NNN` = anchor 三位补零;highway 为 225–317,其余 000–092)。
- Drive 成片:`koi_waymo2pano_colab/datasets/av2_ground_video_v1/{tag}.mp4`。
- 本地成片:`deliverables/ground_video_v1/{tag}_h264.mp4`(h264 / yuv420p)。
- 同一批旧视频也已拷入会议 deck:`meeting/6.19_meeting with BOSCH/outpainting_deck/assets/{tag}_h264.mp4`。

---
*生成于 2026-06-19;帧数、F、时间戳均经实测核对。*
