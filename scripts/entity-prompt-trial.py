"""Old vs new prompt on the same units: are approved names rendered as approved (D-047)?

Each unit is translated twice by the same engine profile, sequentially (one request at a time):
``old`` sends the locked terms and hints as before D-047; ``new`` also sends the approved names
occurring in the unit (``chat.entities@v1``). Only the first draft is compared (no repair, no QE),
and the deterministic checks of this checkout are run on both. Nothing is written: no run store,
no translation memory, no LLM cache (``cache=None``).

    python scripts/entity-prompt-trial.py run.json termbase.json units.txt BASE_URL [MODEL_ID] > trial.jsonl

``run.json``: ``scripts/translator-notes-measure.py export``; ``termbase.json``:
``scripts/named-entity-measure.py export-termbase``; ``units.txt``: one unit id per line;
``BASE_URL``: the engine's OpenAI-compatible endpoint (e.g. the b2c gemma4 container address).
"""
from __future__ import annotations

import json
import sys
import time

from b2c_contracts import EngineIdentity, TranslationUnit
from b2c_llm_gateway.client import LLMGateway
from b2c_translation.book_pipeline import names_in, restore_footnotes, terms_in
from b2c_translation.engines.base import EngineCapabilities, EngineRequest
from b2c_translation.engines.chat import ChatEngine
from b2c_translation.qa import CheckContext
from b2c_translation.qa.checks import run_checks
from b2c_translation.termbase import BookTermbase

CONTEXT_CHARS = 400
CAPS = EngineCapabilities(follows_instructions=True, supports_terms=True, supports_context=True,
                          supports_repair=True, max_source_chars=1200)


def main(run_path: str, termbase_path: str, ids_path: str, base_url: str,
         model_id: str = "gemma4-b2c:26b-a4b-it-qat-t18") -> None:
    data = json.load(open(run_path, encoding="utf-8"))
    tb = BookTermbase.from_json(open(termbase_path, encoding="utf-8").read())
    terms, entities, hints = tb.locked_terms(), tb.entity_table(), tb.hint_terms()
    forbidden = tb.forbidden_variants()
    units = [TranslationUnit.model_validate(u) for u in data["units"]]
    index = {u.unit_id: i for i, u in enumerate(units)}
    wanted = [line.strip() for line in open(ids_path, encoding="utf-8") if line.strip()]
    gateway = LLMGateway(base_url, model_id, cache=None, timeout_s=900, reasoning_effort="none")
    engine = ChatEngine(gateway, EngineIdentity(provider="ollama-local", model_id=model_id, revision=None,
                                                prompt_version="unset"), CAPS, max_output_tokens=2048)
    for uid in wanted:
        i = index[uid]
        unit = units[i]
        before = units[i - 1].source_text[-CONTEXT_CHARS:] if i > 0 else None
        after = units[i + 1].source_text[:CONTEXT_CHARS] if i + 1 < len(units) else None
        names = names_in(unit.source_text, entities)
        row = {"unit_id": uid, "kind": unit.kind, "source": unit.source_text, "names": names}
        for variant in ("old", "new"):
            request = EngineRequest(unit=unit, source_language="en", target_language="ar",
                                    terms=terms_in(unit.source_text, terms), hints=terms_in(unit.source_text, hints),
                                    entities=names if variant == "new" else {},
                                    context_before=before, context_after=after)
            started = time.monotonic()
            try:
                result = engine.translate(request)
            except Exception as exc:  # measurement: record and continue
                row[variant] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            target = restore_footnotes(result.text, unit.footnote_markers)
            ctx = CheckContext(unit=unit, target=target, terms=terms_in(unit.source_text, terms),
                               entities=terms_in(unit.source_text, entities),
                               forbidden={k: v for k, v in forbidden.items() if k in terms_in(unit.source_text, {k: ""})},
                               hints=terms_in(unit.source_text, hints))
            issues = run_checks(ctx)
            row[variant] = {"text": target, "prompt_version": result.identity.prompt_version,
                            "seconds": round(time.monotonic() - started, 1),
                            "named_entity": [i.evidence for i in issues if i.check_id == "named_entity"],
                            "blocking": sorted({i.check_id for i in issues if i.severity in ("major", "critical")})}
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        sys.exit(__doc__)
    main(*sys.argv[1:])
