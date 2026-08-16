from __future__ import annotations

from agent.db181_multids.generated_source_exec import execute_generated_source


def test_generated_import_is_visible_to_generated_function_globals() -> None:
    source = "\n".join(
        (
            "import math as np",
            "def erp_dirs():",
            "    return np.sqrt(4.0)",
            "RESULT = erp_dirs()",
        )
    )
    namespace = execute_generated_source(source, filename="<test-generated>")
    assert namespace["RESULT"] == 2.0


def test_generated_source_runs_as_main() -> None:
    namespace = execute_generated_source("RESULT = __name__")
    assert namespace["RESULT"] == "__main__"
