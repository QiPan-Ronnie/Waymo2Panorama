import sys, cv2, numpy as np
from pathlib import Path
REPO = Path("/content/waymo2panorama"); sys.path.insert(0, str(REPO/"code")); sys.path.insert(0, str(REPO/"scripts/phase3"))
sys.stdout.reconfigure(encoding="utf-8")
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band
from run_a1_streetview_pipeline import _make_dis, _fb_consistency, _circular_center_col
from run_a0_plane_dibr_probe import load_lidar_feather
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT  = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/killtest"); OUT.mkdir(parents=True, exist_ok=True)
BMW="02a00399-3857-444e-8db3-a8f58489c394"; H,W=1024,2048
loader=AV2RingLoader(ROOT/BMW); ts=loader.anchor_timestamps_ns(); frame=loader.load_synced_frame(ts[0])
l1=[]; w=[]
for cam in RING_CAMS_7:
    cb=frame.calibrations[cam]
    r,_a,wt=render_camera_to_erp(frame.images[cam],cb.K,cb.T_ego_cam,erp_hw=(H,W),convergence_distance_m=None)
    l1.append(r); w.append(wt)
L1=hard_select(l1,w)
dis=_make_dis()
def seam_warps(i,j):
    overlap=(w[i]>1e-6)&(w[j]>1e-6)
    if int(overlap.sum())<200: return None
    cc=_circular_center_col(overlap,W); roll=(W//2-cc) if cc is not None else 0
    si=np.roll(l1[i],roll,1); sj=np.roll(l1[j],roll,1); wir=np.roll(w[i],roll,1); wjr=np.roll(w[j],roll,1)
    ov=(wir>1e-6)&(wjr>1e-6)
    band,signed=build_voronoi_seam_band(wir.astype(np.float32),wjr.astype(np.float32),band_half_width=90,threshold=1e-6)
    gi=cv2.cvtColor(np.clip(si,0,255).astype(np.uint8),cv2.COLOR_RGB2GRAY); gj=cv2.cvtColor(np.clip(sj,0,255).astype(np.uint8),cv2.COLOR_RGB2GRAY)
    kov=cv2.dilate(ov.astype(np.uint8),cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(9,9))).astype(bool)
    gi=np.where(kov,gi,0).astype(np.uint8); gj=np.where(kov,gj,0).astype(np.uint8)
    fij=dis.calc(gi,gj,None); fji=dis.calc(gj,gi,None)
    for fl in (fij,fji):
        fl[...,0]=cv2.GaussianBlur(fl[...,0],(0,0),1.5); fl[...,1]=cv2.GaussianBlur(fl[...,1],(0,0),1.5)
    np.clip(fij,-60,60,out=fij); np.clip(fji,-60,60,out=fji)
    shift=np.zeros((H,W),np.float32); s=wir+wjr; m=ov
    shift[m]=(wjr[m]/np.maximum(s[m],1e-9)).astype(np.float32)
    xx,yy=np.meshgrid(np.arange(W),np.arange(H)); xx=xx.astype(np.float32); yy=yy.astype(np.float32)
    wi_img=cv2.remap(si.astype(np.float32),(xx-shift*fij[...,0]).astype(np.float32),(yy-shift*fij[...,1]).astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    wj_img=cv2.remap(sj.astype(np.float32),(xx-(1-shift)*fji[...,0]).astype(np.float32),(yy-(1-shift)*fji[...,1]).astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    return dict(roll=roll,si=si,sj=sj,wir=wir,wjr=wjr,band=band,signed=signed,shift=shift,fij=fij,fji=fji,warp_i=wi_img,warp_j=wj_img,overlap=ov)
def composite(novel_fn):
    out=L1.astype(np.float32).copy()
    for (i,j) in RING_PAIRS:
        d=seam_warps(i,j)
        if d is None: continue
        novel=novel_fn(d)
        dd=np.clip(np.abs(d['signed'])/90.0,0,1); feather=np.where(d['band'],0.5*(1+np.cos(np.pi*dd)),0).astype(np.float32)
        nov=np.roll(novel,-d['roll'],1); fe=np.roll(feather,-d['roll'],1)
        out=out*(1-fe[...,None])+nov*fe[...,None]
    return np.clip(out,0,255).astype(np.uint8)
def lab(im,t):
    b=np.zeros((22,im.shape[1],3),np.uint8); cv2.putText(b,t,(4,16),cv2.FONT_HERSHEY_SIMPLEX,0.5,(255,255,255),1); return np.vstack([b,im])
def alpha_novel(d):
    a=d['shift'][...,None]; return d['warp_i']*(1-a)+d['warp_j']*a
def make_compare(yours_full, name):
    alpha_full=composite(alpha_novel)
    rows=[]
    for (u0,u1,v0,v1,tag) in [(560,900,330,560,'graycar'),(1600,1960,360,600,'bmw')]:
        def cr(im): return cv2.cvtColor(im[v0:v1,u0:u1],cv2.COLOR_RGB2BGR)
        rows.append(np.hstack([lab(cr(L1),'L1 '+tag),lab(cr(alpha_full),'alpha-blend'),lab(cr(yours_full),name)]))
    maxw=max(r.shape[1] for r in rows)
    rows=[np.pad(r,((0,0),(0,maxw-r.shape[1]),(0,0)),mode='constant') for r in rows]
    g=np.vstack(rows); g=cv2.resize(g,(g.shape[1]*2,g.shape[0]*2),interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT/(name+'_compare.jpg')),g,[cv2.IMWRITE_JPEG_QUALITY,95]); print('[saved]',OUT/(name+'_compare.jpg'))

# ======================= MY METHOD: Jump disparity-ordered OVER =======================
K_INTERVAL = 2.0  # px interval for "same surface" test

def depthover_novel(d):
    # per-pixel disparity proxy = flow magnitude (inverse-depth proxy)
    disp_i = np.sqrt(d['fij'][...,0]**2 + d['fij'][...,1]**2).astype(np.float32)
    disp_j = np.sqrt(d['fji'][...,0]**2 + d['fji'][...,1]**2).astype(np.float32)
    a = d['shift'][...,None]
    blend = d['warp_i']*(1-a) + d['warp_j']*a
    # nearer = larger disparity occludes (OVER)
    nearer = np.where((disp_i >= disp_j)[...,None], d['warp_i'], d['warp_j'])
    same_surface = (np.abs(disp_i - disp_j) < K_INTERVAL)
    novel = np.where(same_surface[...,None], blend, nearer).astype(np.float32)
    return novel

# ---- diagnostics: per-crop occlusion fraction + which camera won on car ----
# crops are in UNROLLED (canonical L1) frame. We accumulate per-seam classification maps
# into a full-pano canvas (rolled back), then sample the crops.
def diag():
    # full-pano maps in canonical (unrolled) frame
    band_canvas   = np.zeros((H,W), np.bool_)
    diff_canvas   = np.zeros((H,W), np.bool_)   # different-surface (occlusion) within band
    iwin_canvas   = np.zeros((H,W), np.bool_)   # nearer fragment is cam i (disp_i>=disp_j)
    cov_canvas    = np.zeros((H,W), np.bool_)   # band-covered (for which-cam denom)
    for (i,j) in RING_PAIRS:
        d=seam_warps(i,j)
        if d is None: continue
        disp_i = np.sqrt(d['fij'][...,0]**2 + d['fij'][...,1]**2).astype(np.float32)
        disp_j = np.sqrt(d['fji'][...,0]**2 + d['fji'][...,1]**2).astype(np.float32)
        band = d['band'].astype(bool)
        diff = band & (np.abs(disp_i - disp_j) >= K_INTERVAL)
        iwin = band & (disp_i >= disp_j)
        # roll back to canonical frame
        band_r = np.roll(band, -d['roll'], 1)
        diff_r = np.roll(diff, -d['roll'], 1)
        iwin_r = np.roll(iwin, -d['roll'], 1)
        # store cam id of i for crops; we encode "i won" as boolean per seam.
        # To know which actual camera, also store seam id map.
        band_canvas |= band_r
        diff_canvas |= diff_r
        iwin_canvas |= iwin_r
        cov_canvas  |= band_r
    return band_canvas, diff_canvas, iwin_canvas

# Build the depth-over full pano
yours_full = composite(depthover_novel)
alpha_full_metric = composite(alpha_novel)
make_compare(yours_full, "depthover")

# Artifact metric: per-crop mean abs RGB diff between depth-over and alpha-blend (where they disagree),
# plus a high-freq (gradient-energy) proxy comparing both to L1 (lower ghost energy is better).
def grad_energy(img):
    g=cv2.cvtColor(np.clip(img,0,255).astype(np.uint8),cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx=cv2.Sobel(g,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(g,cv2.CV_32F,0,1,ksize=3)
    return np.sqrt(gx*gx+gy*gy)
print("\n=== Artifact metric per crop (depthover vs alpha) ===")
for (u0,u1,v0,v1,tag) in [(560,900,330,560,'graycar'),(1600,1960,360,600,'bmw')]:
    a=alpha_full_metric[v0:v1,u0:u1].astype(np.float32)
    d2=yours_full[v0:v1,u0:u1].astype(np.float32)
    diff=np.abs(a-d2).mean()
    chg=(np.abs(a-d2).sum(2)>10).mean()  # fraction of pixels that changed vs alpha
    ge_a=grad_energy(a).mean(); ge_d=grad_energy(d2).mean()
    print(f"[{tag}] mean|depthover-alpha|={diff:.2f}  px_changed_frac={chg:.3f}  gradE(alpha)={ge_a:.1f}  gradE(depthover)={ge_d:.1f}  (lower gradE ~ less double-edge ghost)")

# Diagnostics print
band_canvas, diff_canvas, iwin_canvas = diag()
crops = [(560,900,330,560,'graycar'),(1600,1960,360,600,'bmw')]
print("\n=== Jump disparity-ordered OVER diagnostics (k=%.1f px) ===" % K_INTERVAL)
for (u0,u1,v0,v1,tag) in crops:
    bsel = band_canvas[v0:v1,u0:u1]
    dsel = diff_canvas[v0:v1,u0:u1]
    isel = iwin_canvas[v0:v1,u0:u1]
    nb = int(bsel.sum())
    nd = int(dsel.sum())
    frac = (nd/nb) if nb>0 else 0.0
    # which camera won among band pixels: fraction where cam-i (lower index of the seam pair) is nearer
    ni = int((isel & bsel).sum())
    iwin_frac = (ni/nb) if nb>0 else 0.0
    print(f"[{tag}] band_px={nb}  different-surface(occlusion)_frac={frac:.3f}  cam-i-nearer_frac={iwin_frac:.3f}  (cam-i = lower-index of seam pair; >0.5 means left/earlier camera occludes)")

# Identify which seam(s) overlap each crop and report the actual camera names involved
print("\n=== seam->crop overlap + camera identity ===")
for (u0,u1,v0,v1,tag) in crops:
    print(f"[{tag}] crop u[{u0}:{u1}] v[{v0}:{v1}]")
    for (i,j) in RING_PAIRS:
        d=seam_warps(i,j)
        if d is None: continue
        band_r = np.roll(d['band'].astype(bool), -d['roll'], 1)
        bcount = int(band_r[v0:v1,u0:u1].sum())
        if bcount > 50:
            disp_i = np.sqrt(d['fij'][...,0]**2 + d['fij'][...,1]**2).astype(np.float32)
            disp_j = np.sqrt(d['fji'][...,0]**2 + d['fji'][...,1]**2).astype(np.float32)
            iwin = (disp_i >= disp_j)
            iwin_r = np.roll(iwin, -d['roll'], 1)
            band_only = band_r[v0:v1,u0:u1]
            iw = int((iwin_r[v0:v1,u0:u1] & band_only).sum())
            tot = int(band_only.sum())
            winner = RING_CAMS_7[i] if iw>tot/2 else RING_CAMS_7[j]
            print(f"   seam ({RING_CAMS_7[i]} | {RING_CAMS_7[j]})  band_px_in_crop={bcount}  cam-i-nearer_frac={iw/max(tot,1):.3f}  -> WINNER(majority)={winner}")
print("\n[done]")
