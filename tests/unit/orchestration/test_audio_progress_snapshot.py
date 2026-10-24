import io
import json
from b2c_orchestration.audio import persist_audio_progress_snapshot


class Missing(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 404}}


class S3:
    def __init__(self):
        self.files = {}
    def get_object(self, Bucket, Key):
        if Key not in self.files:
            raise Missing()
        return {"Body": io.BytesIO(self.files[Key])}
    def put_object(self, Bucket, Key, Body, **kwargs):
        self.files[Key] = Body


class Store:
    bucket = "artifacts"
    def __init__(self):
        self.s3 = S3()


def test_retry_keeps_previous_populated_manifest_without_claiming_old_audio_is_current():
    store = Store()
    old = {"workflow_id": "audio-1", "course_id": "course", "tracks": [{"track_id": "lesson-1"}]}
    persist_audio_progress_snapshot(store, old)
    reset = {"workflow_id": "audio-1", "course_id": "course", "tracks": []}
    persist_audio_progress_snapshot(store, reset)
    current = json.loads(store.s3.files["_system/audio-progress/audio-1.json"])
    archived = json.loads(store.s3.files[current["previous_snapshot_key"]])
    assert archived["tracks"] == old["tracks"]
    assert current["tracks"] == []
    assert current["previous_saved_tracks"] == 1
    # Subsequent progress keeps the archived comparison copy discoverable.
    updated = {"workflow_id": "audio-1", "course_id": "course", "tracks": [{"track_id": "new-lesson-1"}]}
    persist_audio_progress_snapshot(store, updated)
    assert updated["previous_snapshot_key"] == current["previous_snapshot_key"]
    assert json.loads(store.s3.files[current["previous_snapshot_key"]]) == old
