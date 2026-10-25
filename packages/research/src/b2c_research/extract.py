"""Sandboxed extraction: fetched pages become plain text DATA (learning-companion §6).

``html_to_text`` is a pure ``html.parser`` walk: nothing is executed or loaded (no scripts, styles,
frames, forms, embedded objects, comments, ``<head>``/``<meta>``), and nodes hidden from readers
(``hidden``, ``aria-hidden="true"``, inline ``display:none``/``visibility:hidden``) are dropped, because
indirect prompt injection hides there. Navigation chrome (``nav``, ``header``, ``footer``, ``aside``)
is dropped too.

``sanitize_text`` normalises Unicode, strips invisible/bidi control characters, removes sentences
that read as instructions to an AI system (they are reported, never sent to a model) and enforces a
size limit. The citation validator matches quotes with ``quote_in_text`` against the sanitized text.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser

DROP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "math", "iframe", "frame", "frameset",
                       "object", "embed", "applet", "canvas", "form", "button", "select", "textarea", "input",
                       "head", "title", "meta", "link", "nav", "header", "footer", "aside", "dialog"})
VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                       "source", "track", "wbr"})
BLOCK_TAGS = frozenset({"p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dt", "dd", "table",
                        "tr", "td", "th", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "br",
                        "figcaption", "figure", "hr"})
_HIDDEN_STYLE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0(?![.\d]))",
                           re.I)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []  # (tag, suppresses text)

    def _suppressed(self) -> bool:
        return any(s for _, s in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        hidden = ("hidden" in a or a.get("aria-hidden", "").lower() == "true"
                  or bool(_HIDDEN_STYLE.search(a.get("style", ""))) or a.get("type", "").lower() == "hidden")
        if tag in VOID_TAGS:
            if tag in ("br", "hr"):
                self.parts.append("\n")
            return
        self.stack.append((tag, tag in DROP_TAGS or hidden))
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("br", "hr"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed():
            self.parts.append(data)

    # comments, processing instructions and declarations are ignored (never text)
    def handle_comment(self, data: str) -> None:
        return None


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    text = "".join(parser.parts)
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------- sanitizing
_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿᠎]")
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|preceding)\b"
    r"[^.\n]{0,20}\b(instruction|prompt|direction|rule|message)s?",
    r"\byou are (now )?(chatgpt|an? (ai|assistant|language model|unrestricted)|in developer mode|dan)\b",
    r"\b(system|developer) (prompt|message|mode)\b",
    r"^\s*(system|assistant|user)\s*:",
    r"#{2,}\s*(instruction|system|prompt)",
    r"<\|?(im_start|im_end|system|endoftext)\|?>",
    r"\bdo not (tell|inform|reveal to) the (user|reader|human)\b",
    r"\b(reply|respond|answer|output)\s+only\s+with\b",
    r"\b(reply|respond)\s+with\s+[\"'“]",
    r"\b(api[_ ]?key|password|secret token|credentials)\b.*\b(send|print|reveal|output|post)\b",
    r"\b(send|print|reveal|output|post)\b.*\b(api[_ ]?key|password|secret token|credentials)\b",
    r"\bas an ai (language )?model\b",
    r"\bnew instructions?\s*:",
))
_CHAT_TOKENS = re.compile(r"<\|[a-z_]{2,20}\|>[^<\n]*(?:<\|[a-z_]{2,20}\|>)?", re.I)
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+[\"'”’)\]]*|\n|$)")


@dataclass(frozen=True)
class SanitizedText:
    text: str
    removed: list[str] = field(default_factory=list)
    truncated: bool = False


def looks_like_instruction(sentence: str) -> bool:
    return any(p.search(sentence) for p in INJECTION_PATTERNS)


def sanitize_text(text: str, *, max_chars: int) -> SanitizedText:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = _CHAT_TOKENS.sub(lambda m: "\n" + m.group(0) + "\n", text)  # a chat-template segment is its own sentence
    kept_lines: list[str] = []
    removed: list[str] = []
    for line in text.splitlines():
        kept: list[str] = []
        for m in _SENTENCE.finditer(line):
            sentence = m.group(0)
            if not sentence.strip():
                continue
            if looks_like_instruction(sentence):
                removed.append(sentence.strip())
            else:
                kept.append(sentence)
        joined = re.sub(r"\s+", " ", "".join(kept)).strip()
        if joined:
            kept_lines.append(joined)
    clean = "\n".join(kept_lines)
    truncated = len(clean) > max_chars
    if truncated:
        cut = clean[:max_chars]
        clean = cut[: cut.rfind(" ")] if " " in cut[-200:] else cut
    return SanitizedText(text=clean, removed=removed, truncated=truncated)


# ---------------------------------------------------------------- quote matching
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"', "‘": "'", "’": "'",
                         "‚": "'", "‛": "'", "–": "-", "—": "-", "―": "-", "‐": "-", "‑": "-", "…": "..."})


def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    text = re.sub(r"\s*-\s*", " - ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """The quote occurs verbatim up to case, whitespace, quote marks and dash style."""
    q = normalize_for_match(quote).strip(" .\"'")
    return bool(q) and q in normalize_for_match(text)


__all__ = ["INJECTION_PATTERNS", "SanitizedText", "html_to_text", "looks_like_instruction", "normalize_for_match",
           "quote_in_text", "sanitize_text"]
