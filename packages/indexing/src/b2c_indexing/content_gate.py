"""Pre-chunk content gate (R5, audit 03 C2 / §5.2.1).

Decides, per block, whether its text is lesson *body* (teaching content), a *note* (evidence
of its own class, never lesson content) or *excluded* (furniture, front/back matter, TOC,
index, bibliography, OCR text of figures, empty), and flags corrupted text.

ParsedBook v2 carries zones and apparatus types from S2, so the gate only reads them. A v1
ParsedBook has no zones; the gate then degrades to the v1 block type (page_header/footer,
footnote), the section heading the block sits under (Index, Notes, Contents, ...) and the
line-level TOC/index/bibliography rules proven in ingestion (``b2c_core.source_text``).

The gate never repairs text (that is S2's job). Corrupt blocks are *quarantined*: kept out of
the index, listed by id in the build report, and counted against the gate's ratio.
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from b2c_contracts import ParsedBookV2, parsed_book_v1_from_v2
from b2c_contracts.models import FURNITURE_TYPES, NOTE_TYPES, ParsedBook
from b2c_core.source_text import NOTE_LABEL, index_like, is_bibliographic, section_word, toc_like

GATE_VERSION = "b2c-content-gate/1"
Evidence = Literal["body", "note", "excluded"]

# text-integrity thresholds (calibrated on SAT and Gramsci; eval/reports/indexing-v2-2026-10-25.md)
CONTROL_RATIO = 0.01  # audit 03 §5.4: 0 chunks with control chars > 1%
LOW_ALPHA_RATIO = 0.5  # letters / non-space characters, prose types only
LOW_ALPHA_MIN_CHARS = 20
SPACED_LETTER_RATIO = 0.08  # stray single letters / alphabetic tokens
SPACED_LETTER_MIN = 3
SPACED_RUN = 4  # "N O T g e t t i n g": 4+ consecutive stray single letters
GLUED_PART_ZIPF = 3.5  # both halves common English words ...
GLUED_SHORT_ZIPF = 5.5  # ... and very common if short ("of", "the", "to")
CORRUPT_FLAGS = frozenset({"control_chars", "low_alpha", "letter_spacing"})

_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f�]")
_ALPHA_TOKEN = re.compile(r"^[^\w]*([A-Za-z]+)[^\w]*$")
_INITIAL = re.compile(r"^[(\[“\"'‘]?[A-Z]\.[,;:)]*$")  # "D." in "Walter D. Mignolo" is an initial, not residue
_WORD = re.compile(r"[A-Za-z]+")
_GRID_TYPES = frozenset({"table", "equation", "code"})  # single letters are labels/variables there
_PROSE_TYPES = frozenset({"paragraph", "list_item", "heading", "caption"})  # notes cite: "SPN, 208; Q13"
_V2_EXCLUDED_TYPES = {"toc_entry": "toc", "index_entry": "index", "bibliography_entry": "bibliography",
                      "figure": "figure_text"}
_V1_SECTION_CLASS = {"index": "index", "bibliography": "bibliography", "toc": "toc"}


@dataclass(frozen=True)
class GateBlock:
    block_id: str  # the id in the book handed to the gate
    alt_id: str | None  # v2 only: the id of the same block in the v1 projection
    type: str
    zone: str | None  # None for a v1 book (no zones)
    text: str
    pages: tuple[int, ...]
    section_path: tuple[str, ...]
    evidence: Evidence
    reason: str | None  # why excluded
    heading: bool
    flags: tuple[str, ...]
    quarantined: bool
    cited_by: tuple[str, ...] = ()  # notes: ids of the blocks that cite them

    @property
    def teaching(self) -> bool:
        return self.evidence == "body" and not self.quarantined


def build_vocab(texts: Iterable[str]) -> Counter:
    words: Counter = Counter()
    for text in texts:
        words.update(w.lower() for w in _WORD.findall(text))
    return words


@lru_cache(maxsize=200_000)
def zipf(word: str) -> float:
    """General-English frequency (wordfreq 3.1.1, pinned; same lexicon as S2): 3.0 = 1 per million."""
    from wordfreq import zipf_frequency

    return zipf_frequency(word, "en")


def _glued(token: str, vocab: Counter) -> bool:
    """An unknown word (not English, not repeated in the book) that is two common words joined."""
    low = token.lower()
    if len(low) < 6 or vocab.get(low, 0) > 2 or zipf(low) > 0:
        return False
    for i in range(2, len(low) - 1):
        left, right = low[:i], low[i:]
        if all(zipf(part) >= (GLUED_SHORT_ZIPF if len(part) <= 3 else GLUED_PART_ZIPF) for part in (left, right)):
            return True
    return False


def text_flags(text: str, *, vocab_texts: Iterable[str] | None = None, vocab: Counter | None = None,
               block_type: str = "paragraph") -> tuple[str, ...]:
    """Integrity flags of one text. Corrupt flags (CORRUPT_FLAGS) quarantine a block."""
    flags: list[str] = []
    if text and len(_CONTROL.findall(text)) / len(text) > CONTROL_RATIO:
        flags.append("control_chars")
    nonspace = [ch for ch in text if not ch.isspace()]
    if block_type in _PROSE_TYPES and len(nonspace) >= LOW_ALPHA_MIN_CHARS:
        if sum(ch.isalpha() for ch in nonspace) / len(nonspace) < LOW_ALPHA_RATIO:
            flags.append("low_alpha")
    alpha = [m.group(1) for t in text.split() if not _INITIAL.match(t) and (m := _ALPHA_TOKEN.match(t))]
    singles = [len(t) == 1 and t not in ("a", "A", "I") for t in alpha]
    run = best = 0
    for single in singles:
        run = run + 1 if single else 0
        best = max(best, run)
    n_single = sum(singles)
    if block_type not in _GRID_TYPES and (best >= SPACED_RUN or (n_single >= SPACED_LETTER_MIN and alpha
                              and n_single / len(alpha) >= SPACED_LETTER_RATIO)):
        flags.append("letter_spacing")
    if vocab is None and vocab_texts is not None:
        vocab = build_vocab(vocab_texts)
    if vocab is not None and any(_glued(w, vocab) for w in _WORD.findall(text)):
        flags.append("glued_words")
    return tuple(flags)


def _lines_share(text: str, rule) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return sum(bool(rule(line)) for line in lines) / len(lines) if lines else 0.0


def _v1_reason(block_type: str, text: str, section_path: list[str]) -> tuple[Evidence, str | None]:
    if block_type in ("page_header", "page_footer"):
        return "excluded", "furniture"
    if block_type == "footnote":
        return "note", None
    for title in reversed(section_path):
        kind = section_word(title)
        if kind == "notes":  # a numbered or citation-like entry under a Notes heading
            if NOTE_LABEL.match(text) or is_bibliographic(text, notes_context=True):
                return "note", None
            break
        if kind in _V1_SECTION_CLASS:
            return "excluded", _V1_SECTION_CLASS[kind]
    if block_type == "heading":
        return "body", None
    if _lines_share(text, index_like) >= 0.6:
        return "excluded", "index"
    if _lines_share(text, toc_like) >= 0.6:
        return "excluded", "toc"
    if is_bibliographic(text, notes_context=False):
        return "excluded", "bibliography"
    return "body", None


def _v2_reason(block) -> tuple[Evidence, str | None]:
    if block.zone == "furniture" or block.type in FURNITURE_TYPES:
        return "excluded", "furniture"
    if block.type in NOTE_TYPES:
        return "note", None
    if block.type in _V2_EXCLUDED_TYPES:
        return "excluded", _V2_EXCLUDED_TYPES[block.type]
    if block.zone in ("front_matter", "back_matter"):
        return "excluded", block.zone
    return "body", None


def gate_blocks(book: ParsedBook | ParsedBookV2) -> list[GateBlock]:
    """One verdict per block, in reading order."""
    v2 = isinstance(book, ParsedBookV2)
    vocab = build_vocab(b.text for b in book.blocks)
    alt_ids = [b.block_id for b in parsed_book_v1_from_v2(book).blocks] if v2 else [None] * len(book.blocks)
    cited: dict[str, tuple[str, ...]] = {}
    if v2:
        for note in book.notes:
            for block_id in note.block_ids:
                cited[block_id] = tuple(dict.fromkeys([*cited.get(block_id, ()), *note.cited_by]))
    out: list[GateBlock] = []
    for block, alt in zip(book.blocks, alt_ids):
        evidence, reason = _v2_reason(block) if v2 else _v1_reason(block.type, block.text, block.section_path)
        if evidence != "excluded" and not block.text.strip():
            evidence, reason = "excluded", "empty"
        flags: tuple[str, ...] = ()
        if evidence != "excluded":
            flags = text_flags(block.text, vocab=vocab, block_type=block.type)
        out.append(GateBlock(
            block_id=block.block_id, alt_id=alt, type=block.type, zone=block.zone if v2 else None,
            text=block.text, pages=tuple(sorted({r.page_no for r in block.refs})),
            section_path=tuple(block.section_path), evidence=evidence, reason=reason,
            heading=block.type == "heading", flags=flags,
            quarantined=evidence != "excluded" and bool(CORRUPT_FLAGS.intersection(flags)),
            cited_by=cited.get(block.block_id, ())))
    return out


__all__ = ["CORRUPT_FLAGS", "GATE_VERSION", "GateBlock", "build_vocab", "gate_blocks", "text_flags"]
