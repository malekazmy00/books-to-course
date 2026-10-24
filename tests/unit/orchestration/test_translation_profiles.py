"""Named engine / QE profiles for the rebuilt translation path (D-028 part D)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from b2c_translation.engines.chat import ChatEngine
from b2c_translation.engines.mt import PlainMTEngine
from b2c_translation.qa.estimators import LLMJudge

from b2c_orchestration.translation_profiles import (EngineProfile, ProfileRuntime, QEProfile,
                                                    TranslationProfiles, UnknownProfile,
                                                    load_translation_profiles)

SETTINGS = SimpleNamespace(llm_base_url="http://llm:8080/v1", llm_model_id="local-gemma",
                           llm_allow_remote=False, llm_api_key_file="", llm_reasoning_effort=None)


def test_builtins_are_local_and_typed():
    profiles = load_translation_profiles(SETTINGS, env={})
    chat = profiles.engine("configured-llm")
    assert chat.kind == "chat" and chat.model_id == "local-gemma" and not chat.allow_remote
    hy = profiles.engine("hy-mt2")
    assert hy.kind == "plain_mt" and hy.template_id == "hy-mt2" and hy.base_url.endswith("/plain/v1")
    assert not hy.capabilities().follows_instructions and not hy.capabilities().supports_repair
    assert profiles.qe == {}  # no semantic QE unless configured: engine output ends needs_review


def test_file_adds_and_overrides_profiles(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({
        "engines": [{"id": "hy-mt2", "kind": "plain_mt", "provider": "hy", "base_url": "http://hy:3007/plain/v1",
                     "model_id": "tencent/Hy-MT2-1.8B", "revision": "abc", "template_id": "hy-mt2"}],
        "qe": [{"id": "judge", "kind": "llm_judge", "provider": "local", "base_url": "http://judge:8080/v1",
                "model_id": "judge-model", "prompt_version": "mqm@1"},
               {"id": "kiwi", "kind": "cometkiwi", "model_path": "/nope/model.ckpt", "threshold": 0.8}],
    }))
    profiles = load_translation_profiles(SETTINGS, env={"B2C_TRANSLATION_PROFILES_FILE": str(path)})
    assert profiles.engine("hy-mt2").revision == "abc"
    assert profiles.quality("judge").model_id == "judge-model"
    assert profiles.quality(None) is None
    with pytest.raises(UnknownProfile):
        profiles.engine("missing")
    with pytest.raises(UnknownProfile):
        profiles.quality("missing")


@pytest.mark.parametrize("bad", [
    {"id": "x", "kind": "plain_mt", "provider": "p", "base_url": "http://h/v1", "model_id": "m"},  # no template
    {"id": "x", "kind": "plain_mt", "provider": "p", "base_url": "http://h/v1", "model_id": "m",
     "template_id": "hy-mt2", "supports_repair": True},
    {"id": "x", "kind": "chat", "provider": "p", "base_url": "http://h/v1", "model_id": "m", "template_id": "hy-mt2"},
    {"id": "Bad Id", "kind": "chat", "provider": "p", "base_url": "http://h/v1", "model_id": "m"},
    {"id": "x", "kind": "chat", "provider": "p", "base_url": "http://h/v1", "model_id": "m", "surprise": 1},
])
def test_invalid_engine_profiles_are_refused(bad):
    with pytest.raises(ValueError):
        EngineProfile.model_validate(bad)


def test_invalid_qe_profiles_are_refused():
    with pytest.raises(ValueError):
        QEProfile.model_validate({"id": "j", "kind": "llm_judge", "provider": "p", "base_url": "http://h/v1"})
    with pytest.raises(ValueError):
        QEProfile.model_validate({"id": "k", "kind": "cometkiwi", "threshold": 0.5})


def test_fingerprint_tracks_model_identity_not_secrets(tmp_path):
    base = load_translation_profiles(SETTINGS, env={})
    fp = base.fingerprint("hy-mt2", None)
    assert fp == base.fingerprint("hy-mt2", None) and len(fp) == 64
    assert fp != base.fingerprint("configured-llm", None)
    changed = TranslationProfiles(engines={**base.engines, "hy-mt2": base.engine("hy-mt2").model_copy(
        update={"revision": "other"})}, qe=base.qe)
    assert changed.fingerprint("hy-mt2", None) != fp
    secret = TranslationProfiles(engines={**base.engines, "hy-mt2": base.engine("hy-mt2").model_copy(
        update={"api_key_file": "/run/secrets/x", "timeout_s": 5.0})}, qe=base.qe)
    assert secret.fingerprint("hy-mt2", None) == fp


class FakeGateway:
    def __init__(self, profile):
        self.profile = profile


def test_runtime_builds_engines_and_estimators_once():
    profiles = TranslationProfiles(
        engines=load_translation_profiles(SETTINGS, env={}).engines,
        qe={"judge": QEProfile(id="judge", kind="llm_judge", provider="local", base_url="http://judge:8080/v1",
                               model_id="judge-model", prompt_version="mqm@1")})
    made = []

    def factory(profile):
        made.append(profile.id)
        return FakeGateway(profile)

    runtime = ProfileRuntime(profiles, gateway_factory=factory)
    hy = runtime.engine("hy-mt2")
    assert isinstance(hy, PlainMTEngine) and runtime.engine("hy-mt2") is hy
    assert hy.identity.model_id == "tencent/Hy-MT2-1.8B" and hy.identity.provider == "hy-translation"
    assert isinstance(runtime.engine("configured-llm"), ChatEngine)
    judge = runtime.estimator("judge")
    assert isinstance(judge, LLMJudge) and runtime.estimator("judge") is judge
    assert runtime.estimator(None) is None
    assert made == ["hy-mt2", "configured-llm", "judge"]


def test_runtime_cometkiwi_not_configured_is_reported():
    from b2c_translation.qa.estimators import NotConfigured
    profiles = TranslationProfiles(engines={}, qe={"kiwi": QEProfile(id="kiwi", kind="cometkiwi",
                                                                    model_path="/nope.ckpt", threshold=0.8)})
    with pytest.raises(NotConfigured):
        ProfileRuntime(profiles, gateway_factory=FakeGateway).estimator("kiwi")
