"""DB-80 Step C: generality — run the step-A battery + step-B render on the remaining 3 staged
AV2 logs (highway / downtown / crowd). Reuses the step-A and step-B remote code verbatim with
only the CASES list substituted (one bounded /exec each). Waymo raw sensor data is NOT staged
on Drive (verified 2026-06-09) -> Waymo generality remains a DATA step, recorded not skipped.
"""
from __future__ import annotations
import json, time
from pathlib import Path
import db80_virtual_centre as A
import db80_stepB_render as B
from db64_ltr_v0_phase4b_z_visibility_cause import ColabClient, secret_hits

OUT_DIR = A.ROOT / "deliverables" / "db80_virtual_centre"
REMOTE_OUT = A.REMOTE_OUT

OLD = 'CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]'
# full uuids (depth_visibility_seam_probe.LOG_UUIDS only maps a subset of shorts)
NEW = ('CASES = [("2c652f9e-8db8-3572-aa49-fae1344a875b:30:highway", "2c652f9e_a030_highway"), '
       '("9f871fb4-3b8e-34b3-9161-ed961e71a6da:30:downtown", "9f871fb4_a030_downtown"), '
       '("fbee355f-8878-31fa-8ac8-b9a45a3f130a:30:crowd", "fbee355f_a030_crowd")]')
NAMES = ["2c652f9e_a030_highway", "9f871fb4_a030_downtown", "fbee355f_a030_crowd"]


def run(client: ColabClient, py: str, result_remote: str, rename: dict[str, str], timeout_s: int = 2400) -> dict:
    submit = client.post("/exec", {"cmd": ["bash", "-lc", A.remote_bash(py)], "cwd": "/content/waymo2panorama", "timeout_s": timeout_s}, timeout=180)
    job = A.poll_job(client, submit["job_id"], timeout_s)
    fetched = {}
    for remote_name, local_name in rename.items():
        raw = client.read_file(REMOTE_OUT + "/" + remote_name, max_size_mb=95)
        if raw is not None:
            (OUT_DIR / local_name).write_bytes(raw); fetched[remote_name] = local_name
    return {"job_state": job.get("state"), "fetched": fetched}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ColabClient()
    # --- step A numbers on the 3 new logs ---
    pyA = A.remote_db80_python()
    assert OLD in pyA, "CASES anchor line not found in step-A remote code"
    pyA = pyA.replace(OLD, NEW)
    pyA = pyA.replace("DB80_summary.json", "DB80C_summary.json").replace("DB80_remote_result.json", "DB80C_remote_result.json").replace("DB80_review_board.jpg", "DB80C_review_board.jpg")
    renameA = {"DB80C_summary.json": "DB80C_summary.json", "DB80C_remote_result.json": "DB80C_remote_result.json"}
    for n in NAMES:
        renameA[f"{n}_db80_board.jpg"] = f"{n}_db80_board.jpg"
    repA = run(client, pyA, REMOTE_OUT + "/DB80C_remote_result.json", renameA)
    # --- step B renders on the 3 new logs ---
    pyB = B.remote_py()
    assert OLD in pyB, "CASES anchor line not found in step-B remote code"
    pyB = pyB.replace(OLD, NEW).replace("DB80B_remote_result.json", "DB80CB_remote_result.json")
    renameB = {"DB80CB_remote_result.json": "DB80CB_remote_result.json"}
    for n in NAMES:
        renameB[f"{n}_db80B_board.jpg"] = f"{n}_db80B_board.jpg"
        for t in ("ego_rot", "cen_depth"):
            renameB[f"{n}_{t}.png"] = f"{n}_{t}.png"
    repB = run(client, pyB, REMOTE_OUT + "/DB80CB_remote_result.json", renameB)
    report = {"stepA": repA, "stepB": repB}
    report["secret_hits"] = secret_hits(json.dumps(report))
    out = Path.home() / ".waymo2panorama" / "db80c_run_report.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("report written (non-repo):", out)


if __name__ == "__main__":
    main()
