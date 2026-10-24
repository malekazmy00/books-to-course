"""Opt-in Qwen/DeepSeek vision OCR for sparse or scanned PDF pages."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

import pypdf
from pypdf import PdfReader

from b2c_contracts import Block, BookUpload, PageRef, ParsedBook
from b2c_llm_gateway import LLMGateway

from .pdf_book import _HEADING, _outline_entries, _paragraphs, UnsupportedPDF

OCR_PROMPT_VERSION = "vision-ocr-markdown-v1"
SUPPORTED_MODELS = {"qwen/qwen3.8-27b:free", "deepseek/deepseek-v4-flash-vision-exp"}


def _page_png(pdfium, page_no: int) -> bytes:
    page = pdfium[page_no - 1]
    image = page.render(scale=3.0).to_pil()
    out = BytesIO()
    image.save(out, format="PNG")
    image.close()
    page.close()
    return out.getvalue()


def _markdown_kind(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and any("|" in line for line in lines[:4]) and any(
        re.fullmatch(r"\|?[:\- |]+\|?", line) for line in lines[:4]
    ):
        return "table"
    return "paragraph"


def parse_pdf_with_openrouter(upload: BookUpload, source: str | Path, *, gateway: LLMGateway,
                              min_page_chars: int = 8, max_tokens: int = 12000,
                              gemini_api_key: str | None = None) -> ParsedBook:
    """Keep native PDF text local; call the selected tested vision model once per scanned page."""
    try:
        import pypdfium2
    except ImportError as exc:
        raise RuntimeError("OpenRouter page rendering requires pypdfium2") from exc
    path = Path(source)
    if upload.mime != "application/pdf" or not path.is_file():
        raise UnsupportedPDF("OpenRouter OCR adapter needs an existing PDF")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest.lower() != upload.file_sha256.lower():
        raise UnsupportedPDF("uploaded PDF SHA-256 does not match upload metadata")
    if gateway.provider_name != "openrouter" or gateway.model_id not in SUPPORTED_MODELS:
        raise ValueError("OpenRouter OCR requires the tested Qwen free or DeepSeek vision model profile")
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise UnsupportedPDF("password-protected PDFs are not supported")
    # Gemini remains narrowly scoped to table crops: Docling identifies table regions;
    # those pages reuse its structured blocks and are not sent to the vision OCR model.
    table_page_blocks: dict[int, list[Block]] = {}
    layout_blocks_by_page: dict[int, list[Block]] = {}
    if gemini_api_key:
        from .docling_pdf import parse_pdf_with_docling
        layout_book = parse_pdf_with_docling(upload, path, gemini_api_key=gemini_api_key)
        for block in layout_book.blocks:
            for ref in block.refs:
                layout_blocks_by_page.setdefault(ref.page_no, []).append(block)
                if block.type == "table":
                    table_page_blocks.setdefault(ref.page_no, []).append(block)

    outlines = _outline_entries(reader)
    outline_by_page = {}
    for page0, depth, title in outlines:
        outline_by_page.setdefault(page0 + 1, []).append((depth + 1, title))

    blocks: list[Block] = []
    section_path: list[str] = []
    sequences: dict[int, int] = {}
    pdfium = pypdfium2.PdfDocument(str(path))
    try:
        for page_no, page in enumerate(reader.pages, 1):
            native = (page.extract_text() or "").strip()
            if page_no in table_page_blocks:
                blocks.extend(layout_blocks_by_page[page_no])
                continue
            for level, title in outline_by_page.get(page_no, []):
                section_path = section_path[:max(0, level - 1)] + [title]
                seq = sequences.get(page_no, 0)
                blocks.append(Block(block_id=f"{upload.book_id}:{page_no}:{seq}", type="heading",
                    level=level, text=title, refs=[PageRef(book_id=upload.book_id, page_no=page_no)],
                    section_path=section_path.copy(), ocr=False))
                sequences[page_no] = seq + 1
            if len("".join(native.split())) < min_page_chars:
                image = _page_png(pdfium, page_no)
                image_hash = hashlib.sha256(image).hexdigest()
                result = gateway.chat(
                    [{"role": "user", "content": [
                        {"type": "text", "text": (
                            f"OCR the complete image for physical PDF page {page_no}. Transcribe exactly; "
                            "preserve reading order, headings, lists, tables as Markdown, equations, and "
                            "all visible text. Do not translate, summarize, or omit content.")},
                        {"type": "image_url", "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")}},
                    ]}],
                    prompt_version=f"{OCR_PROMPT_VERSION}:page:{page_no}:sha256:{image_hash}",
                    max_tokens=max_tokens,
                )
                text = result.text.strip()
                if not text:
                    raise ValueError(f"OpenRouter OCR returned empty output for page {page_no}")
                kind = _markdown_kind(text)
                seq = sequences.get(page_no, 0)
                blocks.append(Block(block_id=f"{upload.book_id}:{page_no}:{seq}", type=kind,
                    text=text, refs=[PageRef(book_id=upload.book_id, page_no=page_no)],
                    section_path=section_path.copy(), ocr=True))
                sequences[page_no] = seq + 1
            else:
                for text in _paragraphs(native):
                    level = 1 if len(text) <= 140 and _HEADING.match(text) else None
                    kind = "heading" if level else "paragraph"
                    if level:
                        section_path = section_path[:max(0, level - 1)] + [text]
                    seq = sequences.get(page_no, 0)
                    blocks.append(Block(block_id=f"{upload.book_id}:{page_no}:{seq}", type=kind,
                        level=level, text=text, refs=[PageRef(book_id=upload.book_id, page_no=page_no)],
                        section_path=section_path.copy(), ocr=False))
                    sequences[page_no] = seq + 1
    finally:
        pdfium.close()
    if not blocks:
        raise ValueError("PDF contained no extractable text and OpenRouter returned no OCR blocks")
    options = {"model": gateway.model_id, "prompt": OCR_PROMPT_VERSION, "max_tokens": max_tokens,
               "page_policy": "one-image-per-request; cached by image hash and physical page",
               "table_backend": "gemini-table-crops" if gemini_api_key else "vision-markdown",
               "pypdf": pypdf.__version__}
    return ParsedBook(book_id=upload.book_id, title=upload.title, role=upload.role,
        page_count=len(reader.pages), toc_source="pdf_outline" if outlines else "headings", blocks=blocks,
        parser={"name": "openrouter-vision-ocr", "version": OCR_PROMPT_VERSION,
                "options_hash": hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()})
