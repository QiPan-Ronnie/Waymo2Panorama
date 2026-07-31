"""Fail-closed analysis of frozen DB-226 raw same-ray artifact bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.phase3 import db226_luma_response as luma_response  # noqa: E402
from scripts.phase3.db226_luma_response import (  # noqa: E402
    PROFILE_SCHEMA_VERSION,
    RAW_PAIR_SCHEMA_VERSION,
    canonicalize_pair_frame,
    collect_pair_samples,
    evaluate_profile_transfer,
    split_assignment_sha256,
)


PAIR_ARRAY_SUFFIXES = (
    "rgb_a",
    "rgb_b",
    "erp_flat_index",
    "xy_a",
    "xy_b",
    "depth_m",
    "parallax_deg",
)
PRIMARY_CONFIG = {"rho_min": 0.45, "max_parallax_deg": 5.0}
SENSITIVITY_RHO = (None, 0.30, 0.45, 0.60)
SENSITIVITY_PARALLAX = (2.0, 5.0, None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_log_ids(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"manifest {field} must be a nonempty list")
    if any(not isinstance(log_id, str) or not log_id for log_id in value):
        raise ValueError(f"manifest {field} must contain nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"manifest {field} must not contain duplicates")
    return sorted(value)


def validate_split_manifest(
    manifest: Mapping[str, object],
    *,
    expected_log_count: int | None = None,
) -> dict[str, object]:
    """Validate an already-frozen split without selecting or overwriting it."""

    if not isinstance(manifest, Mapping):
        raise ValueError("split manifest must be a JSON object")
    train_ids = _manifest_log_ids(manifest.get("train_log_ids"), field="train_log_ids")
    heldout_ids = _manifest_log_ids(
        manifest.get("heldout_log_ids"), field="heldout_log_ids"
    )
    if not set(train_ids).isdisjoint(heldout_ids):
        raise ValueError("manifest train_log_ids and heldout_log_ids must be disjoint")
    derived_selected = sorted(train_ids + heldout_ids)
    selected_value = manifest.get("selected_log_ids")
    if selected_value is None:
        selected_ids = derived_selected
    else:
        selected_ids = _manifest_log_ids(selected_value, field="selected_log_ids")
        if selected_ids != derived_selected:
            raise ValueError("manifest selected_log_ids must equal the frozen split selected set")
    if expected_log_count is not None and len(selected_ids) != expected_log_count:
        raise ValueError(f"manifest must contain exactly {expected_log_count} selected logs")

    def normalize_anchors(
        raw_anchors: object,
        *,
        source: str,
        require_nonempty: bool,
    ) -> dict[str, list[int]]:
        if not isinstance(raw_anchors, Mapping):
            raise ValueError(f"manifest {source} must map log IDs to anchor lists")
        if set(raw_anchors) != set(selected_ids):
            raise ValueError(f"manifest {source} must cover exactly the selected log set")
        result: dict[str, list[int]] = {}
        for log_id in selected_ids:
            values = raw_anchors[log_id]
            if not isinstance(values, list):
                raise ValueError(f"manifest {source} for {log_id} must be a list")
            if require_nonempty and not values:
                raise ValueError(f"manifest {source} for {log_id} must be nonempty")
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in values
            ):
                raise ValueError(
                    f"manifest {source} for {log_id} must contain nonnegative integers"
                )
            if len(set(values)) != len(values):
                raise ValueError(f"manifest {source} for {log_id} has duplicate identities")
            result[log_id] = sorted(values)
        return result

    anchor_views: list[tuple[str, dict[str, list[int]]]] = []
    if manifest.get("anchors") is not None:
        anchor_views.append(
            (
                "anchors",
                normalize_anchors(
                    manifest["anchors"], source="anchors", require_nonempty=False
                ),
            )
        )
    if manifest.get("anchors_by_log") is not None:
        anchor_views.append(
            (
                "anchors_by_log",
                normalize_anchors(
                    manifest["anchors_by_log"],
                    source="anchors_by_log",
                    require_nonempty=False,
                ),
            )
        )

    cases_value = manifest.get("cases")
    if cases_value is not None:
        if not isinstance(cases_value, list) or not cases_value:
            raise ValueError("manifest cases must be a nonempty list")
        case_anchors: dict[str, list[int]] = {}
        for case in cases_value:
            if not isinstance(case, Mapping):
                raise ValueError("manifest cases entries must be objects")
            log_id = case.get("log_id")
            if not isinstance(log_id, str) or log_id not in selected_ids:
                raise ValueError("manifest cases must cover only the selected log set")
            if log_id in case_anchors:
                raise ValueError(f"manifest cases contain duplicate log/case for {log_id}")
            expected_partition = "train" if log_id in train_ids else "heldout"
            if case.get("partition") != expected_partition:
                raise ValueError(
                    f"manifest cases partition for {log_id} must be {expected_partition}"
                )
            case_anchors[log_id] = case.get("anchors")
        case_anchors = normalize_anchors(
            case_anchors,
            source="cases anchors",
            require_nonempty=True,
        )
        source_split_sha256 = manifest.get("source_split_sha256")
        if (
            not isinstance(source_split_sha256, str)
            or len(source_split_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_split_sha256)
        ):
            raise ValueError("enriched manifest source_split_sha256 must be lowercase SHA-256")
        anchor_views.append(("cases", case_anchors))

    anchors: dict[str, list[int]] | None = None
    if anchor_views:
        first_source, anchors = anchor_views[0]
        for source, view in anchor_views[1:]:
            if view != anchors:
                raise ValueError(f"manifest {first_source} and {source} anchor views disagree")

    computed_hash = split_assignment_sha256(selected_ids, train_ids, heldout_ids)
    recorded_hash = manifest.get("split_sha256")
    if recorded_hash is not None and recorded_hash != computed_hash:
        raise ValueError("manifest split_sha256 does not match the frozen split assignment")
    normalized: dict[str, object] = dict(manifest)
    normalized.update(
        {
            "selected_log_ids": selected_ids,
            "train_log_ids": train_ids,
            "heldout_log_ids": heldout_ids,
            "split_sha256": computed_hash,
        }
    )
    if anchors is not None:
        normalized["anchors"] = anchors
        normalized.pop("anchors_by_log", None)
    return normalized


def _expected_helper_sha256(manifest: Mapping[str, object]) -> str:
    local_hash = sha256_file(Path(luma_response.__file__).resolve())
    recorded = manifest.get("helper_source_sha256")
    if recorded is not None and recorded != local_hash:
        raise ValueError("manifest helper_source_sha256 does not match the analyzer helper")
    return local_hash


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid sidecar JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"sidecar must contain a JSON object: {path}")
    return value


def _verified_npz_path(sidecar_path: Path, sample_npz: object) -> Path:
    if not isinstance(sample_npz, str) or not sample_npz:
        raise ValueError(f"sidecar sample_npz must be a nonempty sibling filename: {sidecar_path}")
    relative = Path(sample_npz)
    if relative.is_absolute() or relative.name != sample_npz or relative.suffix != ".npz":
        raise ValueError(f"sidecar sample_npz must be a sibling .npz filename: {sidecar_path}")
    npz_path = (sidecar_path.parent / relative).resolve()
    if npz_path.parent != sidecar_path.parent.resolve():
        raise ValueError(f"sidecar sample_npz escapes its bundle directory: {sidecar_path}")
    if not npz_path.is_file():
        raise ValueError(f"sidecar sample_npz does not exist: {npz_path}")
    return npz_path


def _verify_transaction(sidecar: Mapping[str, object], *, path: Path) -> None:
    binding = json.dumps(
        {
            "log_id": sidecar["log_id"],
            "anchor_index": sidecar["anchor_index"],
            "sample_sha256": sidecar["sample_sha256"],
            "helper_source_sha256": sidecar["helper_source_sha256"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = hashlib.sha256(binding).hexdigest()
    if sidecar.get("artifact_transaction_id") != expected:
        raise ValueError(f"sidecar artifact transaction hash mismatch: {path}")


def _load_pair_rows(
    sidecar: Mapping[str, object],
    npz_path: Path,
    *,
    sidecar_path: Path,
) -> list[dict[str, object]]:
    pairs = sidecar.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError(f"sidecar pairs must be a list: {sidecar_path}")
    prefixes: list[str] = []
    for pair in pairs:
        if not isinstance(pair, Mapping):
            raise ValueError(f"sidecar pair entries must be objects: {sidecar_path}")
        prefix = pair.get("sample_prefix")
        if not isinstance(prefix, str) or not prefix:
            raise ValueError(f"sidecar pair sample_prefix must be nonempty: {sidecar_path}")
        prefixes.append(prefix)
    if len(prefixes) != len(set(prefixes)):
        raise ValueError(f"sidecar pair sample_prefix values must be unique: {sidecar_path}")
    expected_keys = {
        prefix + "__" + suffix for prefix in prefixes for suffix in PAIR_ARRAY_SUFFIXES
    }

    rows: list[dict[str, object]] = []
    try:
        with np.load(npz_path, allow_pickle=False) as archive:
            if set(archive.files) != expected_keys:
                missing = sorted(expected_keys - set(archive.files))
                extra = sorted(set(archive.files) - expected_keys)
                raise ValueError(
                    f"NPZ prefix keys mismatch for {sidecar_path}; missing={missing}, extra={extra}"
                )
            arrays = {key: np.asarray(archive[key]).copy() for key in sorted(expected_keys)}
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("NPZ prefix keys mismatch"):
            raise
        raise ValueError(f"could not read NPZ with allow_pickle=False for {sidecar_path}: {exc}") from exc

    for pair in pairs:
        prefix = str(pair["sample_prefix"])
        profile = pair.get("fixed_brightness_profile")
        if not isinstance(profile, Mapping):
            raise ValueError(f"pair fixed_brightness_profile missing: {sidecar_path}")
        if profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
            raise ValueError(f"pair profile schema mismatch: {sidecar_path}")
        if profile.get("raw_pair_schema_version") != RAW_PAIR_SCHEMA_VERSION:
            raise ValueError(f"pair raw schema mismatch: {sidecar_path}")
        try:
            gain_a = float(profile["gain_log_a"])
            gain_b = float(profile["gain_log_b"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"pair gains missing or invalid: {sidecar_path}") from exc
        if not math.isfinite(gain_a) or not math.isfinite(gain_b):
            raise ValueError(f"pair gains must be finite: {sidecar_path}")
        try:
            samples = collect_pair_samples(
                rgb_a=arrays[prefix + "__rgb_a"],
                rgb_b=arrays[prefix + "__rgb_b"],
                erp_flat_index=arrays[prefix + "__erp_flat_index"],
                xy_a=arrays[prefix + "__xy_a"],
                xy_b=arrays[prefix + "__xy_b"],
                depth_m=arrays[prefix + "__depth_m"],
                parallax_deg=arrays[prefix + "__parallax_deg"],
            )
        except ValueError as exc:
            raise ValueError(f"pair array shape/value contract failed for {sidecar_path}: {exc}") from exc
        emitted_n = pair.get("emitted_n")
        if not isinstance(emitted_n, int) or isinstance(emitted_n, bool):
            raise ValueError(f"pair emitted_n must be an integer: {sidecar_path}")
        if emitted_n != len(samples.rgb_a):
            raise ValueError(f"pair emitted_n does not match NPZ shape: {sidecar_path}")
        camera_pair = pair.get("camera_pair")
        row = canonicalize_pair_frame(
            {
                "log_id": sidecar["log_id"],
                "anchor_index": sidecar["anchor_index"],
                "camera_pair": camera_pair,
                "samples": samples,
                "gain_log_a": gain_a,
                "gain_log_b": gain_b,
                "rho_log_luma": pair.get("rho_log_luma"),
                "sat_lo": sidecar["sat_lo"],
                "sat_hi": sidecar["sat_hi"],
                "sample_prefix": prefix,
                "sidecar_path": str(sidecar_path),
                "sample_npz": str(npz_path),
            }
        )
        rows.append(row)
    return rows


def load_verified_sidecars(
    input_root: Path,
    manifest: Mapping[str, object],
    *,
    require_all_selected_logs: bool = True,
    expected_identity_count: int | None = None,
) -> list[dict[str, object]]:
    """Load every frozen sidecar/NPZ or raise; malformed bundles are never skipped."""

    root = input_root.resolve()
    if not root.is_dir():
        raise ValueError(f"input_root is not a directory: {root}")
    normalized = validate_split_manifest(manifest)
    selected_ids = set(normalized["selected_log_ids"])
    anchors = normalized.get("anchors")
    expected_identities = None
    if isinstance(anchors, Mapping):
        expected_identities = {
            (log_id, int(anchor))
            for log_id, values in anchors.items()
            for anchor in values
        }
    helper_sha256 = _expected_helper_sha256(normalized)
    sidecar_paths = sorted(root.rglob("*_color_diag.json"), key=lambda path: path.as_posix())
    if not sidecar_paths and (expected_identities or require_all_selected_logs):
        raise ValueError("no DB-226 color diagnostic sidecars found")

    seen: dict[tuple[str, int], Path] = {}
    rows: list[dict[str, object]] = []
    for sidecar_path in sidecar_paths:
        sidecar = _load_json_object(sidecar_path)
        if sidecar.get("artifact_state") != "complete":
            raise ValueError(f"sidecar artifact_state is not complete: {sidecar_path}")
        if sidecar.get("schema_version") != RAW_PAIR_SCHEMA_VERSION:
            raise ValueError(f"sidecar schema mismatch: {sidecar_path}")
        log_id = sidecar.get("log_id")
        if not isinstance(log_id, str) or log_id not in selected_ids:
            raise ValueError(f"sidecar log_id is not in the frozen selected set: {sidecar_path}")
        anchor = sidecar.get("anchor_index")
        if not isinstance(anchor, int) or isinstance(anchor, bool) or anchor < 0:
            raise ValueError(f"sidecar anchor_index is invalid: {sidecar_path}")
        identity = (log_id, anchor)
        if expected_identities is not None and identity not in expected_identities:
            raise ValueError(f"sidecar anchor is not present in the frozen manifest: {sidecar_path}")
        if identity in seen:
            raise ValueError(
                f"duplicate sidecar for log_id/anchor {identity}: {seen[identity]} and {sidecar_path}"
            )
        seen[identity] = sidecar_path
        try:
            sat_lo = float(sidecar["sat_lo"])
            sat_hi = float(sidecar["sat_hi"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"sidecar saturation bounds are missing or invalid: {sidecar_path}") from exc
        if not math.isfinite(sat_lo) or not math.isfinite(sat_hi) or sat_lo >= sat_hi:
            raise ValueError(f"sidecar saturation bounds must be finite and increasing: {sidecar_path}")
        if sidecar.get("helper_source_sha256") != helper_sha256:
            raise ValueError(f"sidecar helper_source_sha256 mismatch: {sidecar_path}")
        npz_path = _verified_npz_path(sidecar_path, sidecar.get("sample_npz"))
        expected_sample_sha = sidecar.get("sample_sha256")
        if not isinstance(expected_sample_sha, str) or sha256_file(npz_path) != expected_sample_sha:
            raise ValueError(f"sidecar sample_sha256 mismatch: {sidecar_path}")
        _verify_transaction(sidecar, path=sidecar_path)
        if sidecar.get("measurement") != "same_3d_ray_at_curved_ownership_boundary":
            raise ValueError(f"sidecar measurement contract mismatch: {sidecar_path}")
        rows.extend(_load_pair_rows(sidecar, npz_path, sidecar_path=sidecar_path))

    if expected_identities is not None:
        missing = sorted(expected_identities - set(seen))
        if missing:
            raise ValueError(f"missing expected sidecars for frozen manifest anchors: {missing}")
    if require_all_selected_logs:
        missing_logs = sorted(selected_ids - {log_id for log_id, _ in seen})
        if missing_logs:
            raise ValueError(f"missing expected sidecars for selected logs: {missing_logs}")
    if expected_identity_count is not None and len(seen) != expected_identity_count:
        raise ValueError(
            f"expected exactly {expected_identity_count} identities, found {len(seen)}"
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["log_id"]),
            int(row["anchor_index"]),
            tuple(row["camera_pair"]),
        ),
    )


def analyze_rows(
    rows: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Run the registered primary evaluation plus the frozen 4x3 sensitivity grid."""

    normalized = validate_split_manifest(manifest)
    sensitivity = []
    for rho_min in SENSITIVITY_RHO:
        for max_parallax_deg in SENSITIVITY_PARALLAX:
            evaluation = evaluate_profile_transfer(
                    rows,
                    train_log_ids=normalized["train_log_ids"],
                    heldout_log_ids=normalized["heldout_log_ids"],
                    rho_min=rho_min,
                    max_parallax_deg=max_parallax_deg,
                )
            sensitivity.append(
                {
                    "rho_filter": (
                        "all_samples" if rho_min is None else f"rho_gte_{rho_min:.2f}"
                    ),
                    "rho_min": rho_min,
                    "parallax_filter": (
                        "all_parallax"
                        if max_parallax_deg is None
                        else f"parallax_lte_{max_parallax_deg:g}deg"
                    ),
                    "max_parallax_deg": max_parallax_deg,
                    "evaluation": evaluation,
                }
            )
    primary_cell = next(
        cell
        for cell in sensitivity
        if cell["rho_min"] == PRIMARY_CONFIG["rho_min"]
        and cell["max_parallax_deg"] == PRIMARY_CONFIG["max_parallax_deg"]
    )
    primary = primary_cell["evaluation"]
    sensitivity_statuses = [cell["evaluation"]["status"] for cell in sensitivity]
    return {
        "schema_version": "db226.frozen_cross_log_analysis.v1",
        "decision_rule": (
            "primary PASS requires at least three reliable bins and strict majorities of "
            "evaluable heldout pairs and logs for nonlinear-vs-zero; affine is diagnostic only"
        ),
        "primary_config": dict(PRIMARY_CONFIG),
        "primary": primary,
        "sensitivity": sensitivity,
        "sensitivity_stable": len(set(sensitivity_statuses)) == 1,
        "split_assignment_sha256": normalized["split_sha256"],
    }


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-split-manifest-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    manifest_path = args.split_manifest.resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if args.expected_split_manifest_sha256 != manifest_sha256:
        raise ValueError("split manifest file SHA-256 does not match the frozen expected hash")
    try:
        manifest_value = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid split manifest JSON: {manifest_path}") from exc
    normalized = validate_split_manifest(manifest_value, expected_log_count=24)
    rows = load_verified_sidecars(
        args.input_root,
        normalized,
        require_all_selected_logs=True,
        expected_identity_count=72,
    )
    report = analyze_rows(rows, normalized)
    identities = {(str(row["log_id"]), int(row["anchor_index"])) for row in rows}
    report.update(
        {
            "split_manifest_sha256": manifest_sha256,
            "split_assignment_sha256": normalized["split_sha256"],
            "artifact_summary": {
                "log_n": len({log_id for log_id, _ in identities}),
                "anchor_n": len(identities),
                "pair_frame_n": len(rows),
            },
        }
    )
    _write_json_atomic(args.output.resolve(), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
