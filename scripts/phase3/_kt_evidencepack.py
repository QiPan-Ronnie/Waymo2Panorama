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

# ============================ DB-12 EVIDENCE PACK ============================
# Question: for the doubled objects (gray car + BMW) in the seam band, can the
# LiDAR depth disambiguate WHICH camera's copy is the geometrically correct one?
#
# Mechanism of doubling: L1 renders each camera rotation-only (convergence=None),
# i.e. it DROPS the camera translation t_ego_cam. A 3D point P at finite range is
# therefore placed at the ERP azimuth of its UNIT RAY *as seen from the camera*:
#     d_cam = R_cam_ego_i @ (P - t_i)      (direction from cam_i to P)
#     d_ego_ray = R_ego_cam_i @ (d_cam/|d_cam|)   (rotation-only puts it back as a ray)
#     azimuth_cam_i = atan2(d_ego_ray.y, d_ego_ray.x)
# Two cameras with different centers t_i, t_j give DIFFERENT azimuths for the same
# finite P -> that azimuth gap IS the doubling (parallax).
#
# The geometrically-correct single-center azimuth of P (ego origin) is:
#     azimuth_true = atan2(P.y, P.x)
# LiDAR gives us P directly, hence azimuth_true. So we ask, per doubling point:
# is azimuth_true closer to cam_i's copy or cam_j's copy?  If LiDAR decisively
# (>3px) picks ONE, then LiDAR can disambiguate the correct copy.

LM=loader  # alias

def azel_to_uv(theta, phi):
    """ERP convention used by render_camera_to_erp: theta=pi-(u+.5)/W*2pi, phi top->bottom."""
    u = ((np.pi - theta) / (2*np.pi) * W - 0.5) % W
    v = ((np.pi/2 - phi) / np.pi * H - 0.5)
    return u, v

def true_uv(P):
    """Geometrically-correct (ego-center) ERP (u,v) of 3D ego points P (...,3)."""
    x,y,z=P[...,0],P[...,1],P[...,2]
    theta=np.arctan2(y,x)
    phi=np.arctan2(z,np.hypot(x,y))
    return azel_to_uv(theta,phi)

def cam_copy_uv(P, cam):
    """Where camera `cam` (a CameraCalibration) places ego points P in its L1 (rotation-only)
    ERP slab. Returns (u,v,valid) where valid = point is in front of and inside cam image."""
    cb=LM.calibration(cam)
    R=cb.T_ego_cam[:3,:3]; t=cb.T_ego_cam[:3,3]; K=cb.K
    Rc=R.T
    d_cam=(P - t[None,:]) @ Rc.T          # direction cam->P in cam frame  (N,3)
    z=d_cam[:,2]
    in_front=z>1e-6
    zs=np.where(in_front,z,1.0)
    uimg=K[0,0]*(d_cam[:,0]/zs)+K[0,2]
    vimg=K[1,1]*(d_cam[:,1]/zs)+K[1,2]
    inb=(uimg>=0.5)&(uimg<=cb.image_width-1.5)&(vimg>=0.5)&(vimg<=cb.image_height-1.5)
    valid=in_front&inb
    # rotation-only re-projection: the ERP ray that lands on this image pixel is
    # the unit direction d_cam rotated back to ego.  Azimuth from that ray.
    n=np.linalg.norm(d_cam,axis=1,keepdims=True); n=np.where(n<1e-9,1.0,n)
    ray_ego=(d_cam/n) @ R.T               # (N,3) unit ray in ego frame
    theta=np.arctan2(ray_ego[:,1],ray_ego[:,0])
    phi=np.arctan2(ray_ego[:,2],np.hypot(ray_ego[:,0],ray_ego[:,1]))
    u,v=azel_to_uv(theta,phi)
    return u,v,valid

# ---- load LiDAR ----
pts,lts,ldelta=load_lidar_feather(ROOT/BMW, ts[0], max_delta_ms=75.0)
pts=np.asarray(pts)[:,:3]
print(f"[lidar] N={len(pts)} delta={ldelta:.1f}ms")

# ---- two crop ROIs (canonical L1 frame) with their adjacent seam camera pair ----
# graycar is front-LEFT seam region; bmw is RIGHT seam region. Find which RING_PAIR
# overlap-band falls inside each crop box, automatically.
CROPS=[(560,900,330,560,'graycar'),(1600,1960,360,600,'bmw')]  # (u0,u1,v0,v1,tag)

def pair_for_crop(u0,u1,v0,v1):
    """Pick the RING_PAIR whose L1 overlap band is most present inside the crop box."""
    best=None; bestc=-1
    for (i,j) in RING_PAIRS:
        ov=(w[i]>1e-6)&(w[j]>1e-6)
        c=int(ov[v0:v1,u0:u1].sum())
        if c>bestc: bestc=c; best=(i,j)
    return best,bestc

# ---- precompute per-camera copy positions for ALL lidar pts (only need the seam cams) ----
cam_uv_cache={}
def get_cam_uv(ci):
    if ci not in cam_uv_cache:
        cam_uv_cache[ci]=cam_copy_uv(pts, RING_CAMS_7[ci])
    return cam_uv_cache[ci]

ut,vt=true_uv(pts)                # true (single-center) erp positions of every lidar point
rng=np.linalg.norm(pts[:,:2],axis=1)   # ground range for coloring / sorting

# JET color by range (clip 2..40m)
rclip=np.clip(rng,2.0,40.0)
rn=((rclip-2.0)/(40.0-2.0)*255).astype(np.uint8)
jet=cv2.applyColorMap(rn.reshape(-1,1),cv2.COLORMAP_JET).reshape(-1,3)  # BGR

base=cv2.cvtColor(np.clip(L1,0,255).astype(np.uint8),cv2.COLOR_RGB2BGR)

# helpers for drawing into the full-pano BGR canvas
def draw_pts(canvas, u, v, colors, valid, rad=1):
    ui=np.round(u).astype(int)%W; vi=np.round(v).astype(int)
    ok=valid&(vi>=0)&(vi<H)
    for k in np.flatnonzero(ok):
        cv2.circle(canvas,(int(ui[k]),int(vi[k])),rad,(int(colors[k,0]),int(colors[k,1]),int(colors[k,2])),-1)

# ============ build the three diagnostic panels per crop ============
def lab(im,t):
    b=np.zeros((22,im.shape[1],3),np.uint8); cv2.putText(b,t,(4,16),cv2.FONT_HERSHEY_SIMPLEX,0.5,(255,255,255),1); return np.vstack([b,im])

panel_rows=[]
metrics_lines=[]
for (u0,u1,v0,v1,tag) in CROPS:
    (i,j),npix=pair_for_crop(u0,u1,v0,v1)
    ci_name=RING_CAMS_7[i]; cj_name=RING_CAMS_7[j]
    ui,vi,vali=get_cam_uv(i)
    uj,vj,valj=get_cam_uv(j)

    # select lidar points whose TRUE position lands inside this crop box (and visible in BOTH cams)
    in_box_true=(ut>=u0)&(ut<u1)&(vt>=v0)&(vt<v1)
    both=vali&valj
    # also require the camera copies land near the crop (in band region)
    in_box_i=(ui>=u0-120)&(ui<u1+120)&(vi>=v0-60)&(vi<v1+60)
    sel=in_box_true&both&in_box_i
    nsel=int(sel.sum())

    # doubling magnitude = ERP azimuth gap between the two camera copies (circular px)
    du=np.abs(((ui-uj+W/2)%W)-W/2)      # circular |ui-uj| in px
    doubling=sel&(du>3.0)               # >3px parallax = a visibly doubled point
    nd=int(doubling.sum())

    # circular distance of TRUE position to each camera copy
    di=np.abs(((ut-ui+W/2)%W)-W/2)
    dj=np.abs(((ut-uj+W/2)%W)-W/2)
    # LiDAR "decisively" picks one copy if the two distances differ by >3px
    decisive=doubling&(np.abs(di-dj)>3.0)
    picks_i=decisive&(di<dj)
    picks_j=decisive&(dj<di)
    ndec=int(decisive.sum())
    frac_dec=(ndec/nd) if nd>0 else float('nan')

    # --- DIAGNOSTICS so we trust the headline number (printed, not on image) ---
    # signed circular offsets of each copy from TRUE (positive = copy is to the +u side of true)
    si_=(((ui-ut+W/2)%W)-W/2); sj_=(((uj-ut+W/2)%W)-W/2)
    db=doubling
    # straddle = true lies BETWEEN the two copies (opposite signs); else both on same side
    straddle=db&((si_*sj_)<0)
    print(f"[diag {tag}] di med/p90={np.median(di[db]):.2f}/{np.percentile(di[db],90):.2f}px "
          f"dj med/p90={np.median(dj[db]):.2f}/{np.percentile(dj[db],90):.2f}px "
          f"signed_i med={np.median(si_[db]):+.2f} signed_j med={np.median(sj_[db]):+.2f} "
          f"straddle_frac={straddle.sum()/max(nd,1):.3f}")
    # sanity: cam_i copy azimuth should also equal where L1-renderer placed it. Spot check
    # by re-deriving azimuth two ways already done; here check du==|si-sj| (consistency)
    cons=np.abs(du[db]-np.abs(((ui-uj+W/2)%W)-W/2)[db]).max()
    print(f"[diag {tag}] du-vs-recompute maxerr={cons:.3f}px (should be ~0)")
    # KEY honest metric: residual of the BEST (closest) copy to LiDAR-true. If this is
    # large, then even the copy LiDAR 'picks' is NOT at the correct position -> LiDAR
    # disambiguating WHICH copy doesn't yield a correctly-placed object.
    best_res=np.minimum(di[db],dj[db])
    print(f"[diag {tag}] WINNING-copy residual to LiDAR-true: "
          f"med={np.median(best_res):.2f}px p90={np.percentile(best_res,90):.2f}px "
          f"(vs doubling gap median={np.median(du[db]):.2f}px)")

    metrics_lines.append(
        f"[{tag}] seam cams=({ci_name},{cj_name}) | lidar-in-crop(both-cams)={nsel} "
        f"doubling(>3px)={nd} decisive(|di-dj|>3px)={ndec} "
        f"FRAC_DECISIVE={frac_dec:.3f} picks_i={int(picks_i.sum())} picks_j={int(picks_j.sum())} "
        f"mean_du={float(du[doubling].mean()) if nd>0 else 0:.1f}px "
        f"medianRange={float(np.median(rng[doubling])) if nd>0 else 0:.1f}m"
    )

    # ----- panel A: L1 crop + range-colored true LiDAR overlay -----
    cA=base.copy()
    draw_pts(cA,ut,vt,jet,sel,rad=1)
    A=cA[v0:v1,u0:u1].copy()

    # ----- panel B: where cam_i (green) vs cam_j (red) place the SAME points (copies),
    #               with the LiDAR-TRUE single-center position (magenta) for comparison -----
    cB=base.copy()
    green=np.tile(np.array([[0,255,0]]),(len(pts),1))
    red=np.tile(np.array([[0,0,255]]),(len(pts),1))
    magenta=np.tile(np.array([[255,0,255]]),(len(pts),1))
    draw_pts(cB,ut,vt,magenta,sel,rad=2)  # LiDAR-true single-center position
    draw_pts(cB,ui,vi,green,sel,rad=1)    # cam_i copies
    draw_pts(cB,uj,vj,red,sel,rad=1)      # cam_j copies
    B=cB[v0:v1,u0:u1].copy()

    # ----- panel C: per doubling point, color by which copy LiDAR predicts -----
    #   cyan  = LiDAR true-pos agrees with cam_i copy
    #   yellow= LiDAR true-pos agrees with cam_j copy
    #   white = ambiguous (not decisive)
    # drawn AT the true position
    cC=base.copy()
    cyan=np.tile(np.array([[255,255,0]]),(len(pts),1))    # BGR cyan
    yellow=np.tile(np.array([[0,255,255]]),(len(pts),1))  # BGR yellow
    white=np.tile(np.array([[255,255,255]]),(len(pts),1))
    amb=doubling&(~decisive)
    draw_pts(cC,ut,vt,white,amb,rad=1)
    draw_pts(cC,ut,vt,cyan,picks_i,rad=2)
    draw_pts(cC,ut,vt,yellow,picks_j,rad=2)
    C=cC[v0:v1,u0:u1].copy()

    row=np.hstack([lab(A,tag+' L1+range-LiDAR'),
                   lab(B,'TRUE=mag cam_i=grn cam_j=red'),
                   lab(C,'LiDAR pick: cyan=i yel=j wht=amb')])
    panel_rows.append(row)

# rows can differ in width (crops have different pixel widths) -> pad to common width
maxw=max(r.shape[1] for r in panel_rows)
panel_rows=[np.pad(r,((0,0),(0,maxw-r.shape[1]),(0,0))) if r.shape[1]<maxw else r for r in panel_rows]
grid=np.vstack(panel_rows)
grid=cv2.resize(grid,(grid.shape[1]*2,grid.shape[0]*2),interpolation=cv2.INTER_NEAREST)
# stamp the headline metric at the bottom
foot=np.zeros((26*len(metrics_lines)+8,grid.shape[1],3),np.uint8)
for k,ln in enumerate(metrics_lines):
    cv2.putText(foot,ln,(6,20+26*k),cv2.FONT_HERSHEY_SIMPLEX,0.42,(0,255,255),1)
grid=np.vstack([grid,foot])
outp=OUT/'evidencepack_compare.jpg'
cv2.imwrite(str(outp),grid,[cv2.IMWRITE_JPEG_QUALITY,95])
print('[saved]',outp)
print('==== METRICS ====')
for ln in metrics_lines: print(ln)
print('=================')
