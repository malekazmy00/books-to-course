"""The field and the book reach the engine (D-034, learning-companion §11a: «سياق المجال يغذّي محرك الترجمة»).

Knowledge research, done in the book's language BEFORE translation, gives a short brief of the book and
its field (verified facts only). The chat engine receives it in the system message as background, never
as text to translate; the user turn stays the bare source. Without a brief the prompt and its version are
unchanged (earlier runs and their cache stay valid). Plain MT models get no brief.
"""
from datetime import datetime, timezone

import b2c_contracts as c
from b2c_contracts import EngineIdentity
from b2c_translation.book_pipeline import UnitTranslator
from b2c_translation.domain import domain_brief
from b2c_translation.engines.base import EngineCapabilities, EngineRequest
from b2c_translation.engines.chat import ChatEngine
from b2c_translation.prompts import get_template

IDENTITY = EngineIdentity(provider="llama.cpp", model_id="qwen-test", revision="sha-abc", prompt_version="unset")
FULL = EngineCapabilities(follows_instructions=True, supports_terms=True, supports_context=True,
                          supports_repair=True, max_source_chars=2000)
SOURCE = "Hegemony rests on consent."
BRIEF = "Book: From Subordination to Revolution, by John Chalcraft. The book applies Gramsci's theory to popular mobilization."
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def request(unit, **kw):
    return EngineRequest(unit=unit, source_language="en", target_language="ar", **kw)


def test_the_brief_is_background_in_the_system_message(gateway, make_unit):
    result = ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE), domain=BRIEF))
    call = gateway.calls[0]
    assert get_template("chat.domain").render(domain_text=BRIEF) in call["messages"][0]["content"]
    assert call["messages"][1]["content"] == SOURCE
    assert "chat.domain@v1" in call["prompt_version"] == result.identity.prompt_version
    assert "never translate" in get_template("chat.domain").text


def test_without_a_brief_the_prompt_is_unchanged(gateway, make_unit):
    engine = ChatEngine(gateway, IDENTITY, FULL)
    engine.translate(request(make_unit(SOURCE)))
    engine.translate(request(make_unit(SOURCE), domain=""))
    first, second = gateway.calls
    assert first["messages"] == second["messages"] and "chat.domain" not in first["prompt_version"]


def test_the_translator_passes_its_brief_to_every_request():
    seen = []

    class Engine:
        identity = IDENTITY
        capabilities = FULL

        def translate(self, req):
            seen.append(req.domain)
            raise RuntimeError("stop")

    t = UnitTranslator(Engine(), domain=BRIEF)
    unit = c.TranslationUnit(unit_id="bk:u00001", book_id="bk", block_ids=["bk:1:0"], kind="prose",
                             source_text=SOURCE, pages=[1], section_path=[])
    try:
        t.translate(unit)
    except RuntimeError:
        pass
    assert seen == [BRIEF]


def dossier(claims):
    src = c.ResearchSource(source_id="src_" + "1" * 16, url="https://openalex.org/W1", provider="openalex",
                           title="OpenAlex", tier=1, fetched_at=NOW, content_sha256="a" * 64, text_chars=10)
    ev = c.Evidence(source_id=src.source_id, url=src.url, quote="q", tier=1, fetched_at=NOW, quote_found=True,
                    supports=True, judged_by="m")
    made = [c.DossierClaim(claim_id=f"clm_{i:012x}", section=s, statement=t, kind=k, evidence=[ev],
                           status="verified" if k == "fact" else "attributed",
                           attributed_to=None if k == "fact" else "A Critic")
            for i, (s, t, k) in enumerate(claims)]
    ident = c.ProducerIdentity(role="x", model_id="m")
    return c.KnowledgeDossier(book_id="bk", title="From Subordination to Revolution", authors=["John Chalcraft"],
                              created_at=NOW, sources=[src], claims=made, extractor=ident, judge=ident,
                              extractor_family="deepseek", judge_family="inception")


def test_the_brief_is_verified_facts_about_the_book_and_its_field_only():
    d = dossier([("book_card", "The book applies Gramsci's theory to popular mobilization.", "fact"),
                 ("context", "It studies the Middle East and North Africa since 1900.", "fact"),
                 ("reception", "The book is brilliant.", "viewpoint"),
                 ("author", "Chalcraft teaches at the LSE.", "fact")])
    brief = domain_brief(d)
    assert brief.startswith("Book: From Subordination to Revolution, by John Chalcraft.")
    assert "popular mobilization" in brief and "Middle East" in brief
    assert "brilliant" not in brief and "LSE" not in brief
    assert len(domain_brief(d, max_chars=80)) <= 80
    assert domain_brief(None) == ""
