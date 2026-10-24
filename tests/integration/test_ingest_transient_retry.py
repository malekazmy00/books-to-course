from __future__ import annotations

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

from b2c_contracts import ArtifactRef, BookUpload
from b2c_core import Settings
from b2c_orchestration.ingest import IngestPDFActivities, IngestPDFInput
from b2c_orchestration.retry import DEFAULT_RETRY

pytestmark = pytest.mark.integration


def _text_pdf() -> bytes:
    """Build a minimal one-page, extractable PDF without external fixture files."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length 47 >>\\nstream\\nBT /F1 12 Tf 72 720 Td (Chapter 1) Tj ET\\nendstream".replace(b"\\n", b"\n"),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode())
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(out)


class _TransientStore:
    def __init__(self, pdf: bytes):
        self.pdf = pdf
        self.download_attempts = 0
        self.compute_calls = 0
        self.saved: dict[str, bytes] = {}
        self.ref: ArtifactRef | None = None

    def download_to_path(self, ref: ArtifactRef, name: str, path: str) -> None:
        self.download_attempts += 1
        if self.download_attempts == 1:
            raise ConnectionError("synthetic one-time MinIO transport reset")
        Path(path).write_bytes(self.pdf)

    def get_or_compute(self, course_id: str, stage: str, key: str, compute):
        self.compute_calls += 1
        self.saved = dict(compute())
        digest = hashlib.sha256(b"".join(self.saved.values())).hexdigest()
        self.ref = ArtifactRef(stage=stage, key=key,
            uri=f"s3://artifacts/{course_id}/{stage}/{key}/", sha256=digest,
            created_at=datetime.now(timezone.utc))
        return self.ref, False

    def read(self, ref: ArtifactRef, name: str) -> bytes:
        return self.saved[name]


@workflow.defn(name="b2c_test_ingest_transient_retry", sandboxed=False)
class _RetryWorkflow:
    @workflow.run
    async def run(self, args: tuple[IngestPDFInput, str]) -> dict:
        inp, activity_queue = args
        return await workflow.execute_activity(
            "ingest_parse_pdf", inp, task_queue=activity_queue,
            retry_policy=DEFAULT_RETRY, start_to_close_timeout=timedelta(minutes=2),
        )


async def test_ingest_activity_retries_transient_download_and_recovers():
    settings = Settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    queue = f"b2c-test-ingest-retry-{uuid.uuid4().hex}"
    pdf = _text_pdf()
    pdf_sha = hashlib.sha256(pdf).hexdigest()
    upload = BookUpload(book_id="retry-fixture", role="supplementary", file_sha256=pdf_sha,
                        mime="application/pdf", title="Retry fixture", rights_status="internal_test_only")
    source = ArtifactRef(stage="book-upload", key=pdf_sha,
        uri=f"s3://artifacts/retry-fixture/book-upload/{pdf_sha}/", sha256=pdf_sha,
        created_at=datetime.now(timezone.utc))
    inp = IngestPDFInput(course_id="retry-fixture-course", upload=upload.model_dump(mode="json"),
                         source_ref=source.model_dump(mode="json"), supplementary_book_ids=[])
    store = _TransientStore(pdf)
    activities = IngestPDFActivities(store=store)
    workflow_id = f"b2c-test-ingest-retry-{uuid.uuid4().hex}"

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        async with Worker(client, task_queue=queue, workflows=[_RetryWorkflow],
                          activities=[activities.parse_pdf], activity_executor=executor):
            handle = await client.start_workflow(_RetryWorkflow.run, (inp, queue), id=workflow_id,
                                                 task_queue=queue)
            result = await handle.result()
    finally:
        executor.shutdown(wait=True)

    assert store.download_attempts == 2
    assert store.compute_calls == 2
    assert result["page_count"] == 1
    assert result["block_count"] >= 1
    assert result["cache_hit"] is False
