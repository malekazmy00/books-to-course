#!/usr/bin/env python3
"""Copy a course's episode MP3s to an SD card / USB stick in playback order (D-038, D-042).

Many inexpensive MP3 players do not sort by name: they play files in the order of their FAT
directory entries, i.e. the order the files were written to the card. A file manager copies
in its own order (often by size, or in parallel). This script writes one file at a time in
episode order (00, 01, 02, ...), flushes and fsyncs each file and the directory before the
next, then reads the directory back and checks the entry order.

Targets:
  --dest DIR     a mounted card or a folder on it (vfat/exfat expected; a warning otherwise,
                 and the entry order is then not checked, since only FAT keeps write order)
  --image FILE   a FAT image file, written through mtools (mcopy/mdir); no mount, no root.
                 Also how the tests exercise a real FAT directory.

Rules: the destination directory must hold no .mp3 yet (deleted or older entries can take
earlier slots and break the order: use an empty folder or a freshly formatted card). If the
check fails, ``fatsort`` can reorder a card that was copied another way.

    python scripts/copy-course-to-card.py COURSE_DIR --dest /media/$USER/CARD/Course [--ascii-only]
    python scripts/copy-course-to-card.py COURSE_DIR --image card.img
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

FAT_TYPES = {"vfat", "msdos", "exfat", "fat", "fat32", "umsdos"}
_EPISODE = re.compile(r"^(\d+)(?: - .*)?\.mp3$", re.IGNORECASE)


class CopyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CopyItem:
    number: int
    source: Path
    dest_name: str


def plan_copy(source_dir: Path, *, ascii_only: bool = False) -> list[CopyItem]:
    """Episode files of ``source_dir`` (named by ``b2c_speech.episode_naming``) in episode order.

    Order is the episode number, not text order; files that are not .mp3 are ignored, an .mp3
    without a leading episode number is refused rather than guessed."""
    items, numbers = [], {}
    for path in sorted(Path(source_dir).iterdir()):
        if not path.is_file() or path.suffix.lower() != ".mp3":
            continue
        m = _EPISODE.match(path.name)
        if not m:
            raise CopyError(f"not an episode file name (expected 'NN - title.mp3'): {path.name}")
        number = int(m.group(1))
        if number in numbers:
            raise CopyError(f"duplicate episode number {number}: {numbers[number]} and {path.name}")
        numbers[number] = path.name
        label = m.group(1)
        items.append(CopyItem(number=number, source=path, dest_name=f"{label}.mp3" if ascii_only else path.name))
    if not items:
        raise CopyError(f"no episode .mp3 files in {source_dir}")
    return sorted(items, key=lambda i: i.number)


def _unescape_mount(path: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def filesystem_type(path: str | Path, *, mountinfo: str | None = None) -> str | None:
    """File system type of the mount that holds ``path`` (longest mount point prefix)."""
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text()
        except OSError:
            return None
    target = os.path.abspath(str(path))
    best, fstype = "", None
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        fields = left.split()
        if not sep or len(fields) < 5 or not right.split():
            continue
        mount = _unescape_mount(fields[4])
        inside = target == mount or target.startswith(mount.rstrip("/") + "/")
        if inside and len(mount) >= len(best):
            best, fstype = mount, right.split()[0]
    return fstype


class Target(Protocol):
    def describe(self) -> str: ...
    def fs_type(self) -> str | None: ...
    def entries(self) -> list[str]: ...
    def write(self, name: str, source: Path) -> None: ...
    def free_bytes(self) -> int | None: ...


class DirectoryTarget:
    """A mounted card. On FAT, Linux ``readdir`` (and Windows) list entries in directory order."""

    def __init__(self, root: Path, *, mountinfo: str | None = None):
        self.root = Path(root)
        self._mountinfo = mountinfo

    def describe(self) -> str:
        return str(self.root)

    def fs_type(self) -> str | None:
        return filesystem_type(self.root, mountinfo=self._mountinfo)

    def entries(self) -> list[str]:
        with os.scandir(self.root) as it:  # directory order, not sorted
            return [e.name for e in it]

    def write(self, name: str, source: Path) -> None:
        dest = self.root / name
        with open(source, "rb") as src, open(dest, "xb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)  # the directory entry itself, before the next file
        finally:
            os.close(fd)

    def free_bytes(self) -> int | None:
        return shutil.disk_usage(self.root).free


class MtoolsImageTarget:
    """A FAT image file written with mtools (``mcopy``) and listed with ``mdir`` (entry order)."""

    def __init__(self, image: Path, folder: str = ""):
        self.image = Path(image)
        self.folder = folder.strip("/")

    def _env(self) -> dict[str, str]:
        return {**os.environ, "MTOOLS_SKIP_CHECK": "1", "LC_ALL": os.environ.get("LC_ALL") or "C.UTF-8"}

    def _path(self, name: str = "") -> str:
        return "::/" + "/".join(p for p in (self.folder, name) if p)

    def describe(self) -> str:
        return f"{self.image} (FAT image, mtools)"

    def fs_type(self) -> str | None:
        return "fat"

    def entries(self) -> list[str]:
        out = subprocess.run(["mdir", "-i", str(self.image), "-b", self._path() + "/"], check=True,
                             capture_output=True, text=True, env=self._env()).stdout
        prefix = self._path().rstrip("/") + "/"
        return [line[len(prefix):] for line in out.splitlines() if line.startswith(prefix)]

    def write(self, name: str, source: Path) -> None:
        subprocess.run(["mcopy", "-i", str(self.image), "-D", "o", str(source), self._path(name)], check=True,
                       capture_output=True, env=self._env())
        fd = os.open(self.image, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def free_bytes(self) -> int | None:
        return None  # mcopy fails cleanly when the image is full


@dataclass(frozen=True)
class OrderCheck:
    ok: bool
    expected: list[str]
    found: list[str]
    first_mismatch: int | None = None


def verify_order(target: Target, expected: list[str]) -> OrderCheck:
    wanted = set(expected)
    found = [n for n in target.entries() if n in wanted]
    mismatch = next((i for i, (a, b) in enumerate(zip(found, expected)) if a != b), None)
    if mismatch is None and len(found) != len(expected):
        mismatch = min(len(found), len(expected))
    return OrderCheck(ok=mismatch is None, expected=expected, found=found, first_mismatch=mismatch)


@dataclass
class CopyReport:
    copied: list[str] = field(default_factory=list)
    fs_type: str | None = None
    order_checked: bool = False
    ok: bool = False
    warnings: list[str] = field(default_factory=list)


def copy_course(items: list[CopyItem], target: Target, *, log=lambda msg: None) -> CopyReport:
    report = CopyReport(fs_type=target.fs_type())
    is_fat = (report.fs_type or "").lower() in FAT_TYPES
    if not is_fat:
        report.warnings.append(
            f"destination file system is {report.fs_type or 'unknown'}, not FAT/exFAT: the order of directory "
            "entries is not kept there, so it is not checked (players that sort by name or read TRCK still work)")
    existing = [n for n in target.entries() if n.lower().endswith(".mp3")]
    if existing:
        raise CopyError(f"destination is not empty of .mp3 files ({len(existing)} found, e.g. {existing[0]}); "
                        "use an empty folder or a freshly formatted card so entries are written in order")
    need = sum(i.source.stat().st_size for i in items)
    free = target.free_bytes()
    if free is not None and free < need:
        raise CopyError(f"not enough space on the destination: need {need} bytes, {free} free")
    for n, item in enumerate(items, 1):
        target.write(item.dest_name, item.source)
        report.copied.append(item.dest_name)
        log(f"[{n}/{len(items)}] {item.dest_name}")
    check = verify_order(target, [i.dest_name for i in items])
    if is_fat:
        report.order_checked = True
        report.ok = check.ok
        if not check.ok:
            report.warnings.append(
                f"directory order differs from episode order at position {check.first_mismatch}: "
                f"expected {check.expected[check.first_mismatch:check.first_mismatch + 1]}, found "
                f"{check.found[check.first_mismatch:check.first_mismatch + 1]}; repair with `fatsort` or recopy "
                "to an empty folder")
    else:
        report.ok = sorted(check.found) == sorted(check.expected)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path, help="folder with the course episodes ('00 - ….mp3', '01 - ….mp3', …)")
    dest = ap.add_mutually_exclusive_group(required=True)
    dest.add_argument("--dest", type=Path, help="mounted card folder (must hold no .mp3 yet)")
    dest.add_argument("--image", type=Path, help="FAT image file to write with mtools")
    ap.add_argument("--image-folder", default="", help="folder inside the image (created beforehand with mmd)")
    ap.add_argument("--ascii-only", action="store_true", help="name files by number only (03.mp3)")
    ap.add_argument("--dry-run", action="store_true", help="print the copy order and stop")
    args = ap.parse_args(argv)
    try:
        items = plan_copy(args.source, ascii_only=args.ascii_only)
        if args.dry_run:
            for item in items:
                print(f"{item.number:>4}  {item.source.name}  ->  {item.dest_name}")
            return 0
        target: Target = DirectoryTarget(args.dest) if args.dest else MtoolsImageTarget(args.image, args.image_folder)
        report = copy_course(items, target, log=print)
    except (CopyError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for warning in report.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    state = "OK" if report.ok else "FAILED"
    order = "entry order verified" if report.order_checked else "entry order not checked"
    print(f"{state}: {len(report.copied)} files to {target.describe()} ({report.fs_type}); {order}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
