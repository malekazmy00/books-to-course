"""ChatEngine: an instruction-following chat model translates one unit (D-028).

Channel separation: the system message carries the instructions, register rules, the
locked terms that occur in the unit and (optionally) neighbouring context marked as
not-to-translate. The user message is exactly ``unit.source_text``. A repair adds the
typed issues to the system message and continues the conversation as
[system, user(source), assistant(previous draft), user(fixed revision request)].
Output is plain text; nothing is parsed as JSON.
"""
from __future__ import annotations

import json
import re

from b2c_contracts import EngineIdentity, QAIssue

from ..prompts import PromptTemplate, compose_version, get_template, language_name, repair_fix
from ..qa.checks import names_in
from .base import EngineCapabilities, EngineRequest, EngineResult, TextCompletionGateway

_MAX_EVIDENCE_CHARS = 600
_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("«", "»"), ("“", "”"), ("‘", "’"), ("`", "`"))
_LABEL = re.compile(r"^\s*(translation|arabic translation|arabic|english|الترجمة|النص المترجم|الترجمة العربية)"
                    r"\s*[:：]\s*", re.IGNORECASE)
_FENCE = re.compile(r"^```[A-Za-z]*\n(.*)\n```$", re.DOTALL)


def clean_output(text: str, source: str) -> str:
    """Strip whitespace, a code fence, a leading label and one wrapping quote pair, each only
    when the source itself does not have it."""
    out = text.strip()
    fence = _FENCE.match(out)
    if fence and "```" not in source:
        out = fence.group(1).strip()
    label = _LABEL.match(out)
    if label and not _LABEL.match(source):
        out = out[label.end():].strip()
    src = source.strip()
    for opening, closing in _QUOTE_PAIRS:
        inner = out[len(opening):-len(closing)] if len(out) > 2 else ""
        if (out.startswith(opening) and out.endswith(closing) and inner
                and opening not in inner and closing not in inner
                and not (src.startswith(opening) and src.endswith(closing))):
            out = inner.strip()
            break
    return out


def output_token_budget(source_text: str, ceiling: int) -> int:
    """Generous bound for one unit (Arabic expands; tokenizers vary), capped by ``ceiling``."""
    return max(1, min(ceiling, max(128, int(len(source_text) * 1.5) + 64)))


def _evidence(issue: QAIssue) -> str:
    raw = json.dumps(issue.evidence, ensure_ascii=False, sort_keys=True)
    return raw if len(raw) <= _MAX_EVIDENCE_CHARS else raw[:_MAX_EVIDENCE_CHARS] + "…"


class ChatEngine:
    def __init__(self, gateway: TextCompletionGateway, identity: EngineIdentity,
                 capabilities: EngineCapabilities, *, max_output_tokens: int = 4096) -> None:
        if not capabilities.follows_instructions:
            raise ValueError("ChatEngine needs an instruction-following model; use PlainMTEngine")
        self._gateway = gateway
        self._identity = identity
        self._capabilities = capabilities
        self._max_output_tokens = max_output_tokens

    @property
    def identity(self) -> EngineIdentity:
        base = compose_version(get_template("chat.translate.system"), get_template("chat.register.ar"))
        return self._identity.model_copy(update={"prompt_version": base})

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._capabilities

    def translate(self, request: EngineRequest) -> EngineResult:
        unit, caps = request.unit, self._capabilities
        source = unit.source_text
        if len(source) > caps.max_source_chars:
            raise ValueError(f"{unit.unit_id}: {len(source)} chars exceeds engine limit "
                             f"{caps.max_source_chars}; split the unit")
        if request.repair is not None and not caps.supports_repair:
            raise ValueError("this engine does not support repair requests")

        used: list[PromptTemplate] = []
        system_t = get_template("chat.translate.system")
        try:
            register_t = get_template(f"chat.register.{request.target_language}")
        except KeyError:
            register_t = get_template("chat.register.default")
        used += [system_t, register_t]
        parts = [system_t.render(source_language=language_name(request.source_language),
                                 target_language=language_name(request.target_language),
                                 register_rules=register_t.render())]

        if caps.supports_terms:
            folded = source.casefold()
            present = [(s, t) for s, t in sorted(request.terms.items()) if s.casefold() in folded]
            if present:
                terms_t, line_t = get_template("chat.terms"), get_template("chat.term_line")
                used += [terms_t, line_t]
                parts.append(terms_t.render(term_lines="\n".join(
                    line_t.render(source_term=s, target_term=t) for s, t in present)))
            locked = {s.casefold() for s, _ in present}
            # D-047: approved names occurring in the unit (as names, case-sensitive like the check)
            named = [(s, t) for s, t in sorted(names_in(source, request.entities).items())
                     if s.casefold() not in locked]
            if named:
                entities_t, line_t = get_template("chat.entities"), get_template("chat.term_line")
                used += [entities_t] + ([] if line_t in used else [line_t])
                parts.append(entities_t.render(term_lines="\n".join(
                    line_t.render(source_term=s, target_term=t) for s, t in named)))
                locked |= {s.casefold() for s, _ in named}
            hinted = [(s, t) for s, t in sorted(request.hints.items())
                      if s.casefold() in folded and s.casefold() not in locked]
            if hinted:  # D-039: preferred renderings, rendered only when present (prompt version unchanged otherwise)
                hints_t, line_t = get_template("chat.term_hints"), get_template("chat.term_line")
                used += [hints_t] + ([] if line_t in used else [line_t])
                parts.append(hints_t.render(term_lines="\n".join(
                    line_t.render(source_term=s, target_term=t) for s, t in hinted)))

        if caps.supports_context and (request.context_before or request.context_after):
            context_t = get_template("chat.context")
            blocks, block_ts = [], []
            for text, tid in ((request.context_before, "chat.context_before"),
                              (request.context_after, "chat.context_after")):
                if text:
                    block_t = get_template(tid)
                    block_ts.append(block_t)
                    blocks.append(block_t.render(context_text=text))
            used += [context_t, *block_ts]
            parts.append(context_t.render(context_blocks="\n\n".join(blocks)))

        messages = [{"role": "system", "content": ""}, {"role": "user", "content": source}]
        if request.repair is not None:
            repair_t, line_t = get_template("chat.repair.system"), get_template("chat.repair.issue_line")
            request_t = get_template("chat.repair.request")
            lines, fix_ts = [], []
            for issue in request.repair.issues:
                fix_t = repair_fix(issue.category)
                if fix_t not in fix_ts:
                    fix_ts.append(fix_t)
                lines.append(line_t.render(category=issue.category, severity=issue.severity,
                                           required_fix=fix_t.render(), evidence=_evidence(issue)))
            used += [repair_t, line_t, *fix_ts, request_t]
            parts.append(repair_t.render(issue_lines="\n".join(lines) or "- (no issue details)"))
            messages += [{"role": "assistant", "content": request.repair.previous_target},
                         {"role": "user", "content": request_t.render()}]
        messages[0]["content"] = "\n\n".join(parts)

        prompt_version = compose_version(*used)
        completion = self._gateway.complete_text(
            messages, prompt_version=prompt_version,
            max_tokens=output_token_budget(source, self._max_output_tokens), temperature=0.0)
        return EngineResult(
            text=clean_output(completion.text, source),
            identity=self._identity.model_copy(update={"prompt_version": prompt_version}),
            finish_reason=completion.finish_reason, usage=dict(completion.usage))
