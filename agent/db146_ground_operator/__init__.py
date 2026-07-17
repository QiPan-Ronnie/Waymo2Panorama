"""DB-146: cross-validated, evidence-gated sensor-native ground inverse."""

from .gate import BAND_SPECS, GateDecision, select_safe_band

__all__ = ["BAND_SPECS", "GateDecision", "select_safe_band"]
__version__ = "0.1.0"
