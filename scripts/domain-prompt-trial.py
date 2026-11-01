"""With vs without the field brief (D-034): does the research brief help the engine? Does it leak?

The same units are translated twice by the local engine, sequentially: ``old`` as today, ``new`` with
``domain_brief(dossier)``. Both drafts are checked deterministically and judged by an independent judge
of another family. Nothing is written to a store (``cache=None``). Runs on the host against the given
endpoints; book text stays off git (output path on the data disk).

    python scripts/domain-prompt-trial.py units.json dossier.json ENGINE_URL JUDGE_KEY_FILE OUT.jsonl [N] [SEED]
"""
from __future__ import annotations

import json
import random
import sys
import time

from b2c_contracts import EngineIdentity, KnowledgeDossier, TranslationUnit
from b2c_llm_gateway.client import LLMGateway
from b2c_translation.domain import domain_brief
from b2c_translation.engines.base import EngineCapabilities, EngineRequest
from b2c_translation.engines.chat import ChatEngine
from b2c_translation.qa.estimators import LLMJudge

MODEL = "gemma4-b2c:26b-a4b-it-qat-t18"
CAPS = EngineCapabilities(follows_instructions=True, supports_terms=True, supports_context=True,
                          supports_repair=True, max_source_chars=1200)


def leaked(brief: str, target: str) -> bool:
    """A Latin run of the brief copied into the output (the title or an author name, say)."""
    words = [w for w in brief.replace(".", " ").replace(",", " ").split() if len(w) > 4]
    return any(w in target for w in words)


def main(units_path, dossier_path, engine_url, key_file, out_path, n="20", seed="20261102"):
    units = [TranslationUnit.model_validate(u) for u in json.load(open(units_path, encoding="utf-8"))]
    brief = domain_brief(KnowledgeDossier.model_validate_json(open(dossier_path, encoding="utf-8").read()))
    print("BRIEF:", brief, file=sys.stderr)
    pool = [(i, u) for i, u in enumerate(units) if u.kind == "prose" and 120 <= len(u.source_text) <= 400]
    picked = random.Random(int(seed)).sample(pool, int(n))
    gateway = LLMGateway(engine_url, MODEL, cache=None, timeout_s=900, reasoning_effort="none")
    engine = ChatEngine(gateway, EngineIdentity(provider="ollama-local", model_id=MODEL, revision=None,
                                                prompt_version="unset"), CAPS, max_output_tokens=2048)
    judge_gw = LLMGateway("https://openrouter.ai/api/v1", "deepseek/deepseek-v4-flash-0731", cache=None, allow_remote=True,
                          api_key=open(key_file).read().strip(), timeout_s=300, reasoning_effort="none")
    judge = LLMJudge(judge_gw, prompt_version="qe-mqm-v1", max_tokens=2048)
    with open(out_path, "a", encoding="utf-8") as fh:
        for i, unit in picked:
            before = units[i - 1].source_text[-400:] if i else None
            after = units[i + 1].source_text[:400] if i + 1 < len(units) else None
            row = {"unit_id": unit.unit_id, "source": unit.source_text}
            for variant, domain in (("old", ""), ("new", brief)):
                t0 = time.monotonic()
                text = engine.translate(EngineRequest(unit=unit, source_language="en", target_language="ar", domain=domain,
                                                      context_before=before, context_after=after)).text
                verdict = judge.estimate(unit, text)
                row[variant] = {"text": text, "seconds": round(time.monotonic() - t0, 1), "passed": verdict.passed,
                                "errors": [(x.severity, x.category) for x in verdict.issues],
                                "leak": bool(domain) and leaked(domain, text)}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()


if __name__ == "__main__":
    main(*sys.argv[1:])
