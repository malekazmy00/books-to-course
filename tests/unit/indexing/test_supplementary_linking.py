"""T-IDX-06: lesson -> supplementary links need a relevance score above a threshold, with a
recorded judgement per candidate; "no supplementary evidence" is a valid outcome (audit 03 H1)."""
from b2c_contracts import StructuredChunk, SupplementLinkJudgement
from b2c_indexing.linking import SUPPLEMENT_LINK_POLICY, LinkPolicy, link_supplementary


def chunk(cid, text, book="supp", role="supplementary", evidence="body"):
    return StructuredChunk(chunk_id=cid, book_id=book, role=role, section_path=["S"], block_ids=[cid], pages=[1],
                           text=text, token_count=len(text.split()), embedding_model="unassigned",
                           evidence_class=evidence)


LESSON = [chunk("l1", "Bayes theorem updates the prior probability of a hypothesis with the likelihood of the "
                      "evidence to give a posterior probability. Base rates matter for the posterior.",
                book="spine", role="spine")]
RELEVANT = chunk("s1", "The posterior probability follows from Bayes theorem: multiply the prior by the likelihood "
                       "and normalise. Ignoring base rates inflates the posterior probability of rare hypotheses.")
UNRELATED = chunk("s2", "Gramsci describes hegemony as the consent organised by civil society, the war of position "
                        "fought in schools, churches and newspapers before any war of manoeuvre.")


def test_relevant_chunk_is_linked_and_unrelated_is_judged_but_not_linked():
    linked, judgements = link_supplementary({"m1.l1": LESSON}, [RELEVANT, UNRELATED])
    assert [c.chunk_id for c in linked["m1.l1"]] == ["s1"]
    verdicts = {j.chunk_id: j for j in judgements}
    assert verdicts["s1"].verdict == "linked" and verdicts["s1"].rank == 1
    assert verdicts["s2"].verdict == "below_threshold"
    assert all(isinstance(j, SupplementLinkJudgement) and j.policy_version == SUPPLEMENT_LINK_POLICY.version
               and j.calibrated is False for j in judgements)


def test_no_relevant_supplement_means_no_link():
    linked, judgements = link_supplementary({"m1.l1": LESSON}, [UNRELATED])
    assert linked == {"m1.l1": []}
    assert judgements and all(j.verdict == "below_threshold" for j in judgements)


def test_top_k_bounds_the_recorded_candidates_and_notes_are_not_candidates():
    many = [chunk(f"s{i}", RELEVANT.text + f" Example {i}.") for i in range(10)]
    note = chunk("n1", RELEVANT.text, evidence="note")
    linked, judgements = link_supplementary({"m1.l1": LESSON}, [*many, note],
                                            policy=LinkPolicy(threshold=0.0, top_k=3))
    assert len(linked["m1.l1"]) == 3 and len(judgements) == 3
    assert "n1" not in {j.chunk_id for j in judgements}


def test_policy_version_follows_its_definition():
    assert LinkPolicy(threshold=0.2, top_k=5).version != LinkPolicy(threshold=0.3, top_k=5).version
    assert SUPPLEMENT_LINK_POLICY.version == LinkPolicy(threshold=SUPPLEMENT_LINK_POLICY.threshold,
                                                        top_k=SUPPLEMENT_LINK_POLICY.top_k).version


def test_scores_do_not_grow_with_query_length():
    """The old query (title + 2400 raw characters) matched anything; cosine is length-normalised."""
    long_lesson = [chunk(f"l{i}", LESSON[0].text, book="spine", role="spine") for i in range(20)]
    _, short = link_supplementary({"a": LESSON}, [UNRELATED], policy=LinkPolicy(threshold=0.0, top_k=1))
    _, long = link_supplementary({"a": long_lesson}, [UNRELATED], policy=LinkPolicy(threshold=0.0, top_k=1))
    assert abs(short[0].score - long[0].score) < 1e-9
