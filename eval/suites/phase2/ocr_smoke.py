from __future__ import annotations

import argparse
import hashlib
import json
import resource
import tempfile
import time
import unicodedata
from pathlib import Path

from b2c_contracts import BookUpload
from b2c_ingest import parse_pdf_with_docling
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

TRUTH = [
    "\u0627\u0644\u0627\u062d\u062a\u0645\u0627\u0644 \u0647\u0648 \u0645\u0642\u064a\u0627\u0633 \u0644\u0641\u0631\u0635\u0629 \u0648\u0642\u0648\u0639 \u062d\u062f\u062b \u0645\u0639\u064a\u0646.",
    "\u062a\u0643\u0648\u0646 \u0642\u064a\u0645\u0629 \u0627\u0644\u0627\u062d\u062a\u0645\u0627\u0644 \u0628\u064a\u0646 \u0635\u0641\u0631 \u0648\u0648\u0627\u062d\u062f.",
    "\u0625\u0630\u0627 \u0643\u0627\u0646 \u0627\u0644\u062d\u062f\u062b \u0645\u0624\u0643\u062f\u0627\u064b \u0641\u0625\u0646 \u0627\u062d\u062a\u0645\u0627\u0644\u0647 \u064a\u0633\u0627\u0648\u064a \u0648\u0627\u062d\u062f\u0627\u064b.",
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\u0640", "")
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn" and not ch.isspace())


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def render_pdf(path: Path, font_path: str | None, *, degraded: bool, input_image: str | None) -> None:
    if input_image:
        image = Image.open(input_image).convert("RGB")
    else:
        if not features.check("raqm"):
            raise RuntimeError("Provide --input-image when Pillow lacks libraqm for Arabic rendering")
        image = Image.new("RGB", (2550, 3300), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(font_path, 64, layout_engine=ImageFont.Layout.RAQM)
        for line_no, text in enumerate(TRUTH):
            draw.text((2320, 520 + line_no * 220), text, font=font, fill="black",
                      direction="rtl", language="ar", anchor="ra")
    if degraded:
        image = image.resize((850, 1100), Image.Resampling.BILINEAR)
        image = image.filter(ImageFilter.GaussianBlur(0.75))
        image = image.resize((2550, 3300), Image.Resampling.BILINEAR)
        image = image.rotate(1.1, resample=Image.Resampling.BILINEAR, fillcolor="white")
    image.save(path, format="PDF", resolution=300.0)


def run_case(font_path: str | None, *, degraded: bool, input_image: str | None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="b2c-ocr-smoke-") as directory:
        path = Path(directory) / "arabic-scan.pdf"
        render_pdf(path, font_path, degraded=degraded, input_image=input_image)
        raw = path.read_bytes()
        upload = BookUpload(
            book_id="synthetic-arabic", role="spine",
            file_sha256=hashlib.sha256(raw).hexdigest(), mime="application/pdf",
            title="Synthetic Arabic OCR smoke", rights_status="internal_test_only",
            ocr_language="ar",
        )
        started = time.perf_counter()
        parsed = parse_pdf_with_docling(upload, path)
        elapsed = time.perf_counter() - started
        actual = "\n".join(block.text for block in parsed.blocks)
        expected = normalize(" ".join(TRUTH))
        recognized = normalize(actual)
        distance = edit_distance(expected, recognized)
        return {
            "case": "degraded_arabic" if degraded else "clean_arabic",
            "elapsed_seconds": round(elapsed, 3),
            "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            "pages": parsed.page_count,
            "blocks": len(parsed.blocks),
            "all_blocks_marked_ocr": bool(parsed.blocks) and all(block.ocr for block in parsed.blocks),
            "expected_characters": len(expected),
            "edit_distance": distance,
            "cer": round(distance / max(1, len(expected)), 4),
            "expected": expected,
            "recognized": recognized,
            "expected_lines": [normalize(line) for line in TRUTH],
            "recognized_blocks": [normalize(block.text) for block in parsed.blocks],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--font")
    parser.add_argument("--input-image")
    args = parser.parse_args()
    if not args.font and not args.input_image:
        parser.error("supply --font (if Pillow has libraqm) or --input-image")
    print(json.dumps({
        "docling_version": __import__("docling").__version__,
        "ocr_backend": "RapidOCR ONNX Runtime PP-OCRv5 arabic",
        "cases": [run_case(args.font, degraded=False, input_image=args.input_image),
                  run_case(args.font, degraded=True, input_image=args.input_image)],
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
