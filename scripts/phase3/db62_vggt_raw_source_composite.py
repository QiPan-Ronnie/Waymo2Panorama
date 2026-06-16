from __future__ import annotations

import base64
import gzip
import importlib.util
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db62_vggt_raw_source_composite"
REMOTE_RESULT = OUT_DIR / "db62_vggt_raw_source_remote_result.json"
MANIFEST = OUT_DIR / "db62_vggt_raw_source_composite_manifest.json"
BOARD = OUT_DIR / "db62_vggt_raw_source_composite_board.jpg"
REMOTE_DRIVE_JSON = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db62_vggt_raw_source_composite/db62_vggt_raw_source_remote_result.json"

DB61_SCRIPT = ROOT / "scripts" / "phase3" / "db61_fresh_vggt_a1g_quicklook.py"
A1_PANO = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "A1_view_none_bmw_1024x2048.png"
G_PANO = ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg"
DB61_BOARD = ROOT / "deliverables" / "dit360_v2" / "db61_fresh_vggt_a1g_quicklook" / "db61_fresh_vggt_a1g_quicklook_board.jpg"

TARGET = {
    "uuid": "02a00399-3857-444e-8db3-a8f58489c394",
    "anchor": 0,
    "roi_key": "db25_longline",
    "roi_xyxy": [850, 420, 1650, 720],
}

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "cloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com", re.IGNORECASE),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "json_hex_token": re.compile(r'"token"\s*:\s*"[0-9a-fA-F]{32}"'),
}


def rel(path: Path | str | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    try:
        return str(p.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return "<non-repo path omitted>"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        clean: dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in {"token", "authorization", "headers"}:
                clean[key] = "<redacted>"
            else:
                clean[key] = sanitize(value)
        return clean
    if isinstance(obj, list):
        return [sanitize(value) for value in obj]
    if isinstance(obj, str):
        text = obj
        for pattern in TOKEN_PATTERNS.values():
            text = pattern.sub("<redacted>", text)
        return text
    return obj


def token_hits(obj: Any) -> list[dict[str, Any]]:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    hits = []
    for name, pattern in TOKEN_PATTERNS.items():
        found = pattern.findall(text)
        if found:
            hits.append({"path": "manifest_preview", "pattern": name, "count": len(found)})
    return hits


def png_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    with Image.open(path) as img:
        return {"exists": True, "size": list(img.size), "bytes": int(path.stat().st_size)}


class ColabClient:
    def __init__(self) -> None:
        db61 = load_module(DB61_SCRIPT, "db61_runtime_helpers")
        runtime = db61.load_runtime_secret()
        self.url = runtime["url"].rstrip("/")
        self.token = runtime["token"]

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        timeout: int = 180,
    ) -> dict[str, Any]:
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get(self, path: str, timeout: int = 180) -> dict[str, Any]:
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, body: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
        return self.request("POST", path, body=body, timeout=timeout)

    def read_file(self, remote_path: str, max_size_mb: int = 10) -> bytes:
        data = self.request(
            "GET",
            "/read",
            params={"path": remote_path, "base64": "true", "max_size_mb": str(max_size_mb)},
            timeout=240,
        )
        return base64.b64decode(data["content"])


def db62_remote_python() -> str:
    db61 = load_module(DB61_SCRIPT, "db61_remote_template")
    code = db61.db61_remote_python()
    replacements = {
        "DB-61": "DB-62",
        "db61_fresh_vggt_a1g_quicklook": "db62_vggt_raw_source_composite",
        "db61_fresh_vggt_remote_result.json": "db62_vggt_raw_source_remote_result.json",
        "DB61_JSON_BEGIN": "DB62_JSON_BEGIN",
        "DB61_JSON_END": "DB62_JSON_END",
    }
    for old, new in replacements.items():
        code = code.replace(old, new)

    injection = r'''

    # DB62: use VGGT point/depth confidence as raw-camera source-selection evidence
    # inside DB25 only. This creates raw-camera-backed crops; it is not a renderer
    # or an inpainting model.
    def _db62_norm(arr, valid):
        arr = np.asarray(arr, dtype=np.float32)
        finite = valid & np.isfinite(arr)
        if not bool(finite.any()):
            return np.zeros_like(arr, dtype=np.float32)
        vals = arr[finite]
        lo = float(np.percentile(vals, 10))
        hi = float(np.percentile(vals, 90))
        if hi <= lo + 1e-6:
            return np.zeros_like(arr, dtype=np.float32)
        out = (arr - lo) / (hi - lo)
        return np.clip(np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0).astype(np.float32)

    def _db62_save_png(path, arr):
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        Image.fromarray(arr).save(path)

    def _db62_save_gray(path, arr):
        arr = np.clip(np.nan_to_num(arr, nan=0.0), 0.0, 1.0)
        Image.fromarray((arr * 255.0).astype(np.uint8)).save(path)

    def _db62_heat(arr):
        arr = np.clip(np.nan_to_num(arr, nan=0.0), 0.0, 1.0)
        r = np.clip(2.0 * arr, 0, 1)
        g = np.clip(1.5 - np.abs(arr - 0.5) * 2.0, 0, 1)
        b = np.clip(2.0 * (1.0 - arr), 0, 1)
        return np.stack([r, g, b], axis=-1) * 255.0

    roi_key = "db25_longline"
    x0, y0, x1, y1 = ROIS[roi_key]
    roi_h, roi_w = y1 - y0, x1 - x0
    rr, cc = np.indices((roi_h, roi_w))
    n_views = len(slabs)
    raw_stack = np.stack([s[y0:y1, x0:x1].astype(np.float32) for s in slabs], axis=0)
    weight_stack = np.stack([w[y0:y1, x0:x1].astype(np.float32) for w in weights], axis=0)
    valid_stack = np.zeros((n_views, roi_h, roi_w), dtype=bool)
    dc_stack = np.full((n_views, roi_h, roi_w), np.nan, dtype=np.float32)
    wpc_stack = np.full((n_views, roi_h, roi_w), np.nan, dtype=np.float32)
    wp_stack = np.full((n_views, roi_h, roi_w, 3), np.nan, dtype=np.float32)

    for cam_idx in range(n_views):
        u_map, v_map, uv_valid_full = uv_maps[cam_idx]
        u_raw = u_map[y0:y1, x0:x1]
        v_raw = v_map[y0:y1, x0:x1]
        uv_valid = uv_valid_full[y0:y1, x0:x1]
        x_model, y_model, pre_valid = raw_to_model_xy(u_raw, v_raw, preprocess[cam_idx])
        valid = uv_valid & pre_valid & (weight_stack[cam_idx] > 1e-6)
        valid_stack[cam_idx] = valid
        dc_stack[cam_idx] = sample_scalar(depth_conf, cam_idx, x_model, y_model, valid)
        wpc_stack[cam_idx] = sample_scalar(world_points_conf, cam_idx, x_model, y_model, valid)
        wp_stack[cam_idx] = sample_vec3(world_points, cam_idx, x_model, y_model, valid)

    owner_idx = label_map[y0:y1, x0:x1].astype(np.int64)
    owner_rgb = raw_stack[owner_idx, rr, cc]
    owner_wp = wp_stack[owner_idx, rr, cc]
    dist = np.linalg.norm(wp_stack - owner_wp[None, :, :, :], axis=3)
    dist_valid = valid_stack & np.isfinite(dist)
    point_agreement = 1.0 - np.clip(np.nan_to_num(dist, nan=1.0) / 0.35, 0.0, 1.0)
    dc_norm = _db62_norm(dc_stack, valid_stack)
    wpc_norm = _db62_norm(wpc_stack, valid_stack)
    weight_norm = weight_stack / np.maximum(weight_stack.max(axis=0, keepdims=True), 1e-6)
    score = 0.34 * dc_norm + 0.26 * wpc_norm + 0.28 * point_agreement + 0.12 * np.clip(weight_norm, 0.0, 1.0)
    score[~valid_stack] = -1e6
    best_idx = np.argmax(score, axis=0).astype(np.int64)
    best_score = score[best_idx, rr, cc]
    owner_score = score[owner_idx, rr, cc]
    margin = np.maximum(best_score - owner_score, 0.0)
    overlap = valid_stack.sum(axis=0) >= 2
    alpha = np.clip((margin - 0.03) / 0.22, 0.0, 1.0).astype(np.float32)
    alpha *= overlap.astype(np.float32)
    alpha *= (1.0 - obj_mask[y0:y1, x0:x1].astype(np.float32))
    alpha = cv2.GaussianBlur(alpha, (0, 0), 4)
    alpha = np.clip(alpha, 0.0, 0.85).astype(np.float32)

    best_rgb = raw_stack[best_idx, rr, cc]
    hard_rgb = best_rgb
    soft_rgb = owner_rgb * (1.0 - alpha[..., None]) + best_rgb * alpha[..., None]

    colors = np.array([
        [231, 76, 60],
        [46, 204, 113],
        [52, 152, 219],
        [155, 89, 182],
        [241, 196, 15],
        [230, 126, 34],
        [26, 188, 156],
    ], dtype=np.float32)
    label_rgb = colors[np.clip(best_idx, 0, len(colors) - 1)]
    margin_heat = _db62_heat(np.clip(margin / 0.35, 0.0, 1.0))
    alpha_heat = _db62_heat(alpha / max(float(alpha.max()), 1e-6))

    owner_path = WORK / "db62_raw_owner_crop.png"
    hard_path = WORK / "db62_vggt_source_select_hard_crop.png"
    soft_path = WORK / "db62_vggt_source_composite_crop.png"
    alpha_path = WORK / "db62_vggt_source_alpha.png"
    label_path = WORK / "db62_vggt_source_label.png"
    margin_path = WORK / "db62_vggt_source_margin_heat.png"
    alpha_heat_path = WORK / "db62_vggt_source_alpha_heat.png"
    _db62_save_png(owner_path, owner_rgb)
    _db62_save_png(hard_path, hard_rgb)
    _db62_save_png(soft_path, soft_rgb)
    _db62_save_gray(alpha_path, alpha)
    _db62_save_png(label_path, label_rgb)
    _db62_save_png(margin_path, margin_heat)
    _db62_save_png(alpha_heat_path, alpha_heat)

    OUT["db62_raw_source_operator"] = {
        "operator": "vggt_point_confidence_guided_raw_camera_source_composite",
        "roi_key": roi_key,
        "roi_xyxy": [int(x0), int(y0), int(x1), int(y1)],
        "db25_only": True,
        "source_backing": "pixels copied only from rendered raw camera ERP slabs; VGGT supplies scoring/consistency evidence",
        "score_terms": {
            "depth_conf_norm": 0.34,
            "world_points_conf_norm": 0.26,
            "point_agreement_to_current_owner": 0.28,
            "renderer_weight_norm": 0.12
        },
        "stats": {
            "alpha_mean": round(float(alpha.mean()), 6),
            "alpha_max": round(float(alpha.max()), 6),
            "alpha_changed_frac_gt_0_05": round(float((alpha > 0.05).mean()), 6),
            "overlap_frac": round(float(overlap.mean()), 6),
            "best_differs_from_owner_frac": round(float((best_idx != owner_idx).mean()), 6),
            "best_differs_and_alpha_gt_0_05_frac": round(float(((best_idx != owner_idx) & (alpha > 0.05)).mean()), 6),
            "mean_margin": round(float(margin.mean()), 6),
            "p95_margin": round(float(np.percentile(margin, 95)), 6)
        },
        "remote_files": {
            "raw_owner_crop": str(owner_path),
            "vggt_source_select_hard_crop": str(hard_path),
            "vggt_source_composite_crop": str(soft_path),
            "vggt_source_alpha": str(alpha_path),
            "vggt_source_label": str(label_path),
            "vggt_source_margin_heat": str(margin_path),
            "vggt_source_alpha_heat": str(alpha_heat_path)
        },
        "claim_boundary": "diagnostic raw-camera-backed local composite; not source-faithful unless separately accepted",
    }
'''
    marker = '    OUT["target_uv_sampling"] = roi_results\n'
    if marker not in code:
        raise RuntimeError("target_uv_sampling marker missing in DB62 remote template")
    code = code.replace(marker, injection + "\n" + marker, 1)
    return code


def recovery_python() -> str:
    return r'''
import base64
import gzip
import json
import pathlib
import traceback

SRC = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db62_vggt_raw_source_composite/db62_vggt_raw_source_remote_result.json")
try:
    data = json.loads(SRC.read_text(encoding="utf-8"))
except Exception as exc:
    data = {"db": "DB-62", "error": {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-1500:]}}
payload = gzip.compress(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"), compresslevel=9)
print("DB62_RECOVERY_B64_BEGIN")
print(base64.b64encode(payload).decode("ascii"))
print("DB62_RECOVERY_B64_END")
'''


def extract_remote_json(log: str) -> dict[str, Any]:
    match = re.search(r"DB62_JSON_BEGIN\s*(\{.*\})\s*DB62_JSON_END", log, re.S)
    if match:
        return json.loads(match.group(1))
    b64_match = re.search(r"DB62_RECOVERY_B64_BEGIN\s*([A-Za-z0-9+/=\s]+?)\s*DB62_RECOVERY_B64_END", log, re.S)
    if b64_match:
        payload = re.sub(r"\s+", "", b64_match.group(1))
        return json.loads(gzip.decompress(base64.b64decode(payload)).decode("utf-8"))
    return {
        "db": "DB-62",
        "error": {"type": "MissingRemoteJson", "message": "Remote log did not contain DB62 JSON markers."},
    }


def recover_drive_json(client: ColabClient) -> dict[str, Any]:
    raw = client.read_file(REMOTE_DRIVE_JSON, max_size_mb=10)
    return sanitize(json.loads(raw.decode("utf-8")))


def run_exec(client: ColabClient, code: str, timeout_s: int, purpose: str) -> dict[str, Any]:
    remote_code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    bash = (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{remote_code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db62_remote>', 'exec'))\n"
        "PY"
    )
    job = client.post("/exec", {"cmd": ["bash", "-lc", bash], "cwd": "/content", "timeout_s": timeout_s}, timeout=180)
    job_id = job["job_id"]
    started = time.time()
    while True:
        time.sleep(8 if purpose == "vggt_raw_source" else 3)
        state = client.get(f"/jobs/{urllib.parse.quote(str(job_id))}", timeout=180)
        if state.get("state") != "running":
            result = extract_remote_json(state.get("log_tail", ""))
            result["colab_job"] = {
                "job_id": job_id,
                "state": state.get("state"),
                "exit_code": state.get("exit_code"),
                "duration_s": state.get("duration_s"),
                "purpose": purpose,
            }
            return sanitize(result)
        if time.time() - started > timeout_s + 120:
            return {
                "db": "DB-62",
                "error": {"type": "LocalPollTimeout", "message": f"timed out waiting for job {job_id}"},
                "colab_job": {"job_id": job_id, "state": state.get("state"), "purpose": purpose},
            }


def run_remote(timeout_s: int) -> tuple[dict[str, Any], ColabClient]:
    client = ColabClient()
    status = sanitize(client.get("/status", timeout=180))
    result = run_exec(client, db62_remote_python(), timeout_s, "vggt_raw_source")
    if result.get("error", {}).get("type") == "MissingRemoteJson":
        try:
            recovered = recover_drive_json(client)
            result = recovered
            result["recovered_from_drive_json_after_log_truncation"] = True
        except Exception:
            recovered = run_exec(client, recovery_python(), 240, "recover_db62_drive_json")
            if not recovered.get("error"):
                result = recovered
                result["recovered_after_log_truncation"] = True
    result["runtime_status_pre_exec"] = {
        "runtime_type": status.get("runtime_type"),
        "gpu_name": status.get("gpu_name"),
        "gpu_mem_free_mb": status.get("gpu_mem_free_mb"),
        "active_jobs": status.get("active_jobs"),
    }
    result["runtime_secret_source"] = "approved_env_or_non_repo_file"
    REMOTE_RESULT.write_text(json.dumps(sanitize(result), indent=2), encoding="utf-8")
    return result, client


def fetch_remote_images(remote: dict[str, Any], client: ColabClient) -> dict[str, Any]:
    files = remote.get("db62_raw_source_operator", {}).get("remote_files", {})
    fetched: dict[str, Any] = {}
    for key, remote_path in files.items():
        suffix = Path(str(remote_path)).suffix or ".png"
        local = OUT_DIR / f"db62_{key}{suffix}"
        raw = client.read_file(str(remote_path), max_size_mb=10)
        local.write_bytes(raw)
        fetched[key] = {"remote_path": "<remote drive path omitted>", "path": rel(local), **png_stats(local)}
    return fetched


def load_rgb(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def load_gray(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32) / 255.0


def compose_base(base_path: Path, crop_path: Path, alpha_path: Path, out_path: Path) -> dict[str, Any]:
    base = load_rgb(base_path)
    crop = load_rgb(crop_path)
    alpha = load_gray(alpha_path)
    roi = TARGET["roi_xyxy"]
    base_crop = base.crop(tuple(roi)).convert("RGB")
    base_arr = np.asarray(base_crop, dtype=np.float32)
    crop_arr = np.asarray(crop, dtype=np.float32)
    a = np.clip(alpha * 0.95, 0.0, 0.95)[..., None]
    out_crop = base_arr * (1.0 - a) + crop_arr * a
    out = base.copy()
    out.paste(Image.fromarray(np.clip(out_crop, 0, 255).astype(np.uint8)), (roi[0], roi[1]))
    out.save(out_path)
    diff = np.abs(out_crop - base_arr)
    return {
        "path": rel(out_path),
        **png_stats(out_path),
        "metrics": {
            "roi_alpha_mean": round(float(alpha.mean()), 6),
            "roi_alpha_max": round(float(alpha.max()), 6),
            "roi_changed_frac_alpha_gt_0_05": round(float((alpha > 0.05).mean()), 6),
            "roi_mean_abs_delta": round(float(diff.mean()), 6),
            "roi_p95_abs_delta": round(float(np.percentile(diff, 95)), 6),
            "roi_max_abs_delta": round(float(diff.max()), 6),
        },
    }


def diff_crop(before: Image.Image, after: Image.Image, roi: list[int]) -> Image.Image:
    b = np.asarray(before.crop(tuple(roi)).convert("RGB"), dtype=np.float32)
    a = np.asarray(after.crop(tuple(roi)).convert("RGB"), dtype=np.float32)
    d = np.clip(np.abs(a - b) * 5.0, 0, 255).astype(np.uint8)
    return Image.fromarray(d)


def build_local_outputs(fetched: dict[str, Any]) -> dict[str, Any]:
    crop = OUT_DIR / "db62_vggt_source_composite_crop.png"
    alpha = OUT_DIR / "db62_vggt_source_alpha.png"
    a1_out = OUT_DIR / "db62_a1_vggt_raw_source_composite.png"
    g_out = OUT_DIR / "db62_g_vggt_raw_source_composite.png"
    a1_stats = compose_base(A1_PANO, crop, alpha, a1_out)
    g_stats = compose_base(G_PANO, crop, alpha, g_out)
    return {"a1_candidate": a1_stats, "g_candidate": g_stats}


def font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill=(235, 235, 235), size=16) -> None:
    draw.text(xy, str(text), fill=fill, font=font(size))


def panel(board: Image.Image, image: Image.Image | Path, box: tuple[int, int, int, int], label: str) -> None:
    draw = ImageDraw.Draw(board)
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=(24, 27, 32), outline=(86, 91, 101), width=2)
    if isinstance(image, Path):
        if not image.exists():
            draw_text(draw, (x0 + 12, y0 + 30), "missing", fill=(246, 142, 142), size=15)
            draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)
            return
        img = Image.open(image).convert("RGB")
    else:
        img = image.convert("RGB")
    img.thumbnail((x1 - x0 - 20, y1 - y0 - 48))
    board.paste(img, (x0 + (x1 - x0 - img.width) // 2, y0 + 10))
    draw_text(draw, (x0 + 12, y1 - 31), label, fill=(220, 230, 245), size=13)


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (2400, 1750), (15, 17, 22))
    draw = ImageDraw.Draw(board)
    roi = TARGET["roi_xyxy"]
    draw_text(draw, (40, 28), "DB62 VGGT point-guided raw-camera source composite", size=28)
    draw_text(draw, (40, 70), "DB25-only. Raw camera pixels selected/blended by VGGT point/conf evidence. Diagnostic quick-look.", fill=(246, 214, 150), size=16)
    remote = manifest["remote_result"]
    op = remote.get("db62_raw_source_operator", {})
    stats = op.get("stats", {})
    vggt = remote.get("vggt", {})
    lines = [
        f"runtime={remote.get('runtime_status_pre_exec', {}).get('runtime_type')} gpu={remote.get('runtime_status_pre_exec', {}).get('gpu_name')}",
        f"vggt={vggt.get('model_id')} ok={vggt.get('inference_ok')} duration={vggt.get('duration_s')}",
        f"alpha_mean={stats.get('alpha_mean')} alpha_max={stats.get('alpha_max')} changed={stats.get('alpha_changed_frac_gt_0_05')}",
        f"best_diff_owner={stats.get('best_differs_from_owner_frac')} overlap={stats.get('overlap_frac')} secret_hits={len(manifest['token_scan_hits'])}",
    ]
    y = 112
    for line in lines:
        draw_text(draw, (52, y), line, fill=(224, 232, 245), size=15)
        y += 25
    a1 = load_rgb(A1_PANO)
    g = load_rgb(G_PANO)
    a1_out = load_rgb(OUT_DIR / "db62_a1_vggt_raw_source_composite.png")
    g_out = load_rgb(OUT_DIR / "db62_g_vggt_raw_source_composite.png")
    panel(board, a1.crop(tuple(roi)), (40, 240, 600, 485), "A1 original DB25 ROI")
    panel(board, a1_out.crop(tuple(roi)), (620, 240, 1180, 485), "A1 + DB62 raw/VGGT composite")
    panel(board, diff_crop(a1, a1_out, roi), (1200, 240, 1760, 485), "A1 diff x5")
    panel(board, g.crop(tuple(roi)), (1780, 240, 2340, 485), "G original DB25 ROI")
    panel(board, g_out.crop(tuple(roi)), (40, 510, 600, 755), "G + DB62 raw/VGGT composite")
    panel(board, diff_crop(g, g_out, roi), (620, 510, 1180, 755), "G diff x5")
    panel(board, OUT_DIR / "db62_raw_owner_crop.png", (1200, 510, 1760, 755), "Raw owner crop")
    panel(board, OUT_DIR / "db62_vggt_source_composite_crop.png", (1780, 510, 2340, 755), "VGGT raw source composite crop")
    panel(board, OUT_DIR / "db62_vggt_source_alpha_heat.png", (40, 780, 600, 1025), "VGGT composite alpha")
    panel(board, OUT_DIR / "db62_vggt_source_margin_heat.png", (620, 780, 1180, 1025), "VGGT score margin")
    panel(board, OUT_DIR / "db62_vggt_source_label.png", (1200, 780, 1760, 1025), "Selected source camera label")
    panel(board, DB61_BOARD, (1780, 780, 2340, 1025), "DB61 mask-only context")
    panel(board, a1_out.resize((512, 256)), (40, 1050, 600, 1295), "A1 full candidate")
    panel(board, g_out.resize((512, 256)), (620, 1050, 1180, 1295), "G full candidate")
    y = 1330
    draw_text(draw, (40, y), "Claim boundary", size=22)
    y += 38
    for key, value in manifest["claim_boundaries"].items():
        draw_text(draw, (58, y), f"{key}: {value}", fill=(246, 214, 150), size=15)
        y += 25
    draw_text(draw, (40, 1692), f"Manifest: {rel(MANIFEST)}", fill=(185, 190, 200), size=13)
    board.save(BOARD, quality=92)


def build_manifest(remote: dict[str, Any], fetched: dict[str, Any], local_outputs: dict[str, Any]) -> dict[str, Any]:
    op = remote.get("db62_raw_source_operator", {})
    manifest: dict[str, Any] = {
        "db": "DB-62",
        "status": "vggt_raw_source_composite_created" if op else "blocked_or_failed",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "scope": {
            "a100_job_submitted": bool(remote.get("colab_job", {}).get("job_id")),
            "new_vggt_inference": remote.get("vggt", {}).get("inference_ok") is True,
            "db25_only_remote_scope": op.get("db25_only") is True,
            "raw_camera_pixels_used": bool(op),
            "vggt_point_confidence_scoring_used": bool(op),
            "a1_g_local_outputs_created": bool(local_outputs),
            "source_id_map_created": False,
            "dit_flux_prompt_generation": False,
            "inpainting": False,
            "db41_edited": False,
            "permission_change": False,
        },
        "remote_result": sanitize(remote),
        "fetched_remote_images": fetched,
        "local_outputs": local_outputs,
        "outputs": {
            "manifest": rel(MANIFEST),
            "board": rel(BOARD),
            "remote_result": rel(REMOTE_RESULT),
            "output_dir": rel(OUT_DIR),
        },
        "claim_boundaries": {
            "source_faithful": False,
            "raw_camera_backed_diagnostic": bool(op),
            "accepted_repair": False,
            "presentation_only": True,
            "diagnostic_stress_test": True,
            "a1_g_repaired": False,
            "db32_changed": False,
            "source_id_map": False,
            "red_promotion": False,
            "bosch_training_ready": False,
        },
    }
    manifest["token_scan_hits"] = token_hits(manifest)
    manifest["hard_checks_passed"] = all(
        [
            len(manifest["token_scan_hits"]) == 0,
            manifest["scope"]["db25_only_remote_scope"] is True,
            manifest["scope"]["raw_camera_pixels_used"] is True,
            manifest["scope"]["vggt_point_confidence_scoring_used"] is True,
            manifest["scope"]["source_id_map_created"] is False,
            manifest["claim_boundaries"]["source_faithful"] is False,
            manifest["claim_boundaries"]["presentation_only"] is True,
        ]
    )
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    build_board(manifest)
    return manifest


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true")
    parser.add_argument("--recover-remote", action="store_true")
    parser.add_argument("--timeout-s", type=int, default=2400)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.run_remote:
        remote, client = run_remote(args.timeout_s)
        fetched = fetch_remote_images(remote, client) if remote.get("db62_raw_source_operator") else {}
    elif args.recover_remote:
        client = ColabClient()
        remote = recover_drive_json(client)
        remote["recovered_from_drive_json"] = True
        remote["runtime_secret_source"] = "approved_env_or_non_repo_file"
        REMOTE_RESULT.write_text(json.dumps(sanitize(remote), indent=2), encoding="utf-8")
        fetched = fetch_remote_images(remote, client) if remote.get("db62_raw_source_operator") else {}
    else:
        remote = read_json(REMOTE_RESULT) if REMOTE_RESULT.exists() else {"db": "DB-62", "error": {"type": "MissingRemoteResult"}}
        fetched = {}
        for path in OUT_DIR.glob("db62_*.png"):
            fetched[path.stem.replace("db62_", "")] = {"path": rel(path), **png_stats(path)}
    local_outputs = build_local_outputs(fetched) if (OUT_DIR / "db62_vggt_source_composite_crop.png").exists() else {}
    manifest = build_manifest(remote, fetched, local_outputs)
    print(json.dumps({
        "status": manifest["status"],
        "manifest": rel(MANIFEST),
        "board": rel(BOARD),
        "vggt_inference_ok": manifest["remote_result"].get("vggt", {}).get("inference_ok"),
        "operator_stats": manifest["remote_result"].get("db62_raw_source_operator", {}).get("stats"),
        "hard_checks_passed": manifest["hard_checks_passed"],
        "token_scan_hits": len(manifest["token_scan_hits"]),
        "claim": "raw-camera-backed diagnostic; not accepted source-faithful repair",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
