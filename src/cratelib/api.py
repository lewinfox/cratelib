"""Read a whole library, write a whole library, or open one to edit in place."""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import grid as gridlib
from .edit import Editor
from .keys import KeyNotation
from .model import Library
from .paths import PathMap
from .rekordbox.usb import OneLibraryMode
from .rekordbox.xml import RekordboxWriteOptions
from .sources import Mixxx, RekordboxDb, RekordboxUsb, RekordboxXml, Serato, Source

Progress = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


# --- options -------------------------------------------------------------------------


@dataclass
class MixxxOptions:
    key_notation: KeyNotation = KeyNotation.MUSICAL
    playlists_as_crates: bool = False  # write every playlist as a crate
    memory_cues_to_hot_cues: bool = True  # Mixxx has no memory cues: use free hot cue slots
    overwrite_existing: bool = True  # replace cues and grids of tracks already in the library
    base: Path | None = None  # for a new library: start from a copy of this mixxxdb.sqlite


@dataclass
class SeratoOptions:
    key_notation: KeyNotation = KeyNotation.MUSICAL
    # Serato keeps cues and beat grids inside the audio files: write them there too.
    file_tags: bool = False
    max_hot_cues: int = 8
    memory_cues_to_hot_cues: bool = True  # Serato has no memory cues: use free hot cue slots
    copy_missing: bool = True  # on a USB stick: copy tracks that aren't on it onto it
    base: Path | None = None  # for a new library: start from this database V2


RekordboxXmlOptions = RekordboxWriteOptions


@dataclass
class RekordboxUsbOptions:
    copy_missing: bool = True  # copy tracks that aren't on the stick onto it
    waveforms: bool = True  # measure waveforms with ffmpeg (else reuse or placeholders)
    transcode: bool = True  # convert formats players can't read (Ogg, Opus...) to MP3
    device_name: str = ""
    onelibrary: OneLibraryMode = OneLibraryMode.AUTO
    # Also put a rekordbox.xml of the stick's tracks at its root, for importing into
    # rekordbox on a computer (which can't open a stick's device library directly).
    xml: bool = True
    xml_root: str = ""  # how that computer sees the stick (E:/, /Volumes/STICK); "" = here


WriteOptions = MixxxOptions | SeratoOptions | RekordboxXmlOptions | RekordboxUsbOptions


@dataclass
class WriteResult:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


# --- read ----------------------------------------------------------------------------


def read(
    source: Source,
    paths: PathMap | None = None,
    *,
    file_tags: bool = True,
    progress: Progress = _quiet,
) -> Library:
    """Read a whole library.

    ``paths`` says how the library's program sees this machine's files; each track's
    ``file`` is set where its file is found. ``file_tags=False`` skips what is stored
    outside the database (Serato's cues and grids in audio files, Rekordbox's grids
    in analysis files), which is much faster.
    """
    paths = paths or PathMap()
    resolve = paths.find
    match source:
        case Mixxx():
            from .mixxx import MixxxReadOptions, read_mixxx

            library = read_mixxx(
                Path(source.path), MixxxReadOptions(source.mp3_decoder), resolve, progress
            )
        case RekordboxXml():
            from .rekordbox.xml import read_rekordbox_xml

            library = read_rekordbox_xml(Path(source.path))
        case RekordboxDb():
            from .rekordbox.db import read_rekordbox_db

            library = read_rekordbox_db(Path(source.path), progress, read_grids=file_tags)
        case RekordboxUsb():
            from .rekordbox.usb import read_rekordbox_usb

            library = read_rekordbox_usb(Path(source.path), progress)
        case Serato():
            from .serato.library import SeratoReadOptions, read_serato

            library = read_serato(
                Path(source.path),
                SeratoReadOptions(source.volume_root, file_tags),
                resolve,
                progress,
            )
        case _:
            raise TypeError(f"not a library source: {source!r}")
    for track in library.tracks.values():
        track.grid = gridlib.simplify(track.grid)
        track.file = resolve(track.location)
    return library


# --- write ---------------------------------------------------------------------------


def _backup(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.cratelib-{stamp}")
    if path.is_dir():
        shutil.copytree(path, backup)
    else:
        shutil.copy2(path, backup)
    return backup


def _refuse_second_library(folder: Path, target: Source) -> None:
    """A drive holds one DJ library: a second one next to it can break the first
    program's reading of it. Convert the drive instead (cratemover does)."""
    from .devices import _libraries

    if not folder.is_dir():
        return
    others = sorted({lib["format"] for lib in _libraries(folder)} - {target.format})
    if others:
        raise ValueError(
            f"{folder} already has a {', '.join(others)} library; "
            "putting a second library on a drive can break the first."
        )


def _stick_xml(root: Path, options: RekordboxUsbOptions, progress: Progress) -> Path:
    """``rekordbox.xml`` at the stick's root, describing the stick's own tracks.

    XML needs absolute paths, so they are written as the rekordbox computer sees
    the drive (``options.xml_root``).
    """
    from .paths import apply_rules
    from .rekordbox.usb import find_stick_root, read_rekordbox_usb
    from .rekordbox.xml import write_rekordbox_xml

    progress("Writing rekordbox.xml for importing into rekordbox")
    stick_root = find_stick_root(root)
    stick = read_rekordbox_usb(stick_root, progress)
    seen_as = options.xml_root.strip() or str(stick_root)
    for track in stick.tracks.values():
        track.location = apply_rules(track.location, [(str(stick_root), seen_as)])
    target = stick_root / "rekordbox.xml"
    write_rekordbox_xml(stick, target, RekordboxWriteOptions(key_notation=KeyNotation.CAMELOT))
    return target


def _place_on_drive(library: Library, root: Path, warnings: list[str]) -> None:
    """Copy tracks that aren't on the drive at ``root`` onto it (Serato USB sticks)."""
    from .rekordbox.usb import _copy_to_stick, _stick_path

    copied = 0
    for track in library.tracks.values():
        if track.file is None or _stick_path(track.file, root) is not None:
            continue
        stick_path, was_copied = _copy_to_stick(track.file, track, root)
        track.location = str(root) + stick_path
        track.file = Path(str(root) + stick_path)
        copied += was_copied
    if copied:
        warnings.append(f"Copied {copied} track(s) onto {root}.")


def write(
    library: Library,
    target: Source,
    options: WriteOptions | None = None,
    paths: PathMap | None = None,
    *,
    progress: Progress = _quiet,
) -> WriteResult:
    """Write a whole library to ``target``.

    A new library is created if there isn't one there. An existing one is updated
    in place: its previous files are kept next to it as ``*.cratelib-<time>``.
    Tracks whose ``file`` is known get their location from ``paths`` (how the
    target's program sees this machine); the rest keep the location they have.
    ``library`` itself is not changed. To edit Rekordbox's own library, use
    :func:`open`.
    """
    paths = paths or PathMap()
    library = copy.deepcopy(library)
    for track in library.tracks.values():
        if track.file is not None:
            track.location = paths.to_app(track.file)
    files_by_location = {t.location: t.file for t in library.tracks.values()}

    def resolve(location: str) -> Path | None:
        if (found := files_by_location.get(location)) is not None:
            return found
        path = Path(location)
        return path if path.is_file() else None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(target.path)
    result = WriteResult(warnings=list(library.warnings))
    match target:
        case RekordboxXml():
            options = options or RekordboxXmlOptions()
            assert isinstance(options, RekordboxWriteOptions)
            from .rekordbox.xml import write_rekordbox_xml

            xml = out if out.suffix.lower() == ".xml" else out / "rekordbox.xml"
            xml.parent.mkdir(parents=True, exist_ok=True)
            if backup := _backup(xml, stamp):
                result.warnings.append(f"Backed up the previous file to {backup.name}.")
            rb = write_rekordbox_xml(library, xml, options)
            result.files += rb.files
            result.warnings += rb.warnings

        case RekordboxUsb():
            options = options or RekordboxUsbOptions()
            assert isinstance(options, RekordboxUsbOptions)
            from .rekordbox.usb import UsbWriteOptions, write_rekordbox_usb

            _refuse_second_library(out, target)
            usb = write_rekordbox_usb(
                library,
                out,
                UsbWriteOptions(
                    copy_missing=options.copy_missing,
                    waveforms=options.waveforms,
                    device_name=options.device_name,
                    transcode=options.transcode,
                    onelibrary=options.onelibrary,
                ),
                resolve,
                progress,
            )
            result.files += usb.files
            result.warnings.insert(
                0,
                f"{usb.tracks} track(s) on the stick: {usb.copied} copied, "
                f"{usb.transcoded} converted to MP3; waveforms {usb.reused} reused, "
                f"{usb.measured} measured, {usb.placeholders} placeholders.",
            )
            result.warnings += usb.warnings
            if options.xml:
                result.files.append(_stick_xml(out, options, progress))

        case Serato():
            options = options or SeratoOptions()
            assert isinstance(options, SeratoOptions)
            from .serato.library import (
                CRATE_DIR,
                DATABASE_FILE,
                SeratoWriteOptions,
                find_serato_dir,
                write_serato,
            )

            try:
                serato_dir = find_serato_dir(out)
            except FileNotFoundError:
                serato_dir = out if out.name == "_Serato_" else out / "_Serato_"
            base = serato_dir / DATABASE_FILE
            if base.is_file():
                for path in (base, serato_dir / CRATE_DIR):
                    if backup := _backup(path, stamp):
                        result.warnings.append(f"Backed up {path.name} to {backup.name}.")
            else:
                base = options.base
            volume_root = target.volume_root or "/"
            if volume_root != "/" and options.copy_missing:
                _refuse_second_library(Path(volume_root), target)
                _place_on_drive(library, Path(volume_root), result.warnings)
                files_by_location.update({t.location: t.file for t in library.tracks.values()})
            se = write_serato(
                library,
                serato_dir.parent,
                SeratoWriteOptions(
                    volume_root=volume_root,
                    key_notation=options.key_notation,
                    write_file_tags=options.file_tags,
                    max_hot_cues=options.max_hot_cues,
                    memory_cues_to_hot_cues=options.memory_cues_to_hot_cues,
                    base_database=base,
                ),
                resolve,
                progress,
            )
            result.files += se.files
            result.warnings += se.warnings
            if options.file_tags:
                result.warnings.insert(
                    0, f"Serato tags written to {se.tags_written} audio file(s)."
                )

        case Mixxx():
            options = options or MixxxOptions()
            assert isinstance(options, MixxxOptions)
            from .mixxx import MixxxWriteOptions, write_mixxx

            db_path = out if out.suffix == ".sqlite" else out / "mixxxdb.sqlite"
            mixxx_options = MixxxWriteOptions(
                mp3_decoder=target.mp3_decoder,
                base_database=options.base,
                playlists_as_crates=options.playlists_as_crates,
                overwrite_existing=options.overwrite_existing,
                memory_cues_to_hot_cues=options.memory_cues_to_hot_cues,
                key_notation=options.key_notation,
            )
            if db_path.is_file():
                if backup := _backup(db_path, stamp):
                    result.warnings.append(f"Backed up {db_path.name} to {backup.name}.")
                mixxx_options.base_database = db_path
                mixxx_options.replace_playlists = True
                # Build the new database next to the old one, then swap it in.
                with tempfile.TemporaryDirectory(dir=db_path.parent) as tmp:
                    mx = write_mixxx(library, Path(tmp), mixxx_options, resolve, progress)
                    os.replace(Path(tmp) / "mixxxdb.sqlite", db_path)
                mx.files = [db_path]
            else:
                mx = write_mixxx(library, db_path.parent, mixxx_options, resolve, progress)
            result.files += mx.files
            result.warnings += mx.warnings

        case RekordboxDb():
            raise ValueError(
                "write() can't replace Rekordbox's own library; use open() to edit it in place"
            )
        case _:
            raise TypeError(f"not a library target: {target!r}")

    missing = sum(1 for t in library.tracks.values() if resolve(t.location) is None)
    if missing and not isinstance(target, RekordboxXml):
        result.warnings.append(
            f"{missing} of {len(library.tracks)} track file(s) were not found on this machine."
        )
    result.summary = library.summary()
    return result


# --- edit ----------------------------------------------------------------------------


def open(source: Source, paths: PathMap | None = None) -> Editor:
    """Open a library to change it in place: find and add tracks, set playlists.

    Use it as a context manager; nothing is written until :meth:`Editor.save`.
    Only Rekordbox's own library (``master.db``) can be opened so far.
    """
    paths = paths or PathMap()
    match source:
        case RekordboxDb():
            from .rekordbox.edit import RekordboxEditor

            return RekordboxEditor(Path(source.path), paths)
    raise NotImplementedError(f"editing {source.format} libraries in place isn't supported yet")
