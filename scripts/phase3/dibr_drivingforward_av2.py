"""DrivingForward zero-shot on AV2 -> single virtual-center ERP panorama.

Sweep #1 route. Loads nuScenes-pretrained DrivingForward depth_net + gs_net,
feeds an AV2 ring frame (7 cams -> 6 nuScenes slots by azimuth), predicts
per-pixel 3D Gaussians in the ego frame, then:
  (A) sanity: re-renders each real view (PSNR vs input);
  (B) GOAL: renders 6 virtual pinhole faces sharing the EGO optical center
      (zero inter-view parallax) -> cube -> single-center ERP panorama.

Color fix: images are fed as raw [0,1] (NO ImageNet norm) -- the repo transform
only does ToTensor + colorjitter. Bypasses dataset/DGP via a sys.modules stub.
Run in the `df` conda env on Colab.
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
for p in [str(REPO_DF), str(REPO_DF / "external" / "packnet_sfm"), str(REPO_DF / "external"), str(REPO_W2P / "code")]:
    sys.path.insert(0, p)

NUSC_SLOTS = ["CAM_FRONT", "CAM_FRONT_LEFT", "CAM_FRONT_RIGHT", "CAM_BACK_LEFT", "CAM_BACK_RIGHT", "CAM_BACK"]
SLOT_TARGET_AZ_DEG = {"CAM_FRONT": 0.0, "CAM_FRONT_LEFT": 55.0, "CAM_FRONT_RIGHT": -55.0,
                      "CAM_BACK_LEFT": 125.0, "CAM_BACK_RIGHT": -125.0, "CAM_BACK": 180.0}

# Cube faces in AV2 ego frame (x-fwd, y-left, z-up). Each: (name, right, down, forward) in ego.
CUBE_FACES = [
    ("front", (0, -1, 0), (0, 0, -1), (1, 0, 0)),
    ("back",  (0, 1, 0),  (0, 0, -1), (-1, 0, 0)),
    ("left",  (1, 0, 0),  (0, 0, -1), (0, 1, 0)),
    ("right", (-1, 0, 0), (0, 0, -1), (0, -1, 0)),
    ("up",    (0, -1, 0), (1, 0, 0),  (0, 0, 1)),
    ("down",  (0, -1, 0), (-1, 0, 0), (0, 0, -1)),
]


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
    new = {(k[7:] if k.startswith("module.") else k): v for k, v in sd.items()}
    miss, unexp = net.load_state_dict(new, strict=False)
    return len(new), len(miss), len(unexp)


def cam_azimuth_deg(T_ego_cam):
    fwd = T_ego_cam[:3, 2]
    return float(np.degrees(np.arctan2(fwd[1], fwd[0])))


def pick_6(av2_cams, calibs):
    az = {c: cam_azimuth_deg(calibs[c].T_ego_cam) for c in av2_cams}
    chosen, used = {}, set()
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
        img = frame.images[cam]
        h0, w0 = img.shape[:2]
        rs = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0  # [0,1] RGB, no norm
        colors.append(torch.from_numpy(rs.transpose(2, 0, 1)))
        K = frame.calibrations[cam].K.astype(np.float64).copy()
        K[0] *= (W / w0)
        K[1] *= (H / h0)
        Ks_native.append(K)
    color = torch.stack(colors, 0).unsqueeze(0).float().to(device)
    inputs = {("color", 0, 0): color, ("color_aug", 0, 0): color}
    for s in range(4):
        Ks, invKs = [], []
        for K in Ks_native:
            K4 = np.eye(4)
            Ksc = K.copy()
            Ksc[:2] /= (2 ** s)
            K4[:3, :3] = Ksc
            Ks.append(torch.from_numpy(K4))
            invKs.append(torch.from_numpy(np.linalg.inv(K4)))
        inputs[("K", s)] = torch.stack(Ks, 0).unsqueeze(0).float().to(device)
        inputs[("inv_K", s)] = torch.stack(invKs, 0).unsqueeze(0).float().to(device)
    extr, extr_inv = [], []
    for slot in NUSC_SLOTS:
        T = frame.calibrations[chosen[slot]].T_ego_cam.astype(np.float64)
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


def aggregate_gaussians(outputs, rearrange):
    xyz, rot, scale, opac, sh = [], [], [], [], []
    for c in range(6):
        o = outputs[("cam", c)]
        valid = o[("pts_valid", 0, 0)][0]
        xyz.append(o[("xyz", 0, 0)][0][valid].view(-1, 3))
        rot.append(o[("rot_maps", 0, 0)][0].permute(1, 2, 0).view(-1, 4)[valid])
        scale.append(o[("scale_maps", 0, 0)][0].permute(1, 2, 0).view(-1, 3)[valid])
        opac.append(o[("opacity_maps", 0, 0)][0].permute(1, 2, 0).view(-1, 1)[valid])
        sh_c = rearrange(o[("sh_maps", 0, 0)][0], "p srf r xyz d_sh -> (p srf r) d_sh xyz").contiguous()
        sh.append(sh_c[valid])
    return (torch.cat(xyz, 0), torch.cat(rot, 0), torch.cat(scale, 0), torch.cat(opac, 0), torch.cat(sh, 0))


def render_virtual_face(face, agg, size, render, getProjectionMatrix, focal2fov, device):
    _, r, dn, f = face
    R_cam2ego = np.stack([np.array(r, float), np.array(dn, float), np.array(f, float)], axis=1)  # cols = cam axes in ego
    R_ego2cam = R_cam2ego.T
    ego2cam = np.eye(4)
    ego2cam[:3, :3] = R_ego2cam  # t = 0 (virtual cam at ego origin)
    fx = fy = size / 2.0  # 90 deg FoV
    cx = cy = size / 2.0
    K = torch.tensor([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]], dtype=torch.float32, device=device)
    wvt = torch.from_numpy(ego2cam).float().to(device).transpose(0, 1)
    proj = getProjectionMatrix(0.01, 80.0, K, size, size).transpose(0, 1).to(device)
    full = wvt.unsqueeze(0).bmm(proj.unsqueeze(0)).squeeze(0)
    campos = wvt.inverse()[3, :3]
    xyz, rot, scale, opac, sh = agg
    img = render(novel_FovX=focal2fov(fx, size), novel_FovY=focal2fov(fy, size),
                 novel_height=size, novel_width=size,
                 novel_world_view_transform=wvt, novel_full_proj_transform=full,
                 novel_camera_center=campos, pts_xyz=xyz, pts_rgb=None,
                 rotations=rot, scales=scale, opacity=opac, shs=sh, bg_color=[0.0, 0.0, 0.0])
    return img.clamp(0, 1).permute(1, 2, 0).cpu().numpy(), R_ego2cam, fx, cx


def cube_to_erp(faces_rgb, He=1024, We=2048):
    # ego rays for ERP grid (repo convention)
    uu, vv = np.meshgrid(np.arange(We), np.arange(He))
    theta = np.pi - (uu + 0.5) / We * 2 * np.pi
    phi = np.pi / 2 - (vv + 0.5) / He * np.pi
    cphi = np.cos(phi)
    rays = np.stack([cphi * np.cos(theta), cphi * np.sin(theta), np.sin(phi)], -1)  # He,We,3
    erp = np.zeros((He, We, 3), np.float32)
    filled = np.zeros((He, We), bool)
    for img, R_ego2cam, fx, cx in faces_rgb:
        size = img.shape[0]
        c = rays @ R_ego2cam.T  # to cam coords
        cz = c[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            px = c[..., 0] / cz * fx + cx
            py = c[..., 1] / cz * fx + cx
        inface = (cz > 1e-6) & (px >= 0) & (px <= size - 1) & (py >= 0) & (py <= size - 1) & (~filled)
        ys, xs = np.where(inface)
        if ys.size:
            xi = np.clip(px[ys, xs].astype(np.int32), 0, size - 1)
            yi = np.clip(py[ys, xs].astype(np.int32), 0, size - 1)
            erp[ys, xs] = img[yi, xi]
            filled[ys, xs] = True
    return (erp * 255).astype(np.uint8)


def run(args):
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from network import DepthNetwork
    import types
    if "dataset" not in sys.modules:
        st = types.ModuleType("dataset"); st.construct_dataset = lambda *a, **k: None
        sys.modules["dataset"] = st
    from models.gaussian import GaussianNetwork, depth2pc, pts2render, focal2fov, getProjectionMatrix, rotate_sh
    from models.gaussian.gaussian_renderer import render
    from einops import rearrange

    device = "cuda"
    H, W = 352, 640
    cfg = load_cfg(REPO_DF / "configs/nuscenes/main.yaml")
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
        print(f"[case {short}:{anchor}] mapping={chosen}", flush=True)
        inputs = build_inputs(frame, chosen, H, W, device)
        with torch.no_grad():
            depth_feats = depth_net(inputs)
            outputs = {("cam", c): {} for c in range(6)}
            for c in range(6):
                outputs[("cam", c)].update(depth_feats[("cam", c)])
            for c in range(6):
                K0 = inputs[("K", 0)][:, c, ...]
                depth = to_depth(outputs[("cam", c)][("disp", 0)], K0, H, W)
                e2c = inputs["extrinsics_inv"][:, c, ...]
                outputs[("cam", c)][("depth", 0, 0)] = depth
                outputs[("cam", c)][("xyz", 0, 0)] = depth2pc(depth, e2c, K0)
                outputs[("cam", c)][("pts_valid", 0, 0)] = (depth != 0.0).view(1, -1)
                rot, scale, opac, sh = gs_net(inputs[("color", 0, 0)][:, c, ...], depth, outputs[("cam", c)][("img_feat", 0, 0)])
                c2w_rot = rearrange(inputs["extrinsics"][:, c, :3, :3], "k i j -> k () () () i j")
                sh = rotate_sh(sh, c2w_rot[..., None, :, :])
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

            # (A) sanity real-view re-render + PSNR
            panels, psnrs = [], {}
            for c in range(6):
                ren = pts2render(inputs, outputs, 6, c, 0, [0.0, 0.0, 0.0], "SF")[0].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
                inp = inputs[("color", 0, 0)][0, c].permute(1, 2, 0).cpu().numpy()
                mse = float(np.mean((ren - inp) ** 2)) + 1e-9
                psnrs[NUSC_SLOTS[c]] = round(-10 * np.log10(mse), 2)
                pair = np.ascontiguousarray(np.vstack([(inp * 255).astype(np.uint8), (ren * 255).astype(np.uint8)]))
                cv2.putText(pair, NUSC_SLOTS[c], (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                panels.append(pair)
            cv2.imwrite(str(out_root / f"{short}_a{anchor:03d}_realview_check.jpg"),
                        cv2.cvtColor(np.hstack(panels), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])

            # (B) GOAL: single virtual ego-center cubemap -> ERP
            agg = aggregate_gaussians(outputs, rearrange)
            faces = [render_virtual_face(fc, agg, 512, render, getProjectionMatrix, focal2fov, device) for fc in CUBE_FACES]
            erp = cube_to_erp(faces, 1024, 2048)
            cv2.imwrite(str(out_root / f"{short}_a{anchor:03d}_dfwd_ERP.jpg"),
                        cv2.cvtColor(erp, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
            facemont = np.hstack([(f[0] * 255).astype(np.uint8) for f in faces])
            cv2.imwrite(str(out_root / f"{short}_a{anchor:03d}_cube_faces.jpg"),
                        cv2.cvtColor(facemont, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
            n_gauss = int(agg[0].shape[0])
        print(f"  PSNR(real-view,color-fixed)={psnrs}  n_gaussians={n_gauss}", flush=True)
        print(f"  [saved] {short}_a{anchor:03d}_dfwd_ERP.jpg + realview + cube_faces", flush=True)
        results.append({"case": case, "psnr": psnrs, "n_gaussians": n_gauss})
    with open(out_root / "summary.json", "w") as f:
        json.dump(results, f, indent=2)
    print("[done]", json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", default="/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
    ap.add_argument("--weights", default="/content/DrivingForward/pretrained/weights_SF")
    ap.add_argument("--out-dir", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/dibr_drivingforward_av2_v2")
    ap.add_argument("--cases", nargs="+", default=["02a00399:0", "fbee355f:95", "0bae3b5e:30"])
    args = ap.parse_args()
    run(args)
