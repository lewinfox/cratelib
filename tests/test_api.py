from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import cratelib
from cratelib import PathMap


def test_pathmap_both_ways(tmp_path: Path) -> None:
    paths = PathMap({"/home/me/Music": "C:/users/dj/Music"})
    assert paths.to_app("/home/me/Music/house/a.mp3") == "C:/users/dj/Music/house/a.mp3"
    assert paths.to_local("C:/users/dj/Music/house/a.mp3") == Path("/home/me/Music/house/a.mp3")
    # Windows paths match whatever their case and slash direction.
    assert paths.to_local("c:\\Users\\DJ\\Music\\a.mp3") == Path("/home/me/Music/a.mp3")
    # Paths nothing covers are the same on both sides.
    assert paths.to_app("/srv/x.mp3") == "/srv/x.mp3"


@pytest.mark.skipif(os.name == "nt", reason="a Windows path is local there")
def test_pathmap_unmapped_windows_path_is_not_local() -> None:
    assert PathMap().to_local("D:/Music/a.mp3") is None
    assert PathMap().find("D:/Music/a.mp3") is None


def test_pathmap_find_and_infer(tmp_path: Path) -> None:
    (tmp_path / "house").mkdir()
    (tmp_path / "house" / "a.mp3").write_bytes(b"")
    paths = PathMap.infer(["C:/users/dj/Music/house/a.mp3"], [tmp_path])
    assert paths == PathMap({str(tmp_path): "C:/users/dj/Music"})
    assert paths.find("C:/users/dj/Music/house/a.mp3") == tmp_path / "house" / "a.mp3"
    assert paths.find("C:/users/dj/Music/house/missing.mp3") is None


def test_write_uses_the_targets_pathmap(tmp_path: Path) -> None:
    song = tmp_path / "music" / "a.mp3"
    song.parent.mkdir()
    song.write_bytes(b"")
    library = cratelib.Library("test")
    library.add_track(cratelib.Track("1", "/elsewhere/a.mp3", title="A", file=song))
    library.add_track(cratelib.Track("2", "/elsewhere/b.mp3", title="B"))  # no local file
    library.playlists.children.append(cratelib.Playlist("P", ["1", "2"]))
    xml = tmp_path / "out.xml"
    cratelib.write(
        library, cratelib.RekordboxXml(xml), paths=PathMap({str(tmp_path / "music"): "E:/DJ"})
    )
    back = {t.title: t.location for t in cratelib.read(cratelib.RekordboxXml(xml)).tracks.values()}
    assert back == {"A": "E:/DJ/a.mp3", "B": "/elsewhere/b.mp3"}
    assert library.tracks["1"].location == "/elsewhere/a.mp3"  # the input is left alone


def test_write_backs_up_an_existing_file(tmp_path: Path) -> None:
    xml = tmp_path / "rekordbox.xml"
    cratelib.write(cratelib.Library("one"), cratelib.RekordboxXml(xml))
    result = cratelib.write(cratelib.Library("two"), cratelib.RekordboxXml(xml))
    assert [p.name.split(".cratelib-")[0] for p in tmp_path.glob("*.cratelib-*")] == [
        "rekordbox.xml"
    ]
    assert any("Backed up" in w for w in result.warnings)


def test_detect_returns_sources(tmp_path: Path) -> None:
    (tmp_path / "_Serato_").mkdir()
    (tmp_path / "_Serato_" / "database V2").write_bytes(b"")
    (tmp_path / "master.db").write_bytes(b"")
    found = cratelib.detect(tmp_path)
    assert cratelib.Serato(tmp_path / "_Serato_", "/") in found
    assert cratelib.RekordboxDb(tmp_path / "master.db") in found


def test_select_keeps_named_playlists_and_their_tracks() -> None:
    library = cratelib.Library("test")
    for tid in "abc":
        library.add_track(cratelib.Track(tid, f"/{tid}.mp3"))
    folder = cratelib.Playlist.folder("F", [cratelib.Playlist("One", ["a", "b"])])
    library.playlists.children += [folder, cratelib.Playlist("Two", ["c"])]
    assert library.playlist_paths() == ["F / One", "Two"]
    picked = library.select(["F / One"])
    assert sorted(picked.tracks) == ["a", "b"] and picked.playlist_paths() == ["F / One"]
    assert library.select([]) is library


def test_installed_finds_xdg_places_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    home = tmp_path / "home"
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    (home / ".config").mkdir(parents=True)
    (home / ".config/user-dirs.dirs").write_text('XDG_MUSIC_DIR="$HOME/Musik"\n')
    rekordbox = data / "rekordbox-wine/prefix/drive_c/users/dj/AppData/Roaming/Pioneer/rekordbox"
    rekordbox.mkdir(parents=True)
    (rekordbox / "master.db").write_bytes(b"")
    (home / "Musik/_Serato_").mkdir(parents=True)
    (home / "Musik/_Serato_/database V2").write_bytes(b"")
    (data / "mixxx").mkdir()
    (data / "mixxx/mixxxdb.sqlite").write_bytes(b"")

    assert cratelib.installed(home) == [
        cratelib.RekordboxDb(rekordbox / "master.db"),
        cratelib.Serato(home / "Musik/_Serato_", "C:/"),
        cratelib.Mixxx(data / "mixxx/mixxxdb.sqlite"),
    ]


def test_installed_falls_back_to_default_folders_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    rekordbox = tmp_path / ".wine/drive_c/users/me/AppData/Roaming/Pioneer/rekordbox"
    rekordbox.mkdir(parents=True)
    (rekordbox / "master.db").write_bytes(b"")
    (tmp_path / "Music/_Serato_").mkdir(parents=True)
    (tmp_path / "Music/_Serato_/database V2").write_bytes(b"")
    (tmp_path / ".mixxx").mkdir()
    (tmp_path / ".mixxx/mixxxdb.sqlite").write_bytes(b"")

    assert cratelib.installed(tmp_path) == [
        cratelib.RekordboxDb(rekordbox / "master.db"),
        cratelib.Serato(tmp_path / "Music/_Serato_", "C:/"),
        cratelib.Mixxx(tmp_path / ".mixxx/mixxxdb.sqlite"),
    ]
