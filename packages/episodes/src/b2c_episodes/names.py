"""A line on a person, place, work or event at its first mention in the course (learning-companion §10:
«سطر تعريف لكل علم أو حدث عند أول ذكر»).

The listener who has never met a name gets one sentence from an open reference (Wikidata, the entity whose
Arabic spelling was approved, D-034), announced as an external source, right after the unit that first
mentions it. It is a reference entry, not generated text; the author's text is split around it and no unit
is lost. A cap per episode keeps the most frequent names (no flooding, §4).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from b2c_contracts import EpisodeScript, EpisodeSection, ScriptLine, ScriptSource

NAME_NOTES_PER_EPISODE = 5
ATTRIBUTION = "Wikidata"


@dataclass(frozen=True)
class KnownName:
    name: str
    description: str
    url: str
    frequency: int = 0


def first_mentions(units: Sequence[tuple[str, str]], names: Sequence[KnownName]) -> dict[str, list[KnownName]]:
    """{unit id: names first mentioned there}, walking the course's units in reading order."""
    out: dict[str, list[KnownName]] = {}
    pending = [(n, re.compile(rf"(?<!\w){re.escape(n.name)}(?!\w)")) for n in names]
    for unit_id, text in units:
        if not pending:
            break
        left = []
        for name, pattern in pending:
            if pattern.search(text):
                out.setdefault(unit_id, []).append(name)
            else:
                left.append((name, pattern))
        pending = left
    return out


def _line(ep: int, i: int, n: KnownName) -> ScriptLine:
    return ScriptLine(line_id=f"e{ep:03d}.x{i:02d}", text=f"According to {ATTRIBUTION}, {n.name}: {n.description.rstrip('.')}.",
                      attributed_to=ATTRIBUTION,
                      sources=[ScriptSource(kind="reference", url=n.url, note=f"{ATTRIBUTION} description")])


def insert_name_notes(script: EpisodeScript, mentions: Mapping[str, Sequence[KnownName]], *,
                      unit_blocks: Mapping[str, Sequence[str]], cap: int = NAME_NOTES_PER_EPISODE
                      ) -> tuple[EpisodeScript, list[str]]:
    """Insert ``name_note`` sections after the units that first mention a name, splitting the book text."""
    here = [n for s in script.sections if s.kind == "book_text" for u in s.unit_ids for n in mentions.get(u, ())]
    kept = {n.name for n in sorted(here, key=lambda n: (-n.frequency, n.name))[:cap]}
    if not kept:
        return script, []
    placed: list[str] = []
    sections: list[EpisodeSection] = []
    for section in script.sections:
        if section.kind != "book_text":
            sections.append(section)
            continue
        part: list[str] = []
        k = 0

        def flush() -> None:
            nonlocal part, k
            if part:
                blocks = [b for u in part for b in unit_blocks.get(u, ()) if b in section.block_ids]
                sections.append(section.model_copy(update={
                    "section_id": section.section_id if k == 0 else f"{section.section_id}.n{k}", "unit_ids": list(part),
                    "block_ids": list(dict.fromkeys(blocks)) or list(section.block_ids)}))
                k += 1
                part = []

        for uid in section.unit_ids:
            part.append(uid)
            notes = [n for n in mentions.get(uid, ()) if n.name in kept and n.name not in placed]
            if notes:
                flush()
                lines = []
                for n in notes:
                    lines.append(_line(script.episode, len(placed), n))
                    placed.append(n.name)
                sections.append(EpisodeSection(section_id=f"e{script.episode:03d}.name{len(placed):02d}", kind="name_note",
                                               voice="external", lines=lines))
        flush()
    data = script.model_dump()
    data["sections"] = [s.model_dump() for s in sections]
    data["metrics"] = {**script.metrics, "name_notes": placed}
    return EpisodeScript.model_validate(data), placed


__all__ = ["KnownName", "NAME_NOTES_PER_EPISODE", "first_mentions", "insert_name_notes"]
