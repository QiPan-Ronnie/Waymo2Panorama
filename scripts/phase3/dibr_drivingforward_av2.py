"""DrivingForward zero-shot on AV2 — sanity milestone: re-render the real views.

Go/no-go probe for the feed-forward 3DGS single-center route (sweep #1). Loads
nuScenes-pretrained DrivingForward depth_net + gs_net, feeds an AV2 ring frame
(7 cams mapped to the model's 6 nuScenes slots by azimuth), predicts per-pixel
3D Gaussians in the ego frame, and RE-RENDERS each of the 6 real camera views
from the aggregated Gaussians via the repo's own pts2render. Comparing rendered
vs input tells us whether the nuScenes-trained nets generalize to AV2 at all,
BEFORE we invest in the cubemap->ERP single-center render.

Bypasses the dataset/DGP import chain; constructs the input dict directly.
Run inside the `df` conda env on Colab (torch2.2+cu121, CUDA exts built).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import yaml

REPO_DF = Path("/content/DrivingForward")
REPO_W2P = Path("/content/waymo2panorama")
for p in [REPO_DF, REPO_DF / "external" / "packnet_sfm", REPO_DF / "external", str(REPO_W2P / "code")]:
    sys.path.insert(0, str(p))

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)

# nuScenes 6 slots and their target azimuth in AV2 ego frame (x-fwd, y-left; +az = left)
NUSC_SLOTS = ["CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT", "CAM_BACK_LEFT", "CAM_BACK_RIGHT", "CAM_BACK"]
SLOT_TARGET_AZ_DEG = {"CAM_FRONT": 0.0, "CAM_FRONT_LEFT": 55.0, "CAM_FRONT_RIGHT": -55.0,
                      "CAM_BACK_LEFT": 125.0, "CAM_BACK_RIGHT": -125.0, "CAM_BACK": 180.0}


def load_cfg(path: Path):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["model"]["novel_view_mode"] = "SF"
    cfg["data"]["num_cams"] = 6
    cfg["data"]["mode"] = "eval"
    cfg["training"]["batch_size"] = 1
    return cfg


def _load_state(net, pth):
    sd = torch.load(pth, map_location="cpu")
    if isinstance(sd, dict) and "model" in sd and isinstance(sd["model"], dict):
        sd = sd["model"]
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
    new = {}
    for k, v in sd.items():
        new[k[7:] if k.startswith("module.") else k] = v
    missing, unexpected = net.load_state_dict(new, strict=False)
    return len(new), len(missing), len(unexpected)


def cam_azimuth_deg(T_ego_cam):
    # camera optical axis (+z in cam) expressed in ego = R_ego_cam @ [0,0,1] = 3rd column
    fwd = T_ego_cam[:3, 2]
    return float(np.degrees(np.arctan2(fwd[1], fwd[0])))


def pick_6(av2_cams, calibs):
    az = {c: cam_azimuth_deg(calibs[c].T_ego_cam) for c in av2_cams}
    chosen = {}
    used = set()
    for slot in NUSC_SLOTS:
        tgt = SLOT_TARGET_AZ_DEG[slot]
        best, bestd = None, 1e9
        for c in av2_cams:
            if c in used:
                continue
            d = abs((az[c] - tgt + 180) % 360 - 180)
            if d < bestd:
                best, bestd = c, d
        chosen[slot] = best
        used.add(best)
    return chosen, az


def build_inputs(frame, chosen, H, W, device):
    colors, Ks_native = [], []
    for slot in NUSC_SLOTS:
        cam = chosen[slot]
        img = frame.images[cam]  # HxWx3 RGB uint8
        h0, w0 = img.shape[:2]
        rs = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        rs = (rs - IMAGENET_MEAN) / IMAGENET_STD
        colors.append(torch.from_numpy(rs.transpose(2, 0, 1)))
        K = frame.calibrations[cam].K.astype(np.float64).copy()
        K[0] *= (W / w0)
        K[1] *= (H / h0)
        Ks_native.append(K)
    color = torch.stack(colors, 0).unsqueeze(0).float().to(device)  # [1,6,3,H,W]

    inputs = {}
    inputs[("color", 0, 0)] = color
    inputs[("color_aug", 0, 0)] = color
    for s in range(4):
        Ks, invKs = [], []
        for K in Ks_native:
            K4 = np.eye(4, dtype=np.float64)
            Ksc = K.copy()
            Ksc[:2] /= (2 ** s)
            K4[:3, :3] = Ksc
            Ks.append(torch.from_numpy(K4))
            invKs.append(torch.from_numpy(np.linalg.inv(K4)))
        inputs[("K", s)] = torch.stack(Ks, 0).unsqueeze(0).float().to(device)
        inputs[("inv_K", s)] = torch.stack(invKs, 0).unsqueeze(0).float().to(device)
    extr, extr_inv = [], []
    for slot in NUSC_SLOTS:
        T = frame.calibrations[chosen[slot]].T_ego_cam.astype(np.float64)  # cam->ego
        extr.append(torch.from_numpy(T))
        extr_inv.append(torch.from_numpy(np.linalg.inv(T)))
    inputs["extrinsics"] = torch.stack(extr, 0).unsqueeze(0).float().to(device)
    inputs["extrinsics_inv"] = torch.stack(extr_inv, 0).unsqueeze(0).float().to(device)
    inputs["mask"] = torch.ones(1, 6, 1, H, W, dtype=torch.float32, device=device)
    return inputs


def to_depth(disp, K0_cam, H, W, min_depth=1.5, max_depth=80.0, fls=300.0):
    import torch.nn.functional as F
    min_disp, max_disp = 1.0 / max_depth, 1.0 / min_depth
    disp = F.interpolate(disp, [H, W], mode="bilinear", align_corners=False)
    disp = min_disp + (max_disp - min_disp) * disp
    depth = 1.0 / disp
    return depth * K0_cam[:, 0:1, 0:1].unsqueeze(2) / fls


def run(args):
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from network import DepthNetwork
    from models.gaussian import GaussianNetwork, depth2pc, pts2render, focal2fov, getProjectionMatrix, rotate_sh
    from einops import rearrange

    device = "cuda"
    H, W = 352, 640
    cfg = load_cfg(REPO_DF / "configs/nuscenes/main.yaml")
    print("[cfg] num_cams=6 novel_view_mode=SF", flush=True)

    depth_net = DepthNetwork(cfg).to(device).eval()
    gs_net = GaussianNetwork(rgb_dim=3, depth_dim=1).to(device).eval()
    w = Path(args.weights)
    print("[load depth_net]", _load_state(depth_net, w / "depth_net.pth"), flush=True)
    print("[load gs_net]", _load_state(gs_net, w / "gs_net.pth"), flush=True)

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    results = []
    for case in args.cases:
        short, anchor = case.split(":")[0], int(case.split(":")[1])
        log_dir = Path(args.av2_root) / [d.name for d in Path(args.av2_root).iterdir() if d.name.startswith(short)][0]
        loader = AV2RingLoader(log_dir)
        ts = loader.anchor_timestamps_ns()[anchor]
        frame = loader.load_synced_frame(ts)
        chosen, az = pick_6(list(RING_CAMS_7), frame.calibrations)
        print(f"[case {short}:{anchor}] az={ {k: round(v,1) for k,v in az.items()} }", flush=True)
        print(f"  mapping={chosen}", flush=True)

        inputs = build_inputs(frame, chosen, H, W, device)
        with torch.no_grad():
            depth_feats = depth_net(inputs)
            outputs = {("cam", c): {} for c in range(6)}
            for c in range(6):
                outputs[("cam", c)].update(depth_feats[("cam", c)])
            # frame0 gaussians + camera params (SF)
            for c in range(6):
                K0 = inputs[("K", 0)][:, c, ...]
                disp = outputs[("cam", c)][("disp", 0)]
                depth = to_depth(disp, K0, H, W)
                outputs[("cam", c)][("depth", 0, 0)] = depth
                e2c = inputs["extrinsics_inv"][:, c, ...]
                outputs[("cam", c)][("xyz", 0, 0)] = depth2pc(depth, e2c, K0)
                outputs[("cam", c)][("pts_valid", 0, 0)] = (depth != 0.0).view(1, -1)
                rot, scale, opac, sh = gs_net(inputs[("color", 0, 0)][:, c, ...], depth,
                                              outputs[("cam", c)][("img_feat", 0, 0)])
                c2w_rot = rearrange(inputs["extrinsics"][:, c, :3, :3], "k i j -> k () () () i j")
                sh = rotate_sh(sh, c2w_rot[..., None, :, :])
                outputs[("cam", c)][("rot_maps", 0, 0)] = rot
                outputs[("cam", c)][("scale_maps", 0, 0)] = scale
                outputs[("cam", c)][("opacity_maps", 0, 0)] = opac
                outputs[("cam", c)][("sh_maps", 0, 0)] = sh
                # camera params for frame0 real view
                intr = K0[0]
                extr = e2c[0]
                proj = getProjectionMatrix(0.01, 80.0, intr, H, W).transpose(0, 1).to(device)
                wvt = extr.transpose(0, 1).to(device)
                full = wvt.unsqueeze(0).bmm(proj.unsqueeze(0)).squeeze(0)
                outputs[("cam", c)][("FovX", 0, 0)] = torch.tensor([focal2fov(intr[0, 0], W)]).to(device)
                outputs[("cam", c)][("FovY", 0, 0)] = torch.tensor([focal2fov(intr[1, 1], H)]).to(device)
                outputs[("cam", c)][("world_view_transform", 0, 0)] = wvt.unsqueeze(0)
                outputs[("cam", c)][("full_proj_transform", 0, 0)] = full.unsqueeze(0)
                outputs[("cam", c)][("camera_center", 0, 0)] = wvt.inverse()[3, :3].unsqueeze(0)

            # re-render each real view
            panels = []
            psnrs = {}
            for c in range(6):
                rendered = pts2render(inputs, outputs, cam_num=6, novel_cam=c, novel_frame_id=0,
                                      bg_color=[1.0, 1.0, 1.0], mode="SF")  # [1,3,H,W]
                ren = rendered[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
                inp = inputs[("color", 0, 0)][0, c].permute(1, 2, 0).cpu().numpy()
                inp = (inp * IMAGENET_STD + IMAGENET_MEAN).clip(0, 1)
                mse = float(np.mean((ren - inp) ** 2)) + 1e-9
                psnrs[NUSC_SLOTS[c]] = round(-10 * np.log10(mse), 2)
                pair = np.vstack([(inp * 255).astype(np.uint8), (ren * 255).astype(np.uint8)])
                cv2.putText(pair, NUSC_SLOTS[c], (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                panels.append(pair)
        mont = np.hstack(panels)
        outp = out_root / f"{short}_a{anchor:03d}_dfwd_realview_check.jpg"
        cv2.imwrite(str(outp), cv2.cvtColor(mont, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(f"  PSNR rendered-vs-input: {psnrs}", flush=True)
        print(f"  [saved] {outp}", flush=True)
        results.append({"case": case, "mapping": {k: str(v) for k, v in chosen.items()}, "psnr": psnrs})
    with open(out_root / "dfwd_realview_summary.json", "w") as f:
        json.dump(results, f, indent=2)
    print("[done]", json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", default="/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
    ap.add_argument("--weights", default="/content/DrivingForward/pretrained/weights_SF")
    ap.add_argument("--out-dir", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/dibr_drivingforward_av2_v1")
    ap.add_argument("--cases", nargs="+", default=["02a00399:0", "fbee355f:95", "0bae3b5e:30"])
    args = ap.parse_args()
    run(args)
