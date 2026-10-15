"""Canonical SQLite compaction + Gear-CDC chunk reuse across versions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from edgeproc.bundles.adapters import FilesystemAdapter
from edgeproc.bundles.cas import FilesystemCacheStore
from edgeproc.bundles.chunking import GearCDC
from edgeproc.bundles.manifest import IndexManifest
from edgeproc.bundles.publish import build_bundle
from edgeproc.bundles.signing import Ed25519Signer, Ed25519Verifier, generate_keypair
from edgeproc.bundles.sqlite import (
    CANONICAL_PAGE_SIZE,
    SQLITE_HEADER,
    canonicalize_sqlite,
    canonicalize_sqlite_bytes,
    is_sqlite_bytes,
)
from edgeproc.bundles.sync import materialize_file, sync_index


def _build_sample_db(path: Path, *, rows: int = 3_000, updated_row: int | None = None) -> bytes:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA page_size = 1024")
        conn.execute(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, sku TEXT NOT NULL, body TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO items(id, sku, body) VALUES (?, ?, ?)",
            [(i, f"SKU-{i:06d}", f"payload-{i:06d}-" + ("x" * 180)) for i in range(rows)],
        )
        # Create and delete scratch rows to leave freelist fragmentation before canonicalization.
        conn.executemany(
            "INSERT INTO items(id, sku, body) VALUES (?, ?, ?)",
            [(rows + i, f"TMP-{i:06d}", "temp-" * 40) for i in range(200)],
        )
        conn.execute("DELETE FROM items WHERE id >= ?", (rows,))
        if updated_row is not None:
            conn.execute(
                "UPDATE items SET body = ? WHERE id = ?",
                ("updated-payload-" + ("y" * 180), updated_row),
            )
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def test_is_sqlite_bytes_checks_magic_header() -> None:
    assert is_sqlite_bytes(SQLITE_HEADER + b"rest")
    assert not is_sqlite_bytes(b"short")
    assert not is_sqlite_bytes(b"not a sqlite header at all")


def test_canonicalize_sqlite_enforces_page_size_and_eliminates_freelist(tmp_path: Path) -> None:
    db_path = tmp_path / "fragmented.sqlite3"
    raw = _build_sample_db(db_path, rows=600)
    canonical = canonicalize_sqlite(db_path)

    assert len(canonical) < len(raw)
    assert db_path.read_bytes() == canonical

    conn = sqlite3.connect(db_path)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        count = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    finally:
        conn.close()

    assert page_size == CANONICAL_PAGE_SIZE
    assert freelist == 0
    assert count == 600


def test_canonicalize_sqlite_refuses_non_sqlite_and_symlinks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a SQLite format 3"):
        canonicalize_sqlite_bytes(b"not-sqlite")

    real_file = tmp_path / "real.sqlite3"
    real_file.write_bytes(b"not-sqlite")
    link = tmp_path / "link.sqlite3"
    link.symlink_to(real_file)

    with pytest.raises(ValueError, match="regular file, not a symlink"):
        canonicalize_sqlite(link)
    with pytest.raises(ValueError, match="regular file, not a symlink"):
        canonicalize_sqlite(tmp_path / "missing.sqlite3")


def test_canonicalize_sqlite_refuses_corrupt_btree_and_failed_integrity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corrupt = SQLITE_HEADER + (b"\xff" * 4080)
    with pytest.raises(ValueError, match="corrupt SQLite database"):
        canonicalize_sqlite_bytes(corrupt)

    db_path = tmp_path / "valid.sqlite3"
    valid_bytes = _build_sample_db(db_path, rows=20)
    real_connect = sqlite3.connect

    class _BadIntegrityConn:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def execute(self, sql: str) -> sqlite3.Cursor:
            if "integrity_check" in sql:
                return self._inner.execute("SELECT 'row 1 missing from index'")
            return self._inner.execute(sql)

        def close(self) -> None:
            self._inner.close()

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: _BadIntegrityConn(real_connect(*args, **kwargs)),
    )
    with pytest.raises(ValueError, match="integrity_check failed"):
        canonicalize_sqlite_bytes(valid_bytes)


def test_canonical_sqlite_update_reuses_majority_of_gear_cdc_chunks(tmp_path: Path) -> None:
    v1_raw = _build_sample_db(tmp_path / "v1.sqlite3", rows=3_000)
    v2_raw = _build_sample_db(tmp_path / "v2.sqlite3", rows=3_000, updated_row=1_500)
    v1_bytes = canonicalize_sqlite_bytes(v1_raw)
    v2_bytes = canonicalize_sqlite_bytes(v2_raw)

    private, public = generate_keypair()
    signer = Ed25519Signer(private)
    verifier = Ed25519Verifier.from_public_bytes(public.public_bytes_raw())
    origin = tmp_path / "origin"
    cache = FilesystemCacheStore(tmp_path / "cache")

    build_bundle(
        files={"catalog.sqlite3": v1_bytes},
        store=FilesystemCacheStore(origin),
        chunker=GearCDC(),
        signer=signer,
        bundle_id="catalog",
        version="1.0.0",
    )
    first = sync_index(
        base_url=str(origin),
        store=cache,
        adapter=FilesystemAdapter(),
        verifier=verifier,
    )
    assert first.chunks_fetched >= 4
    assert first.chunks_reused == 0

    pointer_v2 = build_bundle(
        files={"catalog.sqlite3": v2_bytes},
        store=FilesystemCacheStore(origin),
        chunker=GearCDC(),
        signer=signer,
        bundle_id="catalog",
        version="1.0.1",
    )
    second = sync_index(
        base_url=str(origin),
        store=cache,
        adapter=FilesystemAdapter(),
        verifier=verifier,
    )
    assert second.chunks_reused > second.chunks_fetched
    assert second.bytes_fetched < len(v2_bytes) // 2

    manifest_v2 = IndexManifest.model_validate_json(
        (origin / "manifest" / pointer_v2.manifest_hash).read_bytes()
    )
    assert materialize_file(cache, manifest_v2, "catalog.sqlite3") == v2_bytes
