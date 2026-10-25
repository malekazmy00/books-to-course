"""scripts/copy-course-to-card.py (T-AUD-11): copy episodes to an SD card / USB stick in order.

Many cheap MP3 players play files in FAT directory-entry order (the order they were written),
so the script writes one file at a time in episode order with fsync, then reads the directory
back and checks the order. FAT is exercised on an image file through mtools (no mount, no
real disk); the test is skipped when mtools is not installed (set B2C_MTOOLS_BIN to its bin dir).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "copy-course-to-card.py"
SPEC = importlib.util.spec_from_file_location("copy_course_to_card", SCRIPT)
card = importlib.util.module_from_spec(SPEC)
sys.modules["copy_course_to_card"] = card
SPEC.loader.exec_module(card)

TITLES = ["مقدمة الكورس", "الفصل الأول: التفكير", "الفصل الثاني", "الفصل الثالث", "الفصل الرابع", "الفصل الخامس",
          "الفصل السادس", "الفصل السابع", "الفصل الثامن", "الفصل التاسع", "الفصل العاشر", "الخاتمة"]


def course(tmp_path: Path, n=len(TITLES)) -> Path:
    from b2c_speech.episode_naming import episode_filename
    src = tmp_path / "course"
    src.mkdir()
    for i in reversed(range(n)):  # created out of order on purpose
        (src / episode_filename(i, TITLES[i], last_episode=n - 1)).write_bytes(bytes([i]) * (1000 + i))
    (src / "cover.jpg").write_bytes(b"jpg")  # not an episode: ignored
    return src


@pytest.fixture
def mtools(monkeypatch):
    extra = os.environ.get("B2C_MTOOLS_BIN")
    if extra:
        monkeypatch.setenv("PATH", extra + os.pathsep + os.environ["PATH"])
    if not (shutil.which("mformat") and shutil.which("mcopy") and shutil.which("mdir")):
        pytest.skip("mtools not installed (set B2C_MTOOLS_BIN)")
    monkeypatch.setenv("MTOOLS_SKIP_CHECK", "1")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")


def fat_image(path: Path) -> Path:
    subprocess.run(["mformat", "-i", str(path), "-C", "-T", "65536", "-v", "B2C", "::"], check=True)
    return path


# ------------------------------------------------------------------ plan
def test_plan_orders_by_episode_number_and_ignores_non_episodes(tmp_path):
    items = card.plan_copy(course(tmp_path))
    assert [i.number for i in items] == list(range(12))
    assert items[0].dest_name == "00 - مقدمة الكورس.mp3" and items[1].dest_name == "01 - الفصل الأول التفكير.mp3"


def test_numeric_order_even_if_widths_differ(tmp_path):
    src = tmp_path / "c"
    src.mkdir()
    for name in ("100 - c.mp3", "99 - b.mp3", "9 - a.mp3"):
        (src / name).write_bytes(b"x")
    assert [i.number for i in card.plan_copy(src)] == [9, 99, 100]


def test_ascii_only_names_keep_the_label_width(tmp_path):
    items = card.plan_copy(course(tmp_path), ascii_only=True)
    assert [i.dest_name for i in items][:3] == ["00.mp3", "01.mp3", "02.mp3"]
    assert all(i.dest_name.isascii() for i in items)


def test_unnumbered_or_duplicate_episodes_are_refused(tmp_path):
    src = tmp_path / "c"
    src.mkdir()
    (src / "01 - a.mp3").write_bytes(b"x")
    (src / "intro.mp3").write_bytes(b"x")
    with pytest.raises(card.CopyError, match="intro.mp3"):
        card.plan_copy(src)
    (src / "intro.mp3").unlink()
    (src / "01 - b.mp3").write_bytes(b"x")
    with pytest.raises(card.CopyError, match="duplicate"):
        card.plan_copy(src)


# ------------------------------------------------------------------ filesystem type
MOUNTINFO = """\
22 1 8:2 / / rw,relatime - ext4 /dev/sdb rw
40 22 8:33 / /media/card rw,relatime - vfat /dev/sdc1 rw,fmask=0022
41 22 8:49 / /media/stick rw,relatime - exfat /dev/sdd1 rw
42 22 0:50 / /mnt/e rw,relatime - 9p drvfs rw
"""


@pytest.mark.parametrize("path,fs", [("/media/card/Course", "vfat"), ("/media/stick", "exfat"),
                                     ("/media/cardx", "ext4"), ("/mnt/e/x", "9p"), ("/home/u", "ext4")])
def test_filesystem_type_is_the_longest_mount_prefix(path, fs):
    assert card.filesystem_type(path, mountinfo=MOUNTINFO) == fs


def test_mount_points_with_escaped_spaces():
    info = "50 22 8:33 / /media/my\\040card rw - vfat /dev/sdc1 rw\n"
    assert card.filesystem_type("/media/my card/x", mountinfo=info) == "vfat"


# ------------------------------------------------------------------ copy to a mounted directory
def test_directory_copy_writes_in_order_with_fsync_and_warns_when_not_fat(tmp_path, monkeypatch):
    src = course(tmp_path)
    dest = tmp_path / "card"
    dest.mkdir()
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(card.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    writes = []
    target = card.DirectoryTarget(dest, mountinfo="22 1 8:2 / / rw - ext4 /dev/sdb rw\n")
    original = target.write
    monkeypatch.setattr(target, "write", lambda name, path: (writes.append(name), original(name, path))[1])
    report = card.copy_course(card.plan_copy(src), target)
    assert writes == [i.dest_name for i in card.plan_copy(src)]
    assert len(synced) >= 2 * len(writes)  # the file and its directory, after every file
    assert sorted(os.listdir(dest)) == sorted(writes)
    assert all((dest / i.dest_name).read_bytes() == i.source.read_bytes() for i in card.plan_copy(src))
    assert report.order_checked is False and report.ok is True
    assert any("FAT" in w for w in report.warnings)


def test_destination_with_mp3_files_already_is_refused(tmp_path):
    src = course(tmp_path)
    dest = tmp_path / "card"
    dest.mkdir()
    (dest / "old.mp3").write_bytes(b"x")
    with pytest.raises(card.CopyError, match="empty"):
        card.copy_course(card.plan_copy(src), card.DirectoryTarget(dest, mountinfo=MOUNTINFO))


def test_not_enough_space_is_refused_before_writing(tmp_path, monkeypatch):
    src = course(tmp_path)
    dest = tmp_path / "card"
    dest.mkdir()
    target = card.DirectoryTarget(dest, mountinfo=MOUNTINFO)
    monkeypatch.setattr(target, "free_bytes", lambda: 10)
    with pytest.raises(card.CopyError, match="space"):
        card.copy_course(card.plan_copy(src), target)
    assert os.listdir(dest) == []


# ------------------------------------------------------------------ FAT image through mtools
def test_fat_image_entries_follow_episode_order(tmp_path, mtools):
    src = course(tmp_path)
    image = fat_image(tmp_path / "card.img")
    target = card.MtoolsImageTarget(image)
    report = card.copy_course(card.plan_copy(src), target)
    assert report.ok and report.order_checked and report.warnings == []
    assert target.entries() == [i.dest_name for i in card.plan_copy(src)]


def test_fat_image_ascii_mode(tmp_path, mtools):
    image = fat_image(tmp_path / "card.img")
    report = card.copy_course(card.plan_copy(course(tmp_path), ascii_only=True), card.MtoolsImageTarget(image))
    assert report.ok and card.MtoolsImageTarget(image).entries() == [f"{i:02d}.mp3" for i in range(12)]


def test_verification_detects_a_card_written_out_of_order(tmp_path, mtools):
    src = course(tmp_path)
    items = card.plan_copy(src)
    image = fat_image(tmp_path / "card.img")
    target = card.MtoolsImageTarget(image)
    for item in reversed(items):  # what a drag-and-drop copy can do
        target.write(item.dest_name, item.source)
    check = card.verify_order(target, [i.dest_name for i in items])
    assert check.ok is False and check.first_mismatch == 0


def test_cli_copies_to_an_image(tmp_path, mtools, capsys):
    src = course(tmp_path)
    image = fat_image(tmp_path / "card.img")
    assert card.main([str(src), "--image", str(image)]) == 0
    out = capsys.readouterr().out
    assert "12" in out and "OK" in out


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    src = course(tmp_path)
    dest = tmp_path / "card"
    dest.mkdir()
    assert card.main([str(src), "--dest", str(dest), "--dry-run"]) == 0
    assert os.listdir(dest) == [] and "00 - مقدمة الكورس.mp3" in capsys.readouterr().out
