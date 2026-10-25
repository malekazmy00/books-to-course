"""b2c_core — cross-cutting utilities. Depends only on b2c_contracts."""
from b2c_contracts import stage_key

from .artifacts import ArtifactStore
from .config import Settings
from .logging import configure_logging, get_logger
from .prov import ProvRecorder
from .revision import code_revision
from .stage_registry import StageOutputRegistry

__all__ = [
    "ArtifactStore",
    "ProvRecorder",
    "Settings",
    "StageOutputRegistry",
    "code_revision",
    "configure_logging",
    "get_logger",
    "stage_key",
]
