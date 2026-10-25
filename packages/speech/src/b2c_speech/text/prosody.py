"""Prosodic boundary plan and engine rendering (docs/proposals/2026-10-25-speech-readability.md).

Every segment ends at a typed boundary. The boundary has an SSML 1.1 break strength (W3C
Recommendation 2010-09-07, §3.2.3: none < x-weak < weak < medium < strong < x-strong) and a
realized pause in milliseconds for engines that take plain text (Fasih/XTTS): the worker inserts
that silence between segment WAVs. An engine with SSML gets ``render_ssml``.

Pause values: read-speech measurements put sentence pauses at ~0.2-1.4 s and paragraph pauses
at ~0.4-1.8 s, with paragraph ≈ 1.4× sentence (Kazlauskienė & Kalašinskaitė 2020); read speech
has no pauses above ~1 s (Campione & Véronis 2002). The track gate refuses internal silence
above 1.5 s, so the largest planned pause is 1.2 s, leaving ~0.3 s for the voices' own edges.
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from b2c_contracts import SpeechSegment, SpeechTextDocument

from .waqf import apply_waqf

BOUNDARIES = ("phrase", "sentence", "list_item", "paragraph", "heading", "section")
PAUSE_MS = {"phrase": 250, "sentence": 550, "list_item": 600, "paragraph": 900, "heading": 1000, "section": 1200}
BREAK_STRENGTH = {"phrase": "weak", "sentence": "medium", "list_item": "medium", "paragraph": "strong",
                  "heading": "strong", "section": "x-strong"}
MAX_PAUSE_MS = 1200
# The boundary after the last segment of a unit, by the unit's speech structure.
STRUCTURE_BOUNDARY = {"paragraph": "paragraph", "heading": "heading", "list_item": "list_item",
                      "caption": "paragraph", "question": "paragraph", "answer": "paragraph"}
PARAGRAPH_LEVEL = {"list_item", "paragraph", "heading", "section"}


@dataclass(frozen=True)
class EngineProfile:
    """What one TTS engine can take in a single request, and whether it reads SSML.

    ``engine_chunk_chars``/``diacritized_expansion``: an engine that re-splits its input (Fasih
    cuts CATT-diacritized text at 160 characters with a fixed 120 ms gap and hard-wraps when no
    punctuation is near) must receive phrases short enough that it never re-splits them; the
    expansion is the length ratio diacritized/undiacritized (estimate 1.8, UNVERIFIED until
    measured with CATT on book text).
    """
    name: str
    max_phrase_chars: int
    max_phrase_words: int
    min_phrase_chars: int
    ssml: bool
    engine_chunk_chars: int | None = None
    diacritized_expansion: float = 1.0
    waqf: bool = False  # apply pausal forms before sending (only meaningful for pre-diacritized text)

    def definition(self) -> dict[str, object]:
        return {"name": self.name, "max_phrase_chars": self.max_phrase_chars,
                "max_phrase_words": self.max_phrase_words, "min_phrase_chars": self.min_phrase_chars,
                "ssml": self.ssml, "engine_chunk_chars": self.engine_chunk_chars,
                "diacritized_expansion": self.diacritized_expansion, "waqf": self.waqf}


ENGINE_PROFILES = {
    # docker/fasih: XTTS v2 fine-tune, 'ar' char limit 166; CATT diacritizes inside the service and
    # overwrites any tashkeel we send below 30% coverage, so waqf cannot be applied from here.
    "fasih-xtts": EngineProfile("fasih-xtts", max_phrase_chars=85, max_phrase_words=16, min_phrase_chars=12,
                                ssml=False, engine_chunk_chars=160, diacritized_expansion=1.8),
    # an SSML 1.1 engine (cloud or local); sentence-level requests well under vendor caps
    "ssml-generic": EngineProfile("ssml-generic", max_phrase_chars=300, max_phrase_words=35, min_phrase_chars=1,
                                  ssml=True),
}
DEFAULT_ENGINE_PROFILE = "fasih-xtts"


def engine_profile(profile: str | EngineProfile) -> EngineProfile:
    if isinstance(profile, EngineProfile):
        return profile
    try:
        return ENGINE_PROFILES[profile]
    except KeyError:
        raise ValueError(f"unknown speech engine profile {profile!r}; known: {sorted(ENGINE_PROFILES)}") from None


def plan_boundaries(structures: list[str], boundaries: list[str]) -> list[str]:
    """Document-level pass: the end of a list is a paragraph; what precedes a heading is a section."""
    out = list(boundaries)
    for i, b in enumerate(out):
        nxt = structures[i + 1] if i + 1 < len(structures) else None
        if b == "list_item" and nxt != "list_item":
            out[i] = "paragraph"
        if nxt == "heading" and structures[i] != "heading" and out[i] in PARAGRAPH_LEVEL:
            out[i] = "section"
    return out


def request_text(segment: SpeechSegment, profile: EngineProfile) -> str:
    """The plain text one engine request receives for one segment."""
    return apply_waqf(segment.spoken_text) if profile.waqf else segment.spoken_text


def render_ssml(doc: SpeechTextDocument, profile: EngineProfile | None = None) -> str:
    """SSML 1.1 for the whole document: <p> per paragraph-level unit, <s> per sentence, <break>."""
    profile = profile or ENGINE_PROFILES["ssml-generic"]
    parts = [f'<speak version="1.1" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{doc.language}">']
    in_p = in_s = False
    for segment in doc.segments:
        boundary = segment.boundary_after or "sentence"
        if not in_p:
            parts.append("<p>")
            in_p = True
        if not in_s:
            parts.append("<s>")
            in_s = True
        parts.append(escape(request_text(segment, profile)))
        if boundary != "phrase":
            parts.append("</s>")
            in_s = False
        if boundary in PARAGRAPH_LEVEL:
            parts.append("</p>")
            in_p = False
        strength = segment.break_after or BREAK_STRENGTH[boundary]
        parts.append(f'<break strength="{strength}" time="{segment.pause_after_ms}ms"/>')
    if in_s:
        parts.append("</s>")
    if in_p:
        parts.append("</p>")
    parts.append("</speak>")
    return "".join(parts)


__all__ = ["BOUNDARIES", "BREAK_STRENGTH", "DEFAULT_ENGINE_PROFILE", "ENGINE_PROFILES", "EngineProfile",
           "MAX_PAUSE_MS", "PAUSE_MS", "PARAGRAPH_LEVEL", "STRUCTURE_BOUNDARY", "engine_profile", "plan_boundaries",
           "render_ssml", "request_text"]
