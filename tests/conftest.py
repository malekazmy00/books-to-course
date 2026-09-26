import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Integration tests need the b2c stack; they run inside the b2c network (make test-integration)."""
    if os.environ.get("B2C_RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="needs b2c stack: run `make test-integration`")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
