"""Optional local SWEET editor; rejected edits retain the accepted translation."""
from __future__ import annotations
import hashlib
import json
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from datetime import UTC, datetime

POLICY = "sweet-qalb-conservative-v1"


def assess_edit(source, original, candidate, glossary=None):
    from .generation import (_number_tokens, _table_saved_valid,
        translation_completeness_issue, terminology_violations)
    reason = None
    if not candidate.strip():
        reason = "empty_candidate"
    elif _number_tokens(original) != _number_tokens(candidate):
        reason = "numbers_changed"
    elif original.count("\n") != candidate.count("\n"):
        reason = "paragraph_structure_changed"
    elif not _table_saved_valid(source, candidate):
        reason = "table_structure_changed"
    elif translation_completeness_issue(source, candidate, target_language="ar"):
        reason = "completeness_rejected"
    elif terminology_violations(source, candidate, glossary or {}):
        reason = "terminology_changed"
    elif re.findall(r"(?<!\w)(?:لا|ليس|ليست|لم|لن|بدون|يجب|قد|يمكن|ينبغي|كل|بعض|جميع)(?!\w)", original) != re.findall(r"(?<!\w)(?:لا|ليس|ليست|لم|لن|بدون|يجب|قد|يمكن|ينبغي|كل|بعض|جميع)(?!\w)", candidate):
        reason = "logical_qualification_changed"
    elif SequenceMatcher(None, original, candidate, autojunk=False).ratio() < .90:
        reason = "edit_budget_exceeded"
    elif re.findall(r"[A-Za-z][A-Za-z0-9_:/.-]*", original) != re.findall(r"[A-Za-z][A-Za-z0-9_:/.-]*", candidate):
        reason = "literal_name_changed"
    return {"text": original if reason else candidate,
            "status": "rejected" if reason else "unchanged" if candidate == original else "accepted",
            "reason": reason or "invariants_preserved",
            "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest()}


class SweetEditor:
    def __init__(self, root, source_dir, device="cpu", threshold=.95):
        import sys
        sys.path.insert(0, source_dir)
        import torch
        from transformers import BertTokenizer, BertForTokenClassification
        from edits.edit import SubwordEdit
        self.torch, self.SubwordEdit, self.device, self.threshold = torch, SubwordEdit, device, threshold
        self.models = []
        for suffix in ("nopnx", "pnx"):
            path = str(Path(root) / f"text-editing-qalb14-{suffix}")
            tokenizer = BertTokenizer.from_pretrained(path, local_files_only=True)
            model = BertForTokenClassification.from_pretrained(path, local_files_only=True).to(device).eval()
            self.models.append((tokenizer, model))

    def predict(self, text, tokenizer, model):
        inputs = tokenizer(text.split(), return_tensors="pt", is_split_into_words=True, truncation=False)
        if inputs["input_ids"].shape[1] > model.config.max_position_embeddings:
            raise ValueError("sentence_exceeds_model_context")
        with self.torch.inference_mode():
            scores = model(**{k: v.to(self.device) for k, v in inputs.items()}).logits[0].softmax(dim=-1)
        probs, labels = scores.max(dim=-1)
        tokens = tokenizer.convert_ids_to_tokens(inputs["input_ids"][0][1:-1])
        edits = [model.config.id2label[int(label)] if float(prob) >= self.threshold else "K*"
                 for label, prob in zip(labels[1:-1], probs[1:-1])]
        rewritten = []
        for token, edit in zip(tokens, edits):
            operation = self.SubwordEdit(subword=token, raw_subword=token, edit=edit)
            rewritten.append(operation.apply(token) if operation.is_applicable(token) else token)
        merged = []
        for token, edit in zip(rewritten, edits):
            if edit.startswith("M") and merged:
                merged[-1] += token
            else:
                merged.append(token)
        detokenized = []
        for token in merged:
            if token.startswith("##"):
                if not detokenized:
                    raise ValueError("invalid wordpiece continuation")
                detokenized[-1] += token[2:]
            else:
                detokenized.append(token)
        return " ".join(detokenized)

    def __call__(self, text):
        parts = re.split(r"(\n+)", text)
        for i, part in enumerate(parts):
            if not part.strip() or part.startswith("\n"):
                continue
            sentences = re.split(r"(?<=[.!؟?؛])\s+", part)
            edited = []
            for sentence in sentences:
                for tokenizer, model in self.models[:1] * 2 + self.models[1:]:
                    sentence = self.predict(sentence, tokenizer, model)
                edited.append(sentence)
            parts[i] = " ".join(edited)
        return "".join(parts)


@lru_cache(maxsize=1)
def configured_editor():
    return SweetEditor(os.environ["B2C_ARABIC_GEC_MODEL_DIR"],
        os.environ["B2C_ARABIC_GEC_SOURCE_DIR"], os.getenv("B2C_ARABIC_GEC_DEVICE", "cpu"))


def correction_identity():
    if os.getenv("B2C_ARABIC_GEC_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        return {"policy": "disabled"}
    root = Path(os.environ["B2C_ARABIC_GEC_MODEL_DIR"])
    manifest = (root / "download-manifest.json").read_bytes()
    digest = hashlib.sha256(manifest).hexdigest()
    if digest != os.environ["B2C_ARABIC_GEC_MANIFEST_SHA256"]:
        raise ValueError("Arabic correction manifest checksum mismatch")
    return {"policy": POLICY, "model_manifest_sha256": digest}


def correct_pack(pack, sources, store, *, glossary=None, editor=None, progress_callback=None):
    identity = correction_identity()
    if pack.target_language != "ar" or identity["policy"] == "disabled":
        return pack, []
    engine, by_id = editor or configured_editor(), {item.segment_id: item for item in sources}
    accepted, audit = [], []
    cache_hits = cache_misses = accepted_edits = rejected_edits = 0
    started_at = datetime.now(UTC).isoformat()
    total = len(pack.segments)

    def report(event: str, processed: int, current_segment_id: str = "", last_segment_id: str = "") -> None:
        if progress_callback is None:
            return
        progress_callback({
            "event": event,
            "status": "completed" if event == "completed" else "running",
            "processed_segments": processed,
            "total_segments": total,
            "remaining_segments": max(0, total - processed),
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "accepted_edits": accepted_edits,
            "rejected_edits": rejected_edits,
            "current_segment_id": current_segment_id,
            "last_segment_id": last_segment_id,
            "started_at": started_at,
            "updated_at": datetime.now(UTC).isoformat(),
        })

    report("started", 0)
    for index, item in enumerate(pack.segments, 1):
        source = by_id[item.segment_id]
        report("segment_started", index - 1, current_segment_id=item.segment_id)
        unit_identity = {**identity, "source_sha256": hashlib.sha256(source.text.encode()).hexdigest(),
            "original_sha256": hashlib.sha256(item.text.encode()).hexdigest(),
            "glossary": {**dict(glossary or {}), **getattr(source, "glossary", {})}}
        digest = hashlib.sha256(json.dumps(unit_identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = f"_system/arabic-correction/{digest}.json"
        try:
            saved = json.loads(store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read())
            cache_hits += 1
        except Exception as exc:
            if getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise
            try:
                decision = assess_edit(source.text, item.text, engine(item.text), unit_identity["glossary"])
            except (ValueError, RuntimeError) as exc:
                decision = assess_edit(source.text, item.text, item.text, unit_identity["glossary"])
                decision.update(status="rejected", reason=type(exc).__name__)
            saved = {"identity": unit_identity, "original": item.text, "decision": decision}
            store.s3.put_object(Bucket=store.bucket, Key=key,
                Body=json.dumps(saved, ensure_ascii=False).encode(), ContentType="application/json")
            cache_misses += 1
        if saved.get("identity") != unit_identity or saved.get("original") != item.text:
            raise ValueError("Arabic correction cache identity mismatch")
        decision = saved["decision"]
        check = assess_edit(source.text, item.text, decision["text"], unit_identity["glossary"])
        accepted.append(item.model_copy(update={"text": check["text"]}))
        if decision.get("status") == "accepted":
            accepted_edits += 1
        elif decision.get("status") == "rejected":
            rejected_edits += 1
        audit.append({"segment_id": item.segment_id, "cache_key": key,
                      **{k: v for k, v in decision.items() if k != "text"}})
        report("segment_completed", index, last_segment_id=item.segment_id)
    report("completed", total)
    return pack.model_copy(update={"segments": accepted}), audit
