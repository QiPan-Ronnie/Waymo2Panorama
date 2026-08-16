from __future__ import annotations

from typing import Any, Mapping


def require_successful_batch(
    namespace: Mapping[str, Any], *, expected_cases: int
) -> dict[str, Any]:
    out = namespace.get("OUT")
    if not isinstance(out, dict):
        raise RuntimeError("generated renderer did not expose OUT")
    if out.get("status") != "db89_completed":
        raise RuntimeError(f"DB89 status is not completed: {out.get('status')!r}")
    cases = out.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("DB89 OUT.cases is not a list")
    if len(cases) != expected_cases:
        raise RuntimeError(f"expected {expected_cases} cases, got {len(cases)}")
    failures = [
        f"{case.get('case', '<unknown>')}: {case.get('error')}"
        for case in cases
        if isinstance(case, dict) and case.get("error")
    ]
    if failures:
        raise RuntimeError("DB89 case failures: " + "; ".join(failures))
    ownership_failures = [
        str(case.get("case", "<unknown>"))
        for case in cases
        if not isinstance(case, dict) or case.get("n_objects_composited") != 0
    ]
    if ownership_failures:
        raise RuntimeError(
            "raw-sensor ownership was not proven for cases: "
            + ", ".join(ownership_failures)
        )
    return out


__all__ = ["require_successful_batch"]
