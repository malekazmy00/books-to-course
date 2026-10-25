"""Episode file names for SD-card MP3 players (D-038)."""
import pytest

from b2c_speech.episode_naming import episode_filename, episode_label, episode_tags, playback_order


def test_labels_are_zero_padded_and_the_intro_is_00():
    assert [episode_label(n, last_episode=12) for n in (0, 1, 9, 12)] == ["00", "01", "09", "12"]


def test_width_grows_when_a_course_has_100_or_more_episodes():
    assert episode_label(0, last_episode=99) == "00"
    assert [episode_label(n, last_episode=120) for n in (0, 7, 120)] == ["000", "007", "120"]


def test_lexical_order_equals_episode_order():
    names = [episode_filename(n, f"T{n}", last_episode=120) for n in range(121)]
    assert sorted(names) == names


def test_filename_keeps_the_arabic_title_but_strips_unsafe_characters():
    assert episode_filename(3, "التحليل: الفرضيات/المتنافسة؟", last_episode=40) == \
        "03 - التحليل الفرضيات المتنافسة.mp3"


def test_ascii_mode_for_players_that_cannot_show_arabic_names():
    assert episode_filename(3, "التحليل", last_episode=40, ascii_only=True) == "03.mp3"


def test_id3_tags_carry_the_track_number_for_tag_aware_players():
    tags = episode_tags(3, "التحليل", book_title="كتاب", last_episode=40)
    assert tags == {"TRCK": "4/41", "TIT2": "03 - التحليل", "TALB": "كتاب"}


def test_playback_order_is_the_copy_order():
    files = [episode_filename(n, f"T{n}", last_episode=3) for n in (2, 0, 3, 1)]
    assert playback_order(files) == sorted(files)


def test_negative_episode_is_refused():
    with pytest.raises(ValueError):
        episode_label(-1, last_episode=3)
