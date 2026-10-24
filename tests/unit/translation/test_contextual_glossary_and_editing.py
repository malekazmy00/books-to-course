import hashlib
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
import pytest
from b2c_translation.glossary import ContextualGlossary, glossary_identity
from b2c_translation.generation import SourceSegment, TranslatedSegment, translate_segments, terminology_violations
from b2c_translation.arabic_correction import assess_edit, correct_pack
from b2c_contracts import ArtifactRef

def snapshot(domain="intelligence-analysis", target="استخبارات"):
    return ContextualGlossary(domain_id=domain, mappings={"intelligence": target})

def test_contextual_senses_are_separate_and_match_source_words():
    intel = snapshot()
    ai = snapshot("artificial-intelligence", "ذكاء")
    assert glossary_identity(intel) != glossary_identity(ai)
    assert terminology_violations("Intelligence analysis.", "تحليل الاستخبارات.", intel.mappings) == []
    assert terminology_violations("Artificial intelligence.", "الذكاء الاصطناعي.", ai.mappings) == []
    assert terminology_violations("Intelligence analysis.", "تحليل الحكمة.", intel.mappings) == ["intelligence"]
    assert terminology_violations("Intelligently assess facts.", "تقييم الحقائق.", intel.mappings) == []
    assert terminology_violations("Intelligences differ.", "تختلف القدرات.", intel.mappings) == []

def test_glossary_snapshot_has_stable_identity_and_rejects_false_hash():
    first = ContextualGlossary(domain_id="test", mappings={" B ":" ب ", "A":"أ"})
    second = ContextualGlossary(domain_id="test", mappings={"A":"أ", "B":"ب"})
    assert first.sha256 == second.sha256
    with pytest.raises(ValueError, match="checksum"):
        ContextualGlossary(domain_id="test", mappings={"A":"أ"}, sha256="0"*64)
    with pytest.raises(ValueError, match="duplicate"):
        ContextualGlossary(domain_id="test", mappings={"A":"أ", "a":"ب"})

@pytest.mark.parametrize("original,candidate,reason", [
    ("القيمة هي 5.", "القيمة هي 6.", "numbers_changed"),
    ("لا يمكن قبول نتيجة التحليل دون التحقق من الأدلة والمعلومات المتاحة.", "يمكن قبول نتيجة التحليل دون التحقق من الأدلة والمعلومات المتاحة.", "logical_qualification_changed"),
    ("يشير المحلل Robert إلى أن نتيجة التحليل تحتاج إلى مزيد من التحقق والمراجعة.", "يشير المحلل David إلى أن نتيجة التحليل تحتاج إلى مزيد من التحقق والمراجعة.", "literal_name_changed"),
])
def test_correction_rejects_semantic_changes(original, candidate, reason):
    decision = assess_edit("A source statement.", original, candidate)
    assert decision["status"] == "rejected"
    assert decision["reason"] == reason
    assert decision["text"] == original

def test_correction_accepts_small_grammar_edit_and_keeps_term():
    original = "المحللون يعمل في تحليل الاستخبارات."
    candidate = "المحللون يعملون في تحليل الاستخبارات."
    result = assess_edit("Analysts work in intelligence analysis.", original, candidate, snapshot().mappings)
    assert result["status"] == "accepted"
    assert result["text"] == candidate
    changed = assess_edit("Intelligence analysis.", "تحليل الاستخبارات ضروري لفهم الأدلة وتقييم المعلومات المتاحة.", "تحليل الحكمة ضروري لفهم الأدلة وتقييم المعلومات المتاحة.", snapshot().mappings)
    assert changed["reason"] == "terminology_changed"

class Gateway:
    model_id = "fake"
    def __init__(self, target):
        self.target, self.calls = target, []
    def structured(self, messages, schema, **kwargs):
        data = json.loads(messages[1]["content"])
        self.calls.append(data)
        return schema(segments=[{"segment_id": s["segment_id"], "text": self.target} for s in data["segments"]])

def test_changed_glossary_rejects_old_sense_checkpoint_and_retranslates():
    source = SourceSegment(segment_id="p1", text="Intelligence analysis assesses uncertainty.")
    gateway = Gateway("تحليل الاستخبارات يقيّم عدم اليقين.")
    result = translate_segments([source], gateway, source_language="en", target_language="ar",
        glossary=snapshot().mappings, resume_segments=[TranslatedSegment(segment_id="p1", text="تحليل الذكاء يقيّم عدم اليقين.")])
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["glossary"] == snapshot().mappings
    assert result.segments[0].text == gateway.target

def test_wrong_terminology_is_durable_partial_not_accepted():
    source = SourceSegment(segment_id="p1", text="Intelligence analysis assesses uncertainty.")
    result = translate_segments([source], Gateway("تحليل الحكمة يقيّم عدم اليقين."),
        source_language="en", target_language="ar", glossary=snapshot().mappings)
    assert result.status == "partial"
    assert result.missing_segment_ids == ["p1"]
    assert result.segments == []

def test_glossary_checkpoint_identity_is_bidirectional_and_editor_independent():
    from b2c_orchestration.translation import merge_translation_checkpoint
    source = SourceSegment(segment_id="p1", text="Intelligence analysis.")
    saved = {"batch_index":1, "completed_at":datetime.now(UTC).isoformat(),
        "segments":[{"segment_id":"p1", "text":"تحليل الاستخبارات."}],
        "glossary_sha256":snapshot().sha256, "arabic_editing":{"policy":"old-editor"}}
    selected = {}
    merge_translation_checkpoint(saved, {}, {"p1":source}, selected)
    assert not selected  # explicit glossary cannot leak into a no-glossary run
    merge_translation_checkpoint(saved, {"glossary_sha256":snapshot("ai","ذكاء").sha256}, {"p1":source}, selected)
    assert not selected
    merge_translation_checkpoint(saved, {"glossary_sha256":snapshot().sha256}, {"p1":source}, selected)
    assert list(selected) == ["p1"]  # raw remains reusable when only editor changes

class Missing(Exception):
    response = {"ResponseMetadata":{"HTTPStatusCode":404}}

class Store:
    bucket = "artifacts"
    def __init__(self):
        self.s3, self.objects, self.artifacts = self, {}, {}
    def put_object(self, **args):
        self.objects[args["Key"]] = args["Body"]
    def get_object(self, **args):
        if args["Key"] not in self.objects:
            raise Missing()
        return {"Body":io.BytesIO(self.objects[args["Key"]])}
    def list_objects_v2(self, **args):
        return {"Contents":[{"Key":k} for k in self.objects if k.startswith(args["Prefix"])], "IsTruncated":False}
    def get_or_compute(self, course, stage, key, compute):
        self.artifacts[key] = compute()
        return ArtifactRef(stage=stage,key=key,uri=f"s3://artifacts/{course}/{stage}/{key}",sha256=key,created_at=datetime.now(UTC)),False

def test_edit_cache_preserves_original_and_reuses_only_matching_identity(monkeypatch):
    import b2c_translation.arabic_correction as mod
    from b2c_translation.generation import TranslationPack
    monkeypatch.setattr(mod,"correction_identity",lambda:{"policy":"test"})
    source = SourceSegment(segment_id="p1",text="Analysts work in intelligence analysis.")
    original = "المحللون يعمل في تحليل الاستخبارات."
    edited = "المحللون يعملون في تحليل الاستخبارات."
    pack = TranslationPack(source_language="en",target_language="ar",model_id="fake",
        segments=[TranslatedSegment(segment_id="p1",text=original)], deterministic_checks={})
    calls = []
    def editor(text):
        calls.append(text)
        return edited
    store = Store()
    result, audit = correct_pack(pack,[source],store,glossary=snapshot().mappings,editor=editor)
    again, second_audit = correct_pack(pack,[source],store,glossary=snapshot().mappings,editor=editor)
    assert result.segments[0].text == again.segments[0].text == edited
    assert calls == [original]
    saved = json.loads(store.objects[audit[0]["cache_key"]])
    assert saved["original"] == original
    assert saved["decision"]["candidate_sha256"] == hashlib.sha256(edited.encode()).hexdigest()

def test_workflow_preserves_raw_corrected_glossary_and_checkpoint(monkeypatch):
    import b2c_orchestration.translation as mod
    source = SourceSegment(segment_id="p1",text="Intelligence analysis assesses uncertainty.")
    raw = "تحليل الاستخبارات يقيّم عدم اليقين."
    corrected = raw + " "
    monkeypatch.setattr(mod,"artifact_source_identity",lambda *_:{"source_parsed_sha256":"1"*64})
    monkeypatch.setattr(mod,"translation_source_segments",lambda *_:[source])
    monkeypatch.setattr(mod,"translation_manual_overrides",lambda *_:[])
    monkeypatch.setattr(mod.activity,"info",lambda:SimpleNamespace(workflow_id="b2c-translate-"+"2"*64))
    monkeypatch.setattr(mod.activity,"heartbeat",lambda *_:None)
    monkeypatch.setattr(mod,"wait_for_activity_checkpoint",lambda *_:None)
    monkeypatch.setattr(mod,"correction_identity",lambda:{"policy":"test-editor"})
    seen = []
    def edit(pack, sources, store, **kwargs):
        seen.append(kwargs["glossary"])
        return pack.model_copy(update={"segments":[TranslatedSegment(segment_id="p1",text=corrected)]}), [{"status":"accepted"}]
    monkeypatch.setattr(mod,"correct_pack",edit)
    store=Store()
    inp=mod.TranslateContentInput(course_id="c",source_artifact_ref=ArtifactRef(stage="ingest-parse-pdf",key="1"*64,uri="s3://artifacts/c/ingest-parse-pdf/x",sha256="1"*64,created_at=datetime.now(UTC)).model_dump(mode="json"),
        source_kind="book",source_language="en",target_language="ar",contextual_glossary=snapshot())
    result=mod.TranslationActivities(store,Gateway(raw)).translate(inp)
    output=store.artifacts[result["artifact"]["key"]]
    assert json.loads(output["translation-raw.json"])["segments"][0]["text"] == raw
    assert json.loads(output["translation.json"])["segments"][0]["text"] == corrected
    assert json.loads(output["translation-glossary.json"])["sha256"] == snapshot().sha256
    assert seen == [snapshot().mappings]
    checkpoints=[json.loads(v) for k,v in store.objects.items() if k.startswith("_system/translation-checkpoints/")]
    assert checkpoints[0]["glossary_sha256"] == snapshot().sha256
    assert checkpoints[0]["arabic_editing"]["policy"] == "test-editor"
    assert checkpoints[0]["segments"][0]["text"] == raw

def test_snapshot_mutation_cannot_reuse_original_hash():
    glossary=snapshot()
    glossary.mappings["intelligence"]="حكمة"
    with pytest.raises(ValueError,match="checksum"):
        glossary_identity(glossary)

@pytest.mark.parametrize("target", ["تحليل الاستخبارات", "التحليل الاستخباراتي", "للتحليل الاستخباراتي"])
def test_selected_intelligence_sense_accepts_arabic_noun_and_adjective(target):
    terms = {"intelligence analysis": "تحليل استخباراتي", "intelligence": "استخبارات"}
    assert terminology_violations("Intelligence analysis", target, terms) == []

@pytest.mark.parametrize("target", ["تحليل الحكمة", "تحليل الذكاء", "غيراستخباراتيا"])
def test_intelligence_equivalents_reject_other_senses_and_embedded_strings(target):
    terms = {"intelligence analysis": "تحليل استخباراتي", "intelligence": "استخبارات"}
    assert set(terminology_violations("Intelligence analysis", target, terms)) == set(terms)

def test_selected_technique_phrase_accepts_same_meaning_without_relaxing_other_terms():
    assert terminology_violations("Structured Analytic Techniques", "التقنيات التحليلية المنظمة",
        {"structured analytic techniques": "تقنيات التحليل المنظم"}) == []
    assert terminology_violations("Intelligence", "استخباراتي", {"intelligence": "ذكاء"}) == ["intelligence"]
    assert terminology_violations("Intelligence", "استخبارات", {"intelligence": "الحكمة"}) == ["intelligence"]

def test_selected_arabic_term_accepts_contracted_preposition_before_definite_article():
    assert terminology_violations("Critical Thinking for Strategic Intelligence",
        "التفكير النقدي للاستخبارات الاستراتيجية", {"intelligence": "استخبارات"}) == []
    assert terminology_violations("Intelligence", "وللاستخبارات", {"intelligence": "استخبارات"}) == []
    assert terminology_violations("Intelligence", "للحكمة", {"intelligence": "استخبارات"}) == ["intelligence"]
    assert terminology_violations("Intelligence", "للذكاء", {"intelligence": "استخبارات"}) == ["intelligence"]
