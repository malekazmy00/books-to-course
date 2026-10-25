"""Run knowledge research + the concept layer for one book from a development machine (no Temporal).

Uses the SAME activity code as the worker (``b2c_orchestration.knowledge.KnowledgeActivities``) with
a local file artifact store, an in-memory stage-output registry (promotion + GateResults as in
production), an on-disk LLM cache and an on-disk fetch cache, all under ``--work-dir`` (outside git).
Models are reached through b2c_llm_gateway (external OpenRouter models are a D-029 development
opt-in; the key file stays outside the repository and is never printed).

Example (inputs exported read-only from MinIO into /root/.cache/b2c-knowledge/minio):

    uv run python scripts/knowledge/run_knowledge.py --name sat \
        --parsed  <dump>/ingest-parse-pdf/<key>/parsed-book.json \
        --outline <dump>/approve-outline/<key>/approved-outline.json \
        --termbase <dump>/book-termbase-approved/<key>/termbase.json \
        --work-dir /root/.cache/b2c-knowledge/run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from b2c_concepts import ConceptModel
from b2c_contracts import ArtifactRef, ProducerIdentity, StageOutputRecord, GateResult
from b2c_core.stage_registry import TransitionRefused
from b2c_llm_gateway import LLMGateway
from b2c_orchestration.knowledge import BuildConceptLayerInput, KnowledgeActivities, ResearchKnowledgeInput
from b2c_orchestration.promotion import PromotionService
from b2c_research import ModelRole
from b2c_research.http import FileFetchCache, HttpxFetcher
from b2c_research.providers import standard_providers

OPENROUTER = "https://openrouter.ai/api/v1"
COURSE = "b2c-dev-knowledge"


class LocalArtifactStore:
    """Same surface as b2c_core.ArtifactStore for what the knowledge activities use."""
    bucket = "local"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _dir(self, course_id: str, stage: str, key: str) -> Path:
        return self.root / course_id / stage / key

    def _ref(self, course_id: str, stage: str, key: str) -> ArtifactRef:
        d = self._dir(course_id, stage, key)
        manifest = json.loads((d / "_manifest.json").read_text())
        return ArtifactRef.model_validate(manifest)

    def exists(self, course_id: str, stage: str, key: str) -> ArtifactRef | None:
        return self._ref(course_id, stage, key) if (self._dir(course_id, stage, key) / "_manifest.json").exists() else None

    def put(self, course_id: str, stage: str, key: str, files: dict[str, bytes]) -> ArtifactRef:
        d = self._dir(course_id, stage, key)
        d.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        for name in sorted(files):
            (d / name).write_bytes(files[name])
            h.update(name.encode() + b"\0" + hashlib.sha256(files[name]).hexdigest().encode())
        ref = ArtifactRef(stage=stage, key=key, uri=f"file://{d}/", sha256=h.hexdigest(),
                          created_at=datetime.now(timezone.utc))
        (d / "_manifest.json").write_text(ref.model_dump_json())
        return ref

    def get_or_compute(self, course_id: str, stage: str, key: str, compute) -> tuple[ArtifactRef, bool]:
        hit = self.exists(course_id, stage, key)
        if hit is not None:
            return hit, True
        return self.put(course_id, stage, key, compute()), False

    def read(self, ref: ArtifactRef, name: str) -> bytes:
        return (Path(ref.uri.removeprefix("file://")) / name).read_bytes()

    def list_files(self, ref: ArtifactRef) -> list[str]:
        return sorted(p.name for p in Path(ref.uri.removeprefix("file://")).iterdir() if p.name != "_manifest.json")


class FileLLMCache:
    """LLMCache protocol on disk (temperature-0 calls only, as in the gateway)."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        p = self.dir / f"{key}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, key: str, model_id: str, value: dict[str, Any], *, replace: bool = False) -> None:
        p = self.dir / f"{key}.json"
        with self.lock:
            if replace or not p.exists():
                p.write_text(json.dumps(value, ensure_ascii=False))


class MemoryRegistry:
    """The StageOutputRegistry surface PromotionService uses, in memory (records are re-validated)."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], StageOutputRecord] = {}

    def register(self, record: StageOutputRecord) -> bool:
        k = (record.course_id, record.artifact.stage, record.artifact.key)
        if k in self.records:
            return False
        self.records[k] = record
        return True

    def add_gate_result(self, course_id: str, result: GateResult) -> bool:
        k = (course_id, result.artifact.stage, result.artifact.key)
        rec = self.records[k]
        self.records[k] = StageOutputRecord.model_validate({**rec.model_dump(), "gate_results": [
            *rec.gate_results, result]})
        return True

    def transition(self, course_id, stage, key, to_status, *, actor, code_revision=None, reason=None):
        from b2c_contracts import STAGE_OUTPUT_TRANSITIONS
        rec = self.records[(course_id, stage, key)]
        if to_status not in STAGE_OUTPUT_TRANSITIONS[rec.status]:
            raise TransitionRefused(f"{rec.status} -> {to_status}")
        new = StageOutputRecord.model_validate({**rec.model_dump(), "status": to_status, "status_reason": reason,
                                                "status_changed_at": datetime.now(timezone.utc),
                                                "status_changed_by": actor})
        self.records[(course_id, stage, key)] = new
        return new

    def get(self, course_id, stage, key):
        return self.records.get((course_id, stage, key))

    def accepted(self, *, course_id, stage, limit=100):
        return [r for r in self.records.values() if r.course_id == course_id and r.stage == stage
                and r.status == "accepted"][:limit]


def git_revision() -> str:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
    return head + ("-dirty" if dirty else "")


def import_input(store: LocalArtifactStore, stage: str, name: str, path: Path) -> ArtifactRef:
    data = path.read_bytes()
    return store.get_or_compute("inputs", stage, hashlib.sha256(data).hexdigest(), lambda: {name: data})[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--parsed", type=Path, required=True)
    ap.add_argument("--outline", type=Path, required=True, help="approved-outline.json or course-outline.json")
    ap.add_argument("--termbase", type=Path)
    ap.add_argument("--units", type=Path)
    ap.add_argument("--meta-override", type=json.loads)
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--key-file", type=Path, default=Path("/root/.config/book2course/openrouter-api-key"))
    ap.add_argument("--extractor-model", default="deepseek/deepseek-v4-flash-0731")
    ap.add_argument("--judge-model", default="google/gemini-3.1-flash-lite")
    ap.add_argument("--concepts-model", default=None)
    ap.add_argument("--max-research-concepts", type=int, default=10)
    ap.add_argument("--max-candidates", type=int, default=80)
    ap.add_argument("--max-model-concepts", type=int)
    ap.add_argument("--use-unaccepted-dossier", action="store_true",
                    help="development only: give the concept layer a dossier that is not accepted")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--reasoning-effort", default="none",
                    help="sent to deepseek models (as the deepseek-mqm profile does); thinking tokens otherwise "
                         "exhaust max_tokens")
    args = ap.parse_args()

    work = args.work_dir
    store = LocalArtifactStore(work / "artifacts")
    cache = FileLLMCache(work / "llm-cache")
    key = args.key_file.read_text().strip()

    def role(model: str, name: str) -> ModelRole:
        effort = args.reasoning_effort if model.startswith("deepseek/") else None
        gw = LLMGateway(OPENROUTER, model, cache=cache, allow_remote=True, api_key=key, timeout_s=300,
                        reasoning_effort=effort)
        return ModelRole(gw, ProducerIdentity(role=name, provider="openrouter", model_id=model), model.split("/")[0])

    extractor, judge = role(args.extractor_model, "extractor"), role(args.judge_model, "judge")
    concepts = role(args.concepts_model or args.extractor_model, "concepts")
    fetcher = HttpxFetcher(cache=FileFetchCache(work / "fetch-cache"))
    revision = git_revision()
    promotion = PromotionService(MemoryRegistry(), code_revision=revision)
    acts = KnowledgeActivities(artifact_store=store, extractor=extractor, judge=judge,
                               concept_model=ConceptModel(concepts.llm, concepts.identity),
                               providers=lambda b, m: standard_providers(b, m, fetcher=fetcher),
                               promotion=promotion, workers=args.workers)
    parsed = import_input(store, "ingest-parse-pdf", "parsed-book.json", args.parsed)
    outline_name = "approved-outline.json" if "approved-outline" in args.outline.name else "course-outline.json"
    outline = import_input(store, "approve-outline", outline_name, args.outline)
    termbase = import_input(store, "book-termbase", "termbase.json", args.termbase) if args.termbase else None
    units = import_input(store, "translation-units", "units.json", args.units) if args.units else None
    dump = lambda r: r.model_dump(mode="json") if r else None
    t0 = time.monotonic()
    research = acts._research(ResearchKnowledgeInput(
        course_id=COURSE, parsed_ref=dump(parsed), termbase_ref=dump(termbase), units_ref=dump(units),
        meta_override=args.meta_override, max_research_concepts=args.max_research_concepts,
        max_candidates=args.max_candidates))
    t1 = time.monotonic()
    layer = acts._concepts(BuildConceptLayerInput(
        course_id=COURSE, parsed_ref=dump(parsed), outline_ref=dump(outline), termbase_ref=dump(termbase),
        units_ref=dump(units), dossier_ref=research["ref"], require_accepted_dossier=not args.use_unaccepted_dossier,
        max_candidates=args.max_candidates, max_model_concepts=args.max_model_concepts))
    t2 = time.monotonic()
    usage = {n: r.llm.usage_totals for n, r in (("extractor", extractor), ("judge", judge), ("concepts", concepts))}
    records = {k[1]: {"status": r.status, "reason": r.status_reason,
                      "gates": {g.gate_id: g.passed for g in r.gate_results}}
               for k, r in promotion.registry.records.items()}
    summary = {"name": args.name, "code_revision": revision, "research": research, "concepts": layer,
               "seconds": {"research": round(t1 - t0, 1), "concepts": round(t2 - t1, 1)},
               "llm_usage_this_process": usage, "registry": records}
    out = work / f"summary-{args.name}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({"summary": str(out), "research_status": research["stage_output_status"],
                      "concepts_status": layer["stage_output_status"], "seconds": summary["seconds"],
                      "usage": usage}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
