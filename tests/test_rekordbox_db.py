from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES

pytest.importorskip("sqlcipher3")

from cratelib.rekordbox.db import grid_from_anlz, read_rekordbox_db

ANLZ = FIXTURES / "rekordbox/anlz/demo-track-1.DAT"


def test_grid_from_anlz() -> None:
    grid = grid_from_anlz(ANLZ)
    assert len(grid) == 1
    assert grid[0].bpm == 128 and grid[0].position_ms == pytest.approx(25) and grid[0].beat == 1


MASTER = FIXTURES / "rekordbox" / "master"


def _make_rekordbox_dir(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """pyrekordbox's test library, with cues, playlists and an analysis file added."""
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.db6 import database, tables

    monkeypatch.setattr(database, "get_rekordbox_pid", lambda: 0)
    shutil.copytree(MASTER, root, dirs_exist_ok=True)
    db = Rekordbox6Database(path=root / "master.db", db_dir=root)
    demo = db.get_content(Title="Demo Track 1").one()
    key = db.get_key(ScaleName="Fm").first()
    if key is None:
        key = tables.DjmdKey.create(ID="9001", ScaleName="Fm", Seq=9001)
        db.add(key)
    demo.Rating, demo.DJPlayCount, demo.ColorID, demo.KeyID = 4, "7", "5", key.ID
    for n, (start, end, kind, colour, comment, deleted) in enumerate(
        [
            (25, -1, 0, 0, "", 0),  # memory cue
            (15025, -1, 1, 3, "Drop", 0),  # hot cue A
            (30025, -1, 5, 0, "", 0),  # hot cue D (Kind skips 4)
            (45025, 46900, 2, 0, "Loop", 0),  # hot loop B
            (50000, -1, 3, 0, "gone", 1),  # deleted
        ],
        1,
    ):
        db.add(
            tables.DjmdCue.create(
                ID=str(9100 + n), ContentID=demo.ID, InMsec=start, OutMsec=end, Kind=kind,
                ColorTableIndex=colour, Comment=comment, rb_local_deleted=deleted,
            )
        )  # fmt: skip
    friday = db.create_playlist("Friday", parent=db.create_playlist_folder("Sets"))
    db.add_to_playlist(friday, demo)
    smart = db.create_playlist("Smart")
    smart.Attribute = 4  # an intelligent playlist, as far as reading goes
    analysis = str(demo.AnalysisDataPath)
    db.commit()
    db.close()
    db.engine.dispose()
    anlz_dir = root / "share" / analysis.strip("/").rsplit("/", 1)[0]
    anlz_dir.mkdir(parents=True)
    shutil.copy(ANLZ, anlz_dir / "ANLZ0000.DAT")
    return root


def test_read_master_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library = read_rekordbox_db(_make_rekordbox_dir(tmp_path, monkeypatch))
    track = next(t for t in library.tracks.values() if t.title == "Demo Track 1")
    assert (track.title, track.artist, track.bpm, track.rating, track.play_count) == (
        "Demo Track 1",
        "Loopmasters",
        128.0,
        4,
        7,
    )
    assert track.colour == 0x00FF00 and track.key is not None and track.key.minor
    assert track.location == "C:/Users/dylan/Music/PioneerDJ/Demo Tracks/Demo Track 1.mp3"
    cues = sorted((c.position_ms, c.slot, c.end_ms, c.name) for c in track.cues)
    assert cues == [
        (25, None, None, ""),
        (15025, 0, None, "Drop"),
        (30025, 3, None, ""),
        (45025, 1, 46900, "Loop"),
    ]
    assert [(p, n.name, n.track_ids) for p, n in library.playlists.walk()] == [
        ((), "Trial playlist - Cloud Library Sync", []),
        (("Sets",), "Friday", [track.id]),
    ]
    assert "1 intelligent playlist(s) not converted." in library.warnings
    assert len(track.grid) == 1 and track.grid[0].bpm == 128
