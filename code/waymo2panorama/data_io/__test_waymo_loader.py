"""
Unit tests for waymo_loader.py — Waymo ring-camera loader skeleton (WS1.4).

These tests verify the *skeleton contract* of the WaymoRingLoader handoff:
constants, public API parity with AV2RingLoader, and that the two stub
internals raise NotImplementedError as documented. They do NOT verify any
actual Waymo data parsing (that's the teammate's job once the stubs are filled).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path wiring — allow running from repo root without installation
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_CODE_ROOT = (_HERE / "../../..").resolve()  # code/waymo2panorama/data_io -> code/
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from waymo2panorama.data_io.av2_loader import AV2RingLoader  # noqa: E402
from waymo2panorama.data_io.waymo_loader import (  # noqa: E402
    RING_CAMS_5,
    WaymoRingLoader,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_ring_cams_5_count() -> None:
    """Waymo ring-cam tuple must have exactly 5 entries."""
    assert len(RING_CAMS_5) == 5


def test_ring_cams_5_names() -> None:
    """Names must include the canonical 'FRONT' anchor in upper-case form."""
    assert "FRONT" in RING_CAMS_5
    # All names should be non-empty strings (catches accidental None / int).
    for name in RING_CAMS_5:
        assert isinstance(name, str) and name


# ---------------------------------------------------------------------------
# Class existence + API parity
# ---------------------------------------------------------------------------


def test_waymo_loader_class_exists() -> None:
    """Smoke test: the import worked and we got a class object."""
    assert inspect.isclass(WaymoRingLoader)


def _public_methods(cls: type) -> set[str]:
    """Return the set of non-underscore callable attribute names on ``cls``."""
    return {
        name
        for name, member in inspect.getmembers(cls, callable)
        if not name.startswith("_")
    }


def test_waymo_loader_api_matches_av2() -> None:
    """Public method set of WaymoRingLoader must equal AV2RingLoader's.

    This is a guardrail against API drift: any future addition to AV2RingLoader
    that downstream code uses must be mirrored here (or vice versa).
    """
    av2_api = _public_methods(AV2RingLoader)
    waymo_api = _public_methods(WaymoRingLoader)
    assert waymo_api == av2_api, (
        f"public API drift:\n"
        f"  only in AV2:   {sorted(av2_api - waymo_api)}\n"
        f"  only in Waymo: {sorted(waymo_api - av2_api)}"
    )


# ---------------------------------------------------------------------------
# Stub behaviour
# ---------------------------------------------------------------------------


def test_load_calibrations_raises_not_implemented(tmp_path: Path) -> None:
    """``__init__`` calls ``_load_calibrations`` -> stub must raise.

    We use a real (empty) temp dir so the ``exists()`` precondition passes
    and we actually reach the stub.
    """
    with pytest.raises(NotImplementedError):
        WaymoRingLoader(tmp_path)


def test_index_images_raises_not_implemented() -> None:
    """``_index_images`` stub must also raise.

    ``__init__`` would normally fail at ``_load_calibrations`` first, so we
    bypass init entirely with ``object.__new__`` and call the stub directly.
    Once the teammate fills ``_load_calibrations``, ``_index_images`` becomes
    reachable through the normal path; this test still guards it independently.
    """
    obj = object.__new__(WaymoRingLoader)
    with pytest.raises(NotImplementedError):
        obj._index_images()
