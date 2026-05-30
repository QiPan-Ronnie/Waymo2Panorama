"""Evaluate the #3 held-out-camera model: render each ring camera FROM ITS NEIGHBOURS ONLY
(zero its own Gaussians) and compare to the REAL camera. Tests whether the model learned a
faithful + sharp neighbour->held-out-camera mapping (the wall-breaker hypothesis). Runs in df env.
Saves per-camera [REAL | held-out RENDER] panels for VISION judging.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import numpy as np
import torch
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO_DF = Path("/content/DrivingForward"); REPO_W2P = Path("/content/waymo2panorama")
for p in [str(REPO_DF), str(REPO_DF / "external" / "packnet_sfm"), str(REPO_DF / "external"), str(REPO_W2P / "code")]:
    sys.path.insert(0, p)
import dibr_drivingforward_av2 as dfa  # noqa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="bmw")
    args = ap.parse_args()
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    device = "cuda"; H, W = 352, 640
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from network import DepthNetwork
    import types
    if "dataset" not in sys.modules:
        st = types.ModuleType("dataset"); st.construct_dataset = lambda *a, **k: None; sys.modules["dataset"] = st
    from models.gaussian import GaussianNetwork, depth2pc, pts2render, focal2fov, getProjectionMatrix, rotate_sh
    from einops import rearrange

    cfg = dfa.load_cfg(REPO_DF / "configs/nuscenes/main.yaml")
    depth_net = DepthNetwork(cfg).to(device).eval()
    gs_net = GaussianNetwork(rgb_dim=3, depth_dim=1).to(device).eval()
    print("[load]", dfa._load_state(depth_net, Path(args.weights) / "depth_net.pth"),
          dfa._load_state(gs_net, Path(args.weights) / "gs_net.pth"), flush=True)

    loader = AV2RingLoader(Path(args.log_dir)); ts = loader.anchor_timestamps_ns()[args.anchor]
    frame = loader.load_synced_frame(ts); frame._log_dir = Path(args.log_dir); frame.anchor_ts = ts
    chosen, _ = dfa.pick_6(list(RING_CAMS_7), frame.calibrations)
    inputs = dfa.build_inputs(frame, chosen, H, W, device)
    with torch.no_grad():
        depth_feats = depth_net(inputs)
        outputs = {("cam", c): {} for c in range(6)}
        for c in range(6):
            outputs[("cam", c)].update(depth_feats[("cam", c)])
            K0 = inputs[("K", 0)][:, c, ...]; disp = outputs[("cam", c)][("disp", 0)]
            depth = dfa.to_depth(disp, K0, H, W); outputs[("cam", c)][("depth", 0, 0)] = depth
            e2c = inputs["extrinsics_inv"][:, c, ...]
            outputs[("cam", c)][("xyz", 0, 0)] = depth2pc(depth, e2c, K0)
            outputs[("cam", c)][("pts_valid", 0, 0)] = torch.ones(1, H * W, dtype=torch.bool, device=device)
            rot, scale, opac, sh = gs_net(inputs[("color", 0, 0)][:, c, ...], depth, outputs[("cam", c)][("img_feat", 0, 0)])
            c2w = rearrange(inputs["extrinsics"][:, c, :3, :3], "k i j -> k () () () i j"); sh = rotate_sh(sh, c2w[..., None, :, :])
            outputs[("cam", c)][("rot_maps", 0, 0)] = rot; outputs[("cam", c)][("scale_maps", 0, 0)] = scale
            outputs[("cam", c)][("opacity_maps", 0, 0)] = opac; outputs[("cam", c)][("sh_maps", 0, 0)] = sh
            intr, extr = K0[0], e2c[0]
            proj = getProjectionMatrix(0.01, 80.0, intr, H, W).transpose(0, 1).to(device); wvt = extr.transpose(0, 1).to(device)
            outputs[("cam", c)][("FovX", 0, 0)] = torch.tensor([focal2fov(intr[0, 0], W)]).to(device)
            outputs[("cam", c)][("FovY", 0, 0)] = torch.tensor([focal2fov(intr[1, 1], H)]).to(device)
            outputs[("cam", c)][("world_view_transform", 0, 0)] = wvt.unsqueeze(0)
            outputs[("cam", c)][("full_proj_transform", 0, 0)] = wvt.unsqueeze(0).bmm(proj.unsqueeze(0))
            outputs[("cam", c)][("camera_center", 0, 0)] = wvt.inverse()[3, :3].unsqueeze(0)

        panels = []
        for c in range(6):
            saved = outputs[("cam", c)][("pts_valid", 0, 0)]
            outputs[("cam", c)][("pts_valid", 0, 0)] = torch.zeros_like(saved)  # HELD OUT
            ren = pts2render(inputs, outputs, 6, c, 0, [0.0, 0.0, 0.0], "SF")
            outputs[("cam", c)][("pts_valid", 0, 0)] = saved
            real = inputs[("color", 0, 0)][:, c, ...]
            r = (ren.reshape(3, H, W).clamp(0, 1).cpu().numpy().transpose(1, 2, 0) * 255).astype(np.uint8)
            gt = (real.reshape(3, H, W).clamp(0, 1).cpu().numpy().transpose(1, 2, 0) * 255).astype(np.uint8)
            sep = np.full((H, 4, 3), 200, np.uint8)
            panels.append(np.hstack([gt, sep, r]))
        sep = np.full((6, panels[0].shape[1], 3), 90, np.uint8)
        out = panels[0]
        for p in panels[1:]:
            out = np.vstack([out, sep, p])
        Image.fromarray(out).save(Path(args.out_dir) / f"{args.tag}_heldout_realVSrender.png")
        print(f"[{args.tag}] saved heldout eval (left=REAL, right=neighbour-render) -> {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
