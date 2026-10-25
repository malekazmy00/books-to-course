"""How much of a book reaches speech once translator notes are spoken (D-041). Read-only, offline.

Input: the export of ``scripts/translator-notes-measure.py export <book>`` (SELECT-only snapshot of
the run's unit results + its units artifact). Only units that already have a result are measured
(the run may still be going). Nothing is written anywhere.

    python scripts/translator-notes-speech-measure.py run.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone

from b2c_contracts import ArtifactRef, TranslationUnit, UnitTranslation
from b2c_speech.text import NotSpeechReady, build_speech_text
from b2c_speech.text.frontend import SPOKEN_KINDS
from b2c_translation.notes import build_translator_notes

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SNAPSHOT_REF = ArtifactRef(stage="translate-book", key="snapshot", uri="s3://snapshot/translate-book/snapshot/",
                           sha256="0" * 64, created_at=NOW)
NOTES_REF = ArtifactRef(stage="translator-notes", key="snapshot", uri="s3://snapshot/translator-notes/snapshot/",
                        sha256="0" * 64, created_at=NOW)


def main(path: str) -> None:
    data = json.load(open(path, encoding="utf-8"))
    translations = {r["unit_id"]: UnitTranslation.model_validate(r["translation"]) for r in data["results"]}
    drafts = {r["unit_id"]: r["drafts"] for r in data["results"]}
    units = [u for u in (TranslationUnit.model_validate(x) for x in data["units"]) if u.unit_id in translations]
    out: dict = {"run_id": data["run_id"], "decided_units": len(units)}
    try:
        build_speech_text(units, translations, book_id=data["book_id"], source=SNAPSHOT_REF)
        out["strict_path"] = "builds"
    except NotSpeechReady as exc:
        out["strict_path"] = f"refused: {exc}"
    notes = build_translator_notes(units, translations, drafts, run_id=data["run_id"])
    by_unit = {n.unit_id: n for n in notes.notes}
    doc = build_speech_text(units, translations, book_id=data["book_id"], source=SNAPSHOT_REF,
                            notes=by_unit, notes_ref=NOTES_REF)
    spoken_units = {u for s in doc.segments for u in s.unit_ids}
    noted_spoken = {s.note_id for s in doc.segments if s.note_id}
    with_translation = {s.note_id for s in doc.segments if s.note_part == "approximate_translation"}
    spoken_kind_notes = [n for n in notes.notes if n.unit_kind in SPOKEN_KINDS]
    out.update({
        "notes": notes.annotated, "notes_on_spoken_kinds": len(spoken_kind_notes),
        "withheld": dict(Counter(n.withheld for n in notes.notes if n.withheld)),
        "units_spoken": len(spoken_units),
        "units_spoken_accepted": len([u for u in spoken_units if translations[u].decision == "accepted"]),
        "notes_spoken": len(noted_spoken), "notes_spoken_with_translation": len(with_translation),
        "notes_spoken_announcement_only": len(noted_spoken - with_translation),
        "segments": len(doc.segments), "note_segments": sum(1 for s in doc.segments if s.note_id),
        "excluded": dict(Counter(e.reason.split(":")[0] for e in doc.excluded)),
        "u00628": by_unit.get(f"{data['book_id']}:u00628").model_dump(mode="json", exclude={"schema_version"})
        if f"{data['book_id']}:u00628" in by_unit else None,
        "withheld_examples": [{"unit_id": n.unit_id, "kind": n.unit_kind, "withheld": n.withheld,
                               "source": n.source_text[:120], "final": translations[n.unit_id].target_text[:160]}
                              for n in notes.notes if n.withheld][:12],
        "sample_note_segments": [{"unit_id": s.unit_ids[0], "part": s.note_part, "spoken": s.spoken_text}
                                 for s in doc.segments if s.note_id][:8],
    })
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
