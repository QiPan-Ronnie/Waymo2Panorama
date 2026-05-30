"""Patch finetune_drivingforward_av2.py -> finetune_dfwd_heldout.py (#3 held-out-camera retrain).

Two surgical edits:
  (1) photometric loss -> HELD-OUT: before rendering camera ci, zero its pts_valid so ci is
      reconstructed from NEIGHBOURS only (faithfulness = the literal loss; render at ci's REAL
      pose -> no single-center warp). Add LPIPS for sharpness (kills the blur).
  (2) init an LPIPS(vgg) module after the nets load.
Warm-start + out-dir are set via CLI (--weights = the existing finetune ckpt).
"""
SRC = "/content/waymo2panorama/scripts/phase3/finetune_drivingforward_av2.py"
DST = "/content/waymo2panorama/scripts/phase3/finetune_dfwd_heldout.py"

src = open(SRC).read()

photo_old = '''        for c in view_ids:
            ren = pts2render(inputs, outputs, 6, int(c), 0, [0.0, 0.0, 0.0], "SF")
            inp = inputs[("color", 0, 0)][:, int(c), ...]
            photo_loss = photo_loss + torch.abs(ren - inp).mean()'''
photo_new = '''        for c in view_ids:
            ci = int(c)
            _saved = outputs[("cam", ci)][("pts_valid", 0, 0)]
            outputs[("cam", ci)][("pts_valid", 0, 0)] = torch.zeros_like(_saved)  # HELD-OUT: reconstruct cam ci from NEIGHBOURS only
            ren = pts2render(inputs, outputs, 6, ci, 0, [0.0, 0.0, 0.0], "SF")
            outputs[("cam", ci)][("pts_valid", 0, 0)] = _saved
            inp = inputs[("color", 0, 0)][:, ci, ...]
            r4 = ren.reshape(-1, 3, H, W).clamp(0, 1)
            i4 = inp.reshape(-1, 3, H, W).clamp(0, 1)
            photo_loss = photo_loss + torch.abs(r4 - i4).mean() + 0.8 * LPIPS_FN(r4 * 2 - 1, i4 * 2 - 1).mean()'''
assert photo_old in src, "photo block not found"
src = src.replace(photo_old, photo_new)

anchor = 'print("[load gs_net]", dfa._load_state(gs_net, Path(args.weights) / "gs_net.pth"), flush=True)'
ins = anchor + '''
    import lpips as _lp
    LPIPS_FN = _lp.LPIPS(net="vgg").to(device).eval()
    for _p in LPIPS_FN.parameters():
        _p.requires_grad = False
    print("[lpips] vgg ready", flush=True)'''
assert anchor in src, "gs_net anchor not found"
src = src.replace(anchor, ins, 1)

open(DST, "w").write(src)
print("patched ->", DST)
