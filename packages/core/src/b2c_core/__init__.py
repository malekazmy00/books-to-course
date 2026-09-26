"""b2c_core — cross-cutting utilities. Depends only on b2c_contracts."""
from b2c_contracts import stage_key

from .artifacts import ArtifactStore
from .config import Settings
from .logging import configure_logging, get_logger
from .prov import ProvRecorder

__all__ = [
    "ArtifactStore",
    "ProvRecorder",
    "Settings",
    "configure_logging",
    "get_logger",
    "stage_key",
]
