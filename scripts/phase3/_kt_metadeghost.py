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
    rows=[np.pad(r,((0,0),(0,maxw-r.shape[1]),(0,0))) if r.shape[1]<maxw else r for r in rows]
    g=np.vstack(rows); g=cv2.resize(g,(g.shape[1]*2,g.shape[0]*2),interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT/(name+'_compare.jpg')),g,[cv2.IMWRITE_JPEG_QUALITY,95]); print('[saved]',OUT/(name+'_compare.jpg'))

# ============ Meta Surround360 NovelView deghost-softmax compositing ============
def meta_deghost_novel(d):
    wi=d['warp_i']; wj=d['warp_j']; shift=d['shift']; fij=d['fij']; fji=d['fji']
    colorDiff = np.mean(np.abs(wi-wj),axis=2)/255.0
    deghost = np.tanh(colorDiff*10.0)
    blendL = 1.0-shift; blendR = shift
    nL = np.sqrt(fij[...,0]**2+fij[...,1]**2)/W
    nR = np.sqrt(fji[...,0]**2+fji[...,1]**2)/W
    # softmax in log-space for numerical stability (exp args can be ~hundreds)
    aL = 10.0*blendL*(1.0+100.0*nL)
    aR = 10.0*blendR*(1.0+100.0*nR)
    m = np.maximum(aL,aR)
    expL = np.exp(aL-m); expR = np.exp(aR-m)
    den = expL+expR
    smL = expL/den; smR = expR/den
    wL = blendL*(1.0-deghost) + smL*deghost
    wR = blendR*(1.0-deghost) + smR*deghost
    s = wL+wR; s = np.maximum(s,1e-9)
    wL = wL/s; wR = wR/s
    novel = wi*wL[...,None] + wj*wR[...,None]
    return novel.astype(np.float32)

your_full = composite(meta_deghost_novel)
make_compare(your_full, "metadeghost")

# ============ Artifact metric ============
# For each crop: (a) fraction of band pixels with colorDiff>0.15 (potential ghost) BEFORE,
# (b) local gradient sharpness (mean |Laplacian|) of alpha-blend vs deghost result in the band region.
def gradient_sharpness(rgb, mask):
    g = cv2.cvtColor(np.clip(rgb,0,255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
    if mask is None or mask.sum()==0:
        return float(np.mean(np.abs(lap)))
    return float(np.mean(np.abs(lap[mask])))

# build a global band mask + global colorDiff (in canonical/un-rolled ERP frame) by accumulating each seam
band_global = np.zeros((H,W),bool)
colorDiff_global = np.zeros((H,W),np.float32)
for (i,j) in RING_PAIRS:
    d=seam_warps(i,j)
    if d is None: continue
    cd = np.mean(np.abs(d['warp_i']-d['warp_j']),axis=2)/255.0
    bm = d['band'].astype(bool)
    # roll back to canonical frame
    cd_c = np.roll(cd,-d['roll'],1); bm_c = np.roll(bm,-d['roll'],1)
    colorDiff_global = np.where(bm_c & (cd_c>colorDiff_global), cd_c, colorDiff_global)
    band_global |= bm_c

alpha_full = composite(alpha_novel)
print("=== ARTIFACT METRIC (Meta deghost-softmax) ===")
for (u0,u1,v0,v1,tag) in [(560,900,330,560,'graycar'),(1600,1960,360,600,'bmw')]:
    bm = band_global[v0:v1,u0:u1]
    cd = colorDiff_global[v0:v1,u0:u1]
    nband = int(bm.sum())
    if nband==0:
        print(f"[{tag}] no band pixels in crop"); continue
    frac_ghost = float((cd[bm]>0.15).mean())
    sharp_alpha = gradient_sharpness(alpha_full[v0:v1,u0:u1], bm)
    sharp_deg   = gradient_sharpness(your_full[v0:v1,u0:u1], bm)
    print(f"[{tag}] band_px={nband}  frac(colorDiff>0.15)={frac_ghost:.3f}  "
          f"sharpness_alpha={sharp_alpha:.2f}  sharpness_deghost={sharp_deg:.2f}  "
          f"delta_sharp={sharp_deg-sharp_alpha:+.2f}")
print("[done]")
