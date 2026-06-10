"""AV2 panorama DATASET GENERATION: run the full v2.2 stack (db89_ghost_recovery)
over many anchors per log and write panoramas DIRECTLY to a Drive dataset folder
(no local copies). One /exec per log; per-case isolation so a bad anchor cannot
kill its batch. Output: datasets/av2_pano_v1/<tag>_aNNN_segcomposite.png + a
manifest json per log."""
from __future__ import annotations
import json, time
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, sanitize, secret_hits
import db89_ghost_recovery as m

LOGS = [
    ("02a00399", "bmw"),
    ("0bae3b5e-417d-3b03-abaa-806b433233b8", "clean"),
    ("2c652f9e-8db8-3572-aa49-fae1344a875b", "highway"),
    ("9f871fb4-3b8e-34b3-9161-ed961e71a6da", "downtown"),
    ("fbee355f-8878-31fa-8ac8-b9a45a3f130a", "crowd"),
]
ANCHORS = list(range(0, 150, 10))   # 15 anchors per log, 1s apart -> 75 panoramas
DATASET = "datasets/av2_pano_v1"

OLD_CASES = '''CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"),
         ("9f871fb4-3b8e-34b3-9161-ed961e71a6da:30:downtown", "9f871fb4_a030_downtown"),
         ("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "fbee355f_a030_crowd"),
         ("0bae3b5e-417d-3b03-abaa-806b433233b8:30:clean", "0bae3b5e_a030_clean"),
         ("2c652f9e-8db8-3572-aa49-fae1344a875b:30:highway", "2c652f9e_a030_highway")]'''


def batch_py(log_uuid: str, tag: str, anchors: list[int]) -> str:
    py = m.remote_py()
    cases = ", ".join(f'("{log_uuid}:{a}:{tag}", "{tag}_a{a:03d}")' for a in anchors)
    assert OLD_CASES in py
    py = py.replace(OLD_CASES, "CASES = [" + cases + "]")
    py = py.replace("results/db89_ghost_recovery", DATASET)
    py = py.replace("DB89_remote_result.json", f"manifest_{tag}.json")
    # dataset mode: keep only the final panorama per anchor (no emc/board copies)
    py = py.replace('save_rgb(REMOTE_OUT / f"{run_name}_emc.png", emc)', "pass")
    py = py.replace('board.save(REMOTE_OUT / f"{run_name}_db89_board.jpg", quality=90)', "pass")
    # per-case isolation: one bad anchor must not kill the batch
    py = py.replace(
        "reports = [run_case(cs, rn) for cs, rn in CASES]",
        "reports = []\n"
        "    for cs, rn in CASES:\n"
        "        try:\n"
        "            reports.append(run_case(cs, rn))\n"
        "        except Exception as e:\n"
        "            reports.append({'case': rn, 'error': type(e).__name__ + ': ' + str(e)[:200]})",
    )
    return py


def run_batch(client: ColabClient, log_uuid: str, tag: str, timeout_s: int = 3000) -> dict:
    import base64
    b = base64.b64encode(batch_py(log_uuid, tag, ANCHORS).encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<dsgen>', 'exec'))\nPY")
    submit = client.post("/exec", {"cmd": ["bash", "-lc", bash],
                                   "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = m.poll_job(client, submit["job_id"], timeout_s)
    raw = client.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/{DATASET}/manifest_{tag}.json", max_size_mb=8)
    rep = {"log": tag, "job_state": job.get("state")}
    if raw:
        d = json.loads(raw)
        rep["status"] = d.get("status")
        cases = d.get("cases", [])
        rep["n_ok"] = sum(1 for c in cases if "error" not in c)
        rep["n_err"] = sum(1 for c in cases if "error" in c)
        rep["errors"] = [c for c in cases if "error" in c][:3]
    rep["secret_hits"] = secret_hits(json.dumps(rep))
    return rep


def submit_batch(client: ColabClient, log_uuid: str, tag: str, timeout_s: int = 5400) -> str:
    import base64
    b = base64.b64encode(batch_py(log_uuid, tag, ANCHORS).encode()).decode()
    bash = ("set +x\npython - <<'PY'\nimport base64\n"
            "exec(compile(base64.b64decode('" + b + "').decode(), '<dsgen>', 'exec'))\nPY")
    submit = client.post("/exec", {"cmd": ["bash", "-lc", bash],
                                   "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    return submit["job_id"]


def poll_manifests(client: ColabClient) -> list[dict]:
    out = []
    for _, tag in LOGS:
        raw = client.read_file(f"/content/drive/MyDrive/koi_waymo2pano_colab/{DATASET}/manifest_{tag}.json", max_size_mb=8)
        rep = {"log": tag}
        if raw:
            d = json.loads(raw)
            rep["status"] = d.get("status")
            cases = d.get("cases", [])
            rep["n_ok"] = sum(1 for c in cases if "error" not in c)
            rep["n_err"] = sum(1 for c in cases if "error" in c)
            errs = [c for c in cases if "error" in c]
            if errs: rep["err_sample"] = errs[0]
        else:
            rep["status"] = "pending"
        out.append(rep)
    return out


if __name__ == "__main__":
    import sys
    client = ColabClient()
    if "--poll" in sys.argv:
        print(json.dumps(poll_manifests(client), indent=1))
    else:
        skip = set(a.split("=", 1)[1] for a in sys.argv if a.startswith("--skip="))
        jobs = {}
        for log_uuid, tag in LOGS:
            if tag in skip:
                continue
            jobs[tag] = submit_batch(client, log_uuid, tag)
            print("submitted", tag, jobs[tag])
        print(json.dumps({"submitted": list(jobs)}))
