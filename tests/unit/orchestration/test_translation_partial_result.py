import io
import json
from types import SimpleNamespace
from datetime import UTC, datetime

from b2c_contracts import ArtifactRef
from b2c_translation import SourceSegment
from b2c_orchestration.translation import TranslateContentInput, TranslationActivities

class SelectiveGateway:
    model_id = "test-model"
    def __init__(self, failing):
        self.failing = failing
        self.ids = []
        self.versions = []
    def structured(self, messages, schema, **kwargs):
        self.versions.append(kwargs["prompt_version"])
        entries = json.loads(messages[1]["content"])["segments"]
        self.ids.extend(x["segment_id"] for x in entries)
        return schema(segments=[{"segment_id": x["segment_id"], "text": "wrong 6" if x["segment_id"] in self.failing else "correct 5"} for x in entries])



class Store:
    bucket = "artifacts"
    def __init__(self):
        self.s3 = self
        self.objects = {}
        self.artifacts = {}
    def put_object(self, **args):
        self.objects[args["Key"]] = args["Body"]
    def list_objects_v2(self, **args):
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(args["Prefix"])], "IsTruncated": False}
    def get_object(self, **args):
        return {"Body": io.BytesIO(self.objects[args["Key"]])}
    def get_or_compute(self, course, stage, key, compute):
        hit = key in self.artifacts
        if not hit: self.artifacts[key] = compute()
        return ArtifactRef(stage=stage, key=key, uri=f"s3://artifacts/{course}/{stage}/{key}", sha256=key, created_at=datetime.now(UTC)), hit


def test_partial_artifact_is_committed_then_retry_gets_distinct_complete_artifact(monkeypatch):
    import b2c_orchestration.translation as mod
    sources = [SourceSegment(segment_id=f"s{i}", text="The value is 5.", citations=[f"book:p{i+1}"]) for i in range(3)]
    monkeypatch.setattr(mod, "artifact_source_identity", lambda store, ref: {"source_parsed_sha256":ref.sha256, "source_file_sha256":None})
    monkeypatch.setattr(mod, "translation_source_segments", lambda *_: sources)
    monkeypatch.setattr(mod, "translation_manual_overrides", lambda *_: [])
    workflow_id = "b2c-translate-" + "1"*64
    monkeypatch.setattr(mod.activity, "info", lambda: SimpleNamespace(workflow_id=workflow_id))
    monkeypatch.setattr(mod.activity, "heartbeat", lambda *_: None)
    source = ArtifactRef(stage="ingest-parse-pdf", key="source", uri="s3://artifacts/c/source",
                         sha256="a"*64, created_at=datetime.now(UTC))
    inp = TranslateContentInput(course_id="c", source_artifact_ref=source.model_dump(mode="json"),
                                source_kind="book", source_language="en", target_language="ar")
    store = Store()
    first_gateway = SelectiveGateway({"s0"})
    partial = TranslationActivities(store, first_gateway).translate(inp)
    assert partial["status"] == "partial" and partial["segment_count"] == 2 and partial["total_segments"] == 3
    ref1 = partial["artifact"]["key"]
    payload = json.loads(store.artifacts[ref1]["translation.json"])
    assert payload["missing_segment_ids"] == ["s0"]
    assert store.artifacts[ref1]["translation.txt"] == b"correct 5\n\ncorrect 5"
    progress = json.loads(store.objects[f"_system/translation-progress/{workflow_id}.json"])
    assert progress["status"] == "PARTIAL" and progress["retry_segments"] == 1
    # Initial request, one immediate correction, one deferred correction. The
    # final result summary must not count the same failure as a fourth attempt.
    assert progress["failed_units"][0]["attempts"] == 3
    assert all("phase7-translation-v7-source-boundary" in version for version in first_gateway.versions)
    previous_workflow = workflow_id
    workflow_id = "b2c-translate-" + "2"*64
    inp = inp.model_copy(update={"resume_from_workflow_id": previous_workflow})
    gateway = SelectiveGateway(set())
    complete = TranslationActivities(store, gateway).translate(inp)
    assert gateway.ids == ["s0"]
    assert complete["status"] == "completed" and complete["segment_count"] == 3
    assert complete["artifact"]["key"] != ref1
    assert partial["artifact"]["key"] in store.artifacts

    assert all(previous_workflow in v for v in first_gateway.versions)
    assert all(workflow_id in v for v in gateway.versions)
    assert set(first_gateway.versions).isdisjoint(gateway.versions)


def test_same_workflow_prompt_upgrade_reuses_compatible_old_checkpoints(monkeypatch):
    import b2c_orchestration.translation as mod

    sources = [SourceSegment(segment_id=f"s{i}", text="The value is 5.", citations=[f"book:p{i+1}"])
               for i in range(3)]
    monkeypatch.setattr(mod, "artifact_source_identity", lambda store, ref:
                        {"source_parsed_sha256": ref.sha256, "source_file_sha256": None})
    monkeypatch.setattr(mod, "translation_source_segments", lambda *_: sources)
    monkeypatch.setattr(mod, "translation_manual_overrides", lambda *_: [])
    workflow_id = "b2c-translate-" + "3" * 64
    monkeypatch.setattr(mod.activity, "info", lambda: SimpleNamespace(workflow_id=workflow_id))
    monkeypatch.setattr(mod.activity, "heartbeat", lambda *_: None)

    source = ArtifactRef(stage="ingest-parse-pdf", key="source", uri="s3://artifacts/c/source",
                         sha256="b" * 64, created_at=datetime.now(UTC))
    inp = TranslateContentInput(course_id="c", source_artifact_ref=source.model_dump(mode="json"),
                                source_kind="book", source_language="en", target_language="ar")
    store = Store()
    initial = TranslationActivities(store, SelectiveGateway(set())).translate(inp)
    assert initial["status"] == "completed"

    # Model a durable v5 checkpoint under this same workflow ID after code has
    # moved to v6; only the version/fingerprint/path differ.
    current_prefix = f"_system/translation-checkpoints/{workflow_id}/"
    current_key = next(k for k in store.objects if k.startswith(current_prefix) and k.endswith(".json"))
    old = json.loads(store.objects.pop(current_key))
    old["prompt_version"] = "phase7-translation-v5"
    old["run_fingerprint"] = "legacy-v5-fingerprint"
    old_key = f"{current_prefix}legacy-v5/batch-{int(old['batch_index']):06d}.json"
    old["segments"] = [item for item in old["segments"] if item["segment_id"] != "s0"]
    store.objects[old_key] = json.dumps(old).encode()

    # Checkpoint recovery must keep s1/s2 and regenerate only missing s0.
    gateway = SelectiveGateway(set())
    resumed = TranslationActivities(store, gateway).translate(inp)
    assert gateway.ids == ["s0"]
    assert resumed["status"] == "completed"
    assert resumed["segment_count"] == 3
