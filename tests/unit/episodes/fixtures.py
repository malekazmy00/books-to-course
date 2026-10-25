"""Small book, dossier and scripted models for the episode-script tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import b2c_contracts as c
from b2c_episodes import BookTextRef, CourseIntroPlan, EpisodePlan, LessonSource, ModelRole, SourceBlock, TermInfo
from tests.unit.research.fakes import ScriptedLLM

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
B = "bk"


def blk(page, seq, text, type_="paragraph"):
    return SourceBlock(f"{B}:{page}:{seq}", type_, text, (page,))


PREFACE = LessonSource("m01.l01", 1, "Preface", (
    blk(1, 0, "Preface", "heading"),
    blk(1, 1, "We wrote this book to help analysts think about hegemony in a structured way. "
              "Our evidence comes from decades of teaching analysts and from case studies."),
    blk(1, 2, "We thank our colleagues at the institute for their careful reading of the drafts.")))
CH1 = LessonSource("m02.l01", 2, "Consent and force", (
    blk(2, 0, "Consent and force", "heading"),
    blk(2, 1, "Rulers rarely rely on force alone. Consent of the governed lowers the cost of rule."),))
LESSON = LessonSource("m03.l01", 3, "Hegemony", (
    blk(3, 0, "Hegemony", "heading"),
    blk(3, 1, "Hegemony is the combination of force and consent that a ruling group exercises. "
              "Hegemony rests on consent more than on force."),
    blk(3, 2, "The war of position is a long struggle inside civil society. It precedes any open conflict."),
    blk(3, 3, "Civil society is the space of schools, churches and newspapers where consent is built.")))

CLAIM_SRC = c.ResearchSource(source_id="src_" + "1" * 16, url="https://en.wikipedia.org/wiki/Hegemony",
                             provider="wikipedia", title="Hegemony", tier=1, fetched_at=NOW,
                             content_sha256="a" * 64, text_chars=100)
REVIEW_SRC = c.ResearchSource(source_id="src_" + "2" * 16, url="https://journal.example.org/review", provider="web:x",
                              title="Review", tier=2, fetched_at=NOW, content_sha256="b" * 64, text_chars=100)


def ev(src, quote="q"):
    return c.Evidence(source_id=src.source_id, url=src.url, quote=quote, tier=src.tier, fetched_at=NOW,
                      quote_found=True, supports=True, judged_by="google/y")


def claim(n, section, statement, src=CLAIM_SRC, **kw):
    return c.DossierClaim(claim_id=f"clm_{n:012x}", section=section, statement=statement, kind=kw.pop("kind", "fact"),
                          evidence=[ev(src)], status=kw.pop("status", "verified"), **kw)


def dossier():
    claims = [claim(1, "book_card", "The book is a guide to structured analysis of power."),
              claim(2, "author", "The author taught analysts for thirty years."),
              claim(3, "concept_in_field", "Hegemony was used by Marxists before Gramsci.", concept="hegemony"),
              claim(4, "reception", "The book is hugely important for analysts.", src=REVIEW_SRC, kind="viewpoint",
                    status="attributed", attributed_to="Dario Galasso (review)"),
              claim(5, "author", "A rejected claim.", status="rejected", status_reason="x")]
    ident = lambda m: c.ProducerIdentity(role="x", model_id=m)
    return c.KnowledgeDossier(book_id=B, title="Power", authors=["A. Author"], created_at=NOW,
                              sources=[CLAIM_SRC, REVIEW_SRC], claims=claims, extractor=ident("deepseek/x"),
                              judge=ident("google/y"), extractor_family="deepseek", judge_family="google")


HEG = TermInfo("cpt_000000000001", "hegemony", approved=True,
               definition=c.BookSpan(block_id=f"{B}:3:1", pages=[3], quote="Hegemony is the combination of force "
                                                                           "and consent that a ruling group exercises."),
               gloss="Rule through force plus consent.", external=(("clm_000000000003", "x", ("https://x",)),))
WOP = TermInfo("cpt_000000000002", "war of position", approved=True)
CIV = TermInfo("cpt_000000000003", "civil society", approved=True)


def plan(previous=(PREFACE, CH1)):
    return EpisodePlan(book_id=B, book_title="Power", lesson=LESSON, previous=list(previous), terms=[HEG, WOP, CIV],
                       book_text=BookTextRef(block_ids=tuple(b.block_id for b in LESSON.blocks),
                                             unit_ids=("bk:u00010", "bk:u00011", "bk:u00012", "bk:u00013")),
                       translation_run_id="r" * 64, last_episode=9, dossier=dossier())


def ev_b(label, quote):
    return [{"block": label, "quote": quote}]


def lesson_reply():
    q = lambda i, quote="Hegemony rests on consent more than on force.": {
        "question": f"Question {i}: what does hegemony rest on?", "answer": "Mostly on consent.",
        "evidence": ev_b("B2", quote)}
    return {
        "covers": [{"text": "This episode explains hegemony and the war of position.",
                    "evidence": ev_b("B2", "Hegemony is the combination of force and consent")}],
        "summary": [
            {"text": "Hegemony combines force and consent.", "evidence": ev_b("B2", "the combination of force and consent")},
            {"text": "The war of position is a long struggle in civil society.",
             "evidence": ev_b("B3", "The war of position is a long struggle inside civil society.")},
            {"text": "Hegemony was invented in 1920.", "evidence": ev_b("B2", "Hegemony rests on consent")},  # judge: no
            {"text": "Made-up quote.", "evidence": ev_b("B2", "this sentence is not in the lesson")},       # grounding
        ],
        "definitions": [{"term": "war of position", "text": "A long struggle inside civil society.",
                         "evidence": ev_b("B3", "The war of position is a long struggle inside civil society.")},
                        {"term": "civil society", "text": "Schools, churches and newspapers where consent is built.",
                         "evidence": ev_b("B4", "the space of schools, churches and newspapers")}],
        "questions": [q(i) for i in range(6)] + [q(6, "invented quote that is not there")],
    }


def recap_reply():
    return {"recap": [
        {"text": "Rulers rarely rely on force alone.", "evidence": ev_b("P5", "Rulers rarely rely on force alone.")},
        {"text": "The authors wrote to help analysts.", "evidence": ev_b("P2", "We wrote this book to help analysts")},
        {"text": "Hegemony rests on consent.", "evidence": ev_b("P9", "Hegemony rests on consent")},  # unknown block
    ]}


def intro_reply():
    return {"about_book": [{"text": "The book is a guide to structured analysis of power.", "claims": ["clm_000000000001"]},
                           {"text": "It sold a million copies.", "claims": []}],            # no claim: refused
            "about_author": [{"text": "The author taught analysts for thirty years.", "claims": ["clm_000000000002"]}],
            "method": [{"text": "The book draws on decades of teaching and on case studies.",
                        "evidence": ev_b("B2", "Our evidence comes from decades of teaching analysts")}],
            "acknowledgments": [{"text": "The authors thank colleagues for reading the drafts.",
                                 "evidence": ev_b("B3", "We thank our colleagues at the institute")}]}


def generator_script(pv, msg):
    return {"ep-lesson-v2": lesson_reply, "ep-recap-v1": recap_reply, "ep-intro-v1": intro_reply}[pv]()


def judge_script(pv, msg):
    data = json.loads(msg[msg.index("{"):])
    verdicts = []
    for item in data["items"]:
        text = item.get("text") or item.get("question") or ""
        verdicts.append({"id": item["id"], "supported": "1920" not in text, "reason": "x"})
    return {"verdicts": verdicts}


def roles():
    ident = lambda m: c.ProducerIdentity(role="x", provider="openrouter", model_id=m)
    return (ModelRole(ScriptedLLM(generator_script, "deepseek/x"), ident("deepseek/x"), "deepseek"),
            ModelRole(ScriptedLLM(judge_script, "google/y"), ident("google/y"), "google"))


def intro_plan():
    return CourseIntroPlan(book_id=B, book_title="Power", authors=["A. Author"], dossier=dossier(),
                           front_lessons=[PREFACE], episode_titles=[(1, "Preface"), (2, "Consent and force"),
                                                                    (3, "Hegemony")])
