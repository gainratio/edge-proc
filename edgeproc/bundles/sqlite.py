"""Canonical SQLite builder helper for Gear-CDC content-addressed bundle sync.

SQLite databases mutated in place accumulate freelist pages, out-of-order B-tree leaf
allocations, and WAL/journal sidecars that shift byte offsets across the file. Running
``PRAGMA journal_mode = DELETE; PRAGMA page_size = 4096; VACUUM;`` rewrites every table
and index sequentially in primary-key order with zero freelist slack. When chunked with
:class:`~edgeproc.bundles.chunking.GearCDC`, a localized row update perturbs only the
affected 64 KiB chunk spans and reuses ~95%+ of existing chunks in ``sync_index``.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Final

SQLITE_HEADER: Final = b"SQLite format 3\x00"
CANONICAL_PAGE_SIZE: Final = 4096


def is_sqlite_bytes(data: bytes) -> bool:
    """True iff ``data`` begins with the 16-byte SQLite format 3 magic header."""
    return len(data) >= len(SQLITE_HEADER) and data[: len(SQLITE_HEADER)] == SQLITE_HEADER


def canonicalize_sqlite_bytes(data: bytes) -> bytes:
    """Validate and ``VACUUM`` a serialized SQLite database into canonical 4096-byte pages.

    Refuses non-SQLite payloads and databases that fail ``PRAGMA integrity_check(10)``
    before rewriting. Returns the compacted byte image ready for :func:`build_bundle`.
    """
    _require_sqlite_header(data)
    with tempfile.TemporaryDirectory(prefix="edgeproc-sqlite-") as tmp_dir:
        db_path = Path(tmp_dir) / "canonical.sqlite3"
        db_path.write_bytes(data)
        _vacuum_in_place(db_path)
        return db_path.read_bytes()


def canonicalize_sqlite(path: Path) -> bytes:
    """Canonicalize ``path`` in place and return its compacted SQLite bytes."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"SQLite target must be a regular file, not a symlink: {path}")
    canonical = canonicalize_sqlite_bytes(path.read_bytes())
    path.write_bytes(canonical)
    return canonical


def _require_sqlite_header(data: bytes) -> None:
    if not is_sqlite_bytes(data):
        raise ValueError("not a SQLite format 3 database (missing 16-byte magic header)")


def _vacuum_in_place(db_path: Path) -> None:
    try:
        conn = sqlite3.connect(db_path, isolation_level=None)
        try:
            _verify_and_compact(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"corrupt SQLite database: {exc}") from exc


def _verify_and_compact(conn: sqlite3.Connection) -> None:
    verdict = conn.execute("PRAGMA integrity_check(10)").fetchall()
    if verdict != [("ok",)]:
        raise ValueError(f"SQLite integrity_check failed: {verdict!r}")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute(f"PRAGMA page_size = {CANONICAL_PAGE_SIZE}")
    conn.execute("VACUUM")
