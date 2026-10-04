"""The public SQLCipher key of OneLibrary databases on USB sticks.

pyrekordbox opens ``master.db`` itself; its 0.4.4 release doesn't know OneLibrary's
key yet, so that one is kept here in the same obfuscated form, decoded with
pyrekordbox's ``deobfuscate``. The key is from pyrekordbox's ``devicelib_plus``
(MIT, Copyright (c) 2022-2025 Dylan Jones).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyrekordbox.utils import deobfuscate
from sqlcipher3 import dbapi2 as sqlcipher

# OneLibrary (Device Library Plus): PIONEER/rekordbox/exportLibrary.db on sticks.
ONE_LIBRARY = (
    b"PN_1dH8$oLJY)16j_RvM6qphWw`476>;C1cWmI#se(PG`j}~xAjlufj?`#0i{;=glh(SkW)y0>n?YEiD`l%t("
)


def open_encrypted(path: Path, blob: bytes) -> Any:
    """Open an SQLCipher 4 database (default settings) with one of the keys above."""
    conn = sqlcipher.connect(str(path))
    conn.execute(f"PRAGMA key = '{deobfuscate(blob)}'")
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()  # fails if the key is wrong
    return conn
