"""Tests for CI/CD prebuilt zstd ``--patch-from`` static CDN delta patching."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from edgeproc.bundles.adapters import FilesystemAdapter
from edgeproc.bundles.cas import FilesystemCacheStore, IntegrityError
from edgeproc.bundles.chunking import GearCDC
from edgeproc.bundles.manifest import ChunkRef, FileEntry, IndexManifest
from edgeproc.bundles.patching import (
    _window_log_for_span,
    apply_raw_patch,
    assemble_cached_base,
    build_raw_patch,
    ingest_patched_chunks,
    make_patch_ref,
    try_apply_entry_patch,
)
from edgeproc.bundles.publish import build_bundle
from edgeproc.bundles.signing import Ed25519Signer, Ed25519Verifier, generate_keypair
from edgeproc.bundles.sqlite import canonicalize_sqlite_bytes
from edgeproc.bundles.sync import materialize_file, sync_index


def _build_sqlite_version(
    path: Path, *, rows: int = 3_000, updated_step: int | None = None
) -> bytes:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE items(id INTEGER PRIMARY KEY, sku TEXT NOT NULL, body TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO items(id, sku, body) VALUES (?, ?, ?)",
            [(i, f"SKU-{i:06d}", f"payload-{i:06d}-" + ("a" * 180)) for i in range(rows)],
        )
        if updated_step is not None:
            conn.executemany(
                "UPDATE items SET body = ? WHERE id = ?",
                [(f"updated-{i:06d}-" + ("z" * 195), i) for i in range(0, rows, updated_step)],
            )
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def test_window_log_for_span_bounds() -> None:
    assert _window_log_for_span(100) == 10
    assert _window_log_for_span(1024) == 10
    assert _window_log_for_span(2048) == 11
    assert _window_log_for_span(1 << 30) == 27


def test_build_and_apply_raw_patch_roundtrip_and_fail_closed() -> None:
    base = b"SQLite format 3\x00" + bytes(range(256)) * 500
    target = bytearray(base)
    target[2048:2068] = b"PRICE_UPDATED_19.99!"
    target_bytes = bytes(target)
    target_sha = hashlib.sha256(target_bytes).hexdigest()

    patch = build_raw_patch(base, target_bytes)
    assert len(patch) < 120
    restored = apply_raw_patch(
        base, patch, expected_sha256=target_sha, expected_size=len(target_bytes)
    )
    assert restored == target_bytes

    with pytest.raises(IntegrityError, match="failed to decompress"):
        apply_raw_patch(
            base, b"not-zstd", expected_sha256=target_sha, expected_size=len(target_bytes)
        )
    with pytest.raises(IntegrityError, match="output length !="):
        apply_raw_patch(
            base, patch, expected_sha256=target_sha, expected_size=len(target_bytes) - 1
        )
    with pytest.raises(IntegrityError, match="target SHA-256"):
        apply_raw_patch(base, patch, expected_sha256="0" * 64, expected_size=len(target_bytes))


def test_make_patch_ref_skips_identical_empty_or_unprofitable_inputs() -> None:
    sample = b"hello-world-" * 200
    assert make_patch_ref(sample, sample) is None
    assert make_patch_ref(b"", sample) is None
    assert make_patch_ref(sample, b"") is None
    # Tiny uncompressible target where zstd frame overhead >= target length:
    assert make_patch_ref(b"a", b"b") is None


def test_assemble_cached_base_and_ingest_patched_chunks_guards(tmp_path: Path) -> None:
    store = FilesystemCacheStore(tmp_path / "cache")
    chunk_a = b"A" * 1024
    chunk_b = b"B" * 1024
    hash_a = store.put_chunk(chunk_a)
    hash_b = hashlib.sha256(chunk_b).hexdigest()

    entry_missing = FileEntry(
        path="file.bin",
        size=2048,
        file_sha256=hashlib.sha256(chunk_a + chunk_b).hexdigest(),
        chunks=[ChunkRef(hash=hash_a, size=1024), ChunkRef(hash=hash_b, size=1024)],
    )
    assert assemble_cached_base(entry_missing, store) is None

    store.put_chunk(chunk_b)
    assert assemble_cached_base(entry_missing, store) == chunk_a + chunk_b

    bad_entry = entry_missing.model_copy(update={"file_sha256": "f" * 64})
    with pytest.raises(IntegrityError, match="cached base file"):
        assemble_cached_base(bad_entry, store)

    with pytest.raises(IntegrityError, match=r"chunk .* mismatch"):
        ingest_patched_chunks(entry_missing, b"X" * 2048, store)

    with pytest.raises(IntegrityError, match="chunk boundary mismatch"):
        ingest_patched_chunks(entry_missing, chunk_a + chunk_b + b"extra", store)


def test_sync_index_applies_prebuilt_zstd_patch_with_zero_chunk_fetches(
    tmp_path: Path,
) -> None:
    v1_raw = _build_sqlite_version(tmp_path / "v1.sqlite", rows=2_500)
    v2_raw = _build_sqlite_version(tmp_path / "v2.sqlite", rows=2_500, updated_step=80)
    v2_canonical = canonicalize_sqlite_bytes(v2_raw)

    private, public = generate_keypair()
    signer = Ed25519Signer(private)
    verifier = Ed25519Verifier.from_public_bytes(public.public_bytes_raw())
    origin = tmp_path / "origin"
    cache = FilesystemCacheStore(tmp_path / "cache")

    build_bundle(
        files={"catalog.sqlite": v1_raw},
        store=FilesystemCacheStore(origin),
        chunker=GearCDC(),
        signer=signer,
        bundle_id="catalog",
        version="1.0.0",
        sequence=1,
    )
    first = sync_index(
        base_url=str(origin),
        store=cache,
        adapter=FilesystemAdapter(),
        verifier=verifier,
    )
    assert first.chunks_fetched >= 4

    # Build v2 WITH previous_files so CI/CD emits /patch/<patch_sha256>:
    pointer_v2 = build_bundle(
        files={"catalog.sqlite": v2_raw, "unchanged.txt": b"static"},
        previous_files={"catalog.sqlite": v1_raw, "unchanged.txt": b"static"},
        store=FilesystemCacheStore(origin),
        chunker=GearCDC(),
        signer=signer,
        bundle_id="catalog",
        version="1.0.1",
        sequence=2,
    )
    manifest_v2 = IndexManifest.model_validate_json(
        (origin / "manifest" / pointer_v2.manifest_hash).read_bytes()
    )
    catalog_entry = next(f for f in manifest_v2.files if f.path == "catalog.sqlite")
    assert catalog_entry.patches is not None
    assert len(catalog_entry.patches) == 1
    patch_ref = catalog_entry.patches[0]
    assert (origin / "patch" / patch_ref.patch_sha256).is_file()

    unchanged_entry = next(f for f in manifest_v2.files if f.path == "unchanged.txt")
    second = sync_index(
        base_url=str(origin),
        store=cache,
        adapter=FilesystemAdapter(),
        verifier=verifier,
    )
    # Catalog chunks were satisfied via the single zstd patch; only unchanged.txt was fetched.
    assert second.chunks_fetched == len(unchanged_entry.chunks)
    assert second.chunks_reused >= len(catalog_entry.chunks)
    assert second.bytes_fetched < first.bytes_fetched // 5
    assert materialize_file(cache, manifest_v2, "catalog.sqlite") == v2_canonical


def test_patch_fallback_when_patch_missing_and_fail_closed_when_tampered(
    tmp_path: Path,
) -> None:
    base = b"base-content-" * 4000
    target = b"base-content-" * 3900 + b"updated-tail-" * 100
    store = FilesystemCacheStore(tmp_path / "cache")
    base_hash = store.put_chunk(base)
    base_entry = FileEntry(
        path="data.bin",
        size=len(base),
        file_sha256=base_hash,
        chunks=[ChunkRef(hash=base_hash, size=len(base))],
    )
    built = make_patch_ref(base, target)
    assert built is not None
    patch_ref, _ = built
    target_hash = hashlib.sha256(target).hexdigest()
    target_entry = FileEntry(
        path="data.bin",
        size=len(target),
        file_sha256=target_hash,
        chunks=[ChunkRef(hash=target_hash, size=len(target))],
        patches=[patch_ref],
    )

    origin = tmp_path / "origin"
    (origin / "patch").mkdir(parents=True)

    # 1. Missing patch file on CDN falls back cleanly (returns None):
    assert (
        try_apply_entry_patch(
            base_url=str(origin),
            entry=target_entry,
            base_entries={base_hash: base_entry},
            adapter=FilesystemAdapter(),
            store=store,
            remaining_budget=1_000_000,
        )
        is None
    )

    # 2. Budget exceeded skips patch cleanly:
    assert (
        try_apply_entry_patch(
            base_url=str(origin),
            entry=target_entry,
            base_entries={base_hash: base_entry},
            adapter=FilesystemAdapter(),
            store=store,
            remaining_budget=1,
        )
        is None
    )

    # 3. Tampered patch bytes fail closed with IntegrityError:
    (origin / "patch" / patch_ref.patch_sha256).write_bytes(b"corrupt" * 10)
    with pytest.raises(IntegrityError, match="failed content-address check"):
        try_apply_entry_patch(
            base_url=str(origin),
            entry=target_entry,
            base_entries={base_hash: base_entry},
            adapter=FilesystemAdapter(),
            store=store,
            remaining_budget=1_000_000,
        )
