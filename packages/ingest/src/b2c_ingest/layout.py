"""S1 (R3): lossless parse intermediates that S2 normalization re-runs from without re-parsing.

Two intermediates, both plain JSON:

* ``pdf_text_layer`` — the PDF's own text layer, page by page, extracted twice by independent
  engines: pypdf (pure Python, ``text``) and pdfium (Chromium's PDF engine via pypdfium2,
  ``text_pdfium``), with the pdfium bounding boxes of raster images (``image_boxes``, top-left
  origin like ``PageRef.bbox``), the page labels (ISO 32000-1 §12.4.2) and the bookmark tree
  (title, depth, page, /XYZ top). pypdf text is the input of the text-layer normalization path;
  both extractions are the references of the cross-parser quality gate (D-040). Docling reads
  text through docling-parse, a third engine.
* ``docling_layout_batches`` — Docling's full ``DoclingDocument`` export per page batch,
  every content layer included (body *and* furniture: running heads, page numbers), each
  item with its page and bbox provenance. Batches are independent, so the caller can store
  each one as a checkpoint and resume after a worker restart (audit 01 M4).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

import pypdf
from pypdf import PdfReader

TEXT_LAYER_FORMAT = "pdf-text-layer/2"  # /2: text_pdfium, image_boxes (D-040)
LAYOUT_FORMAT = "docling-document-batch/1"
DEFAULT_PAGE_BATCH = 50


def _outline(reader: PdfReader) -> list[dict[str, Any]]:
    """Bookmarks in tree order: title, depth (0 = top), zero-based page, /XYZ top if any."""
    out: list[dict[str, Any]] = []

    def walk(items: list[Any], depth: int) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = re.sub(r"\s+", " ", str(getattr(item, "title", "") or "")).strip()
            try:
                page = reader.get_destination_page_number(item)
            except (ValueError, TypeError, IndexError, KeyError):
                continue
            if not title or page is None or page < 0:
                continue
            top = getattr(item, "top", None)
            try:
                top = float(top) if top is not None else None
            except (TypeError, ValueError):
                top = None
            out.append({"title": title, "depth": depth, "page_index": page, "top": top})

    try:
        walk(reader.outline, 0)
    except (AttributeError, TypeError, KeyError):
        return []
    return out


def _pdfium_pages(source: str | Path) -> tuple[str | None, list[tuple[str, list[list[float]]]] | None]:
    """pdfium text and raster-image boxes per page; (None, None) when pypdfium2 is not installed
    (it ships with the b2c-ingest[docling] extra of the worker image). The quality gate then
    records a single reference and fails its ``independent_references`` check."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except ImportError:
        return None, None
    from importlib.metadata import version

    pages: list[tuple[str, list[list[float]]]] = []
    pdf = pdfium.PdfDocument(str(source))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                text = page.get_textpage().get_text_range()
            except Exception:  # a broken content stream must not hide the other pages
                text = ""
            height = page.get_height()
            boxes = []
            try:
                for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
                    left, bottom, right, top = obj.get_bounds()
                    boxes.append([round(left, 2), round(height - top, 2), round(right, 2), round(height - bottom, 2)])
            except Exception:
                boxes = []
            pages.append((text, boxes))
    finally:
        pdf.close()
    return version("pypdfium2"), pages


def pdf_text_layer(source: str | Path) -> dict[str, Any]:
    """Read the PDF text layer (pypdf and pdfium), labels and bookmarks into a JSON document."""
    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("password-protected PDFs are not supported")
    has_labels = "/PageLabels" in reader.trailer["/Root"]
    labels = list(reader.page_labels) if has_labels else [None] * len(reader.pages)
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # a broken content stream must not hide the other pages
            text = ""
        try:
            has_images = bool(page.images)
        except Exception:
            has_images = False
        box = page.mediabox
        pages.append({"page_no": i + 1, "label": labels[i], "text": text, "has_images": has_images,
                      "width": float(box.width), "height": float(box.height)})
    pdfium_version, pdfium_pages = _pdfium_pages(source)
    if pdfium_pages is not None and len(pdfium_pages) == len(pages):
        for page, (text, boxes) in zip(pages, pdfium_pages):
            page["text_pdfium"] = text
            page["image_boxes"] = boxes
    return {"format": TEXT_LAYER_FORMAT, "pypdf_version": pypdf.__version__, "pdfium_version": pdfium_version,
            "page_count": len(pages), "has_page_labels": has_labels,
            "outline": _outline(reader), "pages": pages}


def docling_options(*, ocr_language: str = "en", num_threads: int = 8) -> dict[str, Any]:
    """The layout configuration; it is part of the stage identity."""
    return {"backend": "docling", "do_ocr": True, "ocr_engine": "rapidocr-onnxruntime",
            "ocr_language": ocr_language, "ocr_text_score": 0.35, "ocr_scale": 3.0,
            "do_table_structure": True, "content_layers": "all", "num_threads": num_threads}


def docling_versions() -> dict[str, str]:
    from importlib.metadata import version

    return {name: version(name) for name in ("docling", "docling-core", "docling-ibm-models")}


def batch_ranges(page_count: int, page_batch: int = DEFAULT_PAGE_BATCH) -> list[tuple[int, int]]:
    if page_batch < 1:
        raise ValueError("page_batch must be >= 1")
    return [(a, min(page_count, a + page_batch - 1)) for a in range(1, page_count + 1, page_batch)]


def batch_name(first: int, last: int) -> str:
    return f"layout/pages-{first:04d}-{last:04d}.json"


def _converter(ocr_language: str, num_threads: int):
    from .docling_pdf import DoclingUnavailable

    try:
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise DoclingUnavailable("install the b2c-ingest[docling] extra in the worker image") from exc
    if ocr_language != "en":
        raise ValueError("the v2 layout path supports ocr_language='en' only; Arabic stays on v1")
    cfg = docling_options(ocr_language=ocr_language, num_threads=num_threads)
    options = PdfPipelineOptions()
    options.do_ocr = cfg["do_ocr"]
    options.ocr_options = RapidOcrOptions(lang=["en"], backend="onnxruntime",
                                          text_score=cfg["ocr_text_score"], scale=cfg["ocr_scale"])
    options.do_table_structure = cfg["do_table_structure"]
    options.accelerator_options = AcceleratorOptions(num_threads=num_threads, device="cpu")
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def _convert(converter: Any, source: str | Path, first: int, last: int) -> bytes:
    result = converter.convert(Path(source), page_range=(first, last))
    payload = result.document.export_to_dict()
    payload["b2c_batch"] = {"format": LAYOUT_FORMAT, "first_page": first, "last_page": last}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def docling_layout_batch(source: str | Path, first: int, last: int, *, ocr_language: str = "en",
                         num_threads: int = 8) -> bytes:
    """One page batch [first, last] (1-based, inclusive) as DoclingDocument JSON."""
    return _convert(_converter(ocr_language, num_threads), source, first, last)


def docling_layout_batches(
    source: str | Path, *, page_count: int, ocr_language: str = "en", num_threads: int = 8,
    page_batch: int = DEFAULT_PAGE_BATCH, done: Iterable[str] = (),
) -> Iterator[tuple[str, bytes]]:
    """Run Docling over every page, one batch at a time; yield (name, DoclingDocument JSON).

    Batches whose name is in ``done`` are skipped (resume). Arabic OCR keeps the existing
    ``parse_pdf_with_docling`` path; this layout path is for left-to-right books.
    """
    converter = _converter(ocr_language, num_threads)
    skip = set(done)
    for first, last in batch_ranges(page_count, page_batch):
        name = batch_name(first, last)
        if name not in skip:
            yield name, _convert(converter, source, first, last)
