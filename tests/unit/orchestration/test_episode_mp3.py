"""One learner episode -> one gated MP3 artifact, after the audio (D-042 open item, D-044).

``speech_v2_encode_episode`` takes the episode's ACCEPTED speech tracks in order, streams them from
the artifact store to disk, normalizes and encodes them block by block (bounded memory), tags the
MP3 (ID3v2.3, 00/01/02 naming) and records a candidate ``episode-mp3`` artifact with two gates:
``speech.episode-loudness`` (the owner's current default, -16 LUFS +-1, true peak <= -1 dBTP) and
``speech.episode-format`` (MPEG-1 Layer III mono CBR, ID3v2.3). Nothing starts it automatically."""
from __future__ import annotations

import pytest
from b2c_contracts import ArtifactRef
from b2c_speech.audio.mp3 import mp3_frame_probe, read_id3
from temporalio.exceptions import ApplicationError

from b2c_orchestration.episode_mp3 import (EPISODE_MP3_STAGE, EncodeEpisodeInput, EncodeEpisodeMp3Workflow,
                                           EpisodeMp3Activities, episode_mp3_activities)
from b2c_orchestration.speech_v2 import AssembleSpeechTrackInput
from tests.unit.orchestration.test_speech_v2 import (prepared_text, promo, results, store,  # noqa: F401
                                                     synth_all)


def accepted_track(store, results, promo):  # noqa: F811
    a, prepared = prepared_text(store, results, promo)
    track, statuses = synth_all(a, prepared)
    out = a.assemble(AssembleSpeechTrackInput(course_id="c1", speech_text_ref=prepared.speech_text_ref, track=track,
                                              segments=statuses, require_intelligibility=False))
    assert out.status == "accepted"
    return out.artifact


def encode_input(refs, **kw):
    data = dict(course_id="c1", track_refs=refs, episode=1, last_episode=12, title="الفصل الأول: الهيمنة",
                book_title="كتاب التجربة", artist="مؤلف")
    data.update(kw)
    return EncodeEpisodeInput(**data)


def test_episode_is_encoded_tagged_gated_and_stored(store, results, promo, tmp_path):  # noqa: F811
    ref = accepted_track(store, results, promo)
    acts = EpisodeMp3Activities(artifact_store=store, promotion=promo, tmp_dir=str(tmp_path), block_s=0.5)
    out = acts.encode(encode_input([ref, ref]))
    assert out.status == "accepted", out
    assert out.filename == "01 - الفصل الأول الهيمنة.mp3"
    assert out.gates == {"speech.episode-loudness": True, "speech.episode-format": True}
    art = ArtifactRef.model_validate(out.artifact)
    assert art.stage == EPISODE_MP3_STAGE
    data = store.read(art, "episode.mp3")
    tags = read_id3(data)
    assert tags["version"] == "2.3.0" and tags["TRCK"] == "2/13" and tags["TIT2"] == "01 - الفصل الأول: الهيمنة"
    frames = mp3_frame_probe(data)
    assert frames["bitrates_kbps"] == [96] and frames["channel_modes"] == ["mono"] and frames["versions"] == ["MPEG-1"]
    assert out.metrics["integrated_lufs"] == pytest.approx(-16.0, abs=1.0) and out.metrics["true_peak_dbtp"] <= -1.0
    assert out.metrics["tracks"] == 2 and out.metrics["streamed"] is True
    assert not list(tmp_path.iterdir())  # temporary WAV/MP3 files are removed
    again = acts.encode(encode_input([ref, ref]))
    assert again.cache_hit and again.artifact == out.artifact


def test_tracks_must_be_accepted_speech_tracks_of_the_course(store, results, promo, tmp_path):  # noqa: F811
    ref = accepted_track(store, results, promo)
    acts = EpisodeMp3Activities(artifact_store=store, promotion=promo, tmp_dir=str(tmp_path))
    foreign = dict(ref, uri=ref["uri"].replace("/c1/", "/c2/"))
    with pytest.raises(ApplicationError) as err:
        acts.encode(encode_input([foreign]))
    assert err.value.type == "InvalidSpeechInput"
    unknown = dict(ref, sha256="0" * 64, key="0" * 64, uri=ref["uri"].rsplit("/", 2)[0] + "/" + "0" * 64 + "/")
    with pytest.raises(ApplicationError) as err:
        acts.encode(encode_input([unknown]))
    assert err.value.type == "NotAcceptedTrack"


def test_workflow_is_registered_but_not_chained_to_speech():
    from b2c_orchestration.speech_v2 import GenerateSpeechV2Workflow
    import inspect
    assert "speech_v2_encode_episode" not in inspect.getsource(GenerateSpeechV2Workflow)
    assert EncodeEpisodeMp3Workflow.__temporal_workflow_definition.name == "EncodeEpisodeMp3Workflow"
    acts = EpisodeMp3Activities(artifact_store=None, promotion=None)
    assert [f.__temporal_activity_definition.name for f in episode_mp3_activities(acts)] == ["speech_v2_encode_episode"]
