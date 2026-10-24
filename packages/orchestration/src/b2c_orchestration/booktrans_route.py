"""Isolated adapter for BookTrans Pro's whole-book scout/translate/edit/verify passes."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

from b2c_translation.generation import SourceSegment, TranslatedSegment


class BookTransRouteError(RuntimeError):
    pass


def run_booktrans_pro(
    segments: Sequence[SourceSegment],
    *,
    work_root: str,
    source_identity: str,
    model_id: str,
    base_url: str,
    api_key: str | None,
    source_language: str,
    target_language: str,
    glossary: Mapping[str, str] | None,
    domain_id: str | None = None,
    seed_translations: Sequence[TranslatedSegment] = (),
    prompt_version: str = "b2c-booktrans-pro-v1",
    timeout_seconds: int = 23 * 60 * 60,
) -> list[TranslatedSegment]:
    """Run BookTrans' persistent whole-book passes and return ID-preserving text.

    Work directories are content-addressed so Temporal retries and route resumes
    reuse BookTrans' own chapter checkpoints. Secrets are passed only in the
    child environment and never written to its workspace or command line.
    """
    if not segments:
        raise BookTransRouteError("BookTrans input has no source segments")
    segment_ids = [item.segment_id for item in segments]
    if len(segment_ids) != len(set(segment_ids)):
        raise BookTransRouteError("BookTrans input contains duplicate source segment IDs")
    if source_language not in {"ar", "en"} or target_language not in {"ar", "en"}:
        raise BookTransRouteError("BookTrans route supports only Arabic and English")
    if not model_id.strip() or not base_url.startswith(("https://", "http://")):
        raise BookTransRouteError("BookTrans model endpoint is not configured")
    if base_url.startswith("http://") and not any(
        host in base_url for host in ("localhost", "127.0.0.1", "gemma4-ollama", "llm:")
    ):
        raise BookTransRouteError("BookTrans refuses non-local cleartext model endpoints")
    identity = hashlib.sha256(json.dumps({
        "source": source_identity, "model": model_id, "base_url": base_url,
        "source_language": source_language, "target": target_language,
        "glossary": dict(glossary or {}),
        "domain_id": domain_id, "prompt_version": prompt_version,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    work = Path(work_root) / identity
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        work.chmod(0o700)
    except OSError:
        pass

    # Native BookTrans book.json preserves B2C's immutable source IDs exactly.
    source_manifest = {
        "meta": {"title": "Book2Course translation", "lang": source_language},
        "blocks": [{"id": item.segment_id, "kind": "p", "text": item.text}
                   for item in segments],
    }
    book = work / "book.json"
    source_bytes = json.dumps(source_manifest, ensure_ascii=False, sort_keys=True).encode()
    if book.exists():
        try:
            existing = json.loads(book.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BookTransRouteError("BookTrans saved book checkpoint is unreadable") from exc
        if existing != source_manifest:
            raise BookTransRouteError("BookTrans saved checkpoint belongs to different source content")
    else:
        temp = book.with_suffix(".json.tmp")
        temp.write_bytes(source_bytes)
        temp.replace(book)

    prompt_path = work / "b2c-instructions.md"
    instructions = [
        "Translate the complete supplied book faithfully into natural Modern Standard Arabic."
        if target_language == "ar" else
        "Translate the complete supplied book faithfully into natural English.",
        "Keep every fact, qualification, negation, example, number, proper name, and paragraph. Never summarize or omit.",
        "Preserve paragraph order and all source IDs. Keep stable terminology throughout the book.",
        "Treat the following glossary as binding for this book. Use each listed equivalent when its source term occurs in context.",
    ]
    if domain_id:
        instructions.append(f"Book domain: {domain_id}. Select terminology by this book's domain and local context.")
    for term, equivalent in (glossary or {}).items():
        instructions.append(f"- {term} = {equivalent}")
    prompt_text = "\n".join(instructions) + "\n"
    if prompt_path.exists() and prompt_path.read_text(encoding="utf-8") != prompt_text:
        raise BookTransRouteError("BookTrans instruction checkpoint does not match the selected glossary")
    if not prompt_path.exists():
        prompt_path.write_text(prompt_text, encoding="utf-8")
        try:
            prompt_path.chmod(0o600)
        except OSError:
            pass

    # Seed fully completed BookTrans chunks from validated B2C checkpoints.
    # This avoids repeating translation inference when the operator switches
    # routes; BookTrans still runs its independent edit and verification passes.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "vendor" / "booktrans"))
        from booktrans import pipeline as bt_pipeline
        chunks = bt_pipeline.make_chunks(source_manifest["blocks"], target=1800)
        seeded = {item.segment_id: item.text for item in seed_translations}
        tr_dir = Path(bt_pipeline.lpath(str(work), "tr", target_language))
        tr_dir.mkdir(parents=True, exist_ok=True)
        for chunk in chunks:
            blocks = chunk["blocks"]
            ids = [block["id"] for block in blocks]
            if not ids or not all(segment_id in seeded for segment_id in ids):
                continue
            path = tr_dir / f"{chunk['index']:04d}.json"
            payload = {
                "index": chunk["index"],
                "tr": {segment_id: seeded[segment_id] for segment_id in ids},
                "src": {block["id"]: bt_pipeline.fingerprint(block["text"]) for block in blocks},
                "usage": [],
            }
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if any(existing.get("src", {}).get(segment_id) != payload["src"][segment_id]
                       for segment_id in ids):
                    raise BookTransRouteError("BookTrans saved translation belongs to different source content")
                continue
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
    except BookTransRouteError:
        raise
    except Exception as exc:
        raise BookTransRouteError("Could not prepare compatible saved paragraphs for BookTrans") from exc

    source_path = work / "input.md"
    if not source_path.exists():
        source_path.write_text("# Book2Course source\n", encoding="utf-8")
    vendor_root = Path(__file__).resolve().parents[4] / "vendor" / "booktrans"
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(vendor_root),
        "OPENAI_BASE_URL": base_url.rstrip("/"),
        "OPENAI_API_KEY": api_key or "local-no-secret",
        "BT_AGENT": "openai",
        "BT_MODEL": model_id,
        "BT_TO": target_language,
        "BT_UI": "ar",
    })
    command = [
        sys.executable, "-m", "booktrans.cli", str(source_path),
        "--work", str(work), "--agent", "openai", "--model", model_id,
        "--to", target_language, "--prompt", str(prompt_path),
        "--skip", "ocr,structure,ocrfix,notes,index,build,qa",
        "--retries", "3", "--wait", "5", "--max-wait", str(timeout_seconds),
        "--chunk-words", "1800", "--jobs", "1", "--scout-jobs", "1",
        "--self-edit", "allow",
    ]
    try:
        result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BookTransRouteError("BookTrans reached its execution limit; saved chapter files were retained") from exc
    except OSError as exc:
        raise BookTransRouteError("BookTrans runtime is not installed in this worker") from exc
    if result.returncode != 0:
        raise BookTransRouteError(
            f"BookTrans stopped with exit code {result.returncode}; its persistent chapter files were retained"
        )

    try:
        sys.path.insert(0, str(vendor_root))
        from booktrans.pipeline import all_translations
        values, _ = all_translations(str(work), target_language)
    except Exception as exc:
        raise BookTransRouteError("BookTrans output could not be assembled from its saved passes") from exc
    expected_ids = {item.segment_id for item in segments}
    if set(values) != expected_ids:
        missing, unexpected = expected_ids - set(values), set(values) - expected_ids
        raise BookTransRouteError(
            f"BookTrans output identity mismatch (missing={len(missing)}, unexpected={len(unexpected)})"
        )
    return [TranslatedSegment(segment_id=item.segment_id, text=values[item.segment_id])
            for item in segments]


def booktrans_usage_summary(
    work_root: str, *, source_identity: str, model_id: str, base_url: str,
    source_language: str, target_language: str, glossary: Mapping[str, str] | None,
    domain_id: str | None = None, prompt_version: str = "b2c-booktrans-pro-v1",
) -> dict[str, object]:
    """Summarize provider-reported usage saved in this BookTrans run's pass files."""
    identity = hashlib.sha256(json.dumps({
        "source": source_identity, "model": model_id, "base_url": base_url,
        "source_language": source_language, "target": target_language,
        "glossary": dict(glossary or {}),
        "domain_id": domain_id, "prompt_version": prompt_version,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    work = Path(work_root) / identity
    totals: dict[str, object] = {"model": model_id, "api_calls": 0, "input_tokens": 0,
        "cached_input_tokens": 0, "output_tokens": 0, "reported_cost_usd": 0.0,
        "cost_reported_calls": 0, "passes": {}}
    for pass_name in ("tr", "ed", "vf", "nt"):
        pass_totals = {key: 0 for key in ("api_calls", "input_tokens", "cached_input_tokens",
            "output_tokens", "reported_cost_usd", "cost_reported_calls")}
        directory = work / target_language / pass_name
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                try:
                    entry = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                usage = entry.get("usage")
                usage = usage if isinstance(usage, list) else ([entry] if entry.get("model") else [])
                for item in usage:
                    if not isinstance(item, dict):
                        continue
                    pass_totals["api_calls"] += 1
                    tokens = item.get("tokens") or {}
                    for key, token_key in (("input_tokens", "in"),
                                           ("cached_input_tokens", "cached"),
                                           ("output_tokens", "out")):
                        pass_totals[key] += int(tokens.get(token_key) or 0)
                    cost = item.get("cost_usd")
                    if isinstance(cost, (int, float)):
                        pass_totals["reported_cost_usd"] += float(cost)
                        pass_totals["cost_reported_calls"] += 1
        totals["passes"][pass_name] = pass_totals
        for key, value in pass_totals.items():
            totals[key] += value
    return totals
