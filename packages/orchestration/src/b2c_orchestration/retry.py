"""Default activity RetryPolicy (docs/02-CONTRACTS.md §1.3)."""
from datetime import timedelta

from temporalio.common import RetryPolicy

DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=5,
    # Deterministic failures only: retrying cannot change the outcome.
    # FreeTokenQuotaExceeded is a lifetime cap that never refills;
    # InvalidLLMOutput is raised after the gateway's own schema-repair retries
    # at temperature 0 (docs/02 §1.4: never silently accepted, goes to review).
    non_retryable_error_types=["ValidationError", "UnsupportedFormat", "RightsNotCleared",
                               "ConsentMissing", "OutlineNeedsReview", "OpenRouterQuotaExceeded",
                               "OpenRouterRateLimited", "LLMOutputTruncated",
                               "FreeTokenQuotaExceeded", "InvalidLLMOutput"],
)
