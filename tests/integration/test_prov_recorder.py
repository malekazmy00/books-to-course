"""ProvRecorder against the real b2c Postgres (run inside the b2c network)."""
import uuid

import pytest

from b2c_core import ProvRecorder, Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def prov():
    rec = ProvRecorder(Settings().pg_dsn)
    rec.migrate()
    rec.migrate()  # idempotent
    return rec


def test_lineage_roundtrip(prov):
    run = uuid.uuid4().hex[:8]
    src = f"art:ingest:{run}"
    out = f"art:outline:{run}"
    act = prov.start_activity(stage="outline", stage_version="1", agent="mock-model@0",
                              prompt_version="p1", config={"x": 1})
    prov.used(act, src, role="input")
    prov.generated(out, act)
    prov.derived(out, src)
    prov.end_activity(act)

    lin = prov.lineage(out)
    assert lin["entity"] == out
    assert lin["wasGeneratedBy"]["activity"] == act
    assert lin["wasGeneratedBy"]["agent"] == "mock-model@0"
    assert lin["wasGeneratedBy"]["prompt_version"] == "p1"
    assert lin["wasDerivedFrom"] == [src]
    assert lin["used"] == [{"entity": src, "role": "input"}]


def test_record_artifact_is_idempotent(prov):
    from datetime import datetime, timezone

    from b2c_contracts import ArtifactRef, stage_key

    key = stage_key("t", "1", [uuid.uuid4().hex], {}, None, None)
    ref = ArtifactRef(stage="t", key=key, uri=f"s3://artifacts/c/t/{key}/", sha256="0" * 64,
                      created_at=datetime.now(timezone.utc))
    prov.record_artifact("c", ref)
    prov.record_artifact("c", ref)
    assert prov.get_artifact("c", "t", key) == ref
