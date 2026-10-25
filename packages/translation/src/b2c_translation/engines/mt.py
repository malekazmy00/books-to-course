"""PlainMTEngine: translation-only models (D-028), e.g. Tencent Hy-MT2 / Hunyuan-MT.

Such a model translates whatever it receives, so it gets exactly one user message built
from its model card's documented template and nothing else: no system prompt (Hy-MT2 has
no default system prompt), no free-form instructions, no marker inventory, no JSON, no
retry feedback. Terms and context are sent only through a documented template and only
when the engine's capabilities allow it; otherwise terms are enforced by QA alone.

Template sources (verified 2026-10-24):
- https://huggingface.co/tencent/Hy-MT2-1.8B, model card at revision
  9a341cd1b679d3efd23b46e847b01745a71ed792 (identical to the copy on the data disk),
  section "Hy-MT2 Translation Task Instruction Examples", English prompt column:
  "Default Translation", "Terminology" and the background-information template the table
  labels "Structured Data 2" (contextual translation). Also chat_template.jinja there.
- https://github.com/Tencent-Hunyuan/Hy-MT2 README (main @ ff1903ecaa724e10951a23c16817a2413c752b35),
  same table.
The card documents no template combining terminology with context, so terms win and
context is dropped when both apply. The card recommends sampling (temperature 0.7,
top_p 0.6, top_k 20, repetition_penalty 1.05); this engine uses greedy decoding
(temperature 0) for reproducibility and caching — a deliberate deviation.
"""
from __future__ import annotations

from b2c_contracts import EngineIdentity

from ..prompts import PromptTemplate, compose_version, get_template, language_name
from ..qa.checks import names_in
from .base import EngineCapabilities, EngineRequest, EngineResult, TextCompletionGateway
from .chat import output_token_budget

# template family -> role -> template id. Only documented templates are listed.
_FAMILIES: dict[str, dict[str, str]] = {
    "hy-mt2": {"default": "hy-mt2.default", "terminology": "hy-mt2.terminology",
               "terminology_line": "hy-mt2.terminology_line", "context": "hy-mt2.context"},
}


class PlainMTEngine:
    def __init__(self, gateway: TextCompletionGateway, identity: EngineIdentity,
                 capabilities: EngineCapabilities, template_id: str, *,
                 max_output_tokens: int = 4096) -> None:
        if template_id not in _FAMILIES:
            raise ValueError(f"no documented template family {template_id!r}; "
                             f"known: {sorted(_FAMILIES)}")
        self._gateway = gateway
        self._identity = identity
        self._capabilities = capabilities
        self._family = _FAMILIES[template_id]
        self._max_output_tokens = max_output_tokens

    @property
    def identity(self) -> EngineIdentity:
        default = get_template(self._family["default"])
        return self._identity.model_copy(update={"prompt_version": default.version_id})

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._capabilities

    def _template(self, role: str) -> PromptTemplate | None:
        tid = self._family.get(role)
        return get_template(tid) if tid else None

    def translate(self, request: EngineRequest) -> EngineResult:
        unit, caps = request.unit, self._capabilities
        source = unit.source_text
        if request.repair is not None:
            raise ValueError("plain MT models cannot revise a draft; retranslate or use a ChatEngine")
        if len(source) > caps.max_source_chars:
            raise ValueError(f"{unit.unit_id}: {len(source)} chars exceeds engine limit "
                             f"{caps.max_source_chars}; split the unit")
        target = language_name(request.target_language)

        folded = source.casefold()
        terms = [(s, t) for s, t in sorted(request.terms.items()) if s.casefold() in folded]
        locked = {s.casefold() for s, _ in terms}
        # D-047: approved names go through the same documented "reference translations" template
        terms += [(s, t) for s, t in sorted(names_in(source, request.entities).items())
                  if s.casefold() not in locked]
        terminology, line = self._template("terminology"), self._template("terminology_line")
        context = self._template("context")
        background = "\n\n".join(x for x in (request.context_before, request.context_after) if x)

        if caps.supports_terms and terms and terminology and line:
            used = [terminology, line]
            content = terminology.render(
                term_lines="\n".join(line.render(source_term=s, target_term=t) for s, t in terms),
                target_lang=target, source_text=source)
        elif caps.supports_context and background and context:
            used = [context]
            content = context.render(background_text=background, target_lang=target, source_text=source)
        else:
            default = get_template(self._family["default"])
            used = [default]
            content = default.render(target_lang=target, source_text=source)

        prompt_version = compose_version(*used)
        completion = self._gateway.complete_text(
            [{"role": "user", "content": content}], prompt_version=prompt_version,
            max_tokens=output_token_budget(source, self._max_output_tokens), temperature=0.0)
        return EngineResult(
            text=completion.text.strip(),
            identity=self._identity.model_copy(update={"prompt_version": prompt_version}),
            finish_reason=completion.finish_reason, usage=dict(completion.usage))
