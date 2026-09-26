"""Default activity RetryPolicy (docs/02-CONTRACTS.md §1.3)."""
from datetime import timedelta

from temporalio.common import RetryPolicy

DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=5,
    non_retryable_error_types=["ValidationError", "UnsupportedFormat", "RightsNotCleared",
                               "ConsentMissing"],
)
