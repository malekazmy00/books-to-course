"""ArtifactStore: s3://artifacts/{course_id}/{stage}/{key}/<files>, idempotent (docs/02 §1.1-1.2)."""
import boto3
import pytest
from moto import mock_aws

from b2c_contracts import ArtifactRef, stage_key
from b2c_core import ArtifactStore

KEY = stage_key("ingest", "1", ["sha_a"], {"ocr": True}, None, None)


@pytest.fixture
def store():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        st = ArtifactStore(s3_client=s3, bucket="artifacts")
        st.ensure_bucket()
        yield st


def test_put_returns_ref_with_contract_uri(store):
    ref = store.put("crs_01", "ingest", KEY, {"parsed.json": b'{"a":1}'})
    assert isinstance(ref, ArtifactRef)
    assert ref.uri == f"s3://artifacts/crs_01/ingest/{KEY}/"
    assert ref.key == KEY and ref.stage == "ingest"
    assert ref.created_at.tzinfo is not None


def test_roundtrip_and_exists(store):
    assert store.exists("crs_01", "ingest", KEY) is None
    ref = store.put("crs_01", "ingest", KEY, {"a.txt": b"A", "b/c.txt": b"C"})
    assert store.exists("crs_01", "ingest", KEY) == ref
    assert store.read(ref, "a.txt") == b"A"
    assert store.read(ref, "b/c.txt") == b"C"
    assert sorted(store.list_files(ref)) == ["a.txt", "b/c.txt"]


def test_sha256_is_deterministic_over_content(store):
    r1 = store.put("c1", "s", KEY, {"x": b"1", "y": b"2"})
    r2 = store.put("c2", "s", KEY, {"y": b"2", "x": b"1"})
    r3 = store.put("c3", "s", KEY, {"x": b"1", "y": b"3"})
    assert r1.sha256 == r2.sha256 != r3.sha256


def test_put_is_idempotent_and_does_not_overwrite(store):
    r1 = store.put("crs_01", "ingest", KEY, {"a.txt": b"first"})
    r2 = store.put("crs_01", "ingest", KEY, {"a.txt": b"second"})
    assert r2 == r1
    assert store.read(r1, "a.txt") == b"first"


def test_get_or_compute_runs_once(store):
    calls = []

    def compute():
        calls.append(1)
        return {"out.json": b"{}"}

    r1, hit1 = store.get_or_compute("crs_01", "outline", KEY, compute)
    r2, hit2 = store.get_or_compute("crs_01", "outline", KEY, compute)
    assert (hit1, hit2) == (False, True)
    assert r1 == r2 and len(calls) == 1


@pytest.mark.parametrize("course,stage,key,name", [
    ("../x", "s", KEY, "f"),
    ("c", "a/b", KEY, "f"),
    ("c", "s", "not-a-sha", "f"),
    ("c", "s", KEY, "../escape"),
    ("c", "s", KEY, "_manifest.json"),
])
def test_rejects_unsafe_path_components(store, course, stage, key, name):
    with pytest.raises(ValueError):
        store.put(course, stage, key, {name: b"x"})


def test_empty_artifact_rejected(store):
    with pytest.raises(ValueError):
        store.put("c", "s", KEY, {})
