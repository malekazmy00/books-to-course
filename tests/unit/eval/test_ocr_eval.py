from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import pytest
MODULE=Path(__file__).resolve().parents[3]/"eval/suites/phase2/ocr_eval.py"
SPEC=importlib.util.spec_from_file_location("b2c_phase2_ocr_eval",MODULE)
assert SPEC and SPEC.loader
ev=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(ev)
def test_nfc_graphemes_and_arabic_cer():
    assert ev.graphemes("\u0627\u0654")==ev.graphemes("\u0623")
    assert ev.score_page("\u0623 \u0628","\u0623 \u062c")["cer"]==pytest.approx(1/3)
def test_cer_wer():
    r=ev.score_page("one two","one too")
    assert r["character_edits"]==1 and r["word_edits"]==1
    assert r["cer"]==pytest.approx(1/7) and r["wer"]==pytest.approx(1/2)
def _record(page,title,split,source):
    return {"page_id":page,"title_id":title,"split":split,"page_no":1,"language":"en","stratum":"clean_print",
      "rights_status":"internal_test_only","source_sha256":"a"*64,"reference_path":"ref.txt","reference_source":source,
      "hypothesis_path":"hyp.txt","pipeline":{"engine":"test","version":"1","language":"en","config_hash":"b"*64}}
def test_pdf_text_reference_is_diagnostic_only(tmp_path):
    (tmp_path/"ref.txt").write_text("same",encoding="utf-8"); (tmp_path/"hyp.txt").write_text("same",encoding="utf-8")
    m=tmp_path/"pages.jsonl"; m.write_text(json.dumps(_record("p1","b1","test","source_pdf_text"))+"\n",encoding="utf-8")
    r=ev.evaluate(m)
    assert r["release_gate_eligible"] is False and r["reference_quality"]=="diagnostic_only"
    assert r["overall"]["micro_cer"]==0
def test_title_split_leakage_rejected(tmp_path):
    (tmp_path/"ref.txt").write_text("same",encoding="utf-8"); (tmp_path/"hyp.txt").write_text("same",encoding="utf-8")
    m=tmp_path/"pages.jsonl"
    m.write_text("\n".join(json.dumps(_record("p","b1",s,"human_adjudicated")) for p,s in [("p1","development"),("p2","test")])+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="title-level split leakage"): ev.evaluate(m)
