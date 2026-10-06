"""What every editable library does the same way: Rekordbox's master.db, Serato and Mixxx."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import FIXTURES

import cratelib
from cratelib import mixxx_edit
from cratelib.rekordbox import edit as rekordbox_edit
from cratelib.serato import edit as serato_edit

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

KINDS = ["rekordbox", "serato", "mixxx"]
RUNNING = {
    "rekordbox": (rekordbox_edit, "is_running", rekordbox_edit.RekordboxRunning),
    "serato": (serato_edit, "serato_running", serato_edit.SeratoRunning),
    "mixxx": (mixxx_edit, "mixxx_running", mixxx_edit.MixxxRunning),
}


@pytest.fixture
def music(tmp_path: Path) -> Path:
    folder = tmp_path / "music"
    folder.mkdir()
    for name, title in (("a.mp3", "Alpha"), ("b.mp3", "Bravo"), ("c.mp3", "Charlie")):
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2",
             "-metadata", f"title={title}", "-metadata", "artist=Tester", "-q:a", "5",
             str(folder / name)],
            check=True,
        )  # fmt: skip
    return folder


@pytest.fixture(params=KINDS)
def library(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, cratelib.Source]:
    """An empty-ish library of each kind, with its program not running."""
    kind: str = request.param
    module, name, _ = RUNNING[kind]
    monkeypatch.setattr(module, name, lambda: False)
    if kind == "rekordbox":
        shutil.copytree(FIXTURES / "rekordbox" / "master", tmp_path / "rekordbox")
        return kind, cratelib.RekordboxDb(tmp_path / "rekordbox")
    if kind == "serato":
        # Serato under Wine stores paths relative to C:/.
        source: cratelib.Source = cratelib.Serato(tmp_path / "_Serato_", "C:/")
    else:
        source = cratelib.Mixxx(tmp_path / "mixxx")
    cratelib.write(cratelib.Library("empty"), source)
    return kind, source


Open = Callable[[], cratelib.Editor]


def _opener(source: cratelib.Source, music: Path) -> Open:
    paths = cratelib.PathMap({str(music): "C:/users/dj/Music"})
    return lambda: cratelib.open(source, paths)


def test_add_track_and_find_it_again(library: tuple[str, cratelib.Source], music: Path) -> None:
    _, source = library
    open_ = _opener(source, music)
    with open_() as lib:
        track = lib.add_track(music / "a.mp3")
        assert (track.title, track.artist) == ("Alpha", "Tester")
        assert track.location == "C:/users/dj/Music/a.mp3"
        assert lib.track_at(music / "a.mp3") == track
        with pytest.raises(ValueError, match="already"):
            lib.add_track(music / "a.mp3")
        lib.save(backup=False)
    with open_() as lib:
        found = lib.track_at(music / "a.mp3")
        assert found is not None and found.id == track.id and found.file == music / "a.mp3"


def test_set_playlist_round_trips_through_read(
    library: tuple[str, cratelib.Source], music: Path
) -> None:
    _, source = library
    open_ = _opener(source, music)
    with open_() as lib:
        a, b, c = (lib.add_track(music / n) for n in ("a.mp3", "b.mp3", "c.mp3"))
        assert lib.set_playlist(["SoundCloud", "ukg"], [c, a, b]) is True
        assert lib.set_playlist(["SoundCloud", "ukg"], [c, a, b]) is False
        lib.save(backup=False)
    with open_() as lib:
        assert [t.title for t in lib.playlist(["SoundCloud", "ukg"]) or []] == [
            "Charlie",
            "Alpha",
            "Bravo",
        ]
        assert lib.set_playlist(["SoundCloud", "ukg"], [c, a, b]) is False  # still the same
        assert lib.set_playlist(["SoundCloud", "ukg"], [b.id]) is True
        lib.save(backup=False)

    library_read = cratelib.read(source, cratelib.PathMap({str(music): "C:/users/dj/Music"}))
    expected = "SoundCloud / ukg"
    assert expected in library_read.playlist_paths()
    [ukg] = [p for parents, p in library_read.playlists.walk() if p.name.endswith("ukg")]
    assert [library_read.tracks[t].title for t in ukg.track_ids or []] == ["Bravo"]
    assert library_read.tracks[b.id].file == music / "b.mp3"


def test_unsaved_changes_are_dropped(library: tuple[str, cratelib.Source], music: Path) -> None:
    _, source = library
    open_ = _opener(source, music)
    with open_() as lib:
        lib.set_playlist(["Gone"], [lib.add_track(music / "a.mp3")])
    with open_() as lib:
        assert lib.playlist(["Gone"]) is None
        assert lib.track_at(music / "a.mp3") is None


def test_delete_playlist_and_folder(library: tuple[str, cratelib.Source], music: Path) -> None:
    _, source = library
    with _opener(source, music)() as lib:
        a = lib.add_track(music / "a.mp3")
        lib.set_playlist(["Sets", "Friday"], [a])
        lib.set_playlist(["Sets", "Saturday"], [a])
        assert lib.delete_playlist(["Sets", "Friday"]) is True
        assert lib.playlist(["Sets", "Friday"]) is None
        assert lib.playlist(["Sets", "Saturday"]) is not None
        assert lib.delete_playlist(["Sets"]) is True
        assert lib.playlist(["Sets", "Saturday"]) is None
        assert lib.delete_playlist(["Sets"]) is False


def test_save_backs_up_then_writes(library: tuple[str, cratelib.Source], music: Path) -> None:
    _, source = library
    with _opener(source, music)() as lib:
        lib.set_playlist(["New"], [lib.add_track(music / "a.mp3")])
        backup = lib.save()
    assert backup is not None and backup.exists() and ".cratelib-" in backup.name
    assert "New" in cratelib.read(source, file_tags=False).playlist_paths()


def test_save_refuses_while_the_program_runs(
    library: tuple[str, cratelib.Source], music: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kind, source = library
    module, name, error = RUNNING[kind]
    monkeypatch.setattr(module, name, lambda: True)
    with _opener(source, music)() as lib:
        lib.set_playlist(["New"], [])
        with pytest.raises(error):
            lib.save()
    assert "New" not in cratelib.read(source, file_tags=False).playlist_paths()


def test_xml_and_usb_cant_be_opened(tmp_path: Path) -> None:
    for source in (cratelib.RekordboxXml(tmp_path / "a.xml"), cratelib.RekordboxUsb(tmp_path)):
        with pytest.raises(ValueError, match="write"):
            cratelib.open(source)


@pytest.mark.parametrize("keep_library_row", [True, False])
def test_mixxx_brings_back_a_removed_track(
    tmp_path: Path, music: Path, monkeypatch: pytest.MonkeyPatch, keep_library_row: bool
) -> None:
    # Mixxx keeps a removed track's rows, and stores each location only once.
    monkeypatch.setattr(mixxx_edit, "mixxx_running", lambda: False)
    source = cratelib.Mixxx(tmp_path / "mixxx")
    cratelib.write(cratelib.Library("empty"), source)
    open_ = _opener(source, music)
    with open_() as lib:
        old = lib.add_track(music / "a.mp3")
        lib.save(backup=False)
    db = sqlite3.connect(tmp_path / "mixxx" / "mixxxdb.sqlite")
    with db:
        if keep_library_row:  # removed from the library: marked, cues kept
            db.execute("UPDATE library SET mixxx_deleted = 1, bpm = 128")
        else:  # only the location is left
            db.execute("DELETE FROM library")
        db.execute("UPDATE track_locations SET fs_deleted = 1")
    with open_() as lib:
        assert lib.track_at(music / "a.mp3") is None
        track = lib.add_track(music / "a.mp3")
        assert (track.title, track.location) == ("Alpha", "C:/users/dj/Music/a.mp3")
        assert (track.id == old.id) == keep_library_row
        lib.set_playlist(["p"], [track])
        lib.save(backup=False)
    with open_() as lib:
        assert lib.playlist(["p"]) == [lib.track_at(music / "a.mp3")]
    assert db.execute("SELECT COUNT(*), MAX(fs_deleted) FROM track_locations").fetchone() == (1, 0)
    if keep_library_row:
        assert db.execute("SELECT bpm FROM library").fetchone() == (128,)
    db.close()
