from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import FIXTURES, needs_ffmpeg

pytest.importorskip("pyrekordbox")

import cratelib
from cratelib.rekordbox import edit as rb_edit

MASTER = FIXTURES / "rekordbox" / "master"


@pytest.fixture
def rekordbox_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of pyrekordbox's test library (rekordbox 6.6.2: two demo tracks, four samples)."""
    monkeypatch.setattr(rb_edit, "is_running", lambda: False)
    target = tmp_path / "rekordbox"
    shutil.copytree(MASTER, target)
    return target


@pytest.fixture
def music(tmp_path: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    folder = tmp_path / "music"
    folder.mkdir()
    for name, title in (("a.mp3", "Alpha"), ("b.mp3", "Bravo")):
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2",
             "-metadata", f"title={title}", "-metadata", "artist=Tester", "-q:a", "5",
             str(folder / name)],
            check=True,
        )  # fmt: skip
    return folder


def _paths(music: Path) -> cratelib.PathMap:
    return cratelib.PathMap({str(music): "C:/users/dj/Music"})


@needs_ffmpeg
def test_add_track_stores_the_path_rekordbox_sees(rekordbox_dir: Path, music: Path) -> None:
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir), _paths(music)) as rb:
        track = rb.add_track(music / "a.mp3")
        assert track.location == "C:/users/dj/Music/a.mp3"
        assert (track.title, track.artist) == ("Alpha", "Tester")
        assert track.file == music / "a.mp3"
        assert rb.track_at(music / "a.mp3") == track
        with pytest.raises(ValueError, match="already"):
            rb.add_track(music / "a.mp3")


@needs_ffmpeg
def test_set_playlist_creates_folders_and_is_idempotent(rekordbox_dir: Path, music: Path) -> None:
    paths = _paths(music)
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir), paths) as rb:
        a, b = rb.add_track(music / "a.mp3"), rb.add_track(music / "b.mp3")
        assert rb.set_playlist(["SoundCloud", "ukg"], [a, b]) is True
        assert rb.set_playlist(["SoundCloud", "ukg"], [a, b]) is False
        rb.save(backup=False)

    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir), paths) as rb:
        assert [t.title for t in rb.playlist(["SoundCloud", "ukg"]) or []] == ["Alpha", "Bravo"]
        assert rb.set_playlist(["SoundCloud", "ukg"], [b.id, a.id]) is True  # ids work too
        rb.save(backup=False)

    library = cratelib.read(cratelib.RekordboxDb(rekordbox_dir), paths, file_tags=False)
    [(parents, ukg)] = [(p, n) for p, n in library.playlists.walk() if n.name == "ukg"]
    assert parents == ("SoundCloud",)
    assert [library.tracks[t].title for t in ukg.track_ids or []] == ["Bravo", "Alpha"]
    assert library.tracks[a.id].file == music / "a.mp3"
    # pyrekordbox keeps rekordbox's playlist file in step with the database.
    assert 'Attribute="1"' in (rekordbox_dir / "masterPlaylists6.xml").read_text()


@needs_ffmpeg
def test_unsaved_changes_are_dropped(rekordbox_dir: Path, music: Path) -> None:
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir), _paths(music)) as rb:
        rb.set_playlist(["Gone"], [rb.add_track(music / "a.mp3")])
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir), _paths(music)) as rb:
        assert rb.playlist(["Gone"]) is None
        assert rb.track_at(music / "a.mp3") is None


def test_delete_playlist(rekordbox_dir: Path) -> None:
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir)) as rb:
        demo = [t for t in rb.tracks() if t.title.startswith("Demo")]
        rb.set_playlist(["Sets", "Friday"], demo)
        assert rb.delete_playlist(["Sets", "Friday"]) is True
        assert rb.delete_playlist(["Sets", "Friday"]) is False
        assert rb.playlist(["Sets", "Friday"]) is None


def test_playlist_inside_a_playlist_is_refused(rekordbox_dir: Path) -> None:
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir)) as rb:
        rb.set_playlist(["Plain"], [])
        with pytest.raises(ValueError, match="not a folder"):
            rb.set_playlist(["Plain", "Child"], [])


def test_save_backs_up_first(rekordbox_dir: Path) -> None:
    before = (rekordbox_dir / "master.db").read_bytes()
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir)) as rb:
        rb.set_playlist(["New"], [])
        backup = rb.save()
    assert backup is not None and backup.parent == rekordbox_dir
    assert (backup / "master.db").read_bytes() == before
    assert (backup / "masterPlaylists6.xml").is_file()


def test_save_refuses_while_rekordbox_runs(
    rekordbox_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rb_edit, "is_running", lambda: True)
    before = (rekordbox_dir / "master.db").read_bytes()
    with cratelib.open(cratelib.RekordboxDb(rekordbox_dir)) as rb:
        rb.set_playlist(["New"], [])
        with pytest.raises(rb_edit.RekordboxRunning):
            rb.save()
    assert (rekordbox_dir / "master.db").read_bytes() == before
    assert not list(rekordbox_dir.glob("*.cratelib-*"))


def test_write_refuses_rekordbox_db(rekordbox_dir: Path) -> None:
    with pytest.raises(ValueError, match="open"):
        cratelib.write(cratelib.Library("empty"), cratelib.RekordboxDb(rekordbox_dir))


def test_read_sees_changes_still_in_the_write_ahead_log(rekordbox_dir: Path) -> None:
    rb = cratelib.open(cratelib.RekordboxDb(rekordbox_dir))
    try:
        rb.set_playlist(["Fresh"], [])
        rb.save(backup=False)
        # The editor's connection is still open, so the change is only in master.db-wal.
        assert (rekordbox_dir / "master.db-wal").stat().st_size > 0
        library = cratelib.read(cratelib.RekordboxDb(rekordbox_dir), file_tags=False)
        assert "Fresh" in library.playlist_paths()
    finally:
        rb.close()
