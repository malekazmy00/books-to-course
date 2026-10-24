"""Surya 2 local inference client and scanned-PDF parser."""
from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import logging
import re
import time
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pypdf
from pypdf import PdfReader
from b2c_contracts import Block, BookUpload, PageRef, ParsedBook
from .pdf_book import _HEADING, _outline_entries, _paragraphs, UnsupportedPDF

log = logging.getLogger(__name__)
SURYA_PROMPT = "OCR this image to HTML. Each block is a div with data-label and data-bbox (x0 y0 x1 y1, normalized 0-1000)."
DEFAULT_SURYA_MODEL = "/data/models/phase2-ocr-candidates/surya-ocr-2-gguf/surya-2.gguf"


class SuryaUnavailable(RuntimeError):
    """Local Surya request failed or returned incomplete OCR."""


def validate_local_url(base_url: str) -> None:
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    if parsed.scheme != "http" or not host or parsed.username or parsed.password:
        raise ValueError("Surya requires a local HTTP URL without embedded credentials")
    if host in {"localhost", "host.docker.internal", "surya-ocr"}:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Surya URL must use localhost, host.docker.internal, or a private IP") from exc
    if address.is_loopback:
        return
    if isinstance(address, ipaddress.IPv4Address):
        local_ranges = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    else:
        local_ranges = ("fc00::/7",)
    if not any(address in ipaddress.ip_network(net) for net in local_ranges):
        raise ValueError("Public OCR endpoints are not allowed for the local Surya path")


class _HtmlBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.current = None
        self.depth = 0
        self.parts = []
        self.level = 1

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and self.current is None and attrs.get("data-label"):
            self.current = (attrs.get("data-label") or "Text", attrs.get("data-bbox") or "")
            self.depth, self.parts, self.level = 1, [], 1
        elif self.current is not None:
            if tag == "div":
                self.depth += 1
            elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self.level = int(tag[1])
                self._sep("\n")
            elif tag in {"p", "li", "tr", "br"}:
                self._sep("\n")
            elif tag in {"td", "th"}:
                self._sep("\t")

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if tag in {"p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._sep("\n")
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                text = re.sub(r" +", " ", "".join(self.parts))
                text = re.sub(r" *\n *", "\n", html.unescape(text)).strip()
                if text:
                    self.blocks.append({
                        "label": self.current[0], "bbox": self.current[1],
                        "text": text, "level": self.level,
                    })
                self.current, self.parts = None, []

    def handle_data(self, data):
        if self.current is not None:
            self.parts.append(data)

    def _sep(self, value):
        if self.parts and not self.parts[-1].endswith(value):
            self.parts.append(value)


def parse_surya_html(raw: str) -> list[dict]:
    parser = _HtmlBlocks()
    parser.feed(raw)
    parser.close()
    if not parser.blocks:
        raise SuryaUnavailable("Surya returned no readable HTML blocks")
    return parser.blocks


def normalized_bbox_to_points(raw: str, page_width: float, page_height: float):
    match = re.fullmatch(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*", raw)
    if not match:
        return None
    x0, y0, x1, y1 = (int(value) for value in match.groups())
    if not (0 <= x0 <= x1 <= 1000 and 0 <= y0 <= y1 <= 1000):
        return None
    return (
        page_width * x0 / 1000,
        page_height * y0 / 1000,
        page_width * x1 / 1000,
        page_height * y1 / 1000,
    )


def request_surya_page(png: bytes, *, base_url: str, model: str = DEFAULT_SURYA_MODEL,
                       timeout_seconds: int = 1800, max_tokens: int = 16384) -> tuple[list[dict], dict]:
    validate_local_url(base_url)
    if timeout_seconds < 1800 or max_tokens < 8192:
        raise ValueError("Surya needs at least a 30-minute timeout and an 8192-token ceiling")
    token_budget = min(8192, max_tokens)
    started = time.monotonic()
    while True:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}},
                {"type": "text", "text": SURYA_PROMPT},
            ]}],
            "temperature": 0, "top_p": 0.1, "max_tokens": token_budget, "stream": False,
        }
        request = Request(base_url.rstrip("/") + "/chat/completions",
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                result = json.loads(response.read())
        except Exception as exc:
            raise SuryaUnavailable(f"Local Surya request failed: {exc}") from exc
        choices = result.get("choices") or []
        if not choices:
            raise SuryaUnavailable("Surya returned no completion choices")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            if token_budget >= max_tokens:
                raise SuryaUnavailable(f"Surya output was truncated at the configured {max_tokens}-token ceiling")
            token_budget = min(max_tokens, token_budget * 2)
            continue
        raw = (choice.get("message") or {}).get("content") or ""
        blocks = parse_surya_html(raw)
        return blocks, {
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "usage": result.get("usage") or {}, "model": result.get("model") or model,
            "max_tokens_used": token_budget,
        }


def parse_pdf_with_surya(upload: BookUpload, source: str | Path, *, base_url: str,
                         model: str = DEFAULT_SURYA_MODEL, min_page_chars: int = 8,
                         timeout_seconds: int = 1800, max_tokens: int = 16384) -> ParsedBook:
    """Use native text where available and Surya on image-only or sparse pages."""
    try:
        import pypdfium2
    except ImportError as exc:
        raise SuryaUnavailable("Surya PDF rendering requires pypdfium2") from exc
    path = Path(source)
    if upload.mime != "application/pdf" or not path.is_file():
        raise UnsupportedPDF("Surya adapter needs an existing PDF")
    if path.read_bytes()[:5] != b"%PDF-":
        raise UnsupportedPDF("file does not have a PDF signature")
    if hashlib.sha256(path.read_bytes()).hexdigest().lower() != upload.file_sha256.lower():
        raise UnsupportedPDF("uploaded PDF SHA-256 does not match upload metadata")
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise UnsupportedPDF("password-protected PDFs are not supported")
    outlines = _outline_entries(reader)
    by_page = {}
    for page0, depth, title in outlines:
        by_page.setdefault(page0 + 1, []).append((depth + 1, title))

    blocks, section_path, sequences, pdfium = [], [], {}, None
    try:
        for page_no, page in enumerate(reader.pages, 1):
            native = (page.extract_text() or "").strip()
            try:
                scanned = len("".join(native.split())) < min_page_chars and bool(page.images)
            except Exception:
                scanned = len("".join(native.split())) < min_page_chars
            for level, title in by_page.get(page_no, []):
                section_path = section_path[:max(0, level - 1)] + [title]
                seq = sequences.get(page_no, 0)
                blocks.append(Block(block_id=f"{upload.book_id}:{page_no}:{seq}", type="heading",
                    level=level, text=title, refs=[PageRef(book_id=upload.book_id, page_no=page_no)],
                    section_path=section_path.copy(), ocr=False))
                sequences[page_no] = seq + 1
            if scanned:
                if pdfium is None:
                    pdfium = pypdfium2.PdfDocument(str(path))
                rendered = pdfium[page_no - 1]
                image = rendered.render(scale=3.0).to_pil()
                buf = BytesIO()
                image.save(buf, format="PNG")
                image.close()
                rendered.close()
                found, stats = request_surya_page(buf.getvalue(), base_url=base_url, model=model,
                    timeout_seconds=timeout_seconds, max_tokens=max_tokens)
                log.info("Surya OCR completed", extra={"page_no": page_no, **stats})
                for item in found:
                    label = re.sub(r"[^a-z]", "", str(item["label"]).lower())
                    kind = {"sectionheader":"heading", "pageheader":"page_header",
                        "pagefooter":"page_footer", "listgroup":"list_item", "table":"table",
                        "figure":"figure", "image":"figure", "caption":"caption",
                        "equationblock":"equation", "codeblock":"code", "footnote":"footnote"}.get(label, "paragraph")
                    text = str(item["text"])
                    bbox = normalized_bbox_to_points(
                        str(item.get("bbox", "")),
                        float(page.mediabox.width), float(page.mediabox.height),
                    )
                    level = int(item.get("level", 1)) if kind == "heading" else None
                    if level:
                        section_path = section_path[:max(0, level - 1)] + [text]
                    seq = sequences.get(page_no, 0)
                    blocks.append(Block(block_id=f"{upload.book_id}:{page_no}:{seq}", type=kind,
                        level=level, text=text, refs=[PageRef(
                            book_id=upload.book_id, page_no=page_no, bbox=bbox,
                        )], section_path=section_path.copy(), ocr=True))
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
        if pdfium is not None:
            pdfium.close()
    if not blocks:
        raise SuryaUnavailable("PDF contained no extractable text and Surya returned no blocks")
    options = {"model": model, "base_url": base_url.rstrip("/"), "prompt": "surya-ocr-0.22.1",
               "max_tokens": max_tokens, "timeout": timeout_seconds, "pypdf": pypdf.__version__}
    return ParsedBook(book_id=upload.book_id, title=upload.title, role=upload.role,
        page_count=len(reader.pages), toc_source="pdf_outline" if outlines else "headings",
        blocks=blocks, parser={"name":"surya-2-llama.cpp", "version":"surya-ocr-0.22.1",
        "options_hash":hashlib.sha256(json.dumps(options,sort_keys=True).encode()).hexdigest()})
