"""Named translation-engine and quality-estimator profiles (D-028 part D).

A run names one engine profile and, optionally, one QE profile. Profiles are worker
configuration, never request input: a client chooses a name, not a URL or a key.

Built-in profiles (always local, D-004):

* ``configured-llm`` — ``ChatEngine`` over the worker's configured instruction model
  (``B2C_LLM_BASE_URL`` / ``B2C_LLM_MODEL_ID``).
* ``hy-mt2`` — ``PlainMTEngine`` with the documented Hy-MT2 template over the plain
  OpenAI-compatible route of the hy-translation service (``/plain/v1``; never the legacy
  route that builds its own prompt). ``B2C_HY_PLAIN_BASE_URL`` overrides the URL.

More profiles, or overrides of the built-ins, come from the JSON file named by
``B2C_TRANSLATION_PROFILES_FILE``: ``{"engines": [EngineProfile...], "qe": [QEProfile...]}``.
There is no built-in QE profile: without one every engine result ends ``needs_review``
(D-028 principle 7), which is the intended behaviour, not a fault.

``fingerprint`` hashes everything that changes model output (model, revision, URL,
template, capabilities, QE thresholds) and nothing secret; it is part of the run id, and
the batch activity refuses to continue a run whose profile changed underneath it.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from b2c_translation.engines import EngineCapabilities, TextCompletionGateway, TranslationEngine
from b2c_translation.engines.chat import ChatEngine
from b2c_translation.engines.mt import PlainMTEngine
from b2c_translation.prompts import language_name
from b2c_translation.termbase.vote import TermVoter
from b2c_translation.qa import QualityEstimator
from b2c_translation.qa.estimators import CometKiwiEstimator, LadderJudge, LLMJudge
from b2c_translation.termbase import GatewayTermProposer, Proposal, TermCandidate, TermhoodClassifier
from pydantic import BaseModel, ConfigDict, Field, model_validator

from b2c_contracts import EngineIdentity
from b2c_llm_gateway import InvalidLLMOutput, LLMOutputTruncated, LLMRequestRejected

PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
PROFILES_FILE_ENV = "B2C_TRANSLATION_PROFILES_FILE"
HY_PLAIN_URL_ENV = "B2C_HY_PLAIN_BASE_URL"
HY_MT2_MODEL_ID = "tencent/Hy-MT2-1.8B"
# Model card revision the PlainMTEngine template was verified against (engines/mt.py).
HY_MT2_REVISION = "9a341cd1b679d3efd23b46e847b01745a71ed792"
# Not part of the fingerprint: secrets/transport, and the termbase proposer and termhood
# classifier switches (they only add proposals / suggestions for a reviewer; what translation
# uses is the approved-termbase digest).
_SECRET_OR_TRANSPORT = {"api_key_file", "api_key_files", "timeout_s", "propose_terms", "classify_termhood"}


class UnknownProfile(LookupError):
    """The requested profile is not configured in this process."""


class _Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=PROFILE_ID_PATTERN)

    def fingerprint_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude=_SECRET_OR_TRANSPORT)


class _GatewayFields(BaseModel):
    """Fields a profile needs to reach a model through llm_gateway (CLAUDE.md rule 5)."""
    provider: str = ""
    base_url: str = ""
    model_id: str = ""
    revision: str | None = None
    allow_remote: bool = False      # remote endpoints need explicit opt-in + a key file (D-004/D-022)
    api_key_file: str = ""
    # backup keys of the same provider, used in order when a key's balance is empty (HTTP 402)
    api_key_files: list[str] = []
    timeout_s: float = Field(default=900.0, gt=0, le=3600)
    reasoning_effort: str | None = None


class EngineProfile(_GatewayFields, _Profile):
    kind: Literal["chat", "plain_mt"]
    template_id: str | None = None  # plain_mt only: documented template family (engines/mt.py)
    supports_terms: bool = False
    supports_context: bool = False
    supports_repair: bool = False   # chat only
    max_source_chars: int = Field(default=1200, ge=40, le=20000)
    max_output_tokens: int = Field(default=4096, ge=64, le=32768)
    # Ask this profile's model for termbase proposals of unresolved candidates (chat, local only).
    propose_terms: bool = False
    # Ask this profile's model for a locked / hint / dropped suggestion per termbase candidate
    # before any proposal (D-039; chat, local only). Off unless its measured sensitivity on the
    # accepted candidates reaches 0.97 (eval/reports/term-extraction-v3.md).
    classify_termhood: bool = False

    @model_validator(mode="after")
    def _kind_rules(self) -> "EngineProfile":
        if not (self.provider and self.base_url and self.model_id):
            raise ValueError("engine profile needs provider, base_url and model_id")
        if self.propose_terms and (self.kind != "chat" or self.allow_remote):
            raise ValueError("propose_terms needs a local chat profile: the proposer sends book text "
                             "with instructions to the model")
        if self.classify_termhood and (self.kind != "chat" or self.allow_remote):
            raise ValueError("classify_termhood needs a local chat profile: the classifier sends book text "
                             "with instructions to the model")
        if self.kind == "plain_mt":
            if not self.template_id:
                raise ValueError("a plain_mt profile names its documented template_id")
            if self.supports_repair:
                raise ValueError("plain MT models cannot revise a draft")
        elif self.template_id is not None:
            raise ValueError("template_id applies to plain_mt profiles only")
        return self

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(follows_instructions=self.kind == "chat", supports_terms=self.supports_terms,
                                  supports_context=self.supports_context, supports_repair=self.supports_repair,
                                  max_source_chars=self.max_source_chars)

    def identity(self) -> EngineIdentity:
        return EngineIdentity(provider=self.provider, model_id=self.model_id, revision=self.revision,
                              prompt_version="unset")  # adapters stamp the real template version


class QEProfile(_GatewayFields, _Profile):
    kind: Literal["llm_judge", "cometkiwi", "ladder"]
    # ladder (D-033, D-050): two llm_judge profiles of different model families, and a tie-break
    judges: list[str] = []
    tiebreak: str | None = None
    # llm_judge
    prompt_version: str | None = None
    max_critical: int = Field(default=0, ge=0)
    max_major: int = Field(default=0, ge=0)
    max_minor: int | None = Field(default=None, ge=0)
    max_tokens: int = Field(default=1024, ge=64, le=8192)
    # cometkiwi
    model_path: str | None = None
    threshold: float | None = None

    @model_validator(mode="after")
    def _kind_rules(self) -> "QEProfile":
        if self.kind == "llm_judge" and not (self.provider and self.base_url and self.model_id
                                             and self.prompt_version):
            raise ValueError("an llm_judge profile needs provider, base_url, model_id and prompt_version")
        if self.kind == "cometkiwi" and (not self.model_path or self.threshold is None):
            raise ValueError("a cometkiwi profile needs model_path and a calibrated threshold")
        if self.kind == "ladder":
            if len(self.judges) != 2 or len(set(self.judges)) != 2 or self.tiebreak in self.judges:
                raise ValueError("a ladder names two distinct judges and a tie-break other than them")
            if self.provider or self.base_url or self.model_id or self.api_key_file:
                raise ValueError("a ladder has no gateway of its own: it composes judge profiles")
        elif self.judges or self.tiebreak:
            raise ValueError("judges/tiebreak apply to ladder profiles only")
        return self


class TranslationProfiles(BaseModel):
    model_config = ConfigDict(frozen=True)

    engines: dict[str, EngineProfile]
    qe: dict[str, QEProfile] = {}

    @model_validator(mode="after")
    def _ladders_name_judges(self) -> "TranslationProfiles":
        for profile in self.qe.values():
            if profile.kind != "ladder":
                continue
            for ref in [*profile.judges, *([profile.tiebreak] if profile.tiebreak else [])]:
                judge = self.qe.get(ref)
                if judge is None or judge.kind != "llm_judge":
                    raise ValueError(f"ladder {profile.id!r}: {ref!r} is not an llm_judge QE profile")
        return self

    def _qe_payload(self, profile: QEProfile) -> dict[str, Any]:
        payload = profile.fingerprint_payload()
        if profile.kind == "ladder":  # the ladder is what its judges are
            payload["judges"] = [self.qe[j].fingerprint_payload() for j in profile.judges]
            payload["tiebreak"] = None if not profile.tiebreak else self.qe[profile.tiebreak].fingerprint_payload()
        return payload

    def engine(self, profile_id: str) -> EngineProfile:
        try:
            return self.engines[profile_id]
        except KeyError:
            raise UnknownProfile(f"unknown engine profile {profile_id!r}") from None

    def quality(self, profile_id: str | None) -> QEProfile | None:
        if profile_id is None:
            return None
        try:
            return self.qe[profile_id]
        except KeyError:
            raise UnknownProfile(f"unknown QE profile {profile_id!r}") from None

    def fingerprint(self, engine_profile: str, qe_profile: str | None) -> str:
        quality = self.quality(qe_profile)
        payload = {"engine": self.engine(engine_profile).fingerprint_payload(),
                   "qe": None if quality is None else self._qe_payload(quality)}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def builtin_profiles(settings: Any, env: Mapping[str, str]) -> dict[str, EngineProfile]:
    return {
        "configured-llm": EngineProfile(
            id="configured-llm", kind="chat", provider="configured-llm", base_url=settings.llm_base_url,
            model_id=settings.llm_model_id, allow_remote=bool(settings.llm_allow_remote),
            api_key_file=settings.llm_api_key_file or "", reasoning_effort=settings.llm_reasoning_effort,
            supports_terms=True, supports_context=True, supports_repair=True),
        "hy-mt2": EngineProfile(
            id="hy-mt2", kind="plain_mt", provider="hy-translation",
            base_url=env.get(HY_PLAIN_URL_ENV, "").strip() or "http://hy-translation-proxy:3007/plain/v1",
            model_id=HY_MT2_MODEL_ID, revision=HY_MT2_REVISION, template_id="hy-mt2",
            supports_terms=True, supports_context=True),
    }


def load_translation_profiles(settings: Any, *, env: Mapping[str, str] | None = None) -> TranslationProfiles:
    env = os.environ if env is None else env
    engines: dict[str, EngineProfile] = builtin_profiles(settings, env)
    qe: dict[str, QEProfile] = {}
    path = env.get(PROFILES_FILE_ENV, "").strip()
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - {"engines", "qe"}:
            raise ValueError(f"{PROFILES_FILE_ENV} must hold an object with 'engines' and/or 'qe'")
        for raw in data.get("engines", []):
            profile = EngineProfile.model_validate(raw)
            engines[profile.id] = profile
        for raw in data.get("qe", []):
            profile = QEProfile.model_validate(raw)
            qe[profile.id] = profile
    return TranslationProfiles(engines=engines, qe=qe)


class TolerantTermProposer(GatewayTermProposer):
    """A model answer that a retry cannot change (temperature 0, cached) yields no proposal
    for that candidate instead of failing the whole termbase build."""

    def propose(self, candidate: TermCandidate) -> list[Proposal]:
        try:
            return super().propose(candidate)
        except (InvalidLLMOutput, LLMOutputTruncated, LLMRequestRejected):
            return []


GatewayFactory = Callable[[EngineProfile | QEProfile], TextCompletionGateway]


class ProfileRuntime:
    """Builds (once per process) the engine and estimator objects a profile names."""

    def __init__(self, profiles: TranslationProfiles, *, gateway_factory: GatewayFactory) -> None:
        self.profiles = profiles
        self._gateway_factory = gateway_factory
        self._engines: dict[str, TranslationEngine] = {}
        self._estimators: dict[tuple[str, str, str], QualityEstimator] = {}
        self._proposers: dict[str, GatewayTermProposer | None] = {}
        self._classifiers: dict[str, TermhoodClassifier | None] = {}
        self._voters: dict[str, TermVoter | None] = {}
        self._lock = threading.RLock()  # a ladder builds its judges' estimators under the lock

    def fingerprint(self, engine_profile: str, qe_profile: str | None) -> str:
        return self.profiles.fingerprint(engine_profile, qe_profile)

    def engine(self, profile_id: str) -> TranslationEngine:
        with self._lock:
            if profile_id not in self._engines:
                profile = self.profiles.engine(profile_id)
                gateway = self._gateway_factory(profile)
                if profile.kind == "chat":
                    engine: TranslationEngine = ChatEngine(gateway, profile.identity(), profile.capabilities(),
                                                           max_output_tokens=profile.max_output_tokens)
                else:
                    engine = PlainMTEngine(gateway, profile.identity(), profile.capabilities(),
                                           profile.template_id or "", max_output_tokens=profile.max_output_tokens)
                self._engines[profile_id] = engine
            return self._engines[profile_id]

    def term_proposer(self, profile_id: str) -> GatewayTermProposer | None:
        """The termbase proposer of an engine profile, or None unless the profile enables it."""
        with self._lock:
            if profile_id not in self._proposers:
                profile = self.profiles.engine(profile_id)
                proposer = None
                if profile.propose_terms:
                    proposer = TolerantTermProposer(self._gateway_factory(profile), profile.identity(),
                                                    local_gateway_confirmed=not profile.allow_remote)
                self._proposers[profile_id] = proposer
            return self._proposers[profile_id]

    def termhood_classifier(self, profile_id: str) -> TermhoodClassifier | None:
        """The termhood classifier of an engine profile (D-039), or None unless the profile enables it."""
        with self._lock:
            if profile_id not in self._classifiers:
                profile = self.profiles.engine(profile_id)
                classifier = None
                if profile.classify_termhood:
                    classifier = TermhoodClassifier(self._gateway_factory(profile), profile.identity(),
                                                    book_text_allowed=not profile.allow_remote)
                self._classifiers[profile_id] = classifier
            return self._classifiers[profile_id]

    def term_voter(self, profile_id: str) -> TermVoter | None:
        """The termbase voter of an ``llm_judge`` QE profile (D-033), or None for other QE kinds."""
        with self._lock:
            if profile_id not in self._voters:
                profile = self.profiles.quality(profile_id)
                assert profile is not None
                if profile.kind == "ladder":  # terms are voted by the ladder's first judge
                    self._voters[profile_id] = self.term_voter(profile.judges[0])
                else:
                    self._voters[profile_id] = (TermVoter(self._gateway_factory(profile))
                                                if profile.kind == "llm_judge" else None)
            return self._voters[profile_id]

    def estimator(self, profile_id: str | None, *, source_language: str = "en",
                  target_language: str = "ar") -> QualityEstimator | None:
        if profile_id is None:
            return None
        key = (profile_id, source_language, target_language)
        with self._lock:
            if key not in self._estimators:
                profile = self.profiles.quality(profile_id)
                assert profile is not None
                if profile.kind == "ladder":
                    estimator: QualityEstimator = LadderJudge(
                        [self.estimator(j, source_language=source_language, target_language=target_language)
                         for j in profile.judges],
                        tiebreak=None if not profile.tiebreak else self.estimator(
                            profile.tiebreak, source_language=source_language, target_language=target_language))
                elif profile.kind == "llm_judge":
                    estimator = LLMJudge(
                        self._gateway_factory(profile), prompt_version=profile.prompt_version or "",
                        max_critical=profile.max_critical, max_major=profile.max_major,
                        max_minor=profile.max_minor, max_tokens=profile.max_tokens,
                        source_language=language_name(source_language),
                        target_language=language_name(target_language))
                else:
                    estimator = CometKiwiEstimator(profile.model_path, threshold=float(profile.threshold or 0.0))
                self._estimators[key] = estimator
            return self._estimators[key]


__all__ = ["EngineProfile", "GatewayFactory", "HY_MT2_MODEL_ID", "HY_MT2_REVISION", "PROFILES_FILE_ENV",
           "PROFILE_ID_PATTERN", "ProfileRuntime", "QEProfile", "TolerantTermProposer", "TranslationProfiles", "UnknownProfile",
           "builtin_profiles", "load_translation_profiles"]
