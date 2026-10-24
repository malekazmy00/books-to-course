"""ArtifactStore: s3://artifacts/{course_id}/{stage}/{key}/<files>, idempotent (docs/02 §1.1-1.2)."""
import boto3
import hashlib
import io
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


@pytest.mark.parametrize("streamed", [False, True])
def test_racing_writer_never_replaces_a_committed_manifest(store, streamed):
    winner = store.put("crs_01", "outline", KEY, {"out.json": b"winner"})
    # The loser checked exists() before the winner committed its manifest.
    real_exists = store.exists
    calls = []

    def stale_exists(*args):
        calls.append(args)
        return None if len(calls) == 1 else real_exists(*args)
    store.exists = stale_exists
    if streamed:
        data = b"loser"
        loser = store.put_stream("crs_01", "outline", KEY, {"out.json": io.BytesIO(data)},
                                 {"out.json": hashlib.sha256(data).hexdigest()})
    else:
        loser = store.put("crs_01", "outline", KEY, {"out.json": b"loser"})
    assert loser == winner
    store.exists = real_exists
    assert store.exists("crs_01", "outline", KEY) == winner


def test_artifact_row_is_recorded_idempotently_on_every_access(store):
    class Recorder:
        def __init__(self):
            self.rows = []

        def record_artifact(self, course_id, ref):
            self.rows.append((course_id, ref.key))

    recorder = Recorder()
    # An artifact written by a store without a recorder (e.g. the old audio
    # worker) must still be registered when a recording store reuses it.
    store.put("crs_01", "outline", KEY, {"out.json": b"{}"})
    store.recorder = recorder
    store.get_or_compute("crs_01", "outline", KEY, lambda: {"out.json": b"{}"})
    store.put("crs_01", "outline", KEY, {"out.json": b"{}"})
    assert recorder.rows == [("crs_01", KEY), ("crs_01", KEY)]


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


def test_put_stream_roundtrip_and_digest_matches_byte_put(store):
    payload = b"streamed book content"
    metadata = b"{\"title\":\"book\"}"
    files = {"source.pdf": io.BytesIO(payload), "book.json": io.BytesIO(metadata)}
    hashes = {name: hashlib.sha256(stream.getvalue()).hexdigest() for name, stream in files.items()}
    streamed = store.put_stream("crs_01", "upload", KEY, files, hashes)
    byte_put = store.put("crs_02", "upload", KEY, {"source.pdf": payload, "book.json": metadata})
    assert streamed.sha256 == byte_put.sha256
    assert store.read(streamed, "source.pdf") == payload
    assert store.read(streamed, "book.json") == metadata


def test_put_stream_rejects_missing_or_invalid_hashes(store):
    with pytest.raises(ValueError):
        store.put_stream("crs_01", "upload", KEY, {"x": io.BytesIO(b"x")}, {})
    with pytest.raises(ValueError):
        store.put_stream("crs_01", "upload", KEY, {"x": io.BytesIO(b"x")}, {"x": "bad"})


def test_download_to_path_writes_artifact_without_read_api(store, tmp_path):
    ref = store.put("crs_01", "source", KEY, {"source.pdf": b"%PDF-1.7 test"})
    destination = tmp_path / "source.pdf"
    store.download_to_path(ref, "source.pdf", str(destination))
    assert destination.read_bytes() == b"%PDF-1.7 test"
