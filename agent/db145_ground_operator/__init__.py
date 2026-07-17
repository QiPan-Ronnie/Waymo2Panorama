"""DB-145: sensor-native ground reconstruction kill test.

This package is deliberately isolated from the production v15 pipeline.  Its
outputs are experimental evidence, never production pixels.
"""

from .config import DEFAULT_CONFIG, ExperimentConfig

__all__ = ["DEFAULT_CONFIG", "ExperimentConfig"]
__version__ = "0.1.0"
