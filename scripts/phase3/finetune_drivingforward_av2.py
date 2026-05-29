"""Finetune DrivingForward (depth_net + gs_net) on AV2 — kill zero-shot streaks/softness.

Objective (targets the Phase-2 failures directly):
  - LiDAR depth supervision: project AV2 LiDAR into each of the 6 chosen cameras,
    L1 on predicted metric depth where LiDAR hits -> anchors scale, removes the
    near-ground depth errors that cause the "comb" streaks in the ERP.
  - Photometric reconstruction: re-render K real views from the aggregated
    ego-frame Gaussians (repo pts2render, SF) and L1 vs the input image -> sharpens.
  - Edge-aware disparity smoothness (light).

Self-contained: builds the model-input dict directly (reuses the adapter helpers),
bypasses the nuScenes/DGP dataloader. Runs in the `df` conda env on A100.
Checkpoints depth_net/gs_net to Drive periodically.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO_DF = Path("/content/DrivingForward")
REPO_W2P = Path("/content/waymo2panorama")
for p in [str(REPO_DF), str(REPO_DF / "external" / "packnet_sfm"), str(REPO_DF / "external"), str(REPO_W2P / "code")]:
    sys.path.insert(0, p)

import dibr_drivingforward_av2 as dfa  # noqa: E402  (build_inputs, pick_6, to_depth, load_cfg, _load_state, NUSC_SLOTS)


def lidar_depth_for_cams(frame, chosen, H, W, device, min_r=0.5, max_r=80.0):
    """Project nearest LiDAR sweep into each chosen camera -> sparse metric depth [6,1,H,W] (0=invalid)."""
    # Read LiDAR feather directly (av2.read_lidar_sweep needs py3.9; df is py3.8).
    import pandas as pd
    sweep_dir = Path(frame._log_dir) / "sensors" / "lidar"
    sweeps = sorted(sweep_dir.glob("*.feather"))
    tss = np.array([int(p.stem) for p in sweeps], dtype=np.int64)
    idx = int(np.argmin(np.abs(tss - int(frame.anchor_ts))))
    df_ = pd.read_feather(sweeps[idx])
    pts = df_[["x", "y", "z"]].values.astype(np.float64)
    out = torch.zeros(6, 1, H, W, dtype=torch.float32, device=device)
    ranges = np.linalg.norm(pts, axis=1)
    keep = (ranges >= min_r) & (ranges <= max_r)
    P = pts[keep].astype(np.float64)
    for si, slot in enumerate(dfa.NUSC_SLOTS):
        cam = chosen[slot]
        calib = frame.calibrations[cam]
        T = calib.T_ego_cam.astype(np.float64)  # cam->ego
        R = T[:3, :3]; t = T[:3, 3]
        Pc = (P - t[None, :]) @ R  # ego->cam  (R^T @ (X-t)) == (X-t)@R
        z = Pc[:, 2]
        img = frame.images[cam]
        h0, w0 = img.shape[:2]
        K = calib.K.astype(np.float64).copy()
        K[0] *= (W / w0); K[1] *= (H / h0)
        u = K[0, 0] * Pc[:, 0] / z + K[0, 2]
        v = K[1, 1] * Pc[:, 1] / z + K[1, 2]
        m = (z > min_r) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
        ui = u[m].astype(np.int32); vi = v[m].astype(np.int32); zz = z[m].astype(np.float32)
        order = np.argsort(-zz)  # near overwrites far
        d = np.zeros((H, W), np.float32)
        d[vi[order], ui[order]] = zz[order]
        out[si, 0] = torch.from_numpy(d).to(device)
    return out


def edge_aware_smooth(disp, img):
    gx = torch.abs(disp[..., :, :-1] - disp[..., :, 1:])
    gy = torch.abs(disp[..., :-1, :] - disp[..., 1:, :])
    ix = torch.mean(torch.abs(img[..., :, :-1] - img[..., :, 1:]), 1, keepdim=True)
    iy = torch.mean(torch.abs(img[..., :-1, :] - img[..., 1:, :]), 1, keepdim=True)
    return (gx * torch.exp(-ix)).mean() + (gy * torch.exp(-iy)).mean()


def build_frame_list(av2_root, stride):
    from waymo2panorama.data_io.av2_loader import AV2RingLoader
    root = Path(av2_root)
    items = []
    for log in sorted([d for d in root.iterdir() if d.is_dir()]):
        try:
            loader = AV2RingLoader(log)
            ts = loader.anchor_timestamps_ns()
        except Exception as e:
            print(f"  skip {log.name}: {e}", flush=True)
            continue
        for ai in range(0, len(ts), stride):
            items.append((log, ai))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", default="/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
    ap.add_argument("--weights", default="/content/DrivingForward/pretrained/weights_SF")
    ap.add_argument("--out-dir", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/dfwd_av2_finetune_v1")
    ap.add_argument("--iters", type=int, default=3000)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--stride", type=int, default=6)
    ap.add_argument("--views-per-step", type=int, default=2)
    ap.add_argument("--w-depth", type=float, default=0.2)
    ap.add_argument("--w-smooth", type=float, default=0.01)
    ap.add_argument("--save-every", type=int, default=500)
    ap.add_argument("--log-every", type=int, default=25)
    args = ap.parse_args()

    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    device = "cuda"
    H, W = 352, 640
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from network import DepthNetwork
    import types
    if "dataset" not in sys.modules:
        st = types.ModuleType("dataset"); st.construct_dataset = lambda *a, **k: None
        sys.modules["dataset"] = st
    from models.gaussian import GaussianNetwork, depth2pc, pts2render, focal2fov, getProjectionMatrix, rotate_sh
    from einops import rearrange

    cfg = dfa.load_cfg(REPO_DF / "configs/nuscenes/main.yaml")
    depth_net = DepthNetwork(cfg).to(device).train()
    gs_net = GaussianNetwork(rgb_dim=3, depth_dim=1).to(device).train()
    print("[load depth_net]", dfa._load_state(depth_net, Path(args.weights) / "depth_net.pth"), flush=True)
    print("[load gs_net]", dfa._load_state(gs_net, Path(args.weights) / "gs_net.pth"), flush=True)

    frames = build_frame_list(args.av2_root, args.stride)
    print(f"[data] {len(frames)} training frames (stride {args.stride})", flush=True)
    rng = np.random.RandomState(0)

    params = list(depth_net.parameters()) + list(gs_net.parameters())
    opt = torch.optim.Adam(params, lr=args.lr)

    # cache loaders
    loaders = {}
    t0 = time.time()
    running = {"total": 0.0, "photo": 0.0, "depth": 0.0, "n": 0}
    for it in range(1, args.iters + 1):
        log_dir, ai = frames[rng.randint(len(frames))]
        if log_dir not in loaders:
            loaders[log_dir] = AV2RingLoader(log_dir)
        loader = loaders[log_dir]
        ts = loader.anchor_timestamps_ns()[ai]
        frame = loader.load_synced_frame(ts)
        frame._log_dir = log_dir; frame.anchor_ts = ts
        chosen, _ = dfa.pick_6(list(RING_CAMS_7), frame.calibrations)
        inputs = dfa.build_inputs(frame, chosen, H, W, device)
        try:
            lidar_d = lidar_depth_for_cams(frame, chosen, H, W, device)
        except Exception as e:
            print(f"  iter {it}: lidar skip ({e})", flush=True)
            continue

        depth_feats = depth_net(inputs)
        outputs = {("cam", c): {} for c in range(6)}
        for c in range(6):
            outputs[("cam", c)].update(depth_feats[("cam", c)])
        depth_loss = 0.0
        smooth_loss = 0.0
        for c in range(6):
            K0 = inputs[("K", 0)][:, c, ...]
            disp = outputs[("cam", c)][("disp", 0)]
            depth = dfa.to_depth(disp, K0, H, W)
            outputs[("cam", c)][("depth", 0, 0)] = depth
            e2c = inputs["extrinsics_inv"][:, c, ...]
            outputs[("cam", c)][("xyz", 0, 0)] = depth2pc(depth, e2c, K0)
            outputs[("cam", c)][("pts_valid", 0, 0)] = torch.ones(1, H * W, dtype=torch.bool, device=device)
            rot, scale, opac, sh = gs_net(inputs[("color", 0, 0)][:, c, ...], depth, outputs[("cam", c)][("img_feat", 0, 0)])
            c2w = rearrange(inputs["extrinsics"][:, c, :3, :3], "k i j -> k () () () i j")
            sh = rotate_sh(sh, c2w[..., None, :, :])
            outputs[("cam", c)][("rot_maps", 0, 0)] = rot
            outputs[("cam", c)][("scale_maps", 0, 0)] = scale
            outputs[("cam", c)][("opacity_maps", 0, 0)] = opac
            outputs[("cam", c)][("sh_maps", 0, 0)] = sh
            intr, extr = K0[0], e2c[0]
            proj = getProjectionMatrix(0.01, 80.0, intr, H, W).transpose(0, 1).to(device)
            wvt = extr.transpose(0, 1).to(device)
            outputs[("cam", c)][("FovX", 0, 0)] = torch.tensor([focal2fov(intr[0, 0], W)]).to(device)
            outputs[("cam", c)][("FovY", 0, 0)] = torch.tensor([focal2fov(intr[1, 1], H)]).to(device)
            outputs[("cam", c)][("world_view_transform", 0, 0)] = wvt.unsqueeze(0)
            outputs[("cam", c)][("full_proj_transform", 0, 0)] = wvt.unsqueeze(0).bmm(proj.unsqueeze(0))
            outputs[("cam", c)][("camera_center", 0, 0)] = wvt.inverse()[3, :3].unsqueeze(0)
            # LiDAR depth L1 (log space) on valid pixels
            gt = lidar_d[c:c + 1]
            mask = (gt > 0).float()
            if mask.sum() > 10:
                pd = depth.clamp(1.0, 80.0)
                depth_loss = depth_loss + (torch.abs(torch.log(pd) - torch.log(gt.clamp(1.0, 80.0))) * mask).sum() / mask.sum()
            smooth_loss = smooth_loss + edge_aware_smooth(disp, inputs[("color", 0, 0)][:, c, ...])

        # photometric on a random subset of views (memory)
        view_ids = rng.choice(6, size=min(args.views_per_step, 6), replace=False)
        photo_loss = 0.0
        for c in view_ids:
            ren = pts2render(inputs, outputs, 6, int(c), 0, [0.0, 0.0, 0.0], "SF")
            inp = inputs[("color", 0, 0)][:, int(c), ...]
            photo_loss = photo_loss + torch.abs(ren - inp).mean()
        photo_loss = photo_loss / len(view_ids)
        depth_loss = depth_loss / 6
        smooth_loss = smooth_loss / 6
        loss = photo_loss + args.w_depth * depth_loss + args.w_smooth * smooth_loss

        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()

        running["total"] += float(loss); running["photo"] += float(photo_loss)
        running["depth"] += float(depth_loss); running["n"] += 1
        if it % args.log_every == 0:
            n = running["n"]
            print(f"[it {it}/{args.iters}] loss={running['total']/n:.4f} photo={running['photo']/n:.4f} "
                  f"depth={running['depth']/n:.4f} ({(time.time()-t0)/it:.2f}s/it)", flush=True)
            running = {"total": 0.0, "photo": 0.0, "depth": 0.0, "n": 0}
        if it % args.save_every == 0 or it == args.iters:
            torch.save(depth_net.state_dict(), out / "depth_net.pth")
            torch.save(gs_net.state_dict(), out / "gs_net.pth")
            with open(out / "progress.json", "w") as f:
                json.dump({"iter": it, "elapsed_s": round(time.time() - t0, 1)}, f)
            print(f"  [ckpt] saved at iter {it} -> {out}", flush=True)
    print("[finetune done]", flush=True)


if __name__ == "__main__":
    main()
