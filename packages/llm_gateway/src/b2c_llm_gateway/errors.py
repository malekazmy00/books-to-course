class LLMGatewayError(Exception):
    pass


class CloudProviderNotAllowed(LLMGatewayError):
    """Raised for non-local endpoints: local models only in the current stage (D-004)."""


class LLMUnavailable(LLMGatewayError):
    """Transport/provider error; outcome_unknown marks calls unsafe to repeat verbatim."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown


class InvalidLLMOutput(LLMGatewayError):
    """Output failed schema validation after all retries. Never accepted silently (docs/02 §1.4)."""

    def __init__(self, message: str, *, attempts: int, last_output: str, errors: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_output = last_output
        self.errors = errors



class OpenRouterRateLimited(LLMGatewayError):
    """OpenRouter free endpoint or provider pool is rate limited; stop this activity."""


class LLMOutputTruncated(LLMGatewayError):
    """The response hit max output tokens; preserve partial output and usage for audit."""

    def __init__(self, message: str, *, last_output: str = "",
                 usage: dict[str, int | float] | None = None,
                 model_id: str | None = None, finish_reason: str | None = None) -> None:
        super().__init__(message)
        self.last_output = last_output
        self.usage = usage or {}
        self.model_id = model_id
        self.finish_reason = finish_reason


class LLMRequestRejected(LLMGatewayError):
    """Permanent HTTP 400 from a provider, with only sanitized error metadata."""

    def __init__(self, status_code: int, *, code: str | None = None,
                 param: str | None = None, error_type: str | None = None,
                 diagnostic: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.param = param
        self.error_type = error_type
        self.diagnostic = diagnostic
        details = ", ".join(f"{name}={value}" for name, value in (
            ("code", code), ("param", param), ("type", error_type),
            ("diagnostic", diagnostic)) if value)
        super().__init__(f"LLM provider rejected request HTTP {status_code}" +
                         (f" ({details})" if details else ""))
