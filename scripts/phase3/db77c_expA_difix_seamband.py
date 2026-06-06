"""DB-77C EXP-A: Difix3D+ zero-shot seam-band refine of the A1 mosaic (A100).

opus strategy audit TOP-1: the only untested path is a STRONG-CONDITIONED discriminative
single-step refiner on the already-aligned mosaic (vs DiT360 pure generative outpaint that
invents cars). Difix (nvidia/difix, CVPR2025 Oral, single-step img2img, training set includes
an in-house real-driving 3-camera rig ~ our ring overlap) refines the A1 mosaic's SEAM-BAND
512 patches only (NOT the whole ERP — Difix is perspective-trained). Edits confined to the
safe band (seam_band ∩ ~abstain ∩ ~protected); near-field ghost zones stay ABSTAIN.

Object-consistency GATE (the leash): YOLO instance count in the band must NOT increase
(no net-new car/person/sign). Kill: invents objects, smears single-source structure, or no gain.

One bounded A100 /status + /exec. Runtime secret from non-repo file; remote gets no token; scan 0.
Uploads A1 + masks to Drive first (post /write), remote reads them.
"""
from __future__ import annotations
import argparse, base64, json, time, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, rel, safe_status, sanitize, secret_hits

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db77c_expA_difix"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db77c_expA_difix"
REMOTE_IN = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db77c_inputs"
P_RESULT = REMOTE_OUT + "/EXPA_remote_result.json"

UPLOADS = {
    "A1_base.png": "deliverables/gpt_pro_sources/01_A1_view_none_bmw_2048x1024.png",
    "mask_seam_band.png": "deliverables/db77c_leashed_seam/mask_seam_band.png",
    "mask_abstain.png": "deliverables/db77c_leashed_seam/mask_abstain.png",
    "mask_protected.png": "deliverables/db77c_leashed_seam/mask_protected.png",
}
FETCH = {
    "summary": ("EXPA_summary.json", 16), "remote_result": ("EXPA_remote_result.json", 16),
    "board": ("EXPA_board.jpg", 70), "roi": ("EXPA_roi_sheet.jpg", 70),
    "difix_full": ("A1_difix_full.png", 40), "gate": ("EXPA_object_gate.json", 16),
}


def remote_py() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np
REMOTE_OUT = pathlib.Path("__REMOTE_OUT__"); REMOTE_IN = pathlib.Path("__REMOTE_IN__"); RESULT = pathlib.Path("__P_RESULT__")
W, H = 2048, 1024
MARKED_ROIS = {"left_road":(250,515,460,715),"lower_center [ABSTAIN]":(740,595,1035,745),"center_lane":(1030,515,1325,735),"right_curb [ABSTAIN]":(1300,500,1575,760)}
OUT = {"phase":"db77c_expA_difix_seamband","scope":{"presentation_render":True,"source_faithful":False,"single_step":True,"band_only":True,"red_promotion":False},"started":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}

def io(n):
    try: __import__(n); return True
    except Exception: return False

def run(cmd,t=900):
    p=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=t,check=False); return p.returncode,p.stdout[-800:]

try:
    import cv2
    REMOTE_OUT.mkdir(parents=True,exist_ok=True)
    # deps
    deps={}
    # Difix3D requires PINNED diffusers==0.25.1 (its pipeline_difix uses FromOriginalVAEMixin, removed in new diffusers)
    deps["pin"]=run([sys.executable,"-m","pip","install","-q","diffusers==0.25.1","huggingface-hub==0.25.1","transformers==4.38.0","peft==0.9.0"],t=1200)[0]
    if not io("ultralytics"): deps["ultralytics"]=run([sys.executable,"-m","pip","install","-q","ultralytics"])[0]
    run(["bash","-lc","rm -rf /content/Difix3D"])
    deps["clone"]=run(["git","clone","--depth","1","https://github.com/nv-tlabs/Difix3D","/content/Difix3D"])[0]
    import torch
    OUT["cuda"]=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    a1=cv2.cvtColor(cv2.imread(str(REMOTE_IN/"A1_base.png")),cv2.COLOR_BGR2RGB)
    seam=cv2.imread(str(REMOTE_IN/"mask_seam_band.png"),0)>127
    abst=cv2.imread(str(REMOTE_IN/"mask_abstain.png"),0)>127
    prot=cv2.imread(str(REMOTE_IN/"mask_protected.png"),0)>127
    safe=seam&(~abst)&(~prot)
    OUT["safe_band_frac"]=float(safe.mean())
    # ---- load Difix (probe API: diffusers DifixPipeline / HF custom / repo) ----
    import diffusers as _df; OUT["diffusers_version"]=_df.__version__
    sys.path.insert(0,"/content/Difix3D"); sys.path.insert(0,"/content/Difix3D/src")
    pipe=None; api=None
    for modpath in ["pipeline_difix","src.pipeline_difix"]:
        try:
            mod=__import__(modpath,fromlist=["DifixPipeline"]); DP=getattr(mod,"DifixPipeline")
            pipe=DP.from_pretrained("nvidia/difix",trust_remote_code=True,torch_dtype=torch.bfloat16); api=f"repo.{modpath}@diffusers0.25.1"; break
        except Exception as ex:
            OUT["api_err_"+modpath.replace(".","_")]=str(ex)[:400]
    OUT["difix_api"]=api
    if pipe is None:
        OUT["status"]="difix_load_failed"; raise RuntimeError("Difix pipeline could not be loaded; see api_err_*")
    pipe=pipe.to("cuda")
    try: pipe.set_progress_bar_config(disable=True)
    except Exception: pass

    def difix(img_rgb):
        from PIL import Image
        pil=Image.fromarray(img_rgb.astype(np.uint8))
        for kw in [dict(prompt="remove degradation",image=pil,num_inference_steps=1,timesteps=[199],guidance_scale=0.0),
                   dict(image=pil,num_inference_steps=1,timesteps=[199]),
                   dict(prompt="",image=pil,num_inference_steps=1)]:
            try:
                o=pipe(**kw); return np.asarray(o.images[0].convert("RGB").resize((img_rgb.shape[1],img_rgb.shape[0]))),kw
            except Exception as ex:
                last=str(ex)[:200]
        raise RuntimeError("difix call failed: "+last)

    # single-patch smoke first (verify ERP patch runs)
    ys,xs=np.where(safe)
    if ys.size==0: OUT["status"]="no_safe_band"; raise RuntimeError("empty safe band")
    cy,cx=int(ys.mean()),int(xs.mean())
    P=512; y0=max(0,min(H-P,cy-P//2)); x0=max(0,min(W-P,cx-P//2))
    smoke,used_kw=difix(a1[y0:y0+P,x0:x0+P])
    OUT["difix_call_kwargs"]={k:(str(v)[:40] if k!="image" else "PIL") for k,v in used_kw.items()}
    OUT["smoke_ok"]=True

    # full seam-band refine: slide 512 patches over the safe-band bbox, refine, composite ONLY on safe band (feathered)
    a1f=a1.astype(np.float32); out=np.zeros((H,W,3),np.float32); wacc=np.zeros((H,W),np.float32)
    by0,by1,bx0,bx1=ys.min(),ys.max(),xs.min(),xs.max()
    step=384; n_patch=0
    for py in range(max(0,by0-64),min(H,by1+64),step):
        for px in range(max(0,bx0-64),min(W,bx1+64),step):
            yy0=max(0,min(H-P,py)); xx0=max(0,min(W-P,px))
            sb=safe[yy0:yy0+P,xx0:xx0+P]
            if sb.sum()<200: continue
            ref,_=difix(a1[yy0:yy0+P,xx0:xx0+P]); n_patch+=1
            fw=cv2.GaussianBlur(sb.astype(np.float32),(0,0),24)  # feather inside band
            out[yy0:yy0+P,xx0:xx0+P]+=fw[...,None]*ref.astype(np.float32); wacc[yy0:yy0+P,xx0:xx0+P]+=fw
    m=wacc>1e-3; a1_difix=a1f.copy()
    a1_difix[m]=out[m]/wacc[m][...,None]   # weighted-average of refined patches, band only
    a1_difix[~safe]=a1f[~safe]             # hard guarantee: only the safe band changes
    a1_difix=np.clip(a1_difix,0,255).astype(np.uint8)
    OUT["n_patches"]=n_patch; OUT["changed_in_band_px"]=int((np.abs(a1_difix.astype(np.int16)-a1.astype(np.int16)).max(-1)>2).sum()); OUT["changed_outside_band_px"]=int(((np.abs(a1_difix.astype(np.int16)-a1.astype(np.int16)).max(-1)>2)&(~safe)).sum())
    cv2.imwrite(str(REMOTE_OUT/"A1_difix_full.png"),cv2.cvtColor(a1_difix,cv2.COLOR_RGB2BGR))

    # ---- object-consistency GATE (YOLO instance diff in the safe band) ----
    gate={}
    try:
        from ultralytics import YOLO
        y=YOLO("yolov8x.pt")
        def objs(im):
            r=y(im,verbose=False,conf=0.3)[0]; out=[]
            for b,c in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.cls.cpu().numpy()):
                out.append((int(c),float((b[0]+b[2])/2),float((b[1]+b[3])/2)))
            return out
        o0=objs(a1); o1=objs(a1_difix)
        # net-new objects whose center is in the safe band
        def inband(o): return [t for t in o if safe[min(H-1,int(t[2])),min(W-1,int(t[1]))]]
        gate={"n_obj_a1":len(o0),"n_obj_difix":len(o1),"n_obj_a1_inband":len(inband(o0)),"n_obj_difix_inband":len(inband(o1)),
              "net_new_inband":max(0,len(inband(o1))-len(inband(o0)))}
    except Exception as ex:
        gate={"yolo_err":str(ex)[:200]}
    OUT["object_gate"]=gate
    (REMOTE_OUT/"EXPA_object_gate.json").write_text(json.dumps(gate,indent=2))

    # ---- boards ----
    from PIL import Image,ImageDraw,ImageFont
    try: f=ImageFont.truetype("DejaVuSans.ttf",15)
    except Exception: f=ImageFont.load_default()
    def band_(rgb):
        o=rgb.astype(np.float32).copy(); o[...,0][safe]=0.45*o[...,0][safe]+0.55*255; return np.clip(o,0,255).astype(np.uint8)
    cols=[("A1 base",a1),("A1 + Difix seam-band refine",a1_difix),("safe-band (red)",band_(a1))]
    def save_rgb(p,a,q=92): cv2.imwrite(str(p),cv2.cvtColor(np.clip(a,0,255).astype(np.uint8),cv2.COLOR_RGB2BGR),[int(cv2.IMWRITE_JPEG_QUALITY),q] if str(p).endswith("jpg") else [])
    # roi sheet
    rows=[]
    for nm,(x0r,y0r,x1r,y1r) in MARKED_ROIS.items():
        strip=np.concatenate([a1[y0r:y1r,x0r:x1r],a1_difix[y0r:y1r,x0r:x1r]],1)
        strip=cv2.resize(strip,(1000,int(strip.shape[0]*1000/strip.shape[1]))); rows.append((nm,strip))
    sh_w=max(r[1].shape[1] for r in rows); sheet=Image.new("RGB",(sh_w,sum(r[1].shape[0]+22 for r in rows)+24),(12,12,16)); d=ImageDraw.Draw(sheet)
    d.text((6,4),"EXP-A ROI: A1 | A1+Difix(seam-band)  -- did the faint seam reduce w/o new objects?",(235,235,245),font=f); yo=24
    for nm,st in rows:
        bar=Image.new("RGB",(sh_w,22),(22,22,30)); ImageDraw.Draw(bar).text((6,3),nm,(220,220,235),font=f); sheet.paste(bar,(0,yo)); yo+=22; sheet.paste(Image.fromarray(st),(0,yo)); yo+=st.shape[0]
    sheet.save(REMOTE_OUT/"EXPA_roi_sheet.jpg",quality=92)
    # full board
    tiles=[]
    for t,im in cols:
        I=Image.fromarray(im).resize((900,450)); bar=Image.new("RGB",(900,24),(15,15,22)); ImageDraw.Draw(bar).text((6,4),t,(235,235,245),font=f)
        o=Image.new("RGB",(900,474)); o.paste(bar,(0,0)); o.paste(I,(0,24)); tiles.append(o)
    bd=Image.new("RGB",(900,474*3+60),(10,10,14)); dd=ImageDraw.Draw(bd)
    dd.text((8,6),f"EXP-A Difix seam-band refine  api={api} patches={OUT.get('n_patches')} out_of_band_changed={OUT.get('changed_outside_band_px')} net_new_obj_inband={gate.get('net_new_inband')}",(240,240,250),font=f)
    yo=40
    for o in tiles: bd.paste(o,(0,yo)); yo+=o.height
    bd.save(REMOTE_OUT/"EXPA_board.jpg",quality=90)

    summary={"status":"db77c_expA_complete","difix_api":api,"safe_band_frac":OUT["safe_band_frac"],"n_patches":OUT.get("n_patches"),
             "changed_outside_band_px":OUT.get("changed_outside_band_px"),"object_gate":gate,"requires_vision":True}
    (REMOTE_OUT/"EXPA_summary.json").write_text(json.dumps(summary,indent=2)); OUT["status"]="db77c_expA_completed"; OUT["summary"]=summary
except Exception as exc:
    OUT["status"]=OUT.get("status","db77c_expA_failed"); OUT["error"]={"type":type(exc).__name__,"msg":str(exc),"trace":traceback.format_exc()[-2500:]}
finally:
    OUT["ended"]=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()); REMOTE_OUT.mkdir(parents=True,exist_ok=True)
    RESULT.write_text(json.dumps(OUT,indent=2,default=str)); print("EXPA_JSON_BEGIN"); print(json.dumps(OUT,default=str,separators=(",",":"))[:4000]); print("EXPA_JSON_END")
'''
    return code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__REMOTE_IN__", REMOTE_IN).replace("__P_RESULT__", P_RESULT)


def remote_bash(py):
    b = base64.b64encode(py.encode()).decode()
    return "set +x\npython - <<'PY'\nimport base64\nexec(compile(base64.b64decode('" + b + "').decode(),'<expA>','exec'))\nPY"


def upload(client):
    up = {}
    for name, rp in UPLOADS.items():
        raw = (ROOT / rp).read_bytes()
        r = client.post("/write", {"path": f"{REMOTE_IN}/{name}", "content": base64.b64encode(raw).decode(), "base64": True})
        up[name] = r.get("bytes_written", r.get("sha256", "ok"))
    return up


def run_remote(timeout_s):
    client = ColabClient(); status = client.get("/status", timeout=120)
    up = upload(client)
    submit = client.post("/exec", {"cmd": ["bash", "-lc", remote_bash(remote_py())], "cwd": "/content", "timeout_s": timeout_s}, timeout=120)
    jid = submit["job_id"]; t0 = time.time(); job = {}
    while time.time() - t0 < timeout_s + 180:
        time.sleep(10); job = client.get(f"/jobs/{urllib.parse.quote(jid)}", timeout=120)
        if job.get("state") != "running": break
    job = sanitize(job)
    rr = None; raw = client.read_file(P_RESULT, max_size_mb=12)
    if raw:
        try: rr = json.loads(raw.decode())
        except Exception: rr = None
    if rr is None:
        lt = job.get("log_tail", "")
        if "EXPA_JSON_BEGIN" in lt:
            try: rr = json.loads(lt.split("EXPA_JSON_BEGIN",1)[1].split("EXPA_JSON_END",1)[0].strip())
            except Exception: rr = None
    rr = sanitize(rr or {"status": "missing", "log": sanitize(job.get("log_tail", ""))})
    (OUT_DIR / "EXPA_remote_result.json").write_text(json.dumps(rr, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = {}
    for k, (rn, mb) in FETCH.items():
        lp = (OUT_DIR if k in {"summary", "remote_result", "board", "roi", "gate"} else OUT_DIR / "fetch") / rn
        lp.parent.mkdir(parents=True, exist_ok=True)
        d = client.read_file(REMOTE_OUT + "/" + rn, max_size_mb=mb)
        fetched[k] = {"exists": bool(d)}
        if d: lp.write_bytes(d)
    man = {"created": datetime.now(timezone.utc).isoformat(), "status": "db77c_expA_difix_seamband",
           "scope": {"exec_count": 1, "a100_used": True, "band_only": True, "single_step": True, "red_promotion": False},
           "runtime": {"secret": "non_repo_file" if client.source != "process_env" else "env", "status": safe_status(status)},
           "uploads": up, "remote_status": rr.get("status"), "difix_api": rr.get("difix_api"), "object_gate": rr.get("object_gate"),
           "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}), "fetched": fetched,
           "drive_output": "results/db77c_expA_difix/"}
    hits = secret_hits(json.dumps(man) + json.dumps(rr)); man["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits)}
    (OUT_DIR / "EXPA_manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": rr.get("status"), "difix_api": rr.get("difix_api"), "object_gate": rr.get("object_gate"),
            "changed_outside_band_px": rr.get("changed_outside_band_px"), "secret_hits": man["strict_secret_scan"]["hit_count"]}


def check():
    import py_compile; py_compile.compile(str(Path(__file__).resolve()), doraise=True); compile(remote_py(), "<expA>", "exec")
    print(json.dumps({"status": "compile_ok"}))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); ap.add_argument("--run-remote", action="store_true"); ap.add_argument("--timeout-s", type=int, default=1800)
    a = ap.parse_args()
    if a.check: check(); return
    if a.run_remote: print(json.dumps(run_remote(a.timeout_s), indent=2)); return
    print(json.dumps({"status": "ready"}))


if __name__ == "__main__":
    main()
