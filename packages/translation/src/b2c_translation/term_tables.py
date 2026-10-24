"""Book-specific EN->AR term data used by deterministic translation validators.

These tables were curated for the intelligence-analysis evaluation book and its
rating tables. They are data, not policy: bump TERM_TABLES_VERSION whenever an
entry changes so checkpoints/evidence produced under different tables can be told
apart.
"""
from __future__ import annotations

TERM_TABLES_VERSION = "b2c-translation-term-tables-v1"

# Explicit equivalents of selected Arabic glossary targets (diacritics-free,
# NFKC/casefolded form). Definite forms and noun/adjective realizations preserve
# the selected sense. Do not use root stemming: it would admit unrelated senses.
TERMINOLOGY_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "استخبارات": ("استخبارات", "استخباراتي", "استخباراتية"),
    "تحليل استخباراتي": ("تحليل استخباراتي", "التحليل الاستخباراتي", "تحليل الاستخبارات",
                          "التحليل الاستخباراتي", "للتحليل الاستخباراتي"),
    "تقنيات التحليل المنظم": ("تقنيات التحليل المنظم", "التقنيات التحليلية المنظمة"),
}

# Fixed rating-table cell vocabulary (English cell -> Arabic rendering).
RATING_CELL_GLOSSARY: dict[str, str] = {
    "strong positive and strong negative": "إيجابي قوي وسلبي قوي",
    "medium positive and medium negative": "إيجابي متوسط وسلبي متوسط",
    "weak positive and weak negative": "إيجابي ضعيف وسلبي ضعيف",
    "strong positive": "إيجابي قوي", "strong negative": "سلبي قوي",
    "medium positive": "إيجابي متوسط", "medium negative": "سلبي متوسط",
    "weak positive": "إيجابي ضعيف", "weak negative": "سلبي ضعيف",
    "negligible": "ضئيل", "substantial": "كبير", "moderate": "متوسط",
    "low": "منخفض", "strong": "قوي", "nil": "لا شيء",
}

# Unclear extraction tokens preserved verbatim inside recognized rating tables.
RATING_TABLE_LITERALS: tuple[str, ...] = (
    "Strongpositiveandstrongnegativ", "Mediumpositiveandmediumnegativ", "Weakpositiveandweaknegativ",
    "Strongnegativ", "Mediumnegativ", "Weaknegativ", "Mpamn", "Mn",
)

# Accepted Arabic variants of rating cells (Arabic -> English cell name).
RATING_CELL_ARABIC_ALIASES: dict[str, str] = {
    "منخفضة": "low", "معتدل": "moderate", "معتدلة": "moderate", "متوسطة": "moderate",
    "مهمل": "negligible", "مهملة": "negligible", "ضئيلة": "negligible", "لا يذكر": "negligible", "لايذكر": "negligible",
    "كبيرة": "substantial", "جوهري": "substantial", "جوهريّة": "substantial", "جوهرية": "substantial", "ملحوظ": "substantial",
    "قوية": "strong", "منعدم": "nil",
}
