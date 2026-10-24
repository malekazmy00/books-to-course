import importlib.util
import sys
from pathlib import Path
import pytest
SCRIPT=Path(__file__).parents[3]/"eval/suites/phase3/machine_judge_50.py"
SPEC=importlib.util.spec_from_file_location("phase3_machine_judge",SCRIPT)
MODULE=importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name]=MODULE
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
def test_semantic_judge_requires_complete_source_cited_supported_answer():
    chunks=[{"chunk_id":"c1","text":"A fair coin has two equally likely outcomes.","book_id":"b","role":"spine","pages":[1]}]
    decision=MODULE.ReviewDecision(status="supported",reference_answer="Two outcomes.",
      required_claims=[MODULE.Claim(claim="Two outcomes.",chunk_id="c1",evidence_quote="two equally likely outcomes")],
      relevant_chunk_ids=["c1"],unanswered_parts=[],confidence=.9)
    MODULE.validate_decision(decision,chunks)
    bad=decision.model_copy(update={"unanswered_parts":["What are the outcomes?"]})
    with pytest.raises(ValueError,match="answer every part"):
        MODULE.validate_decision(bad,chunks)
    hallucinated=decision.model_copy(update={"reference_answer":"Two outcomes, with a 99% success rate."})
    with pytest.raises(ValueError,match="exactly the individually cited claims"):
        MODULE.validate_decision(hallucinated,chunks)
def test_semantic_judge_rejects_unsupported_or_foreign_citations():
    chunks=[{"chunk_id":"c1","text":"A fair coin has two equally likely outcomes.","book_id":"b","role":"spine","pages":[1]}]
    decision=MODULE.ReviewDecision(status="supported",reference_answer="The rate is 99%.",
      required_claims=[MODULE.Claim(claim="99%",chunk_id="c1",evidence_quote="The rate is 99%.")],
      relevant_chunk_ids=["c1"],unanswered_parts=[],confidence=.99)
    with pytest.raises(ValueError,match="exact source"):
        MODULE.validate_decision(decision,chunks)