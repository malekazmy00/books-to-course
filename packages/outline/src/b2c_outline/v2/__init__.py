"""Outline v2 (R4): ParsedBook v2 -> CourseOutline v2, its deterministic gate and reference metrics."""

from .build import (
    BUILDER_NAME, BUILDER_VERSION, DEFAULT_SIZE_POLICY, OutlineConfig, OutlineUnavailable, build_outline_v2,
)

__all__ = ["BUILDER_NAME", "BUILDER_VERSION", "DEFAULT_SIZE_POLICY", "OutlineConfig", "OutlineUnavailable",
           "build_outline_v2"]
