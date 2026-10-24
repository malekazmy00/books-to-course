"""Fasih-specific CPU threading bootstrap.

Call configure_fasih_cpu_threads once at worker startup, before loading the
Fasih/XTTS checkpoint or starting parallel Torch work. The speech package does
not yet ship a Fasih provider, so this helper has no import-time side effects
and does not change other Torch-based services.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FasihCpuThreadConfig:
    intra_op_threads: int = 4
    inter_op_threads: int = 4

    def __post_init__(self) -> None:
        if self.intra_op_threads < 1 or self.inter_op_threads < 1:
            raise ValueError("Fasih CPU thread counts must be positive integers")

    @classmethod
    def from_environment(cls) -> "FasihCpuThreadConfig":
        """Read Fasih-only overrides; defaults match the measured 4/4 run."""
        try:
            intra = int(os.getenv("B2C_FASIH_INTRA_OP_THREADS", "4"))
            inter = int(os.getenv("B2C_FASIH_INTER_OP_THREADS", "4"))
        except ValueError as exc:
            raise ValueError("Fasih thread overrides must be integers") from exc
        return cls(intra_op_threads=intra, inter_op_threads=inter)


def configure_fasih_cpu_threads(
    config: FasihCpuThreadConfig | None = None,
    *,
    torch_module: Any | None = None,
) -> FasihCpuThreadConfig:
    """Apply the Fasih-only CPU thread configuration before model loading."""
    if config is None:
        config = FasihCpuThreadConfig.from_environment()
    if torch_module is None:
        import torch as torch_module

    torch_module.set_num_threads(config.intra_op_threads)
    torch_module.set_num_interop_threads(config.inter_op_threads)
    return config
