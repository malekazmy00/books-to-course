"""B2C ingestion adapters."""

from .docling_pdf import DoclingUnavailable, parse_pdf_with_docling
from .openrouter_ocr import parse_pdf_with_openrouter
from .pdf_book import OCRRequired, UnsupportedPDF, parse_pdf_book
from .surya_ocr import SuryaUnavailable, parse_pdf_with_surya

__all__ = [
    "OCRRequired", "UnsupportedPDF", "parse_pdf_book",
    "DoclingUnavailable", "parse_pdf_with_docling",
    "SuryaUnavailable", "parse_pdf_with_surya", "parse_pdf_with_openrouter",
]
