from __future__ import annotations

from typing import Any


def execute_generated_source(
    source: str, *, filename: str = "<generated-source>"
) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "__main__"}
    exec(compile(source, filename, "exec"), namespace, namespace)
    return namespace


__all__ = ["execute_generated_source"]
