"""Deterministic structural/provenance checks for synthesized lessons.

The implementation lives in :mod:`b2c_core.provenance_checks` so that other layer
packages (e.g. pedagogy) can share the same pure validators without importing
another layer. This module re-exports it for backward compatibility.
"""
from __future__ import annotations

from b2c_core.provenance_checks import (
    Finding,
    ValidationReport,
    detect_explicit_polarity_conflicts,
    validate_citations,
)

__all__ = [
    "Finding",
    "ValidationReport",
    "detect_explicit_polarity_conflicts",
    "validate_citations",
]
