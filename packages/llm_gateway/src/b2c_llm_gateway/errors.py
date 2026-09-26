class LLMGatewayError(Exception):
    pass


class CloudProviderNotAllowed(LLMGatewayError):
    """Raised for non-local endpoints: local models only in the current stage (D-004)."""


class LLMUnavailable(LLMGatewayError):
    """Transport error or non-2xx from the model server (retryable by the orchestrator)."""


class InvalidLLMOutput(LLMGatewayError):
    """Output failed schema validation after all retries. Never accepted silently (docs/02 §1.4)."""

    def __init__(self, message: str, *, attempts: int, last_output: str, errors: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_output = last_output
        self.errors = errors
