"""B2C course outline construction."""

from .from_book import (
    InferredSection, OutlineCoverage, OutlineUnavailable, outline_from_book,
    validate_outline_coverage,
)

__all__ = [
    "InferredSection", "OutlineCoverage", "OutlineUnavailable",
    "outline_from_book", "validate_outline_coverage",
]
