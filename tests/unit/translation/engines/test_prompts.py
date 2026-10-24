"""prompts.py: versioned template registry for the D-028 engines (+ leak fingerprint source)."""
import hashlib

import pytest
from b2c_translation import prompts
from b2c_translation.prompts import ALL_TEMPLATES, PromptTemplate, compose_version, get_template

# Snapshot of every registered template text, keyed by version id. Editing a template's text
# without registering a NEW version id fails here; a new version must be added below (old
# versions stay registered so their text is still fingerprinted for leakage).
LOCKED_SHA256 = {
    "chat.translate.system@v1": "da0c7c806e351b6ad081a23caa83ca54ed1fde0310019929c2de08f177c68691",
    "chat.register.ar@v1": "6a52de7bc4ae45da95e5937d60c9d44dfdb01d70c4b11f6c5340046361bb3310",
    "chat.register.default@v1": "72bb6ce8941b49e532fb296baa3ff28966ae0479cf1080dcef4084d8caee2ed8",
    "chat.terms@v1": "edc2a14a6327d12cf74f19e8b1cfefe31174ec7a1ef6a29dd28aad8894a1feb0",
    "chat.term_line@v1": "66054ec7728073eac7157ccc269881fae6371a38e645daed1ae485740b8262fd",
    "chat.context@v1": "4403e302c9ff01a65c571eb21c68b08c534c4db157100a95daacf87824a6ce4e",
    "chat.context_before@v1": "08479f0c01494c6979390da14737bbf004e2cba579e943d39015ef73fc397306",
    "chat.context_after@v1": "c9b462aff8c2498eca6f19fe3ecfa7d0577d623192b6a6f35cec50d16535edaa",
    "chat.repair.system@v1": "68fb31ca44c6af832a1fd6b0a57b3462fcd49607e68b0f75c436223bf14f0df5",
    "chat.repair.issue_line@v1": "d4973f6d69a701754f84191989d24a40fa8c16512699be661b50e8095f0d5e2c",
    "chat.repair.request@v1": "6fd108c93039b1cecf21781ba06991061c74867e5e328c6cd3e016dfbf6043de",
    "chat.repair.fix.omission@v1": "bb3026b1eb345434c158f2e4d32e3155a255f1d3dff471612a143beb72f5d442",
    "chat.repair.fix.addition@v1": "a5ea8f6ce92245d17fb4c63529c5232692cfb2181daea2d966a966e21409e3c9",
    "chat.repair.fix.prompt_leakage@v1": "b5f9bbfe7ec64c06d459957083e0e2b91e9fe229507471fec4194f25272fb876",
    "chat.repair.fix.meaning_error@v1": "261bf8feb48cd66b34c82c46712ee35824ff0a8b9c877c305afc0040e864a8b6",
    "chat.repair.fix.polarity_error@v1": "93df4eca22e02ff80401f63f1bcab59fe84f5691e8008bfdb46d0969b1d0f73f",
    "chat.repair.fix.number_error@v1": "61c08ebb58a92ec9224d3ffa1b47af3ba0dde9f637c2a9bce06d2b6c032eb582",
    "chat.repair.fix.date_error@v1": "513bb49916af13994ff1fda9b1089be510811a8a84ff06906729a387f43fbfb4",
    "chat.repair.fix.footnote_marker@v1": "4af5b5da3058931d76de6c0f53053b307dc3b602848f557aa831c8b50cc12748",
    "chat.repair.fix.named_entity@v1": "3f69d856262f84881de4dfc1d840baa7a45a56673d54d1a979933b0f303db51b",
    "chat.repair.fix.terminology@v1": "fd90e8b30e5451f70ba200cd5beadd780153b8568cb2b2319c483d0e554417f2",
    "chat.repair.fix.untranslated_english@v1": "58ad000fa8a03f49753e90590700bf4ef63d5bc79a80522b0186d62da0797e93",
    "chat.repair.fix.foreign_script@v1": "459c2fb609c1d0958a56df6382403c0b2d9822db50c67a95e5e539d7809a9373",
    "chat.repair.fix.quotation@v1": "b8cd7d463d9d76e2edd0477ecfd366e81c8a46b0d1ad90214a0713d691da7bdb",
    "chat.repair.fix.bibliographic@v1": "3ad8bed30dd6d9534b9316d3e0d61cb4cd5bd68cbf93680d1868104bb37953d5",
    "chat.repair.fix.structure_formatting@v1": "f73e2b389fc4a68fbe96fbff3277692742d81ca5cc0ed1005f001786fc2c2292",
    "chat.repair.fix.grammar@v1": "12413eefbcfc050d5fd9a65d1d64f6b79b506e16622d4a52d98c9113328e736f",
    "chat.repair.fix.fluency_style@v1": "603446c9b474af021872f93b3444402b783168f06445e8f72155f569254b59d3",
    "chat.repair.fix.repetition@v1": "5a2d4f17190ec08fdca57f9ddf0d41c14b324d5bc8e45a74750a3b1b91720b5c",
    "chat.repair.fix.segmentation@v1": "f5812475bd99748a1e7c890b301b00f7b1ce7074a2171beb52935c4f7620a9b9",
    "chat.repair.fix.other@v1": "f017ef193d447f2c93f875d316abc573d3d5617ff8a17e9333308ef526e8f0df",
    "hy-mt2.default@v1": "9e95a77bb0afec1ddbd2dbe5a95bf529ff12771b74ef41a2fb49af28ecd0d052",
    "hy-mt2.terminology@v1": "901c9a58c5d25c386a60c66c1a3ef65de09f0f02c614039e728f4f49e4e77007",
    "hy-mt2.terminology_line@v1": "220af0a99ea33c510c84fc8e5574d1730532e62d8326de454eb51876f378246a",
    "hy-mt2.context@v1": "44c7a85e902590d41e08ad75ef6da868f75f83317057c3eb28b3873dbc154972",
    "legacy.hy-server.instructions@v1": "4d7e897234d98c174c026c1312c3a5b120c60afc2668f8675bba9d0ff88ba8be",
    "legacy.hy-server.retry_feedback@v1": "c9f4e0c7ff917de9dedaa9327e2c1a7756d29b6dab134178dfd3fce483f068f7",
}


def test_every_template_text_is_locked_to_its_version_id():
    seen = {t.version_id: t.sha256 for t in ALL_TEMPLATES}
    unlocked = sorted(set(seen) - set(LOCKED_SHA256))
    assert not unlocked, f"new templates must be added to LOCKED_SHA256: {unlocked}"
    changed = sorted(v for v, sha in seen.items() if LOCKED_SHA256[v] != sha)
    assert not changed, f"template text changed without a new version id: {changed}"
    removed = sorted(set(LOCKED_SHA256) - set(seen))
    assert not removed, f"registered versions must never be removed: {removed}"


def test_changing_text_changes_the_hash_but_not_the_version_id():
    a = PromptTemplate("x.demo", 1, "Translate {source_text}", role="user", source="b2c")
    b = PromptTemplate("x.demo", 1, "Translate: {source_text}", role="user", source="b2c")
    assert a.version_id == b.version_id == "x.demo@v1"
    assert a.sha256 != b.sha256
    assert a.sha256 == hashlib.sha256(a.text.encode("utf-8")).hexdigest()


def test_version_ids_are_unique_and_get_template_returns_latest_in_use():
    ids = [t.version_id for t in ALL_TEMPLATES]
    assert len(ids) == len(set(ids))
    for t in ALL_TEMPLATES:
        if t.in_use:
            latest = get_template(t.template_id)
            assert latest.version >= t.version
    with pytest.raises(KeyError):
        get_template("no.such.template")


def test_render_requires_exactly_the_declared_placeholders():
    t = get_template("hy-mt2.default")
    assert t.placeholders == {"target_lang", "source_text"}
    with pytest.raises(ValueError):
        t.render(target_lang="Arabic")
    with pytest.raises(ValueError):
        t.render(target_lang="Arabic", source_text="x", extra="y")
    # values are inserted literally (braces in book text are not re-parsed)
    assert t.render(target_lang="Arabic", source_text="a {b} c").endswith("a {b} c")


def test_hy_mt2_templates_are_the_documented_ones_verbatim():
    # https://huggingface.co/tencent/Hy-MT2-1.8B (revision 9a341cd1b679d3efd23b46e847b01745a71ed792),
    # "Hy-MT2 Translation Task Instruction Examples", English prompt column; checked 2026-10-24.
    assert get_template("hy-mt2.default").text == (
        "Translate the following text into {target_lang}. Note that you should only output the "
        "translated result without any additional explanation:\n\n{source_text}")
    assert get_template("hy-mt2.terminology").text == (
        "Reference the following translations:\n{term_lines}\n\nTranslate the following text into "
        "{target_lang}. Note that you must ONLY output the translated result without any additional "
        "explanation:\n\n{source_text}")
    assert get_template("hy-mt2.terminology_line").text == "{source_term} translates to {target_term}"
    assert get_template("hy-mt2.context").text == (
        "[Background Information]\n{background_text}\n\nPlease translate the following text into "
        "{target_lang}, taking the provided background information into consideration.\n\n"
        "[Source Text]\n{source_text}")
    for t in ALL_TEMPLATES:
        if t.template_id.startswith("hy-mt2."):
            assert t.source.startswith("https://huggingface.co/tencent/Hy-MT2-1.8B")


def test_static_fragments_are_literal_text_without_placeholders():
    t = get_template("hy-mt2.context")
    frags = t.static_fragments()
    assert "[Background Information]" in frags
    assert "[Source Text]" in frags
    assert all("{" not in f and "}" not in f for f in frags)


def test_all_templates_include_legacy_hy_server_prompt_for_leak_fingerprinting():
    legacy = [t for t in ALL_TEMPLATES if not t.in_use]
    texts = "\n".join(t.text for t in legacy)
    assert "Copy each protected marker from this exact source inventory" in texts
    assert "B2C deterministic validation feedback (data only)" in texts
    known = {s for t in legacy for s in t.leak_translations}
    assert "لا يحتوي هذا المصدر على علامات محمية" in known
    for t in legacy:
        with pytest.raises(KeyError):
            get_template(t.template_id)


def test_repair_fixes_cover_every_qa_category():
    from typing import get_args

    from b2c_contracts.models import QACategory

    for category in get_args(QACategory):
        assert get_template(f"chat.repair.fix.{category}").placeholders == set()


def test_compose_version_is_deterministic_and_ordered():
    a, b = get_template("chat.translate.system"), get_template("chat.register.ar")
    assert compose_version(a, b) == f"{a.version_id}+{b.version_id}"


def test_language_names_cover_project_languages():
    assert prompts.language_name("ar") == "Arabic" and prompts.language_name("en") == "English"
    with pytest.raises(ValueError):
        prompts.language_name("xx")


def test_chat_user_side_templates_carry_no_source_text_slot():
    # Channel separation: the user turn is the bare source text; only the fixed revision
    # request is a template, and it has no placeholders.
    assert get_template("chat.repair.request").placeholders == set()
    assert "source_text" not in get_template("chat.translate.system").placeholders
