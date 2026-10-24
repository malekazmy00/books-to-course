"""One corrupt audio checkpoint must not hide every course's saved audio."""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime

from b2c_api.audio_exports import course_audio_checkpoints
from b2c_contracts import ArtifactRef


class Store:
    bucket = "artifacts"

    def __init__(self, objects):
        self.s3 = self
        self.objects = objects

    def list_objects_v2(self, **kw):
        return {"Contents": [{"Key": key} for key in self.objects if key.startswith(kw["Prefix"])]}

    def get_object(self, **kw):
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}


def manifest(wid, course="course"):
    ref = ArtifactRef(stage="generate-mp3-track", key="a" * 64,
                      uri=f"s3://artifacts/{course}/generate-mp3-track/" + "a" * 64 + "/",
                      sha256="b" * 64, created_at=datetime.now(UTC))
    return {"course_id": course, "workflow_id": wid,
            "tracks": [{"track_id": "l1", "artifact_ref": ref.model_dump(mode="json")}]}


def test_unattributable_and_foreign_bad_entries_do_not_break_a_course(caplog):
    good = "b2c-mp3-" + "1" * 64
    objects = {
        f"_system/audio-progress/{good}.json": json.dumps(manifest(good)).encode(),
        "_system/audio-progress/corrupt.json": b"{not json",
        "_system/audio-progress/list.json": b"[]",
        "_system/audio-progress/no-course.json": json.dumps({"workflow_id": good}).encode(),
        # Invalid checkpoints of another course are that course's problem.
        "_system/audio-progress/bad-id.json": json.dumps(manifest("b2c-mp3-XYZ", "other")).encode(),
        "_system/audio-progress/bad-track.json": json.dumps({**manifest("b2c-mp3-" + "2" * 64, "other"),
                                                             "tracks": [{"track_id": "x"}]}).encode(),
    }
    with caplog.at_level("WARNING"):
        records = course_audio_checkpoints(Store(objects), "artifacts", "course")
    assert [record["workflow_id"] for record in records] == [good]
    assert sum("skipping" in message for message in caplog.messages) == 3
