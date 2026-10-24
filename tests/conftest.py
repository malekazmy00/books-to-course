import os

import pytest

# The API requires a bearer token by default. Unit tests build apps without a token file, so they opt
# out explicitly here; tests of the auth middleware turn it back on themselves.
os.environ.setdefault("B2C_API_AUTH_REQUIRED", "false")


def pytest_collection_modifyitems(config, items):
    """Integration tests need the b2c stack; they run inside the b2c network (make test-integration)."""
    if os.environ.get("B2C_RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="needs b2c stack: run `make test-integration`")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
