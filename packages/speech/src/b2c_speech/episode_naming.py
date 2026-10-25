"""Episode file naming for SD-card MP3 players (D-038).

Many inexpensive players play files in the order of their FAT directory entries (the order they
were copied), others sort names as text, and some read the ID3 track number. To get the same order
on all of them:

* names start with the zero-padded episode number (00 = course introduction, then 01..n), wide
  enough that text order equals number order (two digits, three from 100 episodes on);
* files are copied to the card in that order (``playback_order``); ``fatsort`` can repair a card
  copied in another order;
* the ID3 track number (TRCK, 1-based "k/total") is set as well.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_UNSAFE = re.compile(r'[\\/:*?"<>|؟\x00-\x1f]+')
_SPACES = re.compile(r"\s+")


def episode_label(number: int, *, last_episode: int) -> str:
    if number < 0 or last_episode < 0 or number > last_episode:
        raise ValueError("episode numbers run from 0 (course introduction) to last_episode")
    return f"{number:0{max(2, len(str(last_episode)))}d}"


def episode_filename(number: int, title: str, *, last_episode: int, ascii_only: bool = False,
                     extension: str = "mp3") -> str:
    label = episode_label(number, last_episode=last_episode)
    if ascii_only:  # some players cannot display non-Latin names; the number alone keeps the order
        return f"{label}.{extension}"
    clean = _SPACES.sub(" ", _UNSAFE.sub(" ", title)).strip(" .")
    return f"{label} - {clean}.{extension}" if clean else f"{label}.{extension}"


def episode_tags(number: int, title: str, *, book_title: str, last_episode: int,
                 artist: str | None = None) -> dict[str, str]:
    label = episode_label(number, last_episode=last_episode)
    tags = {"TRCK": f"{number + 1}/{last_episode + 1}", "TIT2": f"{label} - {title}", "TALB": book_title}
    if artist:
        tags["TPE1"] = artist
    return tags


def playback_order(filenames: Iterable[str]) -> list[str]:
    """The order to copy files to the card: by name, which equals episode order."""
    return sorted(filenames)


__all__ = ["episode_filename", "episode_label", "episode_tags", "playback_order"]
