import sys, os, importlib.util
sys.stdout.reconfigure(encoding="utf-8")
print("PY", sys.version.split()[0])
try:
    import cv2, numpy
    print("cv2", cv2.__version__, "np", numpy.__version__)
    print("DIS", hasattr(cv2, "DISOpticalFlow_create"))
except Exception as e:
    print("cv2/np ERR", e)
for m in ["pyransac3d", "pyarrow"]:
    print(m, "OK" if importlib.util.find_spec(m) else "MISSING")
paths = {
    "drive": "/content/drive/MyDrive",
    "repo": "/content/waymo2panorama",
    "bmwdata": "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-3857-444e-8db3-a8f58489c394",
    "results": "/content/drive/MyDrive/koi_waymo2pano_colab/results",
}
for k, p in paths.items():
    print(k, "=", "yes" if os.path.isdir(p) else "no", p)
print("gpu_check")
try:
    import torch
    print("torch", torch.__version__, "cuda", torch.cuda.is_available())
except Exception as e:
    print("torch", e)
