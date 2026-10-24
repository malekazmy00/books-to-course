"""Deterministic reference rendering (D-028 principle 2): citations stay in their original
language; only connective tokens are rendered in their conventional Arabic forms."""
from __future__ import annotations

import pytest

from b2c_contracts import TranslationUnit
from b2c_translation.references import CONNECTIVES, render_reference, render_reference_unit


@pytest.mark.parametrize("source,expected", [
    ("Ibid.", "المرجع نفسه."),
    ("Ibid., 55.", "المرجع نفسه، 55."),
    ("ibid., 33", "المرجع نفسه، 33"),
    ("Ibid., p. 12.", "المرجع نفسه، ص 12."),
    ("Doe, op. cit., pp. 24-53.", "Doe، مرجع سابق، ص 24-53."),
    ("Jane Doe, A Book (London: Example Press, n.d.), 4.", "Jane Doe, A Book (London: Example Press، د. ت.), 4."),
    ("Roe, Things, vol. 2, no. 3 (1990): 41-55.", "Roe, Things، مج 2، العدد 3 (1990): 41-55."),
    ("A Book, ed. Frank Example, trans. Ray Sample (New York: Press, 1994), 2.",
     "A Book، تحرير Frank Example، ترجمة Ray Sample (New York: Press, 1994), 2."),
    ("A Book, 2nd ed. (Washington, DC: Press, 2007), 84.", "A Book، ط 2 (Washington, DC: Press, 2007), 84."),
    ("Doe et al., Book, 7.", "Doe وآخرون، Book, 7."),
    ("Mouffe, ed., Gramsci and Theory (London: Routledge, 1979), 1-18.",
     "Mouffe (محرر)، Gramsci and Theory (London: Routledge, 1979), 1-18."),
    ("140. Ibid., 119-124.", "140. المرجع نفسه، 119-124."),
    ("Gramsci, cited in ibid., 33.", "Gramsci، مذكور في المرجع نفسه، 33."),
    ("See also Doe, Book, 3.", "يُنظر أيضًا Doe, Book, 3."),
    ("Cf. Doe, Book, 3.", "قارن Doe, Book, 3."),
])
def test_connectives_rendered_and_everything_else_kept(source, expected):
    assert render_reference(source) == expected


@pytest.mark.parametrize("source", [
    "https://doi.org/10.2307/3010795",
    "Abrahamian, Ervand. 'Ideologue of the Revolution'. MERIP Reports 102 (1982): 24-28.",
    "Ibidem Press Catalogue, Vol de Nuit (Paris, 1931).",  # 'Ibidem'/'Vol' inside names are not connectives
])
def test_titles_names_urls_and_numbers_untouched(source):
    assert render_reference(source) == source


def test_numbers_preserved_exactly():
    import re
    src = "Ibid., 119-124; Doe, op. cit., pp. 3-5, vol. 12, no. 4 (2001)."
    out = render_reference(src)
    assert re.findall(r"\d+", out) == re.findall(r"\d+", src)


def test_idempotent():
    src = "Ibid., p. 12; Doe et al., op. cit., n.d."
    once = render_reference(src)
    assert render_reference(once) == once


def test_unit_rendering_requires_reference_kind():
    u = TranslationUnit(unit_id="b:u00001", book_id="b", block_ids=["b:1:0"], kind="reference",
                        source_text="Ibid., 55.", pages=[1], section_path=[])
    assert render_reference_unit(u) == "المرجع نفسه، 55."
    prose = u.model_copy(update={"kind": "prose"})
    with pytest.raises(ValueError):
        render_reference_unit(prose)


def test_connective_table_documents_every_rule_with_source():
    assert len(CONNECTIVES) >= 10
    for rule in CONNECTIVES:
        assert rule.arabic and rule.source
