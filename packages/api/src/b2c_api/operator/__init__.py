"""Operator API v3 (`/v3/operator/...`): read-only stages and progress of every book (new path).

* ``schemas``  response models (the OpenAPI contract in docs/api/operator-v3.openapi.json)
* ``reader``   the only place that queries Postgres (read-only, indexed, bounded), the store and Temporal
* ``progress`` pure stage computation (status, gate, counts, rate, ETA)
* ``router``   FastAPI routes with a 20 s cache
"""
from b2c_api.operator.router import operator_router

__all__ = ["operator_router"]
