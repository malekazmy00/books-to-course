"""Extraction flags become translator notes (D-052 point 4, SCHEMA 1.24.0).

A translation unit carries the extraction defects of its pages/blocks (``source_flags``); a note on
such a unit gives the learner the reason ``source``: the original text there was not extracted reliably.
"""
import typing

import b2c_contracts as c


def unit(**kw):
    return c.TranslationUnit(unit_id="bk:u00001", book_id="bk", block_ids=["bk:1:0"], kind="prose",
                             source_text="Text.", pages=[1], section_path=[], **kw)


def test_schema_version_bumped_for_source_flags():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 24, 0)


def test_units_carry_source_flags_and_keep_their_bytes_without_them():
    assert unit(source_flags=["page:pages_below_agreement"]).source_flags == ["page:pages_below_agreement"]
    assert "source_flags" not in unit().model_dump_json()


def test_source_is_a_translator_note_reason():
    assert "source" in typing.get_args(c.TranslatorNoteReason)
