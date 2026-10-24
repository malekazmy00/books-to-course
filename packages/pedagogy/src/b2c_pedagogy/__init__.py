"""Phase 5 pedagogy utilities; may import contracts, core and LLM gateway only."""

from .qa_generation import (
    CourseQAResult,
    GroundedQA,
    ScopeResult,
    generate_course_qa,
    persist_course_qa,
)

__all__ = ["CourseQAResult", "GroundedQA", "ScopeResult",
           "generate_course_qa", "persist_course_qa"]
