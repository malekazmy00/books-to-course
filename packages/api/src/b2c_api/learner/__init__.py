"""Learner API v3 (`/v3/learner/...`): read-only, accepted artifacts only, answers hidden.

* ``schemas``  the response/request models (the OpenAPI contract in docs/api/learner-v3.openapi.json)
* ``mapping``  the ONLY place that knows stored artifact formats (outline v2, concept layer, and the
               provisional episode-script / episode-audio formats). Replace it when those change.
* ``router``   FastAPI routes: registry lookup -> ``deps.require_accepted_artifact`` -> mapping.
"""
from b2c_api.learner.router import learner_router

__all__ = ["learner_router"]
