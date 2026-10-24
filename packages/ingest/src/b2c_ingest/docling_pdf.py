"""Docling-backed OCR fallback for scanned and sparse PDF pages."""
from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
from io import BytesIO
from pathlib import Path

import pypdf
from pypdf import PdfReader

from b2c_contracts import Block, BookUpload, PageRef, ParsedBook

from .gemini_tables import DEFAULT_GEMINI_TABLE_MODEL, GeminiTableError, transcribe_table_png

log = logging.getLogger(__name__)


def _restore_logical_arabic_order(text: str) -> str:
    """Convert visual-order Arabic to logical order without crossing line boundaries."""
    from bidi.algorithm import get_display

    return "\n".join(get_display(line) if line else line for line in text.splitlines())


def _bbox_top_left(bbox: object, page_height: float) -> tuple[float, float, float, float]:
    left, right = float(bbox.l), float(bbox.r)
    top, bottom = float(bbox.t), float(bbox.b)
    origin = str(getattr(getattr(bbox, "coord_origin", None), "value", "")).upper()
    if origin.endswith("BOTTOMLEFT"):
        top, bottom = page_height - top, page_height - bottom
    return min(left, right), min(top, bottom), max(left, right), max(top, bottom)


def _render_table_crop_png(source: Path, page_no: int, bbox: object, page_height: float, scale: float = 3.0) -> bytes:
    """Render only the Docling-detected table region for the remote table reader."""
    try:
        from pypdfium2 import PdfDocument
    except ImportError as exc:
        raise DoclingUnavailable("table crop rendering requires pypdfium2") from exc

    pdf = PdfDocument(str(source))
    try:
        page = pdf[page_no - 1]
        image = page.render(scale=scale).to_pil()
        left, top, right, bottom = _bbox_top_left(bbox, page_height)
        padding = round(8 * scale)
        crop = image.crop((
            max(0, round(left * scale) - padding),
            max(0, round(top * scale) - padding),
            min(image.width, round(right * scale) + padding),
            min(image.height, round(bottom * scale) + padding),
        ))
        image.close()
        if crop.width < 8 or crop.height < 8:
            crop.close()
            raise ValueError("detected table crop is empty or too small")
        output = BytesIO()
        crop.save(output, format="PNG")
        crop.close()
        page.close()
        return output.getvalue()
    finally:
        pdf.close()


def _ocr_cluster_for_item(item_bbox: object, page_height: float, clusters: list[object]) -> object | None:
    item_box = _bbox_top_left(item_bbox, page_height)
    item_area = max(1.0, (item_box[2] - item_box[0]) * (item_box[3] - item_box[1]))
    best: tuple[float, object] | None = None
    for cluster in clusters:
        if not any(getattr(cell, "from_ocr", False) for cell in getattr(cluster, "cells", [])):
            continue
        cluster_box = _bbox_top_left(cluster.bbox, page_height)
        width = max(0.0, min(item_box[2], cluster_box[2]) - max(item_box[0], cluster_box[0]))
        height = max(0.0, min(item_box[3], cluster_box[3]) - max(item_box[1], cluster_box[1]))
        coverage = width * height / item_area
        if best is None or coverage > best[0]:
            best = coverage, cluster
    return best[1] if best is not None and best[0] >= 0.25 else None


def _logical_arabic_lines_for_item(
    predictions: list[tuple[object, str]], item_bbox: object, page_height: float, scale: float
) -> list[str]:
    """Select OCR line boxes inside a Docling region and restore each line separately."""
    from bidi.algorithm import get_display

    item_box = _bbox_top_left(item_bbox, page_height)
    selected: list[tuple[float, float, str]] = []
    for polygon, text in predictions:
        if not text.strip():
            continue
        xs = [float(point[0]) / scale for point in polygon]
        ys = [float(point[1]) / scale for point in polygon]
        line_box = min(xs), min(ys), max(xs), max(ys)
        line_area = max(1.0, (line_box[2] - line_box[0]) * (line_box[3] - line_box[1]))
        width = max(0.0, min(item_box[2], line_box[2]) - max(item_box[0], line_box[0]))
        height = max(0.0, min(item_box[3], line_box[3]) - max(item_box[1], line_box[1]))
        center_x = (line_box[0] + line_box[2]) / 2
        center_y = (line_box[1] + line_box[3]) / 2
        if (item_box[0] <= center_x <= item_box[2] and item_box[1] <= center_y <= item_box[3]) or width * height / line_area >= 0.5:
            selected.append((center_y, center_x, text.strip()))

    # For tied baselines (e.g. two columns), Arabic reading order starts at the right.
    selected.sort(key=lambda line: (round(line[0], 1), -line[1]))
    return [logical for _, _, visual in selected if (logical := get_display(visual).strip())]


def _run_arabic_ocr_by_page(source: Path, converter: object, page_numbers: set[int]) -> tuple[dict[int, list[tuple[object, str]]], float]:
    if not page_numbers:
        return {}, 3.0
    try:
        import numpy as np
        from pypdfium2 import PdfDocument
    except ImportError as exc:
        raise DoclingUnavailable("Arabic OCR line reconstruction requires numpy and pypdfium2") from exc

    pipelines = getattr(converter, "initialized_pipelines", {})
    pipeline = next(iter(pipelines.values()), None)
    model = getattr(pipeline, "ocr_model", None)
    reader = getattr(model, "reader", None)
    if reader is None:
        raise DoclingUnavailable("Docling did not expose its configured RapidOCR line reader")
    scale = float(model.scale)
    pdf = PdfDocument(str(source))
    results: dict[int, list[tuple[object, str]]] = {}
    try:
        for page_no in sorted(page_numbers):
            pdf_page = pdf[page_no - 1]
            image = pdf_page.render(scale=scale).to_pil()
            output = reader(
                np.asarray(image),
                use_det=model.options.use_det,
                use_cls=model.options.use_cls,
                use_rec=model.options.use_rec,
            )
            boxes = getattr(output, "boxes", None)
            texts = getattr(output, "txts", None)
            results[page_no] = (
                [(box, text) for box, text in zip(boxes, texts) if text and text.strip()]
                if boxes is not None and texts is not None else []
            )
            image.close()
            pdf_page.close()
    finally:
        pdf.close()
    return results, scale


TABLE_BACKEND_DOCLING = "docling-tableformer"


def _table_backend_identity(outcomes: list[tuple[int, int, str]], gemini_enabled: bool) -> tuple[str, dict]:
    """Summarise which backend actually produced each table.

    Returns (summary, config fields). Without Gemini the identity is unchanged
    ("docling-tableformer", no per-table list). With Gemini enabled, every table
    records its real outcome so a fallback to Docling is never reported as Gemini.
    """
    if not gemini_enabled:
        return TABLE_BACKEND_DOCLING, {"table_backend": TABLE_BACKEND_DOCLING}
    backends = sorted({backend for _, _, backend in outcomes})
    if not backends:
        summary = "none"
    elif len(backends) == 1:
        summary = backends[0]
    else:
        counts = {backend: sum(1 for *_, b in outcomes if b == backend) for backend in backends}
        summary = "mixed:" + ",".join(f"{backend}={counts[backend]}" for backend in backends)
    return summary, {
        "table_backend": summary,
        "table_backend_requested": DEFAULT_GEMINI_TABLE_MODEL,
        "table_backend_outcomes": [f"{page}:{seq}:{backend}" for page, seq, backend in outcomes],
    }


class DoclingUnavailable(RuntimeError):
    """The OCR extra is not installed in this worker image."""


def parse_pdf_with_docling(
    upload: BookUpload, source: str | Path, *, min_page_chars: int = 8,
    gemini_api_key: str | None = None,
) -> ParsedBook:
    """Convert scanned/sparse PDF pages while keeping Docling page provenance."""
    path = Path(source)
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        import docling
    except ImportError as exc:
        raise DoclingUnavailable("install the b2c-ingest[docling] extra in the worker image") from exc

    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("file does not have a PDF signature")
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("password-protected PDFs are not supported")
    native_text = [(page.extract_text() or "").strip() for page in reader.pages]
    ocr_pages = {
        i + 1 for i, text in enumerate(native_text)
        if len("".join(text.split())) < min_page_chars
    }

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(
        lang=["arabic" if upload.ocr_language == "ar" else "en"],
        backend="onnxruntime",
        text_score=0.35,
        scale=3.0,
    )
    options.do_table_structure = True
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    converted = converter.convert(path)
    document = converted.document
    page_info = {int(page.page_no): page for page in converted.pages}
    ocr_clusters_by_page: dict[int, list[object]] = {}
    for page_no, page in page_info.items():
        layout = getattr(page.predictions, "layout", None)
        clusters = getattr(layout, "clusters", []) if layout is not None else []
        text_clusters = [
            cluster for cluster in clusters
            if str(getattr(getattr(cluster, "label", None), "value", "")).lower() == "text"
        ]
        ocr_clusters_by_page[page_no] = text_clusters
        if any(getattr(cell, "from_ocr", False) for cluster in text_clusters for cell in cluster.cells):
            ocr_pages.add(page_no)

    arabic_predictions: dict[int, list[tuple[object, str]]] = {}
    ocr_scale = 3.0
    if upload.ocr_language == "ar" and ocr_pages:
        arabic_predictions, ocr_scale = _run_arabic_ocr_by_page(path, converter, ocr_pages)

    blocks: list[Block] = []
    sections: list[str] = []
    page_sequences: dict[int, int] = {}
    table_outcomes: list[tuple[int, int, str]] = []
    headings = 0

    for item, depth in document.iterate_items(traverse_pictures=True):
        label = getattr(getattr(item, "label", None), "value", str(getattr(item, "label", ""))).lower()
        text = getattr(item, "text", None)
        if label == "table" and hasattr(item, "export_to_markdown"):
            text = item.export_to_markdown(document)
        text = str(text or "").strip()
        provenance = getattr(item, "prov", None) or []
        page_no = int(getattr(provenance[0], "page_no", 0)) if provenance else 0
        if page_no < 1:
            continue
        table_model_used = False
        if label == "table" and gemini_api_key and provenance:
            bbox = getattr(provenance[0], "bbox", None)
            page = page_info.get(page_no)
            if bbox is not None and page is not None:
                try:
                    crop_png = _render_table_crop_png(
                        path, page_no, bbox, float(page.size.height), ocr_scale
                    )
                    text, usage = transcribe_table_png(crop_png, gemini_api_key)
                    table_model_used = True
                    log.info("Gemini table transcription completed", extra={
                        "page_no": page_no, "prompt_tokens": usage["prompt_tokens"],
                        "output_tokens": usage["output_tokens"],
                    })
                except (GeminiTableError, ValueError, OSError) as exc:
                    log.warning("Gemini table transcription failed; keeping Docling table", extra={
                        "page_no": page_no, "reason": str(exc),
                    })
        if not text:
            continue
        if upload.ocr_language == "ar" and label != "table" and provenance:
            item_bbox = getattr(provenance[0], "bbox", None)
            page = page_info.get(page_no)
            if item_bbox is not None and page is not None:
                cluster = _ocr_cluster_for_item(
                    item_bbox, float(page.size.height), ocr_clusters_by_page.get(page_no, [])
                )
                if cluster is not None:
                    lines = _logical_arabic_lines_for_item(
                        arabic_predictions.get(page_no, []), item_bbox,
                        float(page.size.height), ocr_scale,
                    )
                    if lines:
                        text = "\n".join(lines)
                    elif "\n" in text:
                        text = _restore_logical_arabic_order(text)
                    elif any("ARABIC" in unicodedata.name(char, "") for char in text):
                        raise ValueError(
                            f"Arabic OCR reading order could not be verified on page {page_no}; "
                            "line-level OCR output did not overlap the Docling text region."
                        )
        block_type = {
            "title": "heading", "section_header": "heading", "text": "paragraph",
            "list_item": "list_item", "table": "table", "picture": "figure",
            "caption": "caption", "formula": "equation", "code": "code",
            "footnote": "footnote", "page_header": "page_header", "page_footer": "page_footer",
        }.get(label, "paragraph")
        level = max(1, int(depth) + 1) if block_type == "heading" else None
        if block_type == "heading":
            sections = sections[:level - 1]
            sections.append(text)
            headings += 1
        seq = page_sequences.get(page_no, 0)
        page_sequences[page_no] = seq + 1
        if block_type == "table":
            table_outcomes.append((page_no, seq, DEFAULT_GEMINI_TABLE_MODEL if table_model_used
                                   else TABLE_BACKEND_DOCLING))
        blocks.append(Block(
            block_id=f"{upload.book_id}:{page_no}:{seq}", type=block_type,
            level=level, text=text,
            refs=[PageRef(book_id=upload.book_id, page_no=page_no)],
            section_path=sections.copy(), ocr=page_no in ocr_pages or table_model_used,
        ))
    if not blocks:
        raise ValueError("Docling produced no text blocks")
    table_backend, table_config = _table_backend_identity(table_outcomes, bool(gemini_api_key))
    config = {"backend": "docling", "do_ocr": True, "ocr_language": upload.ocr_language,
              "ocr_backend": "onnxruntime",
              "arabic_rtl_order": "visual-to-logical-ocr-lines-v3" if upload.ocr_language == "ar" else "none",
              "min_page_chars": min_page_chars,
              **table_config,
              "docling_version": docling.__version__, "pypdf_version": pypdf.__version__}
    return ParsedBook(
        book_id=upload.book_id, title=upload.title, role=upload.role,
        page_count=len(reader.pages), toc_source="headings" if headings else "pdf_outline",
        blocks=blocks,
        parser={"name": "docling-ocr", "version": docling.__version__,
                "options_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                # Docling-only parses keep their previous identity exactly.
                **({"table_backend": table_backend} if gemini_api_key else {})},
    )

