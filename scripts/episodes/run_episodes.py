"""Build episode scripts (EN, then AR) for chosen episodes of one book from a development machine.

Runs the SAME activity code as ``BuildEpisodeScriptsWorkflow`` (``b2c_orchestration.episodes``)
without Temporal: a local file artifact store, an in-memory stage-output registry (promotion and
GateResults as in production), and an on-disk LLM cache, all under ``--work-dir`` (outside git).
Models are reached through b2c_llm_gateway: the generator and the validator from different families
(external OpenRouter models are a D-029 development opt-in; the key file stays outside the
repository and is never printed). The translation uses the run's engine/QE profiles from a profiles
file (same format as the worker's ``B2C_TRANSLATION_PROFILES_FILE``).

The concept layer and the dossier are imported with their recorded gates and promoted in the local
registry only when every recorded gate passed (as the knowledge run recorded them). The book's unit
results are a read-only JSONL snapshot of ``translation_unit_results`` for the run, e.g.

    docker exec b2c-postgres-1 psql -U b2c -d b2c -Atc "select json_build_object('unit_id',unit_id,
      'seq',seq,'t',translation_json,'termbase_digest',termbase_digest) from translation_unit_results
      where run_id='<run>' order by seq" > results.jsonl

Example:

    uv run python scripts/episodes/run_episodes.py --episodes 0 8 9 \
        --parsed <dump>/ingest-parse-pdf/<key>/parsed-book.json \
        --outline <dump>/approve-outline/<key>/approved-outline.json \
        --units <dump>/translation-units/<key>/units.json \
        --termbase <dump>/book-termbase-approved/<key>/termbase.json \
        --concepts <knowledge-run>/concept-layer/<key> --dossier <knowledge-run>/knowledge-research/<key> \
        --results results.jsonl --run-id <run> --profiles profiles.json \
        --engine-profile gemma4-26b --qe-profile deepseek-mqm --work-dir /root/.cache/b2c-episodes/run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "knowledge"))

from run_knowledge import FileLLMCache, LocalArtifactStore, MemoryRegistry, git_revision, import_input

from b2c_contracts import ArtifactRef, ProducerIdentity, UnitTranslation
from b2c_episodes import ModelRole
from b2c_llm_gateway import LLMGateway
from b2c_orchestration.episodes import EpisodeActivities, EpisodeScriptARInput, EpisodeScriptENInput
from b2c_orchestration.knowledge import attach_gates
from b2c_orchestration.promotion import PromotionService
from b2c_orchestration.translation_profiles import ProfileRuntime, TranslationProfiles
from b2c_orchestration.worker import translation_gateway_factory

OPENROUTER = "https://openrouter.ai/api/v1"
COURSE = "b2c-dev-episodes"


class FileMemory:
    """Translation memory on disk (exact source match; only accepted translations are stored)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, dict[str, Any]] = json.loads(path.read_text()) if path.exists() else {}

    def _key(self, unit) -> str:
        return hashlib.sha256(" ".join(unit.source_text.split()).encode()).hexdigest()

    def lookup(self, unit, source_language, target_language):
        hit = self.data.get(self._key(unit))
        if hit is None:
            return None
        human = hit["origin"] == "human"
        return UnitTranslation(unit_id=unit.unit_id, target_text=hit["target_text"], origin="memory",
                               decision="accepted" if human else "needs_review",
                               decided_by="human" if human else "policy", decided_at=hit["at"])

    def store(self, unit, translation, **_):
        self.data[self._key(unit)] = {"origin": translation.origin, "target_text": translation.target_text,
                                      "at": translation.decided_at.isoformat()}
        self.path.write_text(json.dumps(self.data, ensure_ascii=False))


class FileArtifactStore(LocalArtifactStore):
    def import_dir(self, course_id: str, stage: str, directory: Path) -> ArtifactRef:
        files = {p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.name != "_manifest.json"}
        manifest = directory / "_manifest.json"
        key = json.loads(manifest.read_text())["key"] if manifest.exists() else hashlib.sha256(
            b"".join(files.values())).hexdigest()
        return self.get_or_compute(course_id, stage, key, lambda: files)[0]


class Recorded:
    def __init__(self, gate_id: str, v: dict[str, Any]) -> None:
        self.gate_id, self.kind, self.passed = gate_id, v["kind"], v["passed"]
        self.metrics, self.definition = v.get("metrics") or {}, v.get("definition") or {}


def promote_recorded(promotion: PromotionService, store: FileArtifactStore, ref: ArtifactRef) -> str:
    gates = json.loads(store.read(ref, "gates.json"))
    return attach_gates(promotion, COURSE, ref, [Recorded(g, v) for g, v in gates.items()], inputs=[],
                        config={"imported": True}, identities=[]).status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", type=int, nargs="+", required=True)
    ap.add_argument("--parsed", type=Path, required=True)
    ap.add_argument("--outline", type=Path, required=True)
    ap.add_argument("--units", type=Path, required=True)
    ap.add_argument("--termbase", type=Path, required=True, help="approved termbase.json")
    ap.add_argument("--concepts", type=Path, required=True, help="concept-layer artifact directory")
    ap.add_argument("--dossier", type=Path, help="knowledge-research artifact directory")
    ap.add_argument("--results", type=Path, required=True, help="JSONL snapshot of the run's unit results")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profiles", type=Path, required=True)
    ap.add_argument("--engine-profile", required=True)
    ap.add_argument("--qe-profile")
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--key-file", type=Path, default=Path("/root/.config/book2course/openrouter-api-key"))
    ap.add_argument("--generator-model", default="deepseek/deepseek-v4-flash-0731")
    ap.add_argument("--judge-model", default="google/gemini-3.1-flash-lite")
    ap.add_argument("--en-only", action="store_true")
    args = ap.parse_args()

    work = args.work_dir
    store = FileArtifactStore(work / "artifacts")
    cache = FileLLMCache(work / "llm-cache")
    key = args.key_file.read_text().strip()

    def role(model: str, name: str) -> ModelRole:
        gw = LLMGateway(OPENROUTER, model, cache=cache, allow_remote=True, api_key=key, timeout_s=300,
                        reasoning_effort="none" if model.startswith("deepseek/") else None)
        return ModelRole(gw, ProducerIdentity(role=name, provider="openrouter", model_id=model), model.split("/")[0])

    generator, judge = role(args.generator_model, "generator"), role(args.judge_model, "validator")
    profiles = json.loads(args.profiles.read_text())
    runtime = ProfileRuntime(TranslationProfiles(engines={p["id"]: p for p in profiles["engines"]},
                                                 qe={p["id"]: p for p in profiles.get("qe", [])}),
                             gateway_factory=translation_gateway_factory(cache))
    results: dict[str, UnitTranslation] = {}
    for raw in args.results.read_text().splitlines():
        row = json.loads(raw)
        results[row["unit_id"]] = UnitTranslation.model_validate(row["t"])
    revision = git_revision()
    promotion = PromotionService(MemoryRegistry(), code_revision=revision)
    acts = EpisodeActivities(artifact_store=store, generator=generator, judge=judge, runtime=runtime,
                             memory=FileMemory(work / "memory.json"), book_results=lambda run_id: results,
                             promotion=promotion, workers=4)
    parsed = import_input(store, "ingest-parse-pdf", "parsed-book.json", args.parsed)
    outline_name = "approved-outline.json" if "approved-outline" in args.outline.name else "course-outline.json"
    outline = import_input(store, "approve-outline", outline_name, args.outline)
    units = import_input(store, "translation-units", "units.json", args.units)
    termbase = import_input(store, "book-termbase-approved", "termbase.json", args.termbase)
    concepts = store.import_dir(COURSE, "concept-layer", args.concepts)
    imported = {"concept-layer": promote_recorded(promotion, store, concepts)}
    dossier = None
    if args.dossier:
        dossier = store.import_dir(COURSE, "knowledge-research", args.dossier)
        imported["knowledge-research"] = promote_recorded(promotion, store, dossier)
    d = lambda r: r.model_dump(mode="json") if r else None
    refs = dict(course_id=COURSE, parsed_ref=d(parsed), outline_ref=d(outline), units_ref=d(units),
                concept_ref=d(concepts), dossier_ref=d(dossier), translation_run_id=args.run_id)
    out: dict[str, Any] = {"code_revision": revision, "imported": imported, "episodes": {}}
    for episode in args.episodes:
        t0 = time.monotonic()
        en = acts._build_en(EpisodeScriptENInput(**refs, episode=episode))
        entry: dict[str, Any] = {"en": en, "en_seconds": round(time.monotonic() - t0, 1)}
        print(json.dumps({"episode": episode, "en": en["stage_output_status"], "gates": en["gates"]}), flush=True)
        if not args.en_only and en["has_script"] and en["stage_output_status"] == "accepted":
            t1 = time.monotonic()
            ar = acts._translate(EpisodeScriptARInput(**refs, episode=episode, en_ref=en["ref"], termbase_ref=d(termbase),
                                                      engine_profile=args.engine_profile, qe_profile=args.qe_profile))
            entry.update(ar=ar, ar_seconds=round(time.monotonic() - t1, 1))
            print(json.dumps({"episode": episode, "ar": ar["stage_output_status"], "gates": ar["gates"]}), flush=True)
        out["episodes"][f"{episode:02d}"] = entry
    out["llm_usage_this_process"] = {"generator": generator.llm.usage_totals, "validator": judge.llm.usage_totals}
    summary = work / "summary.json"
    summary.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({"summary": str(summary), "usage": out["llm_usage_this_process"]}, default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
